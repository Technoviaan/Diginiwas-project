"""Shared fixtures: settings isolated from `.env`, a scripted model, a fake listings API."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import BaseTool

from app.assistant import ChatAgent, InMemorySessionStore
from app.assistant.tools import build_property_search_tool
from app.container import Services
from app.core.config import Settings
from app.main import create_app
from app.properties import PropertiesClient
from tests.fakes import FakePropertiesAPI, ScriptedChatModel, load_listings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: the suite never reads a developer's .env.
    return Settings(
        _env_file=None,
        openai_api_key="sk-test",
        cors_origins=["https://diginiwas.com"],
        rate_limit_per_minute=0,
        properties_api_base_url="https://properties.test",
        log_level="WARNING",
    )


@pytest.fixture
def model() -> ScriptedChatModel:
    return ScriptedChatModel()


@pytest.fixture
def properties_api() -> FakePropertiesAPI:
    return FakePropertiesAPI()


@pytest.fixture
def listings() -> list[dict[str, Any]]:
    return load_listings()


@pytest.fixture
def properties_client(settings: Settings, properties_api: FakePropertiesAPI) -> PropertiesClient:
    return PropertiesClient(
        base_url=settings.properties_api_base_url,
        timeout=5,
        transport=properties_api.transport,
    )


@pytest.fixture
def property_tool(settings: Settings, properties_client: PropertiesClient) -> BaseTool:
    return build_property_search_tool(properties_client, page_size=settings.max_property_results)


@pytest.fixture
def sessions() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def build_agent(
    settings: Settings,
    model: ScriptedChatModel,
    property_tool: BaseTool,
    sessions: InMemorySessionStore,
) -> Callable[..., ChatAgent]:
    def build(**overrides: Any) -> ChatAgent:
        options: dict[str, Any] = {
            "chat_model": model,
            "tools": [property_tool],
            "sessions": sessions,
            "history_window": settings.history_window,
            "max_tool_rounds": settings.max_tool_rounds,
            **overrides,
        }
        return ChatAgent(**options)

    return build


@pytest.fixture
def agent(build_agent: Callable[..., ChatAgent]) -> ChatAgent:
    return build_agent()


@pytest.fixture
def make_api(
    settings: Settings, model: ScriptedChatModel, properties_api: FakePropertiesAPI
) -> Callable[..., Any]:
    """Run the real app, wired by the real container, on the fake model and API."""

    @contextmanager
    def make(*, raise_server_exceptions: bool = True, **overrides: Any) -> Iterator[TestClient]:
        app_settings = settings.model_copy(update=overrides)
        services = Services.build(
            app_settings, chat_model=model, properties_transport=properties_api.transport
        )
        app = create_app(app_settings, services=services)
        with TestClient(app, raise_server_exceptions=raise_server_exceptions) as client:
            yield client

    return make


@pytest.fixture
def api(make_api: Callable[..., Any]) -> Iterator[TestClient]:
    with make_api() as client:
        yield client
