"""Tell every client which API version served it, and warn when it's deprecated."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import versions


class VersionHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        info = versions.resolve(scope["path"]) if scope["type"] == "http" else None
        if info is None:
            await self.app(scope, receive, send)
            return

        extra = version_headers(info)

        async def send_with_version(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_version)


def version_headers(info: versions.VersionInfo) -> dict[str, str]:
    headers = {"X-API-Version": info.name, "X-API-Latest-Version": versions.LATEST}
    if info.status == "deprecated":
        # RFC 8594 / draft-ietf-httpapi-deprecation-header: machine-readable
        # warnings, so clients find out from their logs rather than an outage.
        headers["Deprecation"] = "true"
        if info.sunset:
            headers["Sunset"] = info.sunset.strftime("%a, %d %b %Y 00:00:00 GMT")
        if info.successor:
            successor = versions.VERSIONS[info.successor]
            headers["Link"] = f'<{successor.prefix}>; rel="successor-version"'
    return headers
