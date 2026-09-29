"""Ingest guarantees the recon layer relies on."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from src import config, db, ingest, security_master
from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus


def bar(symbol, day, close):
    return {
        "vendor_symbol": symbol, "price_date": day, "open": close, "high": close,
        "low": close, "close": close, "adj_close": close, "volume": 1000.0,
        "dividends": 0.0, "splits": 0.0, "reported_unit": "ZAc",
    }


class FakeSource(PriceSource):
    name = "fake"

    def __init__(self, rows, statuses):
        self.rows, self.statuses, self.calls = rows, statuses, 0

    def vendor_symbol(self, security_id):
        return f"{security_id}.F"

    def fetch(self, symbols, period):
        self.calls += 1
        return pd.DataFrame(self.rows, columns=PRICE_COLUMNS), self.statuses


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patches = [
            mock.patch.object(config, "ROOT", self.tmp),
            mock.patch.object(config, "LANDING_DIR", self.tmp / "landing"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        self.conn = db.connect(self.tmp / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        with mock.patch.object(config, "universe", return_value={"securities": [
            {"security_id": "AAA", "name": "Alpha", "sector": "Test"},
            {"security_id": "BBB", "name": "Beta", "sector": "Test"},
        ]}):
            security_master.sync(self.conn, [FakeSource([], [])])

        self.source = FakeSource(
            rows=[bar("AAA.F", "2026-09-28", 100.0), bar("AAA.F", "2026-09-29", 101.0)],
            statuses=[
                SymbolStatus("AAA.F", "ok", 2, "ZAc"),
                SymbolStatus("BBB.F", "empty", 0),
            ],
        )

    AFTER_CLOSE = datetime(2026, 9, 29, 18, 0, tzinfo=ingest.SAST)
    MID_SESSION = datetime(2026, 9, 29, 10, 0, tzinfo=ingest.SAST)

    def run_ingest(self, refetch=False, now=AFTER_CLOSE):
        path, manifest = ingest.land(
            self.source, ["AAA.F", "BBB.F"], "5d", "2026-09-29", refetch, now=now
        )
        return path, ingest.load(self.conn, path, manifest)

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_rerun_replaces_partition_instead_of_duplicating(self):
        self.run_ingest()
        self.run_ingest()
        self.assertEqual(self.count("raw_price"), 2)
        self.assertEqual(self.count("ingest_run"), 1)
        self.assertEqual(self.count("ingest_symbol_status"), 2)

    def test_landing_is_immutable_without_refetch(self):
        self.run_ingest()
        self.run_ingest()
        self.assertEqual(self.source.calls, 1)
        self.run_ingest(refetch=True)
        self.assertEqual(self.source.calls, 2)

    def test_vendor_duplicates_survive_into_raw(self):
        # Deduplicating on load would hide what the recon must report as a DUP break
        self.source.rows.append(bar("AAA.F", "2026-09-29", 101.0))
        self.run_ingest()
        self.assertEqual(self.count("raw_price"), 3)

    def test_missing_symbol_is_recorded_not_dropped(self):
        _, run_id = self.run_ingest()
        status = self.conn.execute(
            "SELECT status FROM ingest_symbol_status WHERE run_id = ? AND vendor_symbol = 'BBB.F'",
            (run_id,),
        ).fetchone()
        self.assertEqual(status, ("empty",))

    def test_snapshot_taken_mid_session_excludes_todays_bar(self):
        path, _ = self.run_ingest(now=self.MID_SESSION)
        days = [r[0] for r in self.conn.execute("SELECT price_date FROM raw_price")]
        self.assertEqual(days, ["2026-09-28"])
        manifest = (path.parent / "manifest.json").read_text()
        self.assertIn('"excluded_incomplete_session_rows": 1', manifest)

    def test_tampered_landing_file_is_rejected(self):
        path, _ = self.run_ingest()
        path.write_bytes(path.read_bytes() + b"\x00")
        _, manifest = ingest.land(self.source, [], "5d", "2026-09-29", refetch=False, now=self.AFTER_CLOSE)
        with self.assertRaisesRegex(RuntimeError, "manifest hash"):
            ingest.load(self.conn, path, manifest)

    def test_identical_content_lands_identical_bytes(self):
        # The hash is only a tamper check if the same data always reproduces it
        args = (self.source, ["AAA.F", "BBB.F"], "5d", "2026-09-29")
        _, first = ingest.land(*args, refetch=False, now=self.AFTER_CLOSE)
        _, second = ingest.land(*args, refetch=True, now=self.AFTER_CLOSE)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_below_coverage_gate_lands_nothing(self):
        with self.assertRaises(ingest.IncompleteSnapshot):
            ingest.land(
                self.source, ["AAA.F", "BBB.F"], "5d", "2026-09-29", False,
                now=self.AFTER_CLOSE, min_coverage=0.9,
            )
        self.assertFalse(ingest.landing_dir("fake", "2026-09-29").exists())

    def test_reload_keeps_the_landed_run_id(self):
        _, run_id = self.run_ingest()
        _, again = self.run_ingest()
        self.assertEqual(run_id, again)


if __name__ == "__main__":
    unittest.main()
