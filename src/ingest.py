"""Take today's snapshot from a vendor, land it immutably, and load it into the warehouse."""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from src import config, db, security_master, staging
from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus
from src.sources.yahoo import YahooSource

SOURCES = {"yahoo": lambda: YahooSource(suffix=config.universe()["vendor_suffix"]["yahoo"])}

SAST = timezone(timedelta(hours=2))  # no daylight saving, so a fixed offset is exact
SESSION_FINAL = time(17, 30)  # JSE closes at 17:00; allow for the closing auction and vendor publication

PRICES_FILE = "prices.csv.gz"
MANIFEST_FILE = "manifest.json"


class IncompleteSnapshot(Exception):
    pass


def session_cutoff(snapshot_date: str, fetched: datetime) -> str:
    snap = datetime.fromisoformat(snapshot_date).date()
    local = fetched.astimezone(SAST)
    final = local.date() > snap or (local.date() == snap and local.time() >= SESSION_FINAL)
    return snapshot_date if final else (snap - timedelta(days=1)).isoformat()


def completed_sessions(prices: pd.DataFrame, snapshot_date: str, now: datetime) -> pd.DataFrame:
    return prices[prices["price_date"] <= session_cutoff(snapshot_date, now)]


def landing_dir(source: str, snapshot_date: str) -> Path:
    return config.LANDING_DIR / source / snapshot_date


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_prices(prices: pd.DataFrame, path: Path) -> None:
    # mtime=0 keeps the gzip header timestamp-free, so identical content gives an identical hash
    prices.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})


def land(source: PriceSource, symbols, period, snapshot_date, refetch, now=None, min_coverage=0.0):
    now = now or datetime.now(SAST)
    out = landing_dir(source.name, snapshot_date)
    prices_path, manifest_path = out / PRICES_FILE, out / MANIFEST_FILE

    if prices_path.exists() and not refetch:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"Snapshot {snapshot_date} already landed; reloading {prices_path}")
        return prices_path, manifest

    fetched, statuses = source.fetch(symbols, period)

    returned = sum(s.status == "ok" for s in statuses)
    if symbols and returned / len(symbols) < min_coverage:
        failed = ", ".join(s.vendor_symbol for s in statuses if s.status != "ok")
        raise IncompleteSnapshot(
            f"{source.name} returned {returned}/{len(symbols)} symbols, below the "
            f"{min_coverage:.0%} gate. Nothing landed. Failed: {failed}"
        )

    prices = completed_sessions(fetched, snapshot_date, now)
    out.mkdir(parents=True, exist_ok=True)
    write_prices(prices, prices_path)
    manifest = {
        "run_id": str(uuid.uuid4()),
        "source": source.name,
        "snapshot_date": snapshot_date,
        "fetched_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "session_cutoff": session_cutoff(snapshot_date, now),
        "lookback_period": period,
        "rows": len(prices),
        "excluded_incomplete_session_rows": len(fetched) - len(prices),
        "sha256": sha256(prices_path),
        "symbols": [s.__dict__ for s in statuses],
    }
    # LF on every OS, so a manifest is byte-identical wherever it was written
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return prices_path, manifest


