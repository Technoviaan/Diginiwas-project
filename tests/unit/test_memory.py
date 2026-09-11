import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.assistant.memory import InMemorySessionStore, recent_window


@pytest.mark.anyio
async def test_unknown_session_is_empty_and_not_created():
    store = InMemorySessionStore()
    assert await store.load("nobody") == []
    assert await store.count() == 0


@pytest.mark.anyio
async def test_appended_messages_load_back_as_a_copy():
    store = InMemorySessionStore()
    await store.append("s1", [HumanMessage("hi"), AIMessage("hello")])
    loaded = await store.load("s1")
    loaded.clear()
    assert [message.content for message in await store.load("s1")] == ["hi", "hello"]


@pytest.mark.anyio
async def test_delete_reports_whether_the_session_existed():
    store = InMemorySessionStore()
    await store.append("s1", [HumanMessage("hi")])
    assert await store.delete("s1") is True
    assert await store.delete("s1") is False


@pytest.mark.anyio
async def test_least_recently_used_session_is_dropped_past_the_cap():
    store = InMemorySessionStore(max_sessions=2)
    await store.append("a", [HumanMessage("1")])
    await store.append("b", [HumanMessage("2")])
    await store.load("a")  # "a" is now the most recently used
    await store.append("c", [HumanMessage("3")])
    assert await store.count() == 2
    assert await store.load("b") == []
    assert await store.load("a") != []


def test_window_never_starts_on_a_tool_result():
    messages = [
        HumanMessage("q"),
        AIMessage("", tool_calls=[{"name": "search_properties", "args": {}, "id": "c1"}]),
        ToolMessage("result", tool_call_id="c1"),
        AIMessage("answer"),
    ]
    # The last 3 messages begin with the tool call; with no user message left
    # to start on, nothing is kept rather than a broken fragment.
    assert recent_window(messages, 3) == []
    assert recent_window(messages, 4) == messages
