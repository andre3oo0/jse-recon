"""Choose the price the fund should use for each security and day: primary, secondary, or the previous close, with why."""

import sqlite3

from src import config


def hierarchy() -> tuple[str, str]:
    primary, secondary = config.sources()["price_hierarchy"]
    return primary, secondary


def build(conn: sqlite3.Connection) -> int:
    primary, secondary = hierarchy()
    with conn:
        conn.execute("DELETE FROM approved_price")
        conn.execute((config.SQL_DIR / "06_approved_price.sql").read_text(encoding="utf-8"),
                     {"primary": primary, "secondary": secondary, "pair": f"{primary}_vs_{secondary}"})
    return conn.execute("SELECT COUNT(*) FROM approved_price").fetchone()[0]


def latest_day(conn: sqlite3.Connection) -> str | None:
    return conn.execute("SELECT MAX(price_date) FROM approved_price").fetchone()[0]


def for_day(conn: sqlite3.Connection, day: str) -> list[tuple]:
    # Exceptions first, so the prices that need a person come to the top
    return conn.execute(
        """
        SELECT a.security_id, m.name, a.close_zar, a.source, a.status, a.reason
        FROM approved_price a
        LEFT JOIN security_master m ON m.security_id = a.security_id
        WHERE a.price_date = ?
        ORDER BY a.status = 'APPROVED', a.status, a.security_id
        """,
        (day,),
    ).fetchall()


def status_counts(conn: sqlite3.Connection, day: str) -> dict[str, int]:
    return dict(conn.execute(
        "SELECT status, COUNT(*) FROM approved_price WHERE price_date = ? GROUP BY status", (day,)
    ).fetchall())
