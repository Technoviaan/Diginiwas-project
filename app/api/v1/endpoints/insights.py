"""GET /v1/properties/{property_id}/snapshot."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from app.api.dependencies import SnapshotsDep
from app.api.v1 import openapi as docs
from app.insights import PropertySnapshot

router = APIRouter(tags=["property insights v1"])

PropertyId = Annotated[
    str,
    Path(
        description="The listing's ID, from a property card's `id`.",
        openapi_examples={"listing": {"summary": "A live listing", "value": "DW-1003"}},
    ),
]


@router.get(
    "/properties/{property_id}/snapshot",
    response_model=PropertySnapshot,
    summary="Property snapshot",
    description=docs.SNAPSHOT,
    responses={
        200: {
            "description": "The snapshot. Choose an example: a well-covered listing, or a sparse one.",
            "headers": docs.VERSION_HEADERS,
            "content": {"application/json": {"examples": docs.SNAPSHOT_EXAMPLES}},
        },
        404: docs.LISTING_NOT_FOUND,
        502: docs.LISTINGS_UNAVAILABLE,
        504: docs.LISTINGS_TIMEOUT,
    },
)
async def property_snapshot(property_id: PropertyId, snapshots: SnapshotsDep) -> PropertySnapshot:
    snapshot = await snapshots.for_listing(property_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No listing with that ID.")
    return snapshot
