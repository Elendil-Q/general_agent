"""Tests for POST /api/threads/{thread_id}/subagents/{task_id}/resume.

After the unified EventBus refactor, the resume endpoint is fire-and-forget:
it returns 202 JSON immediately instead of streaming SSE. Resume events flow
through EventBus -> SSEBridge -> lead run stream (watched by the frontend).

conftest.py pre-mocks ``deerflow.subagents.executor`` as a MagicMock to break
a circular import. The names imported into ``thread_runs`` (``get_subagent_interrupt``,
``resume_background_subagent``) are therefore MagicMocks; these tests override
them via ``monkeypatch.setattr`` on the router module to control behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs

THREAD_ID = "test-thread"
TASK_ID = "test-task"


def _make_app() -> TestClient:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def mock_interrupted_subagent(monkeypatch):
    """Patch ``get_subagent_interrupt`` + ``resume_background_subagent`` on the
    router module so the resume endpoint sees an INTERRUPTED subagent that
    belongs to the test thread and resumes successfully."""

    def fake_get_interrupt(task_id: str):
        if task_id != TASK_ID:
            return None
        return {
            "task_id": TASK_ID,
            "subagent_thread_id": f"subagent::{THREAD_ID}::{TASK_ID}",
            "description": "need input",
            "interrupts": [{"value": "Approve?", "id": "int-1"}],
        }

    monkeypatch.setattr(thread_runs, "get_subagent_interrupt", fake_get_interrupt)
    monkeypatch.setattr(thread_runs, "resume_background_subagent", MagicMock())
    return {"thread_id": THREAD_ID, "task_id": TASK_ID}


class TestResumeReturnsJson:
    def test_resume_returns_202_json_not_sse(self, mock_interrupted_subagent):
        """POST /resume must return 202 JSON, not an SSE stream."""
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 202, f"Expected 202, got {response.status_code}: {response.text}"
        data = response.json()
        assert data["task_id"] == TASK_ID
        assert data["status"] == "resumed"

    def test_resume_returns_json_content_type(self, mock_interrupted_subagent):
        """Response content type must be JSON, not text/event-stream."""
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 202
        assert "application/json" in response.headers.get("content-type", "")

    def test_resume_not_interrupted_returns_409(self, monkeypatch):
        """Resuming a subagent that is not INTERRUPTED must return 409."""
        monkeypatch.setattr(thread_runs, "get_subagent_interrupt", lambda task_id: None)
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 409

    def test_resume_thread_mismatch_returns_404(self, monkeypatch):
        """Resuming a subagent belonging to a different thread must return 404."""
        monkeypatch.setattr(
            thread_runs,
            "get_subagent_interrupt",
            lambda task_id: {"subagent_thread_id": "subagent::other-thread::test-task"},
        )
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 404

    def test_resume_unknown_task_returns_404(self, monkeypatch):
        """A KeyError from resume_background_subagent must surface as 404."""
        monkeypatch.setattr(
            thread_runs,
            "get_subagent_interrupt",
            lambda task_id: {"subagent_thread_id": f"subagent::{THREAD_ID}::{TASK_ID}"},
        )

        def raise_key_error(task_id, resume):
            raise KeyError(task_id)

        monkeypatch.setattr(thread_runs, "resume_background_subagent", raise_key_error)
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 404

    def test_resume_runtime_error_returns_409(self, monkeypatch):
        """A RuntimeError from resume_background_subagent must surface as 409."""
        monkeypatch.setattr(
            thread_runs,
            "get_subagent_interrupt",
            lambda task_id: {"subagent_thread_id": f"subagent::{THREAD_ID}::{TASK_ID}"},
        )

        def raise_runtime_error(task_id, resume):
            raise RuntimeError("task is running, not interrupted")

        monkeypatch.setattr(thread_runs, "resume_background_subagent", raise_runtime_error)
        client = _make_app()

        response = client.post(
            f"/api/threads/{THREAD_ID}/subagents/{TASK_ID}/resume",
            json={"resume": "user answer"},
        )
        assert response.status_code == 409
