"""The `search_properties` tool: how the model searches DigiNiwas listings.

Each call produces two outputs, on purpose:

- a compact JSON summary for the model: few tokens, only what it needs to
  recommend and compare; and
- the full `PropertyCard`s as the tool's *artifact*. LangChain never sends
  artifacts to the model; the agent hands them to the app to render.

The input model is written for the language model rather than for people:
budgets arrive the way users say them ("1 crore") and are converted here,
because the model is unreliable at that arithmetic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.properties import (
    PropertiesAPIError,
    PropertiesClient,
    PropertyCard,
    PropertyQuery,
    format_inr,
    parse_inr,
)
from app.properties.query import Category, Furnishing, TransactionType

logger = logging.getLogger(__name__)

TOOL_NAME = "search_properties"

# How far either side of an "around X" price to search.
APPROX_PRICE_SPREAD = 0.15

_PRICE_HELP = (
    "Pass it exactly as the user said it - '30K', '50 lakh', '1.2 crore' - and it "
    "is converted to rupees for you. For rent, the monthly amount."
)


class PropertySearchInput(BaseModel):
    """Filters for DigiNiwas' live, verified listings. All optional."""

    search: str | None = Field(
        None,
        description=(
            "Free text matched against locality, project name, title and listing "
            "ID. E.g. 'Vijay Nagar', 'Royal Residency', 'DW-1001'."
        ),
    )
    city: str | None = Field(None, description="City name the user gave, e.g. 'Indore'. Never guessed.")
    category: Category | None = Field(
        None,
        description=(
            "Residential for flats/houses/villas, Commercial for shops/offices, "
            "Plot/Land for plots. Omit unless the user named a property type."
        ),
    )
    transaction_type: TransactionType | None = Field(
        None, description="'Sale' to buy, 'Rent' to rent or lease."
    )
    min_price: int | str | None = Field(None, description=f"Minimum price. {_PRICE_HELP}")
    max_price: int | str | None = Field(None, description=f"Maximum price / budget. {_PRICE_HELP}")
    approx_price: int | str | None = Field(
        None,
        description=(
            "Use instead of min/max when the user says 'around', 'about' or "
            f"'approximately' a price; searches 15% either side. {_PRICE_HELP}"
        ),
    )
    bedrooms: int | None = Field(
        None, ge=1, le=20, description="Bedrooms / BHK. 7 or more matches '7+' listings."
    )
    bathrooms: int | None = Field(None, ge=1, le=20, description="Number of bathrooms.")
    furnishing: Furnishing | None = None
    negotiable: bool | None = Field(
        None, description="Only set when the user explicitly wants negotiable prices."
    )
    page: int = Field(1, ge=1, description="Results page. Increase for 'show me more'.")

    @field_validator("min_price", "max_price", "approx_price", mode="before")
    @classmethod
    def _to_rupees(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        return parse_inr(value)

    @model_validator(mode="after")
    def _price_range(self) -> PropertySearchInput:
        # "Around X" becomes a range here rather than in the model: asked to
        # do the ±15% itself, gpt-4o-mini sent only max_price=X, which hid
        # listings just above X that a user would count as "around" it.
        if self.approx_price is not None:
            if self.min_price is None:
                self.min_price = round(int(self.approx_price) * (1 - APPROX_PRICE_SPREAD))
            if self.max_price is None:
                self.max_price = round(int(self.approx_price) * (1 + APPROX_PRICE_SPREAD))
        if (
            self.min_price is not None
            and self.max_price is not None
            and int(self.min_price) > int(self.max_price)
        ):
            raise ValueError("min_price is greater than max_price")
        return self

    def to_query(self) -> PropertyQuery:
        return PropertyQuery(
            search=self.search or None,
            city=self.city or None,
            category=self.category,
            transaction_type=self.transaction_type,
            min_price=None if self.min_price is None else int(self.min_price),
            max_price=None if self.max_price is None else int(self.max_price),
            bedrooms=self.bedrooms,
            bathrooms=self.bathrooms,
            furnishing=self.furnishing,
            negotiable=self.negotiable,
            page=self.page,
        )

    def describe(self) -> dict[str, Any]:
        """The filters used, as the model should read them back.

        Prices carry their Indian label, so the model restates the budget from
        the parsed value rather than from its own arithmetic.
        """
        applied = self.model_dump(exclude_none=True, exclude_defaults=True)
        for key in ("min_price", "max_price", "approx_price"):
            if key in applied:
                applied[key] = f"{applied[key]} ({format_inr(applied[key])})"
        return applied


def summarize_for_model(card: PropertyCard) -> dict[str, Any]:
    """The part of a card the model sees. Leaves out images, coordinates and URLs."""
    price = f"{card.price_label}{card.price_period or ''}" if card.price_label else None
    floor = (
        f"{card.floor_no:g} of {card.total_floors:g}"
        if card.floor_no is not None and card.total_floors is not None
        else None
    )
    summary = {
        "id": card.id,
        "title": card.title,
        "project": card.project_name,
        "for": card.transaction_type,
        "category": card.category,
        "price_label": price,
        "price_rupees": card.price,
        "price_per_sqft": card.price_per_sqft,
        "maintenance": card.maintenance,
        "negotiable": card.negotiable,
        "locality": card.locality,
        "city": card.city,
        "bedrooms": card.bedrooms,
        "bathrooms": card.bathrooms,
        "size": f"{card.size:g} {card.size_unit}" if card.size else None,
        "carpet_area": card.carpet_area,
        "furnishing": card.furnishing,
        "floor": floor,
        "facing": card.facing,
        "parking": card.parking,
        "amenities": card.amenities,
        "tags": card.tags,
        "description": card.description[:300] if card.description else None,
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def build_property_search_tool(client: PropertiesClient, *, page_size: int) -> BaseTool:
    async def search_properties(**filters: Any) -> tuple[str, list[PropertyCard] | None]:
        # Re-validate: LangChain passes only the keys the model sent, so
        # defaults such as page=1 would otherwise be missing.
        arguments = PropertySearchInput(**filters)
        applied = arguments.describe()

        try:
            page = await client.search(arguments.to_query(), limit=page_size)
        except httpx.TimeoutException:
            logger.warning("Properties API timed out for %s", applied)
            error = "The listings service timed out."
        except (httpx.HTTPError, PropertiesAPIError, ValueError) as exc:
            logger.warning("Properties API failed for %s: %s", applied, exc)
            error = "The listings service is unavailable."
        else:
            result: dict[str, Any] = {
                "total_matches": page.total,
                "page": page.page,
                "total_pages": page.total_pages,
                "showing": len(page.cards),
                "filters_applied": applied,
                "listings": [summarize_for_model(card) for card in page.cards],
            }
            if not page.cards:
                result["note"] = (
                    "No listings matched. Tell the user, then consider searching "
                    "again with one filter removed."
                )
            return json.dumps(result, ensure_ascii=False), page.cards

        # Artifact None (rather than []) tells the agent the search failed.
        return json.dumps({"error": f"{error} No results could be loaded."}), None

    return StructuredTool.from_function(
        coroutine=search_properties,
        name=TOOL_NAME,
        description=(
            "Search DigiNiwas' live, verified property listings. Returns matching "
            "listings with price, location, size, bedrooms, furnishing and "
            "amenities. Always call this before recommending or describing a property."
        ),
        args_schema=PropertySearchInput,
        response_format="content_and_artifact",
    )
