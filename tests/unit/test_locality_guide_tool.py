"""The locality_guide chat tool: places per topic, and the pages they came from."""

import json
from contextlib import aclosing
from dataclasses import replace

import httpx
import pytest

from app.assistant import SourcesFound, Status
from app.assistant.tools import build_locality_guide_tool
from app.assistant.tools.locality_guide import LocalityGuideArtifact
from app.insights import LocalityPlace
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, Reply, ToolCalls, make_listing

pytestmark = pytest.mark.anyio

SCHOOLS_QUOTE = (
    "Best Schools in Vijay Nagar, Indore · SICA Senior Secondary School · Podar International School"
)
AIRPORT_QUOTE = "Indore Airport to Vijay Nagar distance is 10 Km"


def school(name: str) -> LocalityPlace:
    return LocalityPlace(
        topic="schools",
        name=name,
        distance_km=None,
        quote=SCHOOLS_QUOTE,
        title="33 Best Schools in Vijay Nagar, Indore",
        source_name="www.edustoke.com",
        source_url="https://www.edustoke.com/vn",
    )


AIRPORT = LocalityPlace(
    topic="connectivity",
    name="Indore Airport",
    distance_km=10.0,
    quote=AIRPORT_QUOTE,
    title="Indore Airport to Vijay Nagar cab",
    source_name="cabbazar.com",
    source_url="https://cabbazar.com/vn",
)


class StubGuideFinder:
    def __init__(self, found: dict | None) -> None:
        self.found = found
        self.calls: list[tuple[str, str, list[str]]] = []

    async def find(self, locality, city, topics):
        self.calls.append((locality, city, list(topics)))
        return None if self.found is None else {topic: self.found.get(topic, []) for topic in topics}


async def invoke(tool, **arguments):
    return await tool.ainvoke(
        {"name": "locality_guide", "args": arguments, "id": "call_1", "type": "tool_call"}
    )


async def test_lists_places_per_topic_with_the_site_listing_them():
    finder = StubGuideFinder(
        {
            "schools": [school("SICA Senior Secondary School"), school("Podar International School")],
            "connectivity": [AIRPORT],
        }
    )
    message = await invoke(build_locality_guide_tool(finder), locality="Vijay Nagar", city="Indore")
    result = json.loads(message.content)

    assert finder.calls == [("Vijay Nagar", "Indore", ["schools", "hospitals", "connectivity"])]
    assert result["schools"][0] == {
        "name": "SICA Senior Secondary School",
        "listed_by": "www.edustoke.com",
        "quote": SCHOOLS_QUOTE,
    }
    assert result["connectivity"] == [
        {"name": "Indore Airport", "listed_by": "cabbazar.com", "distance_km": 10.0, "quote": AIRPORT_QUOTE}
    ]
    assert result["hospitals"] == []
    assert "Nothing reliable was found for: hospitals" in result["note"]

    # One link per page, not per place.
    assert isinstance(message.artifact, LocalityGuideArtifact)
    assert [(source.url, source.snippet) for source in message.artifact.sources] == [
        ("https://www.edustoke.com/vn", SCHOOLS_QUOTE),
        ("https://cabbazar.com/vn", AIRPORT_QUOTE),
    ]


async def test_links_each_quote_once():
    whole = f"{SCHOOLS_QUOTE} · Daisy Dales School"
    finder = StubGuideFinder(
        {
            "schools": [
                school("SICA Senior Secondary School"),
                replace(school("Daisy Dales School"), quote=whole),
                replace(
                    school("Podar International School"),
                    quote=whole,
                    source_url="https://www.edustoke.com/vn-2",
                ),
            ]
        }
    )
    message = await invoke(
        build_locality_guide_tool(finder), locality="Vijay Nagar", city="Indore", topics=["schools"]
    )

    # The whole snippet replaces the part of it already quoted, and the same
    # snippet under a second URL of the same site isn't linked again.
    assert [(source.url, source.snippet) for source in message.artifact.sources] == [
        ("https://www.edustoke.com/vn", whole),
    ]


async def test_looks_up_only_the_topics_asked_about():
    finder = StubGuideFinder({"connectivity": [AIRPORT]})
    result = json.loads(
        (
            await invoke(
                build_locality_guide_tool(finder),
                locality="Vijay Nagar",
                city="Indore",
                topics=["connectivity"],
            )
        ).content
    )

    assert finder.calls == [("Vijay Nagar", "Indore", ["connectivity"])]
    assert set(result) == {"area", "connectivity", "note"}
    assert "Nothing reliable" not in result["note"]


