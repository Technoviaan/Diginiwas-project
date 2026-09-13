"""Sending a message."""

from pydantic import BaseModel, Field

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
    session_id: str = Field(
        "default",
        min_length=1,
        max_length=128,
        description=(
            "Identifies the conversation. Use one id per user conversation and send "
            "it with every follow-up. If omitted, the request joins the single shared "
            "`default` conversation, together with every other caller that omits it."
        ),
        examples=["user-42"],
    )
    system_prompt: str | None = Field(
        None,
        description=(
            "Replaces the system prompt for this request. Rejected with `403` unless "
            "the server sets `ALLOW_SYSTEM_PROMPT_OVERRIDE=true`. Leave it out."
        ),
    )


class Usage(BaseModel):
    """Model tokens for the whole turn. A turn that searches makes two model calls."""

    input_tokens: int | None = Field(None, description="Tokens sent to the model.")
    output_tokens: int | None = Field(None, description="Tokens the model generated.")


class ChatResponse(BaseModel):
    session_id: str = Field(..., description="The conversation this turn belongs to.")
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
