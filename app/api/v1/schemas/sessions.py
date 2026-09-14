"""Conversation history."""

from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"] = Field(..., description="Who sent the message.")
    content: str = Field(..., description="The message text.")


class HistoryResponse(BaseModel):
    session_id: str = Field(..., description="The conversation's id.")
    messages: list[Message] = Field(..., description="Oldest first.")
