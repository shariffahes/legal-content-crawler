"""The spider's pagination behaviour, driven through fake responses.

These exist because a branch that has never executed is a claim, not a capability.
"""

from types import SimpleNamespace

import pytest
from scrapy.http import HtmlResponse, Request

from wrc.items import DocumentRecord
from wrc.spiders.listing import ListingSpider

WRC_URL = "https://www.workplacerelations.ie/en/search/"
FIXTURE_URL = "https://fixture.example/decisions"


@pytest.fixture
def spider_factory(monkeypatch):
    def make(source_path: str, max_recovery: int = 50) -> ListingSpider:
        monkeypatch.setattr(
            "wrc.spiders.listing.get_settings",
            lambda: SimpleNamespace(
                log_level="ERROR",
                source_spec_path=source_path,
                partition_size="month",
                max_recovery_pages=max_recovery,
            ),
        )
        return ListingSpider(start_date="2024-01-01", end_date="2024-01-31")

    return make


def wrc_html(total: str, ids: list[str], next_href: str | None = None) -> str:
    rows = "".join(
        f'<li class="each-item"><h2 class="title"><a href="/en/cases/{i}.html"> {i}</a></h2>'
        f'<span class="date">15/01/2024</span><p class="fullpath" title="/en/cases/{i}.html"></p>'
        f'<p class="description">d</p><span class="refNO"> {i}</span>'
        f'<div class="bottom-ref"><a class="btn btn-primary" href="/en/cases/{i}.html">View</a></div></li>'
        for i in ids
    )
    pager = f'<ul class="pager"><a class="next" href="{next_href}">»</a></ul>' if next_href else ""
    return f'<div class="searchhead">{total}</div><ul>{rows}</ul>{pager}'


def fixture_html(ids: list[str], next_href: str | None) -> str:
    rows = "".join(
        f'<article class="decision"><h3><a href="/d/{i}.html">{i}</a></h3>'
        f'<time>2024-01-15</time><p class="summary">s</p></article>'
        for i in ids
    )
    pager = f'<nav><a rel="next" href="{next_href}">next</a></nav>' if next_href else "<nav></nav>"
    return rows + pager


def respond(spider, url, html, page, recovery_depth=0, partition=None):
    facet_value = spider.facet_values[0]
    partition = partition or next(
        __import__("wrc.partitions", fromlist=["x"]).iter_partitions(
            spider.start_date, spider.end_date, spider.partition_size
        )
    )
    context = {
        "facet_value": facet_value,
        "partition": partition,
        "page": page,
        "recovery_depth": recovery_depth,
    }
    request = Request(url, cb_kwargs=context)
    response = HtmlResponse(url, body=html.encode(), encoding="utf-8", request=request)
    outputs = list(spider.parse_listing(response, **context))
    document_requests = [o for o in outputs if isinstance(o, Request) and o.callback == spider.parse_document]
    listing_requests = [o for o in outputs if isinstance(o, Request) and o.callback == spider.parse_listing]
    records = [r.cb_kwargs["record"] for r in document_requests]
    return records, listing_requests


def partition_stats(spider):
    return spider.run_stats.partitions[0]


# ---- declared_total ----------------------------------------------------------


