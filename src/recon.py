"""Reconcile two sets of staged prices and classify every difference."""

import argparse
import sqlite3
import sys
from dataclasses import dataclass

from src import config, db, lifecycle, staging

BREAKS = ("VAL", "ONE_A", "ONE_B", "DUP", "UNIT", "CAL", "STALE")


@dataclass(frozen=True)
class Side:
    source: str
    snapshot_date: str

    def __str__(self) -> str:
        return f"{self.source}@{self.snapshot_date}"

    @classmethod
    def parse(cls, text: str) -> "Side":
        source, snapshot_date = text.split("@")
        return cls(source, snapshot_date)


def coverage(conn: sqlite3.Connection, side: Side) -> tuple[str, str]:
    first_bar, cutoff = conn.execute(
        """
        SELECT MIN(p.price_date), MAX(r.session_cutoff)
        FROM stg_price p
        JOIN ingest_run r ON r.run_id = p.run_id
        WHERE p.source = ? AND p.snapshot_date = ?
        """,
        (side.source, side.snapshot_date),
    ).fetchone()
    if first_bar is None:
        raise ValueError(f"No staged prices for {side}")
    return first_bar, cutoff


def window(conn: sqlite3.Connection, a: Side, b: Side) -> tuple[str, str]:
    # Only dates both sides could have covered, so a longer lookback on one side is not a wall of breaks
    (first_a, cutoff_a), (first_b, cutoff_b) = coverage(conn, a), coverage(conn, b)
    return max(first_a, first_b), min(cutoff_a, cutoff_b)


