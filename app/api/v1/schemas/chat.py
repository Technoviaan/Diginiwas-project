"""Sending a message."""

from pydantic import BaseModel, Field, field_validator

from app.insights.models import LocalitySource
from app.properties import PropertyCard


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="What the user typed.",
        examples=["Find a 2 BHK in Model Town under ₹30K"],
    )
    session_id: str | None = Field(
        None,
        max_length=128,
        description=(
            "Identifies the conversation. **Leave it out, or send an empty string, to start "
            "a new conversation**: the server generates an id and returns it as `session_id` "
            "(and in the `X-Session-ID` header). Send that id with every follow-up to continue "
            "the conversation, and use it to fetch the history."
        ),
        examples=["3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c"],
    )
    property_id: str | None = Field(
        None,
        max_length=64,
        description=(
            "The listing the user has selected in the app, e.g. `DW-1003`. Send it with each message "
            'while a property is selected: *"How far is it from the airport?"* is then answered '
            "about that listing, with no need to name it. Leave it out when nothing is selected."
        ),
        examples=["DW-1003"],
    )
    system_prompt: str | None = Field(
        None,
        description=(
            "Replaces the system prompt for this request. Rejected with `403` unless "
            "the server sets `ALLOW_SYSTEM_PROMPT_OVERRIDE=true`. Leave it out."
        ),
    )

    @field_validator("session_id", "property_id", mode="before")
    @classmethod
    def _blank_is_missing(cls, value: object) -> object:
        # An empty session_id starts a new conversation; an empty property_id selects nothing.
        if isinstance(value, str):
            return value.strip() or None
        return value


class Usage(BaseModel):
    """Model tokens for the whole turn. A turn that searches makes two model calls."""

    input_tokens: int | None = Field(None, description="Tokens sent to the model.")
    output_tokens: int | None = Field(None, description="Tokens the model generated.")


class ChatResponse(BaseModel):
    session_id: str = Field(
        ...,
        description=(
            "The conversation this turn belongs to: the id you sent, or a new one generated for "
            "you. Keep it and send it with the next message to continue."
        ),
    )
    reply: str = Field(
        ..., description="Text for the assistant's chat bubble. Plain text, usually 1–2 sentences."
    )
    properties: list[PropertyCard] = Field(
        default_factory=list,
        description=(
            "Cards to render under the bubble, in order: the listings this reply is "
            "about. Empty when the reply isn't about any listing."
        ),
    )
    sources: list[LocalitySource] = Field(
        default_factory=list,
        description=(
            "Pages the reply quotes figures from, such as published area price rates. Show them "
            "as links under the bubble; `snippet` holds the exact quote. Empty for most replies."
        ),
    )
    model: str = Field(..., description="The model that generated the reply.")
    usage: Usage | None = Field(None, description="Token usage, when the provider reports it.")
