"""Conversation history: read it, or forget it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from langchain_core.messages import AIMessage, HumanMessage

from app.api.dependencies import SessionsDep
from app.api.v1 import openapi as docs
from app.api.v1.schemas import HistoryResponse, Message
from app.assistant.messages import extract_text

router = APIRouter(tags=["chat v1"])

SessionId = Annotated[
    str,
    Path(
        description="The conversation's `session_id`.",
        openapi_examples={"conversation": {"summary": "A user's conversation", "value": "user-42"}},
    ),
]


@router.get(
    "/sessions/{session_id}/history",
    response_model=HistoryResponse,
    summary="Get a conversation's history",
    description=docs.HISTORY,
    responses={200: {"content": {"application/json": {"example": docs.HISTORY_EXAMPLE}}}},
)
async def history(session_id: SessionId, sessions: SessionsDep) -> HistoryResponse:
    # Tool calls and raw tool results stay in memory for the model; only what
    # the user saw is returned.
    messages: list[Message] = []
    for stored in await sessions.load(session_id):
        text = extract_text(stored.content)
        if isinstance(stored, HumanMessage):
            messages.append(Message(role="user", content=text))
        elif isinstance(stored, AIMessage) and text:
            messages.append(Message(role="assistant", content=text))
    return HistoryResponse(session_id=session_id, messages=messages)


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    summary="Delete a conversation",
    description=docs.DELETE_SESSION,
    responses={204: {"description": "Deleted."}, 404: docs.SESSION_NOT_FOUND},
)
async def delete_session(session_id: SessionId, sessions: SessionsDep) -> None:
    if not await sessions.delete(session_id):
        raise HTTPException(status_code=404, detail="No such session.")
