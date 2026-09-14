"""The locality_guide chat tool: places per topic, and the pages they came from."""

import json
from contextlib import aclosing
from dataclasses import replace

import pytest

from app.assistant import SourcesFound, Status
from app.assistant.tools import build_locality_guide_tool
from app.assistant.tools.locality_guide import LocalityGuideArtifact
from app.insights import LocalityPlace
from tests.fakes import Reply, ToolCalls

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
