# Niwas AI — DigiNiwas property concierge API

A chat API for DigiNiwas. A user says what they're looking for — *"Find a
2 BHK in Model Town under ₹30K"* — and Niwas AI turns that into a search of
live, verified DigiNiwas listings, then replies with a short recommendation
plus **property cards** for the app to render under the chat bubble.

FastAPI + LangChain + OpenAI. Versioned by URL path; current version **`/v1`**.

```
POST /v1/chat ──► api/v1/endpoints/chat.py
                        │
                        ▼
                  assistant/agent.py ── model picks filters ──► assistant/tools/property_search.py
                        ▲                                                  │
                        │      compact listing summary  ◄─────────────────┤  properties/client.py
                        │                                                  │  GET /api/properties
                        └── reply ──► response ◄── PropertyCard artifacts (never sent to the model)
```

## Quick start

```bash
make install      # .venv with the pinned runtime dependencies, pytest and ruff
```

Put your OpenAI key in `.env` (gitignored; `.env.example` lists every
setting), then:

```bash
make dev          # uvicorn app.main:app --reload
```

Swagger is at http://127.0.0.1:8000/docs. The server refuses to start
without `OPENAI_API_KEY`.

## Project structure

```
app/
├── main.py                 create_app(): settings, services, middleware, routers
├── container.py            Services: builds the object graph at startup, closes it at shutdown
├── core/
│   ├── config.py           Settings, from environment variables and .env
│   └── logging.py
├── properties/             DigiNiwas listings — knows nothing about AI or HTTP
│   ├── models.py           PropertyCard, PropertyPage
│   ├── query.py            PropertyQuery → the API's query parameters
│   ├── client.py           async client for GET /api/properties
│   ├── mapper.py           raw listing → PropertyCard, through a field whitelist
│   └── money.py            parse_inr ("1.2 crore" → 12000000), format_inr
├── assistant/              Niwas AI — knows nothing about HTTP
│   ├── agent.py            the tool-calling loop
│   ├── events.py           what a turn reports: Status, PropertiesFound, TextDelta, TurnComplete
│   ├── memory.py           SessionStore protocol and its in-memory implementation
│   ├── cards.py            which cards belong with a reply
│   ├── prompts.py          the system prompt
│   ├── llm.py              the OpenAI chat model
│   ├── messages.py
│   └── tools/
│       └── property_search.py   the search_properties tool
└── api/                    HTTP
    ├── dependencies.py     FastAPI Depends() providers
    ├── errors.py           exceptions → HTTP status codes, in one place
    ├── openapi.py          Swagger metadata
    ├── meta.py             /health, /versions
    ├── versions.py         API version registry
    ├── middleware/         rate_limit.py, version_headers.py
    └── v1/
        ├── router.py
        ├── endpoints/      chat.py, sessions.py
        ├── schemas/        chat.py, events.py, sessions.py, errors.py
        └── openapi.py      Swagger text and examples for v1
tests/
├── fakes.py                ScriptedChatModel and FakePropertiesAPI: no network, no cost
├── fixtures/               sample listings, including fake seller contacts that must never leak
├── unit/
└── integration/
```

### Design rules

- **Dependencies point one way:** `api → assistant → properties → core`.
  `properties` could serve another service unchanged, and `assistant` could
  sit behind something other than HTTP — a WhatsApp bot, a CLI.
- **No globals.** [`app/container.py`](app/container.py) is the only place
  that picks implementations. Endpoints receive collaborators through
  [`app/api/dependencies.py`](app/api/dependencies.py). Replacing in-memory
  sessions with Redis means writing a `SessionStore` and changing one line
  there.
- **Typed boundaries.** The agent emits typed events; the HTTP layer maps
  them to the documented schemas, which are the same models that serialise
  responses. The API contract can't drift from Swagger unnoticed:
  `tests/integration/test_openapi.py` validates every documented example.
- **Errors in one place.** [`app/api/errors.py`](app/api/errors.py) maps
  model-provider failures to status codes for every endpoint, and words the
  streaming `error` event the same way.
- **Streaming-safe middleware.** Rate limiting and version headers are pure
  ASGI, so they never buffer Server-Sent Events.

## Development

