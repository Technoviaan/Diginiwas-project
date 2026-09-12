"""The rent behind the estimated rental yield.

The question is "what could this home rent for today?", so rents come from
DigiNiwas' own comparable rentals, each counting for how close a match it is
and how recently it was listed:

    listed within 1 year    0.5
    1 to 2 years ago        0.3
    2 to 3 years ago        0.2
    older                   ignored

The listings API holds live listings only, not rent history, so "recent"
means recently *listed*.

When there are too few comparable rentals, a locality's average rent may be
quoted from the web instead. That is weaker evidence - usually an average over
every property type - so the trend's rules apply, enforced in code: the quote
must appear verbatim in a snippet that names the locality and the city and
says "average" or "median"; the amount must be in the quote and is read by
code, not by the model; and a single listing's rent is refused.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.insights.models import LocalitySource
from app.insights.websearch import LocalitySearch
from app.properties import parse_inr

logger = logging.getLogger(__name__)

# (listed at most this many years ago, weight). Anything older weighs nothing.
RECENCY_WEIGHTS: tuple[tuple[float, float], ...] = ((1.0, 0.5), (2.0, 0.3), (3.0, 0.2))
# A listing without a date still counts, but no more than the oldest bucket.
UNKNOWN_AGE_WEIGHT = 0.2
# Every comparable passed the radius and size filters, so none weighs nothing.
MIN_SIMILARITY_WEIGHT = 0.1

RENT_QUERY = "{locality} {city} {homes} average rent per month"
# A monthly rent outside this range is a misread snippet.
MIN_MONTHLY_RENT = 1_000
MAX_MONTHLY_RENT = 1_000_000
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRIES = 512
# "3 BHK", "3BHK", "3-BHK": the home size a quote names, if any.
_BHK = re.compile(r"(\d+)\s*-?\s*bhk", re.IGNORECASE)

EXTRACTION_PROMPT = """\
You read search result snippets about renting homes in one Indian locality and \
report the AVERAGE or MEDIAN monthly rent ONLY if a snippet states one plainly.

Reply with JSON and nothing else:
{"found": true, "amount": "Rs. 17,418", "quote": "...", "source": 2}
or
{"found": false}

Rules:
- Only an average or median rent for the locality counts. The rent of one \
property ("The rent is ₹6,000 per month", "expected rent is 51,000 monthly") \
is NOT an average: ignore it.
- `amount` is copied exactly as the snippet writes it, currency and commas \
included. Do no arithmetic and no conversion.
- `quote` is copied word for word from one snippet, and contains the amount.
- The snippet must be about the locality asked for, in the same city.
- `source` is the number of the snippet the quote came from.
- If no snippet states an average or median rent for this locality, answer not \
found. Never estimate, infer, or use anything outside these snippets.
"""


def recency_weight(listed_on: date | None, today: date) -> float:
    """How much a rental counts, by how long ago it was listed."""
    if listed_on is None:
        return UNKNOWN_AGE_WEIGHT
    age_years = max(0, (today - listed_on).days) / 365.25
    for max_age, weight in RECENCY_WEIGHTS:
        if age_years <= max_age:
            return weight
    return 0.0


def observation_weight(similarity: float, listed_on: date | None, today: date) -> float:
    """A comparable rental's weight: how close a match × how recently listed."""
    return max(similarity, MIN_SIMILARITY_WEIGHT) * recency_weight(listed_on, today)


@dataclass(frozen=True, slots=True)
class RentEstimate:
    """A locality's average monthly rent, quoted from a web page."""

    monthly_rent: int
    bedrooms: int | None  # None: an average over all property types
    quote: str
    source_name: str | None
    source_url: str


