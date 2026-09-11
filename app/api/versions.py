"""The registry of API versions this service exposes.

Adding a version means adding a package under `app/api/` with its own
schemas and router, then one entry here plus one `include_router` call in
`app/main.py`. Existing versions are never edited in place - that is the
whole point of versioning them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

VersionStatus = Literal["current", "deprecated"]


@dataclass(frozen=True)
class VersionInfo:
    name: str
    status: VersionStatus
    #  RFC 8594 sunset date: when a deprecated version stops being served.
    sunset: date | None = None
    #  The version clients should move to.
    successor: str | None = None

    @property
    def prefix(self) -> str:
        return f"/{self.name}"


VERSIONS: dict[str, VersionInfo] = {
    "v1": VersionInfo(name="v1", status="current"),
    # When v2 ships, v1 becomes e.g.:
    # "v1": VersionInfo("v1", "deprecated", date(2027, 1, 1), successor="v2"),
}

LATEST: str = "v1"


def resolve(path: str) -> VersionInfo | None:
    """Which API version does this request path belong to, if any."""
    segment = path.lstrip("/").split("/", 1)[0]
    return VERSIONS.get(segment)
