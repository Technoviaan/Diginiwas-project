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

### Sessions

| Send | What happens |
| --- | --- |
| no `session_id`, `""` or `null` | a new chat starts; the server generates an id (a UUID) |
| a `session_id` you got back earlier | the chat continues with its earlier messages |
| any other `session_id`, e.g. `"user-42"` | used as is: a new chat under that id, or the existing one |

When the user selects a property in the app, also send its ID as
`property_id` with each message. *"How far is it from the airport?"* is then
answered about that listing, with no need to say which one:

```bash
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"message": "How far is it from the airport?", "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c", "property_id": "DW-1003"}'
```

The id comes back as `session_id` in the response (and `done` event), and in
the `X-Session-ID` header, which the streaming endpoint sends before any event.
Store it in the app and send it with every follow-up. Show an earlier chat
with `GET /v1/sessions/{session_id}/history`; forget it with
`DELETE /v1/sessions/{session_id}`.

```bash
# New chat: no session_id
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"message": "Show me homes for sale in Vijay Nagar"}'
# -> {"session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c", "reply": "…", …}

# Follow-up in the same chat
curl -s localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"message": "Compare them", "session_id": "3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c"}'

# Its history
curl -s localhost:8000/v1/sessions/3f6c1b2e-8d4a-4c1e-9b7a-2d5e6f7a8b9c/history
```

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
// sessionId is null for a new chat; the server generates one.
const res = await fetch("/v1/chat/stream", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ message, session_id: sessionId }),
});
sessionId = res.headers.get("X-Session-ID"); // keep it for the next message

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

## Area price rates

Ask the chatbot *"What is the average land price in Vijay Nagar, Indore?"* and
it calls `lookup_area_rates`
([`app/assistant/tools/area_rates.py`](app/assistant/tools/area_rates.py)),
which combines two things:

- **DigiNiwas' own listings** in that area — Plot/Land for land, Residential
  for flats — returned as `properties` cards, with a median price per sq ft
  once there are 3 or more;
- **the rates property portals publish**, found by web search and checked by
  [`app/insights/rates.py`](app/insights/rates.py).

Search results mix area-wide rates with single listings, so a published rate
is accepted only if code confirms that:

- the quote appears word for word in a result naming the locality and city;
- it reads as an area rate (average, range, rates, around…), so one listing's
  "₹2.6 Cr. ₹13,000 /sqft" is refused;
- it is about what was asked — land and plots, or flats;
- every amount is in the quote, and code reads it;
- it names exactly one unit — sq yd, sq m and acres are converted to per sq ft,
  a bigha is kept as written because its size differs by state;
- the rate is plausible.

A government registry rate is labelled "registry rate". Every accepted figure
comes back in the chat response's `sources` (and a `sources` stream event),
with the exact quote in `snippet`. The prompt requires the reply to name the
source of each figure, give ranges as ranges, and say plainly when nothing
could be verified instead of guessing. Lookups are cached for 7 days per area
and kind; switch the feature off with `AREA_RATES_ENABLED=false`.

## Locality guide

Ask *"Is Rau, Indore good for families?"* or *"How far is Vijay Nagar from the
airport?"* and the chatbot calls `locality_guide`
([`app/assistant/tools/locality_guide.py`](app/assistant/tools/locality_guide.py))
for schools, hospitals and connectivity. Asked about a listing instead —
*"schools near DW-1003"*, or *"is the first one close to a hospital?"* after a
search — the bot passes the listing's `property_id` (optional), the tool looks
the listing up, and searches its locality and city; the listing's card comes
back in `properties`. It searches the web once per topic,
in parallel, and [`app/insights/guide.py`](app/insights/guide.py) accepts a
place only if:

- the page isn't social media (Facebook, Instagram, Reddit, Quora, YouTube, X);
- the quote appears word for word in a result naming the locality and city;
- the place's name is in the quote and reads as what was asked — a school
  (not a coaching or driving class), a hospital or clinic, a station or airport;
- a distance, when given, is in the quote; code converts miles and metres to km
  and refuses vague ones like "10 to 15 Km". Connectivity entries without a
  distance are dropped.

