"""The service's object graph: built once at startup, closed at shutdown.

This is the only place that decides which implementation backs each part:
OpenAI for the model, the DigiNiwas HTTP API for listings, process memory
for sessions. Everything else receives its collaborators.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from langchain_core.language_models import BaseChatModel

from app.assistant import ChatAgent, InMemorySessionStore, SessionStore
from app.assistant.llm import build_chat_model
from app.assistant.prompts import SYSTEM_PROMPT
from app.assistant.tools import build_property_search_tool
from app.core.config import Settings
from app.insights import (
    ComparablesFinder,
    LocalitySearch,
    SnapshotService,
    WebRentEstimator,
    WebTrendEstimator,
)
from app.insights.websearch import build_locality_search
from app.properties import PropertiesClient


@dataclass
class Services:
    settings: Settings
    sessions: SessionStore
    properties: PropertiesClient
    agent: ChatAgent
    snapshots: SnapshotService
    locality_search: LocalitySearch

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        chat_model: BaseChatModel | None = None,
        properties_transport: httpx.AsyncBaseTransport | None = None,
        search_transport: httpx.AsyncBaseTransport | None = None,
    ) -> Services:
        """Wire the real implementations.

        `chat_model`, `properties_transport` and `search_transport` replace
        OpenAI, the listings API and Google search - tests use them - while
        everything else stays real.
        """
        sessions = InMemorySessionStore(max_sessions=settings.max_sessions)
        properties = PropertiesClient(
            base_url=settings.properties_api_base_url,
            timeout=settings.properties_api_timeout,
            url_template=settings.property_url_template,
            transport=properties_transport,
        )
        model = chat_model if chat_model is not None else build_chat_model(settings)
        # Reading figures out of search snippets must give the same answer every
        # time, or a card would change between two refreshes: temperature 0.
        # (With REASONING_EFFORT set, build_chat_model sends no temperature.)
        extractor = (
            chat_model
            if chat_model is not None
            else build_chat_model(settings.model_copy(update={"temperature": 0.0}))
        )
        agent = ChatAgent(
            chat_model=model,
            tools=[build_property_search_tool(properties, page_size=settings.max_property_results)],
            sessions=sessions,
            system_prompt=settings.system_prompt or SYSTEM_PROMPT,
            history_window=settings.history_window,
            max_tool_rounds=settings.max_tool_rounds,
        )
        locality_search = build_locality_search(settings, transport=search_transport)
        snapshots = SnapshotService(
            properties,
            ComparablesFinder(
                properties,
                radius_km=settings.comparable_radius_km,
                area_tolerance=settings.comparable_area_tolerance,
                max_comparables=settings.max_comparables,
            ),
            search=locality_search,
            trend=WebTrendEstimator(
                locality_search,
                extractor,
                enabled=settings.web_trend_enabled,
                results=settings.locality_sources_limit,
            ),
            web_rent=WebRentEstimator(locality_search, extractor, enabled=settings.web_rent_enabled),
            vacancy_months=settings.rental_vacancy_months,
            radius_km=settings.comparable_radius_km,
        )
        return cls(
            settings=settings,
            sessions=sessions,
            properties=properties,
            agent=agent,
            snapshots=snapshots,
            locality_search=locality_search,
        )

    async def aclose(self) -> None:
        await self.properties.aclose()
        await self.locality_search.aclose()
