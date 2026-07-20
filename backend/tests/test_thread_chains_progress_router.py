"""Direct-call tests for the chain progress GET/DELETE endpoints.

Mirrors the ``test_artifacts_router`` pattern: the decorated handlers are
invoked via ``call_unwrapped`` inside ``asyncio.run`` (the sandbox blocks
worker-thread file IO under ``asyncio.to_thread``, so we replace the on-disk
``ChainProgressStore`` with an in-memory fake - the handlers' real logic for
``resumable`` computation, permission paths, and response shaping is still
exercised end-to-end).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from _router_auth_helpers import call_unwrapped
from langgraph.checkpoint.memory import InMemorySaver

import app.gateway.routers.thread_runs as thread_runs_mod
from app.gateway.routers import thread_runs
from deerflow.chains import progress as progress_mod
from deerflow.chains.parser import parse_chain_file
from deerflow.chains.progress import ChainNodeProgress, ChainProgress
from deerflow.chains.types import ChainCategory
from deerflow.runtime import RunManager
from deerflow.runtime.runs.store.memory import MemoryRunStore

CHAIN_YAML = """
description: example pipeline
nodes:
  researcher:
    subagent: general-purpose
    prompt: "research: {input}"
  reporter:
    subagent: general-purpose
    depends_on: [researcher]
"""


def _chain(tmp_path: Path):
    f = tmp_path / "example-pipeline.yaml"
    f.write_text(CHAIN_YAML, encoding="utf-8")
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    return chain


class _FakeProgressStore:
    """In-memory stand-in for ``ChainProgressStore`` keyed by chain name."""

    _registry: dict[tuple[str, str, str], ChainProgress] = {}

    def __init__(self, thread_id: str, chain_name: str, *, user_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.chain_name = chain_name
        self.user_id = user_id or "default"

    @property
    def _key(self):
        return (self.thread_id, self.chain_name, self.user_id)

    def load(self):
        return self._registry.get(self._key)

    def delete(self) -> bool:
        return self._registry.pop(self._key, None) is not None

    @staticmethod
    def list_for_thread(thread_id: str, *, user_id: str | None = None) -> list[ChainProgress]:
        uid = user_id or "default"
        return [v for (t, _c, u), v in _FakeProgressStore._registry.items() if t == thread_id and u == uid]


def _install_fake(monkeypatch):
    _FakeProgressStore._registry.clear()
    monkeypatch.setattr(progress_mod, "ChainProgressStore", _FakeProgressStore)

    # Run progress IO synchronously so the test never dispatches to the
    # asyncio default executor (whose shutdown hangs under the sandbox).
    async def _sync_to_thread(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(thread_runs_mod, "_to_thread", _sync_to_thread)


def _seed(*, status="interrupted", completed=("researcher",)):
    progress = ChainProgress(
        chain_name="example-pipeline",
        run_id="run-1",
        thread_id="thread-1",
        user_id="default",
        status=status,
        input="write a report",
        chain_hash="h",
        nodes={
            "researcher": ChainNodeProgress(),
            "reporter": ChainNodeProgress(),
        },
        terminal_nodes=["reporter"],
    )
    for node in completed:
        progress.nodes[node].status = "completed"
        progress.nodes[node].result = f"{node}-result"
    _FakeProgressStore._registry[("thread-1", "example-pipeline", "default")] = progress
    return progress


def _make_request(*, run_manager=None, checkpointer=None, thread_store=None, user_id="default"):
    run_manager = run_manager or RunManager(store=MemoryRunStore())
    checkpointer = checkpointer or InMemorySaver()
    thread_store_mock = thread_store or MagicMock()
    thread_store_mock.check_access = AsyncMock(return_value=True)

    app_state = SimpleNamespace(
        run_manager=run_manager,
        checkpointer=checkpointer,
        thread_store=thread_store_mock,
    )
    state = SimpleNamespace(
        user=SimpleNamespace(id=user_id, system_role="user"),
        auth_source="auth_disabled",
    )
    app = SimpleNamespace(state=app_state)
    return SimpleNamespace(
        app=app,
        state=state,
        cookies={},
        headers={},
        _deerflow_test_bypass_auth=True,
    )


def test_list_progress_returns_resumable_entry(monkeypatch):
    _install_fake(monkeypatch)
    _seed()
    request = _make_request()
    result = asyncio.run(call_unwrapped(thread_runs.list_chain_progress, "thread-1", request=request))
    assert len(result.chains) == 1
    entry = result.chains[0]
    assert entry.chain_name == "example-pipeline"
    assert entry.status == "interrupted"
    assert entry.completed_count == 1
    assert entry.total_count == 2
    assert entry.resumable is True


def test_list_progress_excludes_completed(monkeypatch):
    _install_fake(monkeypatch)
    _seed(status="completed", completed=("researcher", "reporter"))
    request = _make_request()
    result = asyncio.run(call_unwrapped(thread_runs.list_chain_progress, "thread-1", request=request))
    assert result.chains[0].resumable is False
    assert result.chains[0].status == "completed"


def test_list_progress_empty_when_no_progress(monkeypatch):
    _install_fake(monkeypatch)
    request = _make_request()
    result = asyncio.run(call_unwrapped(thread_runs.list_chain_progress, "thread-1", request=request))
    assert result.chains == []


def test_get_progress_detail(monkeypatch):
    _install_fake(monkeypatch)
    _seed()
    request = _make_request()
    result = asyncio.run(call_unwrapped(thread_runs.get_chain_progress, "thread-1", "example-pipeline", request=request))
    assert result.chain_name == "example-pipeline"
    assert {n.name for n in result.nodes} == {"researcher", "reporter"}
    researcher = next(n for n in result.nodes if n.name == "researcher")
    assert researcher.status == "completed"
    assert researcher.result == "researcher-result"


def test_get_progress_404_when_missing(monkeypatch):
    import pytest
    from fastapi import HTTPException

    _install_fake(monkeypatch)
    request = _make_request()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_unwrapped(thread_runs.get_chain_progress, "thread-1", "nope", request=request))
    assert exc.value.status_code == 404


def test_delete_progress(monkeypatch):
    _install_fake(monkeypatch)
    _seed()
    request = _make_request()
    res = asyncio.run(call_unwrapped(thread_runs.delete_chain_progress, "thread-1", "example-pipeline", request=request))
    assert res.status_code == 204
    assert ("thread-1", "example-pipeline", "default") not in _FakeProgressStore._registry


def test_delete_progress_404_when_missing(monkeypatch):
    import pytest
    from fastapi import HTTPException

    _install_fake(monkeypatch)
    request = _make_request()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call_unwrapped(thread_runs.delete_chain_progress, "thread-1", "nope", request=request))
    assert exc.value.status_code == 404
