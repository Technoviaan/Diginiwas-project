"""Data confidence: each figure's own level, weighted into Low, Medium or High."""

from datetime import date

import pytest

from app.insights import ComparablesFinder, RentEstimate, SnapshotService
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, make_listing

pytestmark = pytest.mark.anyio

SUBJECT_ID = "DW-SUB"
PRICE = 6_000_000
# About 4 km from the subject, in another locality.
ELSEWHERE = {"locality": "Palasia", "latitude": 22.7200, "longitude": 75.8700}
NO_COORDINATES = {"latitude": None, "longitude": None}


class StubRent:
    def __init__(self, estimate: RentEstimate | None) -> None:
        self._estimate = estimate

    async def estimate(self, locality, city, bedrooms):
        return self._estimate


async def confidence(listings, **options):
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    service = SnapshotService(client, ComparablesFinder(client), today=lambda: date(2026, 9, 13), **options)
    return (await service.for_listing(SUBJECT_ID)).data_confidence


def subject(**extra) -> dict:
    return make_listing(SUBJECT_ID, price=PRICE, size=1100, **extra)


def sales(count: int, **extra) -> list[dict]:
    return [make_listing(f"DW-S{i}", price=6_100_000, size=1100, **extra) for i in range(count)]


def rentals(count: int, **extra) -> list[dict]:
    return [
        make_listing(f"DW-R{i}", transaction_type="Rent", price=25_000, size=1100, **extra)
        for i in range(count)
    ]


def levels(data_confidence) -> dict[str, str]:
    return {factor.figure: factor.level for factor in data_confidence.factors}


async def test_thin_data_is_low():
    # Like DW-1003 today: one comparable sale, no rentals, no trend.
    result = await confidence([subject(), *sales(1)])

    assert result.level == "Low"
    assert result.score == 1.0
    assert levels(result) == {"price_comparison": "Low", "rental_yield": "Low", "locality_trend": "Low"}
    assert result.caption == "1 comparable listing"


async def test_strong_prices_with_some_rentals_is_medium():
    # Price High (3 × 0.5) + yield Medium (2 × 0.3) + trend Low (1 × 0.2) = 2.3.
    result = await confidence([subject(), *sales(12), *rentals(6)])

    assert levels(result) == {"price_comparison": "High", "rental_yield": "Medium", "locality_trend": "Low"}
    assert (result.level, result.score) == ("Medium", 2.3)
    assert result.comparable_listings == 18


async def test_strong_prices_and_rentals_is_high_even_without_a_trend():
    # 3 × 0.5 + 3 × 0.3 + 1 × 0.2 = 2.6.
    result = await confidence([subject(), *sales(12), *rentals(12)])

    assert (result.level, result.score) == ("High", 2.6)


async def test_many_listings_count_for_little_when_they_are_city_wide():
    # Twelve sales, but none nearby or in the same locality: a city-wide comparison.
    result = await confidence([subject(), *sales(12, **ELSEWHERE), *rentals(12)])

    assert levels(result)["price_comparison"] == "Low"
    assert (result.level, result.score) == ("Low", 1.6)


async def test_a_web_quoted_rent_counts_as_low():
    quoted = RentEstimate(
        monthly_rent=25_000,
        bedrooms=None,
        quote="The average rent in Vijay Nagar is Rs. 25,000 per month",
        source_name="housing.com",
        source_url="https://housing.com/1",
    )
    result = await confidence([subject(), *sales(12)], web_rent=StubRent(quoted))

    assert levels(result)["rental_yield"] == "Low"
    assert (result.level, result.score) == ("Medium", 2.0)
    assert any("quoted from the web" in reason for reason in result.reasons)


async def test_no_coordinates_is_never_high():
    listings = [subject(**NO_COORDINATES), *sales(12, **NO_COORDINATES), *rentals(12, **NO_COORDINATES)]
    result = await confidence(listings)

    assert result.score == 2.6  # would be High
    assert result.level == "Medium"
    assert any("no coordinates" in reason for reason in result.reasons)


async def test_a_rental_listing_is_rated_without_a_yield():
    listings = [
        make_listing(SUBJECT_ID, transaction_type="Rent", price=25_000, size=1100),
        *rentals(12),
    ]
    result = await confidence(listings)

    # Price High (3 × 0.5) + trend Low (1 × 0.2), over the 0.7 of weight used: 2.43.
    assert [factor.figure for factor in result.factors] == ["price_comparison", "locality_trend"]
    assert (result.level, result.score) == ("Medium", 2.43)


async def test_the_rule_is_explained_on_the_details_screen():
    api = FakePropertiesAPI([subject(), *sales(12), *rentals(6)])
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    snapshot = await SnapshotService(client, ComparablesFinder(client)).for_listing(SUBJECT_ID)

    [step] = [step for step in snapshot.calculation if step.title == "Data confidence"]
    assert "price comparison High, rental yield Medium, locality trend Low" in step.detail
    assert "average 2.3 out of 3" in step.detail
