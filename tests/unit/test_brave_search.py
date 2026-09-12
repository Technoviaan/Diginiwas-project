"""Brave as the search provider, and choosing a provider from settings."""

import httpx
import pytest

from app.core.config import Settings
from app.insights import LocalitySearch
from app.insights.websearch import build_locality_search

BRAVE_RESULTS = {
    "web": {
        "results": [
            {
                "title": "Property Rates in <strong>Borkhera</strong>, Kota 2026",
                "url": "https://www.99acres.com/property-rates-and-price-trends-in-borkhera-kota",
                "description": (
                    "Flat rates in <strong>Borkhera</strong>, Kota changed by 14.8 % in the "
                    "last 3 years &amp; 44.2 % in the last 5 years."
                ),
                "meta_url": {"hostname": "www.99acres.com"},
            },
            {"title": "A result without a link is skipped"},
        ]
    }
}


def brave(handler, **options) -> LocalitySearch:
    return LocalitySearch(
        provider="brave",
        api_key=options.pop("api_key", "brave-test-key"),
        transport=httpx.MockTransport(handler),
        **options,
    )


def test_brave_needs_only_an_api_key():
    assert LocalitySearch(provider="brave", api_key="key").enabled is True
    assert LocalitySearch(provider="brave", api_key=None).enabled is False
    # Google, by contrast, needs a search engine id too.
    assert LocalitySearch(provider="google", api_key="key").enabled is False


@pytest.mark.anyio
async def test_sends_the_key_as_a_header_never_in_the_url():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=BRAVE_RESULTS)

    await brave(handle).sources("Borkhera", "Kota")

    [request] = requests
    assert request.headers["X-Subscription-Token"] == "brave-test-key"
    assert "brave-test-key" not in str(request.url)
    assert request.url.params["q"] == "Borkhera Kota property price trend"
    assert request.url.params["count"] == "3"


@pytest.mark.anyio
async def test_turns_results_into_plain_text_sources():
    sources = await brave(lambda request: httpx.Response(200, json=BRAVE_RESULTS)).sources("Borkhera", "Kota")

    [source] = sources  # the result without a link was skipped
    assert source.title == "Property Rates in Borkhera, Kota 2026"
    assert source.snippet == (
        "Flat rates in Borkhera, Kota changed by 14.8 % in the last 3 years & 44.2 % in the last 5 years."
    )
    assert source.source == "www.99acres.com"
    assert source.url.startswith("https://www.99acres.com/")


@pytest.mark.anyio
async def test_a_brave_failure_costs_only_the_sources(caplog):
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"type": "ErrorResponse", "error": {"detail": "Rate limited"}})

    assert await brave(fail).sources("Borkhera", "Kota") == []
    assert "HTTP 429 - Rate limited" in caplog.text
    assert "brave-test-key" not in caplog.text


@pytest.mark.anyio
async def test_settings_choose_the_provider():
    chosen = build_locality_search(
        Settings(_env_file=None, search_provider="brave", brave_search_api_key="key")
    )
    google = build_locality_search(
        Settings(
            _env_file=None,
            search_provider="google",
            google_search_api_key="key",
            google_search_engine_id="cx",
        )
    )
    try:
        assert (chosen.provider, chosen.enabled) == ("brave", True)
        assert (google.provider, google.enabled) == ("google", True)
    finally:
        await chosen.aclose()
        await google.aclose()
