"""The break register: every disagreement from first sighting until it clears, with ageing and analyst notes."""

import sqlite3

from src import config

AGE_BUCKETS = [(0, 1, "0-1"), (2, 5, "2-5"), (6, 20, "6-20"), (21, 10**6, "21+")]  # trading days
RESOLUTIONS = {"VENDOR_ERROR_A", "VENDOR_ERROR_B", "TIMING", "ACCEPTED", "INVESTIGATING"}


class BadNote(ValueError):
    pass


def load_notes(conn: sqlite3.Connection) -> int:
    notes = config.load_yaml("break_notes.yaml").get("notes") or []
    for n in notes:
        missing = {"recon", "key", "price_date", "resolution", "note"} - set(n)
        if missing or n["resolution"] not in RESOLUTIONS:
            raise BadNote(f"Break note {n} needs recon, key, price_date, note and one of {sorted(RESOLUTIONS)}")
    conn.execute("DELETE FROM break_note")
    conn.executemany(
        "INSERT INTO break_note VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(n["recon"], n["key"], str(n["price_date"]), n["resolution"], n["note"], n.get("author"),
          str(n["noted_on"]) if n.get("noted_on") else None) for n in notes],
    )
    return len(notes)


def build(conn: sqlite3.Connection) -> int:
    timing_days = config.tolerance_rules()["dq"]["timing_clear_days"]
    with conn:
        conn.execute("DELETE FROM break_episode")
        conn.execute((config.SQL_DIR / "04_break_lifecycle.sql").read_text(encoding="utf-8"),
                     {"timing_days": timing_days})
        load_notes(conn)
    return conn.execute("SELECT COUNT(*) FROM break_episode").fetchone()[0]


def summary(conn: sqlite3.Connection, recon_name: str) -> dict:
    states = dict(conn.execute(
        "SELECT state, COUNT(*) FROM break_episode WHERE recon_name = ? GROUP BY state", (recon_name,)
    ).fetchall())
    open_ages = [r[0] for r in conn.execute(
        "SELECT age_days FROM break_episode WHERE recon_name = ? AND state = 'OPEN'", (recon_name,)
    )]
    cleared_ages = [r[0] for r in conn.execute(
        "SELECT age_days FROM break_episode WHERE recon_name = ? AND state <> 'OPEN'", (recon_name,)
    )]
    return {
        "episodes": sum(states.values()),
        "open": states.get("OPEN", 0),
        "timing": states.get("TIMING", 0),
        "cleared": states.get("CLEARED", 0),
        "buckets": {label: sum(lo <= a <= hi for a in open_ages) for lo, hi, label in AGE_BUCKETS},
        "mean_days_to_clear": sum(cleared_ages) / len(cleared_ages) if cleared_ages else None,
    }


def report(conn: sqlite3.Connection) -> None:
    names = [r[0] for r in conn.execute("SELECT DISTINCT recon_name FROM break_episode ORDER BY recon_name")]
    if not names:
        print("Break register: no disagreements between sources recorded yet")
        return
    for name in names:
        s = summary(conn, name)
        print(f"Break register, {name}: {s['episodes']} episodes; {s['open']} open, "
              f"{s['timing']} cleared as timing differences, {s['cleared']} cleared later")
        print("  Open by age in trading days: " + ", ".join(f"{k}: {v}" for k, v in s["buckets"].items()))
        if s["mean_days_to_clear"] is not None:
            print(f"  Mean trading days to clear: {s['mean_days_to_clear']:.1f}")
        oldest = conn.execute(
            """
            SELECT e.key_id, e.price_date, e.latest_status, e.age_days, e.first_seen, e.latest_explanation,
                   n.resolution, n.note
            FROM break_episode e
            LEFT JOIN break_note n
                ON n.recon_name = e.recon_name AND n.key_id = e.key_id AND n.price_date = e.price_date
            WHERE e.recon_name = ? AND e.state = 'OPEN'
            ORDER BY e.age_days DESC, e.key_id
            LIMIT 5
            """,
            (name,),
        ).fetchall()
        if oldest:
            print("  Oldest open:")
        for key, day, status, age, first, why, resolution, note in oldest:
            tag = f" [{resolution}: {note}]" if resolution else ""
            print(f"    {key:<6} {day}  {status:<6} open {age} days since {first}. {why or ''}{tag}")
