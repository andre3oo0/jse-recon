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
        conn.execute("DELETE FROM source_unit")
        conn.executemany(
            "INSERT INTO source_unit VALUES (?, ?)",
            [(name, s.get("assumed_unit")) for name, s in config.sources().items() if isinstance(s, dict)],
        )
        conn.execute("DELETE FROM stg_price")
        conn.execute(
            (config.SQL_DIR / "01_stg_price.sql").read_text(encoding="utf-8"),
            {"unit_lo": band[0], "unit_hi": band[1]},
        )
    conn.executescript((config.SQL_DIR / "02_dq_views.sql").read_text(encoding="utf-8"))
    dq = config.tolerance_rules()["dq"]
    with conn:
        conn.execute("DELETE FROM dq_setting")
        conn.executemany("INSERT INTO dq_setting VALUES (?, ?)",
                         [(name, dq[name]) for name in ("move_abs", "move_excess", "move_min_market")])
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


def findings(conn: sqlite3.Connection, source: str, snap: str) -> dict:
    key = (source, snap)
    stale_days = config.tolerance_rules()["dq"]["stale_price_days"]
    gaps = conn.execute(
        "SELECT price_date, symbols_expected, symbols_missing FROM v_session_gap "
        "WHERE source = ? AND snapshot_date = ? ORDER BY price_date",
        key,
    ).fetchall()
    whole = [g for g in gaps if g[2] >= WHOLE_MARKET * g[1]]
    whole_days = {g[0] for g in whole}
    return {
        "anomalies": conn.execute(
            """
            SELECT price_date, security_id, unit_anomaly, close_zar,
                   close_zar / ratio_ref1, close_zar / ratio_ref2
            FROM stg_price
            WHERE source = ? AND snapshot_date = ? AND unit_anomaly IS NOT NULL
            ORDER BY price_date, security_id
            """,
            key,
        ).fetchall(),
        "whole": whole,
        "partial": [g for g in gaps if g[2] < WHOLE_MARKET * g[1]],
        "missing_bars": [
            (day, sec) for day, sec in conn.execute(
                "SELECT price_date, security_id FROM v_calendar_exception "
                "WHERE source = ? AND snapshot_date = ? AND exception = 'MISSING_BAR' ORDER BY price_date, security_id",
                key,
            )
            if day not in whole_days
        ],
        "stale_days": stale_days,
        "stale": conn.execute(
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
        ).fetchall(),
        "closed": conn.execute(
            """
            SELECT price_date, reason, COUNT(*), SUM(volume > 0)
            FROM v_calendar_exception
            WHERE source = ? AND snapshot_date = ? AND exception = 'CLOSED_DAY_BAR'
            GROUP BY price_date, reason
            ORDER BY price_date
            """,
            key,
        ).fetchall(),
    }


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
        f = findings(conn, source, snap)
        print(f"\n{source} snapshot {snap}, sessions complete to {cutoff}")
        print(f"  Unit anomalies: {len(f['anomalies'])}")
        for day, sec, kind, close, ref1, ref2 in f["anomalies"]:
            print(f"    {day}  {sec:<4} {kind:<9}  R{close:,.2f}  (nearest bars R{ref1:,.2f} / R{ref2:,.2f})")
        print(f"  Trading days missing for the whole market: {len(f['whole'])}")
        for day, expected, missing in f["whole"]:
            print(f"    {day}  {missing}/{expected} symbols have no bar")
        if f["partial"]:
            print(f"  Individual missing bars: {len(f['missing_bars'])} across {len(f['partial'])} sessions")
        print(f"  Prices unchanged for {f['stale_days']}+ sessions: {len(f['stale'])}")
        for sec, end, days, close in f["stale"][:5]:
            print(f"    {sec:<4} R{close:,.2f} for {days} sessions, to {end}")
        moves = conn.execute(
            "SELECT security_id, price_date, ret, market_ret FROM v_price_move "
            "WHERE source = ? AND snapshot_date = ? ORDER BY price_date DESC",
            (source, snap),
        ).fetchall()
        print(f"  Moves the market did not share, to verify against company news: {len(moves)}")
        for sec, day, ret, market in moves[:5]:
            print(f"    {day}  {sec or '?':<4} {ret:+.1%} against a median share {market:+.1%}")
        print(f"  Days with bars while the JSE was closed: {len(f['closed'])}")
        for day, reason, bars, traded in f["closed"]:
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
