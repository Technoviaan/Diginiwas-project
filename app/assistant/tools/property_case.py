"""The `property_case` tool: the evidence for one listing, gathered in one call.

Answers "why should I buy DW-1003?" with checkable facts rather than sales
talk: the listing's own data, how its price sits against comparable listings
and against the rates portals publish for the area, the rental yield, the
locality trend, and the schools, hospitals and connectivity web pages list
nearby. Each web figure carries its source, and whatever could not be
established is named so the model says so instead of inventing it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator

from app.insights.guide import TOPICS, LocalityGuideFinder, LocalityPlace, Topic
from app.insights.models import LocalitySource, PropertySnapshot
from app.insights.rates import AreaRate, AreaRateFinder, Kind
from app.insights.snapshot import SnapshotService
from app.properties import PropertiesAPIError, PropertiesClient, PropertyCard

logger = logging.getLogger(__name__)

TOOL_NAME = "property_case"

# Which published rates are comparable with this listing's price per sq ft.
RATE_KIND_BY_CATEGORY: dict[str, Kind] = {"Plot/Land": "land", "Residential": "flat"}
# Facts about the listing worth putting in front of the model.
FEATURES = (
    "project_name",
    "transaction_type",
    "price_label",
    "price_per_sqft",
    "size",
    "size_unit",
    "carpet_area",
    "bedrooms",
    "bathrooms",
    "balconies",
    "furnishing",
    "facing",
    "parking",
    "floor_no",
    "total_floors",
    "maintenance",
    "booking_amount",
    "negotiable",
    "verified",
)


class PropertyCaseInput(BaseModel):
    """Which listing to make the case for."""

    property_id: str = Field(
        ...,
        min_length=1,
        description="The listing's ID, e.g. 'DW-1003': the one the user asked about, or the selected one.",
    )

    @field_validator("property_id", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True, slots=True)
class PropertyCaseArtifact:
    """What the app shows: the listing's card, and every page quoted."""

    sources: list[LocalitySource]
    cards: list[PropertyCard] = field(default_factory=list)


def build_property_case_tool(
    properties: PropertiesClient,
    snapshots: SnapshotService,
    *,
    guide: LocalityGuideFinder | None = None,
    rates: AreaRateFinder | None = None,
) -> BaseTool:
    async def property_case(**arguments: Any) -> tuple[str, PropertyCaseArtifact]:
        args = PropertyCaseInput(**arguments)
        try:
            listing = await properties.find_by_id(args.property_id)
        except (httpx.HTTPError, PropertiesAPIError) as exc:
            logger.warning("Listing %s unavailable for its case: %s", args.property_id, exc)
            return _only_note(
                args.property_id,
                "DigiNiwas listings could not be loaded right now. Say so and suggest trying again shortly.",
            )
        if listing is None:
            return _only_note(
                args.property_id,
                f"No live DigiNiwas listing has the ID {args.property_id}. Say so and ask which property they mean.",
            )

        snapshot, places, area_rates = await _gather(listing, snapshots, guide, rates)

        result: dict[str, Any] = {"property": _property(listing)}
        missing: list[str] = []
        if snapshot is not None:
            result["price_check"] = _price_check(snapshot)
            result["rental_yield"] = _rental_yield(snapshot)
            result["locality_trend"] = _trend(snapshot)
            result["data_confidence"] = {
                "level": snapshot.data_confidence.level,
                "caption": snapshot.data_confidence.caption,
            }
            if not snapshot.rental_yield.available:
                missing.append("rental yield")
            if not snapshot.locality_trend.available:
                missing.append("locality price trend")
        else:
            missing.append("price comparison, rental yield and locality trend")

        if comparison := _versus_area_rate(listing, area_rates):
            result["versus_published_rate"] = comparison
        if area_rates is not None and not area_rates:
            missing.append("published area rate")

        if places is not None:
            result["nearby"] = {topic: [_place(place) for place in places.get(topic, [])] for topic in TOPICS}
            missing.extend(topic for topic in TOPICS if not places.get(topic))
        else:
            missing.append("schools, hospitals and connectivity")

        if missing:
            result["not_established"] = missing
        result["note"] = _NOTE

        sources = _sources(snapshot, places, area_rates)
        return json.dumps(result, ensure_ascii=False, default=str), PropertyCaseArtifact(
            sources=sources, cards=[listing]
        )

    return StructuredTool.from_function(
        coroutine=property_case,
        name=TOOL_NAME,
        description=(
            'The evidence for one listing, for questions like "why should I buy this?", "what\'s '
            'good about it?", "is it worth the price?" or "what\'s nearby?". Gathers the '
            "listing's data, how its price compares with similar listings and with published area "
            "rates, the rental yield, the locality trend, and nearby schools, hospitals and transport."
        ),
        args_schema=PropertyCaseInput,
        response_format="content_and_artifact",
    )


_NOTE = (
    "Make the case for this listing from these facts only. Name the source of every web figure, and "
    "say nearby places and distances are what pages list for the locality, not measured from the "
    "building. State the honest picture: if the price is above comparable listings, or something is "
    "in not_established, say so. Never invent an amenity, distance, school or rating, and never "
    "promise that prices will rise."
)


