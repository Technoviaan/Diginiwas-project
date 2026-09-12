"""Test doubles: a scripted chat model and a fake DigiNiwas listings API.

Neither touches the network, so the suite is fast, free and deterministic.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr

FIXTURES = Path(__file__).parent / "fixtures"


def load_listings() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "properties.json").read_text())


def make_listing(
    listing_id: str,
    *,
    transaction_type: str = "Sale",
    price: int = 6_000_000,
    size: int = 1100,
    bedrooms: str = "2",
    locality: str = "Vijay Nagar",
    city: str = "Indore",
    latitude: float | None = 22.7533,
    longitude: float | None = 75.8937,
    furnishing: str = "Semi-Furnished",
    **extra: Any,
) -> dict[str, Any]:
    """A raw listing like the API returns, for building comparable datasets."""
    rental = transaction_type == "Rent"
    listing: dict[str, Any] = {
        "propertyId": listing_id,
        "title": f"{bedrooms} BHK {'Rental ' if rental else ''}Property {listing_id}",
        "transactionType": transaction_type,
        "category": "Residential",
        "propertyVerificationStatus": "Verified",
        "price": price,
        "propertySize": size,
        "sizeUnit": "sqft",
        "pricePerSqft": None if rental else round(price / size),
        "bedrooms": bedrooms,
        "bathrooms": bedrooms,
        "locality": locality,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
        "furnishing": furnishing,
    }
    listing.update(extra)
    return listing


def google_search_transport(*titles: str) -> httpx.MockTransport:
    """A Google Programmable Search API that returns one result per title."""

    def handle(request: httpx.Request) -> httpx.Response:
        items = [
            {
                "title": title,
                "link": f"https://news.test/{index}",
                "snippet": f"{title} snippet",
                "displayLink": "news.test",
            }
            for index, title in enumerate(titles)
        ]
        return httpx.Response(200, json={"items": items})

    return httpx.MockTransport(handle)


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #


@dataclass
class Reply:
    """The model answers with this text."""

    text: str


@dataclass
class ToolCalls:
    """The model calls these tools, as (name, arguments) pairs."""

    calls: list[tuple[str, dict[str, Any]]]


@dataclass
class Raise:
    """Calling the model raises this."""

    error: BaseException


def search(**arguments: Any) -> ToolCalls:
    return ToolCalls([("search_properties", arguments)])


# What an answer-only model says when the script asks it to call a tool.
FORCED_REPLY = "Here is what I have so far."
USAGE_PER_CALL = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class ScriptedChatModel(BaseChatModel):
    """A chat model that follows a script: each call takes the next step.

    Records every list of messages it was sent in `requests`.
    """

    script: list[Any] = Field(default_factory=list)
    requests: list[list[BaseMessage]] = Field(default_factory=list)
    answer_only: bool = False
    # A bound copy reads `script` and records `requests` on the model it was
    # bound from, so a test can set the script after building the agent.
    _origin: ScriptedChatModel | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> ScriptedChatModel:
        if tool_choice != "none":
            return self
        bound = self.model_copy(update={"answer_only": True})
        bound._origin = self._origin or self
        return bound

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        """One whole reply, for callers that don't stream."""
        origin = self._origin or self
        origin.requests.append(list(messages))
        step = origin.script.pop(0) if origin.script else Reply("(end of script)")
        if isinstance(step, Raise):
            raise step.error
        if isinstance(step, ToolCalls):
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": args, "id": f"call_{index}"}
                    for index, (name, args) in enumerate(step.calls)
                ],
                usage_metadata=USAGE_PER_CALL,
            )
        else:
            message = AIMessage(content=step.text, usage_metadata=USAGE_PER_CALL)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        origin = self._origin or self
        origin.requests.append(list(messages))
        step = origin.script.pop(0) if origin.script else Reply("(end of script)")
        if isinstance(step, Raise):
            raise step.error
        if isinstance(step, ToolCalls) and self.answer_only:
            step = Reply(FORCED_REPLY)

        if isinstance(step, ToolCalls):
            chunks = [
                {
                    "name": name,
                    "args": json.dumps(args),
                    "id": f"call_{i}",
                    "index": i,
                    "type": "tool_call_chunk",
                }
                for i, (name, args) in enumerate(step.calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
        else:
            words = step.text.split(" ")
            for i, word in enumerate(words):
                text = word if i == len(words) - 1 else f"{word} "
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=USAGE_PER_CALL))


def provider_error(kind: str) -> openai.OpenAIError:
    """An OpenAI SDK exception like the ones the real client raises."""
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    if kind == "connection":
        return openai.APIConnectionError(request=request)
    status, error_class = {
        "auth": (401, openai.AuthenticationError),
        "rate_limit": (429, openai.RateLimitError),
        "server": (500, openai.InternalServerError),
    }[kind]
    return error_class(f"{kind} error", response=httpx.Response(status, request=request), body=None)


# --------------------------------------------------------------------------- #
# the listings API
# --------------------------------------------------------------------------- #


class FakePropertiesAPI:
    """Serves the fixture listings like GET /api/properties, applying the main filters.

    Set `error`, `status_code` or `body` to make it misbehave.
    """

    def __init__(self, listings: list[dict[str, Any]] | None = None) -> None:
        self.listings = load_listings() if listings is None else listings
        self.requests: list[httpx.Request] = []
        self.error: Exception | None = None
        self.status_code = 200
        self.body: dict[str, Any] | None = None
        self.transport = httpx.MockTransport(self._handle)

    @property
    def last_params(self) -> dict[str, str]:
        return dict(self.requests[-1].url.params)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.body is not None:
            return httpx.Response(self.status_code, json=self.body)
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"success": False, "message": "error"})

        params = request.url.params
        matches = [listing for listing in self.listings if _matches(listing, params)]
        limit, page = int(params.get("limit", 20)), int(params.get("page", 1))
        chunk = matches[(page - 1) * limit : page * limit]
        return httpx.Response(
            200,
            json={
                "success": True,
                "total": len(matches),
                "count": len(chunk),
                "pagination": {
                    "currentPage": page,
                    "totalPages": max(1, math.ceil(len(matches) / limit)),
                    "limit": limit,
                },
                "data": chunk,
                "properties": chunk,
            },
        )


def _matches(listing: dict[str, Any], params: httpx.QueryParams) -> bool:
    def text(*keys: str) -> str:
        return " ".join(str(listing.get(key) or "") for key in keys).lower()

    if (query := params.get("search")) and query.lower() not in text(
        "title", "locality", "projectName", "propertyId", "city", "address"
    ):
        return False
    if (city := params.get("city")) and city.lower() != text("city"):
        return False
    if wanted := params.get("transactionType"):
        # The real backend treats Lease as Rent.
        actual = listing.get("transactionType")
        if {"Lease": "Rent"}.get(actual, actual) != {"Lease": "Rent"}.get(wanted, wanted):
            return False
    if (bedrooms := params.get("bedrooms")) and str(listing.get("bedrooms")) != bedrooms:
        return False
    price = listing.get("price") or 0
    if (low := params.get("minPrice")) and price < float(low):
        return False
    return not ((high := params.get("maxPrice")) and price > float(high))
