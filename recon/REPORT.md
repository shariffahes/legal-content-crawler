# Reconnaissance report

Generated 2026-09-05 against `https://www.workplacerelations.ie`.

Requests issued: **183**, issued sequentially over a single reused HTTP session, 0.6s apart. No concurrency.

Result: **0 failed** of 8 checks.

Regenerate with `python scripts/recon.py --full`.

---
## Selector contract

**PASS**

| field | selector | present |
| --- | --- | --- |
| total_count | `div.searchhead` | yes |
| record | `li.each-item` | yes |
| identifier | `h2.title a` | yes |
| published_date | `span.date` | yes |
| doc_path | `p.fullpath` | yes |
| description | `p.description` | yes |
| ref_no | `span.refNO` | yes |
| view_page | `div.bottom-ref a.btn-primary` | yes |

Fields empty across 10 records: none
`identifier` matches `ref_no` for every record: **True**

The document path is published three times per record. It is resolved by precedence
rather than by trusting one selector: `view_page` > `href` > `doc_path`.
The footer *View Page* link wins because it is the link a user actually follows;
`p.fullpath` is least trusted because its text node is empty and the value lives only
in a `title` attribute, so a text-based selector silently yields an empty string.

| source | agrees with resolved path |
| --- | --- |
| `view_page` | 10/10 |
| `href` | 10/10 |
| `doc_path` | 10/10 |

Records where the three sources disagreed: **0**
Records with no usable path at all: **0**

Sample record:
```
identifier_raw  ' ADJ-00022400'
identifier      'ADJ-00022400'
href            '/en/cases/2021/january/adj-00022400.html'
doc_path        '/en/cases/2021/january/adj-00022400.html'
view_page       '/en/cases/2021/january/adj-00022400.html'
ref_no          'ADJ-00022400'
description     'A Receptionist Medical Secretary / A Medical Practice'
published_date  '31/12/2020'
resolved_path   '/en/cases/2021/january/adj-00022400.html'
path_conflict   False
```

Identifiers carrying surrounding whitespace in the raw markup: **1/10** (e.g. ' ADJ-00022400' -> 'ADJ-00022400').

Identifiers elsewhere in the corpus also contain internal spaces (`IR - SC - 00001595`),
and descriptions contain doubled spaces. Normalisation is required before an identifier
becomes a filename or a primary key.

## Page size is fixed

**PASS**

Page size is server-controlled; no request parameter overrides it.

| override attempted | records returned |
| --- | --- |
| `(none)` | 10 |
| `pageSize` | 10 |
| `itemsPerPage` | 10 |
| `resultsPerPage` | 10 |
| `take` | 10 |

Configured `SOURCE_PAGE_SIZE` = 10.

Consequence: listing requests scale strictly as `ceil(records / 10)`. There is no
lever to reduce request count other than not requesting empty ranges at all.

## Statelessness and pagination bounds

**PASS**

Each request is issued cold: no cookies, no prior navigation, no session.

- page 1 returned 10 records
- page 5 returned 10 records, overlap with page 1: 0
- page 9999 returned 0 records

A deep page fetched cold returns *that* page rather than page 1, which rules out
server-side position tracking behind the `#DecUP` UpdatePanel. Requesting past the
end yields an empty list rather than an error or a clamp to the last page, which is
the pagination stop condition.

The ASP.NET postback observed in the browser is therefore Post/Redirect/Get: it only
mints the bookmarkable GET URL. `__VIEWSTATE` is never needed.

## Bodies partition the corpus

**PASS**

Counts for 2020:

| body | value | records |
| --- | --- | --- |
| Employment Appeals Tribunal | `2` | 3 |
| Equality Tribunal | `1` | 8 |
| Labour Court | `3` | 338 |
| Workplace Relations Commission | `15376` | 1572 |
| **sum** | | **1921** |
| combined request | `2,1,3,15376` | **1921** |

The per-body counts sum exactly to the combined count, so the bodies are disjoint
and exhaustive. Scraping one body at a time is therefore lossless and produces no
duplicates, and this equality is available as a cheap end-of-run assertion.

