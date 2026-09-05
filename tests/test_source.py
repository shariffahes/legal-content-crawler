import calendar
from datetime import date

import pytest

import yaml
from pathlib import Path

from wrc.partitions import PartitionSize, iter_partitions
from wrc.pagination import DeclaredTotal, FollowNext, Pagination
from wrc.source import SourceSpec

SPEC = SourceSpec.load("config/sources/wrc.yml")


def month(year: int, mon: int):
    last = calendar.monthrange(year, mon)[1]
    return next(iter_partitions(date(year, mon, 1), date(year, mon, last), PartitionSize.MONTH))


def test_spec_loads_source_identity():
    assert (SPEC.name, SPEC.jurisdiction, SPEC.language) == ("wrc", "IE", "en")
    assert isinstance(SPEC.pagination, DeclaredTotal)


def test_facet_declares_which_metadata_field_it_populates():
    assert SPEC.facet.maps_to == "issuing_authority"
    assert SPEC.facet.param == "body"
    assert {v.id for v in SPEC.facet.values} == {"2", "1", "3", "15376"}
    assert SPEC.facet.value("15376").name == "Workplace Relations Commission"


def test_facet_values_are_skipped_before_they_existed():
    """The WRC's first records date from 2016; a 2010 partition cannot contain any."""
    wrc = SPEC.facet.value("15376")
    assert not wrc.covers(month(2010, 6))
    assert wrc.covers(month(2016, 6))


def test_no_trailing_bound_is_applied():
    """Absence of recent records is not evidence that none will appear."""
    equality = SPEC.facet.value("1")
    assert equality.covers(month(2026, 1))


def test_listing_params_use_day_first_dates_without_zero_padding():
    params = SPEC.listing_params(SPEC.facet.value("3"), month(2024, 2), page=3)
    assert params["from"] == "1/2/2024"
    assert params["to"] == "29/2/2024"
    assert params["body"] == "3"
    assert params["pageNumber"] == 3
    assert params["decisions"] == 1


def test_document_url_resolves_like_a_browser():
    listing = "https://www.workplacerelations.ie/en/search/?body=3&pageNumber=2"
    assert SPEC.absolute_url("/en/cases/a.html", listing) == "https://www.workplacerelations.ie/en/cases/a.html"
    assert SPEC.absolute_url("cases/a.html", listing) == "https://www.workplacerelations.ie/en/search/cases/a.html"
    assert SPEC.absolute_url("//cdn.example/a.pdf", listing) == "https://cdn.example/a.pdf"
    assert SPEC.absolute_url("https://elsewhere/x.html", listing) == "https://elsewhere/x.html"
    assert SPEC.absolute_url("/en/cases/a.html") == "https://www.workplacerelations.ie/en/cases/a.html"


def test_unknown_facet_value_raises():
    with pytest.raises(KeyError):
        SPEC.facet.value("999")


def test_facet_target_is_validated_at_load(tmp_path):
    """An unrecognised maps_to fails at load rather than silently dropping the field."""
    raw = yaml.safe_load(Path("config/sources/wrc.yml").read_text())
    raw["facet"]["maps_to"] = "not_a_schema_field"
    path = tmp_path / "bad.yml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="maps_to"):
        SourceSpec.load(path)


def test_doc_path_sources_must_reference_declared_selectors(tmp_path):
    raw = yaml.safe_load(Path("config/sources/wrc.yml").read_text())
    raw["listing_contract"]["doc_path_sources"] = ["view_page", "nonexistent"]
    path = tmp_path / "bad.yml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="undeclared selectors"):
        SourceSpec.load(path)


def test_listing_params_use_the_clamped_window_not_the_calendar_period():
    """The query covers only what was asked for; lineage still names the whole month.

    Building the request from the calendar period instead would make --start 2024-01-15
    silently fetch from 1 January: outside the requested range, while appearing healthy.
    """
    partition = next(iter_partitions(date(2024, 1, 15), date(2024, 1, 31), PartitionSize.MONTH))
    assert partition.truncated
    assert partition.key == "2024-01"
    assert partition.partition_date == date(2024, 1, 1)

    params = SPEC.listing_params(SPEC.facet.value("3"), partition, page=1)
    assert params["from"] == "15/1/2024"
    assert params["to"] == "31/1/2024"


