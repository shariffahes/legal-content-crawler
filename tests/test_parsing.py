from datetime import date

import pytest

from wrc.parsing import (
    extract_rows,
    extract_total,
    normalise,
    parse_published_date,
    parse_total,
    resolve_doc_path,
)
from wrc.source import SourceSpec

CONTRACT = SourceSpec.load("config/sources/wrc.yml").contract


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Shows 1 to 10 of 1572 results", 1572),
        ("            Shows 1 to\n            10\n\n            of  1921  results\n          ", 1921),
        ("There are no search results fitting your keywords", 0),
        ("Shows 1 to 10 of 1 results", 1),
    ],
)
def test_parse_total_recognises_both_known_shapes(text, expected):
    assert parse_total(text, CONTRACT) == expected


@pytest.mark.parametrize("text", [None, "", "Something else entirely", "of many results"])
def test_parse_total_returns_none_for_unrecognised_shapes(text):
    """None must be distinguishable from a measured zero, or a failed run looks empty."""
    assert parse_total(text, CONTRACT) is None


def test_measured_zero_is_not_confused_with_parse_failure():
    assert parse_total("There are no search results fitting your keywords", CONTRACT) == 0
    assert parse_total("<unexpected>", CONTRACT) is None


def test_resolve_doc_path_prefers_view_page():
    chosen, conflict = resolve_doc_path({"view_page": "/a.html", "identifier": "/b.html", "doc_path": "/c.html"}, CONTRACT
    )
    assert chosen == "/a.html"
    assert conflict is True


def test_resolve_doc_path_falls_back_through_the_chain():
    assert resolve_doc_path({"view_page": None, "identifier": "/b.html", "doc_path": None}, CONTRACT) == ("/b.html", False)
    assert resolve_doc_path({"view_page": None, "identifier": None, "doc_path": "/c.html"}, CONTRACT) == ("/c.html", False)
    assert resolve_doc_path({"view_page": None, "identifier": None, "doc_path": None}, CONTRACT) == (None, False)


def test_resolve_doc_path_reports_agreement():
    same = {"view_page": "/a.html", "identifier": "/a.html", "doc_path": "/a.html"}
    assert resolve_doc_path(same, CONTRACT) == ("/a.html", False)


def test_normalise_strips_leading_space_and_collapses_internal_runs():
    assert normalise(" ADJ-00022400") == "ADJ-00022400"
    assert normalise("Christian  Nolan V  Brennans Bakery") == "Christian Nolan V Brennans Bakery"
    assert normalise("IR - SC - 00001595") == "IR - SC - 00001595"
    assert normalise(None) == ""


def test_published_date_is_day_first():
    assert parse_published_date("31/12/2020", CONTRACT) == date(2020, 12, 31)
    assert parse_published_date(" 05/04/2024 ", CONTRACT) == date(2024, 4, 5)


def test_published_date_returns_none_rather_than_guessing():
    assert parse_published_date("not a date", CONTRACT) is None
    assert parse_published_date("", CONTRACT) is None


ROW = """
<li class="each-item clearfix">
  <div class="row">
    <div class="col-sm-9">
      <h2 class="title" title=" ADJ-00022400">
        <a href="{href}" title=" ADJ-00022400"> ADJ-00022400</a>
      </h2>
    </div>
    <div class="col-sm-3"><span class="date">{date}</span></div>
  </div>
  <p class="fullpath" title="{fullpath}"></p>
  <p class="description" title="x">A Receptionist  Medical Secretary / A Medical Practice</p>
  <div class="row bottom-ref">
    <div class="col-sm-9 ref"><span>Ref no: </span><span class="refNO"> ADJ-00022400</span></div>
    <div class="col-sm-3 link"><a class="btn btn-primary" href="{view}">View Page</a></div>
  </div>
</li>
"""


def page(rows: str, head: str = "Shows 1 to 10 of  1921  results") -> str:
    return f'<div class="searchhead">{head}</div><div class="item-list search-list"><ul>{rows}</ul></div>'


