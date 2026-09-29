"""Holdings recon: South African amounts, ISIN mapping, T+3 settlement, and the planted fixture outcomes."""

import csv
import tempfile
import unittest
from pathlib import Path

from src import db, holdings, synthetic_holdings, trading_calendar


class ParseMoneyTest(unittest.TestCase):
    def test_south_african_formats(self):
        cases = {
            "R2 000.00": 2000.0, "R2\u00a0000.00": 2000.0, "R12\u202f345.67": 12345.67, "R2,000.00": 2000.0,
            "R2 000,50": 2000.5, "R1,234": 1234.0, "-R5.00": -5.0, "R 16.03": 16.03,
        }
        for text, value in cases.items():
            self.assertEqual(holdings.parse_money(text), value, text)

    def test_unreadable_amounts_raise(self):
        for text in ("R\u2014", "", "N/A", "R1.2.3"):
            with self.assertRaises(ValueError, msg=text):
                holdings.parse_money(text)


class HoldingsReconTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.conn = db.connect(self.tmp / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        trading_calendar.load(self.conn)
        self.conn.executemany(
            "INSERT INTO security_master (security_id, name, isin, status) VALUES (?, ?, ?, ?)",
            [("AAA", "Alpha", None, "active"), ("TCP", "Transaction Capital", "ZAE000167391", "retired"),
             ("NTU", "Nutun", "ZAE000167391", "active")],
        )
        closes = {"2026-09-23": {"AAA": 100.0, "NTU": 0.92}, "2026-09-25": {"AAA": 101.0, "NTU": 0.90}}
        self.conn.executemany(
            "INSERT INTO stg_price (source, snapshot_date, security_id, vendor_symbol, price_date, close_zar, run_id) "
            "VALUES ('yahoo', '2026-09-29', ?, ?, ?, ?, 'r')",
            [(sec, sec, day, c) for day, px in closes.items() for sec, c in px.items()],
        )
        self.conn.commit()

    def files(self, lines, txns, as_of="2026-09-25"):
        export, ledger = self.tmp / f"statement_{as_of}.csv", self.tmp / "ledger.csv"
        for path, rows, fields in (
            (export, lines, ["name", "contract_code", "purchase_value", "current_value", "current_price", "isin"]),
            (ledger, txns, ["reference", "trade_date", "security_id", "side", "quantity", "price_zar", "fees_zar"]),
        ):
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
        file_id = holdings.run(self.conn, export, ledger)
        return holdings.results(self.conn, file_id)

    def line(self, code, value, price, isin=""):
        return {"name": code, "contract_code": f"EQU.ZA.{code}", "purchase_value": "R1 000.00",
                "current_value": value, "current_price": price, "isin": isin}

    def buy(self, ref, day, sec, qty):
        return {"reference": ref, "trade_date": day, "security_id": sec, "side": "BUY", "quantity": qty,
                "price_zar": "100", "fees_zar": "0"}

    def test_old_contract_code_is_matched_by_isin(self):
        got = self.files([self.line("TCP", "R90.00", "R0.90", isin="ZAE000167391")],
                         [self.buy("T1", "2026-06-01", "NTU", "100")])
        self.assertEqual(got, {"NTU": "MATCH"})

    def test_unsettled_trade_across_a_holiday_is_settle_not_qty(self):
        # Bought Monday 21st and Wednesday 23rd; with Heritage Day on the 24th, the 23rd settles on the 29th
        txns = [self.buy("T1", "2026-09-21", "AAA", "10"), self.buy("T2", "2026-09-23", "AAA", "5")]
        self.assertEqual(self.files([self.line("AAA", "R1 010.00", "R101.00")], txns), {"AAA": "SETTLE"})

    def test_a_missing_settled_trade_is_a_quantity_break(self):
        txns = [self.buy("T1", "2026-09-01", "AAA", "10"), self.buy("T2", "2026-09-02", "AAA", "5")]
        self.assertEqual(self.files([self.line("AAA", "R1 010.00", "R101.00")], txns), {"AAA": "QTY"})

    def test_unreadable_value_has_an_unknown_difference(self):
        self.files([self.line("AAA", "R\u2014", "R101.00")], [self.buy("T1", "2026-09-01", "AAA", "10")])
        status, diff = self.conn.execute("SELECT status, value_diff FROM holding_recon_result").fetchone()
        self.assertEqual((status, diff), ("PARSE", None))


class FixtureTest(unittest.TestCase):
    def test_committed_fixture_expectations_match_the_generator(self):
        with holdings.EXPECTED.open(encoding="utf-8", newline="") as f:
            committed = {r["key_id"]: r["status"] for r in csv.DictReader(f)}
        self.assertEqual(committed, synthetic_holdings.EXPECTED)

    def test_fixture_is_labelled_synthetic(self):
        self.assertIn("SYNTHETIC", holdings.EXPORT.name)
        self.assertIn("SYNTHETIC", holdings.LEDGER.name)


if __name__ == "__main__":
    unittest.main()
