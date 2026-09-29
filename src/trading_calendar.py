"""JSE trading calendar: weekdays minus South African public holidays."""

import sqlite3
from datetime import date, timedelta

from src import config

FIXED_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (3, 21): "Human Rights Day",
    (4, 27): "Freedom Day",
    (5, 1): "Workers' Day",
    (6, 16): "Youth Day",
    (8, 9): "National Women's Day",
    (9, 24): "Heritage Day",
    (12, 16): "Day of Reconciliation",
    (12, 25): "Christmas Day",
    (12, 26): "Day of Goodwill",
}

DEFAULT_RANGE = (date(2000, 1, 1), date(2035, 12, 31))


def easter_sunday(year: int) -> date:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def statutory_holidays(year: int) -> dict[date, str]:
    holidays = {}
    for (month, day), name in FIXED_HOLIDAYS.items():
        holiday = date(year, month, day)
        holidays.setdefault(holiday, name)
        # Public Holidays Act s2(1): a holiday on a Sunday is observed on the Monday
        if holiday.weekday() == 6:
            holidays.setdefault(holiday + timedelta(days=1), f"{name} (observed)")
    easter = easter_sunday(year)
    holidays[easter - timedelta(days=2)] = "Good Friday"
    holidays[easter + timedelta(days=1)] = "Family Day"
    return holidays


def special_closures() -> dict[date, str]:
    entries = config.load_yaml("jse_calendar.yaml")["special_closures"]
    return {date.fromisoformat(str(e["date"])): e["reason"] for e in entries}


def build(start: date, end: date) -> list[tuple[str, int, str | None]]:
    closed = {}
    for year in range(start.year, end.year + 1):
        closed |= statutory_holidays(year)
    closed |= special_closures()

    rows, day = [], start
    while day <= end:
        if day.weekday() >= 5:
            rows.append((day.isoformat(), 0, "Weekend"))
        elif day in closed:
            rows.append((day.isoformat(), 0, closed[day]))
        else:
            rows.append((day.isoformat(), 1, None))
        day += timedelta(days=1)
    return rows


def load(conn: sqlite3.Connection, start: date = DEFAULT_RANGE[0], end: date = DEFAULT_RANGE[1]) -> None:
    with conn:
        conn.execute("DELETE FROM trading_calendar")
        conn.executemany("INSERT INTO trading_calendar VALUES (?, ?, ?)", build(start, end))
