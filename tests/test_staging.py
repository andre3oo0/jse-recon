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


class MovementCheckTest(unittest.TestCase):
    DAYS = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(tmp / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES ('r1', 'v', '2026-09-22', '2026-09-22T18:00:00+00:00', '2026-09-21', '5d', 0, 0, 0, '', '')
            """
        )

    def market(self, overrides=None, shares=12, day_move=0.0):
        rows = []
        for i in range(shares):
            price = 1000.0 + i
            for d, day in enumerate(self.DAYS):
                if d == 3:
                    price *= 1 + day_move
                close = (overrides or {}).get((i, day), price)
                rows.append((f"S{i:02d}.V", day, close, close))
        self.conn.executemany(
            "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, adj_close, volume, "
            "reported_unit, run_id) VALUES ('v', '2026-09-22', ?, ?, ?, ?, 1000, 'ZAc', 'r1')",
            rows,
        )
        self.conn.commit()
        staging.build(self.conn)
        return self.conn.execute("SELECT vendor_symbol, price_date FROM v_price_move ORDER BY 1, 2").fetchall()

    def test_one_share_jumping_while_the_market_is_flat_is_flagged(self):
        moves = self.market({(3, "2026-09-17"): 1003.0 * 1.40, (3, "2026-09-18"): 1003.0 * 1.40,
                             (3, "2026-09-21"): 1003.0 * 1.40})
        self.assertEqual(moves, [("S03.V", "2026-09-17")])

    def test_ninety_nine_percent_fall_outside_the_unit_band_is_flagged(self):
        fallen = {(5, day): 1005.0 * 0.006 for day in self.DAYS[3:]}
        self.assertEqual(self.market(fallen), [("S05.V", "2026-09-17")])

    def test_whole_market_falling_twenty_percent_is_not_flagged(self):
        self.assertEqual(self.market(day_move=-0.20), [])

    def test_twelve_percent_move_is_below_the_threshold(self):
        moved = {(2, day): 1002.0 * 1.12 for day in self.DAYS[3:]}
        self.assertEqual(self.market(moved), [])

    def test_unit_glitch_is_left_to_the_unit_check_not_repeated_here(self):
        glitch = {(4, "2026-09-17"): 1004.0 / 100}
        self.assertEqual(self.market(glitch), [])

    def test_too_few_shares_priced_gives_no_market_to_compare_with(self):
        moved = {(1, day): 1001.0 * 1.50 for day in self.DAYS[3:]}
        self.assertEqual(self.market(moved, shares=5), [])
