"""Turning failures into HTTP responses, in one place.

An error raised anywhere in a request becomes the same JSON error body, so
endpoints need no try/except of their own. The streaming endpoint, which
can't change its status once it has started, uses `http_error_for` to word
its `error` event the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import openai
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.properties import PropertiesAPIError


@dataclass(frozen=True, slots=True)
class HTTPError:
    status_code: int
    detail: str


def http_error_for(exc: Exception) -> HTTPError:
    """A status code and a message that is safe to show callers.

    Most specific first: one broad handler would lose the difference between
    retryable failures (429, 5xx, network) and permanent ones (401, 400).

    Listings-API failures reach here only from the snapshot endpoint. In a
    chat turn the search tool catches them and reports them to the model,
    which tells the user, so the reply still succeeds.
    """
    if isinstance(exc, openai.AuthenticationError):
        return HTTPError(401, "Invalid or missing OpenAI API key.")
    if isinstance(exc, openai.PermissionDeniedError):
        return HTTPError(403, "This API key may not use that model.")
    if isinstance(exc, openai.NotFoundError):
        return HTTPError(404, f"Unknown model or endpoint: {exc}")
    if isinstance(exc, openai.RateLimitError):
        return HTTPError(429, "Rate limited by the model provider. Retry shortly.")
    if isinstance(exc, openai.BadRequestError):
        return HTTPError(400, f"Rejected by the model provider: {exc}")
    if isinstance(exc, openai.APIConnectionError):
        return HTTPError(503, "Could not reach the model provider.")
    if isinstance(exc, openai.APIStatusError):
        return HTTPError(502, f"Model provider error ({exc.status_code}).")
    if isinstance(exc, httpx.TimeoutException):
        return HTTPError(504, "The listings service timed out. Try again shortly.")
    if isinstance(exc, httpx.HTTPError | PropertiesAPIError):
        return HTTPError(502, "The listings service is unavailable.")
    return HTTPError(500, "Internal error generating a reply.")


def register_exception_handlers(app: FastAPI) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        error = http_error_for(exc)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    app.add_exception_handler(openai.OpenAIError, handle)
    # Unexpected errors: a JSON body instead of plain text. Uvicorn still logs
    # the traceback.
    app.add_exception_handler(Exception, handle)
