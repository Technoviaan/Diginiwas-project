"""Quoting a locality's average rent from the web, and refusing anything weaker."""

import json

import httpx
import pytest

from app.insights import LocalitySearch, WebRentEstimator
from tests.fakes import Reply, ScriptedChatModel

pytestmark = pytest.mark.anyio

TITLE = "Flats for Rent in Vijay Nagar, Indore"
AVERAGE = (
    "The average rent in Vijay Nagar is Rs. 17,418 per month and property prices average Rs. 4,261 per sq ft."
)
ONE_HOUSE = "This 3 BHK rental house is located in Vijay Nagar, Indore. The rent is ₹6,000 per month."


def result(title: str, snippet: str) -> dict:
    return {"title": title, "snippet": snippet, "link": "https://housing.com/1", "displayLink": "housing.com"}


def searching(*items: dict) -> LocalitySearch:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(items)})

    return LocalitySearch(api_key="key", engine_id="cx", transport=httpx.MockTransport(handle))


def claiming(**payload) -> ScriptedChatModel:
    return ScriptedChatModel(script=[Reply(json.dumps(payload))])


def estimator(search: LocalitySearch, model: ScriptedChatModel, *, enabled: bool = True) -> WebRentEstimator:
    return WebRentEstimator(search, model, enabled=enabled)


async def estimate(search, model, bedrooms=3):
    return await estimator(search, model).estimate("Vijay Nagar", "Indore", bedrooms)


async def test_quotes_an_average_rent_a_snippet_states():
    quote = "The average rent in Vijay Nagar is Rs. 17,418 per month"
    model = claiming(found=True, amount="Rs. 17,418", bedrooms=None, quote=quote, source=0)
    found = await estimate(searching(result(TITLE, AVERAGE)), model)

    assert found.monthly_rent == 17_418  # read by code from "Rs. 17,418"
    assert found.bedrooms is None
    assert found.quote == quote
    assert (found.source_name, found.source_url) == ("housing.com", "https://housing.com/1")


async def test_refuses_the_rent_of_a_single_house():
    model = claiming(found=True, amount="₹6,000", bedrooms=3, quote="The rent is ₹6,000 per month.", source=0)
    assert await estimate(searching(result(TITLE, ONE_HOUSE)), model) is None


async def test_refuses_a_quote_the_model_wrote_itself():
    model = claiming(
        found=True,
        amount="Rs. 17,418",
        bedrooms=None,
        quote="Average rent in Vijay Nagar is about Rs. 17,418",
        source=0,
    )
    assert await estimate(searching(result(TITLE, AVERAGE)), model) is None


async def test_refuses_an_amount_that_is_not_in_the_quote():
    quote = "The average rent in Vijay Nagar is Rs. 17,418 per month"
    model = claiming(found=True, amount="Rs. 25,000", bedrooms=None, quote=quote, source=0)
    assert await estimate(searching(result(TITLE, AVERAGE)), model) is None


async def test_refuses_a_snippet_about_another_city():
    elsewhere = "The average rent in Vijay Nagar is Rs. 21,000 per month."
    model = claiming(found=True, amount="Rs. 21,000", bedrooms=None, quote=elsewhere, source=0)
    search = searching(result("Flats for Rent in Vijay Nagar, Delhi", elsewhere))
    assert await estimate(search, model) is None


async def test_refuses_an_average_for_a_different_home_size():
    snippet = "The average rent for 2 BHK flats in Vijay Nagar, Indore is Rs. 14,000 per month."
    model = claiming(found=True, amount="Rs. 14,000", bedrooms=2, quote=snippet, source=0)
    assert await estimate(searching(result(TITLE, snippet)), model, bedrooms=3) is None


async def test_accepts_an_average_for_the_same_home_size():
    snippet = "The average rent for 3 BHK flats in Vijay Nagar, Indore is Rs. 30,500 per month."
    model = claiming(found=True, amount="Rs. 30,500", bedrooms=3, quote=snippet, source=0)
    found = await estimate(searching(result(TITLE, snippet)), model, bedrooms=3)
    assert (found.monthly_rent, found.bedrooms) == (30_500, 3)


async def test_the_home_size_comes_from_the_quote_not_the_model():
    # Live, the model labelled this all-types average "3 BHK" because it had
    # been asked about a 3 BHK home. The quote names no BHK count, so it isn't one.
    quote = "The average rent in Vijay Nagar is Rs. 17,418 per month"
    model = claiming(found=True, amount="Rs. 17,418", bedrooms="3", quote=quote, source=0)
    found = await estimate(searching(result(TITLE, AVERAGE)), model, bedrooms=3)
    assert (found.monthly_rent, found.bedrooms) == (17_418, None)


async def test_refuses_a_quote_naming_several_home_sizes():
    snippet = "Average rent in Vijay Nagar, Indore: 2 BHK Rs. 14,000 per month, 3 BHK Rs. 30,500 per month."
    model = claiming(found=True, amount="Rs. 30,500", quote=snippet, source=0)
    assert await estimate(searching(result(TITLE, snippet)), model, bedrooms=3) is None


async def test_is_off_unless_switched_on_and_configured():
    model = claiming(found=False)
    assert estimator(searching(), model, enabled=False).enabled is False
    assert estimator(LocalitySearch(api_key=None, engine_id=None), model).enabled is False
    assert await estimator(searching(), model, enabled=False).estimate("Vijay Nagar", "Indore", 3) is None
    assert model.requests == []


async def test_asks_once_per_locality_and_home_size():
    quote = "The average rent in Vijay Nagar is Rs. 17,418 per month"
    model = claiming(found=True, amount="Rs. 17,418", bedrooms=None, quote=quote, source=0)
    client = estimator(searching(result(TITLE, AVERAGE)), model)

    first = await client.estimate("Vijay Nagar", "Indore", 3)
    second = await client.estimate("Vijay Nagar", "Indore", 3)

    assert first == second
    assert len(model.requests) == 1
