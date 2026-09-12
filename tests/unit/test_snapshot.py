"""The figures on the Property Snapshot card."""

import pytest

from app.insights import ComparablesFinder, SnapshotService
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, make_listing

pytestmark = pytest.mark.anyio

SUBJECT_ID = "DW-SUB"
SUBJECT_PRICE = 6_000_000  # 1100 sqft -> 5,455/sqft


def snapshots_for(listings) -> SnapshotService:
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return SnapshotService(client, ComparablesFinder(client), radius_km=3.0)


def sale(listing_id: str, price: int) -> dict:
    return make_listing(listing_id, price=price, size=1100)


def rental(listing_id: str, rent: int) -> dict:
    return make_listing(listing_id, transaction_type="Rent", price=rent, size=1100)


async def test_reports_a_cheaper_listing_as_below_the_market():
    # Subject 5,455/sqft against neighbours at about 6,364/sqft.
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE)] + [sale(f"DW-{i}", 7_000_000) for i in range(5)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)

    comparison = snapshot.price_comparison
    assert comparison.direction == "below"
    assert comparison.headline == "14% below"
    assert comparison.caption == "similar 2 BHK homes within 3 km"
    assert comparison.sample_size == 5
    assert comparison.listing_price_per_sqft == 5455
    assert comparison.median_price_per_sqft == 6364
    assert comparison.basis.startswith("₹5,455 per sqft against a median of ₹6,364")


async def test_widens_the_comparison_when_nothing_similar_is_nearby():
    # Same 2 BHK locality, but far larger homes: too different for the narrow
    # set, still worth comparing against.
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE)] + [
        make_listing(f"DW-BIG{index}", price=21_000_000, size=3000) for index in range(4)
    ]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)

    comparison = snapshot.price_comparison
    assert comparison.caption == "2 BHK homes in Vijay Nagar"
    assert comparison.headline == "22% below"
    assert comparison.sample_size == 4


async def test_lists_every_comparison_that_could_be_made():
    listings = (
        [sale(SUBJECT_ID, SUBJECT_PRICE)]
        + [sale(f"DW-{index}", 6_100_000) for index in range(3)]
        + [make_listing(f"DW-3B{index}", price=9_000_000, size=1600, bedrooms="3") for index in range(2)]
    )
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)

    breakdown = snapshot.price_comparison.breakdown
    # Narrowest first, and scopes holding the same listings are not repeated.
    assert [scope.scope for scope in breakdown] == ["similar_nearby", "locality"]
    assert [scope.sample_size for scope in breakdown] == [3, 5]
    assert snapshot.price_comparison.caption == "similar 2 BHK homes within 3 km"


async def test_reports_a_dearer_listing_as_above_the_market():
    listings = [sale(SUBJECT_ID, 8_000_000)] + [sale(f"DW-{i}", 6_000_000) for i in range(5)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)
    assert snapshot.price_comparison.direction == "above"
    assert snapshot.price_comparison.headline == "33% above"


async def test_reports_a_matching_price_as_in_line():
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE)] + [sale(f"DW-{i}", SUBJECT_PRICE) for i in range(5)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)
    assert snapshot.price_comparison.direction == "in line"


async def test_says_so_when_there_is_nothing_to_compare_against():
    snapshot = await snapshots_for([sale(SUBJECT_ID, SUBJECT_PRICE)]).for_listing(SUBJECT_ID)
    assert snapshot.price_comparison.direction == "unknown"
    assert snapshot.price_comparison.headline == "Not enough data"
    assert snapshot.price_comparison.sample_size == 0


async def test_estimates_rental_yield_from_comparable_rents():
    # 25,000 a month on a 60,00,000 property is 5% a year.
    rents = [24_000, 25_000, 25_000, 26_000, 25_500, 24_500]
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE)] + [
        rental(f"DW-R{index}", rent) for index, rent in enumerate(rents)
    ]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)

    estimate = snapshot.rental_yield
    assert estimate.available
    assert estimate.estimated_monthly_rent == pytest.approx(25_000, abs=300)
    assert estimate.estimated_annual_rent == estimate.estimated_monthly_rent * 12
    assert estimate.low_percent < 5.0 < estimate.high_percent
    assert estimate.headline.endswith("%") and "–" in estimate.headline
    assert estimate.sample_size == 6


async def test_will_not_estimate_yield_from_one_or_two_rentals():
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE), rental("DW-R1", 25_000)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)
    assert snapshot.rental_yield.available is False
    assert "at least 3" in snapshot.rental_yield.basis


async def test_yield_does_not_apply_to_a_rental_listing():
    listings = [rental(SUBJECT_ID, 25_000), rental("DW-R1", 24_000)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)
    assert snapshot.rental_yield.available is False
    assert snapshot.rental_yield.headline == "Not applicable"


async def test_locality_trend_is_always_unavailable_with_a_reason():
    snapshot = await snapshots_for([sale(SUBJECT_ID, SUBJECT_PRICE)]).for_listing(SUBJECT_ID)
    trend = snapshot.locality_trend
    assert trend.available is False
    assert trend.yearly_percent is None
    assert "price history" in trend.reason


async def test_explains_how_each_figure_was_worked_out():
    listings = [sale(SUBJECT_ID, SUBJECT_PRICE)] + [sale(f"DW-{i}", 6_100_000) for i in range(5)]
    snapshot = await snapshots_for(listings).for_listing(SUBJECT_ID)
    titles = [step.title for step in snapshot.calculation]
    assert titles == [
        "Comparable listings",
        "Price comparison",
        "Rental yield",
        "Outliers",
        "Locality trend",
        "Data confidence",
    ]
    assert "3 km" in snapshot.calculation[0].detail


async def test_unknown_listing_has_no_snapshot():
    assert await snapshots_for([sale(SUBJECT_ID, SUBJECT_PRICE)]).for_listing("DW-NOPE") is None
