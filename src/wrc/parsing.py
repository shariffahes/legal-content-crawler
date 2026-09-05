from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from urllib.parse import unquote

from parsel import Selector

# The fields every listing must yield, whatever the site. Selector names are this
# parser's vocabulary; only their values are source-specific.
CORE_SELECTORS = frozenset({"record", "identifier", "published_date", "description"})

# Selectors with structural roles, consumed by the pagination strategy rather than
# surfaced as record fields.
STRUCTURAL_SELECTORS = frozenset({"result_count", "next_page"})


class ContractRequirements(Protocol):
    """What a pagination strategy needs the contract to declare."""

    required_selectors: frozenset[str]
    required_patterns: frozenset[str]


@dataclass(frozen=True, slots=True)
class ListingContract:
    """How to read one source's listing pages."""

    selectors: dict[str, str]
    published_date_format: str
    doc_path_sources: tuple[str, ...]
    path_attributes: dict[str, tuple[str, ...]]
    extra_fields: tuple[str, ...]
    total_pattern: re.Pattern[str] | None = None
    no_results_pattern: re.Pattern[str] | None = None

    @classmethod
    def from_config(cls, raw: dict, requirements: ContractRequirements) -> ListingContract:
        """Build a contract, rejecting an incomplete one at load.

        A missing or misspelled key fails here, before any request is made, rather than
        partway through a crawl. Every problem is reported together so a new source's
        config can be fixed in one pass.
        """
        selectors = dict(raw.get("selectors") or {})
        patterns = dict(raw.get("patterns") or {})
        sources, attributes = _path_sources(raw.get("doc_path_sources") or ())

        problems = _contract_problems(selectors, patterns, sources, requirements)
        if problems:
            raise ValueError("listing_contract: " + "; ".join(problems))

        return cls(
            selectors=selectors,
            published_date_format=raw["published_date_format"],
            doc_path_sources=sources,
            path_attributes=attributes,
            extra_fields=_extra_fields(selectors, sources),
            total_pattern=_compile(patterns.get("total")),
            no_results_pattern=_compile(patterns.get("no_results"), re.I),
        )


def _contract_problems(selectors, patterns, sources, requirements) -> list[str]:
    """Everything wrong with a listing_contract block, as human-readable sentences."""
    required = CORE_SELECTORS | requirements.required_selectors
    problems = []

    missing_selectors = sorted(required - selectors.keys())
    if missing_selectors:
        problems.append(f"missing selectors: {missing_selectors}")

    missing_patterns = sorted(requirements.required_patterns - patterns.keys())
    if missing_patterns:
        problems.append(f"missing patterns: {missing_patterns}")

    blank_selectors = sorted(name for name, css in selectors.items() if not (css or "").strip())
    if blank_selectors:
        problems.append(f"empty selectors: {blank_selectors}")

    if not sources:
        problems.append("needs at least one doc_path_source")

    undeclared_sources = [name for name in sources if name not in selectors]
    if undeclared_sources:
        problems.append(f"doc_path_sources reference undeclared selectors: {undeclared_sources}")

    return problems


def _path_sources(entries) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """Path sources in trust order, each with the attributes that may carry the path.

    An entry is a selector name, meaning ``href``, or a mapping naming other attributes
    for an element that keeps its path somewhere unusual. Attaching that to the one
    element concerned keeps a title attribute on some other element from ever being
    mistaken for a path.
    """
    names, attributes = [], {}
    for entry in entries:
        if isinstance(entry, str):
            names.append(entry)
            attributes[entry] = ("href",)
        else:
            name = entry["selector"]
            names.append(name)
            attributes[name] = tuple(entry.get("attributes") or ("href",))
    return tuple(names), attributes


def _extra_fields(selectors, sources) -> tuple[str, ...]:
    """Selectors that are neither core, structural, nor a path source: source vocabulary."""
    reserved = CORE_SELECTORS | STRUCTURAL_SELECTORS | set(sources)
    return tuple(name for name in selectors if name not in reserved)


def _compile(pattern: str | None, flags: int = 0) -> re.Pattern[str] | None:
    return re.compile(pattern, flags) if pattern else None


