"""Both strategies exercised against page shapes they have to handle."""

from wrc.pagination import DeclaredTotal, FollowNext, strategy_for
from wrc.parsing import extract_rows
from wrc.source import SourceSpec

WRC = SourceSpec.load("config/sources/wrc.yml")
FIXTURE = SourceSpec.load("tests/fixtures/follow_next_source.yml")


def wrc_page(total_text: str, rows: int = 0, pager: bool = False) -> str:
    row = (
        '<li class="each-item"><h2 class="title"><a href="/en/cases/x{i}.html"> ID-{i}</a></h2>'
        '<span class="date">01/02/2024</span><p class="fullpath" title="/en/cases/x{i}.html"></p>'
        '<p class="description">d</p><span class="refNO"> ID-{i}</span>'
        '<div class="bottom-ref"><a class="btn btn-primary" href="/en/cases/x{i}.html">View</a></div></li>'
    )
    body = "".join(row.format(i=i) for i in range(rows))
    nav = '<ul class="pager"><a class="next" href="?pageNumber=99">»</a></ul>' if pager else ""
    return f'<div class="searchhead">{total_text}</div><ul>{body}</ul>{nav}'


def fixture_page(rows: int, next_href: str | None) -> str:
    row = (
        '<article class="decision"><h3><a href="/d/{i}.html">HC-{i}</a></h3>'
        "<time>2024-02-01</time><p class=\"summary\">s</p></article>"
    )
    nav = f'<nav><a rel="next" href="{next_href}">next</a></nav>' if next_href else "<nav></nav>"
    return "".join(row.format(i=i) for i in range(rows)) + nav


# ---- strategy selection ------------------------------------------------------


def test_sources_resolve_their_strategy_by_name():
    assert isinstance(WRC.pagination, DeclaredTotal)
    assert isinstance(FIXTURE.pagination, FollowNext)
    assert type(strategy_for("follow_next")) is FollowNext


# ---- declared_total ----------------------------------------------------------


def test_declared_total_plans_every_page_from_page_one():
    info = DeclaredTotal().inspect(wrc_page("Shows 1 to 10 of 45 results"), WRC.contract, page=1, page_size=10)
    assert (info.declared_total, info.pages_expected) == (45, 5)
    assert list(info.prefetch) == [2, 3, 4, 5]
    assert info.error is None


def test_declared_total_measured_zero_plans_nothing():
    info = DeclaredTotal().inspect(wrc_page("There are no search results fitting your keywords"), WRC.contract, 1, 10)
    assert (info.declared_total, info.pages_expected, list(info.prefetch)) == (0, 0, [])


def test_declared_total_unrecognised_page_is_an_error_not_a_zero():
    info = DeclaredTotal().inspect(wrc_page("Something unexpected"), WRC.contract, 1, 10)
    assert info.error == "unparseable_result_count"
    assert info.declared_total is None


def test_declared_total_later_pages_do_not_replan():
    info = DeclaredTotal().inspect(wrc_page("Shows 11 to 20 of 45 results"), WRC.contract, page=2, page_size=10)
    assert info.declared_total == 45
    assert info.pages_expected is None
    assert list(info.prefetch) == []


def test_declared_total_does_not_follow_the_pager_mid_enumeration():
    html = wrc_page("Shows 11 to 20 of 45 results", pager=True)
    assert DeclaredTotal().continue_from(html, WRC.contract, page=2, pages_expected=5, recovery_depth=0, max_recovery=50).stops


def test_declared_total_flags_and_recovers_an_underrun():
    html = wrc_page("Shows 41 to 45 of 45 results", pager=True)
    step = DeclaredTotal().continue_from(html, WRC.contract, page=5, pages_expected=5, recovery_depth=0, max_recovery=50)
    assert step.anomalies == ("pagination_underrun",)
    assert step.url == "?pageNumber=99"
    assert step.recovery_depth == 1


def test_declared_total_recovery_continues_silently_then_stops_at_the_pager_end():
    with_pager = wrc_page("Shows 51 to 55 of 45 results", pager=True)
    step = DeclaredTotal().continue_from(with_pager, WRC.contract, page=6, pages_expected=5, recovery_depth=1, max_recovery=50)
    assert step.anomalies == () and step.recovery_depth == 2

    without_pager = wrc_page("Shows 51 to 55 of 45 results", pager=False)
    assert DeclaredTotal().continue_from(without_pager, WRC.contract, page=7, pages_expected=5, recovery_depth=2, max_recovery=50).stops