Bodies must be requested separately regardless: no element in a result row
identifies which body produced it.

## Document format distribution

**PASS**

Extension distribution over the first three pages of each body and era.

| body | era | distribution |
| --- | --- | --- |
| Employment Appeals Tribunal | 1995– | {'html': 30} |
| Employment Appeals Tribunal | 2015– | {'html': 30} |
| Equality Tribunal | 1995– | {'html': 30} |
| Equality Tribunal | 2015– | {'html': 30} |
| Labour Court | 1995– | {'html': 30} |
| Labour Court | 2015– | {'html': 30} |
| Workplace Relations Commission | 1995– | no records |
| Workplace Relations Commission | 2015– | {'html': 30} |

Sampled 210 records: {'html': 210}

Every sampled record links to an `.html` page.

This check fails if a non-HTML extension appears, so the assumption is monitored
rather than asserted. Requirement 6a still calls for a PDF/DOC branch; it dispatches
on the response `Content-Type` header rather than the URL extension, because the
extension is a hint and the header is closer to authoritative.

## Empty-result states

**PASS**

| case | `div.searchhead` | records | parsed total |
| --- | --- | --- | --- |
| empty range (WRC 2010, before the body existed) | 'There are no search results fitting your keyword' | 0 | 0 |
| past the last page (WRC 2020, page 9999) | 'There are no search results fitting your keyword' | 0 | 0 |
| populated page (WRC 2020, page 1) | 'Shows 1 to 10 of 1572 results' | 10 | 1572 |

An empty range and a request past the last page are indistinguishable: **True**. Both render the same banner, so the page alone
cannot tell them apart — the page number does. On page 1 the banner means the
partition is genuinely empty; on any later page it means pagination is exhausted.

This yields a three-state parsing contract, which is what keeps the completeness
check honest:

| `div.searchhead` content | meaning | action |
| --- | --- | --- |
| `Shows A to B of N results` | N records declared | scrape and reconcile against N |
| `There are no search results…` | legitimately empty | record 0, stop paginating |
| absent, or matching neither | unexpected response | **fail loudly**, never treat as 0 |

Collapsing the second and third states into a silent zero is the failure mode that
would let a run report success having scraped nothing.

## robots.txt

**PASS**

`https://www.workplacerelations.ie/robots.txt` disallows 25 paths. Those relevant to case documents:

- `Disallow: /Cases/`
- `Disallow: /en/Cases/`
- `Disallow: /ga/Cases/`

`/en/search/` (the listing endpoint) is **not** disallowed.

Case-sensitive match of `/en/cases/2021/january/adj-00022400.html` against those rules: **False**.

RFC 9309 specifies case-sensitive path matching, and document URLs are lowercase,
so the rule does not literally apply.

Probing the capitalised form `/en/Cases/2021/january/adj-00022400.html`:

- status `301`
- location `/en/cases/2021/january/adj-00022400.html`
- canonicalises to the lowercase path: **True**

The disallowed path therefore genuinely exists and redirects to the path the
documents are served from, so the operator's intent is unambiguous even though the
literal rule misses. The gap is an artefact of an IIS stack where URL routing is
case-insensitive and robots.txt is the only case-sensitive component.

See ARCHITECTURE.md for the position taken and its justification.

## Corpus volume and coverage

**PASS**

