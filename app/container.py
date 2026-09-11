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
from app.properties import PropertiesClient


@dataclass
class Services:
    settings: Settings
    sessions: SessionStore
    properties: PropertiesClient
    agent: ChatAgent

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        chat_model: BaseChatModel | None = None,
        properties_transport: httpx.AsyncBaseTransport | None = None,
    ) -> Services:
        """Wire the real implementations.

        `chat_model` and `properties_transport` replace OpenAI and the listings
        API - tests use them - while everything else stays real.
        """
        sessions = InMemorySessionStore(max_sessions=settings.max_sessions)
        properties = PropertiesClient(
            base_url=settings.properties_api_base_url,
            timeout=settings.properties_api_timeout,
            url_template=settings.property_url_template,
            transport=properties_transport,
        )
        agent = ChatAgent(
            chat_model=chat_model if chat_model is not None else build_chat_model(settings),
            tools=[build_property_search_tool(properties, page_size=settings.max_property_results)],
            sessions=sessions,
            system_prompt=settings.system_prompt or SYSTEM_PROMPT,
            history_window=settings.history_window,
            max_tool_rounds=settings.max_tool_rounds,
        )
        return cls(settings=settings, sessions=sessions, properties=properties, agent=agent)

    async def aclose(self) -> None:
        await self.properties.aclose()
