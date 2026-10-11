"""Monthly periods and parsing of period column headers."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime

_MONTH_WORDS: dict[str, int] = {}
for _i in range(1, 13):
    _MONTH_WORDS[calendar.month_abbr[_i].lower()] = _i
    _MONTH_WORDS[calendar.month_name[_i].lower()] = _i
_MONTH_WORDS["sept"] = 9

_ISO = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$")
_SHORT_YEAR = re.compile(r"^([A-Za-z]+)[-' ]+'?(\d{2})$")  # 'Jun-26', "Jun '26"
_TOKEN = re.compile(r"[A-Za-z]+|\d+")


@dataclass(frozen=True, order=True)
class Period:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{calendar.month_abbr[self.month]} {self.year}"

    @property
    def ordinal(self) -> int:
        return self.year * 12 + (self.month - 1)

    def shift(self, months: int) -> "Period":
        n = self.ordinal + months
        return Period(n // 12, n % 12 + 1)

    @classmethod
    def from_key(cls, key: str) -> "Period":
        m = _ISO.match(key.strip())
        if not m:
            raise ValueError(f"Invalid period key {key!r}; expected YYYY-MM")
        return cls(int(m.group(1)), int(m.group(2)))


def parse_period(value: object) -> Period | None:
    """Parse a column header into a Period, or return None if it is not a single month.

    Accepts datetime/date cells and strings such as 'Jun 2026', 'June 2026', 'Jun-26',
    '30 Jun 2026', 'June 30, 2026', '2026-06' and '2026-06-30'. Strings that contain
    anything else (e.g. 'Jan - Jun 2026', 'Total', report titles) are rejected.
    """
    if isinstance(value, datetime):
        return Period(value.year, value.month)
    if isinstance(value, date):
        return Period(value.year, value.month)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    iso = _ISO.match(text)
    if iso:
        month = int(iso.group(2))
        return Period(int(iso.group(1)), month) if 1 <= month <= 12 else None

    short = _SHORT_YEAR.match(text)
    if short:
        month = _MONTH_WORDS.get(short.group(1).lower())
        return Period(2000 + int(short.group(2)), month) if month else None

    month = year = None
    day_seen = False
    for token in _TOKEN.findall(text):
        if token.isalpha():
            if token.lower() not in _MONTH_WORDS or month is not None:
                return None
            month = _MONTH_WORDS[token.lower()]
        elif len(token) == 4 and year is None:
            year = int(token)
        elif len(token) <= 2 and not day_seen and 1 <= int(token) <= 31:
            day_seen = True
        else:
            return None
    if month is None or year is None:
        return None
    return Period(year, month)


def consecutive_gaps(periods: list[Period]) -> list[Period]:
    """Return the months missing between the first and last of the (sorted) periods."""
    if not periods:
        return []
    present = {p.ordinal for p in periods}
    start, end = min(present), max(present)
    return [Period(n // 12, n % 12 + 1) for n in range(start, end + 1) if n not in present]
