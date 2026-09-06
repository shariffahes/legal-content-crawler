# Reconnaissance report

Generated 2026-09-05 against `https://www.workplacerelations.ie` (Workplace Relations decisions and determinations).

Requests issued: **43**, issued sequentially over a single reused HTTP session, 0.6s apart. No concurrency.

Result: **0 failed** of 7 checks.

Regenerate with `python scripts/recon.py --full`.

---
## Selector contract

**PASS**

| field | selector | present |
| --- | --- | --- |
| result_count | `div.searchhead` | yes |
| record | `li.each-item` | yes |
| identifier | `h2.title a` | yes |
| published_date | `span.date` | yes |
| doc_path | `p.fullpath` | yes |
| description | `p.description` | yes |
| ref_no | `span.refNO` | yes |
| view_page | `div.bottom-ref a.btn-primary` | yes |
| next_page | `ul.pager a.next` | yes |

Fields empty across 10 records: none
`identifier` matches `ref_no` for every record: **True**

The document path is published three times per record. It is resolved by precedence
rather than by trusting one selector: `view_page` > `identifier` > `doc_path`.
The footer *View Page* link wins because it is the link a user actually follows;
`p.fullpath` is least trusted because its text node is empty and the value lives only
in a `title` attribute, so a text-based selector silently yields an empty string.

| source | agrees with resolved path |
| --- | --- |
| `view_page` | 10/10 |
| `identifier` | 10/10 |
| `doc_path` | 10/10 |

Records where the three sources disagreed: **0**
Records with no usable path at all: **0**

Sample record:
```
identifier_raw  ' ADJ-00022400'
identifier      'ADJ-00022400'
extras          {'ref_no': 'ADJ-00022400'}
description     'A Receptionist Medical Secretary / A Medical Practice'
published_date  datetime.date(2020, 12, 31)
doc_path        '/en/cases/2021/january/adj-00022400.html'
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

Configured page size = 10.

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