Every page used comes back in `sources`, quotes included. The prompt requires
the reply to name the site listing each place, give only distances the result
states, never call a place "the best", and say so when a topic has nothing
reliable. Up to 5 places per topic; cached 7 days per area and topic; switch it
off with `LOCALITY_GUIDE_ENABLED=false`.

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
| `GET` | `/v1/properties/{id}/snapshot` | Price, yield and confidence for one listing |
| `GET` | `/health` | Liveness *(unversioned)* |
| `GET` | `/versions` | Versions and sunset dates *(unversioned)* |

## Property snapshot

`GET /v1/properties/{id}/snapshot` returns the data behind the **Niwas AI
Property Snapshot** card: how a listing's price compares with similar ones
nearby, its estimated rental yield, and how much the numbers can be trusted.

Comparables are live, verified listings of the same transaction type and
bedroom count, of a similar size, within `COMPARABLE_RADIUS_KM` (3 km by
default). Listings without coordinates fall back to matching by locality
name.

| Card tile | Field | How it's worked out |
| --- | --- | --- |
| Price Comparison | `price_comparison` | This listing's ₹/sqft against the **median** of comparable listings |
| Estimated Rental Yield | `rental_yield` | Median rent of comparable rentals × 12 ÷ this price, shown as the middle-half range |
| Locality Trend | `locality_trend` | **Always `available: false`** — see below |
| Data Confidence | `data_confidence` | How many comparables were found, and whether coordinates were available |
| "See How This Was Calculated" | `calculation` | One plain-language line per figure |

Three deliberate choices:

- **No language model touches these numbers.** They are arithmetic over
  DigiNiwas listings, so they are reproducible and explainable.
- **A figure the data can't support is returned as unavailable,** with a
  reason, rather than estimated. Rental yield needs at least 3 comparable
  rentals; below that it says so.
- **Outliers are dropped** before any median (values outside 1.5× the
  interquartile range), so one rent typed as a yearly figure can't move the
  result.

### Data confidence

Only `Low`, `Medium` or `High`, worked out from how far each figure on the
card rests on close, DigiNiwas-own data — not from a raw listing count.

| Figure | High | Medium | Low | Weight |
| --- | --- | --- | --- | --- |
| Price comparison | 10+ similar homes nearby, or same BHK in the locality | 3+ of those, or 10+ in the locality | fewer, or only a city-wide set | 50% |
| Rental yield | 10+ comparable rentals | 5+ | 3–4, a web-quoted rent, or none | 30% |
| Locality trend | — | — | web-quoted, or not available | 20% |

The levels score 3, 2 and 1 and are averaged with those weights: **2.5 or more
is High, 1.75 or more Medium, anything lower Low**. A listing without
coordinates is never High. A rental listing has no yield, so the other two
weights are used alone. `factors` gives each figure's level and reason, and
the "Data confidence" calculation step spells the working out.

| Example | Score | Level |
| --- | --- | --- |
| 1 comparable sale, no rentals, no trend | 1.0 | Low |
| 12 nearby sales, 6 rentals, web trend | 2.3 | Medium |
| 12 nearby sales, 12 rentals, web trend | 2.6 | High |
| 12 sales but all city-wide, 12 rentals | 1.6 | Low |

### Rental yield

```
gross % = estimated monthly rent × 12 ÷ price × 100
net %   = (annual rent − vacancy − listed maintenance) ÷ price × 100
```

**The rent** is a weighted median of comparable rentals: same BHK, similar
size, within the radius. Each counts for how close a match it is × how
recently it was listed:

| Listed | Weight |
| --- | --- |
| within 1 year | 0.5 |
| 1–2 years ago | 0.3 |
| 2–3 years ago | 0.2 |
| over 3 years ago | ignored |

The listings API returns live listings only, not rent history, so "recent"
means recently *listed*.

**Net yield** assumes `RENTAL_VACANCY_MONTHS` (1 by default) without a tenant
and the owner paying the listing's own maintenance charge. Property tax,
insurance and repairs aren't in the data, so they aren't deducted.
`assumptions` states all of this, and should be shown with the figure.

