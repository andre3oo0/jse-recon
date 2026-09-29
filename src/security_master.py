"""Upsert config/universe.yaml into security_master and security_xref; retire securities, never delete them."""

import sqlite3

from src import config
from src.sources.base import PriceSource


OPTIONAL = {"sector": None, "isin": None, "status": "active", "status_note": None}


def sync(conn: sqlite3.Connection, sources: list[PriceSource]) -> int:
    securities = [OPTIONAL | s for s in config.universe()["securities"]]

    conn.executemany(
        """
        INSERT INTO security_master (security_id, name, sector, isin, status, status_note)
        VALUES (:security_id, :name, :sector, :isin, :status, :status_note)
        ON CONFLICT (security_id) DO UPDATE SET
            name        = excluded.name,
            sector      = excluded.sector,
            isin        = excluded.isin,
            status      = excluded.status,
            status_note = excluded.status_note,
            updated_at  = datetime('now')
        """,
        securities,
    )

    conn.executemany(
        """
        INSERT INTO security_xref (source, vendor_symbol, security_id)
        VALUES (?, ?, ?)
        ON CONFLICT (source, vendor_symbol) DO UPDATE SET
            security_id = excluded.security_id
        """,
        [
            (src.name, src.vendor_symbol(s["security_id"]), s["security_id"])
            for src in sources
            for s in securities
        ],
    )

    # A security deleted from the universe file would otherwise stay active and keep being fetched
    listed = [s["security_id"] for s in securities]
    conn.execute(
        f"""
        UPDATE security_master
        SET status = 'unresolved',
            status_note = 'Missing from universe.yaml; retire it there instead of deleting',
            updated_at = datetime('now')
        WHERE security_id NOT IN ({', '.join('?' * len(listed))})
          AND status <> 'unresolved'
        """,
        listed,
    )
    conn.commit()
    return len(securities)


def rotation_batch(conn: sqlite3.Connection, source: str, size: int, snapshot_date: str) -> list[str]:
    # Least recently tried first; earlier dates only, so a same-day refetch gets the same batch
    rows = conn.execute(
        """
        SELECT x.vendor_symbol
        FROM security_xref x
        JOIN security_master m ON m.security_id = x.security_id
        LEFT JOIN (
            SELECT s.vendor_symbol, MAX(r.snapshot_date) AS last_tried
            FROM ingest_symbol_status s
            JOIN ingest_run r ON r.run_id = s.run_id
            WHERE r.source = ? AND r.snapshot_date < ?
            GROUP BY s.vendor_symbol
        ) t ON t.vendor_symbol = x.vendor_symbol
        WHERE x.source = ? AND m.status = 'active' AND x.valid_to IS NULL
        ORDER BY t.last_tried IS NOT NULL, t.last_tried, x.vendor_symbol
        LIMIT ?
        """,
        (source, snapshot_date, source, size),
    ).fetchall()
    return [r[0] for r in rows]


def active_symbols(conn: sqlite3.Connection, source: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT x.vendor_symbol
        FROM security_xref x
        JOIN security_master m ON m.security_id = x.security_id
        WHERE x.source = ?
          AND m.status = 'active'
          AND x.valid_to IS NULL
        ORDER BY x.vendor_symbol
        """,
        (source,),
    ).fetchall()
    return [r[0] for r in rows]
