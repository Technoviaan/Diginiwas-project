"""Everything under /v1."""

from fastapi import APIRouter

from app.api.v1.endpoints import chat, sessions

router = APIRouter(prefix="/v1")
router.include_router(chat.router)
router.include_router(sessions.router)
