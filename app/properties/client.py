"""Async client for DigiNiwas' GET /api/properties."""

from __future__ import annotations

import logging

import httpx

from app.properties.mapper import to_card
from app.properties.models import PropertyCard, PropertyPage
from app.properties.query import PropertyQuery

logger = logging.getLogger(__name__)

# How many listings to re-read when the API's own filters find nothing. Enough
# to cover the whole catalogue today, and bounded so a growing one can't make
# this call expensive.
UNFILTERED_LIMIT = 100


class PropertiesAPIError(Exception):
    """The API answered, but not with a usable listings response."""


class PropertiesClient:
    """One per process: it holds a connection pool. Close it on shutdown."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        url_template: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url_template = url_template
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )

    async def search(self, query: PropertyQuery, *, limit: int) -> PropertyPage:
        """One page of live, verified listings matching `query`.

        An empty result is checked a second time against listings fetched
        unfiltered: the API can't filter on fields newer listings keep in a
        nested object, so it reports none where matches exist.

        Raises httpx.HTTPError for network and HTTP failures, and
        PropertiesAPIError for a response that isn't a listing page.
        """
        page = await self._page(query, limit)
        if page.cards or not query.has_filters:
            return page
        return await self._matched_here(query, limit)

    async def _matched_here(self, query: PropertyQuery, limit: int) -> PropertyPage:
        """Fetch a wide unfiltered page and apply the filters in code."""
        wide = await self._page(PropertyQuery(page=query.page), UNFILTERED_LIMIT)
        matched = [card for card in wide.cards if query.matches(card)]
        if matched:
            logger.info(
                "The listings API returned none for %s; matched %d of %d here instead",
                query.to_params(limit),
                len(matched),
                len(wide.cards),
            )
        return PropertyPage(cards=matched[:limit], total=len(matched), page=query.page, total_pages=1)

    async def _page(self, query: PropertyQuery, limit: int) -> PropertyPage:
        response = await self._http.get("/api/properties", params=query.to_params(limit))
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not body.get("success"):
            message = body.get("message") if isinstance(body, dict) else None
            raise PropertiesAPIError(message or "unsuccessful response")

        cards: list[PropertyCard] = []
        for raw in body.get("data") or body.get("properties") or []:
            try:
                cards.append(to_card(raw, url_template=self._url_template))
            except Exception:  # one bad listing must not sink the whole search
                listing = raw.get("propertyId") if isinstance(raw, dict) else raw
                logger.warning("Skipping malformed listing %s", listing, exc_info=True)

        pagination = body.get("pagination") or {}
        return PropertyPage(
            cards=cards,
            total=int(body.get("total", len(cards))),
            page=int(pagination.get("currentPage", query.page)),
            total_pages=pagination.get("totalPages"),
        )

    async def find_by_id(self, property_id: str) -> PropertyCard | None:
        """The live, verified listing with this ID ('DW-1003'), or None.

        The API has no lookup by ID, so this searches for the ID and keeps the
        exact match. Raises like `search`.
        """
        wanted = property_id.strip().casefold()
        if not wanted:
            return None
        page = await self.search(PropertyQuery(search=property_id.strip()), limit=10)
        return next((card for card in page.cards if card.id.casefold() == wanted), None)

    async def aclose(self) -> None:
        await self._http.aclose()
