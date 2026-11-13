"""Search filters for GET /api/properties, and their mapping to query parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.properties.models import PropertyCard

Category = Literal["Residential", "Commercial", "Rental", "Sell", "Plot/Land"]
TransactionType = Literal["Sale", "Rent"]
Furnishing = Literal["Furnished", "Semi-Furnished", "Unfurnished"]


@dataclass(frozen=True, slots=True)
class PropertyQuery:
    """Listing filters. `None` means "don't filter on this"."""

    search: str | None = None
    city: str | None = None
    category: Category | None = None
    transaction_type: TransactionType | None = None
    min_price: int | None = None
    max_price: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    furnishing: Furnishing | None = None
    negotiable: bool | None = None
    page: int = 1

    def to_params(self, limit: int) -> dict[str, str | int]:
        """The API's camelCase query parameters, for the filters that are set."""
        params: dict[str, str | int] = {"page": self.page, "limit": limit}
        for attribute, param in (
            ("search", "search"),
            ("city", "city"),
            ("category", "category"),
            ("transaction_type", "transactionType"),
            ("min_price", "minPrice"),
            ("max_price", "maxPrice"),
            ("furnishing", "furnishing"),
        ):
            value = getattr(self, attribute)
            if value is not None and value != "":
                params[param] = value
        if self.bedrooms is not None:
            params["bedrooms"] = _room_count(self.bedrooms)
        if self.bathrooms is not None:
            params["bathrooms"] = _room_count(self.bathrooms)
        if self.negotiable is not None:
            params["negotiable"] = "true" if self.negotiable else "false"
        return params

    @property
    def has_filters(self) -> bool:
        """Whether anything but paging is set."""
        return any(
            getattr(self, name) is not None
            for name in (
                "search",
                "city",
                "category",
                "transaction_type",
                "min_price",
                "max_price",
                "bedrooms",
                "bathrooms",
                "furnishing",
                "negotiable",
            )
        )

    def matches(self, card: PropertyCard) -> bool:
        """Whether a listing satisfies these filters, judged here rather than by the API.

        Used when the API returns nothing for filters it can't apply itself -
        newer listings keep their city and locality in a nested object its
        filters don't read - so a search still finds listings that do match.
        """
        if self.transaction_type and card.transaction_type != self.transaction_type:
            return False
        if self.city and not _mentions(self.city, card.city):
            return False
        if self.category and not _mentions(self.category, card.category):
            return False
        if self.furnishing and card.furnishing != self.furnishing:
            return False
        if self.negotiable is not None and card.negotiable is not self.negotiable:
            return False
        if self.bedrooms is not None and not _rooms_match(self.bedrooms, card.bedrooms):
            return False
        if self.bathrooms is not None and not _rooms_match(self.bathrooms, card.bathrooms):
            return False
        price = float(card.price) if card.price else None
        if self.min_price is not None and (price is None or price < self.min_price):
            return False
        if self.max_price is not None and (price is None or price > self.max_price):
            return False
        return not (self.search and not _found_in_text(self.search, card))


def _mentions(wanted: str, value: str | None) -> bool:
    """'Indore' matches 'Indore' and 'Indore City', either way round."""
    if not value:
        return False
    wanted, value = wanted.casefold().strip(), value.casefold().strip()
    return wanted in value or value in wanted


def _found_in_text(search: str, card: PropertyCard) -> bool:
    """Every word of the search appears somewhere a user would expect to find it."""
    haystack = " ".join(
        part.casefold()
        for part in (card.id, card.title, card.project_name, card.locality, card.city, card.address)
        if part
    )
    return all(word in haystack for word in search.casefold().split())


def _rooms_match(wanted: int, listed: str | None) -> bool:
    """'3' matches 3, and '7+' matches 7 or more."""
    if not listed:
        return False
    listed = listed.strip()
    if listed.endswith("+"):
        start = listed[:-1]
        return start.isdigit() and wanted >= int(start)
    return listed.isdigit() and int(listed) == wanted


def _room_count(count: int) -> str:
    # The API matches room counts as exact strings: "7" does not match a
    # "7+" listing.
    return "7+" if count >= 7 else str(count)
