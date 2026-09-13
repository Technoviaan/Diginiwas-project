"""Published area rates: what portals say land or flats cost, and what code refuses.

The snippets are taken from real search results for Vijay Nagar and Rau, Indore.
"""

import json

import httpx
import pytest

from app.insights import AreaRateFinder, LocalitySearch
from tests.fakes import Reply, ScriptedChatModel

pytestmark = pytest.mark.anyio

HOUSING_TITLE = "639+ Residential Land / Plots for sale in Vijay Nagar, Indore"
HOUSING = (
    "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048. "
    "The price range per sqft is Rs. 356 - Rs. 22,987. See Price Trends in Vijay Nagar."
)
LISTING_TITLE = "Plots for sale in Vijay Nagar Indore - 75+ Residential Land"
LISTING = "Plots for Sale in Vijay Nagar Indore ; Vijay Nagar , Indore · ₹2.6 Cr. ₹13,000 /sqft · 2,000 sqft(186 sqm)"
RAU_TITLE = "Property Rates in Rau, Indore 2026"
RAU = (
    "Flat prices in Rau, Indore are in the range of Rs 2650-4100 per square feet (sq ft). "
    "Land rates in Rau, Indore are around Rs 3250-5800 per sq ft."
)


def result(title: str, snippet: str, link: str = "https://housing.com/1", host: str = "housing.com") -> dict:
    return {"title": title, "snippet": snippet, "link": link, "displayLink": host}


def searching(*items: dict) -> LocalitySearch:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(items)})

    return LocalitySearch(api_key="key", engine_id="cx", limit=6, transport=httpx.MockTransport(handle))


def claiming(*rates: dict) -> ScriptedChatModel:
    return ScriptedChatModel(script=[Reply(json.dumps({"rates": list(rates)}))])


def rate(quote: str, source: int = 0, *, average=None, low=None, high=None) -> dict:
    return {"average": average, "low": low, "high": high, "quote": quote, "source": source}


async def find(search, model, *, locality="Vijay Nagar", city="Indore", kind="land"):
    return await AreaRateFinder(search, model, enabled=True).find(locality, city, kind)


# --- accepted ----------------------------------------------------------------


async def test_reads_an_average_and_a_range_a_portal_publishes():
    quote = (
        "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048. "
        "The price range per sqft is Rs. 356 - Rs. 22,987."
    )
    model = claiming(rate(quote, average="Rs. 11,048", low="Rs. 356", high="Rs. 22,987"))
    [found] = await find(searching(result(HOUSING_TITLE, HOUSING)), model)

    assert (found.average, found.low, found.high, found.unit) == (11_048, 356, 22_987, "sqft")
    assert (found.kind, found.basis) == ("land", "asking price")
    assert found.summary == "₹11,048 per sq ft average; ₹356–₹22,987 per sq ft"
    assert (found.source_name, found.source_url) == ("housing.com", "https://housing.com/1")


async def test_a_land_question_takes_the_land_rate_not_the_flat_rate():
    flat = rate(
        "Flat prices in Rau, Indore are in the range of Rs 2650-4100 per square feet (sq ft).",
        low="Rs 2650",
        high="4100",
    )
    land = rate("Land rates in Rau, Indore are around Rs 3250-5800 per sq ft.", low="Rs 3250", high="5800")
    search = searching(result(RAU_TITLE, RAU, link="https://www.99acres.com/rau", host="www.99acres.com"))
    found = await find(search, claiming(flat, land), locality="Rau")

    assert [(item.kind, item.low, item.high) for item in found] == [("land", 3_250, 5_800)]


async def test_converts_square_yards_to_square_feet():
    snippet = "Plot rates in Rau, Indore average ₹ 36,000 per sq yd."
    [found] = await find(
        searching(result("Plot rates in Rau, Indore", snippet)),
        claiming(rate(snippet, average="₹ 36,000")),
        locality="Rau",
    )
    assert (found.unit, found.average, found.per_sqft(found.average)) == ("sqyd", 36_000, 4_000)


async def test_keeps_a_bigha_rate_as_written():
    snippet = "Agricultural land rates in Rau, Indore average ₹ 25 lakh per bigha."
    [found] = await find(
        searching(result("Land rates in Rau, Indore", snippet)),
        claiming(rate(snippet, average="₹ 25 lakh")),
        locality="Rau",
    )
    # A bigha's size differs by state, so there is no honest per-sq-ft figure.
    assert (found.unit, found.average, found.per_sqft(found.average)) == ("bigha", 2_500_000, None)
    assert found.summary == "₹25 L per bigha average"


async def test_labels_a_government_registry_rate():
    snippet = "The average registry rate for plots in Rau, Indore is ₹ 2,399 per sq ft."
    [found] = await find(
        searching(result("Plots for sale in Rau, Indore", snippet)),
        claiming(rate(snippet, average="₹ 2,399")),
        locality="Rau",
    )
    assert found.basis == "registry rate"


