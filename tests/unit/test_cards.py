from langchain_core.messages import ToolMessage

from app.assistant.cards import cards_mentioned, dedupe
from app.properties import PropertyCard

SKY = PropertyCard(id="DW-1", title="Sky Heights", project_name="SH")
LAKE = PropertyCard(id="DW-2", title="Lake View Villa", project_name="Lake View")


def search_result(*cards):
    return ToolMessage(content="", tool_call_id="call_1", artifact=list(cards))


def test_cards_come_back_in_the_order_the_reply_mentions_them():
    assert cards_mentioned("Lake View beats Sky Heights", [search_result(SKY, LAKE)]) == [LAKE, SKY]


def test_listing_ids_count_as_mentions():
    assert cards_mentioned("DW-2 is the one", [search_result(SKY, LAKE)]) == [LAKE]


def test_names_shorter_than_four_characters_are_ignored():
    assert cards_mentioned("the shop is SHut", [search_result(SKY)]) == []


def test_newer_search_results_replace_older_copies():
    older, newer = SKY.model_copy(update={"price": 1}), SKY.model_copy(update={"price": 2})
    [card] = cards_mentioned("Sky Heights", [search_result(older), search_result(newer)])
    assert card.price == 2


def test_ignores_artifacts_that_are_not_cards():
    message = ToolMessage(content="", tool_call_id="call_1", artifact={"not": "cards"})
    assert cards_mentioned("Sky Heights", [message]) == []


def test_dedupe_keeps_the_first_of_each_listing():
    assert dedupe([SKY, LAKE, SKY]) == [SKY, LAKE]
