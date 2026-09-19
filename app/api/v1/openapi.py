"""OpenAPI (Swagger) text and examples for v1.

Kept out of the endpoint modules so the handlers stay readable. Examples use
real DigiNiwas listing data and replies captured from live runs, and
tests/integration/test_openapi.py checks each one still validates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.api.v1.schemas import ErrorResponse

# --------------------------------------------------------------------------- #
# descriptions
# --------------------------------------------------------------------------- #

CHAT = """\
One chat turn. Niwas AI reads the message, searches verified DigiNiwas listings \
when it needs to, and responds once the reply is complete.

**Render it as**
- `reply` → the assistant's chat bubble (plain text, usually 1–2 sentences)
- `properties` → property cards under the bubble, in order
- `sources` → links under the bubble to the pages quoted figures or places came from; usually `[]`

`properties` holds the listings the reply is about: from a search made in this \
turn, or from earlier in the conversation for follow-ups such as *"compare them"*. \
It is `[]` when the reply isn't about any listing.

**Area price rates.** Questions such as *"average land price in Vijay Nagar, \
Indore"* are answered from the rates property portals publish, plus DigiNiwas' \
own listings there (as `properties`). Each published figure in the reply is in \
`sources`, with the exact quote in `snippet` - show them. A figure is used only \
when it appears word for word in a search result naming the locality and city.

**Locality guide.** Questions such as *"is Rau good for families?"* or *"how far \
is Vijay Nagar from the airport?"* are answered from the schools, hospitals, \
stations and distances web pages list for the area. Each page used is in \
`sources`, with the exact quotes in `snippet`. Social media is never read, and a \
distance is used only when the page states it. Ask about a listing - *"schools \
near DW-1003"*, or *"is the first one close to a hospital?"* after a search - and \
the listing is looked up to use its locality and city; its card comes back in \
`properties`.

**Why buy this one.** *"Why should I buy DW-1003?"*, *"is it worth the price?"* \
or *"what's nearby?"* is answered from evidence gathered in one go: the listing's \
own data, its price against comparable listings and against published area rates, \
the rental yield, the locality trend, and nearby schools, hospitals and transport. \
The listing's card comes back in `properties` and every page quoted in `sources`. \
Anything that couldn't be established is said plainly rather than filled in.

**Selected property.** When the user has picked a listing in the app, send its \
ID as `property_id` with each message. *"How far is it from the airport?"* or \
*"Any schools nearby?"* is then answered about that listing without naming it.

**Conversations.** To start a new chat, send no `session_id` (or an empty one): \
the server generates one and returns it as `session_id` and in the `X-Session-ID` \
header. Send that id with every follow-up to continue the same chat, and pass it \
to `GET /v1/sessions/{session_id}/history` to show earlier messages. Memory lives \
in the server process and is cleared when it restarts.

**When listings can't be loaded** the response is still `200`, and the reply \
tells the user. The error codes below come from the request itself or from the \
model provider.

In the app, prefer `POST /v1/chat/stream` so the reply appears as it's written.
"""

CHAT_STREAM = """\
The same turn as `POST /v1/chat`, sent as Server-Sent Events so the app can \
show progress and type the reply out.

Each event is one `data:` line holding a JSON object. The shapes are under \
**StreamEvent** in Schemas. Usual order:

| Event | Sent when | What the app should do |
| --- | --- | --- |
| `status` | a search or web lookup starts | show the message as a progress indicator |
| `properties` | results arrive | render the cards, **replacing** cards sent earlier in this turn |
| `sources` | web pages were quoted, e.g. area rates or a locality guide | show them as links, **replacing** sources sent earlier in this turn |
| `token` | reply text is written | append it to the chat bubble |
| `done` | the turn is complete | hide the indicator |
| `error` | something failed after streaming began | show the message (sent instead of `done`) |

For follow-ups about listings from earlier in the conversation, `properties` \
arrives **after** the tokens.

The conversation's `session_id` - the one sent, or a new one when none was - is \
in the `X-Session-ID` response header before the first event, and in `done`.

The request body is the same as `POST /v1/chat`: `message`, optional \
`session_id`, and optional `property_id` for the listing the user has selected.

Only problems caught before the stream starts are HTTP errors: `403`, `422`, \
and `429` when this client has sent too many messages. Once the stream has \
started the status is already `200`, so later failures arrive as an `error` event.

