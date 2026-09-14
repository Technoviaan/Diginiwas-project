"""The chat agent: one user message in; a reply plus property cards out.

A plain tool-calling loop rather than a framework agent, so every step is
visible: ask the model, run the tools it calls, feed the results back, and
repeat until it answers in text. Its collaborators - model, tools, session
store - are passed in, so each can be replaced independently.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_core.messages.tool import ToolCall
from langchain_core.tools import BaseTool

from app.assistant.cards import cards_mentioned, dedupe
from app.assistant.events import (
    AgentEvent,
    PropertiesFound,
    SourcesFound,
    Status,
    TextDelta,
    TokenUsage,
    TurnComplete,
)
from app.assistant.memory import SessionStore, recent_window
from app.assistant.messages import extract_text
from app.assistant.prompts import SYSTEM_PROMPT
from app.assistant.tools.area_rates import TOOL_NAME as AREA_RATES
from app.assistant.tools.locality_guide import TOOL_NAME as LOCALITY_GUIDE
from app.assistant.tools.property_search import TOOL_NAME as PROPERTY_SEARCH
from app.insights.models import LocalitySource
from app.properties import PropertyCard

logger = logging.getLogger(__name__)

# What the app shows while a tool runs.
STATUS_BY_TOOL = {
    PROPERTY_SEARCH: "Searching verified DigiNiwas listings…",
    AREA_RATES: "Checking published area rates…",
    LOCALITY_GUIDE: "Looking up schools, hospitals and connectivity…",
}


@dataclass
class TurnResult:
    """A whole turn, collected."""

    reply: str
    properties: list[PropertyCard] = field(default_factory=list)
    sources: list[LocalitySource] = field(default_factory=list)
    usage: TokenUsage | None = None


class ChatAgent:
    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        tools: Sequence[BaseTool],
        sessions: SessionStore,
        system_prompt: str = SYSTEM_PROMPT,
        history_window: int = 24,
        max_tool_rounds: int = 3,
    ) -> None:
        self._sessions = sessions
        self._system_prompt = system_prompt
        self._history_window = history_window
        self._max_tool_rounds = max_tool_rounds
        self._tools = {tool.name: tool for tool in tools}
        self._model = chat_model.bind_tools(list(tools))
        # For the final round: tools stay declared (the history contains tool
        # calls) but the model has to answer with what it has.
        self._answer_only_model = chat_model.bind_tools(list(tools), tool_choice="none")

    async def stream(
        self,
        session_id: str,
        message: str,
        *,
        system_prompt: str | None = None,
        property_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn, yielding events as they happen.

        Typically Status → PropertiesFound → TextDelta… → TurnComplete. A later
        PropertiesFound replaces an earlier one: if the model relaxes a filter
        and searches again, its reply is about the newer results.

        `property_id` is the listing the user has selected in the app: "it" and
        "this property" in the message mean that listing. The note saying so
        goes to the model for this turn only; the saved turn keeps the user's
        own words.

        Always consume this with `contextlib.aclosing`, so the turn is saved
        even when the consumer stops early.
        """
        history = await self._sessions.load(session_id)
        human = HumanMessage(content=message)
        messages: list[BaseMessage] = [
            SystemMessage(content=system_prompt or self._system_prompt),
            *recent_window(history, self._history_window),
            *([SystemMessage(content=selected_property_note(property_id))] if property_id else []),
            human,
        ]
        turn: list[BaseMessage] = [human]
        text_parts: list[str] = []
        input_tokens = output_tokens = 0
        saw_usage = searched = completed = False

        try:
            for round_no in range(self._max_tool_rounds + 1):
                model = self._model if round_no < self._max_tool_rounds else self._answer_only_model

                chunks: AIMessageChunk | None = None
                async for chunk in model.astream(messages):
                    chunks = chunk if chunks is None else chunks + chunk
                    if text := extract_text(chunk.content):
                        text_parts.append(text)
                        yield TextDelta(text)
                if chunks is None:
                    break

                if chunks.usage_metadata:
                    saw_usage = True
                    input_tokens += chunks.usage_metadata.get("input_tokens", 0)
                    output_tokens += chunks.usage_metadata.get("output_tokens", 0)

                ai_message = message_chunk_to_message(chunks)
                messages.append(ai_message)
                turn.append(ai_message)
                if not ai_message.tool_calls:
                    break

                for call in ai_message.tool_calls:
                    yield Status(STATUS_BY_TOOL.get(call["name"], "Working on it…"))
                tool_messages = await asyncio.gather(
                    *(self._run_tool(call) for call in ai_message.tool_calls)
                )
                messages.extend(tool_messages)
                turn.extend(tool_messages)

                # Parallel tool calls in one round (e.g. comparing two localities)
                # are merged; an artifact of None means that tool failed.
                artifacts = [result.artifact for result in tool_messages if result.artifact is not None]
                if artifacts:
                    searched = True
                    yield PropertiesFound(
                        dedupe(card for artifact in artifacts for card in _cards_in(artifact))
                    )
                    sources = [source for artifact in artifacts for source in _sources_in(artifact)]
                    if sources:
                        yield SourcesFound(sources)

            if not searched:
                # The model may answer from listings found earlier in the
                # conversation ("show me those again", "compare them") without
                # searching. Cards only come from searches, so attach the ones
                # the reply names - otherwise it would describe listings the
                # app shows no cards for.
                mentioned = cards_mentioned("".join(text_parts), messages)
                if mentioned:
                    yield PropertiesFound(mentioned)

            completed = True
            yield TurnComplete(TokenUsage(input_tokens, output_tokens) if saw_usage else None)
        finally:
            # Runs on success, error and early close alike. A complete turn is
            # saved whole; an interrupted one keeps only the user message and
            # any text already shown - never a dangling tool call, which the
            # model API would reject on the next request.
            if completed:
                saved = turn
            else:
                partial = "".join(text_parts)
                saved = [human, AIMessage(content=partial)] if partial else []
            if saved:
                await self._sessions.append(session_id, saved)

    async def run(
        self,
        session_id: str,
        message: str,
        *,
        system_prompt: str | None = None,
        property_id: str | None = None,
    ) -> TurnResult:
        """The same turn, collected into one result."""
        result = TurnResult(reply="")
        parts: list[str] = []
        stream = self.stream(session_id, message, system_prompt=system_prompt, property_id=property_id)
        async with aclosing(stream) as events:
            async for event in events:
                match event:
                    case TextDelta(text=text):
                        parts.append(text)
                    case PropertiesFound(cards=cards):
                        result.properties = cards
                    case SourcesFound(sources=sources):
                        result.sources = sources
                    case TurnComplete(usage=usage):
                        result.usage = usage
        result.reply = "".join(parts)
        return result

    async def _run_tool(self, call: ToolCall) -> ToolMessage:
        tool = self._tools.get(call["name"])
        if tool is None:
            return ToolMessage(
                content=f"Error: there is no tool named {call['name']!r}.",
                tool_call_id=call["id"],
                status="error",
            )
        try:
            return await tool.ainvoke(call)
        except Exception as exc:  # noqa: BLE001 - reported back to the model
            # Usually arguments that fail validation. Telling the model lets it
            # correct itself next round instead of failing the request.
            logger.warning("Tool %s failed with args %s: %s", call["name"], call["args"], exc)
            return ToolMessage(
                content=f"Error calling {call['name']}: {exc}",
                tool_call_id=call["id"],
                status="error",
            )


def selected_property_note(property_id: str) -> str:
    """Tells the model which listing the user has selected in the app."""
    return (
        f"The user has selected the DigiNiwas listing {property_id} in the app. Unless their message "
        f'clearly names a different listing or area, "it", "this property", "this flat", "the first '
        f"one\" and similar mean {property_id}. Don't ask which property they mean. For schools, "
        f"hospitals or connectivity near it, call locality_guide with property_id {property_id}; "
        f"for its details, price or area rates, call search_properties with search {property_id} "
        "first to learn about it."
    )


def _cards_in(artifact: object) -> list[PropertyCard]:
    """Cards in a tool's artifact: a list of them, or an object with `.cards`."""
    items = artifact if isinstance(artifact, list) else getattr(artifact, "cards", [])
    return [item for item in items if isinstance(item, PropertyCard)]


def _sources_in(artifact: object) -> list[LocalitySource]:
    """Source links in a tool's artifact, when it has any."""
    return [item for item in getattr(artifact, "sources", []) if isinstance(item, LocalitySource)]
