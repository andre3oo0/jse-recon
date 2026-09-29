"""Take today's snapshot from a vendor, land it, and load it.

    python -m src.ingest                 # yahoo, 1y lookback
    python -m src.ingest --period 5y     # deeper backfill
    python -m src.ingest --refetch       # replace today's landed file

Landing is immutable by default. If a snapshot for today already exists
it is reloaded from disk rather than fetched again, so re-running never
silently changes what the vendor was recorded as saying.

Each snapshot carries the full lookback, not just the latest bar. That
overlap is what makes restatement recon possible: consecutive snapshots
should agree on history, and where they don't, the vendor restated it.

A snapshot holds completed sessions only. Taken before SESSION_FINAL on
its snapshot date, it drops that day's bar, which is still moving, and
records how many rows it dropped. Otherwise an immutable landing file
would preserve an intraday price as if it were the close.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from src import config, db, security_master
from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus
from src.sources.yahoo import YahooSource

SOURCES = {"yahoo": lambda: YahooSource(suffix=config.universe()["vendor_suffix"]["yahoo"])}

# South Africa has no daylight saving, so a fixed offset is exact and
# avoids needing the tzdata package on Windows.
SAST = timezone(timedelta(hours=2))
# JSE continuous trading ends at 17:00; the closing auction and vendor
# publication take a little longer.
SESSION_FINAL = time(17, 30)


def completed_sessions(prices: pd.DataFrame, snapshot_date: str, now: datetime) -> pd.DataFrame:
    snap = datetime.fromisoformat(snapshot_date).date()
    local = now.astimezone(SAST)
    final = local.date() > snap or (local.date() == snap and local.time() >= SESSION_FINAL)
    last_kept = snapshot_date if final else (snap - timedelta(days=1)).isoformat()
    return prices[prices["price_date"] <= last_kept]


def landing_dir(source: str, snapshot_date: str) -> Path:
    return config.LANDING_DIR / source / snapshot_date


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def land(source: PriceSource, symbols, period, snapshot_date, refetch, now=None):
    """Fetch and write the landing files, or reuse them if already there."""
    now = now or datetime.now(SAST)
    out = landing_dir(source.name, snapshot_date)
    prices_path, manifest_path = out / "prices.csv", out / "manifest.json"

    if prices_path.exists() and not refetch:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        print(f"Snapshot {snapshot_date} already landed; reloading {prices_path}")
        return prices_path, manifest

    fetched_at = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    fetched, statuses = source.fetch(symbols, period)
    prices = completed_sessions(fetched, snapshot_date, now)

    out.mkdir(parents=True, exist_ok=True)
    prices.to_csv(prices_path, index=False)
    manifest = {
        "source": source.name,
        "snapshot_date": snapshot_date,
        "fetched_at": fetched_at,
        "lookback_period": period,
        "rows": len(prices),
        "excluded_incomplete_session_rows": len(fetched) - len(prices),
        "sha256": sha256(prices_path),
        "symbols": [s.__dict__ for s in statuses],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return prices_path, manifest


def load(conn: sqlite3.Connection, prices_path: Path, manifest: dict) -> str:
    """Replace the source + snapshot_date partition in one transaction."""
    source, snapshot_date = manifest["source"], manifest["snapshot_date"]
    statuses = [SymbolStatus(**s) for s in manifest["symbols"]]
    prices = pd.read_csv(prices_path, dtype={"vendor_symbol": str, "price_date": str})
    prices = prices[PRICE_COLUMNS]

    if sha256(prices_path) != manifest["sha256"]:
        raise RuntimeError(f"{prices_path} does not match its manifest hash; landing was modified.")

    run_id = str(uuid.uuid4())
    with conn:
        stale_runs = [
            r[0]
            for r in conn.execute(
                "SELECT run_id FROM ingest_run WHERE source = ? AND snapshot_date = ?",
                (source, snapshot_date),
            )
        ]
        conn.execute(
            "DELETE FROM raw_price WHERE source = ? AND snapshot_date = ?",
            (source, snapshot_date),
        )
        for stale in stale_runs:
            conn.execute("DELETE FROM ingest_symbol_status WHERE run_id = ?", (stale,))
            conn.execute("DELETE FROM ingest_run WHERE run_id = ?", (stale,))

        conn.execute(
            """
            INSERT INTO ingest_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source,
                snapshot_date,
                manifest["fetched_at"],
                manifest["lookback_period"],
                len(statuses),
                sum(s.status == "ok" for s in statuses),
                len(prices),
                str(prices_path.relative_to(config.ROOT)),
                manifest["sha256"],
            ),
        )
        conn.executemany(
            "INSERT INTO ingest_symbol_status VALUES (?, ?, ?, ?, ?, ?)",
            [
                (run_id, s.vendor_symbol, s.status, s.row_count, s.reported_unit, s.error)
                for s in statuses
            ],
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
    run = conn.execute(
        "SELECT source, snapshot_date, symbols_requested, symbols_returned, rows_landed "
        "FROM ingest_run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    source, snap, requested, returned, rows = run
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
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="yahoo", choices=SOURCES)
    p.add_argument("--period", default="1y", help="vendor lookback, e.g. 1y, 5y, max")
    p.add_argument("--snapshot-date", default=datetime.now(SAST).date().isoformat())
    p.add_argument("--refetch", action="store_true", help="overwrite an existing landed snapshot")
    args = p.parse_args(argv)

    source = SOURCES[args.source]()
    conn = db.connect()
    db.apply_schema(conn)
    security_master.sync(conn, [source])
    symbols = security_master.active_symbols(conn, source.name)

    prices_path, manifest = land(source, symbols, args.period, args.snapshot_date, args.refetch)
    run_id = load(conn, prices_path, manifest)
    coverage_report(conn, run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
