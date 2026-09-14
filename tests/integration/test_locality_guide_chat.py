"""The locality guide through the chat API: the reply and the pages it quotes."""

import json

import httpx

from tests.fakes import Reply, ToolCalls

TITLE = "33 Best Schools in Vijay Nagar, Indore 2026-2027"
QUOTE = "Best Schools in Vijay Nagar, Indore · SICA Senior Secondary School · Podar International School"
PAGE = "https://www.edustoke.com/indore/vijay-nagar"
ANSWER = "Edustoke lists SICA Senior Secondary School and Podar International School in Vijay Nagar."
SEARCH_SETTINGS = {
    "search_provider": "google",
    "google_search_api_key": "key",
    "google_search_engine_id": "cx",
}


def search_results() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        item = {
            "title": TITLE,
            "snippet": f"{QUOTE} · Daisy Dales",
            "link": PAGE,
            "displayLink": "www.edustoke.com",
        }
        return httpx.Response(200, json={"items": [item]})

    return httpx.MockTransport(handle)


def test_chat_answers_with_the_places_and_their_page(make_api, model):
    places = [
        {"name": "SICA Senior Secondary School", "distance": None, "quote": QUOTE, "source": 0},
        {"name": "Podar International School", "distance": None, "quote": QUOTE, "source": 0},
    ]
    model.script = [
        ToolCalls([("locality_guide", {"locality": "Vijay Nagar", "city": "Indore", "topics": ["schools"]})]),
        # The guide reads the snippet...
        Reply(json.dumps({"places": places})),
        # ...and the agent answers from the verified places.
        Reply(ANSWER),
    ]

    with make_api(search_transport=search_results(), **SEARCH_SETTINGS) as api:
        body = api.post("/v1/chat", json={"message": "Good schools in Vijay Nagar, Indore?"}).json()

    assert body["reply"] == ANSWER
    assert body["sources"] == [{"title": TITLE, "url": PAGE, "snippet": QUOTE, "source": "www.edustoke.com"}]
