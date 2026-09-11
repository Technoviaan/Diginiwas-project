"""Search filters for GET /api/properties, and their mapping to query parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


def _room_count(count: int) -> str:
    # The API matches room counts as exact strings: "7" does not match a
    # "7+" listing.
    return "7+" if count >= 7 else str(count)
