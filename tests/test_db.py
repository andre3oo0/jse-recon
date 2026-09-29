"""Warehouse schema versioning."""

import tempfile
import unittest
from pathlib import Path

from src import db


class SchemaVersionTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "test.db"

    def test_fresh_warehouse_takes_the_current_version(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        db.apply_schema(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.schema_version())

    def test_outdated_warehouse_asks_for_a_rebuild(self):
        conn = db.connect(self.path)
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE raw_price (x)")
        conn.execute("PRAGMA user_version = 1")
        with self.assertRaisesRegex(db.StaleWarehouse, "rebuild"):
            db.apply_schema(conn)


if __name__ == "__main__":
    unittest.main()
