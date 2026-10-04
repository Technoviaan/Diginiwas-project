"""GET /v1/prompts: the guided flows the app offers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import openapi as docs
from app.api.v1.schemas.prompts import ChipGroup, PromptFlow, PromptsResponse
from app.assistant.flows import FLOWS

router = APIRouter(tags=["chat v1"])


@router.get(
    "/prompts",
    response_model=PromptsResponse,
    summary="Get the guided flows",
    description=docs.PROMPTS,
    responses={
        200: {
            "description": "The flows to show as entry points to the chat.",
            "headers": docs.VERSION_HEADERS,
            "content": {"application/json": {"example": docs.PROMPTS_EXAMPLE}},
        }
    },
)
async def prompts() -> PromptsResponse:
    # Static for now: the wording lives in app/assistant/flows.py beside the
    # guidance the model gets, so the two can't drift apart.
    return PromptsResponse(
        flows=[
            PromptFlow(
                id=flow.id,
                title=flow.title,
                opening_question=flow.opening_question,
                chips=[ChipGroup(label=group.label, options=list(group.options)) for group in flow.chips],
                examples=list(flow.examples),
                answer_includes=list(flow.answer_includes),
                empty_message=flow.empty_message,
            )
            for flow in FLOWS
        ]
    )
