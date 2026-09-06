from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import yaml

from wrc.documents import DocumentContract
from wrc.pagination import Pagination, PaginationStrategy, strategy_for
from wrc.parsing import ListingContract
from wrc.partitions import Partition

# Metadata fields a facet is allowed to populate. Validated at load so a typo or an
# unsupported vocabulary fails immediately, rather than silently leaving the field empty
# on every record. Supporting a new target is a deliberate pair of edits: add the field
# to DocumentRecord, add its name here.
FACET_TARGETS = frozenset({"issuing_authority"})


@dataclass(frozen=True, slots=True)
class FacetValue:
    id: str
    name: str
    active_from: date | None = None

    def covers(self, partition: Partition) -> bool:
        return self.active_from is None or partition.end >= self.active_from


@dataclass(frozen=True, slots=True)
class Facet:
    param: str
    maps_to: str
    values: tuple[FacetValue, ...]

    def value(self, value_id: str) -> FacetValue:
        for value in self.values:
            if value.id == value_id:
                return value
        raise KeyError(f"unknown {self.maps_to} id {value_id!r}")


@dataclass(frozen=True, slots=True)
class ListingConfig:
    path: str
    page_size: int
    date_format: str
    param_date_from: str
    param_date_to: str
    param_page: str
    static_params: dict[str, str | int]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    name: str
    display_name: str
    jurisdiction: str
    language: str
    base_url: str
    listing: ListingConfig
    facet: Facet
    pagination: PaginationStrategy
    contract: ListingContract
    document: DocumentContract

    @classmethod
    def load(cls, path: str | Path) -> SourceSpec:
        raw = yaml.safe_load(Path(path).read_text())
        listing = raw["listing"]
        params = listing["params"]
        facet = raw["facet"]

        if facet["maps_to"] not in FACET_TARGETS:
            raise ValueError(
                f"facet maps_to {facet['maps_to']!r} is not one of {sorted(FACET_TARGETS)}"
            )

        pagination = strategy_for(listing["pagination"])
        return cls(
            name=raw["name"],
            display_name=raw["display_name"],
            jurisdiction=raw["jurisdiction"],
            language=raw["language"],
            base_url=raw["base_url"].rstrip("/"),
            listing=ListingConfig(
                path=listing["path"],
                page_size=int(listing["page_size"]),
                date_format=listing["date_format"],
                param_date_from=params["date_from"],
                param_date_to=params["date_to"],
                param_page=params["page"],
                static_params=dict(params.get("static") or {}),
            ),
            facet=Facet(
                param=facet["param"],
                maps_to=facet["maps_to"],
                values=tuple(
                    FacetValue(id=str(v["id"]), name=v["name"], active_from=v.get("active_from"))
                    for v in facet["values"]
                ),
            ),
            pagination=pagination,
            # The strategy says what the contract must declare for it to work.
            contract=ListingContract.from_config(raw["listing_contract"], pagination),
            document=DocumentContract.from_config(raw.get("document_contract")),
        )

    @property
    def search_url(self) -> str:
        return f"{self.base_url}{self.listing.path}"

    def format_date(self, value: date) -> str:
        return value.strftime(self.listing.date_format)

    def listing_params(self, facet_value: FacetValue, partition: Partition, page: int) -> dict:
        """Query parameters for one listing page.

        The window comes from the partition's clamped start/end, never its calendar
        period: requesting the period would fetch outside the range the run was asked for.
        """
        listing = self.listing
        return {
            **listing.static_params,
            self.facet.param: facet_value.id,
            listing.param_date_from: self.format_date(partition.start),
            listing.param_date_to: self.format_date(partition.end),
            listing.param_page: page,
        }

    def absolute_url(self, path: str, found_on: str | None = None) -> str:
        """Resolve a link the way a browser would: relative to the page it was found on."""
        return urljoin(found_on or f"{self.base_url}/", path)
