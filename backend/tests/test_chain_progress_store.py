"""Tests for ``ChainProgressStore`` and ``compute_chain_hash``.

Covers create/load/update/list/delete/resume plus hash-based drift rejection.
All file IO is redirected under a per-test ``DEER_FLOW_HOME`` so the global
``Paths`` singleton resolves to ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.chains.parser import parse_chain_file
from deerflow.chains.progress import ChainProgressStore
from deerflow.chains.types import ChainCategory, compute_chain_hash

CHAIN_YAML = """
description: example pipeline
nodes:
  researcher:
    subagent: general-purpose
    prompt: "research: {input}"
  synthesizer:
    subagent: general-purpose
    depends_on: [researcher]
    prompt: "synth from: {node_outputs.researcher}"
  reporter:
    subagent: general-purpose
    depends_on: [synthesizer]
"""


def _chain(tmp_path: Path):
    f = tmp_path / "example-pipeline.yaml"
    f.write_text(CHAIN_YAML, encoding="utf-8")
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    return chain


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    yield


def test_compute_chain_hash_stable_and_sensitive(tmp_path):
    chain = _chain(tmp_path)
    h1 = compute_chain_hash(chain)
    h2 = compute_chain_hash(chain)
    assert h1 == h2
    # Touching the prompt changes the hash.
    chain.nodes["researcher"].prompt = "different: {input}"
    assert compute_chain_hash(chain) != h1


def test_create_load_and_counts(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    progress = store.create(chain, "run-1", "do the thing")
    assert progress.status == "running"
    assert progress.total_count() == 3
    assert progress.completed_count() == 0
    assert progress.input == "do the thing"
    assert set(progress.nodes) == {"researcher", "synthesizer", "reporter"}
    assert "reporter" in progress.terminal_nodes

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.run_id == "run-1"
    assert reloaded.chain_hash == progress.chain_hash


def test_update_node_marks_completed_when_all_done(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "x")
    store.update_node("run-1", "researcher", "R")
    assert store.load().status == "running"
    store.update_node("run-1", "synthesizer", "S")
    assert store.load().status == "running"
    store.update_node("run-1", "reporter", "P")
    # All nodes completed -> chain auto-completes.
    assert store.load().status == "completed"


def test_mark_node_running_and_completed_nodes(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "x")
    store.mark_node_running("run-1", "researcher")
    assert store.load().nodes["researcher"].status == "running"
    store.update_node("run-1", "researcher", "R")
    assert store.load().completed_nodes() == {"researcher": "R"}


def test_start_resume_keeps_completed_resets_rest(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "x")
    store.update_node("run-1", "researcher", "R")
    store.mark_status("run-1", "interrupted")

    resumed = store.start_resume("run-2")
    assert resumed is not None
    assert resumed.run_id == "run-2"
    assert resumed.status == "running"
    # Completed node keeps its result; others reset to pending.
    assert resumed.nodes["researcher"].status == "completed"
    assert resumed.nodes["researcher"].result == "R"
    assert resumed.nodes["synthesizer"].status == "pending"
    assert resumed.completed_nodes() == {"researcher": "R"}


def test_start_resume_returns_none_when_absent(tmp_path):
    store = ChainProgressStore("thread-1", "nope", user_id="default")
    assert store.start_resume("run-9") is None


def test_run_id_guard_ignores_stale_writes(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "x")
    # A write with a different run_id must be ignored.
    store.update_node("run-other", "researcher", "STALE")
    assert store.load().nodes["researcher"].status == "pending"


def test_list_for_thread_and_delete(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "x")
    # A second chain under the same thread.
    other_yaml = """
description: other
nodes:
  a:
    subagent: general-purpose
"""
    f = tmp_path / "other.yaml"
    f.write_text(other_yaml, encoding="utf-8")
    other_chain = parse_chain_file(f, ChainCategory.PUBLIC)
    other_store = ChainProgressStore("thread-1", other_chain.name, user_id="default")
    other_store.create(other_chain, "run-2", "y")

    listed = ChainProgressStore.list_for_thread("thread-1", user_id="default")
    assert {p.chain_name for p in listed} == {"example-pipeline", "other"}

    assert store.delete() is True
    assert store.load() is None
    # Idempotent delete.
    assert store.delete() is False


def test_create_overwrites_previous_progress(tmp_path):
    chain = _chain(tmp_path)
    store = ChainProgressStore("thread-1", chain.name, user_id="default")
    store.create(chain, "run-1", "first")
    store.update_node("run-1", "researcher", "R")
    assert store.load().completed_count() == 1
    # A fresh create wipes the prior run.
    store.create(chain, "run-2", "second")
    assert store.load().run_id == "run-2"
    assert store.load().completed_count() == 0
    assert store.load().input == "second"
