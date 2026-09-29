"""Vendor adapters against canned responses, and the rotation that shares out a daily call budget."""

import json
import tempfile
import unittest
from pathlib import Path

from src import db, security_master
from src.sources.afx import AfxSource
from src.sources.eodhd import EodhdSource, MissingApiKey

KEY = "sk-test-0000"

AFX_PAGE = """
<html><body>
<p>The International Securities Identification Number (<abbr>ISIN</abbr>) of JSE:SOL is ZAE000006896.</p>
<p>Monetary values are quoted in South African Rand (ZAR) unless otherwise stated</p>
<table><tr><th>Date</th><th>Volume</th><th>Close</th><th>Change</th><th>Change%</th></tr>
<tr><td>2026-09-28</td><td>2764940</td><td>230.24</td><td>-0.76</td><td>-0.33%</td></tr>
<tr><td>2026-09-25</td><td>1855854</td><td>231.00</td><td>-0.49</td><td>-0.21%</td></tr></table>
</body></html>
"""


class Response:
    def __init__(self, status_code, body):
        self.status_code, self.text = status_code, body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Http:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        result = self.responses[url.rsplit("/", 1)[-1]]
        if isinstance(result, Exception):
            raise result
        return result


class EodhdTest(unittest.TestCase):
    def source(self, responses):
        src = EodhdSource(api_key=KEY)
        src.http = Http(responses)
        return src

    def test_parses_end_of_day_rows(self):
        rows = [{"date": "2026-09-28", "open": 1, "high": 2, "low": 1, "close": 23024,
                 "adjusted_close": 23024, "volume": 2764940}]
        prices, [status] = self.source({"SOL.JSE": Response(200, rows)}).fetch(["SOL.JSE"], "1y")
        self.assertEqual(status.status, "ok")
        self.assertEqual(prices.iloc[0][["price_date", "close", "adj_close", "reported_unit"]].tolist(),
                         ["2026-09-28", 23024, 23024, None])

    def test_plan_refusal_is_recorded_without_retrying(self):
        src = self.source({"SOL.JSE": Response(403, "Forbidden")})
        _, [status] = src.fetch(["SOL.JSE"], "1y")
        self.assertEqual((status.status, status.error), ("error", "HTTP 403: Forbidden"))
        self.assertEqual(len(src.http.calls), 1)

    def test_api_key_never_reaches_the_record(self):
        src = self.source({"SOL.JSE": ConnectionError(f"failed: https://eodhd.com/api/eod/SOL.JSE?api_token={KEY}")})
        src.attempts = 1
        _, [status] = src.fetch(["SOL.JSE"], "1y")
        self.assertNotIn(KEY, status.error)
        self.assertIn("api_token=***", status.error)

    def test_missing_key_fails_loudly(self):
        with self.assertRaises(MissingApiKey):
            EodhdSource(api_key="").fetch(["SOL.JSE"], "1y")


class AfxTest(unittest.TestCase):
    def test_parses_history_unit_and_isin(self):
        prices, status = AfxSource.parse("SOL", AFX_PAGE)
        self.assertEqual((status.status, status.reported_unit, status.isin), ("ok", "ZAR", "ZAE000006896"))
        self.assertEqual(prices[["price_date", "close", "volume"]].values.tolist(),
                         [["2026-09-28", 230.24, 2764940], ["2026-09-25", 231.00, 1855854]])

    def test_unit_is_not_assumed_when_the_page_does_not_state_it(self):
        _, status = AfxSource.parse("SOL", AFX_PAGE.replace("South African Rand (ZAR)", "Rand"))
        self.assertIsNone(status.reported_unit)

    def test_waits_between_pages_as_robots_txt_asks(self):
        waits = []
        src = AfxSource("https://afx.test/jse/{code}.html", crawl_delay_seconds=60, sleep=waits.append)
        src.http = Http({"sol.html": Response(200, AFX_PAGE), "npn.html": Response(404, ""), "sbk.html": Response(200, AFX_PAGE)})
        _, statuses = src.fetch(["SOL", "NPN", "SBK"], "10d")
        self.assertEqual(waits, [60, 60])
        self.assertEqual([s.status for s in statuses], ["ok", "empty", "ok"])


    def test_stops_after_repeated_failures(self):
        waits = []
        src = AfxSource("https://afx.test/jse/{code}.html", sleep=waits.append, attempts=1, give_up_after=2)
        src.http = Http({f"{c}.html": ConnectionError("refused") for c in ("aaa", "bbb", "ccc", "ddd")})
        _, statuses = src.fetch(["AAA", "BBB", "CCC", "DDD"], "10d")
        self.assertEqual(len(src.http.calls), 2)
        self.assertEqual([s.error for s in statuses[2:]], ["Skipped after 2 consecutive failures"] * 2)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(Path(tempfile.mkdtemp()) / "test.db")
        self.addCleanup(self.conn.close)
        db.apply_schema(self.conn)
        for sec in ("AAA", "BBB", "CCC", "DDD"):
            self.conn.execute("INSERT INTO security_master (security_id, name) VALUES (?, ?)", (sec, sec))
            self.conn.execute("INSERT INTO security_xref (source, vendor_symbol, security_id) VALUES ('v', ?, ?)",
                              (sec, sec))

    def tried(self, snapshot_date, symbols):
        run_id = f"r{snapshot_date}"
        self.conn.execute(
            """
            INSERT INTO ingest_run (
                run_id, source, snapshot_date, fetched_at, session_cutoff, lookback_period,
                symbols_requested, symbols_returned, rows_landed, landing_path, landing_sha256
            ) VALUES (?, 'v', ?, '', ?, '', 0, 0, 0, '', '')
            """,
            (run_id, snapshot_date, snapshot_date),
        )
        self.conn.executemany(
            "INSERT INTO ingest_symbol_status VALUES (?, ?, 'ok', 1, NULL, NULL, NULL)",
            [(run_id, s) for s in symbols],
        )

    def test_never_tried_first_then_least_recently_tried(self):
        self.tried("2026-09-21", ["AAA"])
        self.tried("2026-09-22", ["BBB"])
        self.assertEqual(security_master.rotation_batch(self.conn, "v", 3, "2026-09-23"), ["CCC", "DDD", "AAA"])

    def test_same_day_refetch_gets_the_same_batch(self):
        self.tried("2026-09-22", ["AAA", "BBB"])
        first = security_master.rotation_batch(self.conn, "v", 2, "2026-09-23")
        self.tried("2026-09-23", first)
        self.assertEqual(security_master.rotation_batch(self.conn, "v", 2, "2026-09-23"), first)


if __name__ == "__main__":
    unittest.main()
