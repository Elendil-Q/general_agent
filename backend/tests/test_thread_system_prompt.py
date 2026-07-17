"""Tests for the thread-level system-prompt inspection API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs


def _make_app(run_store: MagicMock):
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_store = run_store
    return app


def test_thread_system_prompt_returns_captured_prompt():
    run_store = MagicMock()
    run_store.get_last_system_prompt = AsyncMock(
        return_value={
            "run_id": "run-2",
            "thread_id": "thread-1",
            "system_prompt": "You are a helpful assistant.",
            "caller": "lead_agent",
            "model_name": "gpt-test",
            "captured_at": "2026-07-17T00:00:00+00:00",
            "llm_call_index": 1,
        }
    )
    app = _make_app(run_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/system-prompt")

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "system_prompt": "You are a helpful assistant.",
        "caller": "lead_agent",
        "model_name": "gpt-test",
        "captured_at": "2026-07-17T00:00:00+00:00",
        "run_id": "run-2",
        "llm_call_index": 1,
    }
    run_store.get_last_system_prompt.assert_awaited_once_with("thread-1", user_id=None)


def test_thread_system_prompt_returns_null_fields_when_none_captured():
    run_store = MagicMock()
    run_store.get_last_system_prompt = AsyncMock(return_value=None)
    app = _make_app(run_store)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/system-prompt")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "thread-1"
    assert body["system_prompt"] is None
    assert body["caller"] is None
    assert body["model_name"] is None
    assert body["captured_at"] is None
    assert body["run_id"] is None
    assert body["llm_call_index"] is None