**When fewer than 3 comparable rentals qualify**, the rent is a locality
average quoted from web search (`WEB_RENT_ENABLED`, on once a search key is
set). Accepted only if the snippet says "average" or "median", names the
locality and the city, contains the amount, and the implied gross yield is
1–12%. A single listing's rent ("The rent is ₹6,000 per month") is refused.
It comes back with `source: "web"`, its `quote`, its `rent_scope` — usually
all property types — and `confidence: "Low"`. No search runs when the
listings are enough.

### Locality trend

A 3-year trend needs prices from 3 years ago, and the listings API holds only
what is live today. Two sources are possible, and `locality_trend.source` says
which one a figure came from.

**`listings` — measured (not built yet).** Record the median ₹/sqft per
locality and bedroom count every month, and the trend becomes arithmetic over
your own data. Nothing records it today, so this never appears yet. Other
free sources of real history worth importing: state **collector guideline
rates** (published yearly per locality) and **NHB RESIDEX** (a city-level
index, quarterly since 2017).

**`web` — quoted; on whenever a search key is set** (`WEB_TREND_ENABLED=false` hides it).
the server searches for the locality and asks the model to report a trend
*only if a snippet plainly states one*. It is a citation, not a measurement,
so the guardrails are enforced in code, not left to the model:

1. the quote must appear **verbatim** in a snippet — the model cannot invent
   a number;
2. that snippet must name both the locality and the city ("Vijay Nagar"
   exists in several Indian cities);
3. the value must be plausible for a yearly change (−30% to +50%);
4. anything that fails is discarded and the tile says "Not available yet".

What comes back carries `quote`, `source_url`, `period` and
`confidence: "Low"`. **Show the quote and the source next to it**, and don't
give it the same visual weight as the computed figures: it is a web page's
claim, usually an asking-price aggregate, and search results change week to
week. Figures are cached for 7 days so the card doesn't flicker.

### Locality sources

`locality_sources` holds web pages about the locality from web search, shown
as links for the reader. They never feed a computed figure.

The list is empty unless the chosen provider is configured. Results are cached
for 24 hours per query, and a search failure never fails the snapshot.

### Search provider

`SEARCH_PROVIDER` chooses where locality sources and quoted trends come from.
Switching is one line in `.env`; nothing else changes.

| Provider | Settings | Free tier |
| --- | --- | --- |
| `serper` (default) | `SERPER_API_KEY` | 2,500 queries once, no card — Google's own results |
| `google` | `GOOGLE_SEARCH_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID` | 100 queries/day |
| `brave` | `BRAVE_SEARCH_API_KEY` | none since Feb 2026; card required |

Serper and Brave send their keys as request headers, so they can never appear
in a logged URL. Check either provider end to end, without printing any key:

```bash
PYTHONPATH=. .venv/bin/python scripts/check_search.py Borkhera Kota
```

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
| `PROPERTIES_API_BASE_URL` | **required** | the DigiNiwas backend, e.g. `https://backend-diginiwas.onrender.com`; set only in `.env`, the API won't start without it |
| `PROPERTIES_API_TIMEOUT` | `60` | seconds; a sleeping Render instance is slow to wake |
| `MAX_PROPERTY_RESULTS` | `6` | cards per search |
| `PROPERTY_URL_TEMPLATE` | unset | e.g. `https://diginiwas.com/property/{id}` |
| `COMPARABLE_RADIUS_KM` | `3.0` | how far out snapshot comparables are taken from |
| `COMPARABLE_AREA_TOLERANCE` | `0.25` | a comparable's size may differ by this much |
| `MAX_COMPARABLES` | `30` | closest matches kept per snapshot |
| `GOOGLE_SEARCH_API_KEY` | unset | Custom Search API key, for locality source links |
| `GOOGLE_SEARCH_ENGINE_ID` | unset | Programmable Search Engine `cx` |
| `LOCALITY_SOURCES_LIMIT` | `3` | source links per snapshot |
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
