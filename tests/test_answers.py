"""The figures behind questions 1 and 2, and the cost of a late day."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import answers, config, db, staging


class NavImpactTest(unittest.TestCase):
    def test_one_bad_price_in_a_two_stock_fund(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(conn.close)
        db.apply_schema(conn)
        conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES ('r1', 'v', '2026-09-29', '2026-09-29T16:00:00+00:00', '2026-09-28', '5d', 2, 2, 0, '', '')
            """
        )
        for sec, closes in (("AAA", [10000, 100, 10000]), ("BBB", [5000, 5000, 5000])):
            conn.execute("INSERT INTO security_master (security_id, name) VALUES (?, ?)", (sec, sec))
            conn.execute("INSERT INTO security_xref (source, vendor_symbol, security_id) VALUES ('v', ?, ?)",
                         (f"{sec}.V", sec))
            conn.executemany(
                "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, adj_close, "
                "volume, reported_unit, run_id) VALUES ('v', '2026-09-29', ?, ?, ?, ?, 1, 'ZAc', 'r1')",
                [(f"{sec}.V", d, c, c) for d, c in zip(["2026-09-22", "2026-09-23", "2026-09-25"], closes)],
            )
        conn.commit()
        staging.build(conn)
        # Half the fund is priced at 1% of its value: 0.5 x -99% = -4,950bp
        [(day, n, bp)] = answers.nav_impact_by_day(conn, "v")
        self.assertEqual((day, n), ("2026-09-23", 1))
        self.assertAlmostEqual(bp, -4950, places=6)

    def test_cost_of_rolling_forward_the_previous_price(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(conn.close)
        db.apply_schema(conn)
        conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES ('r1', 'v', '2026-09-29', '2026-09-29T16:00:00+00:00', '2026-09-28', '5d', 2, 2, 0, '', '')
            """
        )
        # Gold Fields-style day: one stock falls 12%, the other is flat
        for sec, fri, mon in (("AAA", 65752, 57840), ("BBB", 10000, 10000)):
            conn.execute("INSERT INTO security_master (security_id, name) VALUES (?, ?)", (sec, sec))
            conn.execute("INSERT INTO security_xref (source, vendor_symbol, security_id) VALUES ('v', ?, ?)",
                         (f"{sec}.V", sec))
            conn.executemany(
                "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, adj_close, "
                "volume, reported_unit, run_id) VALUES ('v', '2026-09-29', ?, ?, ?, ?, 1, 'ZAc', 'r1')",
                [(f"{sec}.V", "2026-09-25", fri, fri), (f"{sec}.V", "2026-09-28", mon, mon)],
            )
        conn.commit()
        staging.build(conn)
        bp, n = answers.rollforward_impact(conn, "v", "2026-09-29", "2026-09-28")
        self.assertEqual(n, 2)
        self.assertAlmostEqual(bp, (65752 / 57840 - 1) / 2 * 10000, places=6)


class OlderSnapshotTest(unittest.TestCase):
    # A 5-year backfill followed by a 3-month daily snapshot: the daily one must not hide the backfill's errors
    def setUp(self):
        self.conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        for sec in ("AAA", "BBB"):
            self.conn.execute("INSERT INTO security_master (security_id, name) VALUES (?, ?)", (sec, sec))
            self.conn.execute("INSERT INTO security_xref (source, vendor_symbol, security_id) VALUES ('v', ?, ?)",
                              (f"{sec}.V", sec))
        self.load("2026-09-29", ["2026-09-22", "2026-09-23", "2026-09-25"], {"AAA": [10000, 100, 10000]})

    def load(self, snap, days, closes):
        run = f"r{snap}"
        self.conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES (?, 'v', ?, '', ?, '', 2, 2, 0, '', '')
            """,
            (run, snap, days[-1]),
        )
        for sec in ("AAA", "BBB"):
            self.conn.executemany(
                "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, adj_close, "
                "volume, reported_unit, run_id) VALUES ('v', ?, ?, ?, ?, ?, 1, 'ZAc', ?)",
                [(snap, f"{sec}.V", d, c, c, run) for d, c in zip(days, closes.get(sec, [5000] * len(days)))],
            )
        self.conn.commit()
        staging.build(self.conn)

    def test_error_outside_the_latest_snapshot_is_still_reported(self):
        self.load("2026-09-30", ["2026-09-25", "2026-09-28", "2026-09-29"], {})
        [(day, n, bp)] = answers.nav_impact_by_day(self.conn, "v")
        self.assertEqual((day, n), ("2026-09-23", 1))
        self.assertAlmostEqual(bp, -4950, places=6)

    def test_price_corrected_in_a_later_snapshot_is_not_reported(self):
        self.load("2026-09-30", ["2026-09-22", "2026-09-23", "2026-09-25"], {"AAA": [10000, 10000, 10000]})
        self.assertEqual(answers.nav_impact_by_day(self.conn, "v"), [])

    def test_a_price_held_by_two_snapshots_is_counted_once(self):
        self.load("2026-09-30", ["2026-09-25", "2026-09-28", "2026-09-29"], {})
        self.assertEqual(answers.stored_prices(self.conn, "v"), (10, "2026-09-22", "2026-09-29"))


class FeedsUnderTestTest(unittest.TestCase):
    def test_rotated_sources_do_not_get_the_questions(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(conn.close)
        db.apply_schema(conn)
        for source in ("eodhd", "yahoo"):
            conn.execute(
                """
                INSERT INTO ingest_run (run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                    symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256)
                VALUES (?, ?, '2026-09-29', '', '2026-09-28', '', 0, 0, 0, '', '')
                """,
                (source, source),
            )
        settings = {"yahoo": {}, "eodhd": {"daily_batch": 18}}
        with mock.patch.object(config, "sources", return_value=settings):
            self.assertEqual(answers.feeds_under_test(conn), ["yahoo"])


if __name__ == "__main__":
    unittest.main()
