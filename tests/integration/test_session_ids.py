"""Session ids: generated when missing, continued when given, with their history."""

import uuid

import pytest

from app.api.v1.schemas import StreamEvent
from tests.fakes import Reply


def test_a_message_without_a_session_id_starts_a_new_conversation(api, model):
    model.script = [Reply("Hello!"), Reply("Hello again!")]

    first = api.post("/v1/chat", json={"message": "hi"})
    second = api.post("/v1/chat", json={"message": "hi"})

    first_id, second_id = first.json()["session_id"], second.json()["session_id"]
    assert first_id != second_id
    uuid.UUID(first_id)
    assert first.headers["X-Session-ID"] == first_id


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_session_id_also_starts_a_new_conversation(api, model, blank):
    model.script = [Reply("Hello!")]
    session_id = api.post("/v1/chat", json={"message": "hi", "session_id": blank}).json()["session_id"]
    uuid.UUID(session_id)


def test_a_given_session_id_is_kept(api, model):
    model.script = [Reply("Hello!")]
    response = api.post("/v1/chat", json={"message": "hi", "session_id": "  user-42 "})
    assert response.json()["session_id"] == "user-42"
    assert response.headers["X-Session-ID"] == "user-42"


def test_the_returned_session_id_continues_the_chat_and_has_its_history(api, model):
    model.script = [Reply("Hello! What are you looking for?"), Reply("Here are homes in Indore.")]

    session_id = api.post("/v1/chat", json={"message": "hi"}).json()["session_id"]
    follow_up = api.post("/v1/chat", json={"message": "Homes in Indore", "session_id": session_id})

    assert follow_up.json()["session_id"] == session_id
    # The model saw the first turn when answering the second.
    assert "Hello! What are you looking for?" in [message.content for message in model.requests[-1]]
    assert api.get(f"/v1/sessions/{session_id}/history").json() == {
        "session_id": session_id,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello! What are you looking for?"},
            {"role": "user", "content": "Homes in Indore"},
            {"role": "assistant", "content": "Here are homes in Indore."},
        ],
    }


def test_the_stream_sends_a_new_session_id_before_any_event_and_in_done(api, model):
    model.script = [Reply("Hello!")]

    with api.stream("POST", "/v1/chat/stream", json={"message": "hi"}) as response:
        session_id = response.headers["X-Session-ID"]
        events = [
            StreamEvent.model_validate_json(line.removeprefix("data: ")).root
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    uuid.UUID(session_id)
    assert events[-1].type == "done"
    assert events[-1].session_id == session_id


def test_browsers_can_read_the_session_header(api, model):
    model.script = [Reply("Hello!")]
    response = api.post("/v1/chat", json={"message": "hi"}, headers={"Origin": "https://diginiwas.com"})
    assert "X-Session-ID" in response.headers["access-control-expose-headers"]
