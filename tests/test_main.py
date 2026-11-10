from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app, create_app


def test_read_root() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to UniLake AI API"}


def test_health_check() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_builds() -> None:
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["openapi"].startswith("3.")


def test_cors_allows_configured_origins_and_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_settings = Settings(
        CORS_ORIGINS="http://localhost:3000",
        CORS_ORIGIN_REGEX=r"^https?://10\.36\.120\.223(:\d+)?$",
        JWT_SECRET_KEY="test-secret",
    )
    monkeypatch.setattr(main_module, "settings", custom_settings)
    test_app = create_app()

    @test_app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(test_app)

    preflight = client.options(
        "/health",
        headers={
            "Origin": "http://10.36.120.223:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "http://10.36.120.223:5173"
    assert preflight.headers.get("access-control-allow-credentials") == "true"

    allowed = client.get("/health", headers={"Origin": "http://10.36.120.223"})
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == "http://10.36.120.223"

    blocked = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert blocked.status_code == 200
    assert "access-control-allow-origin" not in blocked.headers
