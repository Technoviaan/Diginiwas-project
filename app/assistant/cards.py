"""Which property cards belong with a reply."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from langchain_core.messages import BaseMessage, ToolMessage

from app.properties import PropertyCard

# Shorter names would match inside unrelated words.
_MIN_NAME_LENGTH = 4


def cards_mentioned(reply: str, messages: Sequence[BaseMessage]) -> list[PropertyCard]:
    """Cards from earlier searches that `reply` refers to, in the order mentioned.

    A listing counts as mentioned when its ID, title or project name appears
    in the reply. Only searches in `messages` - what the model was shown -
    are candidates, and the latest copy of each listing wins.
    """
    text = reply.casefold()
    if not text:
        return []

    latest: dict[str, PropertyCard] = {}
    for message in messages:  # oldest first, so newer search results overwrite
        if isinstance(message, ToolMessage) and isinstance(message.artifact, list):
            for card in message.artifact:
                if isinstance(card, PropertyCard):
                    latest[card.id] = card

    found: list[tuple[int, PropertyCard]] = []
    for card in latest.values():
        positions = [
            text.find(name.casefold())
            for name in (card.id, card.title, card.project_name)
            if name and len(name) >= _MIN_NAME_LENGTH
        ]
        hits = [position for position in positions if position >= 0]
        if hits:
            found.append((min(hits), card))

    found.sort(key=lambda pair: pair[0])  # stable: ties keep search order
    return [card for _, card in found]


def dedupe(cards: Iterable[PropertyCard]) -> list[PropertyCard]:
    """Cards in their first-seen order, each listing once."""
    seen: set[str] = set()
    unique: list[PropertyCard] = []
    for card in cards:
        if card.id not in seen:
            seen.add(card.id)
            unique.append(card)
    return unique
