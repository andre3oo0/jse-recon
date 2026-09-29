"""Answer the project's questions about a price feed, with numbers taken from the warehouse."""

import sqlite3
import sys

from src import config, db

MATERIALITY_BP = 50.0  # ASISA NAV Standard s10.3.3: suggested maximum tolerance for a pricing error, 0.5% of NAV


def latest_snapshot(conn: sqlite3.Connection, source: str) -> str:
    return conn.execute("SELECT MAX(snapshot_date) FROM ingest_run WHERE source = ?", (source,)).fetchone()[0]


def nav_impact_by_day(conn: sqlite3.Connection, source: str, snap: str) -> list[tuple[str, int, float]]:
    # Equal-weighted fund across every security priced that day; the true price is the mean of the two reference bars
    return conn.execute(
        """
        WITH day_weight AS (
            SELECT price_date, 1.0 / COUNT(DISTINCT security_id) AS w
            FROM stg_price WHERE source = ? AND snapshot_date = ?
            GROUP BY price_date
        ),
        errors AS (
            SELECT price_date,
                   close_zar / ((close_zar / ratio_ref1 + close_zar / ratio_ref2) / 2) - 1 AS rel_error
            FROM stg_price
            WHERE source = ? AND snapshot_date = ? AND unit_anomaly IS NOT NULL
        )
        SELECT e.price_date, COUNT(*), SUM(d.w * e.rel_error) * 10000
        FROM errors e JOIN day_weight d USING (price_date)
        GROUP BY e.price_date
        ORDER BY ABS(SUM(d.w * e.rel_error)) DESC
        """,
        (source, snap, source, snap),
    ).fetchall()


def rollforward_impact(conn: sqlite3.Connection, source: str, snap: str, day: str) -> tuple[float, int]:
    # ASISA s4.2.2 lets a manager use the most recent available price when today's is missing; this costs that
    return conn.execute(
        """
        WITH b AS (
            SELECT security_id, price_date, close_zar FROM stg_price
            WHERE source = ? AND snapshot_date = ? AND close_zar > 0
        )
        SELECT AVG(p.close_zar / c.close_zar - 1) * 10000, COUNT(*)
        FROM b c
        JOIN b p
            ON  p.security_id = c.security_id
            AND p.price_date = (SELECT MAX(price_date) FROM b WHERE price_date < ?)
        WHERE c.price_date = ?
        """,
        (source, snap, day, day),
    ).fetchone()


