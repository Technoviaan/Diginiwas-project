"""Per-client rate limiting for the chat endpoints.

Every chat message costs OpenAI tokens, so an open endpoint lets anyone
spend the account's balance. This caps messages per client IP per minute.

Counts live in memory, which matches the single-worker deployment; with
several workers or servers each would count separately. Behind a reverse
proxy the client IP comes from X-Forwarded-For, so uvicorn must run with
--proxy-headers (the Docker image does).
"""

from __future__ import annotations

import math
import re
import time
from collections import deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# POST /v1/chat and /v1/chat/stream, and the same in future versions.
CHAT_PATH = re.compile(r"^/v\d+/chat(/stream)?/?$")


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        window_seconds: float = 60.0,
        max_tracked_clients: int = 50_000,
    ) -> None:
        self.app = app
        self.limit = limit
        self.window = window_seconds
        self.max_tracked_clients = max_tracked_clients
        self._hits: dict[str, deque[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            self.limit <= 0
            or scope["type"] != "http"
            or scope["method"] != "POST"
            or not CHAT_PATH.match(scope["path"])
        ):
            await self.app(scope, receive, send)
            return

        now = time.monotonic()
        client = scope.get("client")
        key = client[0] if client else "unknown"

        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_tracked_clients:
                self._forget_idle(now)
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            retry_after = max(1, math.ceil(hits[0] + self.window - now))
            response = JSONResponse(
                {"detail": f"Too many messages. Try again in {retry_after} seconds."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        hits.append(now)
        await self.app(scope, receive, send)

    def _forget_idle(self, now: float) -> None:
        """Drop clients with no requests inside the window, to bound memory."""
        cutoff = now - self.window
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]
