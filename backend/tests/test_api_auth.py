"""
Smoke tests for the FastAPI app introduced in Phase 1 of the architecture
migration: route wiring and Supabase JWT auth enforcement.

These deliberately avoid touching a real database — DB-backed endpoints are
exercised against Postgres/Supabase in integration environments, not here.
"""

import os

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_endpoint_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_novels_requires_auth():
    response = client.get("/api/novels")
    assert response.status_code == 401


def test_get_novel_requires_auth():
    response = client.get("/api/novels/1")
    assert response.status_code == 401


def test_generate_requires_auth():
    response = client.post("/api/novels/1/generate", json={"initial_prompt": ""})
    assert response.status_code == 401


def test_invalid_bearer_token_rejected():
    response = client.get(
        "/api/novels", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert response.status_code == 401
