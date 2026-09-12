#!/usr/bin/env python
"""Check the web search settings, and see what a locality trend would say.

    python scripts/check_search.py Borkhera Kota

Reads SEARCH_PROVIDER and that provider's key from .env, runs one real
search, and - when WEB_TREND_ENABLED is on - shows what the extractor makes
of the snippets and whether the result passes its checks.

Never prints the keys themselves.
"""

from __future__ import annotations

import asyncio
import sys

from app.assistant.llm import build_chat_model
from app.core.config import get_settings
from app.insights import WebTrendEstimator
from app.insights.trend import TREND_QUERY
from app.insights.websearch import build_locality_search

SETUP_HELP = {
    "google": (
        "Google needs both of these in .env:\n"
        "  GOOGLE_SEARCH_API_KEY=...      (Console > Credentials > API key,\n"
        "                                  with the Custom Search API enabled)\n"
        "  GOOGLE_SEARCH_ENGINE_ID=...    (programmablesearchengine.google.com,\n"
        "                                  the search engine's cx value)"
    ),
    "serper": (
        "Serper needs this in .env:\n"
        "  SERPER_API_KEY=...             (serper.dev > API Key; 2,500 free\n"
        "                                  queries on signup, no card)"
    ),
    "brave": (
        "Brave needs this in .env:\n"
        "  BRAVE_SEARCH_API_KEY=...       (api-dashboard.search.brave.com >\n"
        "                                  API Keys, on any plan)"
    ),
}


async def main(locality: str, city: str) -> int:
    settings = get_settings()
    search = build_locality_search(settings)

    print(f"Search provider       : {settings.search_provider}")
    if settings.search_provider == "serper":
        print(f"Serper API key        : {_state(settings.serper_api_key)}")
    elif settings.search_provider == "brave":
        print(f"Brave API key         : {_state(settings.brave_search_api_key)}")
    else:
        print(f"Custom Search API key : {_state(settings.google_search_api_key)}")
        print(f"Search engine id (cx) : {_state(settings.google_search_engine_id)}")
    print(f"WEB_TREND_ENABLED     : {settings.web_trend_enabled}")

    if not search.enabled:
        print(f"\n{SETUP_HELP[settings.search_provider]}")
        await search.aclose()
        return 1

    query = TREND_QUERY.format(locality=locality, city=city)
    print(f"\nSearching: {query}")
    sources = await search.sources(locality, city, query=query)

    if not sources:
        print(
            "No results. The key may be wrong or out of quota - the warning above\n"
            "has the provider's own explanation."
        )
        await search.aclose()
        return 1

    for index, source in enumerate(sources):
        print(f"\n[{index}] {source.title}  ({source.source})")
        print(f"    {source.snippet}")
        print(f"    {source.url}")

    if not settings.web_trend_enabled:
        print("\nWEB_TREND_ENABLED is off, so the card would show no trend.")
    elif not settings.resolved_api_key:
        print("\nOPENAI_API_KEY is not set, so the snippets cannot be read.")
    else:
        estimator = WebTrendEstimator(
            search,
            # Temperature 0, as in the app: the same snippets must give the same figure.
            build_chat_model(settings.model_copy(update={"temperature": 0.0})),
            enabled=True,
            results=settings.locality_sources_limit,
        )
        estimate = await estimator.estimate(locality, city)
        print("\n--- what the card would show ---")
        if estimate is None:
            print("Not available yet: no snippet stated a trend that passed the checks.")
        else:
            sign = "+" if estimate.yearly_percent >= 0 else "−"
            print(f"{sign}{abs(estimate.yearly_percent)}% yearly   ({estimate.period})")
            print(f"total {estimate.total_percent}% over {estimate.years} years")
            print(f"source: {estimate.source_name} {estimate.source_url}")
            print(f'quote : "{estimate.quote}"')

    await search.aclose()
    return 0


def _state(value: str | None) -> str:
    return f"set ({len(value)} characters)" if value else "MISSING"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python scripts/check_search.py <locality> <city>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
