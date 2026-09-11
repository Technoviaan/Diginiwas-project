import httpx
import pytest

from app.properties import PropertiesAPIError, PropertyQuery

pytestmark = pytest.mark.anyio


async def test_returns_a_page_and_skips_malformed_listings(properties_client):
    page = await properties_client.search(PropertyQuery(), limit=10)
    assert [card.id for card in page.cards] == ["DW-1003", "DW-2001", "DW-3001"]
    assert (page.total, page.page) == (4, 1)


async def test_sends_filters_as_query_parameters(properties_client, properties_api):
    await properties_client.search(PropertyQuery(city="Indore", bedrooms=3), limit=6)
    assert properties_api.last_params == {"page": "1", "limit": "6", "city": "Indore", "bedrooms": "3"}


async def test_unsuccessful_response_raises(properties_client, properties_api):
    properties_api.body = {"success": False, "message": "maintenance"}
    with pytest.raises(PropertiesAPIError, match="maintenance"):
        await properties_client.search(PropertyQuery(), limit=6)


async def test_http_error_raises(properties_client, properties_api):
    properties_api.status_code = 503
    with pytest.raises(httpx.HTTPStatusError):
        await properties_client.search(PropertyQuery(), limit=6)
