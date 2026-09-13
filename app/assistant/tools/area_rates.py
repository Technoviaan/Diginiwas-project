"""The `lookup_area_rates` tool: what land, plots or flats cost in an area.

Answers questions like "what's the average land price in Vijay Nagar, Indore?"
from two places at once: DigiNiwas' own live listings in that area, and the
rates property portals publish, found by web search and checked by
AreaRateFinder. The model gets a compact summary; the app gets the listings as
cards and the rate pages as source links.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.insights.models import LocalitySource
from app.insights.rates import AreaRate, AreaRateFinder, Kind
from app.properties import PropertiesAPIError, PropertiesClient, PropertyCard, PropertyQuery

logger = logging.getLogger(__name__)

TOOL_NAME = "lookup_area_rates"

# How DigiNiwas' own listings are filtered for each kind of question.
LISTING_FILTERS: dict[str, dict[str, str]] = {
    "land": {"category": "Plot/Land", "transaction_type": "Sale"},
    "flat": {"category": "Residential", "transaction_type": "Sale"},
    "any": {"transaction_type": "Sale"},
}
# Below this many listings, a median price per sq ft would be noise.
MIN_LISTINGS_FOR_MEDIAN = 3


class AreaRatesInput(BaseModel):
    """What to look up."""

    locality: str = Field(
        ..., min_length=1, description="The area or locality the user named, e.g. 'Vijay Nagar'."
    )
    city: str = Field(
        ..., min_length=1, description="The city the user named, e.g. 'Indore'. Ask for it rather than guess."
    )
    kind: Kind = Field(
        "any",
        description="'land' for land and plots, 'flat' for flats and apartments, 'any' when the user didn't say.",
    )


@dataclass(frozen=True, slots=True)
class AreaRatesArtifact:
    """What the app shows: DigiNiwas listings as cards, rate pages as source links."""

    cards: list[PropertyCard]
    sources: list[LocalitySource]


def build_area_rates_tool(client: PropertiesClient, finder: AreaRateFinder, *, page_size: int) -> BaseTool:
    async def lookup_area_rates(**arguments: Any) -> tuple[str, AreaRatesArtifact]:
        args = AreaRatesInput(**arguments)
        cards, listings_note = await _own_listings(client, args, page_size)
        rates = await finder.find(args.locality, args.city, args.kind)

        result: dict[str, Any] = {
            "area": f"{args.locality}, {args.city}",
            "looking_for": args.kind,
            "diginiwas_listings": _listing_summary(cards, listings_note),
            "published_rates": [_rate_summary(rate) for rate in rates or []],
        }
        if rates is None:
            result["note"] = (
                "Published area rates are not available on this server. Say so; do not guess a rate."
            )
        elif not rates:
            result["note"] = (
                "No published rate for this area could be verified. Say so plainly; do not guess a rate."
            )
        else:
            result["note"] = (
                "These are rates property portals publish, usually asking prices, not DigiNiwas "
                "valuations. Name the source of every figure you state."
            )

        artifact = AreaRatesArtifact(cards=cards, sources=[rate.as_source() for rate in rates or []])
        return json.dumps(result, ensure_ascii=False), artifact

    return StructuredTool.from_function(
        coroutine=lookup_area_rates,
        name=TOOL_NAME,
        description=(
            "Look up what land, plots or flats cost in an area: the price rates property portals "
            "publish for that locality, plus DigiNiwas' own listings there. Use it for questions "
            "about average prices or rates per sq ft in an area - not for finding listings to buy."
        ),
        args_schema=AreaRatesInput,
        response_format="content_and_artifact",
    )


async def _own_listings(
    client: PropertiesClient, args: AreaRatesInput, page_size: int
) -> tuple[list[PropertyCard], str | None]:
    query = PropertyQuery(search=args.locality, city=args.city, **LISTING_FILTERS[args.kind])  # type: ignore[arg-type]
    try:
        page = await client.search(query, limit=page_size)
    except (httpx.HTTPError, PropertiesAPIError) as exc:
        # Published rates are still worth giving when the listings API is down.
        logger.warning("Listings unavailable for area rates in %s: %s", args.locality, exc)
        return [], "DigiNiwas listings could not be loaded."
    return page.cards, None


def _listing_summary(cards: list[PropertyCard], note: str | None) -> dict[str, Any]:
    rates = [rate for card in cards if (rate := _price_per_sqft(card))]
    summary: dict[str, Any] = {"count": len(cards)}
    if len(rates) >= MIN_LISTINGS_FOR_MEDIAN:
        summary["median_price_per_sqft"] = round(statistics.median(rates))
    if note:
        summary["note"] = note
    return summary


def _rate_summary(rate: AreaRate) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "source": rate.source_name,
        "about": rate.kind,
        "basis": rate.basis,
        "rate": rate.summary,
        "quote": rate.quote,
    }
    if rate.unit == "bigha":
        summary["unit_note"] = (
            "Per bigha, as published. A bigha's size differs by state, so it isn't converted."
        )
    elif rate.unit != "sqft":
        summary["per_sq_ft"] = {
            name: rate.per_sqft(value)
            for name, value in (("average", rate.average), ("low", rate.low), ("high", rate.high))
            if value is not None
        }
    return summary


def _price_per_sqft(card: PropertyCard) -> float | None:
    if card.price_per_sqft:
        return float(card.price_per_sqft)
    if card.price and card.size:
        return float(card.price) / float(card.size)
    return None