| Command | Does |
| --- | --- |
| `make test` | the test suite — runs in about a second, no network |
| `make lint` | ruff lint and format check |
| `make format` | apply ruff fixes and formatting |
| `make check` | lint + test; run before every commit |
| `make docker-build` | build the production image |

The tests run the real app, wired by the real container, with two
substitutes: `ScriptedChatModel` stands in for OpenAI (each call follows a
script of replies, tool calls or errors), and `FakePropertiesAPI` serves
[`tests/fixtures/properties.json`](tests/fixtures/properties.json) in place
of the DigiNiwas backend.

```python
def test_returns_the_reply_and_its_cards(api, model):
    model.script = [search(search="Vijay Nagar"), Reply("Royal Residency fits best.")]
    response = api.post("/v1/chat", json={"message": "Homes in Vijay Nagar"})
    assert [card["id"] for card in response.json()["properties"]] == ["DW-1003"]
```

`requirements.lock` pins the exact runtime versions the Docker image
installs. Regenerate it from a clean virtualenv that has only the runtime
dependencies (`pip install .` then `pip freeze`), so pytest and ruff stay out
of production.

## Response design

Each reply has two parts, matching the chat UI:

| UI element | Comes from |
| --- | --- |
| Assistant chat bubble | `reply` (or the `token` events when streaming) |
| Property card carousel under the bubble | `properties[]` |
| "Searching…" shimmer | `status` event (streaming only) |

### `POST /v1/chat`

```bash
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"message": "Show me homes for sale in Vijay Nagar", "session_id": "u42"}'
```

```json
{
  "session_id": "u42",
  "reply": "I found 2 verified 3 BHK apartments for sale in Vijay Nagar. …",
  "properties": [
    {
      "id": "DW-1003",
      "title": "3 BHK Premium Apartment Indore",
      "project_name": "Royal Residency",
      "category": "Residential",
      "transaction_type": "Sale",
      "price": 8500000,
      "price_label": "₹85 L",
      "price_period": null,
      "price_per_sqft": 5152,
      "maintenance": 3500,
      "negotiable": true,
      "verified": true,
      "city": "Indore",
      "locality": "Vijay Nagar",
      "address": "Scheme No 54, Vijay Nagar, Indore",
      "latitude": 22.7533,
      "longitude": 75.8937,
      "bedrooms": "3",
      "bathrooms": "3",
      "size": 1650,
      "size_unit": "sqft",
      "furnishing": "Semi-Furnished",
      "floor_no": 5,
      "total_floors": 12,
      "amenities": ["Lift", "Parking", "Security", "Gym", "Swimming Pool"],
      "tags": ["Premium", "Ready To Move"],
      "image": "https://res.cloudinary.com/…/property-1787725754780-877858085.jpg",
      "images": ["…", "…", "…"],
      "url": null
    }
  ],
  "model": "gpt-4o-mini",
  "usage": { "input_tokens": 3085, "output_tokens": 139 }
}
```

(Some card fields are trimmed here. The full schema is in `/docs`.)

### Card field → UI

| Card element (screenshot) | Field(s) |
| --- | --- |
| **VERIFIED** badge | `verified` |
| Photo | `image` (gallery: `images`) |
| ♡ favourite | client-side, keyed by `id` |
| "Green Valley Residency" | `title` (or `project_name`) |
| "₹28k" + "/mo" | `price_label` + `price_period` (`"/mo"` for rent, `null` for sale) |
| 📍 "Model Town, Phase 2" | `locality`, `city` |
| 🛏 "2 BHK" | `bedrooms` + `" BHK"` — a string, can be `"7+"` |
| 📐 "1,100 sqft" | `size` + `size_unit` |
| **View Property →** | `url` if `PROPERTY_URL_TEMPLATE` is set, else build from `id` |

When the model answers from listings it found earlier in the conversation
instead of searching again — *"show me those again"*, *"compare them"*, or
the same question asked twice — `properties` carries the cards for the
listings the reply mentions (matched by ID, title or project name). So a
reply that names listings always comes with their cards. `properties` is
`[]` only when the reply refers to no listing.

### `POST /v1/chat/stream`

Server-Sent Events, one JSON object per frame, in this order:

