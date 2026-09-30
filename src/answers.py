"""Answer the project's questions about a price feed, with numbers taken from the warehouse."""

import sqlite3
import sys

from src import config, db, holdings, lifecycle

MATERIALITY_BP = 50.0  # ASISA NAV Standard s10.3.3: suggested maximum tolerance for a pricing error, 0.5% of NAV


def latest_snapshot(conn: sqlite3.Connection, source: str) -> str:
    return conn.execute("SELECT MAX(snapshot_date) FROM ingest_run WHERE source = ?", (source,)).fetchone()[0]


# Every stored price, each as the latest snapshot holding it has it; a 3-month daily snapshot must not hide older errors
LATEST_PRICE = """
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY security_id, price_date ORDER BY snapshot_date DESC) AS latest
        FROM stg_price WHERE source = ?
    )
    WHERE latest = 1
"""


def stored_prices(conn: sqlite3.Connection, source: str) -> tuple[int, str, str]:
    return conn.execute(
        f"SELECT COUNT(*), MIN(price_date), MAX(price_date) FROM ({LATEST_PRICE})", (source,)
    ).fetchone()


def nav_impact_by_day(conn: sqlite3.Connection, source: str) -> list[tuple[str, int, float]]:
    # Equal-weighted fund across every security priced that day; the true price is the mean of the two reference bars
    return conn.execute(
        f"""
        WITH p AS ({LATEST_PRICE}),
        day_weight AS (
            SELECT price_date, 1.0 / COUNT(DISTINCT security_id) AS w FROM p GROUP BY price_date
        ),
        errors AS (
            SELECT price_date,
                   close_zar / ((close_zar / ratio_ref1 + close_zar / ratio_ref2) / 2) - 1 AS rel_error
            FROM p WHERE unit_anomaly IS NOT NULL
        )
        SELECT e.price_date, COUNT(*), SUM(d.w * e.rel_error) * 10000
        FROM errors e JOIN day_weight d USING (price_date)
        GROUP BY e.price_date
        ORDER BY ABS(SUM(d.w * e.rel_error)) DESC
        """,
        (source,),
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

    impacts = nav_impact_by_day(conn, source)
    wrong = sum(n for _, n, _ in impacts)
    stored, stored_first, stored_last = stored_prices(conn, source)
    scope = (f"all {stored:,} stored prices, {stored_first} to {stored_last}, each as the latest snapshot "
             f"holding it has it")
    out.append("1. Are the prices right?")
    if impacts:
        days = ", ".join(f"{d} ({n} securities)" for d, n, _ in sorted(impacts))
        out.append(f"   No. Of {scope}, {wrong} ({wrong / stored:.3%}) were about 100x wrong, on {len(impacts)} "
                   f"days: {days}.")
    else:
        out.append(f"   No unit errors in {scope}.")

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
    out.append("7. Do independent sources agree?")
    out.extend(cross_source(conn) or ["   Waiting for the first snapshots from the other sources."])

    out.append("8. When sources disagree, does it get fixed, and how fast?")
    out.extend(resolution(conn) or ["   No disagreements between sources recorded yet."])

    out.append("9. Does the broker statement agree with the internal book?")
    out.extend(statement(conn) or ["   No broker statement loaded."])
    return out


def statement(conn: sqlite3.Connection) -> list[str]:
    latest = holdings.latest_file(conn)
    if not latest:
        return []
    file_id, as_of, synthetic = latest
    got = holdings.results(conn, file_id)
    breaks = {}
    for status in got.values():
        if status != "MATCH":
            breaks[status] = breaks.get(status, 0) + 1
    net, gross = holdings.misstatement(conn, file_id)
    book = conn.execute("SELECT SUM(book_value) FROM holding_recon_result WHERE file_id = ?", (file_id,)).fetchone()[0]
    label = "A SYNTHETIC statement (planted breaks, real prices)" if synthetic else "The statement"
    lines = [f"   {label} for {as_of}: {len(got) - sum(breaks.values())} of {len(got)} positions agree; breaks: "
             + ", ".join(f"{s} {n}" for s, n in sorted(breaks.items())) + "."]
    if book:
        lines.append(f"   Net, the statement is out by R{net:+,.0f} ({net / book:+.2%}), which looks close. Gross it is "
                     f"out by R{gross:,.0f} ({gross / book:.2%}): netting lets a duplicated line hide a missing one.")
    if breaks.get("SETTLE"):
        n = breaks["SETTLE"]
        lines.append(f"   {n} {'break is a trade' if n == 1 else 'breaks are trades'} not yet settled (T+3), "
                     f"a timing difference rather than an error.")
    return lines


def resolution(conn: sqlite3.Connection) -> list[str]:
    lines = []
    names = [r[0] for r in conn.execute("SELECT DISTINCT recon_name FROM break_episode ORDER BY recon_name")]
    for name in names:
        s = lifecycle.summary(conn, name)
        speed = (f" Those that cleared took {s['mean_days_to_clear']:.1f} trading days on average."
                 if s["mean_days_to_clear"] is not None else "")
        lines.append(f"   {name}: {s['episodes']} disagreements tracked. {s['timing']} cleared within "
                     f"{config.tolerance_rules()['dq']['timing_clear_days']} trading days (timing differences), "
                     f"{s['cleared']} took longer, and {s['open']} are still open.{speed}")
        oldest = conn.execute(
            "SELECT key_id, price_date, age_days FROM break_episode WHERE recon_name = ? AND state = 'OPEN' "
            "ORDER BY age_days DESC LIMIT 1",
            (name,),
        ).fetchone()
        if oldest:
            lines.append(f"   Oldest open: {oldest[0]} on {oldest[1]}, unresolved for {oldest[2]} trading days.")
    return lines


def cross_source(conn: sqlite3.Connection) -> list[str]:
    lines = []
    runs = conn.execute(
        """
        SELECT recon_run_id, recon_name, source_a, source_b, snapshot_b
        FROM recon_run r
        WHERE recon_name LIKE '%\\_vs\\_%' ESCAPE '\\'
          AND snapshot_b = (SELECT MAX(snapshot_b) FROM recon_run WHERE recon_name = r.recon_name)
        ORDER BY recon_name
        """
    ).fetchall()
    for run_id, _, source_a, source_b, snap_b in runs:
        securities, compared, matched, vol_known, vol_differ = conn.execute(
            """
            SELECT COUNT(DISTINCT key_id),
                   COUNT(*),
                   SUM(status = 'MATCH'),
                   SUM(status = 'MATCH' AND volume_a IS NOT NULL AND volume_b IS NOT NULL),
                   SUM(status = 'MATCH' AND volume_a <> volume_b)
            FROM recon_result
            WHERE recon_run_id = ? AND status NOT IN ('ONE_A', 'ONE_B', 'CAL')
            """,
            (run_id,),
        ).fetchone()
        breaks = conn.execute(
            "SELECT status, COUNT(*) FROM recon_result WHERE recon_run_id = ? AND status <> 'MATCH' "
            "GROUP BY status ORDER BY COUNT(*) DESC",
            (run_id,),
        ).fetchall()
        if not compared:
            lines.append(f"   {source_a} vs {source_b}: no securities in common yet.")
            continue
        detail = ", ".join(f"{s} {n}" for s, n in breaks) or "none"
        volume = f" Volumes differed on {vol_differ / vol_known:.0%} of matched days." if vol_known else ""
        lines.append(f"   {source_a} vs {source_b} ({securities} securities, to {snap_b}): {matched or 0:,} of "
                     f"{compared:,} closes agree ({(matched or 0) / compared:.1%}); breaks: {detail}.{volume}")

    observed = conn.execute(
        """
        SELECT COUNT(DISTINCT x.security_id),
               COUNT(DISTINCT CASE WHEN m.isin IS NOT NULL AND m.isin <> s.isin THEN x.security_id END)
        FROM ingest_symbol_status s
        JOIN ingest_run r ON r.run_id = s.run_id
        JOIN security_xref x ON x.source = r.source AND x.vendor_symbol = s.vendor_symbol
        JOIN security_master m ON m.security_id = x.security_id
        WHERE s.isin IS NOT NULL
        """
    ).fetchone()
    if observed[0]:
        lines.append(f"   ISINs published by vendors: {observed[0]} securities, {observed[1]} disagreeing with the "
                     f"security master.")
    return lines


def feeds_under_test(conn: sqlite3.Connection) -> list[str]:
    # A rotated source holds only a few securities per snapshot, so only full-universe feeds get the questions
    settings = config.sources()
    return [r[0] for r in conn.execute("SELECT DISTINCT source FROM ingest_run ORDER BY source")
            if not settings.get(r[0], {}).get("daily_batch")]


def main() -> int:
    conn = db.connect()
    db.apply_schema(conn)
    for source in feeds_under_test(conn):
        print("\n".join(answers(conn, source)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