def run(conn: sqlite3.Connection, name: str, a: Side, b: Side, tolerance_key: str) -> str:
    rules = config.tolerance_rules()
    tol = rules["price_recon"][tolerance_key]["close"]
    band = rules["dq"]["unit_ratio_band"]
    lo, hi = window(conn, a, b)
    run_id = f"{name}:{a}:{b}"

    with conn:
        conn.execute("DELETE FROM recon_result WHERE recon_run_id = ?", (run_id,))
        conn.execute("DELETE FROM recon_run WHERE recon_run_id = ?", (run_id,))
        conn.execute(
            """
            INSERT INTO recon_run (
                recon_run_id, recon_name, source_a, snapshot_a, source_b, snapshot_b,
                window_start, window_end, abs_floor_zar, rel_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, name, a.source, a.snapshot_date, b.source, b.snapshot_date,
             lo, hi, tol["abs_floor_zar"], tol["rel_pct"]),
        )
        conn.execute(
            (config.SQL_DIR / "03_recon_match.sql").read_text(encoding="utf-8"),
            {
                "run_id": run_id,
                "src_a": a.source, "snap_a": a.snapshot_date,
                "src_b": b.source, "snap_b": b.snapshot_date,
                "lo": lo, "hi": hi,
                "abs_floor": tol["abs_floor_zar"], "rel_pct": tol["rel_pct"],
                "unit_lo": band[0], "unit_hi": band[1],
                "stale_days": rules["dq"]["stale_price_days"],
            },
        )
    return run_id


def requested(conn: sqlite3.Connection, side: Side) -> set[str]:
    return {r[0] for r in conn.execute(
        """
        SELECT x.security_id
        FROM ingest_symbol_status s
        JOIN ingest_run r ON r.run_id = s.run_id
        JOIN security_xref x ON x.source = r.source AND x.vendor_symbol = s.vendor_symbol
        WHERE r.source = ? AND r.snapshot_date = ?
        """,
        (side.source, side.snapshot_date),
    )}


def report(conn: sqlite3.Connection, run_id: str) -> None:
    name, a_src, a_snap, b_src, b_snap, lo, hi, floor, pct = conn.execute(
        "SELECT recon_name, source_a, snapshot_a, source_b, snapshot_b, window_start, window_end, "
        "abs_floor_zar, rel_pct FROM recon_run WHERE recon_run_id = ?",
        (run_id,),
    ).fetchone()
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM recon_result WHERE recon_run_id = ? GROUP BY status", (run_id,)
    ).fetchall())
    total = sum(counts.values())
    breaks = sum(counts.get(s, 0) for s in BREAKS)

    print(f"{name}: A = {a_src}@{a_snap}, B = {b_src}@{b_snap}")
    print(f"  Window {lo} to {hi}, tolerance R{floor:.2f} and {pct:.2f}%")
    in_a, in_b = requested(conn, Side(a_src, a_snap)), requested(conn, Side(b_src, b_snap))
    for label, only in (("A", in_a - in_b), ("B", in_b - in_a)):
        if only:
            listed = ", ".join(sorted(only)) if len(only) <= 10 else f"{len(only)} securities"
            print(f"  Not compared, requested in {label} only: {listed}")
    print(f"  {total:,} prices compared: {counts.get('MATCH', 0):,} matched "
          f"({counts.get('MATCH', 0) / total:.2%}), {breaks:,} breaks")
    for status in BREAKS:
        if counts.get(status):
            print(f"    {status:<6} {counts[status]:>6,}")

    by_date = conn.execute(
        """
        SELECT price_date, status, COUNT(*) FROM recon_result
        WHERE recon_run_id = ? AND status <> 'MATCH'
        GROUP BY price_date, status ORDER BY COUNT(*) DESC, price_date LIMIT 5
        """,
        (run_id,),
    ).fetchall()
    if by_date:
        print("  Busiest break dates:")
        for day, status, n in by_date:
            print(f"    {day}  {status:<6} x{n}")

    largest = conn.execute(
        """
        SELECT price_date, key_id, status, explanation FROM recon_result
        WHERE recon_run_id = ? AND status IN ('VAL', 'STALE', 'UNIT')
        ORDER BY ABS(diff_pct) DESC LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    if largest:
        print("  Largest price breaks:")
        for day, key, status, why in largest:
            print(f"    {day}  {key:<6} {status:<6} {why}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", type=Side.parse, help="side A as source@snapshot_date")
    p.add_argument("--b", type=Side.parse, help="side B as source@snapshot_date")
    p.add_argument("--name", default="adhoc")
    p.add_argument("--tolerance", help="key under price_recon in tolerance_rules.yaml")
    args = p.parse_args(argv)

    conn = db.connect()
    db.apply_schema(conn)
    if not conn.execute("SELECT 1 FROM stg_price LIMIT 1").fetchone():
        staging.build(conn)

    if args.a and args.b:
        report(conn, run(conn, args.name, args.a, args.b, args.tolerance or args.name))
        return 0

    replay(conn)
    lifecycle.build(conn)

    latest = conn.execute(
        """
        SELECT recon_run_id FROM recon_run r
        WHERE snapshot_b = (SELECT MAX(snapshot_b) FROM recon_run WHERE recon_name = r.recon_name)
        ORDER BY recon_name
        """
    ).fetchall()
    for (run_id,) in latest:
        report(conn, run_id)
        print()
    for name in waiting(conn):
        print(name)
    lifecycle.report(conn)
    return 0


def snapshot_dates(conn: sqlite3.Connection) -> dict[str, list[str]]:
    dates: dict[str, list[str]] = {}
    for source, snap in conn.execute("SELECT source, snapshot_date FROM ingest_run ORDER BY snapshot_date"):
        dates.setdefault(source, []).append(snap)
    return dates


def replay(conn: sqlite3.Connection) -> list[str]:
    # Every stored snapshot is reconciled again, so the break register is derived from history rather than kept
    settings = config.sources()
    rotated = {name for name, s in settings.items() if isinstance(s, dict) and s.get("daily_batch")}
    dates = snapshot_dates(conn)
    run_ids = []
    # A rotated source holds different securities each day, so only full-universe sources get restatement recons
    for source in sorted(set(dates) - rotated):
        for prev, cur in zip(dates[source], dates[source][1:]):
            run_ids.append(run(conn, f"{source}_restatement", Side(source, prev), Side(source, cur),
                               f"{source}_restatement"))
    for source_a, source_b in settings.get("recon_pairs", []):
        name = f"{source_a}_vs_{source_b}"
        for day in sorted(set(dates.get(source_a, [])) & set(dates.get(source_b, []))):
            run_ids.append(run(conn, name, Side(source_a, day), Side(source_b, day), name))
    return run_ids


def waiting(conn: sqlite3.Connection) -> list[str]:
    settings, dates, lines = config.sources(), snapshot_dates(conn), []
    for source in sorted(set(dates) - {n for n, s in settings.items() if isinstance(s, dict) and s.get("daily_batch")}):
        if len(dates[source]) < 2:
            lines.append(f"{source}: one snapshot so far; restatement recon starts with the second")
    for source_a, source_b in settings.get("recon_pairs", []):
        if not set(dates.get(source_a, [])) & set(dates.get(source_b, [])):
            lines.append(f"{source_a}_vs_{source_b}: waiting for both sources on the same snapshot date")
    return lines


if __name__ == "__main__":
    sys.exit(main())
