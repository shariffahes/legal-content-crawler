from datetime import date

import pytest

from wrc.partitions import Partition, PartitionSize, iter_partitions


def parts(start: str, end: str, size: PartitionSize) -> list[Partition]:
    return list(iter_partitions(date.fromisoformat(start), date.fromisoformat(end), size))


def test_monthly_year_yields_twelve_untruncated_partitions():
    result = parts("2024-01-01", "2024-12-31", PartitionSize.MONTH)
    assert len(result) == 12
    assert [p.key for p in result[:3]] == ["2024-01", "2024-02", "2024-03"]
    assert not any(p.truncated for p in result)


def test_partitions_tile_the_range_without_gaps_or_overlaps():
    result = parts("2024-01-01", "2024-12-31", PartitionSize.MONTH)
    assert result[0].start == date(2024, 1, 1)
    assert result[-1].end == date(2024, 12, 31)
    for previous, current in zip(result, result[1:]):
        assert (current.start - previous.end).days == 1
    assert sum(p.days for p in result) == 366


def test_leap_day_is_covered():
    february = parts("2024-01-01", "2024-12-31", PartitionSize.MONTH)[1]
    assert february.end == date(2024, 2, 29)


def test_range_edges_are_clamped_and_flagged():
    result = parts("2024-01-15", "2024-03-10", PartitionSize.MONTH)
    assert [p.key for p in result] == ["2024-01", "2024-02", "2024-03"]
    assert (result[0].start, result[0].end) == (date(2024, 1, 15), date(2024, 1, 31))
    assert (result[-1].start, result[-1].end) == (date(2024, 3, 1), date(2024, 3, 10))
    assert [p.truncated for p in result] == [True, False, True]


def test_partition_identity_is_stable_across_different_requested_ranges():
    """A partition addresses a calendar period, not a slice of the request.
    """
    wide = {p.key: p.partition_date for p in parts("2024-01-01", "2024-12-31", PartitionSize.MONTH)}
    narrow = {p.key: p.partition_date for p in parts("2024-02-14", "2024-04-02", PartitionSize.MONTH)}
    assert all(wide[key] == value for key, value in narrow.items())


def test_single_day_range_yields_one_truncated_partition():
    result = parts("2024-06-17", "2024-06-17", PartitionSize.MONTH)
    assert len(result) == 1
    assert result[0].truncated
    assert result[0].days == 1


def test_weeks_align_to_monday():
    result = parts("2024-01-03", "2024-01-20", PartitionSize.WEEK)
    assert result[0].partition_date == date(2024, 1, 1)
    assert result[1].partition_date == date(2024, 1, 8)
    assert [p.key for p in result] == ["2024-W01", "2024-W02", "2024-W03"]


def test_iso_week_keys_can_belong_to_the_following_year():
    """29 Dec 2024 falls in ISO week 2025-W01: the ISO week-year is not the calendar year."""
    result = parts("2024-12-23", "2025-01-05", PartitionSize.WEEK)
    assert [p.key for p in result] == ["2024-W52", "2025-W01"]
    assert result[1].partition_date == date(2024, 12, 30)
    assert result[1].start == date(2024, 12, 30)


def test_quarters_and_years():
    quarters = parts("2024-01-01", "2024-12-31", PartitionSize.QUARTER)
    assert [p.key for p in quarters] == ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"]
    assert quarters[0].end == date(2024, 3, 31)

    years = parts("2023-05-01", "2025-02-01", PartitionSize.YEAR)
    assert [p.key for p in years] == ["2023", "2024", "2025"]
    assert years[1].days == 366


def test_days():
    result = parts("2024-02-28", "2024-03-01", PartitionSize.DAY)
    assert [p.key for p in result] == ["2024-02-28", "2024-02-29", "2024-03-01"]
    assert not any(p.truncated for p in result)


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError, match="precedes"):
        parts("2024-03-01", "2024-01-01", PartitionSize.MONTH)


def test_partitions_span_year_boundaries():
    result = parts("2023-11-15", "2024-02-05", PartitionSize.MONTH)
    assert [p.key for p in result] == ["2023-11", "2023-12", "2024-01", "2024-02"]
