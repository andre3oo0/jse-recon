"""Seeded break suite: one planted defect per security, and the recon must find exactly those."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, db, recon, staging

RULES = {
    "price_recon": {"test": {"close": {"abs_floor_zar": 0.05, "rel_pct": 0.50}}},
    "dq": {"unit_ratio_band": [80, 125], "stale_price_days": 5},
}
DAYS = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-25", "2026-09-28"]  # 24th is Heritage Day
BASE = dict(zip(DAYS, [10000, 10040, 9980, 10020, 10010]))  # cents; a real price moves most days
FLAT = {d: 10000 for d in DAYS}  # five identical closes, which is itself a stale price


class ReconTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(config, "tolerance_rules", return_value=RULES)
        patch.start()
        self.addCleanup(patch.stop)

        self.conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        for source in ("a", "b"):
            self.conn.execute(
                """
                INSERT INTO ingest_run (
                    run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                    symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
                ) VALUES (?, ?, '2026-09-29', '2026-09-29T08:00:00+00:00', '2026-09-28', '5d', 0, 0, 0, '', '')
                """,
                (f"run_{source}", source),
            )
        self.a, self.b = recon.Side("a", "2026-09-29"), recon.Side("b", "2026-09-29")

    def security(self, sec, a=None, b=None, mapped=True, requested="ab"):
        for source in requested:
            self.conn.execute(
                "INSERT INTO ingest_symbol_status (run_id, vendor_symbol, status, row_count) VALUES (?, ?, 'ok', 5)",
                (f"run_{source}", f"{sec}.{source}"),
            )
        if mapped:
            self.conn.execute("INSERT INTO security_master (security_id, name) VALUES (?, ?)", (sec, sec))
        for source, closes in (("a", BASE if a is None else a), ("b", BASE if b is None else b)):
            if mapped:
                self.conn.execute(
                    "INSERT INTO security_xref (source, vendor_symbol, security_id) VALUES (?, ?, ?)",
                    (source, f"{sec}.{source}", sec),
                )
            rows = closes if isinstance(closes, list) else list(closes.items())
            self.conn.executemany(
                "INSERT INTO raw_price (source, snapshot_date, vendor_symbol, price_date, close, "
                "adj_close, volume, reported_unit, run_id) VALUES (?, '2026-09-29', ?, ?, ?, ?, 1000, 'ZAc', ?)",
                [(source, f"{sec}.{source}", day, close, close, f"run_{source}") for day, close in rows],
            )

    def reconcile(self):
        self.conn.commit()
        staging.build(self.conn)
        run_id = recon.run(self.conn, "test", self.a, self.b, "test")
        rows = self.conn.execute(
            "SELECT key_id, price_date, status, explanation FROM recon_result WHERE recon_run_id = ?",
            (run_id,),
        ).fetchall()
        return run_id, {(k, d): (s, e) for k, d, s, e in rows}

    def test_seeded_breaks_are_found_and_nothing_else(self):
        self.security("MAT")
        self.security("SUB", b=BASE | {"2026-09-28": 10050})  # +0.4%: under tolerance
        self.security("VAL", b=BASE | {"2026-09-28": 10110})  # +1.0%
        self.security("FLR", a={d: 50 for d in DAYS}, b={d: 50 for d in DAYS} | {"2026-09-28": 52})  # 4% but R0.02
        self.security("ONA", b={d: c for d, c in BASE.items() if d != "2026-09-28"})
        self.security("ONB", a={d: c for d, c in BASE.items() if d != "2026-09-28"})
        self.security("DUP", a=list(BASE.items()) + [("2026-09-28", 10010)])
        self.security("UNI", b=BASE | {"2026-09-28": 100.1})  # rands in a cents series, on the latest bar
        self.security("CAL", b=BASE | {"2026-09-24": 10000})
        self.security("STL", a=FLAT, b=BASE | {"2026-09-28": 10200})  # A never moved while B did
        self.security("GLT", a=BASE | {"2026-09-23": 99.8}, b=BASE | {"2026-09-23": 99.8})
        self.security("ZZZ", a={}, b={"2026-09-28": 10000}, mapped=False)

        _, results = self.reconcile()
        breaks = {key: s for key, (s, _) in results.items() if s != "MATCH"}
        self.assertEqual(breaks, {
            ("VAL", "2026-09-28"): "VAL",
            ("ONA", "2026-09-28"): "ONE_A",
            ("ONB", "2026-09-28"): "ONE_B",
            ("DUP", "2026-09-28"): "DUP",
            ("UNI", "2026-09-28"): "UNIT",
            ("CAL", "2026-09-24"): "CAL",
            ("STL", "2026-09-28"): "STALE",
            ("UNMAPPED:ZZZ.b", "2026-09-28"): "ONE_B",
        })
        self.assertEqual(results[("GLT", "2026-09-23")],
                         ("MATCH", "Both sides carry the same unit anomaly"))
        self.assertEqual(len(results), 11 * len(DAYS) + 1 + 1)  # every key, plus the CAL and unmapped rows

    def test_explanations_state_the_rule(self):
        self.security("VAL", b=BASE | {"2026-09-28": 10110})
        _, results = self.reconcile()
        self.assertEqual(results[("VAL", "2026-09-28")][1],
                         "Differs by R1.00 (1.00%), over both R0.05 and 0.50%")

    def test_dates_outside_the_shared_window_are_not_breaks(self):
        # A longer lookback on one side must not become a run of one-sided breaks
        self.security("OLD", a={"2026-09-18": 10000, **BASE})
        _, results = self.reconcile()
        self.assertNotIn(("OLD", "2026-09-18"), results)
        self.assertEqual({s for s, _ in results.values()}, {"MATCH"})

    def test_security_requested_on_one_side_only_is_out_of_scope(self):
        # NTU joined the universe after the earlier snapshot: a scope difference, not 63 breaks
        self.security("MAT")
        self.security("NEW", a={}, requested="b")
        _, results = self.reconcile()
        self.assertEqual({k for k, _ in results}, {"MAT"})
        self.assertEqual(recon.requested(self.conn, self.b) - recon.requested(self.conn, self.a), {"NEW"})

    def test_rerun_replaces_the_previous_result(self):
        self.security("VAL", b=BASE | {"2026-09-28": 10110})
        run_id, first = self.reconcile()
        _, second = self.reconcile()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM recon_result WHERE recon_run_id = ?", (run_id,)
        ).fetchone()[0]
        self.assertEqual(first, second)
        self.assertEqual(count, len(DAYS))


if __name__ == "__main__":
    unittest.main()
