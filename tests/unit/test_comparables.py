"""Which listings count as comparable, and which are correctly left out."""

import pytest

from app.insights.comparables import ComparablesFinder, room_count
from app.properties import PropertiesClient, PropertyQuery
from tests.fakes import FakePropertiesAPI, make_listing

pytestmark = pytest.mark.anyio

# 0.01° of latitude is about 1.1 km.
NEAR = {"latitude": 22.7600, "longitude": 75.8937}  # ~0.7 km away
FAR = {"latitude": 22.8100, "longitude": 75.8937}  # ~6 km away


def finder_for(listings, **options) -> tuple[ComparablesFinder, FakePropertiesAPI]:
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return ComparablesFinder(client, **options), api


SUBJECT = make_listing("DW-SUB", price=6_000_000, size=1100)


async def subject_card(api: FakePropertiesAPI):
    """The subject listing as a card, fetched the way the service does."""
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    page = await client.search(PropertyQuery(search="DW-SUB"), limit=5)
    return page.cards[0]


@pytest.mark.parametrize(("written", "expected"), [("3", 3), ("7+", 7), ("", None), (None, None)])
def test_room_count_reads_the_api_strings(written, expected):
    assert room_count(written) == expected


async def test_finds_similar_listings_nearby():
    finder, api = finder_for(
        [
            SUBJECT,
            make_listing("DW-NEAR", **NEAR),
            make_listing("DW-SAME-SPOT"),
        ]
    )
    found = await finder.find(await subject_card(api), transaction_type="Sale")
    assert [comparable.card.id for comparable in found] == ["DW-SAME-SPOT", "DW-NEAR"]


async def test_leaves_out_the_listing_itself():
    finder, api = finder_for([SUBJECT, make_listing("DW-OTHER")])
    found = await finder.find(await subject_card(api), transaction_type="Sale")
    assert "DW-SUB" not in [comparable.card.id for comparable in found]


async def test_leaves_out_listings_beyond_the_radius():
    finder, api = finder_for([SUBJECT, make_listing("DW-FAR", **FAR)], radius_km=3.0)
    assert await finder.find(await subject_card(api), transaction_type="Sale") == []


async def test_leaves_out_listings_of_a_very_different_size():
    finder, api = finder_for([SUBJECT, make_listing("DW-HUGE", size=3000)], area_tolerance=0.25)
    assert await finder.find(await subject_card(api), transaction_type="Sale") == []


async def test_without_coordinates_falls_back_to_the_locality_name():
    finder, api = finder_for(
        [
            SUBJECT,
            make_listing("DW-SAME-AREA", latitude=None, longitude=None),
            make_listing("DW-ELSEWHERE", latitude=None, longitude=None, locality="Palasia"),
        ]
    )
    found = await finder.find(await subject_card(api), transaction_type="Sale")
    assert [comparable.card.id for comparable in found] == ["DW-SAME-AREA"]
    assert found[0].distance_km is None


async def test_keeps_only_the_closest_matches():
    others = [make_listing(f"DW-{index}") for index in range(10)]
    finder, api = finder_for([SUBJECT, *others], max_comparables=4)
    assert len(await finder.find(await subject_card(api), transaction_type="Sale")) == 4


async def test_walks_through_every_page_of_results():
    others = [make_listing(f"DW-{index}") for index in range(150)]
    finder, api = finder_for([SUBJECT, *others], max_comparables=200)
    found = await finder.find(await subject_card(api), transaction_type="Sale")
    assert len(found) == 150
    # One request to fetch the subject, then two pages of 100.
    assert len(api.requests) == 3
