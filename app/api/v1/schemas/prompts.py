"""The guided flows the app shows as entry points to the chat."""

from pydantic import BaseModel, Field


class ChipGroup(BaseModel):
    """One row of quick chips under the opening question."""

    label: str = Field(..., description="Label for the row, e.g. 'Property type'.", examples=["Buy or rent"])
    options: list[str] = Field(
        ...,
        description="Chips to show. Tapping one adds it to the user's message.",
        examples=[["Buy", "Rent"]],
    )


class PromptFlow(BaseModel):
    """One entry point: the opening question, its chips, and what the answer covers."""

    id: str = Field(..., description="Send this back as `flow` on chat requests.", examples=["find_home"])
    title: str = Field(..., description="Button label in the app.", examples=["Find My Home"])
    opening_question: str = Field(
        ..., description="Show this as the assistant's first bubble when the flow opens."
    )
    chips: list[ChipGroup] = Field(default_factory=list, description="Quick chips, in rows.")
    examples: list[str] = Field(
        default_factory=list, description="Example messages to show as hints. May be empty."
    )
    answer_includes: list[str] = Field(
        default_factory=list,
        description="What a complete answer in this flow covers, for laying out the screen. May be empty.",
    )
    empty_message: str | None = Field(
        None,
        description=(
            "Show this instead of starting the flow when its precondition isn't met, e.g. fewer "
            "than two saved properties to compare. Null when the flow has no such state."
        ),
    )


class PromptsResponse(BaseModel):
    flows: list[PromptFlow] = Field(..., description="In the order the app should show them.")
