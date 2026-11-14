"""
Backend tests for the Phase 3 novel-export endpoint
(``GET /api/novels/{id}/export``).

Like ``test_api_auth.py``, these avoid a live Postgres by stubbing the
SQLAlchemy ``Session`` used inside ``app.api.novels`` with an in-memory fake
novel. WeasyPrint's PDF rendering is stubbed too, since it depends on native
Pango/Cairo libraries that aren't guaranteed to be present wherever these
tests run (CI installs them — see ``.github/workflows/backend.yml`` — but
local/dev machines may not); the endpoint wiring and content negotiation are
what's under test here, not WeasyPrint's own rendering.
"""

import os
import time
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

OWNER_ID = "user-123"


def _auth_header(user_id: str = OWNER_ID) -> dict[str, str]:
    token = jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": int(time.time()) + 3600},
        "test-secret",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class _FakeChapter:
    def __init__(self, chapter_number: int, title: str | None, content: str | None):
        self.chapter_number = chapter_number
        self.title = title
        self.content = content


class _FakeNovel:
    def __init__(self, owner_id: str, chapters=None):
        self.id = 1
        self.owner_id = owner_id
        self.title = "The Last Ember"
        self.genre = "Fantasy"
        self.tone = "Epic"
        self.synopsis = "A kingdom on the brink."
        self.chapters = chapters or []
        self.characters = []
        self.creatures = []


def _mock_session_returning(novel):
    """Patch ``app.api.novels.Session`` so ``session.query(Novel)...first()`` returns ``novel``."""
    mock_session = MagicMock()
    mock_session.query.return_value.options.return_value.filter.return_value.first.return_value = (
        novel
    )
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_session
    mock_cm.__exit__.return_value = False
    return patch("app.api.novels.Session", return_value=mock_cm)


def test_export_requires_auth():
    response = client.get("/api/novels/1/export", params={"format": "markdown"})
    assert response.status_code == 401


def test_export_rejects_non_owner():
    novel = _FakeNovel(owner_id="someone-else")
    with _mock_session_returning(novel):
        response = client.get(
            "/api/novels/1/export", params={"format": "markdown"}, headers=_auth_header()
        )
    assert response.status_code == 403


def test_export_invalid_format_rejected():
    response = client.get(
        "/api/novels/1/export", params={"format": "epub"}, headers=_auth_header()
    )
    assert response.status_code == 400


def test_export_without_completed_chapters_returns_clear_error():
    novel = _FakeNovel(owner_id=OWNER_ID, chapters=[])
    with _mock_session_returning(novel):
        response = client.get(
            "/api/novels/1/export", params={"format": "markdown"}, headers=_auth_header()
        )
    assert response.status_code == 409
    assert "no completed chapters" in response.json()["detail"].lower()


def test_export_markdown_returns_markdown_document():
    novel = _FakeNovel(
        owner_id=OWNER_ID,
        chapters=[_FakeChapter(1, "The Spark", "Once upon a time...")],
    )
    with _mock_session_returning(novel):
        response = client.get(
            "/api/novels/1/export", params={"format": "markdown"}, headers=_auth_header()
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "attachment" in response.headers["content-disposition"]
    assert "# The Last Ember" in response.text
    assert "Chapter 1: The Spark" in response.text


def test_export_pdf_returns_pdf_document():
    novel = _FakeNovel(
        owner_id=OWNER_ID,
        chapters=[_FakeChapter(1, "The Spark", "Once upon a time...")],
    )
    with _mock_session_returning(novel), patch(
        "app.api.novels.render_pdf", return_value=b"%PDF-1.7 fake pdf bytes"
    ):
        response = client.get(
            "/api/novels/1/export", params={"format": "pdf"}, headers=_auth_header()
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content == b"%PDF-1.7 fake pdf bytes"
