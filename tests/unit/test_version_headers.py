from datetime import date

from app.api import versions
from app.api.middleware.version_headers import version_headers


def test_current_version_names_itself_and_the_latest():
    assert version_headers(versions.VersionInfo("v1", "current")) == {
        "X-API-Version": "v1",
        "X-API-Latest-Version": "v1",
    }


def test_deprecated_version_warns_with_sunset_and_successor(monkeypatch):
    monkeypatch.setitem(versions.VERSIONS, "v2", versions.VersionInfo("v2", "current"))
    monkeypatch.setattr(versions, "LATEST", "v2")
    old = versions.VersionInfo("v1", "deprecated", sunset=date(2027, 1, 1), successor="v2")

    assert version_headers(old) == {
        "X-API-Version": "v1",
        "X-API-Latest-Version": "v2",
        "Deprecation": "true",
        "Sunset": "Fri, 01 Jan 2027 00:00:00 GMT",
        "Link": '</v2>; rel="successor-version"',
    }