| year | Employment Appeals Tribunal | Equality Tribunal | Labour Court | Workplace Relations Commission | total | pages |
| --- | --- | --- | --- | --- | --- | --- |
| 1995 | 0 | 0 | 540 | 0 | 540 | 54 |
| 1996 | 0 | 53 | 458 | 0 | 511 | 52 |
| 1997 | 0 | 42 | 428 | 0 | 470 | 47 |
| 1998 | 0 | 41 | 460 | 0 | 501 | 51 |
| 1999 | 0 | 102 | 472 | 0 | 574 | 58 |
| 2000 | 0 | 42 | 433 | 0 | 475 | 48 |
| 2001 | 0 | 66 | 472 | 0 | 538 | 54 |
| 2002 | 0 | 119 | 535 | 0 | 654 | 66 |
| 2003 | 0 | 147 | 569 | 0 | 716 | 72 |
| 2004 | 0 | 187 | 545 | 0 | 732 | 74 |
| 2005 | 0 | 140 | 593 | 0 | 733 | 74 |
| 2006 | 0 | 157 | 567 | 0 | 724 | 73 |
| 2007 | 626 | 178 | 498 | 0 | 1302 | 131 |
| 2008 | 1057 | 197 | 594 | 0 | 1848 | 185 |
| 2009 | 1569 | 216 | 564 | 0 | 2349 | 235 |
| 2010 | 2076 | 322 | 686 | 0 | 3084 | 309 |
| 2011 | 2547 | 341 | 654 | 0 | 3542 | 355 |
| 2012 | 2833 | 246 | 613 | 0 | 3692 | 370 |
| 2013 | 2224 | 225 | 612 | 0 | 3061 | 307 |
| 2014 | 1391 | 132 | 554 | 0 | 2077 | 208 |
| 2015 | 1056 | 174 | 517 | 0 | 1747 | 175 |
| 2016 | 869 | 34 | 580 | 1350 | 2833 | 284 |
| 2017 | 256 | 1 | 518 | 2147 | 2922 | 293 |
| 2018 | 11 | 0 | 492 | 2778 | 3281 | 329 |
| 2019 | 4 | 0 | 592 | 2854 | 3450 | 345 |
| 2020 | 3 | 8 | 338 | 1572 | 1921 | 193 |
| 2021 | 0 | 0 | 427 | 1544 | 1971 | 198 |
| 2022 | 1 | 0 | 472 | 1993 | 2466 | 247 |
| 2023 | 2 | 0 | 427 | 2913 | 3342 | 335 |
| 2024 | 1 | 0 | 508 | 2736 | 3245 | 325 |
| 2025 | 0 | 0 | 361 | 2581 | 2942 | 295 |
| 2026 | 1 | 0 | 277 | 1508 | 1786 | 179 |

| body | value | active years | records | peak year |
| --- | --- | --- | --- | --- |
| Employment Appeals Tribunal | `2` | 2007–2026 | 16527 | 2012 (2833) |
| Equality Tribunal | `1` | 1996–2020 | 3170 | 2011 (341) |
| Labour Court | `3` | 1995–2026 | 16356 | 2010 (686) |
| Workplace Relations Commission | `15376` | 2016–2026 | 23976 | 2023 (2913) |

Corpus across 1995–2026: **60,029 records**, **6,003 listing pages** at a fixed page size of 10.

### Partition sizing

The busiest body-year is Workplace Relations Commission in 2023 (2,913 records). Publication is not evenly spread across that year,
so the peak month is measured directly rather than inferred from the annual total:

| month | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| records | 298 | 189 | 227 | 255 | 193 | 236 | 255 | 315 | 217 | 221 | 263 | 244 |

Months sum to 2,913 against an annual count of 2,913.

Each partition costs at least one listing request whether or not it holds records,
so partition size trades blast radius against fixed overhead:

| size | listing requests per year (4 bodies) | peak records per partition | peak pages | basis |
| --- | --- | --- | --- | --- |
| yearly | 4 | 2,913 | 292 | measured |
| quarterly | 16 | 787 | 79 | measured (worst 3 consecutive months) |
| monthly | 48 | 315 | 32 | measured (worst month) |
| weekly | 208 | ~78 | ~8 | estimated from the peak month |

Weekly over-partitions this corpus: the Labour Court publishes roughly ten records a
week, so most weekly partitions would be a single page and the per-partition overhead
would exceed the payload. Quarterly triples the blast radius of a failed partition to
save 32 requests a year. Monthly keeps overhead small while bounding re-work on
failure to a few minutes, and is the configured default.

The dead ranges above (bodies inactive for entire years) account for 44 of 128 body-years. Skipping them
saves more listing requests than this entire reconnaissance consumed.

Year-counts that could not be parsed: none.