Swagger UI shows the stream only after it finishes. To watch events arrive, \
use `curl -N` or `fetch`.
"""

HISTORY = """\
The conversation as the user saw it: their messages and the assistant's replies, \
oldest first. Use the `session_id` a chat response returned. Searches and cards aren't included. An unknown `session_id` \
returns an empty list.
"""

DELETE_SESSION = """\
Forget a conversation, for example when the user starts a new chat. The next \
message with this `session_id` starts fresh.
"""

# --------------------------------------------------------------------------- #
# examples
# --------------------------------------------------------------------------- #

CHAT_REQUEST_EXAMPLES: dict[str, dict[str, Any]] = {
    "new_chat": {
        "summary": "Start a new chat (no session_id)",
        "description": "The response's session_id is generated. Send it with the next message.",
        "value": {"message": "Find a 2 BHK in Model Town under ₹30K"},
    },
    "find": {
        "summary": "Find a home",
        "description": "Budgets can be written the way people say them: 30K, 50 lakh, 1.2 crore.",
        "value": {
            "message": "Find a 2 BHK in Model Town under ₹30K",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
    "buy": {
        "summary": "Buy within a budget",
        "value": {
            "message": "I want to buy a 3 BHK in Indore under 1 crore",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
    "follow_up": {
        "summary": "Follow-up in the same conversation",
        "description": (
            "Send the same session_id as before. The reply refers to listings shown "
            "earlier, and their cards come back in properties."
        ),
        "value": {"message": "Compare them", "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c"},
    },
    "around": {
        "summary": "Approximate budget",
        "description": '"Around" searches 15% either side of the amount.',
        "value": {
            "message": "Any flats around 80 lakh?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
    "hinglish": {
        "summary": "Hinglish",
        "value": {"message": "Vijay Nagar mein ghar dikhao", "session_id": "user-7"},
    },
    "by_id": {
        "summary": "Ask about a listing ID",
        "value": {"message": "Tell me about DW-1003", "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c"},
    },
    "why_buy": {
        "summary": "Why should I buy this one?",
        "description": (
            "Answered from the listing's data, its price against comparables and published area "
            "rates, the rental yield, the locality trend, and what is nearby - each with its source."
        ),
        "value": {
            "message": "Why should I buy this one?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
            "property_id": "DW-1003",
        },
    },
    "selected_property": {
        "summary": "Ask about the property the user selected",
        "description": (
            "The app sends the selected listing as property_id, so the message can just say "
            '"it". Answered about DW-1003 with no need to name it.'
        ),
        "value": {
            "message": "How far is it from the airport?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
            "property_id": "DW-1003",
        },
    },
    "area_rates": {
        "summary": "Average land price in an area",
        "description": "Answered from the rates property portals publish, with each figure's page in sources.",
        "value": {
            "message": "What is the average land price in Vijay Nagar, Indore?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
    "near_a_property": {
        "summary": "Schools and hospitals near a listing",
        "description": "The listing is looked up by ID, and its locality and city are searched.",
        "value": {
            "message": "Are there schools and hospitals near DW-1003?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
    "locality_guide": {
        "summary": "Schools, hospitals and connectivity",
        "description": "Answered from places web pages list for the area, with each page in sources.",
        "value": {
            "message": "Is Rau, Indore good for families? Schools and hospitals nearby?",
            "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
        },
    },
}

_IMG = "https://res.cloudinary.com/dxw8erwq9/image/upload"

CARD_EXAMPLE: dict[str, Any] = {
    "id": "DW-1003",
    "title": "3 BHK Premium Apartment Indore",
    "project_name": "Royal Residency",
    "description": "Premium 3 BHK apartment with modern amenities",
    "category": "Residential",
    "transaction_type": "Sale",
    "price": 8500000,
    "price_label": "₹85 L",
    "price_period": None,
    "price_per_sqft": 5152,
    "maintenance": 3500,
    "booking_amount": 500000,
    "negotiable": True,
    "verified": True,
    "city": "Indore",
    "locality": "Vijay Nagar",
    "address": "Scheme No 54, Vijay Nagar, Indore",
    "latitude": 22.7533,
    "longitude": 75.8937,
    "bedrooms": "3",
    "bathrooms": "3",
    "balconies": "2",
    "size": 1650,
    "size_unit": "sqft",
    "carpet_area": 1350,
    "furnishing": "Semi-Furnished",
    "facing": "East",
    "parking": "Covered",
    "floor_no": 5,
    "total_floors": 12,
    "amenities": ["Lift", "Parking", "Security", "Gym", "Swimming Pool"],
    "tags": ["Premium", "Ready To Move"],
    "image": f"{_IMG}/v1787725758/diginiwas/properties/images/property-1787725754780-877858085.jpg",
    "images": [
        f"{_IMG}/v1787725758/diginiwas/properties/images/property-1787725754780-877858085.jpg",
        f"{_IMG}/v1787725760/diginiwas/properties/images/property-1787725756816-700421434.jpg",
        f"{_IMG}/v1787725759/diginiwas/properties/images/property-1787725756828-402027751.jpg",
    ],
    "url": None,
    "listed_on": "2026-08-26",
}

CHAT_RESPONSE_EXAMPLE: dict[str, Any] = {
    "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
    "reply": (
        'I found 2 verified 3 BHK apartments in Indore under ₹1 Cr. The "Royal Residency" '
        "at ₹85 L is the best fit because it offers premium amenities like a gym and swimming pool."
    ),
    "properties": [CARD_EXAMPLE],
    "sources": [],
    "model": "gpt-4o-mini",
    "usage": {"input_tokens": 4230, "output_tokens": 73},
}

SSE_EXAMPLE = (
    'data: {"type": "status", "message": "Searching verified DigiNiwas listings…"}\n\n'
    'data: {"type": "properties", "properties": [{"id": "DW-1003", '
    '"title": "3 BHK Premium Apartment Indore", "project_name": "Royal Residency", '
    '"price": 8500000, "price_label": "₹85 L", "price_period": null, '
    '"locality": "Vijay Nagar", "city": "Indore", "bedrooms": "3", "size": 1650, '
    '"size_unit": "sqft", "verified": true, "image": "https://res.cloudinary.com/…jpg"}]}\n\n'
    'data: {"type": "token", "content": "I found "}\n\n'
    'data: {"type": "token", "content": "2 verified 3 BHK apartments in Indore under ₹1 Cr."}\n\n'
    'data: {"type": "done", "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c", '
    '"usage": {"input_tokens": 4230, "output_tokens": 73}}\n\n'
)

HISTORY_EXAMPLE: dict[str, Any] = {
    "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
    "messages": [
        {"role": "user", "content": "I want to buy a 3 BHK in Indore under 1 crore"},
        {"role": "assistant", "content": CHAT_RESPONSE_EXAMPLE["reply"]},
        {"role": "user", "content": "Compare them"},
        {
            "role": "assistant",
            "content": (
                "Both Royal Residency listings are identical: ₹85 L, 1650 sqft, "
                "semi-furnished, with a gym and swimming pool. Either suits you equally."
            ),
        },
    ],
}

AREA_RATES_RESPONSE_EXAMPLE: dict[str, Any] = {
    "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
    "reply": (
        "Housing.com lists the average land price in Vijay Nagar, Indore at ₹11,048 per sq ft, with a "
        "price range from ₹356 to ₹22,987 per sq ft. These are asking prices, not DigiNiwas valuations."
    ),
    "properties": [],
    "sources": [
        {
            "title": "639+ Residential Land / Plots for sale in Vijay Nagar, Indore",
            "url": "https://housing.com/in/buy/indore/vijay-nagar-gid/plots-fid/",
            "snippet": (
                "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048. "
                "The price range per sqft is Rs. 356 - Rs. 22,987."
            ),
            "source": "housing.com",
        }
    ],
    "model": "gpt-4o-mini",
    "usage": {"input_tokens": 6049, "output_tokens": 78},
}

LOCALITY_GUIDE_RESPONSE_EXAMPLE: dict[str, Any] = {
    "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
    "reply": "Vijay Nagar, Indore is approximately 12.1 km from Indore Airport (IDR), as listed by www.rome2rio.com.",
    "properties": [],
    "sources": [
        {
            "title": "Vijay Nagar to Indore Airport (IDR) - 5 ways to travel ...",
            "url": (
                "https://www.rome2rio.com/s/Vijay-Nagar-Scheme-No-54-Indore-Madhya-Pradesh-452010-India/"
                "Indore-Airport-IDR"
            ),
            "snippet": "The distance between Vijay Nagar and Indore Airport (IDR) is 8 miles. The road distance is 7.5 miles.",
            "source": "www.rome2rio.com",
        }
    ],
    "model": "gpt-4o-mini",
    "usage": {"input_tokens": 6033, "output_tokens": 64},
}

SELECTED_PROPERTY_RESPONSE_EXAMPLE: dict[str, Any] = {
    "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
    "reply": (
        "In Vijay Nagar, where DW-1003 is located, Indore Airport (IDR) is about 12.1 km away, "
        "according to www.rome2rio.com."
    ),
    "properties": [CARD_EXAMPLE],
    "sources": LOCALITY_GUIDE_RESPONSE_EXAMPLE["sources"],
    "model": "gpt-4o-mini",
    "usage": {"input_tokens": 6749, "output_tokens": 64},
}

CHAT_RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "search": {
        "summary": "Listings found",
        "description": "A search of DigiNiwas listings: cards in properties, no sources.",
        "value": CHAT_RESPONSE_EXAMPLE,
    },
    "area_rates": {
        "summary": "Area price rates",
        "description": (
            '"What is the average land price in Vijay Nagar, Indore?" The rate comes from a portal\'s '
            "page, quoted in sources. No DigiNiwas plots there yet, so properties is empty."
        ),
        "value": AREA_RATES_RESPONSE_EXAMPLE,
    },
    "locality_guide": {
        "summary": "Locality guide",
        "description": (
            '"How far is Vijay Nagar, Indore from the airport?" The road distance (7.5 miles) is '
            "converted to km in code, and the page is in sources."
        ),
        "value": LOCALITY_GUIDE_RESPONSE_EXAMPLE,
    },
    "selected_property": {
        "summary": "Selected property (property_id sent)",
        "description": (
            '"How far is it from the airport?" sent with property_id DW-1003. The listing is looked '
            "up, its locality searched, and its card returned in properties."
        ),
        "value": SELECTED_PROPERTY_RESPONSE_EXAMPLE,
    },
}


def _sse(*events: dict[str, Any]) -> str:
    return "".join(f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events)


SSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "search": {
        "summary": "Listings found",
        "value": SSE_EXAMPLE,
    },
    "area_rates": {
        "summary": "Area price rates, with a sources event",
        "description": "Sent for area rates and the locality guide: show the sources as links.",
        "value": _sse(
            {"type": "status", "message": "Checking published area rates…"},
            {"type": "properties", "properties": []},
            {"type": "sources", "sources": AREA_RATES_RESPONSE_EXAMPLE["sources"]},
            {"type": "token", "content": "Housing.com lists the average land price in Vijay Nagar, Indore "},
            {
                "type": "token",
                "content": "at ₹11,048 per sq ft, with a price range from ₹356 to ₹22,987 per sq ft.",
            },
            {
                "type": "done",
                "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c",
                "usage": AREA_RATES_RESPONSE_EXAMPLE["usage"],
            },
        ),
    },
}

# --------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------- #

SESSION_HEADER: dict[str, Any] = {
    "X-Session-ID": {
        "description": "The conversation's id: the one sent, or a new one when none was sent.",
        "schema": {"type": "string", "example": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c"},
    },
}

VERSION_HEADERS: dict[str, Any] = {
    "X-API-Version": {
        "description": "The API version that served this response.",
        "schema": {"type": "string", "example": "v1"},
    },
    "X-API-Latest-Version": {
        "description": "The newest API version available.",
        "schema": {"type": "string", "example": "v1"},
    },
}


def _error(description: str, detail: str) -> dict[str, Any]:
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"detail": detail}}},
    }


CHAT_ERRORS: dict[int | str, dict[str, Any]] = {
    400: _error(
        "The model provider rejected the request, for example because the conversation is too long.",
        "Rejected by the model provider: …",
    ),
    401: _error(
        "The server's OpenAI API key is missing or invalid. A server "
        "configuration problem, not something the caller can fix.",
        "Invalid or missing OpenAI API key.",
    ),
    403: _error(
        "`system_prompt` was sent but this server doesn't allow overrides "
        "(`ALLOW_SYSTEM_PROMPT_OVERRIDE=false`), or the API key can't use the "
        "configured model.",
        "system_prompt overrides are disabled on this server.",
    ),
    404: _error(
        "The configured `MODEL` doesn't exist for this API key. A server configuration problem.",
        "Unknown model or endpoint: …",
    ),
    429: _error(
        "Too many requests. Either this client sent more than `RATE_LIMIT_PER_MINUTE` "
        "messages in a minute (the response has a `Retry-After` header, in seconds), "
        "or the model provider is rate-limiting the server. Safe to retry after a wait.",
        "Too many messages. Try again in 42 seconds.",
    ),
    500: _error("An unexpected server error.", "Internal error generating a reply."),
    502: _error("The model provider returned an error.", "Model provider error (500)."),
    503: _error(
        "The model provider couldn't be reached. Safe to retry.",
        "Could not reach the model provider.",
    ),
}

SESSION_NOT_FOUND = _error("No conversation exists with that `session_id`.", "No such session.")

# --------------------------------------------------------------------------- #
# property snapshot
# --------------------------------------------------------------------------- #

SNAPSHOT = """\
The data behind the **Niwas AI Property Snapshot** card for one listing: how its \
price compares with similar homes, what it could rent for, how the locality's \
prices have moved, and how far to trust all three.

