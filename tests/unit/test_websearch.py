"""Locality sources from Google Programmable Search."""

import httpx
import pytest

from app.insights import LocalitySearch
from tests.fakes import google_search_transport

pytestmark = pytest.mark.anyio


def search(transport=None, **options) -> LocalitySearch:
    return LocalitySearch(
        api_key=options.pop("api_key", "test-key"),
        engine_id=options.pop("engine_id", "test-cx"),
        transport=transport,
        **options,
    )


async def test_is_off_without_credentials():
    off = search(api_key=None, engine_id=None)
    assert off.enabled is False
    assert await off.sources("Vijay Nagar", "Indore") == []


async def test_turns_results_into_sources():
    client = search(google_search_transport("Vijay Nagar property rates rise"))
    [source] = await client.sources("Vijay Nagar", "Indore")
    assert source.title == "Vijay Nagar property rates rise"
    assert source.url == "https://news.test/0"
    assert source.source == "news.test"


async def test_asks_google_once_per_locality():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    client = search(httpx.MockTransport(handle))
    await client.sources("Vijay Nagar", "Indore")
    await client.sources("Vijay Nagar", "Indore")

    assert len(requests) == 1
    assert requests[0].url.params["q"] == "Vijay Nagar Indore property price trend"
    assert requests[0].url.params["cx"] == "test-cx"


async def test_a_search_failure_costs_only_the_sources():
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "quota"})

    assert await search(httpx.MockTransport(fail)).sources("Vijay Nagar", "Indore") == []


async def test_no_locality_means_no_search():
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": []})

    assert await search(httpx.MockTransport(handle)).sources(None, None) == []
    assert requests == []
