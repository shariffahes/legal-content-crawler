"""Fingerprinting and naming are pure functions; every rule is pinned here."""

import pytest

from wrc.documents import DocumentContract, content_fingerprint, extension_for, object_key, raw_hash, region_text, slugify_identifier


SEL = DocumentContract(content="div.content", title="h1.page-title")


def page(content: str, chrome: str = "<nav>menu</nav>", comment: str = "", title: str = "DWT241") -> bytes:
    return (f"<html>{comment}<body>{chrome}<h1 class='page-title'>{title}</h1>"
            f"<div class='content'>{content}</div><footer>f</footer></body></html>").encode()


def test_fingerprint_covers_only_the_content_region():
    """Chrome, scripts and timing comments may all change without the document changing."""
    base = page("<p>The decision.</p>")
    other_chrome = page("<p>The decision.</p>", chrome="<nav>new cookie banner</nav><script>x()</script>")
    other_comment = page("<p>The decision.</p>", comment="<!-- Elapsed time: 0.9 -->")
    changed = page("<p>The decision, amended.</p>")
    h = lambda b: content_fingerprint(b, "html", SEL)
    assert h(base) == h(other_chrome) == h(other_comment) == (h(base)[0], "content_text")
    assert h(changed)[0] != h(base)[0]
    assert raw_hash(base) != raw_hash(other_chrome)


def test_fingerprint_is_text_based_so_formatting_only_edits_do_not_version():
    plain = page("<p>Award of 500 euro</p>")
    bold = page("<p>Award of <b>500</b> euro</p>")
    respaced = page("<p>Award   of\n 500 euro</p>")
    assert content_fingerprint(plain, "html", SEL) == content_fingerprint(bold, "html", SEL) == content_fingerprint(respaced, "html", SEL)


def test_fingerprint_falls_back_when_the_region_is_missing_and_says_so():
    missing = b"<html><!-- Elapsed time: 1 --><body><p>no region here</p></body></html>"
    digest, basis = content_fingerprint(missing, "html", SEL)
    assert basis == "document_minus_comments"
    assert digest == content_fingerprint(b"<html><body><p>no region here</p></body></html>", "html", SEL)[0]


def test_non_html_is_fingerprinted_as_received():
    pdf = b"%PDF-1.4 ..."
    assert content_fingerprint(pdf, "pdf", SEL) == (raw_hash(pdf), "raw_bytes")


def test_a_changed_title_is_a_changed_document():
    assert content_fingerprint(page("<p>x</p>", title="Smith v Jones"), "html", SEL) != content_fingerprint(page("<p>x</p>", title="Smyth v Jones"), "html", SEL)


def test_title_is_optional_in_the_contract():
    no_title = DocumentContract(content="div.content")
    assert content_fingerprint(page("<p>x</p>", title="A"), "html", no_title) == content_fingerprint(page("<p>x</p>", title="B"), "html", no_title)
    with pytest.raises(ValueError, match="content is required"):
        DocumentContract.from_config({"title": "h1"})


def test_region_text_ignores_script_and_style_like_the_transform_does():
    """A script inside the content region must not make the document look changed."""
    with_script = page("<p>x</p><script>track('v1')</script><style>a{}</style>")
    other_script = page("<p>x</p><script>track('v2')</script>")
    assert region_text(with_script, SEL.content) == "x"
    assert content_fingerprint(with_script, "html", SEL) == content_fingerprint(other_script, "html", SEL)


def test_region_text_normalises_whitespace_and_returns_none_when_absent():
    assert region_text(page("<p>a</p>  <p>b   c</p>"), SEL.content) == "a b c"
    assert region_text(b"<html><body>x</body></html>", SEL.content) is None


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