class WebRentEstimator:
    def __init__(self, search: LocalitySearch, chat_model: BaseChatModel, *, enabled: bool = False) -> None:
        self._search = search
        self._model = chat_model
        self._enabled = enabled
        self._cache: dict[str, tuple[float, RentEstimate | None]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and self._search.enabled

    async def estimate(
        self, locality: str | None, city: str | None, bedrooms: int | None
    ) -> RentEstimate | None:
        """An average rent quoted from the web, or None when nothing checks out."""
        if not self.enabled or not locality or not city:
            return None

        key = f"{locality}|{city}|{bedrooms}".casefold()
        entry = self._cache.get(key)
        if entry is not None and time.monotonic() - entry[0] <= CACHE_TTL_SECONDS:
            return entry[1]

        homes = f"{bedrooms} BHK" if bedrooms else ""
        query = " ".join(RENT_QUERY.format(locality=locality, city=city, homes=homes).split())
        sources = [
            source for source in await self._search.sources(locality, city, query=query) if source.snippet
        ]
        estimate = await self._extract(locality, city, bedrooms, sources) if sources else None

        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), estimate)
        return estimate

    async def _extract(
        self, locality: str, city: str, bedrooms: int | None, sources: list[LocalitySource]
    ) -> RentEstimate | None:
        listing = "\n".join(
            f"[{index}] {source.title} — {source.snippet}" for index, source in enumerate(sources)
        )
        try:
            reply = await self._model.ainvoke(
                [
                    SystemMessage(content=EXTRACTION_PROMPT),
                    # No home size here: the model copied it onto an average that
                    # covers every property type. Code reads it from the quote.
                    HumanMessage(content=f"Locality: {locality}\nCity: {city}\n\n{listing}"),
                ]
            )
            claim = _parse(reply.content if isinstance(reply.content, str) else str(reply.content))
        except Exception as exc:  # noqa: BLE001 - a quoted rent is optional
            logger.warning("Rent extraction failed for %s, %s: %s", locality, city, exc)
            return None

        if claim is None:
            return None
        return _verify(claim, locality=locality, city=city, bedrooms=bedrooms, sources=sources)


@dataclass(frozen=True, slots=True)
class _Claim:
    amount: str
    quote: str
    source: int


def _parse(reply: str) -> _Claim | None:
    """The model's JSON, or None if it said no or answered with anything else."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group())
        if not payload.get("found"):
            return None
        return _Claim(
            amount=str(payload["amount"]).strip(),
            quote=str(payload["quote"]).strip(),
            source=int(payload["source"]),
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _verify(
    claim: _Claim, *, locality: str, city: str, bedrooms: int | None, sources: list[LocalitySource]
) -> RentEstimate | None:
    """Accept the claim only if the snippet actually supports it."""
    if not 0 <= claim.source < len(sources):
        return None
    source = sources[claim.source]
    snippet = _flatten(f"{source.title} {source.snippet or ''}")
    quote = _flatten(claim.quote)

    if quote not in snippet:
        logger.warning("Discarding rent for %s: quote is not in the snippet", locality)
        return None
    if _flatten(locality) not in snippet or _flatten(city) not in snippet:
        logger.warning("Discarding rent for %s: snippet is about somewhere else", locality)
        return None
    if "average" not in quote and "median" not in quote:
        # One property's rent says nothing about the locality.
        logger.warning("Discarding rent for %s: not an average", locality)
        return None
    if _flatten(claim.amount) not in quote:
        logger.warning("Discarding rent for %s: amount is not in the quote", locality)
        return None
    # The home size comes from the quote itself, never from the model: an
    # average that names no BHK count covers every property type.
    stated_sizes = {int(count) for count in _BHK.findall(claim.quote)}
    if len(stated_sizes) > 1:
        logger.warning("Discarding rent for %s: the quote names several home sizes", locality)
        return None
    stated = stated_sizes.pop() if stated_sizes else None
    if stated is not None and bedrooms is not None and stated != bedrooms:
        logger.warning("Discarding rent for %s: it is for %s BHK homes", locality, stated)
        return None
    try:
        rent = parse_inr(claim.amount)
    except ValueError:
        return None
    if not MIN_MONTHLY_RENT <= rent <= MAX_MONTHLY_RENT:
        logger.warning("Discarding implausible rent %s for %s", rent, locality)
        return None

    return RentEstimate(
        monthly_rent=rent,
        bedrooms=stated,
        quote=claim.quote,
        source_name=source.source,
        source_url=source.url,
    )


def _flatten(text: str) -> str:
    return " ".join(text.split()).casefold()
