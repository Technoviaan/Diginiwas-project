"""OpenAPI metadata, and a fix-up for FastAPI's generated schema."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.api import versions

API_TITLE = "Niwas AI Chat API"
API_VERSION = "1.0.0"

API_DESCRIPTION = f"""\
Niwas AI is DigiNiwas' property concierge. A user describes what they want, such \
as *"Find a 2 BHK in Model Town under ₹30K"*. The API searches live, verified \
DigiNiwas listings and answers with a short recommendation plus **property cards** \
to show under the chat bubble.

### Quick start
1. `POST /{versions.LATEST}/chat` with `{{"message": "…", "session_id": "user-42"}}`.
2. Show `reply` in the chat bubble and each item in `properties` as a card.
3. Send follow-ups with the same `session_id`.

In the app, use `POST /{versions.LATEST}/chat/stream` so the reply appears as it's written.

### Beyond listings
The chat also answers questions about an area, from the web, with sources:
- **Area price rates**: *"Average land price in Vijay Nagar, Indore?"* gives the \
rates property portals publish.
- **Locality guide**: *"Is Rau good for families?"* or *"How far is Vijay Nagar \
from the airport?"* gives the schools, hospitals, stations and distances web pages list.

Show each item in the response's `sources` as a link under the bubble. Its \
`snippet` is the exact text quoted. Every figure and place is checked in code \
against the page it came from, so nothing is estimated.

For a listing's **Property Snapshot** card — price comparison, rental yield, locality \
trend and data confidence — call `GET /{versions.LATEST}/properties/{{property_id}}/snapshot` \
with a card's `id`.

### Versioning
Paths are versioned; `/{versions.LATEST}` is current. Versioned responses carry \
`X-API-Version` and `X-API-Latest-Version` headers. A deprecated version also \
sends `Deprecation`, `Sunset` and `Link` headers. `GET /versions` lists them all.
"""

OPENAPI_TAGS = [
    {
        "name": "chat v1",
        "description": (
            "Talk to Niwas AI: find listings, ask area price rates and about schools, hospitals "
            "and connectivity, and manage conversations."
        ),
    },
    {
        "name": "property insights v1",
        "description": "The Property Snapshot card: price, rent, trend and confidence for one listing.",
    },
    {"name": "meta", "description": "Service health and API version discovery. Not versioned."},
]


def use_clean_openapi(app: FastAPI) -> None:
    """Serve FastAPI's schema minus one flaw.

    For a non-JSON response class with a documented model - the SSE endpoint -
    FastAPI emits {"$ref": "...StreamEvent", "type": "string"}. In OpenAPI 3.1
    sibling keywords apply together, so that reads as "an object that is also
    a string" and confuses client generators. Keep only the $ref.
    """
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate()  # caches into app.openapi_schema
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    if not isinstance(operation, dict):
                        continue
                    for response in operation.get("responses", {}).values():
                        for media in (response.get("content") or {}).values():
                            media_schema = media.get("schema")
                            if isinstance(media_schema, dict) and "$ref" in media_schema:
                                media["schema"] = {"$ref": media_schema["$ref"]}
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
