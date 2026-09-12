"""Finding the listings a property should be judged against.

A comparable is another live listing of the same kind, near enough and
similar enough that its price says something about this one: same city,
same transaction type, same bedroom count, similar size, within a few
kilometres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from app.insights.geo import distance_km
from app.properties import PropertiesClient, PropertyCard, PropertyQuery

logger = logging.getLogger(__name__)

# Listings fetched per request while collecting comparables, and how many
# pages to walk before settling for what we have.
PAGE_SIZE = 100
MAX_PAGES = 5


@dataclass(frozen=True, slots=True)
class Comparable:
    """A listing being compared, with how close a match it is."""

    card: PropertyCard
    distance_km: float | None
    similarity: float  # 0-1; higher is a closer match

    @property
    def price_per_sqft(self) -> float | None:
        if self.card.price_per_sqft:
            return float(self.card.price_per_sqft)
        if self.card.price and self.card.size:
            return float(self.card.price) / float(self.card.size)
        return None


@dataclass(frozen=True, slots=True)
class Scope:
    """One way of answering "compared with what?", from narrow to broad."""

    key: str
    label: str
    cards: list[PropertyCard]


def room_count(value: str | None) -> int | None:
    """'3' -> 3, '7+' -> 7, anything else -> None."""
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else None


class ComparablesFinder:
    def __init__(
        self,
        client: PropertiesClient,
        *,
        radius_km: float = 3.0,
        area_tolerance: float = 0.25,
        max_comparables: int = 30,
    ) -> None:
        self._client = client
        self._radius_km = radius_km
        self._area_tolerance = area_tolerance
        self._max_comparables = max_comparables

    async def candidates(self, subject: PropertyCard, *, transaction_type: str) -> list[PropertyCard]:
        """Every live listing of this kind in the city, except the subject.

        Fetched once and reused: the narrow set of comparables and the wider
        price scopes are both filtered from it, so widening the comparison
        costs no extra requests.
        """
        query = PropertyQuery(
            city=subject.city,
            transaction_type="Rent" if transaction_type == "Rent" else "Sale",
        )
        return [card for card in await self._fetch(query) if card.id != subject.id]

    async def find(self, subject: PropertyCard, *, transaction_type: str) -> list[Comparable]:
        """Comparable listings for `subject`, closest match first."""
        candidates = await self.candidates(subject, transaction_type=transaction_type)
        return self.narrow(subject, candidates)

    def scopes(self, subject: PropertyCard, candidates: list[PropertyCard]) -> list[Scope]:
        """Ways to compare this listing's price, narrowest first.

        A narrow scope says the most but often holds too few listings, so the
        snapshot falls back through these until one has enough data.
        """
        bedrooms = room_count(subject.bedrooms)
        homes = f"{bedrooms} BHK homes" if bedrooms else "homes"
        locality = subject.locality or "this locality"
        city = subject.city or "this city"

        same_bedrooms = [card for card in candidates if room_count(card.bedrooms) == bedrooms]
        here = [card for card in candidates if _same_locality(subject, card)]
        candidate_scopes = [
            Scope(
                "similar_nearby",
                f"similar {homes} within {self._radius_km:g} km",
                [comparable.card for comparable in self.narrow(subject, candidates)],
            ),
            Scope(
                "bedrooms_locality",
                f"{homes} in {locality}",
                [card for card in same_bedrooms if _same_locality(subject, card)],
            ),
            Scope("locality", f"all homes in {locality}", here),
            Scope("bedrooms_city", f"{homes} in {city}", same_bedrooms),
            Scope("city", f"all homes in {city}", candidates),
        ]

        # Drop empty scopes, and any that hold exactly what a narrower one did.
        scopes: list[Scope] = []
        seen: list[set[str]] = []
        for scope in candidate_scopes:
            listing_ids = {card.id for card in scope.cards}
            if listing_ids and listing_ids not in seen:
                scopes.append(scope)
                seen.append(listing_ids)
        return scopes

    def narrow(self, subject: PropertyCard, candidates: list[PropertyCard]) -> list[Comparable]:
        """The closest matches: same bedrooms, similar size, within the radius."""
        bedrooms = room_count(subject.bedrooms)
        comparables: list[Comparable] = []
        for card in candidates:
            if card.id == subject.id or room_count(card.bedrooms) != bedrooms:
                continue
            distance = _distance(subject, card)
            if distance is not None and distance > self._radius_km:
                continue
            if distance is None and not _same_locality(subject, card):
                # No coordinates on one of them: fall back to the locality
                # name rather than comparing across a whole city.
                continue
            if not self._area_matches(subject, card):
                continue
            comparables.append(
                Comparable(
                    card=card, distance_km=distance, similarity=self._similarity(subject, card, distance)
                )
            )

        comparables.sort(key=lambda comparable: comparable.similarity, reverse=True)
        return comparables[: self._max_comparables]

    async def _fetch(self, query: PropertyQuery) -> list[PropertyCard]:
        """Every listing matching `query`, up to MAX_PAGES pages."""
        cards: list[PropertyCard] = []
        page = 1
        while page <= MAX_PAGES:
            result = await self._client.search(replace(query, page=page), limit=PAGE_SIZE)
            cards.extend(result.cards)
            if not result.cards or page >= (result.total_pages or 1):
                break
            page += 1
        return cards

    def _area_matches(self, subject: PropertyCard, card: PropertyCard) -> bool:
        if not subject.size or not card.size:
            return True  # unknown size shouldn't exclude an otherwise good match
        ratio = float(card.size) / float(subject.size)
        return 1 - self._area_tolerance <= ratio <= 1 + self._area_tolerance

    def _similarity(self, subject: PropertyCard, card: PropertyCard, distance: float | None) -> float:
        """How close a match, from nearness, size, furnishing and locality."""
        scores: list[tuple[float, float]] = []  # (weight, score)

        if distance is not None:
            scores.append((0.4, max(0.0, 1 - distance / self._radius_km)))
        elif _same_locality(subject, card):
            scores.append((0.4, 0.6))

        if subject.size and card.size:
            difference = abs(float(card.size) - float(subject.size)) / float(subject.size)
            scores.append((0.3, max(0.0, 1 - difference / self._area_tolerance)))

        if subject.furnishing and card.furnishing:
            scores.append((0.2, 1.0 if subject.furnishing == card.furnishing else 0.3))

        scores.append((0.1, 1.0 if _same_locality(subject, card) else 0.0))

        total_weight = sum(weight for weight, _ in scores)
        return sum(weight * score for weight, score in scores) / total_weight if total_weight else 0.0


def _distance(subject: PropertyCard, card: PropertyCard) -> float | None:
    if None in (subject.latitude, subject.longitude, card.latitude, card.longitude):
        return None
    return distance_km(subject.latitude, subject.longitude, card.latitude, card.longitude)  # type: ignore[arg-type]


def _same_locality(subject: PropertyCard, card: PropertyCard) -> bool:
    if not subject.locality or not card.locality:
        return False
    return subject.locality.strip().casefold() == card.locality.strip().casefold()
