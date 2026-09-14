"""POST /v1/chat and POST /v1/chat/stream."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.dependencies import AgentDep, SettingsDep
from app.api.errors import http_error_for
from app.api.v1 import openapi as docs
from app.api.v1.schemas import (
    ChatRequest,
    ChatResponse,
    DoneEvent,
    ErrorEvent,
    PropertiesEvent,
    SourcesEvent,
    StatusEvent,
    StreamEvent,
    TokenEvent,
    Usage,
)
from app.assistant import (
    AgentEvent,
    PropertiesFound,
    SourcesFound,
    Status,
    TextDelta,
    TokenUsage,
    TurnComplete,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat v1"])

SESSION_HEADER = "X-Session-ID"

ChatBody = Annotated[ChatRequest, Body(openapi_examples=docs.CHAT_REQUEST_EXAMPLES)]


class EventStreamResponse(StreamingResponse):
    """A StreamingResponse that OpenAPI documents as text/event-stream."""

    media_type = "text/event-stream"


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a message",
    description=docs.CHAT,
    responses={
        200: {
            "description": "The reply, and the cards and source links to show under it.",
            "headers": {**docs.SESSION_HEADER, **docs.VERSION_HEADERS},
            "content": {"application/json": {"examples": docs.CHAT_RESPONSE_EXAMPLES}},
        },
        **docs.CHAT_ERRORS,
    },
)
async def chat(req: ChatBody, response: Response, agent: AgentDep, settings: SettingsDep) -> ChatResponse:
    system_prompt = _prompt_override(req, settings)
    session_id = _session_id(req)
    response.headers[SESSION_HEADER] = session_id
    result = await agent.run(
        session_id, req.message, system_prompt=system_prompt, property_id=req.property_id
    )
    return ChatResponse(
        session_id=session_id,
        reply=result.reply,
        properties=result.properties,
        sources=result.sources,
        model=settings.model,
        usage=_usage(result.usage),
    )


@router.post(
    "/chat/stream",
    response_class=EventStreamResponse,
    summary="Send a message (streaming)",
    description=docs.CHAT_STREAM,
    responses={
        200: {
            "model": StreamEvent,
            "description": "Server-Sent Events. Each `data:` line is one StreamEvent.",
            "headers": {**docs.SESSION_HEADER, **docs.VERSION_HEADERS},
            "content": {"text/event-stream": {"examples": docs.SSE_EXAMPLES}},
        },
        403: docs.CHAT_ERRORS[403],
        429: docs.CHAT_ERRORS[429],
    },
)
async def chat_stream(
    req: ChatBody, request: Request, agent: AgentDep, settings: SettingsDep
) -> EventStreamResponse:
    # Checked before the stream opens, so a refusal is a real 403.
    system_prompt = _prompt_override(req, settings)
    session_id = _session_id(req)

    async def events() -> AsyncIterator[str]:
        try:
            stream = agent.stream(
                session_id, req.message, system_prompt=system_prompt, property_id=req.property_id
            )
            async with aclosing(stream) as agent_events:
                async for event in agent_events:
                    yield _sse(_to_stream_event(event, session_id))
                    if await request.is_disconnected():
                        break
        except Exception as exc:  # the 200 has already been sent: report the error in-band
            logger.warning("Streaming turn failed", exc_info=exc)
            yield _sse(ErrorEvent(type="error", message=http_error_for(exc).detail))

    # The header arrives before any event, so the app has the id even if the stream breaks.
    return EventStreamResponse(
        events(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", SESSION_HEADER: session_id},
    )


def _session_id(req: ChatRequest) -> str:
    """The conversation to use: the one the caller named, or a new one."""
    return req.session_id or str(uuid.uuid4())


def _prompt_override(req: ChatRequest, settings: Settings) -> str | None:
    if req.system_prompt is None:
        return None
    if not settings.allow_system_prompt_override:
        raise HTTPException(status_code=403, detail="system_prompt overrides are disabled on this server.")
    return req.system_prompt


def _usage(usage: TokenUsage | None) -> Usage | None:
    if usage is None:
        return None
    return Usage(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def _to_stream_event(event: AgentEvent, session_id: str) -> BaseModel:
    match event:
        case Status(message=message):
            return StatusEvent(type="status", message=message)
        case PropertiesFound(cards=cards):
            return PropertiesEvent(type="properties", properties=cards)
        case SourcesFound(sources=sources):
            return SourcesEvent(type="sources", sources=sources)
        case TextDelta(text=text):
            return TokenEvent(type="token", content=text)
        case TurnComplete(usage=usage):
            return DoneEvent(type="done", session_id=session_id, usage=_usage(usage))
    raise TypeError(f"unexpected agent event: {event!r}")


def _sse(event: BaseModel) -> str:
    return f"data: {event.model_dump_json()}\n\n"