**Input:** only the listing ID in the path. No body.

### Card → field

| Card element | Big text | Small text | Details screen |
| --- | --- | --- | --- |
| Price Comparison | `price_comparison.headline` | `price_comparison.caption` | `basis`, `breakdown` |
| Estimated Rental Yield | `rental_yield.headline` | `rental_yield.rent_scope` | `gross_percent`, `net_percent`, `assumptions`, `quote` |
| Locality Trend | `locality_trend.headline` | `locality_trend.caption` | `quote`, `source_name`, `source_url` |
| Data Confidence | `data_confidence.headline` | `data_confidence.caption` | `factors[].reason` |
| See How This Was Calculated | | | `calculation[]` |
| Updated … | `updated_on` | | |
| Disclaimer | `disclaimer` | | |

When a tile's `available` is `false`, show its `headline` ("Not enough data", \
"Not available yet") instead of a number.

### Where the figures come from

- **Price comparison** — the median price per sqft of comparable live listings, \
from the narrowest set holding at least 3: similar homes within `radius_km`, then \
the same BHK in the locality, the whole locality, the same BHK in the city, the \
whole city. `caption` names the set used.
- **Rental yield** — a weighted median of comparable rentals (closer and more \
recently listed ones count for more) × 12 ÷ price, gross and net. With fewer than \
3 rentals, a locality's average rent quoted from web search.
- **Locality trend** — quoted from web search when a page states one; nothing \
records price history yet.
- **Data confidence** — each figure's own confidence, weighted 50% / 30% / 20% \
into Low, Medium or High. See `data_confidence.factors`.

