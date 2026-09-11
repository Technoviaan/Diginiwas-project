"""Server-Sent Events sent by POST /v1/chat/stream."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, RootModel

from app.api.v1.schemas.chat import Usage
from app.properties import PropertyCard


class StatusEvent(BaseModel):
    """A search has started. Show a searching indicator."""

    type: Literal["status"]
    message: str = Field(..., examples=["Searching verified DigiNiwas listings…"])


class PropertiesEvent(BaseModel):
    """Cards for this reply. Replaces any cards sent earlier in the same turn."""

    type: Literal["properties"]
    properties: list[PropertyCard]


class TokenEvent(BaseModel):
    """A piece of reply text. Append it to the chat bubble."""

    type: Literal["token"]
    content: str = Field(..., examples=["I found "])


class DoneEvent(BaseModel):
    """The turn is complete."""

    type: Literal["done"]
    session_id: str
    usage: Usage | None = None


class ErrorEvent(BaseModel):
    """Something failed after the stream started. Sent instead of `done`."""

    type: Literal["error"]
    message: str = Field(..., examples=["Rate limited by the model provider. Retry shortly."])


class StreamEvent(
    RootModel[
        Annotated[
            StatusEvent | PropertiesEvent | TokenEvent | DoneEvent | ErrorEvent,
            Field(discriminator="type"),
        ]
    ]
):
    """One Server-Sent Event from `POST /v1/chat/stream`. Switch on `type`."""
