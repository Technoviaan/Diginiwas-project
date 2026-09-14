"""property_id on a chat request: the listing the user selected in the app."""

from tests.fakes import Reply

NOTE = "selected the DigiNiwas listing"


def notes_sent(model) -> list[str]:
    return [str(message.content) for message in model.requests[-1] if NOTE in str(message.content)]


def test_the_selected_property_reaches_the_model(api, model):
    model.script = [Reply("Indore Airport is about 12 km from DW-1003's locality.")]

    response = api.post(
        "/v1/chat", json={"message": "How far is it from the airport?", "property_id": " DW-1003 "}
    )

    assert response.status_code == 200
    [note] = notes_sent(model)
    assert "listing DW-1003 in the app" in note


def test_the_stream_passes_the_selected_property_too(api, model):
    model.script = [Reply("About DW-1003.")]
    with api.stream(
        "POST", "/v1/chat/stream", json={"message": "Any schools nearby?", "property_id": "DW-1003"}
    ) as r:
        list(r.iter_lines())
    assert len(notes_sent(model)) == 1


def test_a_blank_property_id_selects_nothing(api, model):
    model.script = [Reply("Which property do you mean?")]
    api.post("/v1/chat", json={"message": "How far is it from the airport?", "property_id": ""})
    assert notes_sent(model) == []


def test_history_shows_only_what_the_user_typed(api, model):
    model.script = [Reply("About 12 km.")]
    session_id = api.post(
        "/v1/chat", json={"message": "How far is it from the airport?", "property_id": "DW-1003"}
    ).json()["session_id"]

    messages = api.get(f"/v1/sessions/{session_id}/history").json()["messages"]

    assert messages == [
        {"role": "user", "content": "How far is it from the airport?"},
        {"role": "assistant", "content": "About 12 km."},
    ]
