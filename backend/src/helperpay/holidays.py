"""US federal holidays, computed (no data files, no network)."""

from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def us_federal_holidays(year: int) -> dict[date, str]:
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "MLK Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        _last_weekday(year, 5, 0): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Columbus Day",
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas Day",
    }
