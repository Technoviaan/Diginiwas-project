"""The chat model behind the assistant."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import Settings


def build_chat_model(settings: Settings) -> ChatOpenAI:
    options: dict[str, Any] = {"model": settings.model, "streaming": True}
    if settings.resolved_api_key:
        options["api_key"] = settings.resolved_api_key
    if settings.openai_base_url:
        options["base_url"] = settings.openai_base_url

    if settings.reasoning_effort:
        # Reasoning models (o-series, gpt-5 family) take reasoning_effort and
        # reject temperature. Regular chat models are the other way round.
        options["reasoning_effort"] = settings.reasoning_effort
        options["max_completion_tokens"] = settings.max_tokens
    else:
        options["temperature"] = settings.temperature
        options["max_tokens"] = settings.max_tokens

    return ChatOpenAI(**options)
