"""What the agent reports while it works on a turn.

Transport-neutral: the HTTP layer turns these into JSON or Server-Sent Events.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.insights.models import LocalitySource
from app.properties import PropertyCard


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class Status:
    """A tool has started, such as a listings search."""

    message: str


@dataclass(frozen=True, slots=True)
class PropertiesFound:
    """Cards for the reply. A later one in the same turn replaces an earlier one."""

    cards: list[PropertyCard]


@dataclass(frozen=True, slots=True)
class SourcesFound:
    """Web pages the reply quotes figures from. A later one in the same turn replaces an earlier one."""

    sources: list[LocalitySource]


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of the reply's text."""

    text: str


@dataclass(frozen=True, slots=True)
class TurnComplete:
    """The turn finished. `usage` is None when the provider didn't report it."""

    usage: TokenUsage | None


type AgentEvent = Status | PropertiesFound | SourcesFound | TextDelta | TurnComplete
