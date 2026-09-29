"""The JSE calendar, checked against dates confirmed in Yahoo's five-year history."""

import unittest
from datetime import date

from src import trading_calendar


class TradingCalendarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows = trading_calendar.build(date(2021, 1, 1), date(2026, 12, 31))
        cls.cal = {d: (open_, reason) for d, open_, reason in rows}

    def is_open(self, day):
        return self.cal[day][0] == 1

    def test_easter(self):
        self.assertEqual(trading_calendar.easter_sunday(2024), date(2024, 3, 31))
        self.assertEqual(trading_calendar.easter_sunday(2025), date(2025, 4, 20))
        self.assertEqual(trading_calendar.easter_sunday(2026), date(2026, 4, 5))

    def test_good_friday_and_family_day(self):
        self.assertEqual(self.cal["2025-04-18"], (0, "Good Friday"))
        self.assertEqual(self.cal["2025-04-21"], (0, "Family Day"))

    def test_sunday_holiday_is_observed_on_monday(self):
        # Freedom Day 2025 fell on a Sunday; Yahoo has no bar between 25 and 29 April
        self.assertEqual(self.cal["2025-04-28"], (0, "Freedom Day (observed)"))
        self.assertTrue(self.is_open("2025-04-29"))

    def test_saturday_holiday_is_not_moved(self):
        self.assertEqual(self.cal["2023-12-16"], (0, "Weekend"))
        self.assertTrue(self.is_open("2023-12-18"))

    def test_special_closures(self):
        self.assertEqual(self.cal["2024-05-29"], (0, "National and provincial elections"))
        self.assertFalse(self.is_open("2023-12-15"))
        self.assertFalse(self.is_open("2026-11-04"))

    def test_heritage_day_week_2026(self):
        self.assertFalse(self.is_open("2026-09-24"))
        self.assertTrue(self.is_open("2026-09-25"))
        self.assertTrue(self.is_open("2026-09-28"))


if __name__ == "__main__":
    unittest.main()