def spec_with(tmp_path, mutate) -> Path:
    raw = yaml.safe_load(Path("config/sources/wrc.yml").read_text())
    mutate(raw)
    path = tmp_path / "variant.yml"
    path.write_text(yaml.safe_dump(raw))
    return path


def test_missing_selector_is_rejected_at_load(tmp_path):
    """A misspelled key must fail before any request, not partway through a crawl."""
    path = spec_with(tmp_path, lambda raw: raw["listing_contract"]["selectors"].pop("record"))
    with pytest.raises(ValueError, match="missing selectors: \\['record'\\]"):
        SourceSpec.load(path)


def test_missing_pattern_is_rejected_at_load(tmp_path):
    path = spec_with(tmp_path, lambda raw: raw["listing_contract"]["patterns"].pop("no_results"))
    with pytest.raises(ValueError, match="missing patterns"):
        SourceSpec.load(path)


def test_blank_selector_is_rejected_at_load(tmp_path):
    path = spec_with(
        tmp_path, lambda raw: raw["listing_contract"]["selectors"].update(record="  ")
    )
    with pytest.raises(ValueError, match="empty selectors"):
        SourceSpec.load(path)


def test_doc_path_sources_cannot_be_empty(tmp_path):
    path = spec_with(tmp_path, lambda raw: raw["listing_contract"].update(doc_path_sources=[]))
    with pytest.raises(ValueError, match="at least one doc_path_source"):
        SourceSpec.load(path)


def test_extra_selectors_become_extra_fields(tmp_path):
    """Anything beyond the core and structural selectors is source vocabulary, kept as extras."""
    path = spec_with(
        tmp_path, lambda raw: raw["listing_contract"]["selectors"].update(judge="span.judge")
    )
    contract = SourceSpec.load(path).contract
    assert set(contract.extra_fields) == {"ref_no", "judge"}
    assert "view_page" not in contract.extra_fields


def test_follow_next_source_needs_no_count_patterns(tmp_path):
    """A source publishing no match count must not be forced to invent a total pattern."""

    def to_follow_next(raw):
        raw["listing"]["pagination"] = "follow_next"
        raw["listing_contract"]["patterns"] = {}
        raw["listing_contract"]["selectors"].pop("result_count")

    spec = SourceSpec.load(spec_with(tmp_path, to_follow_next))
    assert isinstance(spec.pagination, FollowNext)
    assert spec.contract.total_pattern is None
    assert "next_page" in spec.contract.selectors


def test_follow_next_source_must_declare_a_pager(tmp_path):
    """The pager is that strategy's only way to advance, so its absence is fatal."""

    def without_pager(raw):
        raw["listing"]["pagination"] = "follow_next"
        raw["listing_contract"]["selectors"].pop("next_page")

    with pytest.raises(ValueError, match="missing selectors: \\['next_page'\\]"):
        SourceSpec.load(spec_with(tmp_path, without_pager))


def test_declared_total_source_must_publish_a_count(tmp_path):
    def without_count(raw):
        raw["listing_contract"]["selectors"].pop("result_count")

    with pytest.raises(ValueError, match="missing selectors: \\['result_count'\\]"):
        SourceSpec.load(spec_with(tmp_path, without_count))


def test_declared_total_pager_is_optional(tmp_path):
    """Without a pager the enumeration simply cannot be verified; it still works."""
    spec = SourceSpec.load(
        spec_with(tmp_path, lambda raw: raw["listing_contract"]["selectors"].pop("next_page"))
    )
    assert spec.contract.total_pattern is not None
    assert "next_page" not in spec.contract.selectors


def test_path_attributes_are_per_source_not_global():
    """Only p.fullpath may read its path from title; a title elsewhere is never a path."""
    attrs = SPEC.contract.path_attributes
    assert attrs["doc_path"] == ("title",)
    assert attrs["identifier"] == ("href",)
    assert attrs["view_page"] == ("href",)
