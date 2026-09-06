from __future__ import annotations
from dataclasses import dataclass, field
from html import escape

from bs4 import BeautifulSoup, Comment

from wrc.documents import DocumentContract

DISCARD_TAGS = ("script", "style", "noscript", "iframe")


@dataclass
class Extraction:
    html: bytes
    title: str | None
    text_chars: int
    extracted_with: str
    quality_flags: list[str] = field(default_factory=list)


def _clean_text(node) -> str:
    return " ".join(node.get_text(" ").split())


def extract_document(body: bytes, contract: DocumentContract, language: str = "en") -> Extraction:
    """Keep the title and content regions; drop everything else.

    Scripts, styles and comments are removed from what is kept. If the content region
    cannot be found the whole body is kept instead and the record is flagged, so a
    redesign costs a flag rather than a lost document.
    """
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(DISCARD_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    flags: list[str] = []
    title_node = soup.select_one(contract.title) if contract.title else None
    title = _clean_text(title_node) if title_node else None

    content = soup.select_one(contract.content)
    extracted_with = "content"
    if content is None:
        content = soup.body or soup
        extracted_with = "body"
        flags.append("extraction_fallback")

    text = _clean_text(content)
    if not text:
        flags.append("extraction_failed")

    inner = content.decode_contents().strip()
    heading = f"<h1>{escape(title)}</h1>\n" if title else ""
    document = (
        "<!doctype html>\n"
        f'<html lang="{escape(language)}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{escape(title or '')}</title>\n</head>\n<body>\n"
        f'{heading}<div class="content">\n{inner}\n</div>\n</body>\n</html>\n'
    )
    return Extraction(
        html=document.encode("utf-8"),
        title=title,
        text_chars=len(text),
        extracted_with=extracted_with,
        quality_flags=flags,
    )
