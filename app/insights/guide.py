"""A locality guide: schools, hospitals and connectivity, as web pages list them.

Search results for "schools in Vijay Nagar, Indore" are mostly directory pages
listing names - "Best Schools in Vijay Nagar, Indore · SICA Senior Secondary
School · Podar International School" - and distance pages - "Indore Airport to
Vijay Nagar distance is 10 Km".

The model picks places out of the snippets. Code accepts one only if:

- the snippet comes from a directory or information page, not social media;
- the quote appears word for word in a result naming the locality and city;
- the place's name is in the quote and reads as what was asked - a school, a
  hospital, a station or airport - and a school isn't a coaching or driving class;
- a distance, when given, is in the quote, and code reads and converts it to km.
  A connectivity entry without a distance is dropped: "near the airport" says
  nothing checkable.

Each place carries its quote and source. These are places pages list for the
area, not recommendations or rankings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.insights.models import LocalitySource
from app.insights.websearch import LocalitySearch

logger = logging.getLogger(__name__)

Topic = Literal["schools", "hospitals", "connectivity"]
TOPICS: tuple[Topic, ...] = ("schools", "hospitals", "connectivity")

GUIDE_RESULTS = 6
MAX_PLACES_PER_TOPIC = 5
MAX_DISTANCE_KM = 60
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 512

GUIDE_QUERIES: dict[str, str] = {
    "schools": "best schools in {locality} {city}",
    "hospitals": "hospitals in {locality} {city}",
    "connectivity": "{locality} {city} distance from railway station airport",
}
TOPIC_ASK: dict[str, str] = {
    "schools": "schools, including preschools - not coaching, tuition, driving, dance or music classes",
    "hospitals": "hospitals, clinics and medical centres",
    "connectivity": (
        "railway stations, airports, metro stations and bus stands, each with the distance the "
        "snippet gives from the area; skip any without a distance"
    ),
}
# Posts and comments, not listings: anyone can write anything there.
UNTRUSTED_HOSTS = (
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "quora.com",
    "youtube.com",
    "x.com",
    "twitter.com",
)

_TOPIC_NAMES: dict[str, re.Pattern[str]] = {
    "schools": re.compile(r"\b(?:schools?|vidyalaya|vidya|academy|niketan|convent|preschool|gurukul)\b"),
    "hospitals": re.compile(r"\b(?:hospitals?|clinics?|medical|healthcare|centre|center|nursing home)\b"),
    "connectivity": re.compile(
        r"\b(?:airport|station|railway|junction|metro|bus stand|isbt|terminal|halt)\b"
    ),
}
_NOT_A_SCHOOL = re.compile(r"\b(?:driving|coaching|tuition|classes|dance|music|yoga|swimming)\b")
_DISTANCE = re.compile(
    r"(?:about |around |approx\.? |approximately )?(\d+(?:\.\d+)?)\s*"
    r"(kms?|kilomet(?:er|re)s?|miles?|mi|met(?:er|re)s?|mtrs?|m)\.?"
)

EXTRACTION_PROMPT = """\
You read search result snippets about one Indian locality and list the places \
they name of the kind asked for.

Reply with JSON and nothing else:
{"places": [{"name": "<as written>", "distance": "<as written, or null>", "quote": "<words copied from one snippet>", "source": <snippet number>}]}
or
{"places": []}

