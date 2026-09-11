from tests.fakes import Reply, search


def test_history_shows_what_the_user_saw(api, model):
    model.script = [search(search="Vijay Nagar"), Reply("Royal Residency fits best.")]
    api.post("/v1/chat", json={"message": "Homes in Vijay Nagar", "session_id": "u1"})

    assert api.get("/v1/sessions/u1/history").json() == {
        "session_id": "u1",
        "messages": [
            {"role": "user", "content": "Homes in Vijay Nagar"},
            {"role": "assistant", "content": "Royal Residency fits best."},
        ],
    }


def test_reading_an_unknown_session_does_not_create_it(api):
    before = api.get("/health").json()["active_sessions"]
    assert api.get("/v1/sessions/nobody/history").json() == {"session_id": "nobody", "messages": []}
    assert api.get("/health").json()["active_sessions"] == before


def test_deleting_a_session(api, model):
    model.script = [Reply("hello")]
    api.post("/v1/chat", json={"message": "hi", "session_id": "u1"})

    assert api.delete("/v1/sessions/u1").status_code == 204
    again = api.delete("/v1/sessions/u1")
    assert (again.status_code, again.json()) == (404, {"detail": "No such session."})
