"""Fingerprinting and naming are pure functions; every rule is pinned here."""

import pytest

from wrc.documents import content_hash, extension_for, object_key, raw_hash, slugify_identifier


def test_content_hash_ignores_html_comments_but_nothing_else():
    """The source injects a timing comment per response; that must not read as a change."""
    a = b"<html><!-- Elapsed time: 0.093 --><body>same</body></html>"
    b = b"<html><!-- cached or not --><!-- Elapsed time: 0 --><body>same</body></html>"
    c = b"<html><body>same </body></html>"
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(c)
    assert raw_hash(a) != raw_hash(b)


def test_content_hash_strips_multiline_comments():
    assert content_hash(b"x<!--\nmany\nlines\n-->y") == content_hash(b"xy")


@pytest.mark.parametrize(
    "identifier, slug",
    [
        (" ADJ-00054658", "ADJ-00054658"),
        ("LCR22912", "LCR22912"),
        ("DEC-E2010-094", "DEC-E2010-094"),
        ("IR - SC \u2013 00001494", "IR-SC-00001494"),
        ("IR-SC-00001494", "IR-SC-00001494"),
        ("dwt247", "DWT247"),
        ("  odd / name  (2)  ", "ODD-NAME-2"),
    ],
)
def test_slug_is_deterministic_safe_and_merges_spellings(identifier, slug):
    assert slugify_identifier(identifier) == slug


@pytest.mark.parametrize(
    "header, url, expected",
    [
        ("text/html; charset=utf-8", "/x.aspx", ("html", True)),
        (b"application/pdf", "/x", ("pdf", True)),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "/x", ("docx", True)),
        (None, "https://h/a/b.PDF", ("pdf", True)),
        ("application/octet-stream", "https://h/a/b.doc", ("doc", True)),
        ("application/octet-stream", "https://h/a/b", ("bin", False)),
        ("", "https://h/a/b.xyz", ("bin", False)),
    ],
)
def test_extension_prefers_header_then_url_then_bin(header, url, expected):
    assert extension_for(header, url) == expected


def test_object_key_layout_leads_with_source_and_ends_with_hash():
    assert object_key("wrc", "3", "2024-02", "LCR22912", "abc123", "html") == "wrc/3/2024-02/LCR22912/abc123.html"
