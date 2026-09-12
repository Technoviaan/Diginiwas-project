"""FastAPI dependency providers: how endpoints receive their collaborators."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.assistant import ChatAgent, SessionStore
from app.container import Services
from app.core.config import Settings
from app.insights import SnapshotService


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_app_settings(services: Annotated[Services, Depends(get_services)]) -> Settings:
    return services.settings


def get_agent(services: Annotated[Services, Depends(get_services)]) -> ChatAgent:
    return services.agent


def get_sessions(services: Annotated[Services, Depends(get_services)]) -> SessionStore:
    return services.sessions


def get_snapshots(services: Annotated[Services, Depends(get_services)]) -> SnapshotService:
    return services.snapshots


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SnapshotsDep = Annotated[SnapshotService, Depends(get_snapshots)]
AgentDep = Annotated[ChatAgent, Depends(get_agent)]
SessionsDep = Annotated[SessionStore, Depends(get_sessions)]
