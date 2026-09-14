"""The `locality_guide` tool: schools, hospitals and connectivity near an area.

Answers questions like "is Rau good for families?" or "how far is Vijay Nagar
from the airport?" from places web pages list for the area, each checked by
LocalityGuideFinder. The model gets names, distances and the site listing
them; the app gets the pages as source links.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.insights.guide import TOPICS, LocalityGuideFinder, LocalityPlace, Topic
from app.insights.models import LocalitySource

TOOL_NAME = "locality_guide"


class LocalityGuideInput(BaseModel):
    """What to look up."""

    locality: str = Field(..., min_length=1, description="The area or locality the user named, e.g. 'Rau'.")
    city: str = Field(
        ..., min_length=1, description="The city the user named, e.g. 'Indore'. Ask for it rather than guess."
    )
    topics: list[Topic] = Field(
        default_factory=lambda: list(TOPICS),
        description=(
            "Only what the user asked about: 'schools', 'hospitals', 'connectivity' (stations, "
            "airport, distances). All three only when they asked about the area in general."
        ),
    )


@dataclass(frozen=True, slots=True)
class LocalityGuideArtifact:
    """What the app shows: the pages the places came from, as source links."""

    sources: list[LocalitySource]


def build_locality_guide_tool(finder: LocalityGuideFinder) -> BaseTool:
    async def locality_guide(**arguments: Any) -> tuple[str, LocalityGuideArtifact]:
        args = LocalityGuideInput(**arguments)
        topics = list(dict.fromkeys(args.topics)) or list(TOPICS)
        found = await finder.find(args.locality, args.city, topics)

        result: dict[str, Any] = {"area": f"{args.locality}, {args.city}"}
        if found is None:
            result["note"] = (
                "The locality guide is not available on this server. Say so; do not answer from memory."
            )
            return json.dumps(result, ensure_ascii=False), LocalityGuideArtifact(sources=[])

        for topic in topics:
            result[topic] = [_place_summary(place) for place in found.get(topic, [])]
        note = (
            "These are places web pages list for this area, not DigiNiwas recommendations or rankings. "
            "Mention only these, name the site that lists them, and give distances only as stated here. "
            "Do not call any of them good, best, top or notable."
        )
        if missing := [topic for topic in topics if not found.get(topic)]:
            note += (
                f" Nothing reliable was found for: {', '.join(missing)}. Say so; do not fill in from memory."
            )
        result["note"] = note

        places = [place for topic in topics for place in found.get(topic, [])]
        return json.dumps(result, ensure_ascii=False), LocalityGuideArtifact(sources=_sources(places))

    return StructuredTool.from_function(
        coroutine=locality_guide,
        name=TOOL_NAME,
        description=(
            "Look up what an area is like to live in: schools, hospitals, and connectivity "
            "(railway station, airport, metro and their distances), as web pages list them. "
            "Use it for questions about an area's amenities or how well connected it is."
        ),
        args_schema=LocalityGuideInput,
        response_format="content_and_artifact",
    )


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
