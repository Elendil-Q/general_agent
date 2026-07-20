"""Tests for the ``merge_node_outputs`` reducer.

The reducer is the correctness precondition for chain resume: parallel root
nodes must not clobber each other's results, and an explicit empty dict must
clear stale state from a previous turn.
"""

from __future__ import annotations

from deerflow.agents.chain_agent.state import merge_node_outputs


def test_none_new_preserves_existing():
    assert merge_node_outputs({"a": "1"}, None) == {"a": "1"}


def test_none_existing_uses_new():
    assert merge_node_outputs(None, {"a": "1"}) == {"a": "1"}


def test_empty_dict_clears():
    assert merge_node_outputs({"a": "1"}, {}) == {}


def test_merges_incremental_updates():
    existing = {"researcher": "R"}
    # Two parallel branches each contribute one node.
    after_root1 = merge_node_outputs(existing, {"root1": "X"})
    after_root2 = merge_node_outputs(after_root1, {"root2": "Y"})
    assert after_root2 == {"researcher": "R", "root1": "X", "root2": "Y"}


def test_new_overrides_existing_key():
    assert merge_node_outputs({"a": "old"}, {"a": "new"}) == {"a": "new"}
