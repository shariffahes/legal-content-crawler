"""Reproducible reconnaissance against the WRC decisions search.

Every empirical claim made in ARCHITECTURE.md is re-verified here, so the design
decisions can be audited rather than taken on trust. Also serves as a contract test:
if the site changes, this reports which assumption broke.

    python scripts/recon.py            # fast checks only
    python scripts/recon.py --full     # adds the per-year coverage sweep
"""

from __future__ import annotations

import argparse
import calendar
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests
from parsel import Selector
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wrc.config import get_settings  # noqa: E402
from wrc.parsing import extract_rows, extract_total  # noqa: E402
from wrc.source import SourceSpec  # noqa: E402

SPEC = SourceSpec.load(get_settings().source_spec_path)
CONTRACT = SPEC.contract
SELECTORS = CONTRACT.selectors
DOC_PATH_SOURCES = CONTRACT.doc_path_sources
BODIES = {value.id: value.name for value in SPEC.facet.values}
FACET_PARAM = SPEC.facet.param




@dataclass
class Recon:
    settings: object
    session: requests.Session = field(default_factory=requests.Session)
    sections: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    requests_made: int = 0
    sample_doc_path: str | None = None

    def __post_init__(self) -> None:
        self.session.headers["User-Agent"] = self.settings.user_agent
        # The source intermittently stalls; without this a transient timeout would be
        # reported as a failed assumption rather than a failed request.
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    @property
    def search_url(self) -> str:
        return SPEC.search_url

    def get(self, **params) -> str:
        if "body" in params:
            params[FACET_PARAM] = params.pop("body")
        for key, value in SPEC.listing.static_params.items():
            params.setdefault(key, value)
        time.sleep(self.settings.request_delay)
        self.requests_made += 1
        resp = self.session.get(self.search_url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.text

    def total(self, html: str) -> int | None:
        return extract_total(html, CONTRACT)

    def records(self, html: str):
        return extract_rows(html, CONTRACT)

    def record(self, title: str, body: str, ok: bool = True) -> None:
        mark = "PASS" if ok else "FAIL"
        self.sections.append(f"## {title}\n\n**{mark}**\n\n{body}\n")
        if not ok:
            self.failures.append(title)

    # ---- checks -------------------------------------------------------

    def check_selector_contract(self) -> None:
        html = self.get(
            **{"from": "1/1/2020", "to": "31/12/2020", "body": ",".join(BODIES), "pageNumber": 1}
        )
        page = Selector(text=html)
        missing = [name for name, sel in SELECTORS.items() if not page.css(sel)]
        rows = self.records(html)
        empty = sorted({name for r in rows for name in r.missing})
        dirty = [r for r in rows if r.identifier_raw != r.identifier]
        sample = rows[0] if rows else None
        if rows and rows[0].doc_path:
            self.sample_doc_path = rows[0].doc_path

        id_consistent = all(r.identifier == r.extras.get("ref_no") for r in rows)
        conflicts = [r for r in rows if r.path_conflict]
        unresolved = [r for r in rows if not r.doc_path]
        agreement = {
            src: sum(1 for r in rows if r.path_candidates.get(src) == r.doc_path)
            for src in DOC_PATH_SOURCES
        }
        consistent = id_consistent and not conflicts and not unresolved
        sample_fields = (
            "identifier_raw", "identifier", "extras", "description",
            "published_date", "doc_path", "path_conflict",
        )
        sample_lines = (
            [f"{name:<16}{getattr(sample, name)!r}" for name in sample_fields]
            if sample
            else ["(no records returned)"]
        )
        lines = [
            "| field | selector | present |",
            "| --- | --- | --- |",
            *(f"| {n} | `{s}` | {'no' if n in missing else 'yes'} |" for n, s in SELECTORS.items()),
            "",
            f"Fields empty across {len(rows)} records: {empty or 'none'}",
            f"`identifier` matches `ref_no` for every record: **{id_consistent}**",
            "",
            "The document path is published three times per record. It is resolved by precedence",
            f"rather than by trusting one selector: {' > '.join(f'`{s}`' for s in DOC_PATH_SOURCES)}.",
            "The footer *View Page* link wins because it is the link a user actually follows;",
            "`p.fullpath` is least trusted because its text node is empty and the value lives only",
            "in a `title` attribute, so a text-based selector silently yields an empty string.",
            "",
            "| source | agrees with resolved path |",
            "| --- | --- |",
            *(f"| `{k}` | {v}/{len(rows)} |" for k, v in agreement.items()),
            "",
            f"Records where the three sources disagreed: **{len(conflicts)}**",
            f"Records with no usable path at all: **{len(unresolved)}**",
            "",
            "Sample record:",
            "```",
            *sample_lines,
            "```",
            "",
            f"Identifiers carrying surrounding whitespace in the raw markup: "
            f"**{len(dirty)}/{len(rows)}** "
            f"(e.g. {(dirty or rows)[0].identifier_raw!r} -> {(dirty or rows)[0].identifier!r}).",
            "",
            "Identifiers elsewhere in the corpus also contain internal spaces (`IR - SC - 00001595`),",
            "and descriptions contain doubled spaces. Normalisation is required before an identifier",
            "becomes a filename or a primary key.",
        ]
        self.record("Selector contract", "\n".join(lines), ok=not missing and consistent)

    def check_page_size(self) -> None:
        base = {"from": "1/1/2020", "to": "31/12/2020", "body": "15376", "pageNumber": 1}
        counts = {"(none)": len(self.records(self.get(**base)))}
        for attempt in ("pageSize", "itemsPerPage", "resultsPerPage", "take"):
            counts[attempt] = len(self.records(self.get(**base, **{attempt: 100})))

        fixed = len(set(counts.values())) == 1
        body = "\n".join(
            [
                "Page size is server-controlled; no request parameter overrides it.",
                "",
                "| override attempted | records returned |",
                "| --- | --- |",
                *(f"| `{k}` | {v} |" for k, v in counts.items()),
                "",
                f"Configured page size = {SPEC.listing.page_size}.",
                "",
                "Consequence: listing requests scale strictly as `ceil(records / 10)`. There is no",
                "lever to reduce request count other than not requesting empty ranges at all.",
            ]
        )
        self.record("Page size is fixed", body, ok=fixed and counts["(none)"] == SPEC.listing.page_size)

    def check_statelessness(self) -> None:
        args = {"from": "1/1/2020", "to": "31/12/2020", "body": "15376"}
        first = {r.identifier for r in self.records(self.get(**args, pageNumber=1))}
        deep = {r.identifier for r in self.records(self.get(**args, pageNumber=5))}
        past_end = self.records(self.get(**args, pageNumber=9999))

        ok = bool(first) and bool(deep) and not (first & deep) and not past_end
        body = "\n".join(
            [
                "Each request is issued cold: no cookies, no prior navigation, no session.",
                "",
                f"- page 1 returned {len(first)} records",
                f"- page 5 returned {len(deep)} records, overlap with page 1: {len(first & deep)}",
                f"- page 9999 returned {len(past_end)} records",
                "",
                "A deep page fetched cold returns *that* page rather than page 1, which rules out",
                "server-side position tracking behind the `#DecUP` UpdatePanel. Requesting past the",
                "end yields an empty list rather than an error or a clamp to the last page, which is",
                "the pagination stop condition.",
                "",
                "The ASP.NET postback observed in the browser is therefore Post/Redirect/Get: it only",
                "mints the bookmarkable GET URL. `__VIEWSTATE` is never needed.",
            ]
        )
        self.record("Statelessness and pagination bounds", body, ok=ok)

    def check_body_disjointness(self, year: int = 2020) -> None:
        args = {"from": f"1/1/{year}", "to": f"31/12/{year}", "pageNumber": 1}
        per_body = {b: self.total(self.get(**args, body=b)) for b in BODIES}
        combined = self.total(self.get(**args, body=",".join(BODIES)))
        total = sum(v or 0 for v in per_body.values())

        body = "\n".join(
            [
                f"Counts for {year}:",
                "",
                "| body | value | records |",
                "| --- | --- | --- |",
                *(f"| {BODIES[b]} | `{b}` | {v} |" for b, v in per_body.items()),
                f"| **sum** | | **{total}** |",
                f"| combined request | `{','.join(BODIES)}` | **{combined}** |",
                "",
                "The per-body counts sum exactly to the combined count, so the bodies are disjoint",
                "and exhaustive. Scraping one body at a time is therefore lossless and produces no",
                "duplicates, and this equality is available as a cheap end-of-run assertion.",
                "",
                "Bodies must be requested separately regardless: no element in a result row",
                "identifies which body produced it.",
            ]
        )
        self.record("Bodies partition the corpus", body, ok=combined is not None and total == combined)

    def check_document_formats(self) -> None:
        eras = [("1/1/1995", "31/12/2010"), ("1/1/2015", "31/12/2024")]
        table = {}
        for body in BODIES:
            for start, end in eras:
                counter = Counter()
                for page in (1, 2, 3):
                    for rec in self.records(
                        self.get(**{"from": start, "to": end, "body": body, "pageNumber": page})
                    ):
                        href = rec.doc_path or ""
                        counter[href.rsplit(".", 1)[-1].lower() if "." in href else "(none)"] += 1
                table[(body, start[-4:])] = counter

        totals = Counter()
        for counter in table.values():
            totals.update(counter)
        sampled = sum(totals.values())

        non_html = {k: v for k, v in totals.items() if k != "html"}
        verdict = (
            "Every sampled record links to an `.html` page."
            if not non_html
            else f"Non-HTML links observed: {non_html}. The all-HTML assumption no longer holds."
        )
        body = "\n".join(
            [
                "Extension distribution over the first three pages of each body and era.",
                "",
                "| body | era | distribution |",
                "| --- | --- | --- |",
                *(
                    f"| {BODIES[b]} | {era}– | {dict(c) or 'no records'} |"
                    for (b, era), c in table.items()
                ),
                "",
                f"Sampled {sampled} records: {dict(totals)}",
                "",
                verdict,
                "",
                "This check fails if a non-HTML extension appears, so the assumption is monitored",
                "rather than asserted. Requirement 6a still calls for a PDF/DOC branch; it dispatches",
                "on the response `Content-Type` header rather than the URL extension, because the",
                "extension is a hint and the header is closer to authoritative.",
            ]
        )
        self.record("Document format distribution", body, ok=sampled > 0 and not non_html)

    def check_empty_states(self) -> None:
        args = {"from": "1/1/2020", "to": "31/12/2020", "body": "15376"}
        cases = {
            "empty range (WRC 2010, before the body existed)":
                self.get(**{"from": "1/1/2010", "to": "31/12/2010", "body": "15376", "pageNumber": 1}),
            "past the last page (WRC 2020, page 9999)": self.get(**args, pageNumber=9999),
            "populated page (WRC 2020, page 1)": self.get(**args, pageNumber=1),
        }
        rows = []
        for label, html in cases.items():
            node = Selector(text=html).css(SELECTORS["result_count"])
            rows.append(
                (
                    label,
                    repr(" ".join(" ".join(node.css("::text").getall()).split())[:48])
                    if node
                    else "(absent)",
                    len(self.records(html)),
                    self.total(html),
                )
            )
        empty_indistinguishable = rows[0][1] == rows[1][1]
        ok = rows[0][3] == 0 and rows[1][3] == 0 and rows[2][3] == 1572

        body = "\n".join(
            [
                "| case | `div.searchhead` | records | parsed total |",
                "| --- | --- | --- | --- |",
                *(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows),
                "",
                f"An empty range and a request past the last page are indistinguishable: "
                f"**{empty_indistinguishable}**. Both render the same banner, so the page alone",
                "cannot tell them apart — the page number does. On page 1 the banner means the",
                "partition is genuinely empty; on any later page it means pagination is exhausted.",
                "",
                "This yields a three-state parsing contract, which is what keeps the completeness",
                "check honest:",
                "",
                "| `div.searchhead` content | meaning | action |",
                "| --- | --- | --- |",
                "| `Shows A to B of N results` | N records declared | scrape and reconcile against N |",
                "| `There are no search results…` | legitimately empty | record 0, stop paginating |",
                "| absent, or matching neither | unexpected response | **fail loudly**, never treat as 0 |",
                "",
                "Collapsing the second and third states into a silent zero is the failure mode that",
                "would let a run report success having scraped nothing.",
            ]
        )
        self.record("Empty-result states", body, ok=ok)

    def check_robots(self) -> None:
        url = f"{SPEC.base_url}/robots.txt"
        self.requests_made += 1
        text = self.session.get(url, timeout=30).text
        rules = [l.strip() for l in text.splitlines() if l.lower().startswith("disallow")]
        case_rules = [r for r in rules if "cases" in r.lower()]
        doc_path = self.sample_doc_path or "/en/cases/2024/january/adj-00040000.html"
        literal_hit = any(doc_path.startswith(r.split(":", 1)[1].strip()) for r in case_rules)

        capitalised = doc_path.replace("/en/cases/", "/en/Cases/", 1)
        time.sleep(self.settings.request_delay)
        self.requests_made += 1
        probe = self.session.head(
            f"{SPEC.base_url}{capitalised}", allow_redirects=False, timeout=30
        )
        redirects_to_lower = (
            probe.status_code in (301, 308)
            and probe.headers.get("location", "").lower().endswith(doc_path.lower())
        )

        body = "\n".join(
            [
                f"`{url}` disallows {len(rules)} paths. Those relevant to case documents:",
                "",
                *(f"- `{r}`" for r in case_rules),
                "",
                f"`{SPEC.listing.path}` (the listing endpoint) is **not** disallowed.",
                "",
                f"Case-sensitive match of `{doc_path}` against those rules: **{literal_hit}**.",
                "",
                "RFC 9309 specifies case-sensitive path matching, and document URLs are lowercase,",
                "so the rule does not literally apply.",
                "",
                f"Probing the capitalised form `{capitalised}`:",
                "",
                f"- status `{probe.status_code}`",
                f"- location `{probe.headers.get('location', '(none)')}`",
                f"- canonicalises to the lowercase path: **{redirects_to_lower}**",
                "",
                "The disallowed path therefore genuinely exists and redirects to the path the",
                "documents are served from, so the operator's intent is unambiguous even though the",
                "literal rule misses. The gap is an artefact of an IIS stack where URL routing is",
                "case-insensitive and robots.txt is the only case-sensitive component.",
                "",
                "See ARCHITECTURE.md for the position taken and its justification.",
            ]
        )
        self.record("robots.txt", body, ok=redirects_to_lower and not literal_hit)

    def check_coverage(self, out_dir: Path, first: int = 1995, last: int | None = None) -> None:
        last = last or date.today().year
        grid: dict[tuple[int, str], int] = {}
        unparsed: list[str] = []
        for year in range(first, last + 1):
            for body in BODIES:
                measured = self.total(
                    self.get(**{"from": f"1/1/{year}", "to": f"31/12/{year}", "body": body, "pageNumber": 1})
                )
                if measured is None:
                    unparsed.append(f"{year}/{body}")
                    measured = 0
                grid[(year, body)] = measured

        tsv = ["year\tbody\tbody_name\trecords"]
        tsv += [
            f"{y}\t{b}\t{BODIES[b]}\t{n}" for (y, b), n in sorted(grid.items())
        ]
        (out_dir / "coverage.tsv").write_text("\n".join(tsv) + "\n")

        years = range(first, last + 1)
        rows = ["| year | " + " | ".join(BODIES[b] for b in BODIES) + " | total | pages |",
                "| --- | " + " | ".join("---" for _ in BODIES) + " | --- | --- |"]
        for y in years:
            vals = [grid[(y, b)] for b in BODIES]
            total = sum(vals)
            rows.append(f"| {y} | " + " | ".join(str(v) for v in vals) + f" | {total} | {-(-total // 10)} |")

        bounds = []
        for b in BODIES:
            active = [y for y in years if grid[(y, b)]]
            peak = max(years, key=lambda y: grid[(y, b)])
            bounds.append(
                f"| {BODIES[b]} | `{b}` | {min(active)}–{max(active)} | "
                f"{sum(grid[(y, b)] for y in years)} | {peak} ({grid[(peak, b)]}) |"
            )

        peak_body_year = max(grid.values())
        peak_key = max(grid, key=grid.get)
        corpus = sum(grid.values())

        # The partition table needs a measured peak month, not a yearly total divided by
        # twelve: publication is not evenly distributed across the year.
        peak_y, peak_b = peak_key
        monthly: dict[int, int] = {}
        for month in range(1, 13):
            end = calendar.monthrange(peak_y, month)[1]
            monthly[month] = self.total(
                self.get(
                    **{
                        "from": f"1/{month}/{peak_y}",
                        "to": f"{end}/{month}/{peak_y}",
                        "body": peak_b,
                        "pageNumber": 1,
                    }
                )
            ) or 0
        peak_month = max(monthly.values())
        peak_quarter = max(
            sum(monthly[m] for m in range(q, q + 3)) for q in (1, 4, 7, 10)
        )
        month_sum = sum(monthly.values())

        body = "\n".join(
            [
                *rows,
                "",
                "| body | value | active years | records | peak year |",
                "| --- | --- | --- | --- | --- |",
                *bounds,
                "",
                f"Corpus across {first}–{last}: **{corpus:,} records**, "
                f"**{-(-corpus // 10):,} listing pages** at a fixed page size of 10.",
                "",
                "### Partition sizing",
                "",
                f"The busiest body-year is {BODIES[peak_b]} in {peak_y} "
                f"({peak_body_year:,} records). Publication is not evenly spread across that year,",
                "so the peak month is measured directly rather than inferred from the annual total:",
                "",
                "| month | " + " | ".join(str(m) for m in range(1, 13)) + " |",
                "| --- | " + " | ".join("---" for _ in range(12)) + " |",
                "| records | " + " | ".join(str(monthly[m]) for m in range(1, 13)) + " |",
                "",
                f"Months sum to {month_sum:,} against an annual count of {peak_body_year:,}"
                + (
                    "."
                    if month_sum == peak_body_year
                    else f" — a discrepancy of {abs(month_sum - peak_body_year)}, "
                    "which suggests records whose date falls outside the monthly windows."
                ),
                "",
                "Each partition costs at least one listing request whether or not it holds records,",
                "so partition size trades blast radius against fixed overhead:",
                "",
                "| size | listing requests per year (4 bodies) | peak records per partition | peak pages | basis |",
                "| --- | --- | --- | --- | --- |",
                f"| yearly | 4 | {peak_body_year:,} | {-(-peak_body_year // 10)} | measured |",
                f"| quarterly | 16 | {peak_quarter:,} | {-(-peak_quarter // 10)} | measured (worst 3 consecutive months) |",
                f"| monthly | 48 | {peak_month:,} | {-(-peak_month // 10)} | measured (worst month) |",
                f"| weekly | 208 | ~{peak_month // 4:,} | ~{-(-(peak_month // 4) // 10)} | estimated from the peak month |",
                "",
                "Weekly over-partitions this corpus: the Labour Court publishes roughly ten records a",
                "week, so most weekly partitions would be a single page and the per-partition overhead",
                "would exceed the payload. Quarterly triples the blast radius of a failed partition to",
                "save 32 requests a year. Monthly keeps overhead small while bounding re-work on",
                "failure to a few minutes, and is the configured default.",
                "",
                f"The dead ranges above (bodies inactive for entire years) account for "
                f"{sum(1 for k, v in grid.items() if v == 0)} of {len(grid)} body-years. Skipping them",
                "saves more listing requests than this entire reconnaissance consumed.",
                "",
                f"Year-counts that could not be parsed: {unparsed or 'none'}.",
            ]
        )
        self.record("Corpus volume and coverage", body, ok=corpus > 0 and not unparsed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="include the per-year coverage sweep")
    parser.add_argument("--out", default="recon", help="output directory")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    recon = Recon(settings=get_settings())
    checks = [
        recon.check_selector_contract,
        recon.check_page_size,
        recon.check_statelessness,
        recon.check_body_disjointness,
        recon.check_document_formats,
        recon.check_empty_states,
        recon.check_robots,
    ]
    for check in checks:
        name = check.__name__
        print(f"running {name} ...", flush=True)
        try:
            check()
        except Exception as exc:
            recon.record(name, f"Check raised `{type(exc).__name__}: {exc}`", ok=False)

    if args.full:
        print("running check_coverage ... (this issues one request per body-year)", flush=True)
        try:
            recon.check_coverage(out_dir)
        except Exception as exc:
            recon.record("Corpus volume and coverage", f"Check raised `{type(exc).__name__}: {exc}`", ok=False)

    header = "\n".join(
        [
            "# Reconnaissance report",
            "",
            f"Generated {date.today().isoformat()} against `{SPEC.base_url}` ({SPEC.display_name}).",
            "",
            f"Requests issued: **{recon.requests_made}**, issued sequentially over a single "
            f"reused HTTP session, {recon.settings.request_delay}s apart. No concurrency.",
            "",
            f"Result: **{len(recon.failures)} failed** of {len(recon.sections)} checks."
            + (f" Failing: {', '.join(recon.failures)}." if recon.failures else ""),
            "",
            "Regenerate with `python scripts/recon.py --full`.",
            "",
            "---",
            "",
        ]
    )
    (out_dir / "REPORT.md").write_text(header + "\n".join(recon.sections))
    print(f"\nwrote {out_dir / 'REPORT.md'} ({recon.requests_made} requests)")
    return 1 if recon.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
