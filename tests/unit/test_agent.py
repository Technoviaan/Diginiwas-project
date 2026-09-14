from contextlib import aclosing

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.assistant import PropertiesFound, TextDelta, TokenUsage
from tests.fakes import FORCED_REPLY, Raise, Reply, ToolCalls, search

pytestmark = pytest.mark.anyio


async def collect(agent, message, *, session="s1", **options):
    async with aclosing(agent.stream(session, message, **options)) as events:
        return [event async for event in events]


def kinds(events):
    """Event type names in order, consecutive repeats collapsed."""
    names = [type(event).__name__ for event in events]
    return [name for i, name in enumerate(names) if i == 0 or name != names[i - 1]]


def tool_result_sent(model, call=1):
    return next(m for m in model.requests[call] if isinstance(m, ToolMessage))


# --- a turn ------------------------------------------------------------------


async def test_search_turn_reports_status_then_cards_then_text(agent, model):
    model.script = [search(search="Vijay Nagar"), Reply("Royal Residency fits best.")]
    events = await collect(agent, "Homes in Vijay Nagar")

    assert kinds(events) == ["Status", "PropertiesFound", "TextDelta", "TurnComplete"]
    [found] = [event for event in events if isinstance(event, PropertiesFound)]
    assert [card.id for card in found.cards] == ["DW-1003"]
    assert events[-1].usage == TokenUsage(input_tokens=20, output_tokens=10)


async def test_run_collects_the_whole_turn(agent, model):
    model.script = [search(search="Palasia"), Reply("Palasia Heights fits.")]
    result = await agent.run("s1", "Rent in Palasia")
    assert result.reply == "Palasia Heights fits."
    assert [card.id for card in result.properties] == ["DW-2001"]
    assert result.usage == TokenUsage(20, 10)


async def test_complete_turn_is_saved_including_the_search(agent, model, sessions):
    model.script = [search(search="Vijay Nagar"), Reply("Found one.")]
    await collect(agent, "Homes in Vijay Nagar")

    saved = await sessions.load("s1")
    assert [type(m).__name__ for m in saved] == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert saved[1].tool_calls[0]["name"] == "search_properties"


async def test_system_prompt_override_replaces_the_default(agent, model):
    model.script = [Reply("ok")]
    await collect(agent, "hi", system_prompt="Be brief.")
    assert model.requests[0][0].content == "Be brief."


async def test_a_selected_property_is_named_to_the_model_for_that_turn_only(agent, model, sessions):
    model.script = [Reply("Indore Airport is about 12 km away."), Reply("You're welcome.")]

    await collect(agent, "How far is it from the airport?", property_id="DW-1003")

    *_, note, question = model.requests[0]
    assert type(note).__name__ == "SystemMessage"
    assert "selected the DigiNiwas listing DW-1003" in note.content
    assert question == HumanMessage(content="How far is it from the airport?")
    # History keeps the user's own words, and the note isn't sent again.
    saved = await sessions.load("s1")
    assert [type(m).__name__ for m in saved] == ["HumanMessage", "AIMessage"]
    await collect(agent, "Thanks")
    assert not any("selected the DigiNiwas listing" in str(m.content) for m in model.requests[1])


async def test_model_sees_recent_history_starting_on_a_user_message(build_agent, model, sessions):
    agent = build_agent(history_window=3)
    await sessions.append("s1", [HumanMessage("q1"), AIMessage("a1"), HumanMessage("q2"), AIMessage("a2")])
    model.script = [Reply("a3")]
    await collect(agent, "q3")
    assert [m.content for m in model.requests[0][1:]] == ["q2", "a2", "q3"]


# --- cards for follow-ups ----------------------------------------------------


async def test_follow_up_without_a_search_brings_the_cards_it_mentions(agent, model):
    model.script = [
        search(city="Indore"),
        Reply("Found three."),
        Reply("Lake View Villa is bigger than Royal Residency."),
    ]
    await collect(agent, "Homes in Indore")
    events = await collect(agent, "Compare them")

    assert kinds(events) == ["TextDelta", "PropertiesFound", "TurnComplete"]
    assert [card.id for card in events[-2].cards] == ["DW-3001", "DW-1003"]


async def test_reply_about_no_listing_brings_no_cards(agent, model):
    model.script = [search(city="Indore"), Reply("Found three."), Reply("I only help with homes.")]
    await collect(agent, "Homes in Indore")
    events = await collect(agent, "What's the weather?")
    assert not any(isinstance(event, PropertiesFound) for event in events)


async def test_empty_search_does_not_bring_back_earlier_cards(agent, model):
    model.script = [
        search(city="Indore"),
        Reply("Found three."),
        search(search="Model Town"),
        Reply("Nothing in Model Town, unlike Royal Residency."),
    ]
    await collect(agent, "Homes in Indore")
    events = await collect(agent, "And Model Town?")
    assert [event.cards for event in events if isinstance(event, PropertiesFound)] == [[]]


# --- failures ----------------------------------------------------------------


async def test_early_close_saves_only_the_message_and_text_already_shown(agent, model, sessions):
    model.script = [Reply("Here are some homes")]
    async with aclosing(agent.stream("s1", "hi")) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                break

    saved = await sessions.load("s1")
    assert [(type(m).__name__, m.content) for m in saved] == [("HumanMessage", "hi"), ("AIMessage", "Here ")]


async def test_model_error_propagates_and_saves_nothing(agent, model, sessions):
    model.script = [Raise(RuntimeError("model down"))]
    with pytest.raises(RuntimeError, match="model down"):
        await agent.run("s1", "hi")
    assert await sessions.load("s1") == []


async def test_unknown_tool_is_reported_back_to_the_model(agent, model):
    model.script = [ToolCalls([("book_visit", {})]), Reply("I can't book visits.")]
    events = await collect(agent, "Book a visit")

    result = tool_result_sent(model)
    assert result.status == "error" and "book_visit" in result.content
    assert not any(isinstance(event, PropertiesFound) for event in events)


async def test_invalid_tool_arguments_are_reported_back_to_the_model(agent, model):
    model.script = [search(min_price="1 crore", max_price="50 lakh"), Reply("Let me fix that.")]
    await collect(agent, "Between 1 crore and 50 lakh")

    result = tool_result_sent(model)
    assert result.status == "error"
    assert "min_price is greater than max_price" in result.content


async def test_last_round_has_to_answer_in_text(build_agent, model):
    agent = build_agent(max_tool_rounds=1)
    model.script = [search(city="Indore"), search(city="Indore")]
    events = await collect(agent, "Keep searching")
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == FORCED_REPLY
