from wrc.documents import DocumentContract
from wrc.transform.extract import extract_document

CONTRACT = DocumentContract(content="div.content", title="h1.page-title")


def page(content: str, title: str = "DWT241") -> bytes:
    return (
        "<html><head><script>track()</script><style>x{}</style></head><body>"
        "<nav>menu</nav><!-- Elapsed time: 0.1 -->"
        f"<div class='col-sm-9'><h1 class='page-title'>{title}</h1>"
        f"<div class='content'>{content}</div><script>var x=1;</script></div>"
        "<footer>f</footer></body></html>"
    ).encode()


def test_keeps_title_and_content_only():
    out = extract_document(page("<p>The <b>decision</b>.</p>"), CONTRACT)
    html = out.html.decode()
    assert out.title == "DWT241" and out.extracted_with == "content" and out.quality_flags == []
    assert "<h1>DWT241</h1>" in html and "<p>The <b>decision</b>.</p>" in html
    for gone in ("menu", "track()", "var x=1", "Elapsed time", "<footer>", "<style>"):
        assert gone not in html
    assert html.startswith("<!doctype html>") and 'charset="utf-8"' in html and '<html lang="en">' in html
    assert out.text_chars == len("The decision ."), "text nodes are joined by single spaces"


def test_markup_inside_content_is_preserved():
    out = extract_document(page("<table><tr><td>Award</td><td>500</td></tr></table>"), CONTRACT)
    assert "<td>500</td>" in out.html.decode()


def test_missing_region_falls_back_to_body_and_flags():
    out = extract_document(b"<html><body><nav>m</nav><p>redesigned</p></body></html>", CONTRACT)
    assert out.extracted_with == "body" and out.quality_flags == ["extraction_fallback"]
    assert "redesigned" in out.html.decode() and out.title is None


def test_empty_content_is_flagged_not_dropped():
    out = extract_document(page("   "), CONTRACT)
    assert "extraction_failed" in out.quality_flags and out.text_chars == 0


def test_title_is_escaped_in_head_and_heading():
    out = extract_document(page("<p>x</p>", title="A &amp; B &lt;C&gt;"), CONTRACT)
    html = out.html.decode()
    assert out.title == "A & B <C>"
    assert "<title>A &amp; B &lt;C&gt;</title>" in html and "<h1>A &amp; B &lt;C&gt;</h1>" in html


def test_language_is_written_to_the_document():
    assert '<html lang="ga">' in extract_document(page("<p>x</p>"), CONTRACT, language="ga").html.decode()