async def test_one_rate_per_page_and_at_most_three():
    pages = [
        result(
            f"Plots in Vijay Nagar, Indore #{n}",
            f"The average price per sqft for Plots in Vijay Nagar, Indore is Rs. {n},000.",
            link=f"https://portal{n}.test",
        )
        for n in (10, 11, 12, 13)
    ]
    claims = [
        rate(
            f"The average price per sqft for Plots in Vijay Nagar, Indore is Rs. {n},000.",
            index,
            average=f"Rs. {n},000",
        )
        for index, n in [(0, 10), (0, 10), (1, 11), (2, 12), (3, 13)]
    ]
    found = await find(searching(*pages), claiming(*claims))
    assert [item.average for item in found] == [10_000, 11_000, 12_000]


# --- refused -----------------------------------------------------------------


async def test_refuses_a_single_listings_price():
    quote = "Vijay Nagar , Indore · ₹2.6 Cr. ₹13,000 /sqft · 2,000 sqft(186 sqm)"
    assert (
        await find(searching(result(LISTING_TITLE, LISTING)), claiming(rate(quote, average="₹13,000"))) == []
    )


async def test_refuses_a_quote_the_model_wrote_itself():
    invented = "Plots in Vijay Nagar, Indore average Rs. 11,048 per sqft."
    model = claiming(rate(invented, average="Rs. 11,048"))
    assert await find(searching(result(HOUSING_TITLE, HOUSING)), model) == []


async def test_refuses_rates_for_another_city():
    snippet = "The average price per sqft for Plots in Vijay Nagar, Jaipur is Rs. 6,100."
    model = claiming(rate(snippet, average="Rs. 6,100"))
    assert await find(searching(result("Plots in Vijay Nagar, Jaipur", snippet)), model) == []


async def test_refuses_an_amount_that_is_not_in_the_quote():
    quote = "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048."
    model = claiming(rate(quote, average="Rs. 12,500"))
    assert await find(searching(result(HOUSING_TITLE, HOUSING)), model) == []


async def test_accepts_amounts_the_model_wrote_with_commas_or_currency():
    quote = "Flat prices in Palasia, Indore are in the range of Rs 6400-11500 per square feet (sq ft)."
    model = claiming(rate(quote, low="Rs 6,400", high="Rs 11,500"))
    [found] = await find(searching(result(RAU_TITLE, quote)), model, locality="Palasia", kind="flat")
    assert (found.kind, found.low, found.high) == ("flat", 6_400, 11_500)


async def test_refuses_an_amount_that_is_only_part_of_a_number_in_the_quote():
    quote = "Flat prices in Palasia, Indore are in the range of Rs 6400-11500 per square feet (sq ft)."
    model = claiming(rate(quote, low="Rs 6400", high="Rs 1500"))
    assert await find(searching(result(RAU_TITLE, quote)), model, locality="Palasia", kind="flat") == []


async def test_refuses_a_quote_naming_two_units():
    snippet = "Plot rates in Vijay Nagar, Indore average ₹ 12,000 per sq ft (₹ 1,29,000 per sq m)."
    model = claiming(rate(snippet, average="₹ 12,000"))
    assert await find(searching(result(HOUSING_TITLE, snippet)), model) == []


async def test_refuses_an_implausible_rate():
    snippet = "Plot rates in Vijay Nagar, Indore average ₹ 5 per sq ft."
    assert await find(searching(result(HOUSING_TITLE, snippet)), claiming(rate(snippet, average="₹ 5"))) == []


async def test_survives_a_model_that_answers_with_prose():
    model = ScriptedChatModel(script=[Reply("Land there is quite expensive.")])
    assert await find(searching(result(HOUSING_TITLE, HOUSING)), model) == []


# --- switched off and cached -------------------------------------------------


async def test_is_off_unless_switched_on_and_configured():
    model = claiming()
    assert (
        await AreaRateFinder(searching(), model, enabled=False).find("Vijay Nagar", "Indore", "land") is None
    )
    unconfigured = LocalitySearch(api_key=None, engine_id=None)
    assert (
        await AreaRateFinder(unconfigured, model, enabled=True).find("Vijay Nagar", "Indore", "land") is None
    )
    assert model.requests == []


async def test_asks_once_per_area_and_kind():
    quote = "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048."
    model = claiming(rate(quote, average="Rs. 11,048"))
    finder = AreaRateFinder(searching(result(HOUSING_TITLE, HOUSING)), model, enabled=True)

    first = await finder.find("Vijay Nagar", "Indore", "land")
    second = await finder.find("Vijay Nagar", "Indore", "land")

    assert first == second
    assert len(model.requests) == 1
