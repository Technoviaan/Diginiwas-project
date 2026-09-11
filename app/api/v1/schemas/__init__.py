"""Request, response and event models for v1 of the API.

These shapes are the contract. Breaking changes belong in a new version
package, not in edits here. Adding optional fields is fine.
"""

from app.api.v1.schemas.chat import ChatRequest, ChatResponse, Usage
from app.api.v1.schemas.errors import ErrorResponse
from app.api.v1.schemas.events import (
    DoneEvent,
    ErrorEvent,
    PropertiesEvent,
    StatusEvent,
    StreamEvent,
    TokenEvent,
)
from app.api.v1.schemas.sessions import HistoryResponse, Message

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "DoneEvent",
    "ErrorEvent",
    "ErrorResponse",
    "HistoryResponse",
    "Message",
    "PropertiesEvent",
    "StatusEvent",
    "StreamEvent",
    "TokenEvent",
    "Usage",
]
