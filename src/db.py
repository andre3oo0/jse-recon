import sqlite3
from pathlib import Path

from src.config import DB_PATH, SQL_DIR

MIN_SQLITE = (3, 39, 0)  # FULL OUTER JOIN


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
    conn.executescript((SQL_DIR / "00_schema.sql").read_text(encoding="utf-8"))
