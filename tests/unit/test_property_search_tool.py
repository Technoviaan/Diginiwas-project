import json

import httpx
import pytest
from pydantic import ValidationError

from app.assistant.tools.property_search import PropertySearchInput


async def invoke(tool, **arguments):
    return await tool.ainvoke(
        {"name": "search_properties", "args": arguments, "id": "call_1", "type": "tool_call"}
    )


# --- input: what the model sends ---------------------------------------------


def test_budgets_in_words_become_rupees():
    assert PropertySearchInput(max_price="1 crore").to_query().max_price == 10_000_000


def test_around_a_price_searches_fifteen_percent_either_side():
    query = PropertySearchInput(approx_price="80 lakh").to_query()
    assert (query.min_price, query.max_price) == (6_800_000, 9_200_000)


def test_an_explicit_bound_wins_over_around():
    query = PropertySearchInput(approx_price="80 lakh", max_price="85 lakh").to_query()
    assert (query.min_price, query.max_price) == (6_800_000, 8_500_000)


def test_minimum_above_maximum_is_rejected():
    with pytest.raises(ValidationError, match="min_price is greater than max_price"):
        PropertySearchInput(min_price="1 crore", max_price="50 lakh")


def test_unreadable_amount_is_rejected():
    with pytest.raises(ValidationError, match="could not read"):
        PropertySearchInput(max_price="ten lakh")


def test_filters_applied_show_rupees_with_their_indian_label():
    described = PropertySearchInput(max_price="1 crore", bedrooms=3).describe()
    assert described == {"max_price": "10000000 (₹1 Cr)", "bedrooms": 3}


# --- the tool: what the model and the app get back ---------------------------


@pytest.mark.anyio
async def test_model_gets_a_summary_and_the_app_gets_cards(property_tool, properties_api):
    message = await invoke(property_tool, search="Vijay Nagar", max_price="1 crore")

    summary = json.loads(message.content)
    assert summary["total_matches"] == 1
    assert summary["filters_applied"] == {"search": "Vijay Nagar", "max_price": "10000000 (₹1 Cr)"}
    listing = summary["listings"][0]
    assert listing["price_label"] == "₹85 L"
    assert not {"image", "images", "latitude", "url"} & listing.keys()

    assert [card.id for card in message.artifact] == ["DW-1003"]
    assert properties_api.last_params["maxPrice"] == "10000000"


@pytest.mark.anyio
async def test_no_matches_tells_the_model_to_relax_a_filter(property_tool):
    message = await invoke(property_tool, search="Model Town")
    summary = json.loads(message.content)
    assert summary["showing"] == 0
    assert "one filter removed" in summary["note"]
    assert message.artifact == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "wording"),
    [(httpx.ConnectTimeout("slow"), "timed out"), (httpx.ConnectError("down"), "unavailable")],
)
async def test_listing_service_failures_are_reported_without_cards(
    property_tool, properties_api, error, wording
):
    properties_api.error = error
    message = await invoke(property_tool, city="Indore")
    assert wording in json.loads(message.content)["error"]
    assert message.artifact is None