@dataclass(frozen=True, slots=True)
class ListingRow:
    identifier: str
    identifier_raw: str
    description: str
    published_date: date | None
    doc_path: str | None
    path_candidates: dict[str, str | None] = field(default_factory=dict)
    path_conflict: bool = False
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def missing(self) -> list[str]:
        """Fields the markup did not supply. Requirement 10 needs a reason per dropped record."""
        absent = [name for name in ("identifier", "doc_path") if not getattr(self, name)]
        if self.published_date is None:
            absent.append("published_date")
        return absent


def normalise(text: str | None) -> str:
    """Collapse runs of whitespace. Identifiers and descriptions both carry stray spaces."""
    return " ".join(text.split()) if text else ""


def parse_total(text: str | None, contract: ListingContract) -> int | None:
    """Declared result count, 0 for a legitimately empty range, None if unrecognised.

    The three states must stay distinct: a silent zero would let a run report success
    having scraped nothing, so the caller has to treat None as a failure.
    """
    if contract.total_pattern is None or contract.no_results_pattern is None:
        raise ValueError("contract declares no result-count patterns")
    if text is None:
        return None
    collapsed = normalise(text)
    match = contract.total_pattern.search(collapsed)
    if match:
        return int(match.group(1))
    return 0 if contract.no_results_pattern.search(collapsed) else None


def resolve_doc_path(
    candidates: dict[str, str | None], contract: ListingContract
) -> tuple[str | None, bool]:
    """Pick the document path from its redundant sources, most authoritative first.

    Sources are compared percent-decoded. Identifiers contain spaces and en dashes
    (``IR - SC - 00001494``), which anchors encode and title attributes do not; comparing
    raw would flag every such record as a conflict and drown the real ones. The value
    returned is still the encoded form, because that is what is fetchable.
    """
    present = [candidates[key] for key in contract.doc_path_sources if candidates.get(key)]
    if not present:
        return None, False
    return present[0], len({unquote(path) for path in present}) > 1


def parse_published_date(text: str | None, contract: ListingContract) -> date | None:
    value = normalise(text)
    if not value:
        return None
    try:
        return datetime.strptime(value, contract.published_date_format).date()
    except ValueError:
        return None


def _text(node: Selector, selector: str) -> str | None:
    return node.css(f"{selector} ::text").get()


def _path(node: Selector, selector: str, attributes: tuple[str, ...]) -> str | None:
    element = node.css(selector)
    for attribute in attributes:
        value = element.attrib.get(attribute)
        if value:
            return value
    return None


def extract_total(html: str, contract: ListingContract) -> int | None:
    if "result_count" not in contract.selectors:
        raise ValueError("contract declares no result_count selector")
    node = Selector(text=html).css(contract.selectors["result_count"])
    if not node:
        return None
    return parse_total(" ".join(node.css("::text").getall()), contract)


def extract_rows(html: str, contract: ListingContract) -> list[ListingRow]:
    """Parse every result row on a listing page.

    Rows are returned even when fields are missing: dropping them here would lose the
    reason, and requirement 10 needs every unscraped record accounted for.
    """
    selectors = contract.selectors
    rows = []
    for node in Selector(text=html).css(selectors["record"]):
        candidates = {
            name: _path(node, selectors[name], contract.path_attributes[name])
            for name in contract.doc_path_sources
        }
        doc_path, conflict = resolve_doc_path(candidates, contract)
        raw = node.css(f"{selectors['identifier']} ::text").get() or ""
        rows.append(
            ListingRow(
                identifier=normalise(raw),
                identifier_raw=raw,
                description=normalise(_text(node, selectors["description"])),
                published_date=parse_published_date(
                    _text(node, selectors["published_date"]), contract
                ),
                doc_path=doc_path,
                path_candidates=candidates,
                path_conflict=conflict,
                extras={
                    name: normalise(_text(node, selectors[name])) for name in contract.extra_fields
                },
            )
        )
    return rows


def next_page_url(html: str, contract: ListingContract) -> str | None:
    """The pager's forward link, if this source has a pager and offers one from here."""
    selector = contract.selectors.get("next_page")
    if selector is None:
        return None
    return Selector(text=html).css(selector).attrib.get("href")
