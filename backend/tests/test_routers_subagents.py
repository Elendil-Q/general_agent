"""Tests for the subagents CRUD API router."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from deerflow.config.paths import Paths

    return Paths(base_dir=base_dir)


def _make_test_app() -> FastAPI:
    """Create a FastAPI app with only the subagents router."""
    from app.gateway.routers.subagents import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    """TestClient with the subagents router, a tmp base_dir and a fixed user.

    Patches ``get_paths`` in the per-user storage module (so load/save/delete
    operate on ``tmp_path``) and ``get_effective_user_id`` on the router module
    (so all requests act as the ``default`` user).
    """
    import app.gateway.routers.subagents as subagents_router

    paths_instance = _make_paths(tmp_path)
    with (
        patch("deerflow.config.subagents_user_config.get_paths", return_value=paths_instance),
        patch.object(subagents_router, "get_effective_user_id", return_value="default"),
    ):
        app = _make_test_app()
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_subagents_includes_builtins(client):
    r = client.get("/api/subagents")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["subagents"]]
    assert "general-purpose" in names
    assert "bash" in names


def test_create_and_get_user_subagent(client, tmp_user_id="default"):
    payload = {
        "name": "researcher",
        "description": "research specialist",
        "system_prompt": "be terse",
        "model": "inherit",
        "max_turns": 50,
        "timeout_seconds": 900,
    }
    r = client.post("/api/subagents", json=payload)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "user"
    assert r.json()["readonly"] is False

    r2 = client.get("/api/subagents/researcher")
    assert r2.status_code == 200
    assert r2.json()["system_prompt"] == "be terse"


def test_create_rejects_builtin_name(client):
    r = client.post("/api/subagents", json={"name": "bash", "description": "x"})
    assert r.status_code == 422


def test_write_to_builtin_403(client):
    assert client.put("/api/subagents/bash", json={"description": "x"}).status_code == 403
    assert client.delete("/api/subagents/bash").status_code == 403


def test_update_user_subagent(client):
    client.post("/api/subagents", json={"name": "r", "description": "d"})
    r = client.put("/api/subagents/r", json={"description": "d2"})
    assert r.status_code == 200 and r.json()["description"] == "d2"


def test_delete_user_subagent(client):
    client.post("/api/subagents", json={"name": "r2", "description": "d"})
    assert client.delete("/api/subagents/r2").status_code == 204
    assert client.get("/api/subagents/r2").status_code == 404


def test_name_check(client):
    r = client.get("/api/subagents/check", params={"name": "new-one"})
    assert r.status_code == 200 and r.json()["available"] is True
    r = client.get("/api/subagents/check", params={"name": "bash"})
    assert r.json()["available"] is False  # builtin reserved


def test_catalogs(client):
    r = client.get("/api/subagents/catalogs")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data and "tools" in data and "skills" in data
    model_names = [m["name"] for m in data["models"]]
    assert "inherit" in model_names


# ---------------------------------------------------------------------------
# Final-review regression tests (F1: cleared fields; F2: preserved fields)
# ---------------------------------------------------------------------------


def test_update_clears_tools_to_inherit_all(client):
    """PUT {"tools": null} clears tools to inherit-all, not preserve-existing (F1)."""
    r = client.post("/api/subagents", json={"name": "clr-tools", "description": "d", "tools": ["read_file"]})
    assert r.status_code == 201, r.text
    assert r.json()["tools"] == ["read_file"]
    r = client.put("/api/subagents/clr-tools", json={"tools": None})
    assert r.status_code == 200, r.text
    got = client.get("/api/subagents/clr-tools").json()
    assert got["tools"] is None  # cleared -> inherit-all, NOT the original list


def test_update_clears_system_prompt(client):
    """PUT {"system_prompt": null} clears the prompt to inherit (F1)."""
    r = client.post("/api/subagents", json={"name": "clr-prompt", "description": "d", "system_prompt": "be terse"})
    assert r.status_code == 201, r.text
    assert r.json()["system_prompt"] == "be terse"
    r = client.put("/api/subagents/clr-prompt", json={"system_prompt": None})
    assert r.status_code == 200, r.text
    got = client.get("/api/subagents/clr-prompt").json()
    assert got["system_prompt"] is None


def test_update_preserves_exclusive_tools_and_workflow(client):
    """Editing any field preserves exclusive_tools/workflow the UI never sends (F2)."""
    from deerflow.config.subagents_user_config import load_user_subagent, save_user_subagent
    from deerflow.subagents.config import SubagentConfig

    # Seed a per-user config (as a hand-authored YAML would) carrying fields
    # the designer form does not expose.
    save_user_subagent(
        SubagentConfig(
            name="wf-agent",
            description="orig",
            exclusive_tools=["some_tool"],
            workflow="mod:obj",
        ),
        "default",
    )
    r = client.put("/api/subagents/wf-agent", json={"description": "edited"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "edited"
    reloaded = load_user_subagent("wf-agent", user_id="default")
    assert reloaded is not None
    assert reloaded.exclusive_tools == ["some_tool"]  # preserved, not stripped
    assert reloaded.workflow == "mod:obj"  # preserved, not stripped
