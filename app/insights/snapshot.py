"""Building the Property Snapshot from comparable listings.

Every figure here is derived from DigiNiwas listings and can be traced back
through the `calculation` steps. Where the data doesn't support a figure,
the snapshot says so instead of estimating one.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from app.insights.comparables import Comparable, ComparablesFinder, Scope, room_count
from app.insights.models import (
    CalculationStep,
    ConfidenceFactor,
    ConfidenceLevel,
    DataConfidence,
    LocalityTrend,
    PriceComparison,
    PropertySnapshot,
    RentalYield,
    ScopeComparison,
)
from app.insights.rent import RentEstimate, WebRentEstimator, observation_weight
from app.insights.stats import (
    SMALL_SAMPLE_SPREAD,
    median,
    weighted_middle_range,
    weighted_quantile,
    without_outliers,
)
from app.insights.trend import WebTrendEstimator
from app.insights.websearch import LocalitySearch
from app.properties import PropertiesClient, PropertyCard, PropertyQuery, format_inr

logger = logging.getLogger(__name__)

# A price within this much of the median counts as "in line with" it.
IN_LINE_PERCENT = 1.0
# Data confidence: each figure's own level scores High 3, Medium 2, Low 1, and
# the card shows the average weighted like this.
CONFIDENCE_WEIGHTS = {"price_comparison": 0.5, "rental_yield": 0.3, "locality_trend": 0.2}
CONFIDENCE_SCORES: dict[str, int] = {"High": 3, "Medium": 2, "Low": 1}
HIGH_CONFIDENCE_SCORE = 2.5
MEDIUM_CONFIDENCE_SCORE = 1.75
# Price comparison confidence: listings needed in a close scope - similar homes
# nearby, or the same BHK in the locality. A locality-wide set needs more for
# Medium and never earns High; a city-wide set is always Low.
CLOSE_PRICE_SCOPES = ("similar_nearby", "bedrooms_locality")
HIGH_PRICE_CONFIDENCE_SAMPLE = 10
MEDIUM_PRICE_CONFIDENCE_SAMPLE = 3
# Below this many comparable rentals, a yield figure would be guesswork.
MIN_RENT_SAMPLE = 3
# Comparable rentals at which a listings-based yield earns more confidence.
HIGH_RENT_CONFIDENCE_SAMPLE = 10
MEDIUM_RENT_CONFIDENCE_SAMPLE = 5
# A web-quoted rent is discarded if it implies a gross yield outside this range:
# Indian residential yields sit around 2-5%, so anything far beyond is a misread.
MIN_WEB_YIELD_PERCENT = 1.0
MAX_WEB_YIELD_PERCENT = 12.0
# Listings a price scope needs before the headline prefers it over a wider one.
MIN_PRICE_SAMPLE = 3

DISCLAIMER = (
    "Estimates are based on available listings and locality data. Actual rent and property value may vary."
)
TREND_REASON = (
    "Locality price history is not recorded yet, so a yearly trend cannot be "
    "measured from DigiNiwas listings."
)


class SnapshotService:
    def __init__(
        self,
        client: PropertiesClient,
        finder: ComparablesFinder,
        *,
        search: LocalitySearch | None = None,
        trend: WebTrendEstimator | None = None,
        web_rent: WebRentEstimator | None = None,
        vacancy_months: float = 1.0,
        radius_km: float = 3.0,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._client = client
        self._finder = finder
        self._search = search
        self._trend = trend
        self._web_rent = web_rent
        self._vacancy_months = vacancy_months
        self._radius_km = radius_km
        self._today = today

    async def for_listing(self, property_id: str) -> PropertySnapshot | None:
        """The snapshot for one listing, or None if there is no such listing."""
        subject = await self._find_listing(property_id)
        if subject is None:
            return None

        is_rental = subject.transaction_type == "Rent"
        candidates = await self._finder.candidates(
            subject, transaction_type=subject.transaction_type or "Sale"
        )
        peers = self._finder.narrow(subject, candidates)
        scopes = self._finder.scopes(subject, candidates)
        rentals = peers if is_rental else await self._finder.find(subject, transaction_type="Rent")

        comparison = _price_comparison(subject, scopes)
        rental_yield = await self._rental_yield(subject, rentals, is_rental=is_rental)
        trend = await self._locality_trend(subject)
        confidence = _confidence(subject, comparison, rental_yield, trend, is_rental=is_rental)

        return PropertySnapshot(
            property_id=subject.id,
            title=subject.title,
            locality=subject.locality,
            city=subject.city,
            price=subject.price,
            price_label=subject.price_label,
            transaction_type=subject.transaction_type,
            price_comparison=comparison,
            rental_yield=rental_yield,
            locality_trend=trend,
            data_confidence=confidence,
            calculation=_calculation(
                subject, peers, rentals, comparison, rental_yield, trend, confidence, self._radius_km
            ),
            locality_sources=await self._sources(subject),
            radius_km=self._radius_km,
            updated_on=self._today(),
            disclaimer=DISCLAIMER,
        )

    async def _find_listing(self, property_id: str) -> PropertyCard | None:
        page = await self._client.search(PropertyQuery(search=property_id), limit=10)
        wanted = property_id.strip().casefold()
        return next((card for card in page.cards if card.id.casefold() == wanted), None)

    async def _sources(self, subject: PropertyCard):
        if self._search is None:
            return []
        return await self._search.sources(subject.locality, subject.city)

    async def _rental_yield(
        self, subject: PropertyCard, rentals: list[Comparable], *, is_rental: bool
    ) -> RentalYield:
        """From comparable rentals when there are enough; else a quoted locality average."""
        if is_rental:
            return RentalYield(
                available=False,
                headline="Not applicable",
                sample_size=0,
                basis="Rental yield applies to a property you buy, not to a rental listing.",
            )
        price = float(subject.price) if subject.price else None
        if not price:
            return RentalYield(
                available=False, headline="Not enough data", sample_size=0, basis="This listing has no price."
            )

        observations = _rent_observations(rentals, self._today())
        if len(observations) >= MIN_RENT_SAMPLE:
            return _yield_from_listings(subject, price, observations, self._vacancy_months, self._radius_km)

        # Searched only now: a snapshot with enough rentals of its own costs
        # no search query.
        estimate = None
        if self._web_rent is not None:
            estimate = await self._web_rent.estimate(
                subject.locality, subject.city, room_count(subject.bedrooms)
            )
        if estimate is not None:
            gross = estimate.monthly_rent * 12 / price * 100
            if MIN_WEB_YIELD_PERCENT <= gross <= MAX_WEB_YIELD_PERCENT:
                return _yield_from_web(subject, price, estimate, self._vacancy_months)
            logger.warning("Discarding quoted rent for %s: implies a %.1f%% yield", subject.id, gross)

        return RentalYield(
            available=False,
            headline="Not enough data",
            sample_size=len(observations),
            basis=(
                f"Needs at least {MIN_RENT_SAMPLE} comparable rentals listed in the last "
                f"3 years; found {len(observations)}."
            ),
        )

    async def _locality_trend(self, subject: PropertyCard) -> LocalityTrend:
        """A trend quoted from the web, or an honest "not available yet"."""
        estimate = None
        if self._trend is not None:
            estimate = await self._trend.estimate(subject.locality, subject.city)
        if estimate is None:
            return LocalityTrend(
                available=False,
                headline="Not available yet",
                caption="Needs price history",
                reason=TREND_REASON,
            )

        sign = "+" if estimate.yearly_percent >= 0 else "−"
        return LocalityTrend(
            available=True,
            yearly_percent=estimate.yearly_percent,
            total_percent=estimate.total_percent,
            years=estimate.years,
            headline=f"{sign}{abs(estimate.yearly_percent):.1f}% yearly",
            caption=estimate.period,
            source="web",
            source_name=estimate.source_name,
            source_url=estimate.source_url,
            quote=estimate.quote,
            period=estimate.period,
            confidence="Low",
        )


def _price_comparison(subject: PropertyCard, scopes: list[Scope]) -> PriceComparison:
    """Compare against every scope, and lead with the narrowest useful one."""
    subject_rate = _price_per_sqft(subject)
    breakdown = [_compare_with(subject_rate, scope) for scope in scopes]
    chosen = _narrowest_useful(breakdown)

    if subject_rate is None or chosen is None:
        return PriceComparison(
            difference_percent=None,
            direction="unknown",
            headline="Not enough data",
            caption="no comparable listings yet",
            listing_price_per_sqft=_round(subject_rate),
            median_price_per_sqft=None,
            sample_size=0,
            basis="No other live listing in this city was close enough to compare with.",
            breakdown=breakdown,
            confidence="Low",
        )

    return PriceComparison(
        difference_percent=chosen.difference_percent,
        direction=chosen.direction,
        headline=_headline(chosen.difference_percent),
        caption=chosen.label,
        listing_price_per_sqft=_round(subject_rate),
        median_price_per_sqft=chosen.median_price_per_sqft,
        sample_size=chosen.sample_size,
        basis=(
            f"{_per_sqft(subject_rate)} per sqft against a median of "
            f"{_per_sqft(chosen.median_price_per_sqft)} across "
            f"{_listings(chosen.sample_size)} ({chosen.label})."
        ),
        breakdown=breakdown,
        confidence=_price_confidence(chosen),
    )


def _price_confidence(chosen: ScopeComparison) -> ConfidenceLevel:
    """Many listings close to this one are strong evidence; a city-wide set is weak."""
    if chosen.scope in CLOSE_PRICE_SCOPES:
        if chosen.sample_size >= HIGH_PRICE_CONFIDENCE_SAMPLE:
            return "High"
        if chosen.sample_size >= MEDIUM_PRICE_CONFIDENCE_SAMPLE:
            return "Medium"
        return "Low"
    if chosen.scope == "locality" and chosen.sample_size >= HIGH_PRICE_CONFIDENCE_SAMPLE:
        return "Medium"
    return "Low"


def _compare_with(subject_rate: float | None, scope: Scope) -> ScopeComparison:
    rates = without_outliers([rate for card in scope.cards if (rate := _price_per_sqft(card))])
    market_rate = median(rates)

    if subject_rate is None or not market_rate:
        return ScopeComparison(
            scope=scope.key,
            label=scope.label,
            median_price_per_sqft=_round(market_rate),
            difference_percent=None,
            direction="unknown",
            sample_size=len(rates),
        )

    difference = (subject_rate - market_rate) / market_rate * 100
    return ScopeComparison(
        scope=scope.key,
        label=scope.label,
        median_price_per_sqft=_round(market_rate),
        difference_percent=round(difference, 1),
        direction=_direction(difference),
        sample_size=len(rates),
    )


def _narrowest_useful(breakdown: list[ScopeComparison]) -> ScopeComparison | None:
    """The narrowest scope with enough listings; otherwise the narrowest at all.

    Scopes arrive narrowest first, so "similar homes within 3 km" wins when it
    has the listings, and a wider set carries the headline when it doesn't.
    """
    usable = [scope for scope in breakdown if scope.difference_percent is not None]
    enough = [scope for scope in usable if scope.sample_size >= MIN_PRICE_SAMPLE]
    return next(iter(enough or usable), None)


def _direction(difference: float) -> str:
    if abs(difference) < IN_LINE_PERCENT:
        return "in line"
    return "below" if difference < 0 else "above"


def _headline(difference: float | None) -> str:
    if difference is None:
        return "Not enough data"
    if abs(difference) < IN_LINE_PERCENT:
        return "In line with"
    return f"{abs(difference):.0f}% {'below' if difference < 0 else 'above'}"


def _rent_observations(rentals: list[Comparable], today: date) -> list[tuple[float, float]]:
    """(monthly rent, weight) for each usable comparable rental, outliers removed."""
    pairs = [
        (float(peer.card.price), observation_weight(peer.similarity, peer.card.listed_on, today))
        for peer in rentals
        if peer.card.price
    ]
    pairs = [(rent, weight) for rent, weight in pairs if weight > 0]
    kept = without_outliers([rent for rent, _ in pairs])
    if not kept:
        return []
    # The outlier fence keeps a contiguous band of values, so its ends define it.
    low, high = min(kept), max(kept)
    return [(rent, weight) for rent, weight in pairs if low <= rent <= high]


def _yield_from_listings(
    subject: PropertyCard,
    price: float,
    observations: list[tuple[float, float]],
    vacancy_months: float,
    radius_km: float,
) -> RentalYield:
    rents = [rent for rent, _ in observations]
    weights = [weight for _, weight in observations]
    typical = weighted_quantile(rents, weights, 0.5) or rents[0]
    low_rent, high_rent = weighted_middle_range(rents, weights) or (typical, typical)
    net, expenses, assumptions = _net_yield(subject, price, typical, vacancy_months)
    bedrooms = room_count(subject.bedrooms)
    count = len(observations)

    return RentalYield(
        available=True,
        headline=f"{low_rent * 12 / price * 100:.1f}%–{high_rent * 12 / price * 100:.1f}%",
        low_percent=round(low_rent * 12 / price * 100, 1),
        high_percent=round(high_rent * 12 / price * 100, 1),
        gross_percent=round(typical * 12 / price * 100, 1),
        net_percent=net,
        estimated_monthly_rent=round(typical),
        estimated_annual_rent=round(typical * 12),
        annual_expenses=expenses,
        assumptions=assumptions,
        sample_size=count,
        rent_scope=f"similar {f'{bedrooms} BHK ' if bedrooms else ''}rentals within {radius_km:g} km",
        basis=(
            f"Weighted median rent of {count} comparable rental{'' if count == 1 else 's'} "
            f"({format_inr(typical)}/month); closer and more recently listed ones count for more."
        ),
        source="listings",
        confidence=_rent_confidence(count),
    )


def _yield_from_web(
    subject: PropertyCard, price: float, estimate: RentEstimate, vacancy_months: float
) -> RentalYield:
    rent = float(estimate.monthly_rent)
    # One quoted figure, so never shown as more precise than ±10%.
    low_rent, high_rent = rent * (1 - SMALL_SAMPLE_SPREAD), rent * (1 + SMALL_SAMPLE_SPREAD)
    net, expenses, assumptions = _net_yield(subject, price, rent, vacancy_months)
    locality = subject.locality or subject.city or "this locality"
    if estimate.bedrooms:
        scope = f"{estimate.bedrooms} BHK homes in {locality}"
    else:
        scope = f"all property types in {locality}"
        assumptions.append(
            "The rent is an average over all property types, so a larger home may rent for more."
        )

    return RentalYield(
        available=True,
        headline=f"{low_rent * 12 / price * 100:.1f}%–{high_rent * 12 / price * 100:.1f}%",
        low_percent=round(low_rent * 12 / price * 100, 1),
        high_percent=round(high_rent * 12 / price * 100, 1),
        gross_percent=round(rent * 12 / price * 100, 1),
        net_percent=net,
        estimated_monthly_rent=round(rent),
        estimated_annual_rent=round(rent * 12),
        annual_expenses=expenses,
        assumptions=assumptions,
        sample_size=0,
        rent_scope=scope,
        basis=(
            f"Too few comparable rentals on DigiNiwas, so this uses the average rent "
            f"{estimate.source_name or 'a web page'} publishes for {scope} "
            f"({format_inr(rent)}/month), shown ±10%."
        ),
        source="web",
        source_name=estimate.source_name,
        source_url=estimate.source_url,
        quote=estimate.quote,
        confidence="Low",
    )


def _net_yield(
    subject: PropertyCard, price: float, monthly_rent: float, vacancy_months: float
) -> tuple[float, int, list[str]]:
    """Net yield, the expenses deducted, and a plain statement of each assumption."""
    vacancy = monthly_rent * vacancy_months
    maintenance = float(subject.maintenance) * 12 if subject.maintenance else 0.0
    expenses = vacancy + maintenance
    net = (monthly_rent * 12 - expenses) / price * 100

    months = f"{vacancy_months:g} month{'' if vacancy_months == 1 else 's'}"
    assumptions = [f"{months} a year without a tenant ({format_inr(vacancy) or '₹0'} of rent)."]
    if subject.maintenance:
        assumptions.append(
            f"Maintenance of {format_inr(subject.maintenance)} a month, as listed, paid by the "
            f"owner ({format_inr(maintenance)} a year)."
        )
    else:
        assumptions.append("No maintenance charge is listed, so none is deducted.")
    assumptions.append(
        "Property tax, insurance and repairs are not deducted: the listing doesn't say what they are."
    )
    return round(net, 1), round(expenses), assumptions


def _rent_confidence(count: int) -> ConfidenceLevel:
    if count >= HIGH_RENT_CONFIDENCE_SAMPLE:
        return "High"
    if count >= MEDIUM_RENT_CONFIDENCE_SAMPLE:
        return "Medium"
    return "Low"


def _confidence(
    subject: PropertyCard,
    comparison: PriceComparison,
    rental_yield: RentalYield,
    trend: LocalityTrend,
    *,
    is_rental: bool,
) -> DataConfidence:
    """How far the card's figures can be trusted, from each figure's own confidence.

    Each figure scores High 3, Medium 2 or Low 1, averaged with CONFIDENCE_WEIGHTS:
    2.5 or more is High, 1.75 or more Medium, anything lower Low. A rental
    listing has no yield, so the other two weights are used alone.
    """
    factors = [_price_factor(comparison)]
    if not is_rental:
        factors.append(_yield_factor(rental_yield))
    factors.append(_trend_factor(trend))

    total_weight = sum(factor.weight for factor in factors)
    score = sum(CONFIDENCE_SCORES[factor.level] * factor.weight for factor in factors) / total_weight
    if score >= HIGH_CONFIDENCE_SCORE:
        level: ConfidenceLevel = "High"
    elif score >= MEDIUM_CONFIDENCE_SCORE:
        level = "Medium"
    else:
        level = "Low"

    reasons = [factor.reason for factor in factors]
    if subject.latitude is None or subject.longitude is None:
        reasons.append(
            "This listing has no coordinates, so nearby homes were matched by locality name, not distance."
        )
        if level == "High":
            level = "Medium"

    comparable = comparison.sample_size + rental_yield.sample_size
    return DataConfidence(
        level=level,
        headline=level,
        caption=_listings(comparable),
        comparable_listings=comparable,
        score=round(score, 2),
        factors=factors,
        reasons=reasons,
    )


def _price_factor(comparison: PriceComparison) -> ConfidenceFactor:
    if comparison.difference_percent is None:
        reason = "Price comparison: no comparable listings to compare with."
    else:
        reason = f"Price comparison: {_listings(comparison.sample_size)} ({comparison.caption})."
    return ConfidenceFactor(
        figure="price_comparison",
        level=comparison.confidence or "Low",
        weight=CONFIDENCE_WEIGHTS["price_comparison"],
        reason=reason,
    )


def _yield_factor(rental_yield: RentalYield) -> ConfidenceFactor:
    level: ConfidenceLevel = "Low"
    if not rental_yield.available:
        reason = "Rental yield: too few comparable rentals to estimate it."
    elif rental_yield.source == "web":
        reason = (
            "Rental yield: too few comparable rentals nearby, so it uses a locality average "
            f"quoted from the web ({rental_yield.source_name or 'a web page'})."
        )
    else:
        level = rental_yield.confidence or "Low"
        count = rental_yield.sample_size
        reason = f"Rental yield: {count} comparable rental{'' if count == 1 else 's'} nearby."
    return ConfidenceFactor(
        figure="rental_yield", level=level, weight=CONFIDENCE_WEIGHTS["rental_yield"], reason=reason
    )


def _trend_factor(trend: LocalityTrend) -> ConfidenceFactor:
    if not trend.available:
        level: ConfidenceLevel = "Low"
        reason = "Locality trend: not available yet."
    else:
        level = trend.confidence or "Low"
        reason = (
            f"Locality trend: quoted from {trend.source_name or 'a web page'}, "
            "not measured from DigiNiwas listings."
        )
    return ConfidenceFactor(
        figure="locality_trend", level=level, weight=CONFIDENCE_WEIGHTS["locality_trend"], reason=reason
    )


def _calculation(
    subject: PropertyCard,
    peers: list[Comparable],
    rentals: list[Comparable],
    comparison: PriceComparison,
    rental_yield: RentalYield,
    trend: LocalityTrend,
    confidence: DataConfidence,
    radius_km: float,
) -> list[CalculationStep]:
    bedrooms = room_count(subject.bedrooms)
    size = f"{subject.size:g} {subject.size_unit}" if subject.size else "unknown size"
    return [
        CalculationStep(
            title="Comparable listings",
            detail=(
                f"Live, verified listings within {radius_km:g} km of this one in "
                f"{subject.locality or subject.city or 'the same area'}, with the same "
                f"{f'{bedrooms} BHK' if bedrooms else 'configuration'} and a similar size "
                f"to {size}: {len(peers)} for sale, {len(rentals)} on rent."
            ),
        ),
        CalculationStep(
            title="Price comparison",
            detail=comparison.basis + _wider_comparisons(comparison),
        ),
        CalculationStep(title="Rental yield", detail=_yield_explanation(subject, rental_yield)),
        CalculationStep(
            title="Outliers",
            detail=(
                "Values far outside the middle half of the sample are dropped before "
                "the median is taken, so one mistyped listing cannot move the figures."
            ),
        ),
        CalculationStep(
            title="Locality trend",
            detail=(
                f'Quoted from {trend.source_name or "a web page"}: "{trend.quote}" '
                "This is a claim published on the web, not measured from DigiNiwas "
                "listings, so treat it as an indication only."
                if trend.available
                else TREND_REASON
            ),
        ),
        CalculationStep(
            title="Data confidence",
            detail=(
                "Each figure is rated High, Medium or Low by how much close, own-market data it "
                "rests on ("
                + ", ".join(
                    f"{factor.figure.replace('_', ' ')} {factor.level}" for factor in confidence.factors
                )
                + "). Scored 3, 2 and 1 and weighted 50% price comparison, 30% rental yield, "
                f"20% locality trend, they average {confidence.score} out of 3: 2.5 or more is "
                "High, 1.75 or more Medium, anything lower Low."
            ),
        ),
    ]


def _price_per_sqft(card: PropertyCard) -> float | None:
    if card.price_per_sqft:
        return float(card.price_per_sqft)
    if card.price and card.size:
        return float(card.price) / float(card.size)
    return None


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


def _listings(count: int) -> str:
    return f"{count} comparable listing{'' if count == 1 else 's'}"


def _per_sqft(value: float | None) -> str:
    # Plain rupees, not "₹5.15k": a rate per sqft is read as a whole number.
    return "unknown" if value is None else f"₹{value:,.0f}"


def _wider_comparisons(comparison: PriceComparison) -> str:
    """The other scopes, for the details screen."""
    others = [
        f"{scope.label}: {_per_sqft(scope.median_price_per_sqft)} per sqft "
        f"from {_listings(scope.sample_size)}"
        for scope in comparison.breakdown
        if scope.label != comparison.caption and scope.median_price_per_sqft
    ]
    return f" Also compared with {'; '.join(others)}." if others else ""


def _yield_explanation(subject: PropertyCard, rental_yield: RentalYield) -> str:
    """The rental yield working, for "See How This Was Calculated"."""
    if not rental_yield.available:
        return rental_yield.basis
    parts = [
        rental_yield.basis,
        f"Gross yield: annual rent {format_inr(rental_yield.estimated_annual_rent)} ÷ price "
        f"{format_inr(subject.price)} × 100 = {rental_yield.gross_percent}%.",
    ]
    if rental_yield.net_percent is not None:
        parts.append(
            f"Net yield: annual rent less {format_inr(rental_yield.annual_expenses)} of expenses, "
            f"÷ price × 100 = {rental_yield.net_percent}%."
        )
    if rental_yield.quote:
        parts.append(f'Quoted from {rental_yield.source_name or "a web page"}: "{rental_yield.quote}"')
    return " ".join(parts)