Rules:
- `name` is copied exactly as the snippet writes it.
- `distance` is copied exactly with its unit ("7.4 miles", "700 metres"), or null \
when the snippet gives none. When a snippet gives a straight-line and a road \
distance, use the road distance. Never estimate.
- `quote` is copied word for word from one snippet, and contains the name and \
the distance.
- Only places the snippet puts in or near the area asked for, in that city.
- `source` is the number of the snippet the quote came from.
- If no snippet names such a place, reply {"places": []}. Never use anything \
outside these snippets.
"""


@dataclass(frozen=True, slots=True)
class LocalityPlace:
    """A place a web page lists for the locality."""

    topic: Topic
    name: str
    distance_km: float | None
    quote: str
    title: str
    source_name: str | None
    source_url: str


class LocalityGuideFinder:
    def __init__(self, search: LocalitySearch, chat_model: BaseChatModel, *, enabled: bool = False) -> None:
        self._search = search
        self._model = chat_model
        self._enabled = enabled
        self._cache: dict[str, tuple[float, list[LocalityPlace]]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._search.enabled

    async def find(
        self, locality: str, city: str, topics: Sequence[Topic] = TOPICS
    ) -> dict[Topic, list[LocalityPlace]] | None:
        """Verified places per topic: [] for a topic with none, None when the guide is off."""
        if not self.enabled:
            return None
        wanted = list(dict.fromkeys(topics)) or list(TOPICS)
        found = await asyncio.gather(*(self._topic(locality, city, topic) for topic in wanted))
        return dict(zip(wanted, found, strict=True))

    async def _topic(self, locality: str, city: str, topic: Topic) -> list[LocalityPlace]:
        key = f"{locality}|{city}|{topic}".casefold()
        entry = self._cache.get(key)
        if entry is not None and time.monotonic() - entry[0] <= CACHE_TTL_SECONDS:
            return entry[1]

        query = GUIDE_QUERIES[topic].format(locality=locality, city=city)
        sources = [
            source
            for source in await self._search.sources(locality, city, query=query)
            if source.snippet and not _untrusted(source.url)
        ]
        places = await self._extract(locality, city, topic, sources) if sources else []

        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), places)
        return places

    async def _extract(
        self, locality: str, city: str, topic: Topic, sources: list[LocalitySource]
    ) -> list[LocalityPlace]:
        listing = "\n".join(
            f"[{index}] {source.title} — {source.snippet}" for index, source in enumerate(sources)
        )
        try:
            reply = await self._model.ainvoke(
                [
                    SystemMessage(content=EXTRACTION_PROMPT),
                    HumanMessage(
                        content=f"Area: {locality}\nCity: {city}\nLooking for: {TOPIC_ASK[topic]}\n\n{listing}"
                    ),
                ]
            )
            claims = _parse(reply.content if isinstance(reply.content, str) else str(reply.content))
        except Exception as exc:  # noqa: BLE001 - a lookup that fails reports no places
            logger.warning("Locality guide extraction failed for %s, %s (%s): %s", locality, city, topic, exc)
            return []

        places: list[LocalityPlace] = []
        seen: set[str] = set()
        for claim in claims:
            place = _verify(claim, locality=locality, city=city, topic=topic, sources=sources)
            if place is None or place.name.casefold() in seen:
                continue
            seen.add(place.name.casefold())
            places.append(place)
            if len(places) >= MAX_PLACES_PER_TOPIC:
                break
        return places


@dataclass(frozen=True, slots=True)
class _Claim:
    name: str
    distance: str | None
    quote: str
    source: int


def _parse(reply: str) -> list[_Claim]:
    """The model's candidate places; [] for anything that isn't the expected JSON."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    items = (payload.get("places") or []) if isinstance(payload, dict) else []

    claims: list[_Claim] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            distance = item.get("distance")
            claims.append(
                _Claim(
                    name=str(item["name"]).strip(),
                    distance=str(distance).strip() or None if distance is not None else None,
                    quote=str(item["quote"]).strip(),
                    source=int(item["source"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return claims


def _verify(
    claim: _Claim, *, locality: str, city: str, topic: Topic, sources: list[LocalitySource]
) -> LocalityPlace | None:
    """Accept a candidate place only if the snippet supports every part of it."""
    if not 0 <= claim.source < len(sources):
        return None
    source = sources[claim.source]
    snippet = _flatten(source.snippet or "")
    title = _flatten(source.title)
    quote = _flatten(claim.quote)
    name = _flatten(claim.name)

    if quote and (quote in snippet or quote in title):
        verbatim = claim.quote
    elif topic != "connectivity" and name and (name in snippet or name in title):
        # Directory snippets list names between "·" separators, and the model
        # often stitches its own quote from them. The name is still checked
        # against the page; the snippet itself becomes the quote. A distance
        # always needs the model's quote to be verbatim.
        verbatim = source.snippet if name in snippet else source.title
        quote = _flatten(verbatim or "")
    else:
        return _rejected(locality, topic, "the quote is not in the snippet")
    text = _compact(f"{source.title} {source.snippet or ''}")
    if _compact(locality) not in text or _compact(city) not in text:
        return _rejected(locality, topic, "the snippet is about somewhere else")
    if not 3 <= len(name) <= 80 or name not in quote:
        return _rejected(locality, topic, "the name is not in the quote")
    if not _TOPIC_NAMES[topic].search(name):
        return _rejected(locality, topic, f"{claim.name!r} doesn't read as {topic}")
    if topic == "schools" and _NOT_A_SCHOOL.search(name):
        return _rejected(locality, topic, f"{claim.name!r} is a class, not a school")
    if _compact(name) in {_compact(locality), _compact(city)}:
        return _rejected(locality, topic, "the name is just the area")

    distance_km: float | None = None
    if claim.distance is not None:
        if _flatten(claim.distance) not in quote:
            return _rejected(locality, topic, "the distance is not in the quote")
        distance_km = _km(claim.distance)
        if distance_km is None or not 0 < distance_km <= MAX_DISTANCE_KM:
            return _rejected(locality, topic, "the distance can't be read or is implausible")
    elif topic == "connectivity":
        return _rejected(locality, topic, "it has no distance")

    return LocalityPlace(
        topic=topic,
        name=claim.name,
        distance_km=distance_km,
        quote=verbatim,
        title=source.title,
        source_name=source.source,
        source_url=source.url,
    )


def _rejected(locality: str, topic: str, why: str) -> None:
    logger.warning("Discarding a %s entry for %s: %s", topic, locality, why)
    return None


def _km(text: str) -> float | None:
    """'7.4 miles' -> 11.9, '700 metres' -> 0.7, '10 Km' -> 10.0; None for anything vaguer."""
    match = _DISTANCE.fullmatch(_flatten(text))
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2)
    if unit.startswith("k"):
        factor = 1.0
    elif unit.startswith("mi"):
        factor = 1.609344
    else:
        factor = 0.001
    return round(value * factor, 1)


def _untrusted(url: str) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in UNTRUSTED_HOSTS)


def _flatten(text: str) -> str:
    return " ".join(text.split()).casefold()


def _compact(text: str) -> str:
    """Letters and digits only, so 'Vijaynagar' matches 'Vijay Nagar'."""
    return re.sub(r"[^0-9a-z]", "", text.casefold())
