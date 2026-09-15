import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_health(api, settings):
    response = api.get("/health")
    assert response.json() == {
        "status": "ok",
        "model": settings.model,
        "active_sessions": 0,
        "api_versions": ["v1"],
        "latest_api_version": "v1",
    }
    assert "X-API-Version" not in response.headers  # unversioned


def test_versions(api):
    assert api.get("/versions").json() == {
        "latest": "v1",
        "versions": [
            {"version": "v1", "status": "current", "base_path": "/v1", "sunset": None, "successor": None}
        ],
    }


def test_root_redirects_to_the_docs(api):
    response = api.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (307, "/docs")


def test_refuses_to_start_without_an_openai_key(settings):
    app = create_app(settings.model_copy(update={"openai_api_key": " "}))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"), TestClient(app):
        pass


@pytest.mark.parametrize("url", [None, ""])
def test_refuses_to_start_without_a_backend_url(settings, url):
    app = create_app(settings.model_copy(update={"properties_api_base_url": url}))
    with pytest.raises(RuntimeError, match="PROPERTIES_API_BASE_URL is not set"), TestClient(app):
        pass


def test_the_backend_url_comes_only_from_the_environment():
    from app.core.config import Settings

    assert Settings(_env_file=None).properties_api_base_url is None