async def test_says_when_the_guide_is_not_configured():
    message = await invoke(build_locality_guide_tool(StubGuideFinder(None)), locality="Rau", city="Indore")
    assert "not available on this server" in json.loads(message.content)["note"]
    assert message.artifact.sources == []


async def test_the_agent_reports_the_pages_it_quotes(build_agent, model):
    agent = build_agent(
        tools=[
            build_locality_guide_tool(StubGuideFinder({"schools": [school("Podar International School")]}))
        ]
    )
    model.script = [
        ToolCalls([("locality_guide", {"locality": "Vijay Nagar", "city": "Indore", "topics": ["schools"]})]),
        Reply("Edustoke lists Podar International School in Vijay Nagar."),
    ]

    async with aclosing(agent.stream("s1", "Schools in Vijay Nagar, Indore?")) as events:
        collected = [event async for event in events]

    assert Status("Looking up schools, hospitals and connectivity…") in collected
    [sources] = [event for event in collected if isinstance(event, SourcesFound)]
    assert [source.source for source in sources.sources] == ["www.edustoke.com"]


# --- near a listing ------------------------------------------------------------


def with_listings(finder: StubGuideFinder, listings: list[dict]):
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return build_locality_guide_tool(finder, properties=client), api


async def test_a_property_id_searches_around_that_listing():
    finder = StubGuideFinder({"schools": [school("Podar International School")]})
    listing = make_listing(
        "DW-1003", locality="Vijay Nagar", city="Indore", address="Scheme No 54, Vijay Nagar"
    )
    tool, _ = with_listings(finder, [make_listing("DW-1002", locality="Rau"), listing])

    message = await invoke(tool, property_id="dw-1003", topics=["schools"])
    result = json.loads(message.content)

    assert finder.calls == [("Vijay Nagar", "Indore", ["schools"])]
    assert result["property"]["id"] == "DW-1003"
    assert result["property"]["address"] == "Scheme No 54, Vijay Nagar"
    assert "where DW-1003 is" in result["note"]
    # The listing's card comes back for the app to show.
    assert [card.id for card in message.artifact.cards] == ["DW-1003"]
    assert [source.source for source in message.artifact.sources] == ["www.edustoke.com"]


async def test_the_listing_wins_over_an_area_the_model_also_passed():
    finder = StubGuideFinder({})
    tool, _ = with_listings(finder, [make_listing("DW-1003", locality="Vijay Nagar", city="Indore")])
    await invoke(tool, property_id="DW-1003", locality="Rau", city="Indore", topics=["hospitals"])
    assert finder.calls == [("Vijay Nagar", "Indore", ["hospitals"])]


async def test_an_unknown_property_id_is_reported_without_searching():
    finder = StubGuideFinder({})
    tool, _ = with_listings(finder, [make_listing("DW-1003")])

    message = await invoke(tool, property_id="DW-9999")

    assert "No live DigiNiwas listing has the ID DW-9999" in json.loads(message.content)["note"]
    assert finder.calls == []
    assert message.artifact.cards == []


async def test_a_listings_outage_is_reported_without_searching():
    finder = StubGuideFinder({})
    tool, api = with_listings(finder, [])
    api.error = httpx.ConnectError("down")

    message = await invoke(tool, property_id="DW-1003")

    assert "could not be loaded" in json.loads(message.content)["note"]
    assert finder.calls == []


async def test_needs_a_property_id_or_an_area():
    tool = build_locality_guide_tool(StubGuideFinder({}))
    with pytest.raises(ValueError, match="property_id, or both locality and city"):
        await invoke(tool, locality="Rau", property_id="  ")


async def test_the_agent_shows_the_listing_asked_about(build_agent, model, properties_client):
    finder = StubGuideFinder({"hospitals": []})
    agent = build_agent(tools=[build_locality_guide_tool(finder, properties=properties_client)])
    listing_id = "DW-1003"  # in the fixture listings
    model.script = [
        ToolCalls([("locality_guide", {"property_id": listing_id, "topics": ["hospitals"]})]),
        Reply("I couldn't find reliable information on hospitals in Vijay Nagar, where DW-1003 is."),
    ]

    result = await agent.run("s1", f"Any hospitals near {listing_id}?")

    assert [card.id for card in result.properties] == [listing_id]
    assert finder.calls and finder.calls[0][2] == ["hospitals"]