def row(href="/a.html", fullpath="/a.html", view="/a.html", date_text="31/12/2020") -> str:
    return ROW.format(href=href, fullpath=fullpath, view=view, date=date_text)


def test_extract_total_reads_the_declared_count():
    assert extract_total(page(row()), CONTRACT) == 1921
    assert extract_total(page("", head="There are no search results fitting your keywords"), CONTRACT) == 0


def test_reading_a_count_from_a_source_that_publishes_none_is_an_error():
    contract = SourceSpec.load("tests/fixtures/follow_next_source.yml").contract
    with pytest.raises(ValueError, match="no result_count selector"):
        extract_total(page(row()), contract)


def test_extract_total_returns_none_when_the_element_is_absent():
    assert extract_total("<html><body>nothing here</body></html>", CONTRACT) is None


def test_extract_rows_normalises_and_resolves():
    (parsed,) = extract_rows(page(row()), CONTRACT)
    assert parsed.identifier == "ADJ-00022400"
    assert parsed.identifier_raw == " ADJ-00022400"
    assert parsed.extras == {"ref_no": "ADJ-00022400"}
    assert parsed.description == "A Receptionist Medical Secretary / A Medical Practice"
    assert parsed.published_date == date(2020, 12, 31)
    assert parsed.doc_path == "/a.html"
    assert parsed.path_conflict is False
    assert parsed.missing == []


def test_conflicting_paths_prefer_view_page_and_are_flagged():
    (parsed,) = extract_rows(page(row(href="/b.html", fullpath="/c.html", view="/a.html")), CONTRACT)
    assert parsed.doc_path == "/a.html"
    assert parsed.path_conflict is True


def test_rows_with_missing_fields_are_returned_with_a_reason():
    """Dropping them here would lose the reason requirement 10 asks us to log."""
    broken = '<li class="each-item clearfix"><h2 class="title"><a> </a></h2></li>'
    (parsed,) = extract_rows(page(broken), CONTRACT)
    assert parsed.identifier == ""
    assert parsed.doc_path is None
    assert parsed.fatal == ["identifier", "doc_path"]
    assert parsed.degraded == ["published_date"]
    assert set(parsed.missing) == {"identifier", "doc_path", "published_date"}


def test_unparseable_date_does_not_stop_the_row():
    (parsed,) = extract_rows(page(row(date_text="not a date")), CONTRACT)
    assert parsed.identifier == "ADJ-00022400"
    assert parsed.published_date is None
    assert parsed.fatal == []
    assert parsed.degraded == ["published_date"]


def test_empty_listing_yields_no_rows():
    assert extract_rows(page("", head="There are no search results fitting your keywords"), CONTRACT) == []


def test_percent_encoding_differences_are_not_conflicts():
    """Identifiers contain spaces and en dashes; anchors encode them, fullpath does not."""
    encoded = "/en/cases/2024/march/ir - sc %E2%80%93 00001494.html"
    raw = "/en/cases/2024/march/ir - sc – 00001494.html"
    chosen, conflict = resolve_doc_path({"view_page": encoded, "identifier": encoded, "doc_path": raw}, CONTRACT)
    assert chosen == encoded
    assert conflict is False


def test_genuinely_different_paths_still_conflict():
    chosen, conflict = resolve_doc_path(
        {
            "view_page": "/en/cases/a.html",
            "identifier": "/en/cases/b.html",
            "doc_path": "/en/cases/a.html",
        },
        CONTRACT,
    )
    assert chosen == "/en/cases/a.html"
    assert conflict is True


def test_a_title_on_the_identifier_anchor_is_never_taken_as_a_path():
    """If the anchor lost its href, its title (the identifier) must not become the doc path."""
    html = page(row(href="", fullpath="/real.html", view=""))
    (parsed,) = extract_rows(html, CONTRACT)
    assert parsed.path_candidates["identifier"] is None
    assert parsed.doc_path == "/real.html"