```
data: {"type":"status","message":"Searching verified DigiNiwas listings…"}
data: {"type":"properties","properties":[ …PropertyCard… ]}
data: {"type":"token","content":"I found "}
data: {"type":"token","content":"2 verified …"}
data: {"type":"done","session_id":"u42","usage":{…}}
```

- A later `properties` event **replaces** an earlier one — when nothing
  matches, the model relaxes a filter and searches again, and its reply is
  about the newer results.
- `{"type":"error","message":"…"}` replaces `done` if generation fails; the
  HTTP status is already 200 by then.

```js
const res = await fetch("/v1/chat/stream", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ message, session_id }),
});

const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
let buffer = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += value;
  const frames = buffer.split("\n\n");
  buffer = frames.pop();
  for (const frame of frames) {
    if (!frame.startsWith("data: ")) continue;
    const evt = JSON.parse(frame.slice(6));
    switch (evt.type) {
      case "status":     showTyping(evt.message); break;
      case "properties": renderCards(evt.properties); break;   // replace, don't append
      case "token":      appendToBubble(evt.content); break;
      case "done":       hideTyping(); break;
      case "error":      showError(evt.message); break;
    }
  }
}
```

## The search tool

[`app/assistant/tools/property_search.py`](app/assistant/tools/property_search.py)
defines `search_properties`. The model fills these arguments, which become
a `PropertyQuery` and then the API's query parameters:

| Tool argument | API param | Notes |
| --- | --- | --- |
| `search` | `search` | locality, project, title, listing ID |
| `city` | `city` | case-insensitive |
| `category` | `category` | `Residential` `Commercial` `Rental` `Sell` `Plot/Land` |
| `transaction_type` | `transactionType` | `Sale` or `Rent` (Lease = Rent) |
| `min_price` / `max_price` | `minPrice` / `maxPrice` | the user's words — `"30K"`, `"50 lakh"`, `"1.2 crore"` — parsed to rupees by `parse_inr`; monthly for rent |
| `approx_price` | → `minPrice` + `maxPrice` | "around 80 lakh" → ₹68 L – ₹92 L (±15%), computed by the tool |
| `bedrooms` / `bathrooms` | `bedrooms` / `bathrooms` | 7 or more is sent as `"7+"` — the API matches exact strings |
| `furnishing` | `furnishing` | |
| `negotiable` | `negotiable` | |
| `page` | `page` | `limit` is fixed by `MAX_PROPERTY_RESULTS` |

**Why budgets are strings:** asked to convert amounts itself, `gpt-4o-mini`
turned "under 1 crore" into `100000000` — ten crore — in live testing. The
model now copies the user's wording and the tool does the arithmetic. The
parsed value is echoed back as `"10000000 (₹1 Cr)"` so the model restates
the budget correctly.

Every search returns two things:

- **To the model:** a compact JSON summary (~200–500 tokens) — enough to
  recommend and compare, no images or coordinates.
- **To the frontend:** full `PropertyCard`s as the tool's *artifact*, which
  LangChain never sends to the model.

**The raw API response contains seller and partner names, emails and phone
numbers, plus internal review notes.**
[`app/properties/mapper.py`](app/properties/mapper.py) builds cards from a
whitelist, so none of it reaches the model or the client; a test with fake
contact details enforces this. Malformed listings are skipped rather than
failing the search.

If the API is down or times out, the tool tells the model, which tells the
user — the request doesn't fail, and the model is instructed not to invent
results.

## The agent

[`app/assistant/agent.py`](app/assistant/agent.py) is a plain tool-calling
loop: call the model → run any tool calls (in parallel) → feed results
back → repeat until it answers in text, up to `MAX_TOOL_ROUNDS`. The final
round forces a text answer.

Memory keeps the whole turn — the user message, tool calls, tool results,
and reply — so follow-ups like *"compare those"* work without searching
again. If a client disconnects mid-turn, only the user message and any text
already shown are saved; a dangling tool call would make the next request
fail.

## The prompt

All of Niwas AI's behaviour is written in
[`app/assistant/prompts.py`](app/assistant/prompts.py):

- **reply format** — at most 2 plain sentences when cards are shown, because
  the cards already carry the details;
