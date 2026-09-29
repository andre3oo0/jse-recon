"""Build stg_price and the data quality views from raw_price, then report what they found."""

import argparse
import sqlite3
import sys

from src import config, db, trading_calendar

WHOLE_MARKET = 0.9  # a session missing for this share of symbols is a market or vendor gap, not one quiet stock


def build(conn: sqlite3.Connection) -> int:
    band = config.tolerance_rules()["dq"]["unit_ratio_band"]
    trading_calendar.load(conn)
    with conn:
        conn.execute("DELETE FROM stg_price")
        conn.execute(
            (config.SQL_DIR / "01_stg_price.sql").read_text(encoding="utf-8"),
            {"unit_lo": band[0], "unit_hi": band[1]},
        )
    conn.executescript((config.SQL_DIR / "02_dq_views.sql").read_text(encoding="utf-8"))
    return conn.execute("SELECT COUNT(*) FROM stg_price").fetchone()[0]


def latest_snapshots(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return conn.execute(
        """
        SELECT source, snapshot_date, session_cutoff
        FROM ingest_run r
        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM ingest_run WHERE source = r.source)
        ORDER BY source
        """
    ).fetchall()


def report(conn: sqlite3.Connection) -> None:
    total, unmapped, unknown_unit, off_calendar = conn.execute(
        """
        SELECT COUNT(*), SUM(security_id IS NULL), SUM(unit_factor IS NULL), SUM(is_trading_day IS NULL)
        FROM stg_price
        """
    ).fetchone()
    print(f"Staged {total:,} rows: {unmapped or 0} unmapped, {unknown_unit or 0} in an unknown unit, "
          f"{off_calendar or 0} outside the calendar")

    for source, snap, cutoff in latest_snapshots(conn):
        key = (source, snap)
        print(f"\n{source} snapshot {snap}, sessions complete to {cutoff}")

        anomalies = conn.execute(
            """
            SELECT price_date, security_id, unit_anomaly, close_zar,
                   close_zar / ratio_ref1, close_zar / ratio_ref2
            FROM stg_price
            WHERE source = ? AND snapshot_date = ? AND unit_anomaly IS NOT NULL
            ORDER BY price_date, security_id
            """,
            key,
        ).fetchall()
        print(f"  Unit anomalies: {len(anomalies)}")
        for day, sec, kind, close, ref1, ref2 in anomalies:
            print(f"    {day}  {sec:<4} {kind:<9}  R{close:,.2f}  (nearest bars R{ref1:,.2f} / R{ref2:,.2f})")

        gaps = conn.execute(
            "SELECT price_date, symbols_expected, symbols_missing FROM v_session_gap "
            "WHERE source = ? AND snapshot_date = ? ORDER BY price_date",
            key,
        ).fetchall()
        whole = [g for g in gaps if g[2] >= WHOLE_MARKET * g[1]]
        partial = [g for g in gaps if g[2] < WHOLE_MARKET * g[1]]
        print(f"  Trading days missing for the whole market: {len(whole)}")
        for day, expected, missing in whole:
            print(f"    {day}  {missing}/{expected} symbols have no bar")
        if partial:
            bars = sum(g[2] for g in partial)
            print(f"  Individual missing bars: {bars} across {len(partial)} sessions")

        stale_days = config.tolerance_rules()["dq"]["stale_price_days"]
        stale = conn.execute(
            """
            SELECT security_id, price_date, stale_days, close_zar
            FROM (
                SELECT security_id, price_date, stale_days, close_zar,
                       LEAD(stale_days) OVER (PARTITION BY vendor_symbol ORDER BY price_date) AS next_days
                FROM stg_price
                WHERE source = ? AND snapshot_date = ?
            )
            WHERE stale_days >= ? AND COALESCE(next_days, 0) <> stale_days + 1
            ORDER BY stale_days DESC, price_date
            """,
            (*key, stale_days),
        ).fetchall()
        print(f"  Prices unchanged for {stale_days}+ sessions: {len(stale)}")
        for sec, end, days, close in stale[:5]:
            print(f"    {sec:<4} R{close:,.2f} for {days} sessions, to {end}")

        closed = conn.execute(
            """
            SELECT price_date, reason, COUNT(*), SUM(volume > 0)
            FROM v_calendar_exception
            WHERE source = ? AND snapshot_date = ? AND exception = 'CLOSED_DAY_BAR'
            GROUP BY price_date, reason
            ORDER BY price_date
            """,
            key,
        ).fetchall()
        print(f"  Days with bars while the JSE was closed: {len(closed)}")
        for day, reason, bars, traded in closed:
            print(f"    {day}  {reason:<34} {bars} bars, {traded} with volume")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report-only", action="store_true", help="report on the tables as they are")
    args = p.parse_args(argv)

    conn = db.connect()
    db.apply_schema(conn)
    if not args.report_only:
        build(conn)
    report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