### Web-quoted figures

A figure with `source: "web"` is a claim a property portal published, not a \
DigiNiwas measurement. It is accepted only when it appears word for word in a \
search result naming the locality and the city, and the amount is read by code, \
never estimated by a language model. It always carries `quote`, `source_name`, \
`source_url` and `confidence: "Low"` — **show the source next to the figure**. \
Without a search key on the server, web-quoted figures are simply off.
"""

_EXAMPLES = Path(__file__).parent / "examples"


def _example(name: str) -> dict[str, Any]:
    return json.loads((_EXAMPLES / name).read_text(encoding="utf-8"))


SNAPSHOT_EXAMPLES: dict[str, dict[str, Any]] = {
    "own_data": {
        "summary": "Well covered: figures from DigiNiwas listings",
        "description": (
            "12 comparable sales and 6 comparable rentals nearby, so price comparison and "
            "rental yield are computed from them. No search provider is configured, so there "
            "is no trend and no sources."
        ),
        "value": _example("snapshot_own_data.json"),
    },
    "web_quoted": {
        "summary": "Sparse data: figures quoted from the web (DW-1003)",
        "description": (
            "One comparable sale and no rentals. The rent and the locality trend are quoted "
            "from web pages, so they carry their source and quote, and confidence is Low."
        ),
        "value": _example("snapshot_web_quoted.json"),
    },
}

LISTING_NOT_FOUND = _error("No live, verified listing has that ID.", "No listing with that ID.")
LISTINGS_UNAVAILABLE = _error(
    "The DigiNiwas listings API could not be reached.", "The listings service is unavailable."
)
LISTINGS_TIMEOUT = _error(
    "The DigiNiwas listings API did not answer in time. Safe to retry.",
    "The listings service timed out. Try again shortly.",
)
