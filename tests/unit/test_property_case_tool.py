"""property_case: the evidence for one listing, gathered in one call."""

import json
from contextlib import aclosing

import httpx
import pytest

from app.assistant import PropertiesFound, SourcesFound, Status
from app.assistant.tools import build_property_case_tool
from app.assistant.tools.property_case import PropertyCaseArtifact
from app.insights import AreaRate, ComparablesFinder, LocalityPlace, SnapshotService
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, Reply, StubAreaRateFinder, ToolCalls, make_listing

pytestmark = pytest.mark.anyio

SUBJECT = "DW-1003"
# 1100 sqft at ₹60 L is 5,455/sqft; the neighbours sit at 6,364/sqft.
SUBJECT_PRICE = 6_000_000
NEIGHBOUR_PRICE = 7_000_000

SCHOOL = LocalityPlace(
    topic="schools",
    name="Podar International School",
    distance_km=None,
    quote="Best Schools in Vijay Nagar, Indore · Podar International School",
    title="33 Best Schools in Vijay Nagar, Indore",
    source_name="www.edustoke.com",
    source_url="https://www.edustoke.com/vn",
)
AIRPORT = LocalityPlace(
    topic="connectivity",
    name="Indore Airport",
    distance_km=12.1,
    quote="The distance between Vijay Nagar and Indore Airport (IDR) is 8 miles.",
    title="Vijay Nagar to Indore Airport",
    source_name="www.rome2rio.com",
    source_url="https://www.rome2rio.com/vn",
)
FLAT_RATE = AreaRate(
    kind="flat",
    basis="asking price",
    unit="sqft",
    average=11_048,
    low=None,
    high=None,
    quote="The average price per sqft for Flats in Vijay Nagar, Indore is Rs. 11,048.",
    title="Flats for sale in Vijay Nagar, Indore",
    source_name="housing.com",
    source_url="https://housing.com/vn",
)
LAND_RATE = AreaRate(
    kind="land",
    basis="asking price",
    unit="sqft",
    average=4_000,
    low=None,
    high=None,
    quote="Land rates in Vijay Nagar, Indore are around Rs 4,000 per sq ft.",
    title="Plots in Vijay Nagar, Indore",
    source_name="www.99acres.com",
    source_url="https://www.99acres.com/vn",
)


class StubGuide:
    """Stands in for LocalityGuideFinder: fixed places, and a record of the asks."""

    def __init__(self, found: dict | None) -> None:
        self.found = found
        self.calls: list[tuple[str | None, str | None]] = []

    async def find(self, locality, city, topics=("schools", "hospitals", "connectivity")):
        self.calls.append((locality, city))
        if self.found is None:
            return None
        return {topic: self.found.get(topic, []) for topic in topics}


class BrokenSnapshots:
    """A snapshot service that can't reach the listings API."""

    async def for_listing(self, property_id: str):
        raise httpx.ConnectError("down")


def tool_for(listings: list[dict], *, guide=None, rates=None, snapshots=None):
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    service = snapshots if snapshots is not None else SnapshotService(client, ComparablesFinder(client))
    return build_property_case_tool(client, service, guide=guide, rates=rates), api


async def invoke(tool, **arguments):
    return await tool.ainvoke({"name": "property_case", "args": arguments, "id": "c1", "type": "tool_call"})


def neighbourhood(subject: dict) -> list[dict]:
    return [subject, *(make_listing(f"DW-N{i}", price=NEIGHBOUR_PRICE, size=1100) for i in range(5))]


async def test_gathers_price_yield_trend_and_what_is_nearby():
    guide = StubGuide({"schools": [SCHOOL], "connectivity": [AIRPORT]})
    tool, _ = tool_for(
        neighbourhood(make_listing(SUBJECT, price=SUBJECT_PRICE, size=1100)),
        guide=guide,
        rates=StubAreaRateFinder([FLAT_RATE]),
    )

    message = await invoke(tool, property_id=" dw-1003 ")
    result = json.loads(message.content)

    assert result["property"]["id"] == SUBJECT
    assert result["property"]["price_per_sqft"] == 5455
    assert result["price_check"]["headline"] == "14% below"
    assert result["price_check"]["comparable_listings"] == 5
    # Its ₹/sqft against the rate housing.com publishes for flats there.
    assert result["versus_published_rate"] == {
        "listing_per_sqft": 5455,
        "area_average_per_sqft": 11_048,
        "difference_percent": -50.6,
        "about": "flat",
        "basis": "asking price",
        "source": "housing.com",
        "quote": FLAT_RATE.quote,
    }
    assert result["nearby"]["schools"] == [
        {"name": "Podar International School", "listed_by": "www.edustoke.com"}
    ]
    assert result["nearby"]["connectivity"][0]["distance_km"] == 12.1
    # No rentals and no web search here, so both are named as unestablished.
    assert "rental yield" in result["not_established"]
    assert "hospitals" in result["not_established"]
    assert "Never invent" in result["note"]

    assert isinstance(message.artifact, PropertyCaseArtifact)
    assert [card.id for card in message.artifact.cards] == [SUBJECT]
    assert [source.source for source in message.artifact.sources] == [
        "housing.com",
        "www.edustoke.com",
        "www.rome2rio.com",
    ]
    assert guide.calls == [("Vijay Nagar", "Indore")]