def test_page_one_emits_records_and_enqueues_the_rest(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    records, requests = respond(
        spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 10 of 25 results", ["A", "B"]), page=1
    )
    assert [r.identifier for r in records] == ["A", "B"]
    assert [r.cb_kwargs["page"] for r in requests] == [2, 3]
    assert partition_stats(spider).pages_expected == 3


def test_clamped_pager_stops_on_no_progress_not_at_the_cap(spider_factory):
    """A site whose last page keeps offering next but returns the same content."""
    spider = spider_factory("config/sources/wrc.yml", max_recovery=50)
    last = wrc_html("Shows 21 to 25 of 25 results", ["X", "Y"], next_href="?pageNumber=4")
    spider.run_stats.partition(spider.facet_values[0].id, spider.facet_values[0].name, "2024-01").pages_expected = 3

    records, requests = respond(spider, f"{WRC_URL}?pageNumber=3", last, page=3)
    assert len(records) == 2
    assert len(requests) == 1 and requests[0].cb_kwargs["recovery_depth"] == 1
    assert [f["reason"] for f in partition_stats(spider).failures] == ["pagination_underrun"]

    clamped = wrc_html("Shows 21 to 25 of 25 results", ["X", "Y"], next_href="?pageNumber=5")
    records, requests = respond(spider, requests[0].url, clamped, page=4, recovery_depth=1)
    assert records == []
    assert requests == []
    stats = partition_stats(spider)
    assert stats.duplicates == 2
    assert [f["reason"] for f in stats.failures] == ["pagination_underrun", "pagination_no_progress"]


def test_a_link_back_to_a_fetched_url_is_a_loop(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    stats = spider.run_stats.partition(spider.facet_values[0].id, spider.facet_values[0].name, "2024-01")
    stats.pages_expected = 1
    url = f"{WRC_URL}?pageNumber=1"

    records, requests = respond(spider, url, wrc_html("Shows 1 to 2 of 2 results", ["A", "B"], next_href=url), page=1)
    assert len(records) == 2
    assert requests == []
    assert [f["reason"] for f in stats.failures] == ["pagination_underrun", "pagination_loop"]


# ---- follow_next ---------------------------------------------------------------


def test_follow_next_advances_and_stops_when_the_pager_ends(spider_factory):
    spider = spider_factory("tests/fixtures/follow_next_source.yml")
    records, requests = respond(spider, FIXTURE_URL, fixture_html(["HC-1", "HC-2"], "/decisions?p=2"), page=1)
    assert [r.identifier for r in records] == ["HC-1", "HC-2"]
    assert [r.url for r in requests] == ["https://fixture.example/decisions?p=2"]

    records, requests = respond(spider, requests[0].url, fixture_html(["HC-3"], None), page=2)
    assert [r.identifier for r in records] == ["HC-3"]
    assert requests == []
    stats = partition_stats(spider)
    assert stats.listing_complete and stats.found == 3 and not stats.found_is_declared


def test_follow_next_self_referential_pager_terminates(spider_factory):
    """Without a count there is no cap; this is the only thing standing between us and forever."""
    spider = spider_factory("tests/fixtures/follow_next_source.yml")
    records, requests = respond(spider, FIXTURE_URL, fixture_html(["HC-1"], "/decisions?p=2"), page=1)
    assert len(requests) == 1

    same_again = fixture_html(["HC-1"], "/decisions?p=3")
    records, requests = respond(spider, requests[0].url, same_again, page=2)
    assert records == [] and requests == []
    assert [f["reason"] for f in partition_stats(spider).failures] == ["pagination_no_progress"]


def test_follow_next_link_to_itself_terminates(spider_factory):
    spider = spider_factory("tests/fixtures/follow_next_source.yml")
    url = f"{FIXTURE_URL}?p=1"
    records, requests = respond(spider, url, fixture_html(["HC-1"], "/decisions?p=1"), page=1)
    assert len(records) == 1 and requests == []
    assert [f["reason"] for f in partition_stats(spider).failures] == ["pagination_loop"]


# ---- field policy --------------------------------------------------------------


def test_missing_identifier_is_fatal_but_a_bad_date_is_not(spider_factory):
    """Two tiers: no key or no path means no record; a bad date means a flagged record."""
    spider = spider_factory("config/sources/wrc.yml")
    html = (
        '<div class="searchhead">Shows 1 to 2 of 2 results</div><ul>'
        '<li class="each-item"><h2 class="title"><a href="/en/cases/a.html">A</a></h2>'
        '<span class="date">not a date</span><p class="description">d</p>'
        '<div class="bottom-ref"><a class="btn btn-primary" href="/en/cases/a.html">View</a></div></li>'
        '<li class="each-item"><h2 class="title"><a href="/en/cases/b.html"> </a></h2>'
        '<span class="date">01/01/2024</span><p class="description">d</p>'
        '<div class="bottom-ref"><a class="btn btn-primary" href="/en/cases/b.html">View</a></div></li>'
        "</ul>"
    )
    records, _ = respond(spider, f"{WRC_URL}?pageNumber=1", html, page=1)
    assert [r.identifier for r in records] == ["A"]
    assert records[0].published_date is None
    assert records[0].quality_flags == ["missing:published_date"]
    stats = partition_stats(spider)
    assert (stats.scraped, stats.degraded, stats.skipped) == (1, 1, 1)
    assert stats.listing_complete


# ---- documents -----------------------------------------------------------------


def fetched(spider, record, body: bytes, content_type: str, url: str | None = None):
    url = url or record.doc_url
    request = Request(url, cb_kwargs={"record": record})
    response = HtmlResponse(url, body=body, request=request, headers={"Content-Type": content_type})
    (out,) = spider.parse_document(response, record)
    return out


def test_listed_records_become_document_requests(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    records, _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", ["LCR22912"]), page=1)
    assert [r.doc_url for r in records] == ["https://www.workplacerelations.ie/en/cases/LCR22912.html"]
    assert records[0].file_hash is None
    assert not partition_stats(spider).complete


def test_parse_document_fingerprints_and_names_the_record(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    (record,), _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", [" IR - SC \u2013 00001494"]), page=1)
    body = b"<html><!-- Elapsed time: 0.1 --><body>decision</body></html>"
    out = fetched(spider, record, body, "text/html; charset=utf-8")

    assert out.identifier_slug == "IR-SC-00001494"
    assert out.extension == "html"
    assert out.file_size == len(body)
    assert out.file_hash != out.raw_sha256
    assert out.object_key == f"wrc/{out.issuing_authority_id}/2024-01/IR-SC-00001494/{out.file_hash}.html"
    assert out.content == body
    assert out.fetched_at is not None
    assert out.quality_flags == []
    stats = partition_stats(spider)
    assert (stats.scraped, stats.downloaded) == (1, 1) and stats.complete


def test_same_content_different_timing_comment_yields_same_hash_and_key(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    (record,), _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", ["A"]), page=1)
    first = fetched(spider, record, b"<!-- Elapsed time: 0.1 --><p>x</p>", "text/html")
    key_1, hash_1 = first.object_key, first.file_hash
    second = fetched(spider, record, b"<!-- Elapsed time: 0.2 --><p>x</p>", "text/html")
    assert (second.object_key, second.file_hash) == (key_1, hash_1)


def test_unknown_content_type_keeps_bytes_and_flags_the_record(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    (record,), _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", ["A"]), page=1)
    out = fetched(spider, record, b"\x00\x01", "application/octet-stream", url="https://h/blob")
    assert out.extension == "bin"
    assert out.quality_flags == ["unknown_content_type"]
    assert out.object_key.endswith(".bin")


def test_pdf_by_header_regardless_of_url(spider_factory):
    spider = spider_factory("config/sources/wrc.yml")
    (record,), _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", ["A"]), page=1)
    out = fetched(spider, record, b"%PDF-1.4", "application/pdf", url="https://h/looks-like.html")
    assert out.extension == "pdf"


def test_failed_download_is_counted_and_partition_is_incomplete(spider_factory):
    from scrapy.spidermiddlewares.httperror import HttpError
    from twisted.python.failure import Failure

    spider = spider_factory("config/sources/wrc.yml")
    (record,), _ = respond(spider, f"{WRC_URL}?pageNumber=1", wrc_html("Shows 1 to 1 of 1 results", ["A"]), page=1)
    request = Request(record.doc_url, cb_kwargs={"record": record})
    response = HtmlResponse(record.doc_url, status=503, body=b"", request=request)
    failure = Failure(HttpError(response))
    failure.request = request
    spider.handle_document_failure(failure)

    stats = partition_stats(spider)
    assert stats.download_failed == 1 and stats.downloaded == 0
    assert not stats.complete
    assert stats.failures[-1]["reason"] == "download_failed"
    assert stats.failures[-1]["status"] == 503