- turning requests into filters ("30K" → max_price, "buy" → Sale, "2 BHK" →
  bedrooms 2);
- what to do when nothing matches (say so, remove one filter, label the
  alternatives);
- comparisons, Indian price formats, scope limits (no visit booking, no
  seller contacts), fair-housing refusals, and answering in the user's
  language.

Clients cannot replace the prompt: a `system_prompt` in the request body
returns **403** unless `ALLOW_SYSTEM_PROMPT_OVERRIDE=true`.

## Endpoints

| Method | Path | What it does |
| --- | --- | --- |
| `POST` | `/v1/chat` | One turn: `reply` + `properties` |
| `POST` | `/v1/chat/stream` | Same turn as SSE |
| `GET` | `/v1/sessions/{id}/history` | User messages and assistant replies |
| `DELETE` | `/v1/sessions/{id}` | Forget a conversation |
| `GET` | `/health` | Liveness *(unversioned)* |
| `GET` | `/versions` | Versions and sunset dates *(unversioned)* |

## Versioning

Everything version-specific lives under `app/api/v1/`: its router,
endpoints, schemas and Swagger text. Swagger at `/docs` is generated from
those files — edit [`app/api/v1/openapi.py`](app/api/v1/openapi.py) for
endpoint text and examples, or a model's `Field(description=...)` for schema
text.

Responses carry `X-API-Version` and `X-API-Latest-Version`. To add v2, copy
`app/api/v1` to `app/api/v2`, register it in `app/api/versions.py`, and add
one `include_router` line in `create_app()`. Marking v1 `deprecated` adds
RFC 8594 `Deprecation`, `Sunset` and `Link` headers to every v1 response.
Only breaking changes need a new version.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | Required |
| `MODEL` | `gpt-4o` | |
| `MAX_TOKENS` | `4096` | |
| `TEMPERATURE` | `0.7` | Ignored when `REASONING_EFFORT` is set |
| `REASONING_EFFORT` | unset | reasoning models only (`o4-mini`, `gpt-5`…) |
| `OPENAI_BASE_URL` | unset | Azure, gateway, or local OpenAI-compatible server |
| `SYSTEM_PROMPT` | unset | replaces the built-in prompt |
| `ALLOW_SYSTEM_PROMPT_OVERRIDE` | `false` | |
| `HISTORY_WINDOW` | `24` | messages replayed; a searching turn uses ~4 |
| `MAX_TOOL_ROUNDS` | `3` | model↔tool round trips per message |
| `MAX_SESSIONS` | `10000` | conversations kept in memory; least recently used dropped first |
| `PROPERTIES_API_BASE_URL` | `https://backend-diginiwas.onrender.com` | |
| `PROPERTIES_API_TIMEOUT` | `60` | seconds; a sleeping Render instance is slow to wake |
| `MAX_PROPERTY_RESULTS` | `6` | cards per search |
| `PROPERTY_URL_TEMPLATE` | unset | e.g. `https://diginiwas.com/property/{id}` |
| `CORS_ORIGINS` | `["*"]` | lock down before deploying |
| `RATE_LIMIT_PER_MINUTE` | `20` | chat messages per client IP; `0` turns it off |
| `LOG_LEVEL` | `INFO` | |

## Deployment

See **[DEPLOY.md](DEPLOY.md)** — Docker Compose with Caddy for automatic
HTTPS, or behind an existing nginx.

## Before production

- **Listing data is test data today:** 3 live listings, two identical
  (DW-1002, DW-1003), and DW-1001 is a "Demo Property" at ₹500 Cr/month.
  Recommendations will look odd until real listings are live.
- **Sessions live in process memory** — lost on restart, not shared across
  workers, so the server runs exactly one worker. To scale out, implement
  `SessionStore` ([`app/assistant/memory.py`](app/assistant/memory.py)) on
  Redis or Postgres and wire it in `app/container.py`. It must preserve
  `ToolMessage.artifact`, which carries the cards follow-ups rely on.
- **No auth.** A per-IP rate limit caps spending, but anyone who can reach
  the API can use it. Set a monthly spend limit in the OpenAI dashboard too.
- **No listing cache.** Every search hits the properties API; a cold Render
  instance can add many seconds to the first reply.