async def test_a_plot_is_compared_with_land_rates_not_flat_rates():
    rates = StubAreaRateFinder([FLAT_RATE, LAND_RATE])
    plot = make_listing(SUBJECT, category="Plot/Land", price=6_000_000, size=1100, bedrooms="")
    tool, _ = tool_for([plot], rates=rates)

    result = json.loads((await invoke(tool, property_id=SUBJECT)).content)

    assert rates.calls == [("Vijay Nagar", "Indore", "land")]
    assert result["versus_published_rate"]["about"] == "land"
    assert result["versus_published_rate"]["difference_percent"] == 36.4  # 5,455 vs 4,000


async def test_no_rate_comparison_when_the_published_rate_is_for_another_kind():
    flat = make_listing(SUBJECT, price=SUBJECT_PRICE, size=1100)
    tool, _ = tool_for([flat], rates=StubAreaRateFinder([LAND_RATE]))
    assert "versus_published_rate" not in json.loads((await invoke(tool, property_id=SUBJECT)).content)


async def test_an_unknown_listing_is_reported_without_gathering_anything():
    guide = StubGuide({})
    tool, _ = tool_for([make_listing(SUBJECT)], guide=guide)

    message = await invoke(tool, property_id="DW-9999")

    assert "No live DigiNiwas listing has the ID DW-9999" in json.loads(message.content)["note"]
    assert guide.calls == []
    assert message.artifact.cards == []


async def test_what_cannot_be_gathered_is_named_not_guessed():
    # The snapshot fails; the guide and rates are switched off.
    tool, _ = tool_for(
        [make_listing(SUBJECT)],
        guide=StubGuide(None),
        rates=StubAreaRateFinder(None),
        snapshots=BrokenSnapshots(),
    )

    result = json.loads((await invoke(tool, property_id=SUBJECT)).content)

    assert result["property"]["id"] == SUBJECT
    assert result["not_established"] == [
        "price comparison, rental yield and locality trend",
        "schools, hospitals and connectivity",
    ]
    assert "price_check" not in result


async def test_the_listings_api_being_down_is_reported():
    tool, api = tool_for([])
    api.error = httpx.ConnectError("down")
    message = await invoke(tool, property_id=SUBJECT)
    assert "could not be loaded" in json.loads(message.content)["note"]


async def test_the_agent_shows_the_card_and_the_pages_it_quoted(build_agent, model, properties_client):
    guide = StubGuide({"schools": [SCHOOL]})
    api = FakePropertiesAPI([make_listing(SUBJECT, price=SUBJECT_PRICE, size=1100)])
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    tool = build_property_case_tool(
        client, SnapshotService(client, ComparablesFinder(client)), guide=guide, rates=StubAreaRateFinder([])
    )
    agent = build_agent(tools=[tool])
    answer = "At ₹5,455 per sq ft it is 14% below similar homes within 3 km, and edustoke lists Podar International School in Vijay Nagar."
    model.script = [ToolCalls([("property_case", {"property_id": SUBJECT})]), Reply(answer)]

    async with aclosing(agent.stream("s1", f"Why should I buy {SUBJECT}?")) as events:
        collected = [event async for event in events]

    assert Status("Checking the price, rental yield and what's nearby…") in collected
    [found] = [event for event in collected if isinstance(event, PropertiesFound)]
    assert [card.id for card in found.cards] == [SUBJECT]
    [sources] = [event for event in collected if isinstance(event, SourcesFound)]
    assert [source.source for source in sources.sources] == ["www.edustoke.com"]
