"""Helpers for LangChain message objects."""

from __future__ import annotations

from typing import Any


def extract_text(content: Any) -> str:
    """Plain text from message content that is a string or a list of blocks.

    OpenAI chat models usually return a string, but reasoning models and
    multimodal replies can return content blocks.
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)