def load(conn: sqlite3.Connection, prices_path: Path, manifest: dict) -> str:
    if sha256(prices_path) != manifest["sha256"]:
        raise RuntimeError(f"{prices_path} does not match its manifest hash; landing was modified.")

    source, snapshot_date, run_id = manifest["source"], manifest["snapshot_date"], manifest["run_id"]
    # Manifests written before session_cutoff existed derive it from their fetch time
    cutoff = manifest.get("session_cutoff") or session_cutoff(
        snapshot_date, datetime.fromisoformat(manifest["fetched_at"])
    )
    statuses = [SymbolStatus(**s) for s in manifest["symbols"]]
    prices = pd.read_csv(prices_path, dtype={"vendor_symbol": str, "price_date": str})[PRICE_COLUMNS]

    with conn:
        stale_runs = [
            r[0]
            for r in conn.execute(
                "SELECT run_id FROM ingest_run WHERE source = ? AND snapshot_date = ?",
                (source, snapshot_date),
            )
        ]
        conn.execute("DELETE FROM raw_price WHERE source = ? AND snapshot_date = ?", (source, snapshot_date))
        for stale in stale_runs:
            conn.execute("DELETE FROM ingest_symbol_status WHERE run_id = ?", (stale,))
            conn.execute("DELETE FROM ingest_run WHERE run_id = ?", (stale,))

        conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source,
                snapshot_date,
                manifest["fetched_at"],
                cutoff,
                manifest["lookback_period"],
                len(statuses),
                sum(s.status == "ok" for s in statuses),
                len(prices),
                prices_path.relative_to(config.ROOT).as_posix(),
                manifest["sha256"],
            ),
        )
        conn.executemany(
            "INSERT INTO ingest_symbol_status VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, s.vendor_symbol, s.status, s.row_count, s.reported_unit, s.error) for s in statuses],
        )
        prices = prices.assign(source=source, snapshot_date=snapshot_date, run_id=run_id)
        prices = prices.astype(object).where(prices.notna(), None)
        cols = ["source", "snapshot_date", *PRICE_COLUMNS, "run_id"]
        conn.executemany(
            f"INSERT INTO raw_price ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            prices[cols].itertuples(index=False, name=None),
        )
    return run_id


def coverage_report(conn: sqlite3.Connection, run_id: str) -> None:
    source, snap, requested, returned, rows = conn.execute(
        "SELECT source, snapshot_date, symbols_requested, symbols_returned, rows_landed "
        "FROM ingest_run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    print(f"\n{source} snapshot {snap}: {returned}/{requested} symbols, {rows:,} rows")

    units = conn.execute(
        "SELECT COALESCE(reported_unit, '?'), COUNT(*) FROM ingest_symbol_status "
        "WHERE run_id = ? AND status = 'ok' GROUP BY 1",
        (run_id,),
    ).fetchall()
    print("Reported units: " + ", ".join(f"{u} x{n}" for u, n in units))

    gaps = conn.execute(
        """
        SELECT s.vendor_symbol, s.status, m.name, s.error
        FROM ingest_symbol_status s
        JOIN security_xref x ON x.source = ? AND x.vendor_symbol = s.vendor_symbol
        JOIN security_master m ON m.security_id = x.security_id
        WHERE s.run_id = ? AND s.status <> 'ok'
        ORDER BY s.status, s.vendor_symbol
        """,
        (source, run_id),
    ).fetchall()
    if gaps:
        print(f"\nCoverage gaps ({len(gaps)}), resolve each in the security master:")
        for sym, status, name, err in gaps:
            print(f"  {sym:<9} {status:<6} {name}" + (f"  [{err[:80]}]" if err else ""))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="yahoo", choices=SOURCES)
    p.add_argument("--period", default="3mo", help="vendor lookback, e.g. 3mo, 1y, 5y, max")
    p.add_argument("--snapshot-date", default=datetime.now(SAST).date().isoformat())
    p.add_argument("--refetch", action="store_true", help="overwrite an existing landed snapshot")
    p.add_argument(
        "--min-coverage", type=float, default=0.90,
        help="refuse to land if fewer than this share of symbols return data",
    )
    args = p.parse_args(argv)

    source = SOURCES[args.source]()
    conn = db.connect()
    db.apply_schema(conn)
    security_master.sync(conn, [source])
    symbols = security_master.active_symbols(conn, source.name)

    try:
        prices_path, manifest = land(
            source, symbols, args.period, args.snapshot_date, args.refetch,
            min_coverage=args.min_coverage,
        )
    except IncompleteSnapshot as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2
    run_id = load(conn, prices_path, manifest)
    coverage_report(conn, run_id)
    staging.build(conn)  # keep the derived tables in step with raw
    return 0


if __name__ == "__main__":
    sys.exit(main())
