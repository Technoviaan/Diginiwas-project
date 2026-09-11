import asyncio

import pytest

from app.api.middleware import RateLimitMiddleware

pytestmark = pytest.mark.anyio


async def ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def request(middleware, *, path="/v1/chat", method="POST", ip="203.0.113.1"):
    scope = {"type": "http", "method": method, "path": path, "client": (ip, 5000), "headers": []}
    response = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}

    await middleware(scope, receive, send)
    return response


async def test_blocks_a_client_over_the_limit_and_says_when_to_retry():
    limiter = RateLimitMiddleware(ok_app, limit=3)
    statuses = [(await request(limiter))["status"] for _ in range(3)]
    blocked = await request(limiter)
    assert statuses == [200, 200, 200]
    assert blocked["status"] == 429
    assert 0 < int(blocked["headers"]["retry-after"]) <= 60


async def test_counts_each_client_separately():
    limiter = RateLimitMiddleware(ok_app, limit=1)
    assert (await request(limiter, ip="203.0.113.1"))["status"] == 200
    assert (await request(limiter, ip="203.0.113.2"))["status"] == 200


@pytest.mark.parametrize("path", ["/v1/chat", "/v1/chat/stream", "/v2/chat"])
async def test_covers_every_chat_endpoint(path):
    limiter = RateLimitMiddleware(ok_app, limit=1)
    await request(limiter, path=path)
    assert (await request(limiter, path=path))["status"] == 429


@pytest.mark.parametrize(
    ("path", "method"),
    [("/health", "POST"), ("/v1/chat", "GET"), ("/v1/chat", "OPTIONS"), ("/v1/chatx", "POST")],
)
async def test_leaves_other_requests_alone(path, method):
    limiter = RateLimitMiddleware(ok_app, limit=1)
    statuses = [(await request(limiter, path=path, method=method))["status"] for _ in range(3)]
    assert statuses == [200, 200, 200]


async def test_limit_zero_turns_limiting_off():
    limiter = RateLimitMiddleware(ok_app, limit=0)
    assert [(await request(limiter))["status"] for _ in range(5)] == [200] * 5


async def test_window_slides():
    limiter = RateLimitMiddleware(ok_app, limit=1, window_seconds=0.05)
    assert (await request(limiter))["status"] == 200
    assert (await request(limiter))["status"] == 429
    await asyncio.sleep(0.06)
    assert (await request(limiter))["status"] == 200


async def test_forgets_idle_clients_to_bound_memory():
    limiter = RateLimitMiddleware(ok_app, limit=5, window_seconds=0.05, max_tracked_clients=2)
    await request(limiter, ip="a")
    await request(limiter, ip="b")
    await asyncio.sleep(0.06)
    await request(limiter, ip="c")
    assert sorted(limiter._hits) == ["c"]
