"""Serper as the search provider: Google's results through one API key."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.insights import LocalitySearch
from app.insights.websearch import build_locality_search

SERPER_RESULTS = {
    "searchParameters": {"q": "Borkhera Kota property rates", "gl": "in"},
    "organic": [
        {
            "title": "Property Rates in Borkhera, Kota 2026 - 99acres.com",
            "link": "https://www.99acres.com/property-rates-and-price-trends-in-borkhera-kota",
            "snippet": (
                "In terms of price appreciation/depreciation, flat rates in Borkhera, Kota "
                "changed by, 14.8 % in the last 3 years, 44.2 % in the last 5 year"
            ),
            "position": 1,
        },
        {"title": "A result without a link is skipped", "position": 2},
    ],
}


def serper(handler, **options) -> LocalitySearch:
    return LocalitySearch(
        provider="serper",
        api_key=options.pop("api_key", "serper-test-key"),
        transport=httpx.MockTransport(handler),
        **options,
    )


def test_serper_needs_only_an_api_key():
    assert LocalitySearch(provider="serper", api_key="key").enabled is True
    assert LocalitySearch(provider="serper", api_key=None).enabled is False


@pytest.mark.anyio
async def test_posts_the_query_with_the_key_in_a_header():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=SERPER_RESULTS)

    await serper(handle).sources("Borkhera", "Kota", query="Borkhera Kota property rates")

    [request] = requests
    assert request.method == "POST"
    assert request.url == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == "serper-test-key"
    assert "serper-test-key" not in str(request.url)
    assert json.loads(request.content) == {
        "q": "Borkhera Kota property rates",
        "num": 3,
        "gl": "in",
        "hl": "en",
    }


@pytest.mark.anyio
async def test_turns_organic_results_into_sources():
    sources = await serper(lambda request: httpx.Response(200, json=SERPER_RESULTS)).sources(
        "Borkhera", "Kota"
    )

    [source] = sources  # the result without a link was skipped
    assert source.title == "Property Rates in Borkhera, Kota 2026 - 99acres.com"
    assert "44.2 % in the last 5 year" in source.snippet
    assert source.source == "www.99acres.com"


@pytest.mark.anyio
async def test_reports_serpers_own_error_message_without_the_key(caplog):
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Unauthorized.", "statusCode": 403})

    assert await serper(reject).sources("Borkhera", "Kota") == []
    assert "HTTP 403 - Unauthorized." in caplog.text
    assert "serper-test-key" not in caplog.text


@pytest.mark.anyio
async def test_serper_and_the_web_trend_are_the_defaults():
    settings = Settings(_env_file=None, serper_api_key="key")
    search = build_locality_search(settings)
    try:
        assert (settings.search_provider, settings.web_trend_enabled) == ("serper", True)
        assert search.enabled is True
    finally:
        await search.aclose()


@pytest.mark.anyio
async def test_nothing_is_searched_without_a_key():
    search = build_locality_search(Settings(_env_file=None))
    try:
        assert search.enabled is False
        assert await search.sources("Borkhera", "Kota") == []
    finally:
        await search.aclose()


@pytest.mark.anyio
async def test_settings_can_choose_serper():
    search = build_locality_search(Settings(_env_file=None, search_provider="serper", serper_api_key="key"))
    try:
        assert (search.provider, search.enabled) == ("serper", True)
    finally:
        await search.aclose()
