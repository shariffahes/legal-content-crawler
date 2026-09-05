from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Iterator


class PartitionSize(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class Partition:
    key: str
    partition_date: date
    start: date
    end: date
    truncated: bool

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _period_bounds(day: date, size: PartitionSize) -> tuple[date, date, str]:
    match size:
        case PartitionSize.DAY:
            return day, day, day.isoformat()
        case PartitionSize.WEEK:
            start = day - timedelta(days=day.weekday())
            iso_year, iso_week, _ = start.isocalendar()
            return start, start + timedelta(days=6), f"{iso_year}-W{iso_week:02d}"
        case PartitionSize.MONTH:
            last = calendar.monthrange(day.year, day.month)[1]
            return (
                day.replace(day=1),
                day.replace(day=last),
                f"{day.year}-{day.month:02d}",
            )
        case PartitionSize.QUARTER:
            quarter = (day.month - 1) // 3
            first_month = quarter * 3 + 1
            last_month = first_month + 2
            last_day = calendar.monthrange(day.year, last_month)[1]
            return (
                date(day.year, first_month, 1),
                date(day.year, last_month, last_day),
                f"{day.year}-Q{quarter + 1}",
            )
        case PartitionSize.YEAR:
            return date(day.year, 1, 1), date(day.year, 12, 31), str(day.year)
    raise ValueError(f"unsupported partition size: {size}")


def iter_partitions(start: date, end: date, size: PartitionSize) -> Iterator[Partition]:
    """Yield calendar-aligned partitions covering [start, end] inclusive."""
    if end < start:
        raise ValueError(f"end date {end} precedes start date {start}")

    cursor = start
    while cursor <= end:
        period_start, period_end, key = _period_bounds(cursor, size)
        window_start = max(period_start, start)
        window_end = min(period_end, end)
        yield Partition(
            key=key,
            partition_date=period_start,
            start=window_start,
            end=window_end,
            truncated=(window_start, window_end) != (period_start, period_end),
        )
        cursor = period_end + timedelta(days=1)
