"""The lookup_area_rates chat tool: DigiNiwas listings plus published rates."""

import json
from contextlib import aclosing

import httpx
import pytest

from app.assistant import SourcesFound, Status
from app.assistant.tools import build_area_rates_tool
from app.assistant.tools.area_rates import AreaRatesArtifact
from app.insights import AreaRate
from app.properties import PropertiesClient
from tests.fakes import (
    FakePropertiesAPI,
    Reply,
    StubAreaRateFinder,
    ToolCalls,
    housing_plot_rate,
    make_listing,
)

pytestmark = pytest.mark.anyio


def plot(listing_id: str, price: int, size: int = 1_000) -> dict:
    return make_listing(listing_id, category="Plot/Land", price=price, size=size, bedrooms="")


def tool_with(listings: list[dict], finder: StubAreaRateFinder):
    api = FakePropertiesAPI(listings)
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return build_area_rates_tool(client, finder, page_size=6), api


async def invoke(tool, **arguments):
    return await tool.ainvoke(
        {"name": "lookup_area_rates", "args": arguments, "id": "call_1", "type": "tool_call"}
    )


async def test_combines_diginiwas_plots_with_published_rates():
    plots = [plot(f"DW-P{i}", price=10_000_000 + i * 500_000) for i in range(3)]  # 10,000-11,000 /sq ft
    tool, api = tool_with(
        [*plots, make_listing("DW-FLAT", price=6_000_000)], StubAreaRateFinder([housing_plot_rate()])
    )

    message = await invoke(tool, locality="Vijay Nagar", city="Indore", kind="land")
    result = json.loads(message.content)

    assert result["area"] == "Vijay Nagar, Indore"
    assert result["diginiwas_listings"] == {"count": 3, "median_price_per_sqft": 10_500}
    [published] = result["published_rates"]
    assert published["source"] == "housing.com"
    assert published["rate"] == "₹11,048 per sq ft average; ₹356–₹22,987 per sq ft"
    assert "Name the source" in result["note"]

    assert isinstance(message.artifact, AreaRatesArtifact)
    assert [card.id for card in message.artifact.cards] == ["DW-P0", "DW-P1", "DW-P2"]  # not the flat
    [source] = message.artifact.sources
    assert (source.source, source.snippet) == ("housing.com", housing_plot_rate().quote)
    assert api.last_params["category"] == "Plot/Land"


async def test_says_when_no_published_rate_could_be_verified():
    tool, _ = tool_with([], StubAreaRateFinder([]))
    result = json.loads((await invoke(tool, locality="Rau", city="Indore")).content)

    assert result["published_rates"] == []
    assert "do not guess" in result["note"]
    assert result["diginiwas_listings"] == {"count": 0}


async def test_says_when_the_lookup_is_not_configured():
    tool, _ = tool_with([], StubAreaRateFinder(None))
    result = json.loads((await invoke(tool, locality="Rau", city="Indore")).content)
    assert "not available on this server" in result["note"]


async def test_a_listings_outage_still_gives_the_published_rates():
    tool, api = tool_with([], StubAreaRateFinder([housing_plot_rate()]))
    api.error = httpx.ConnectError("down")

    result = json.loads((await invoke(tool, locality="Vijay Nagar", city="Indore", kind="land")).content)

    assert result["diginiwas_listings"]["note"] == "DigiNiwas listings could not be loaded."
    assert len(result["published_rates"]) == 1


async def test_other_units_are_also_given_per_sq_ft():
    sq_yd = AreaRate(
        kind="land",
        basis="asking price",
        unit="sqyd",
        average=36_000,
        low=None,
        high=None,
        quote="Plot rates in Rau, Indore average ₹ 36,000 per sq yd.",
        title="Plot rates in Rau",
        source_name="portal.test",
        source_url="https://portal.test",
    )
    tool, _ = tool_with([], StubAreaRateFinder([sq_yd]))
    [published] = json.loads((await invoke(tool, locality="Rau", city="Indore")).content)["published_rates"]
    assert published["per_sq_ft"] == {"average": 4_000}


# --- in the agent ------------------------------------------------------------


async def test_the_agent_reports_the_sources_it_quotes(build_agent, model, properties_client):
    finder = StubAreaRateFinder([housing_plot_rate()])
    agent = build_agent(tools=[build_area_rates_tool(properties_client, finder, page_size=6)])
    lookup = ToolCalls([("lookup_area_rates", {"locality": "Vijay Nagar", "city": "Indore", "kind": "land"})])
    answer = "housing.com puts plots in Vijay Nagar at an average of ₹11,048 per sq ft."

    model.script = [lookup, Reply(answer)]
    async with aclosing(agent.stream("s1", "Average land price in Vijay Nagar, Indore?")) as events:
        collected = [event async for event in events]

    assert Status("Checking published area rates…") in collected
    [sources] = [event for event in collected if isinstance(event, SourcesFound)]
    assert [source.source for source in sources.sources] == ["housing.com"]

    model.script = [lookup, Reply(answer)]
    result = await agent.run("s2", "Average land price in Vijay Nagar, Indore?")
    assert result.reply == answer
    assert [source.url for source in result.sources] == ["https://housing.com/plots-in-vijay-nagar-indore"]
    assert finder.calls[0] == ("Vijay Nagar", "Indore", "land")
