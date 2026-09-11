"""Unversioned service endpoints: health and version discovery.

These sit outside `/v{n}` on purpose. A load balancer probing `/health`
should not have to know which API versions exist, and a client asking
"which versions can I use?" cannot ask that question from inside a version.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api import versions as v
from app.api.dependencies import SessionsDep, SettingsDep
from app.api.versions import VersionStatus

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model: str = Field(..., description="The model replies are generated with.")
    active_sessions: int = Field(..., description="Conversations held in memory by this process.")
    api_versions: list[str] = Field(..., description="API versions this server serves.")
    latest_api_version: str


class VersionEntry(BaseModel):
    version: str = Field(..., examples=["v1"])
    status: VersionStatus = Field(..., description="`current`, or `deprecated` and due to be removed.")
    base_path: str = Field(..., description="Path prefix for this version.", examples=["/v1"])
    sunset: date | None = Field(None, description="When a deprecated version stops being served.")
    successor: str | None = Field(None, description="The version to move to.")


class VersionsResponse(BaseModel):
    latest: str
    versions: list[VersionEntry]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Liveness only: confirms the process is up. It doesn't call OpenAI or the "
        "listings API, so a `200` doesn't mean chat requests will succeed."
    ),
)
async def health(settings: SettingsDep, sessions: SessionsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=settings.model,
        active_sessions=await sessions.count(),
        api_versions=sorted(v.VERSIONS),
        latest_api_version=v.LATEST,
    )


@router.get(
    "/versions",
    response_model=VersionsResponse,
    summary="List API versions",
    description="Which API versions exist, which one is current, and when deprecated ones will be removed.",
)
async def versions() -> VersionsResponse:
    return VersionsResponse(
        latest=v.LATEST,
        versions=[
            VersionEntry(
                version=info.name,
                status=info.status,
                base_path=info.prefix,
                sunset=info.sunset,
                successor=info.successor,
            )
            for info in sorted(v.VERSIONS.values(), key=lambda i: i.name)
        ],
    )
