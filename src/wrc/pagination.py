from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from urllib.parse import urljoin

from wrc.parsing import ListingContract, extract_total, next_page_url


class Pagination(StrEnum):
    DECLARED_TOTAL = "declared_total"
    FOLLOW_NEXT = "follow_next"


@dataclass(frozen=True, slots=True)
class PageInfo:
    """What a strategy learned from one listing page."""

    declared_total: int | None = None
    pages_expected: int | None = None
    prefetch: range = range(0)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Continuation:
    """What follows this page: a URL to request, anomalies to report, or neither.

    Always returned, so a caller has one shape to handle: log every anomaly, request
    the URL if there is one. A clean end is simply no URL and no anomalies.
    """

    url: str | None = None
    recovery_depth: int = 0
    anomalies: tuple[str, ...] = ()

    @property
    def stops(self) -> bool:
        return self.url is None


STOP = Continuation()


class PaginationStrategy(ABC):
    kind: Pagination
    required_selectors: frozenset[str] = frozenset()
    required_patterns: frozenset[str] = frozenset()

    @abstractmethod
    def inspect(self, html: str, contract: ListingContract, page: int, page_size: int) -> PageInfo:
        """Read a page. On page 1 this also plans which further pages to fetch."""

    @abstractmethod
    def continue_from(
        self,
        html: str,
        contract: ListingContract,
        page: int,
        pages_expected: int | None,
        recovery_depth: int,
        max_recovery: int,
    ) -> Continuation:
        """The strategy-specific decision: does another page follow this one?"""

    def advance(
        self,
        html: str,
        contract: ListingContract,
        *,
        page: int,
        pages_expected: int | None,
        recovery_depth: int,
        max_recovery: int,
        base_url: str,
        urls_fetched: set[str],
        new_records: int,
    ) -> Continuation:
        """continue_from, plus the invariants no strategy is allowed to break.

        A pager that never ends looks like one of two things: a link back to a page
        already fetched, or a URL that keeps advancing while the content stays the same
        (a site clamping to its last page). Neither depends on how pages were found, so
        both are enforced here rather than in each strategy.

        base_url is the page just parsed. It is recorded here, so the set of fetched
        URLs has one owner and is always absolute.
        """
        urls_fetched.add(base_url)
        step = self.continue_from(html, contract, page, pages_expected, recovery_depth, max_recovery)
        if step.url is None:
            return step

        next_url = urljoin(base_url, step.url)
        if next_url in urls_fetched:
            return replace(step, url=None, anomalies=step.anomalies + ("pagination_loop",))
        if page > 1 and new_records == 0:
            return replace(step, url=None, anomalies=step.anomalies + ("pagination_no_progress",))
        return replace(step, url=next_url)


class DeclaredTotal(PaginationStrategy):
    """The site publishes a match count.

    Pages are computed from it and fetched concurrently, which is what makes a partition
    parallelisable. The pager link, if the source has one, is used afterwards only to
    check the enumeration was not short; if it was, the tail is recovered by following
    the pager, bounded, and the discrepancy is reported.
    """

    kind = Pagination.DECLARED_TOTAL
    required_selectors = frozenset({"result_count"})
    required_patterns = frozenset({"total", "no_results"})

    def inspect(self, html, contract, page, page_size):
        declared = extract_total(html, contract)
        if declared is None:
            # Never fall back to zero: a silent zero would let a run report success
            # having scraped nothing.
            return PageInfo(error="unparseable_result_count")
        if page != 1:
            return PageInfo(declared_total=declared)
        pages = int(math.ceil(declared / page_size)) if declared > 0 else 0
        return PageInfo(declared_total=declared, pages_expected=pages, prefetch=range(2, pages + 1))

    def continue_from(self, html, contract, page, pages_expected, recovery_depth, max_recovery):
        following = next_page_url(html, contract)
        if following is None:
            return STOP
        at_expected_end = pages_expected is not None and page >= pages_expected
        if not at_expected_end and recovery_depth == 0:
            return STOP
        if recovery_depth >= max_recovery:
            return Continuation(
                url=None, recovery_depth=recovery_depth, anomalies=("pagination_recovery_capped",)
            )
        return Continuation(
            url=following,
            recovery_depth=recovery_depth + 1,
            anomalies=("pagination_underrun",) if recovery_depth == 0 else (),
        )


class FollowNext(PaginationStrategy):
    """The site publishes no count, so the pager is the only way forward.

    Serial by nature, and a dropped request is indistinguishable from the end of the
    results, so completeness can only be self-reported. Used where nothing better exists.
    """

    kind = Pagination.FOLLOW_NEXT
    required_selectors = frozenset({"next_page"})

    def inspect(self, html, contract, page, page_size):
        return PageInfo()

    def continue_from(self, html, contract, page, pages_expected, recovery_depth, max_recovery):
        following = next_page_url(html, contract)
        return Continuation(url=following) if following else STOP


_STRATEGIES: dict[Pagination, type[PaginationStrategy]] = {
    DeclaredTotal.kind: DeclaredTotal,
    FollowNext.kind: FollowNext,
}


def strategy_for(kind: Pagination | str) -> PaginationStrategy:
    return _STRATEGIES[Pagination(kind)]()