def test_declared_total_recovery_is_bounded():
    html = wrc_page("Shows 1 to 10 of 45 results", pager=True)
    step = DeclaredTotal().continue_from(html, WRC.contract, page=55, pages_expected=5, recovery_depth=50, max_recovery=50)
    assert step.anomalies == ("pagination_recovery_capped",)
    assert step.url is None


# ---- follow_next ------------------------------------------------------------


def test_follow_next_publishes_no_count_and_plans_nothing():
    info = FollowNext().inspect(fixture_page(3, "/decisions?p=2"), FIXTURE.contract, page=1, page_size=3)
    assert info.declared_total is None and info.pages_expected is None
    assert list(info.prefetch) == [] and info.error is None


def test_follow_next_advances_while_the_pager_offers_a_link():
    step = FollowNext().continue_from(fixture_page(3, "/decisions?p=2"), FIXTURE.contract, 1, None, 0, 50)
    assert step.url == "/decisions?p=2" and step.anomalies == ()
    assert FollowNext().continue_from(fixture_page(2, None), FIXTURE.contract, 2, None, 0, 50).stops


def test_follow_next_source_rows_parse_without_wrc_vocabulary():
    """The fixture has no ref_no, no fullpath, no view_page: none of that is generic."""
    rows = extract_rows(fixture_page(2, None), FIXTURE.contract)
    assert [r.identifier for r in rows] == ["HC-0", "HC-1"]
    assert rows[0].doc_path == "/d/0.html"
    assert rows[0].extras == {}
    assert rows[0].missing == []


def test_wrc_extras_carry_the_reference_number():
    rows = extract_rows(wrc_page("Shows 1 to 10 of 1 results", rows=1), WRC.contract)
    assert rows[0].extras == {"ref_no": "ID-0"}


# ---- invariants enforced by the base class, whatever the strategy -------------


def advance(strategy, html, contract, **kw):
    defaults = dict(page=2, pages_expected=None, recovery_depth=0, max_recovery=50,
                    base_url="https://fixture.example/decisions?p=2", urls_fetched=set(), new_records=1)
    return strategy.advance(html, contract, **{**defaults, **kw})


def test_advance_resolves_the_next_url_against_the_page():
    step = advance(FollowNext(), fixture_page(1, "/decisions?p=3"), FIXTURE.contract)
    assert step.url == "https://fixture.example/decisions?p=3" and step.anomalies == ()


def test_advance_refuses_a_url_already_fetched():
    step = advance(
        FollowNext(), fixture_page(1, "/decisions?p=2"), FIXTURE.contract,
        urls_fetched={"https://fixture.example/decisions?p=2"},
    )
    assert step.url is None and step.anomalies == ("pagination_loop",)


def test_advance_refuses_to_continue_from_a_page_that_added_nothing():
    step = advance(FollowNext(), fixture_page(1, "/decisions?p=3"), FIXTURE.contract, new_records=0)
    assert step.url is None and step.anomalies == ("pagination_no_progress",)


def test_advance_allows_page_one_to_continue_even_with_nothing_new():
    """Page 1 has nothing to be a duplicate of; the guard must not fire there."""
    step = advance(
        FollowNext(), fixture_page(0, "/decisions?p=2"), FIXTURE.contract,
        page=1, new_records=0, base_url="https://fixture.example/decisions?p=1",
    )
    assert step.url == "https://fixture.example/decisions?p=2"


def test_advance_keeps_strategy_anomalies_alongside_its_own():
    html = wrc_page("Shows 41 to 45 of 45 results", pager=True)
    step = advance(
        DeclaredTotal(), html, WRC.contract, page=5, pages_expected=5,
        base_url="https://www.workplacerelations.ie/en/search/?pageNumber=5",
        urls_fetched={"https://www.workplacerelations.ie/en/search/?pageNumber=99"},
    )
    assert step.anomalies == ("pagination_underrun", "pagination_loop")
    assert step.url is None


def test_advance_records_the_page_it_was_given():
    """The set of fetched URLs has one owner, so it is always absolute and never forgotten."""
    fetched: set[str] = set()
    advance(FollowNext(), fixture_page(1, "/decisions?p=3"), FIXTURE.contract, urls_fetched=fetched)
    assert fetched == {"https://fixture.example/decisions?p=2"}


def test_a_clean_end_is_one_shape_not_two():
    step = advance(FollowNext(), fixture_page(1, None), FIXTURE.contract)
    assert step.stops and step.anomalies == ()
