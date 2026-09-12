"""Estimated rental yield: weighted rents, gross and net yield, and the web fallback."""

from datetime import date

import pytest

from app.insights import ComparablesFinder, RentEstimate, SnapshotService
from app.insights.rent import recency_weight
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, make_listing

TODAY = date(2026, 9, 13)
SUBJECT_ID = "DW-SUB"
PRICE = 6_000_000


class StubRent:
    """Stands in for WebRentEstimator, and records what it was asked."""

    def __init__(self, estimate: RentEstimate | None) -> None:
        self._estimate = estimate
        self.calls: list[tuple] = []

    async def estimate(self, locality, city, bedrooms):
        self.calls.append((locality, city, bedrooms))
        return self._estimate


QUOTED = RentEstimate(
    monthly_rent=17_418,
    bedrooms=None,
    quote="The average rent in Vijay Nagar is Rs. 17,418 per month",
    source_name="housing.com",
    source_url="https://housing.com/price-trends/vijay-nagar",
)


def service(listings, *, web_rent=None, vacancy_months=1.0) -> SnapshotService:
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return SnapshotService(
        client,
        ComparablesFinder(client),
        web_rent=web_rent,
        vacancy_months=vacancy_months,
        today=lambda: TODAY,
    )


def subject(**extra) -> dict:
    return make_listing(SUBJECT_ID, price=PRICE, size=1100, **extra)


def rental(listing_id: str, rent: int, **extra) -> dict:
    return make_listing(listing_id, transaction_type="Rent", price=rent, size=1100, **extra)


async def rental_yield(listings, **options):
    return (await service(listings, **options).for_listing(SUBJECT_ID)).rental_yield


# --- weighting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("listed_on", "weight"),
    [
        (date(2026, 6, 1), 0.5),  # within a year
        (date(2025, 3, 1), 0.3),  # 1-2 years
        (date(2024, 1, 1), 0.2),  # 2-3 years
        (date(2022, 1, 1), 0.0),  # older: ignored
        (None, 0.2),  # no date: counts, but like the oldest
    ],
)
def test_recently_listed_rentals_count_for_more(listed_on, weight):
    assert recency_weight(listed_on, TODAY) == weight


@pytest.mark.anyio
async def test_recent_rents_outweigh_older_ones():
    older = [rental(f"DW-OLD{i}", 20_000, createdAt="2024-03-01T00:00:00Z") for i in range(3)]
    recent = [rental(f"DW-NEW{i}", 25_000, createdAt="2026-06-01T00:00:00Z") for i in range(3)]
    estimate = await rental_yield([subject(), *older, *recent])

    # A plain median of these six rents would be 22,500.
    assert estimate.estimated_monthly_rent == 25_000
    assert (estimate.source, estimate.sample_size) == ("listings", 6)


@pytest.mark.anyio
async def test_closer_rentals_outweigh_distant_ones():
    near = [rental(f"DW-NEAR{i}", 30_000) for i in range(2)]
    far = [rental(f"DW-FAR{i}", 20_000, latitude=22.7785) for i in range(3)]  # ~2.8 km away
    estimate = await rental_yield([subject(), *near, *far])

    # A plain median would be 20,000: three distant rentals against two close ones.
    assert estimate.estimated_monthly_rent == 30_000


@pytest.mark.anyio
async def test_rentals_listed_over_three_years_ago_are_left_out():
    stale = [rental(f"DW-{i}", 25_000, createdAt="2022-01-01T00:00:00Z") for i in range(4)]
    estimate = await rental_yield([subject(), *stale])

    assert estimate.available is False
    assert estimate.sample_size == 0


# --- gross and net -----------------------------------------------------------


@pytest.mark.anyio
async def test_gross_and_net_yield_with_their_assumptions():
    rentals = [rental(f"DW-{i}", 25_000) for i in range(3)]
    estimate = await rental_yield([subject(maintenance=3_000), *rentals])

    # Gross: 25,000 × 12 ÷ 60,00,000 × 100 = 5.0%.
    assert estimate.gross_percent == 5.0
    # Net: (3,00,000 − 25,000 vacancy − 36,000 maintenance) ÷ 60,00,000 × 100 = 3.98%.
    assert estimate.net_percent == 4.0
    assert estimate.annual_expenses == 61_000
    assert any("without a tenant" in line for line in estimate.assumptions)
    assert any("Maintenance of" in line for line in estimate.assumptions)
    assert estimate.rent_scope == "similar 2 BHK rentals within 3 km"


@pytest.mark.anyio
async def test_net_yield_deducts_only_what_is_known():
    rentals = [rental(f"DW-{i}", 25_000) for i in range(3)]
    estimate = await rental_yield([subject(), *rentals], vacancy_months=0)

    assert estimate.net_percent == estimate.gross_percent == 5.0
    assert any("No maintenance charge is listed" in line for line in estimate.assumptions)


@pytest.mark.anyio
async def test_the_working_is_explained():
    rentals = [rental(f"DW-{i}", 25_000) for i in range(3)]
    snapshot = await service([subject(maintenance=3_000), *rentals]).for_listing(SUBJECT_ID)

    [step] = [step for step in snapshot.calculation if step.title == "Rental yield"]
    assert "= 5.0%" in step.detail
    assert "= 4.0%" in step.detail


# --- web fallback ------------------------------------------------------------


@pytest.mark.anyio
async def test_quotes_a_locality_average_when_rentals_are_scarce():
    stub = StubRent(QUOTED)
    snapshot = await service([subject(), rental("DW-R1", 25_000)], web_rent=stub).for_listing(SUBJECT_ID)
    estimate = snapshot.rental_yield

    assert (estimate.available, estimate.source, estimate.confidence) == (True, "web", "Low")
    assert estimate.estimated_monthly_rent == 17_418
    assert estimate.gross_percent == 3.5  # 17,418 × 12 ÷ 60,00,000 × 100
    assert estimate.rent_scope == "all property types in Vijay Nagar"
    assert estimate.quote == QUOTED.quote
    assert estimate.source_url == QUOTED.source_url
    assert any("average over all property types" in line for line in estimate.assumptions)
    assert stub.calls == [("Vijay Nagar", "Indore", 2)]
    assert any("quoted from the web" in reason for reason in snapshot.data_confidence.reasons)


@pytest.mark.anyio
async def test_does_not_search_when_there_are_enough_rentals():
    stub = StubRent(QUOTED)
    rentals = [rental(f"DW-{i}", 25_000) for i in range(3)]
    estimate = await rental_yield([subject(), *rentals], web_rent=stub)

    assert estimate.source == "listings"
    assert stub.calls == []


@pytest.mark.anyio
async def test_discards_a_quoted_rent_that_implies_an_implausible_yield():
    one_room = RentEstimate(
        monthly_rent=1_500,
        bedrooms=None,
        quote="The average rent in Vijay Nagar is Rs. 1,500 per month",
        source_name="example.test",
        source_url="https://example.test",
    )
    # 1,500 × 12 ÷ 60,00,000 is a 0.3% yield: not a figure about this home.
    estimate = await rental_yield([subject()], web_rent=StubRent(one_room))
    assert estimate.available is False


@pytest.mark.anyio
async def test_a_rental_listing_has_no_yield_and_searches_nothing():
    stub = StubRent(QUOTED)
    api_listings = [rental(SUBJECT_ID, 25_000)]
    estimate = (await service(api_listings, web_rent=stub).for_listing(SUBJECT_ID)).rental_yield

    assert estimate.headline == "Not applicable"
    assert stub.calls == []
