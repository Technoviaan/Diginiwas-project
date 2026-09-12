"""Quoting a locality trend from the web, and refusing to when it isn't supported."""

import json

import httpx
import pytest

from app.insights import ComparablesFinder, LocalitySearch, SnapshotService, WebTrendEstimator
from app.insights.trend import yearly_rate
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, Reply, ScriptedChatModel, make_listing

pytestmark = pytest.mark.anyio

# A real snippet shape: portals publish the total change over a period.
SNIPPET = (
    "In terms of price appreciation/depreciation, flat rates in Borkhera, Kota "
    "changed by, 14.8 % in the last 3 years, 44.2 % in the last 5 year"
)


def result(title: str, snippet: str, link: str = "https://99acres.test/1") -> dict:
    return {"title": title, "snippet": snippet, "link": link, "displayLink": "99acres.test"}


def searching(*items: dict) -> LocalitySearch:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(items)})

    return LocalitySearch(api_key="key", engine_id="cx", transport=httpx.MockTransport(handle))


def claiming(**payload) -> ScriptedChatModel:
    return ScriptedChatModel(script=[Reply(json.dumps(payload))])


def estimator(search: LocalitySearch, model: ScriptedChatModel, **options) -> WebTrendEstimator:
    return WebTrendEstimator(search, model, enabled=options.pop("enabled", True), **options)


def rates_page(snippet: str = SNIPPET) -> LocalitySearch:
    return searching(result("Property Rates in Borkhera, Kota 2026", snippet))


# --- the arithmetic the model is not allowed to do ---------------------------


@pytest.mark.parametrize(
    ("total", "years", "expected"),
    [(14.8, 3, 4.7), (44.2, 5, 7.6), (0.0, 3, 0.0), (-15.0, 3, -5.3)],
)
def test_a_total_over_years_compounds_into_a_yearly_rate(total, years, expected):
    # 44.2% over 5 years is 7.6% a year, not 44.2/5 = 8.8%.
    assert yearly_rate(total, years) == pytest.approx(expected, abs=0.05)


# --- extraction --------------------------------------------------------------


async def test_is_off_unless_switched_on():
    model = claiming(found=True, total_percent=14.8, years=3, quote=SNIPPET, source=0)
    off = estimator(rates_page(), model, enabled=False)

    assert off.enabled is False
    assert await off.estimate("Borkhera", "Kota") is None
    assert model.requests == []  # the model was never asked


async def test_is_off_without_google_credentials():
    search = LocalitySearch(api_key=None, engine_id=None)
    assert estimator(search, claiming(found=False)).enabled is False


async def test_quotes_a_portal_rates_page():
    model = claiming(found=True, total_percent=14.8, years=3, quote=SNIPPET, source=0)
    estimate = await estimator(rates_page(), model).estimate("Borkhera", "Kota")

    assert estimate.total_percent == 14.8
    assert estimate.years == 3
    assert estimate.yearly_percent == 4.7  # compounded here, not by the model
    assert estimate.period == "Past 3 years"
    assert estimate.quote == SNIPPET
    assert estimate.source_name == "99acres.test"


async def test_searches_the_way_rates_pages_are_written():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [result("Rates", SNIPPET)]})

    search = LocalitySearch(api_key="key", engine_id="cx", transport=httpx.MockTransport(handle))
    model = claiming(found=True, total_percent=14.8, years=3, quote=SNIPPET, source=0)
    await estimator(search, model).estimate("Borkhera", "Kota")

    assert requests[0].url.params["q"] == "Borkhera Kota property rates price appreciation last 5 years"


async def test_refuses_a_figure_the_model_made_up():
    # The "quote" never appears in the snippet.
    model = claiming(found=True, total_percent=14.8, years=3, quote="Borkhera prices grew 14.8%.", source=0)
    assert await estimator(rates_page(), model).estimate("Borkhera", "Kota") is None


async def test_refuses_a_snippet_about_another_city():
    elsewhere = "Flat rates in Borkhera, Jaipur changed by 20 % in the last 3 years"
    model = claiming(found=True, total_percent=20.0, years=3, quote=elsewhere, source=0)
    search = searching(result("Jaipur property rates", elsewhere))
    assert await estimator(search, model).estimate("Borkhera", "Kota") is None


@pytest.mark.parametrize(("total", "years"), [(900.0, 3), (14.8, 0), (14.8, 25)])
async def test_refuses_an_implausible_claim(total, years):
    snippet = f"Borkhera Kota rates changed by {total} % in the last {years} years"
    model = claiming(found=True, total_percent=total, years=years, quote=snippet, source=0)
    assert await estimator(searching(result("x", snippet)), model).estimate("Borkhera", "Kota") is None


async def test_accepts_that_no_snippet_states_a_trend():
    model = claiming(found=False)
    search = searching(result("Flats for sale in Borkhera Kota", "3 BHK flats from ₹40 L."))
    assert await estimator(search, model).estimate("Borkhera", "Kota") is None


async def test_survives_a_model_that_answers_with_prose():
    model = ScriptedChatModel(script=[Reply("Prices went up quite a lot, I think.")])
    assert await estimator(rates_page(), model).estimate("Borkhera", "Kota") is None


async def test_asks_once_per_locality_and_reuses_the_answer():
    model = claiming(found=True, total_percent=14.8, years=3, quote=SNIPPET, source=0)
    client = estimator(rates_page(), model)

    first = await client.estimate("Borkhera", "Kota")
    second = await client.estimate("Borkhera", "Kota")

    assert first == second
    assert len(model.requests) == 1  # the second answer came from the cache


# --- what reaches the card ---------------------------------------------------


def snapshot_service(trend: WebTrendEstimator | None) -> SnapshotService:
    api = FakePropertiesAPI(
        [make_listing("DW-1", price=6_000_000, size=1100, locality="Borkhera", city="Kota")]
    )
    client = PropertiesClient(base_url="https://properties.test", timeout=5, transport=api.transport)
    return SnapshotService(client, ComparablesFinder(client), trend=trend)


async def test_the_card_carries_the_quote_the_source_and_low_confidence():
    model = claiming(found=True, total_percent=14.8, years=3, quote=SNIPPET, source=0)
    service = snapshot_service(estimator(rates_page(), model))

    trend = (await service.for_listing("DW-1")).locality_trend
    assert trend.available is True
    assert trend.headline == "+4.7% yearly"
    assert trend.caption == "Past 3 years"
    assert (trend.total_percent, trend.years) == (14.8, 3)
    assert (trend.source, trend.confidence) == ("web", "Low")
    assert trend.quote == SNIPPET


async def test_the_card_says_not_available_when_nothing_checks_out():
    service = snapshot_service(estimator(searching(), claiming(found=False)))

    trend = (await service.for_listing("DW-1")).locality_trend
    assert trend.available is False
    assert (trend.source, trend.yearly_percent) == (None, None)
    assert "price history" in trend.reason
