"""Area price rates through the chat API: the reply, its sources, and the stream."""

import json

import httpx

from app.api.v1.schemas import StreamEvent
from tests.fakes import Reply, ToolCalls

SNIPPET = (
    "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048. "
    "The price range per sqft is Rs. 356 - Rs. 22,987."
)
QUOTE = "The average price per sqft for Plots in Vijay Nagar, Indore is Rs. 11,048."
PAGE = "https://housing.com/plots-vijay-nagar"
ANSWER = "housing.com puts plots in Vijay Nagar at an average of ₹11,048 per sq ft."
SEARCH_SETTINGS = {
    "search_provider": "google",
    "google_search_api_key": "key",
    "google_search_engine_id": "cx",
}


def search_results() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        item = {
            "title": "Plots for sale in Vijay Nagar, Indore",
            "snippet": SNIPPET,
            "link": PAGE,
            "displayLink": "housing.com",
        }
        return httpx.Response(200, json={"items": [item]})

    return httpx.MockTransport(handle)


def script() -> list:
    return [
        ToolCalls([("lookup_area_rates", {"locality": "Vijay Nagar", "city": "Indore", "kind": "land"})]),
        # The rate extractor reads the snippet...
        Reply(
            json.dumps(
                {"rates": [{"average": "Rs. 11,048", "low": None, "high": None, "quote": QUOTE, "source": 0}]}
            )
        ),
        # ...and the agent answers from the verified rate.
        Reply(ANSWER),
    ]


def test_chat_answers_with_the_rate_and_its_source(make_api, model):
    model.script = script()
    with make_api(search_transport=search_results(), **SEARCH_SETTINGS) as api:
        response = api.post(
            "/v1/chat", json={"message": "Average land price in Vijay Nagar, Indore?", "session_id": "u1"}
        )

    body = response.json()
    assert body["reply"] == ANSWER
    assert body["properties"] == []
    assert body["sources"] == [
        {
            "title": "Plots for sale in Vijay Nagar, Indore",
            "url": PAGE,
            "snippet": QUOTE,
            "source": "housing.com",
        }
    ]


def test_stream_sends_the_sources_event(make_api, model):
    model.script = script()
    with (
        make_api(search_transport=search_results(), **SEARCH_SETTINGS) as api,
        api.stream(
            "POST", "/v1/chat/stream", json={"message": "Average land price in Vijay Nagar, Indore?"}
        ) as response,
    ):
        events = [
            StreamEvent.model_validate_json(line.removeprefix("data: ")).root
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    names = [event.type for event in events]
    kinds = [name for i, name in enumerate(names) if i == 0 or name != names[i - 1]]
    assert kinds == ["status", "properties", "sources", "token", "done"]
    [sources] = [event for event in events if event.type == "sources"]
    assert [source.url for source in sources.sources] == [PAGE]


def test_most_replies_have_no_sources(api, model):
    model.script = [Reply("Hello! Ask me about homes, plots or area prices.")]
    assert api.post("/v1/chat", json={"message": "hi"}).json()["sources"] == []
