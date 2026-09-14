"""The `locality_guide` tool: schools, hospitals and connectivity near an area.

Answers questions like "is Rau good for families?", "how far is Vijay Nagar
from the airport?" or "any schools near DW-1003?" from places web pages list
for the area, each checked by LocalityGuideFinder. Given a listing's ID, it
looks the listing up and uses its locality and city. The model gets names,
distances and the site listing them; the app gets the pages as source links,
and the listing's card when one was named.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.insights.guide import TOPICS, LocalityGuideFinder, LocalityPlace, Topic
from app.insights.models import LocalitySource
from app.properties import PropertiesAPIError, PropertiesClient, PropertyCard

logger = logging.getLogger(__name__)

TOOL_NAME = "locality_guide"


class LocalityGuideInput(BaseModel):
    """What to look up: a listing's surroundings, or a named area."""

    property_id: str | None = Field(
        None,
        description=(
            "A DigiNiwas listing ID such as 'DW-1003', when the user asks about schools, hospitals "
            "or connectivity near a property: named by ID, or a listing shown earlier in the "
            "conversation. The listing's own locality and city are used, so leave those out."
        ),
    )
    locality: str | None = Field(
        None, description="The area the user named, e.g. 'Rau'. Not needed with property_id."
    )
    city: str | None = Field(
        None,
        description="The city the user named, e.g. 'Indore'. Not needed with property_id; otherwise ask rather than guess.",
    )
    topics: list[Topic] = Field(
        default_factory=lambda: list(TOPICS),
        description=(
            "Only what the user asked about: 'schools', 'hospitals', 'connectivity' (stations, "
            "airport, distances). All three only when they asked about the area in general."
        ),
    )

    @field_validator("property_id", "locality", "city", mode="before")
    @classmethod
    def _blank_is_missing(cls, value: object) -> object:
        return (value.strip() or None) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _needs_a_property_or_an_area(self) -> LocalityGuideInput:
        if self.property_id is None and not (self.locality and self.city):
            raise ValueError("Give a property_id, or both locality and city.")
        return self


@dataclass(frozen=True, slots=True)
class LocalityGuideArtifact:
    """What the app shows: the pages the places came from, and the listing asked about."""

    sources: list[LocalitySource]
    cards: list[PropertyCard] = field(default_factory=list)


def build_locality_guide_tool(
    finder: LocalityGuideFinder, *, properties: PropertiesClient | None = None
) -> BaseTool:
    async def locality_guide(**arguments: Any) -> tuple[str, LocalityGuideArtifact]:
        args = LocalityGuideInput(**arguments)
        topics = list(dict.fromkeys(args.topics)) or list(TOPICS)

        listing: PropertyCard | None = None
        if args.property_id is not None:
            listing, problem = await _listing(properties, args.property_id)
            if listing is None:
                return json.dumps({"property_id": args.property_id, "note": problem}), LocalityGuideArtifact(
                    sources=[]
                )
            locality, city = listing.locality, listing.city
        else:
            locality, city = args.locality, args.city
        assert locality and city  # guaranteed by the input model or by _listing

        result: dict[str, Any] = {"area": f"{locality}, {city}"}
        if listing is not None:
            result["property"] = {
                "id": listing.id,
                "title": listing.title,
                "address": listing.address,
                "locality": listing.locality,
                "city": listing.city,
            }
        cards = [listing] if listing is not None else []

        found = await finder.find(locality, city, topics)
        if found is None:
            result["note"] = (
                "The locality guide is not available on this server. Say so; do not answer from memory."
            )
            return json.dumps(result, ensure_ascii=False), LocalityGuideArtifact(sources=[], cards=cards)

        for topic in topics:
            result[topic] = [_place_summary(place) for place in found.get(topic, [])]
        note = (
            "These are places web pages list for this area, not DigiNiwas recommendations or rankings. "
            "Mention only these, name the site that lists them, and give distances only as stated here. "
            "Do not call any of them good, best, top or notable."
        )
        if listing is not None:
            note += (
                f" They are listed for {locality}, where {listing.id} is: say so, and don't present "
                "them as measured distances from the property itself."
            )
        if missing := [topic for topic in topics if not found.get(topic)]:
            note += (
                f" Nothing reliable was found for: {', '.join(missing)}. Say so; do not fill in from memory."
            )
        result["note"] = note

        places = [place for topic in topics for place in found.get(topic, [])]
        artifact = LocalityGuideArtifact(sources=_sources(places), cards=cards)
        return json.dumps(result, ensure_ascii=False), artifact

    return StructuredTool.from_function(
        coroutine=locality_guide,
        name=TOOL_NAME,
        description=(
            "Look up what an area is like to live in: schools, hospitals, and connectivity "
            "(railway station, airport, metro and their distances), as web pages list them. "
            "Use it for questions about an area's amenities or how well connected it is - "
            "including near a DigiNiwas property, by passing its property_id."
        ),
        args_schema=LocalityGuideInput,
        response_format="content_and_artifact",
    )


async def _listing(properties: PropertiesClient | None, property_id: str) -> tuple[PropertyCard | None, str]:
    """The listing to look around, or why there isn't one."""
    if properties is None:
        return None, "Listings can't be looked up here. Ask the user which area and city they mean."
    try:
        listing = await properties.find_by_id(property_id)
    except (httpx.HTTPError, PropertiesAPIError) as exc:
        logger.warning("Listing %s unavailable for the locality guide: %s", property_id, exc)
        return None, (
            "DigiNiwas listings could not be loaded right now. Say so, and ask which area and city "
            "they mean instead."
        )
    if listing is None:
        return None, (
            f"No live DigiNiwas listing has the ID {property_id}. Say so, and ask which property or "
            "area they mean."
        )
    if not listing.locality or not listing.city:
        return None, (
            f"Listing {listing.id} has no locality or city on record. Ask the user which area they mean."
        )
    return listing, ""


def _place_summary(place: LocalityPlace) -> dict[str, Any]:
    summary: dict[str, Any] = {"name": place.name, "listed_by": place.source_name}
    if place.distance_km is not None:
        summary["distance_km"] = place.distance_km
    summary["quote"] = place.quote
    return summary


def _sources(places: list[LocalityPlace]) -> list[LocalitySource]:
    """One link per page, quoting everything taken from it once."""
    pages: dict[str, tuple[LocalityPlace, list[str]]] = {}
    for place in places:
        _, quotes = pages.setdefault(place.source_url, (place, []))
        if any(place.quote in quote for quote in quotes):
            continue
        # A longer quote (often the whole snippet) replaces the parts of it already kept.
        quotes[:] = [quote for quote in quotes if quote not in place.quote]
        quotes.append(place.quote)

    sources: list[LocalitySource] = []
    shown: set[tuple[str | None, str]] = set()
    for url, (first, quotes) in pages.items():
        snippet = " … ".join(quotes)
        # The same site often serves one snippet under several URLs: link it once.
        if (first.source_name, snippet) in shown:
            continue
        shown.add((first.source_name, snippet))
        sources.append(LocalitySource(title=first.title, url=url, snippet=snippet, source=first.source_name))
    return sources
