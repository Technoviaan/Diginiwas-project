"""Locality pages from web search: Google Programmable Search or Brave.

Search returns links and snippets, not market data, so these are shown as
*sources* next to the snapshot, and the trend estimator only ever quotes
from them. Results are cached per query, because free tiers are small
(Google: 100 queries a day; Serper: 2,500 once, on signup).

SEARCH_PROVIDER picks the service:

  google  GOOGLE_SEARCH_API_KEY    an API key with the Custom Search API enabled
          GOOGLE_SEARCH_ENGINE_ID  a Programmable Search Engine id (the `cx`)
  serper  SERPER_API_KEY           a key from serper.dev - Google's own results
  brave   BRAVE_SEARCH_API_KEY     a key from api-dashboard.search.brave.com

Without the chosen provider's settings the service is simply off, and the
snapshot has no sources and no quoted trend.
"""

from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.core.config import Settings
from app.insights.models import LocalitySource

logger = logging.getLogger(__name__)

Provider = Literal["google", "brave", "serper"]

GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"
# Brave returns at most this many results per request.
BRAVE_MAX_RESULTS = 20
CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_CACHE_ENTRIES = 512

_HTML_TAG = re.compile(r"<[^>]+>")


class LocalitySearch:
    def __init__(
        self,
        *,
        api_key: str | None,
        engine_id: str | None = None,
        provider: Provider = "google",
        limit: int = 3,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._engine_id = engine_id
        self._provider = provider
        self._limit = limit
        self._http = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cache: dict[str, tuple[float, list[LocalitySource]]] = {}

    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def enabled(self) -> bool:
        if self._provider in ("brave", "serper"):
            return bool(self._api_key)
        return bool(self._api_key and self._engine_id)

    async def sources(
        self, locality: str | None, city: str | None, *, query: str | None = None
    ) -> list[LocalitySource]:
        """Pages about this locality's property market. Never raises.

        `query` overrides the default wording - the trend estimator asks the
        way portals phrase their price-rate pages.
        """
        if not self.enabled or not (locality or city):
            return []

        query = query or " ".join(part for part in [locality, city, "property price trend"] if part)
        cached = self._cached(query)
        if cached is not None:
            # Not `if cached:` - a locality with no results must stay cached,
            # or it would cost a query on every request.
            return cached

        try:
            response = await self._request(query)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # Never log the exception itself: its message carries the request
            # URL, and Google's API key is in it.
            logger.warning(
                "Locality search (%s) failed for %r: HTTP %s %s",
                self._provider,
                query,
                exc.response.status_code,
                _reason(exc.response),
            )
            return []
        except (httpx.HTTPError, ValueError) as exc:
            # Sources are a nice-to-have: a snapshot is still useful without them.
            logger.warning(
                "Locality search (%s) failed for %r: %s", self._provider, query, type(exc).__name__
            )
            return []

        sources = self._parse(payload)[: self._limit]
        self._remember(query, sources)
        return sources

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, query: str) -> httpx.Response:
        if self._provider == "serper":
            return await self._http.post(
                SERPER_ENDPOINT,
                # India, in English: where the portals that publish locality rates rank.
                json={"q": query, "num": self._limit, "gl": "in", "hl": "en"},
                # A header, like Brave, so the key never appears in a URL.
                headers={"X-API-KEY": self._api_key or ""},
            )
        if self._provider == "brave":
            return await self._http.get(
                BRAVE_ENDPOINT,
                params={"q": query, "count": min(self._limit, BRAVE_MAX_RESULTS)},
                # Brave takes the key as a header, so it never appears in a URL.
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key or ""},
            )
        return await self._http.get(
            GOOGLE_ENDPOINT,
            params={"key": self._api_key, "cx": self._engine_id, "q": query, "num": self._limit},
        )

    def _parse(self, payload: Any) -> list[LocalitySource]:
        if not isinstance(payload, dict):
            return []
        if self._provider == "serper":
            organic = payload.get("organic") or []
            return [_from_serper(item) for item in organic if isinstance(item, dict) and item.get("link")]
        if self._provider == "brave":
            web = payload.get("web") or {}
            results = web.get("results") or [] if isinstance(web, dict) else []
            return [_from_brave(item) for item in results if isinstance(item, dict) and item.get("url")]
        return [_from_google(item) for item in payload.get("items") or [] if isinstance(item, dict)]

    def _cached(self, query: str) -> list[LocalitySource] | None:
        entry = self._cache.get(query)
        if entry is None:
            return None
        stored_at, sources = entry
        if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
            del self._cache[query]
            return None
        return sources

    def _remember(self, query: str, sources: list[LocalitySource]) -> None:
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[query] = (time.monotonic(), sources)


def build_locality_search(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> LocalitySearch:
    """The search client for whichever provider SEARCH_PROVIDER names."""
    if settings.search_provider == "serper":
        return LocalitySearch(
            provider="serper",
            api_key=settings.serper_api_key,
            limit=settings.locality_sources_limit,
            transport=transport,
        )
    if settings.search_provider == "brave":
        return LocalitySearch(
            provider="brave",
            api_key=settings.brave_search_api_key,
            limit=settings.locality_sources_limit,
            transport=transport,
        )
    return LocalitySearch(
        provider="google",
        api_key=settings.google_search_api_key,
        engine_id=settings.google_search_engine_id,
        limit=settings.locality_sources_limit,
        transport=transport,
    )


def _reason(response: httpx.Response) -> str:
    """The provider's explanation for a rejected request, without the request URL."""
    try:
        body = response.json()
    except ValueError:
        return ""
    # Shapes vary: Google {"error": {"message": ...}}, Brave {"error": {"detail": ...}},
    # Serper {"message": ..., "statusCode": 403}, a bare {"error": "quota"}, or none.
    error = body.get("error") if isinstance(body, dict) else None
    message = (error.get("message") or error.get("detail")) if isinstance(error, dict) else error
    if not message and isinstance(body, dict):
        message = body.get("message")
    return f"- {str(message)[:200]}" if message else ""


def _from_google(item: dict[str, Any]) -> LocalitySource:
    return LocalitySource(
        title=item.get("title") or item.get("link", ""),
        url=item.get("link", ""),
        snippet=item.get("snippet"),
        source=item.get("displayLink"),
    )


def _from_serper(item: dict[str, Any]) -> LocalitySource:
    return LocalitySource(
        title=item.get("title") or item["link"],
        url=item["link"],
        snippet=item.get("snippet"),
        source=urlparse(item["link"]).hostname,
    )


def _from_brave(item: dict[str, Any]) -> LocalitySource:
    meta = item.get("meta_url")
    return LocalitySource(
        title=_plain(item.get("title")) or item["url"],
        url=item["url"],
        snippet=_plain(item.get("description")) or None,
        source=meta.get("hostname") if isinstance(meta, dict) else None,
    )


def _plain(text: str | None) -> str:
    """Brave marks matches with <strong> and escapes entities.

    The trend estimator checks quotes against the snippet character for
    character, so the snippet has to be the plain text the model reads.
    """
    return html.unescape(_HTML_TAG.sub("", text)).strip() if text else ""
