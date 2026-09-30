"""The break register: episodes, clearing, ageing, notes, and the replay that feeds it."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, db, lifecycle, recon, trading_calendar

D1, D2, D3, D4, D5, D6 = "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"
DAY = "2026-09-28"
NAME = "yahoo_vs_eodhd"


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        trading_calendar.load(self.conn)
        self.notes = []
        real = config.load_yaml
        patch = mock.patch.object(
            config, "load_yaml", side_effect=lambda f: {"notes": self.notes} if f == "break_notes.yaml" else real(f)
        )
        patch.start()
        self.addCleanup(patch.stop)

    def observe(self, as_of, results, name=NAME):
        run_id = f"{name}:{as_of}"
        self.conn.execute(
            """
            INSERT OR IGNORE INTO recon_run (recon_run_id, recon_name, source_a, snapshot_a, source_b, snapshot_b,
                                             window_start, window_end, abs_floor_zar, rel_pct)
            VALUES (?, ?, 'a', ?, 'b', ?, '', '', 0.01, 0.05)
            """,
            (run_id, name, as_of, as_of),
        )
        self.conn.executemany(
            "INSERT INTO recon_result (recon_run_id, key_id, price_date, status, explanation) VALUES (?, ?, ?, ?, ?)",
            [(run_id, key, DAY, status, f"{status} on {as_of}") for key, status in results.items()],
        )

    def episodes(self):
        lifecycle.build(self.conn)
        rows = self.conn.execute(
            "SELECT key_id, first_seen, cleared_on, first_status, latest_status, age_days, state "
            "FROM break_episode ORDER BY key_id, first_seen"
        ).fetchall()
        return [dict(zip(["key", "first", "cleared", "first_status", "latest_status", "age", "state"], r))
                for r in rows]

    def history(self):
        self.observe(D1, {"TIM": "VAL", "OPN": "VAL", "ROT": "VAL", "REO": "VAL"})
        self.observe(D2, {"TIM": "VAL", "OPN": "UNIT", "REO": "MATCH"})
        self.observe(D3, {"TIM": "MATCH", "OPN": "VAL", "REO": "VAL"})
        self.observe(D4, {"OPN": "VAL"})
        self.observe(D5, {"OPN": "VAL", "ROT": "MATCH"})
        self.observe(D6, {"OPN": "VAL"})

    def test_episodes_clear_age_and_reopen(self):
        self.history()
        self.assertEqual(self.episodes(), [
            {"key": "OPN", "first": D1, "cleared": None, "first_status": "VAL", "latest_status": "VAL",
             "age": 5, "state": "OPEN"},
            {"key": "REO", "first": D1, "cleared": D2, "first_status": "VAL", "latest_status": "VAL",
             "age": 1, "state": "TIMING"},
            {"key": "REO", "first": D3, "cleared": None, "first_status": "VAL", "latest_status": "VAL",
             "age": 3, "state": "OPEN"},
            {"key": "ROT", "first": D1, "cleared": D5, "first_status": "VAL", "latest_status": "VAL",
             "age": 4, "state": "CLEARED"},
            {"key": "TIM", "first": D1, "cleared": D3, "first_status": "VAL", "latest_status": "VAL",
             "age": 2, "state": "TIMING"},
        ])

    def test_age_from_the_price_date_and_breaks_found_on_the_first_comparison(self):
        self.observe(D1, {"OLD": "VAL", "NEW": "MATCH"})
        self.observe(D2, {"OLD": "VAL", "NEW": "VAL"})
        lifecycle.build(self.conn)
        rows = dict((k, (a, p, f)) for k, a, p, f in self.conn.execute(
            "SELECT key_id, age_days, price_age_days, found_on_first_comparison FROM break_episode"))
        self.assertEqual(rows["OLD"], (1, 4, 1))  # found on its first comparison; priced 28 Sep, four sessions before D2
        self.assertEqual(rows["NEW"], (0, 4, 0))  # matched first, so this break is new

    def test_a_day_without_a_comparison_neither_breaks_nor_clears(self):
        # ROT was not in EODHD's rotation on D2 to D4, so it stays one episode until the D5 match
        self.history()
        rot = [e for e in self.episodes() if e["key"] == "ROT"]
        self.assertEqual(len(rot), 1)
        self.assertEqual(rot[0]["cleared"], D5)

    def test_restatement_breaks_are_events_not_episodes(self):
        self.observe(D1, {"XYZ": "ONE_B"}, name="yahoo_restatement")
        self.assertEqual(self.episodes(), [])

    def test_summary_counts_and_ages(self):
        self.history()
        lifecycle.build(self.conn)
        s = lifecycle.summary(self.conn, NAME)
        self.assertEqual((s["episodes"], s["open"], s["timing"], s["cleared"]), (5, 2, 2, 1))
        self.assertEqual(s["buckets"], {"0-1": 0, "2-5": 2, "6-20": 0, "21+": 0})
        self.assertAlmostEqual(s["mean_days_to_clear"], (1 + 4 + 2) / 3)

    def test_notes_attach_and_bad_codes_are_refused(self):
        self.notes = [{"recon": NAME, "key": "OPN", "price_date": DAY, "resolution": "VENDOR_ERROR_B",
                       "note": "EODHD close differs from the JSE's published close"}]
        self.history()
        lifecycle.build(self.conn)
        self.assertEqual(self.conn.execute("SELECT key_id, resolution FROM break_note").fetchall(),
                         [("OPN", "VENDOR_ERROR_B")])
        self.notes = [{"recon": NAME, "key": "OPN", "price_date": DAY, "resolution": "FIXED", "note": "x"}]
        with self.assertRaises(lifecycle.BadNote):
            lifecycle.build(self.conn)


class ReplayTest(unittest.TestCase):
    def test_pairs_same_day_snapshots_and_skips_restatement_for_rotated_sources(self):
        conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(conn.close)
        db.apply_schema(conn)
        for source, day in [("yahoo", D1), ("yahoo", D2), ("yahoo", D3), ("eodhd", D2), ("eodhd", D3)]:
            conn.execute(
                """
                INSERT INTO ingest_run (run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                    symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256)
                VALUES (?, ?, ?, '', ?, '', 0, 0, 0, '', '')
                """,
                (f"{source}{day}", source, day, day),
            )
        settings = {"yahoo": {}, "eodhd": {"daily_batch": 18}, "recon_pairs": [["yahoo", "eodhd"]]}
        calls = []
        with mock.patch.object(config, "sources", return_value=settings), \
             mock.patch.object(recon, "run", side_effect=lambda c, name, a, b, t: calls.append((name, str(a), str(b)))):
            recon.replay(conn)
        self.assertEqual(calls, [
            ("yahoo_restatement", f"yahoo@{D1}", f"yahoo@{D2}"),
            ("yahoo_restatement", f"yahoo@{D2}", f"yahoo@{D3}"),
            ("yahoo_vs_eodhd", f"yahoo@{D2}", f"eodhd@{D2}"),
            ("yahoo_vs_eodhd", f"yahoo@{D3}", f"eodhd@{D3}"),
        ])


if __name__ == "__main__":
    unittest.main()
