"""Staging: mapping, unit conversion, unit anomaly flags and calendar exceptions."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, db, staging


class StagingTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(tmp / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        self.conn.executemany(
            "INSERT INTO security_master (security_id, name) VALUES (?, ?)",
            [("AAA", "Alpha"), ("BBB", "Beta")],
        )
        self.conn.executemany(
            "INSERT INTO security_xref (source, vendor_symbol, security_id, valid_from, valid_to) "
            "VALUES ('v', ?, ?, ?, ?)",
            [
                ("AAA.V", "AAA", None, None),
                ("BBB.V", "BBB", None, "2026-09-22"),
                ("BBB2.V", "BBB", "2026-09-23", None),
            ],
        )
        # Taken on 29 September before the close, so sessions are complete to the 28th
        self.conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES ('r1', 'v', '2026-09-29', '2026-09-29T08:00:00+00:00', '2026-09-28', '5d', 0, 0, 0, '', '')
            """
        )

    def bars(self, symbol, closes, unit="ZAc"):
        self.conn.executemany(
            "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, adj_close, "
            "volume, reported_unit, run_id) VALUES ('v', '2026-09-29', ?, ?, ?, ?, 1000, ?, 'r1')",
            [(symbol, day, close, close, unit) for day, close in closes.items()],
        )
        self.conn.commit()

    def staged(self, symbol):
        staging.build(self.conn)
        rows = self.conn.execute(
            "SELECT price_date, security_id, close_zar, unit_anomaly FROM stg_price "
            "WHERE vendor_symbol = ? ORDER BY price_date",
            (symbol,),
        ).fetchall()
        return {day: (sec, close, anomaly) for day, sec, close, anomaly in rows}

    def anomalies(self, symbol):
        return {day: a for day, (_, _, a) in self.staged(symbol).items() if a}

    def test_cents_become_rands(self):
        self.bars("AAA.V", {"2026-09-25": 22998})
        self.assertAlmostEqual(self.staged("AAA.V")["2026-09-25"][1], 229.98)

    def test_assumed_unit_applies_only_where_the_vendor_said_nothing(self):
        self.bars("AAA.V", {"2026-09-25": 22998}, unit=None)
        self.bars("ZZZ.V", {"2026-09-25": 229.98}, unit="ZAR")
        with mock.patch.object(config, "sources", return_value={"v": {"assumed_unit": "ZAc"}}):
            staging.build(self.conn)
        rows = dict(((sym, (unit, assumed, round(close, 2))) for sym, unit, assumed, close in self.conn.execute(
            "SELECT vendor_symbol, reported_unit, unit_assumed, close_zar FROM stg_price")))
        self.assertEqual(rows, {"AAA.V": ("ZAc", 1, 229.98), "ZZZ.V": ("ZAR", 0, 229.98)})

    def test_unknown_unit_is_left_unconverted(self):
        self.bars("AAA.V", {"2026-09-25": 22998}, unit="USD")
        self.assertIsNone(self.staged("AAA.V")["2026-09-25"][1])

    def test_single_bar_rand_glitch_is_flagged(self):
        # Vodacom on 2025-01-10: 10,140c, then 101, then 9,984c
        self.bars("AAA.V", {"2026-09-21": 10140, "2026-09-22": 101, "2026-09-23": 9984})
        self.assertEqual(self.anomalies("AAA.V"), {"2026-09-22": "too_small"})

    def test_glitch_is_flagged_when_the_price_also_moved(self):
        # CMH on 2025-04-25: a 10% move the next day took it outside a +/-5% band
        self.bars("AAA.V", {"2026-09-21": 2959, "2026-09-22": 30, "2026-09-23": 3250})
        self.assertEqual(self.anomalies("AAA.V"), {"2026-09-22": "too_small"})

    def test_glitch_on_the_latest_bar_is_flagged(self):
        self.bars("AAA.V", {"2026-09-22": 9990, "2026-09-23": 9984, "2026-09-25": 99.84})
        self.assertEqual(self.anomalies("AAA.V"), {"2026-09-25": "too_small"})

    def test_two_bars_100x_apart_are_not_judged(self):
        # Either bar could be the wrong one, so neither is flagged until a third arrives
        self.bars("AAA.V", {"2026-09-23": 9984, "2026-09-25": 99.84})
        self.assertEqual(self.anomalies("AAA.V"), {})

    def test_cents_bar_in_a_rand_series_is_flagged(self):
        self.bars("AAA.V", {"2026-09-21": 101.4, "2026-09-22": 10140, "2026-09-23": 99.84}, unit="ZAR")
        self.assertEqual(self.anomalies("AAA.V"), {"2026-09-22": "too_large"})

    def test_share_consolidation_is_not_flagged(self):
        self.bars("AAA.V", {"2026-09-21": 50, "2026-09-22": 51, "2026-09-23": 5000, "2026-09-25": 5050})
        self.assertEqual(self.anomalies("AAA.V"), {})

    def test_unmapped_symbol_is_kept(self):
        self.bars("ZZZ.V", {"2026-09-25": 100})
        self.assertIsNone(self.staged("ZZZ.V")["2026-09-25"][0])

    def test_mapping_respects_validity_dates(self):
        self.bars("BBB.V", {"2026-09-22": 100, "2026-09-23": 100})
        self.bars("BBB2.V", {"2026-09-23": 100})
        staged = self.staged("BBB.V")
        self.assertEqual(staged["2026-09-22"][0], "BBB")
        self.assertIsNone(staged["2026-09-23"][0])
        self.assertEqual(self.staged("BBB2.V")["2026-09-23"][0], "BBB")

    def test_session_missing_after_the_last_bar_is_reported(self):
        # The 28 September 2026 case: the vendor stops early, inside the completed-session window
        self.bars("AAA.V", {"2026-09-23": 100, "2026-09-25": 100})
        staging.build(self.conn)
        gaps = self.conn.execute(
            "SELECT price_date, symbols_expected, symbols_missing FROM v_session_gap"
        ).fetchall()
        self.assertEqual(gaps, [("2026-09-28", 1, 1)])

    def test_bar_on_a_holiday_is_reported(self):
        self.bars("AAA.V", {"2026-09-23": 100, "2026-09-24": 100, "2026-09-25": 100, "2026-09-28": 100})
        staging.build(self.conn)
        exceptions = self.conn.execute(
            "SELECT price_date, exception, reason FROM v_calendar_exception"
        ).fetchall()
        self.assertEqual(exceptions, [("2026-09-24", "CLOSED_DAY_BAR", "Heritage Day")])


if __name__ == "__main__":
    unittest.main()
