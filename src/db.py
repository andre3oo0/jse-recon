import re
import sqlite3
from pathlib import Path

from src.config import DB_PATH, SQL_DIR

MIN_SQLITE = (3, 39, 0)  # FULL OUTER JOIN
SCHEMA = SQL_DIR / "00_schema.sql"


class StaleWarehouse(RuntimeError):
    pass


def schema_version() -> int:
    return int(re.search(r"PRAGMA user_version = (\d+)", SCHEMA.read_text(encoding="utf-8")).group(1))


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} is too old; the recon layer "
            f"needs {'.'.join(map(str, MIN_SQLITE))}+ for FULL OUTER JOIN."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    has_tables = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
    if has_tables and current != schema_version():
        raise StaleWarehouse(
            f"Warehouse schema is v{current}, code expects v{schema_version()}. "
            "The warehouse is derived, so rebuild it: python -m src.rebuild"
        )
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
