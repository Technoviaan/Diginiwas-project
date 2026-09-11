"""The error body every HTTP error uses."""

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="A human-readable explanation.")
