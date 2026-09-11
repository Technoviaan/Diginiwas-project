"""Conversation memory: where each session's messages live, and which the model sees."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

from langchain_core.messages import BaseMessage, trim_messages


class SessionStore(Protocol):
    """Storage for conversations, keyed by `session_id`.

    The agent saves whole turns - the user message, tool calls, tool results
    and the reply - so follow-ups can refer to earlier searches. Tool results
    carry their `PropertyCard`s as the message artifact, so a store that
    serialises messages (Redis, a database) must preserve artifacts too.
    """

    async def load(self, session_id: str) -> list[BaseMessage]:
        """The session's messages, oldest first; [] when there is no such session."""
        ...

    async def append(self, session_id: str, messages: Sequence[BaseMessage]) -> None:
        """Add messages to the end of a session, creating it if needed."""
        ...

    async def delete(self, session_id: str) -> bool:
        """Forget a session. False if it didn't exist."""
        ...

    async def count(self) -> int:
        """How many sessions are stored."""
        ...


class InMemorySessionStore:
    """Sessions held in this process, least recently used dropped past `max_sessions`.

    Lost on restart and not shared between workers, which is why the service
    runs a single worker. A Redis- or database-backed store implementing
    `SessionStore` can replace it without touching the agent.
    """

    def __init__(self, max_sessions: int = 10_000) -> None:
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, list[BaseMessage]] = OrderedDict()

    async def load(self, session_id: str) -> list[BaseMessage]:
        messages = self._sessions.get(session_id)
        if messages is None:
            return []
        self._sessions.move_to_end(session_id)
        return list(messages)

    async def append(self, session_id: str, messages: Sequence[BaseMessage]) -> None:
        if not messages:
            return
        self._sessions.setdefault(session_id, []).extend(messages)
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

    async def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    async def count(self) -> int:
        return len(self._sessions)


def recent_window(messages: Sequence[BaseMessage], size: int) -> list[BaseMessage]:
    """The last `size` messages, trimmed so they start on a user message.

    Starting on a user message matters with tools: the window must never
    begin with a tool result whose tool call was cut off, which the model API
    rejects. Counts messages, not tokens.
    """
    return trim_messages(
        list(messages),
        token_counter=len,
        max_tokens=size,
        strategy="last",
        start_on="human",
        include_system=False,
    )