async def _gather(
    listing: PropertyCard,
    snapshots: SnapshotService,
    guide: LocalityGuideFinder | None,
    rates: AreaRateFinder | None,
) -> tuple[PropertySnapshot | None, dict[Topic, list[LocalityPlace]] | None, list[AreaRate] | None]:
    """The snapshot, nearby places and published rates at once; None for whatever failed or is off."""
    locality, city = listing.locality, listing.city
    kind = RATE_KIND_BY_CATEGORY.get(listing.category or "", "any")
    results = await asyncio.gather(
        snapshots.for_listing(listing.id),
        guide.find(locality, city) if guide is not None and locality and city else _none(),
        rates.find(locality, city, kind) if rates is not None and locality and city else _none(),
        return_exceptions=True,
    )
    gathered: list[Any] = []
    for name, value in zip(("snapshot", "nearby places", "published rates"), results, strict=True):
        if isinstance(value, BaseException):
            logger.warning("Could not gather %s for %s: %s", name, listing.id, value)
            gathered.append(None)
        else:
            gathered.append(value)
    return gathered[0], gathered[1], gathered[2]


async def _none() -> None:
    return None


def _property(listing: PropertyCard) -> dict[str, Any]:
    facts: dict[str, Any] = {"id": listing.id, "title": listing.title}
    facts.update({name: value for name in FEATURES if (value := getattr(listing, name)) is not None})
    facts["area"] = ", ".join(part for part in (listing.locality, listing.city) if part)
    for name in ("amenities", "tags"):
        if values := getattr(listing, name):
            facts[name] = values
    if listing.listed_on is not None:
        facts["listed_on"] = listing.listed_on
    return facts


def _price_check(snapshot: PropertySnapshot) -> dict[str, Any]:
    price = snapshot.price_comparison
    return {
        "headline": price.headline,
        "compared_with": price.caption,
        "listing_per_sqft": price.listing_price_per_sqft,
        "comparables_median_per_sqft": price.median_price_per_sqft,
        "comparable_listings": price.sample_size,
        "confidence": price.confidence,
    }


def _rental_yield(snapshot: PropertySnapshot) -> dict[str, Any]:
    yields = snapshot.rental_yield
    if not yields.available:
        return {"available": False, "headline": yields.headline, "why": yields.basis}
    return {
        "available": True,
        "headline": yields.headline,
        "gross_percent": yields.gross_percent,
        "net_percent": yields.net_percent,
        "monthly_rent": yields.estimated_monthly_rent,
        "rent_from": yields.source,
        "source": yields.source_name,
        "quote": yields.quote,
        "confidence": yields.confidence,
    }


def _trend(snapshot: PropertySnapshot) -> dict[str, Any]:
    trend = snapshot.locality_trend
    if not trend.available:
        return {"available": False, "headline": trend.headline}
    return {
        "available": True,
        "headline": trend.headline,
        "yearly_percent": trend.yearly_percent,
        "source": trend.source_name,
        "quote": trend.quote,
        "confidence": trend.confidence,
    }


def _versus_area_rate(listing: PropertyCard, rates: list[AreaRate] | None) -> dict[str, Any] | None:
    """The listing's price per sq ft against a published rate for the same kind of property."""
    wanted = RATE_KIND_BY_CATEGORY.get(listing.category or "")
    per_sqft = listing.price_per_sqft
    if not rates or not per_sqft or wanted is None or listing.transaction_type == "Rent":
        return None
    for rate in rates:
        area_per_sqft = rate.per_sqft(rate.average)
        if rate.kind != wanted or area_per_sqft is None or area_per_sqft <= 0:
            continue
        difference = (float(per_sqft) - area_per_sqft) / area_per_sqft * 100
        return {
            "listing_per_sqft": round(float(per_sqft)),
            "area_average_per_sqft": area_per_sqft,
            "difference_percent": round(difference, 1),
            "about": rate.kind,
            "basis": rate.basis,
            "source": rate.source_name,
            "quote": rate.quote,
        }
    return None


def _place(place: LocalityPlace) -> dict[str, Any]:
    summary: dict[str, Any] = {"name": place.name, "listed_by": place.source_name}
    if place.distance_km is not None:
        summary["distance_km"] = place.distance_km
    return summary


def _sources(
    snapshot: PropertySnapshot | None,
    places: dict[Topic, list[LocalityPlace]] | None,
    rates: list[AreaRate] | None,
) -> list[LocalitySource]:
    """Every page quoted, once each, listing pages first."""
    sources: list[LocalitySource] = []
    if snapshot is not None:
        for figure in (snapshot.rental_yield, snapshot.locality_trend):
            if figure.source_url and figure.quote:
                sources.append(
                    LocalitySource(
                        title=figure.source_name or figure.source_url,
                        url=figure.source_url,
                        snippet=figure.quote,
                        source=figure.source_name,
                    )
                )
    sources.extend(rate.as_source() for rate in rates or [])
    for topic in TOPICS:
        for place in (places or {}).get(topic, []):
            sources.append(
                LocalitySource(
                    title=place.title, url=place.source_url, snippet=place.quote, source=place.source_name
                )
            )

    seen: set[str] = set()
    unique: list[LocalitySource] = []
    for source in sources:
        if source.url in seen:
            continue
        seen.add(source.url)
        unique.append(source)
    return unique


def _only_note(property_id: str, note: str) -> tuple[str, PropertyCaseArtifact]:
    return json.dumps({"property_id": property_id, "note": note}), PropertyCaseArtifact(sources=[])