def answers(conn: sqlite3.Connection, source: str) -> list[str]:
    snap = latest_snapshot(conn, source)
    key = (source, snap)
    total, securities, first, last = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT security_id), MIN(price_date), MAX(price_date) "
        "FROM stg_price WHERE source = ? AND snapshot_date = ?",
        key,
    ).fetchone()
    out = [
        f"Can {source} be trusted to value a fund of JSE shares?",
        f"Evidence: snapshot {snap}, {total:,} closing prices for {securities} securities, {first} to {last}.",
        "",
    ]

    impacts = nav_impact_by_day(conn, source, snap)
    wrong = sum(n for _, n, _ in impacts)
    out.append("1. Are the prices right?")
    if impacts:
        days = ", ".join(f"{d} ({n} securities)" for d, n, _ in sorted(impacts))
        out.append(f"   No. {wrong} prices ({wrong / total:.3%}) were about 100x wrong, on {len(impacts)} days: {days}.")
    else:
        out.append("   No unit errors found.")

    out.append("2. What would those errors have cost?")
    if impacts:
        day, n, bp = impacts[0]
        direction = "understated" if bp < 0 else "overstated"
        out.append(f"   Worst day {day}: a fund valued from this feed would have been {direction} by {abs(bp):,.0f}bp "
                   f"({abs(bp) / 100:.2f}% of NAV), {abs(bp) / MATERIALITY_BP:,.1f}x the {MATERIALITY_BP / 100:g}% "
                   f"materiality tolerance in the ASISA NAV standard. Basis: an equal-weighted fund across the "
                   f"securities priced that day.")
    else:
        out.append("   Nothing to cost.")

    expected, gaps = conn.execute(
        """
        SELECT COUNT(DISTINCT e.price_date),
               (SELECT group_concat(price_date, ', ') FROM v_session_gap g
                WHERE g.source = ? AND g.snapshot_date = ? AND g.symbols_missing >= 0.9 * g.symbols_expected)
        FROM v_expected_bar e WHERE e.source = ? AND e.snapshot_date = ?
        """,
        (*key, *key),
    ).fetchone()
    out.append("3. Is every trading day there?")
    if gaps:
        out.append(f"   Not always. {len(gaps.split(', '))} of {expected:,} trading days had no prices when the "
                   f"snapshot was taken: {gaps}.")
    else:
        out.append(f"   Yes, all {expected:,} trading days in the JSE calendar are present.")

    latest_recon = conn.execute(
        """
        SELECT recon_run_id, snapshot_a, snapshot_b, source_b FROM recon_run
        WHERE recon_name = ? ORDER BY snapshot_b DESC, snapshot_a DESC LIMIT 1
        """,
        (f"{source}_restatement",),
    ).fetchone()
    out.append("4. Does the vendor rewrite history, or publish late?")
    if latest_recon:
        run_id, snap_a, snap_b, source_b = latest_recon
        compared, changed, late = conn.execute(
            """
            SELECT SUM(status NOT IN ('ONE_A', 'ONE_B', 'CAL')),
                   SUM(status IN ('VAL', 'UNIT', 'STALE')),
                   SUM(status = 'ONE_B')
            FROM recon_result WHERE recon_run_id = ?
            """,
            (run_id,),
        ).fetchone()
        late_days = [r[0] for r in conn.execute(
            """
            SELECT price_date FROM recon_result WHERE recon_run_id = ? AND status = 'ONE_B'
            GROUP BY price_date HAVING COUNT(*) >= 10 ORDER BY price_date
            """,
            (run_id,),
        )]
        out.append(f"   Between snapshots {snap_a} and {snap_b}: {changed or 0} of {compared or 0:,} prices were "
                   f"rewritten, and {late or 0} appeared only in the later snapshot.")
        for day in late_days:
            bp, n = rollforward_impact(conn, source_b, snap_b, day)
            out.append(f"   {day} was published late. Falling back on the previous day's prices, as the ASISA "
                       f"standard permits, would have misstated an equal-weighted fund of {n} securities by "
                       f"{bp:+,.0f}bp, {abs(bp) / MATERIALITY_BP:.1f}x the {MATERIALITY_BP / 100:g}% tolerance.")
    else:
        out.append("   Measured from the second snapshot onward; only one exists so far.")

    retired = conn.execute(
        "SELECT security_id, status_note FROM security_master WHERE status = 'retired' ORDER BY security_id"
    ).fetchall()
    listed = conn.execute("SELECT COUNT(*) FROM security_master").fetchone()[0]
    out.append("5. Is the list of securities still accurate?")
    out.append(f"   {len(retired)} of {listed} codes had been renamed or delisted, each checked against a public "
               f"source: " + "; ".join(f"{s} ({n})" for s, n in retired) + ".")

    stale_days = config.tolerance_rules()["dq"]["stale_price_days"]
    runs = conn.execute(
        """
        SELECT security_id, stale_days, price_date FROM (
            SELECT security_id, price_date, stale_days,
                   LEAD(stale_days) OVER (PARTITION BY vendor_symbol ORDER BY price_date) AS next_days
            FROM stg_price WHERE source = ? AND snapshot_date = ?
        )
        WHERE stale_days >= ? AND COALESCE(next_days, 0) <> stale_days + 1
        ORDER BY stale_days DESC
        """,
        (*key, stale_days),
    ).fetchall()
    out.append("6. Are any prices suspiciously frozen?")
    if runs:
        sec, days, end = runs[0]
        out.append(f"   {len(runs)} times a price stayed identical for {stale_days}+ sessions. Longest: {sec}, "
                   f"{days} sessions to {end}. Each needs checking against a trading suspension.")
    else:
        out.append(f"   None frozen for {stale_days}+ sessions.")
    return out


def main() -> int:
    conn = db.connect()
    db.apply_schema(conn)
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM ingest_run ORDER BY source")]
    for source in sources:
        print("\n".join(answers(conn, source)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
