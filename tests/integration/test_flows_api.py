"""`flow` and `property_ids` on a chat request: the app's guided flows."""

import pytest

from tests.fakes import Reply


def notes(model) -> str:
    """Everything the app told the model about this turn, as one string."""
    return "\n".join(
        str(message.content) for message in model.requests[-1] if type(message).__name__ == "SystemMessage"
    )


def test_a_flow_tells_the_assistant_to_ask_one_thing_at_a_time(api, model):
    model.script = [Reply("Which city are you looking in?")]

    api.post("/v1/chat", json={"message": "Buy", "flow": "find_home"})

    told = notes(model)
    assert "Find My Home flow" in told
    assert "ONE short question per reply" in told


def test_each_flow_has_its_own_guidance(api, model):
    for flow, expected in [
        ("compare_homes", "Compare Homes flow"),
        ("explore_locality", "Explore Locality flow"),
        ("price_trends", "Price Trends flow"),
    ]:
        model.script = [Reply("ok")]
        api.post("/v1/chat", json={"message": "hello", "flow": flow})
        assert expected in notes(model)


def test_free_chat_gets_no_flow_guidance(api, model):
    model.script = [Reply("ok")]
    api.post("/v1/chat", json={"message": "hello"})
    assert "flow of the DigiNiwas app" not in notes(model)


def test_an_unknown_flow_is_refused(api, model):
    model.script = [Reply("ok")]
    assert api.post("/v1/chat", json={"message": "hello", "flow": "sell_my_house"}).status_code == 422


def test_properties_to_compare_are_named_to_the_assistant(api, model):
    model.script = [Reply("Royal Residency is ₹10 L cheaper for the same size.")]

    api.post(
        "/v1/chat",
        json={
            "message": "Compare these",
            "flow": "compare_homes",
            "property_ids": [" DW-1003 ", "DW-1002", ""],
        },
    )

    told = notes(model)
    assert "DW-1003, DW-1002" in told
    assert "Don't ask which properties they mean" in told


@pytest.mark.parametrize("ids", [[], [""], None])
def test_no_properties_to_compare_means_no_note(api, model, ids):
    model.script = [Reply("Pick two to compare.")]
    api.post("/v1/chat", json={"message": "Compare these", "flow": "compare_homes", "property_ids": ids})
    assert "selected these DigiNiwas listings" not in notes(model)


def test_history_still_holds_only_what_the_user_typed(api, model):
    model.script = [Reply("Which city are you looking in?")]
    session_id = api.post("/v1/chat", json={"message": "Buy", "flow": "find_home"}).json()["session_id"]

    messages = api.get(f"/v1/sessions/{session_id}/history").json()["messages"]

    assert messages == [
        {"role": "user", "content": "Buy"},
        {"role": "assistant", "content": "Which city are you looking in?"},
    ]
