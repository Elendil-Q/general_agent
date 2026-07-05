"""Tests for chain spec parsing and topology validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.chains.parser import parse_chain_file
from deerflow.chains.types import ChainCategory


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_parses_valid_linear_chain(tmp_path: Path):
    f = _write(
        tmp_path,
        "linear",
        """
description: A linear chain
nodes:
  step-a:
    subagent: general-purpose
    depends_on: []
  step-b:
    subagent: general-purpose
    depends_on: [step-a]
""",
    )
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    assert chain.name == "linear"
    assert chain.description == "A linear chain"
    assert list(chain.nodes) == ["step-a", "step-b"]
    assert chain.nodes["step-b"].depends_on == ["step-a"]


def test_parses_parallel_roots_and_join(tmp_path: Path):
    f = _write(
        tmp_path,
        "fanout",
        """
description: fan-out then join
nodes:
  a:
    subagent: general-purpose
  b:
    subagent: general-purpose
  c:
    subagent: general-purpose
    depends_on: [a, b]
""",
    )
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    assert chain.nodes["a"].depends_on == []
    assert chain.nodes["c"].depends_on == ["a", "b"]


def test_name_taken_from_filename_not_yaml(tmp_path: Path):
    f = _write(
        tmp_path,
        "my-chain",
        """
description: name comes from file
name: ignored
nodes:
  only:
    subagent: general-purpose
""",
    )
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    assert chain.name == "my-chain"


def test_rejects_missing_description(tmp_path: Path):
    f = _write(
        tmp_path,
        "nodesc",
        """
nodes:
  only:
    subagent: general-purpose
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


def test_rejects_empty_nodes(tmp_path: Path):
    f = _write(
        tmp_path,
        "empty",
        """
description: empty nodes
nodes: {}
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


def test_rejects_node_missing_subagent(tmp_path: Path):
    f = _write(
        tmp_path,
        "nosub",
        """
description: no subagent
nodes:
  only:
    depends_on: []
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


def test_rejects_dangling_depends_on(tmp_path: Path):
    f = _write(
        tmp_path,
        "dangling",
        """
description: dangling dep
nodes:
  a:
    subagent: general-purpose
    depends_on: [ghost]
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


@pytest.mark.parametrize(
    "edges",
    [
        # a -> b -> a
        {"a": ["b"], "b": ["a"]},
        # a -> b -> c -> a
        {"a": ["b"], "b": ["c"], "c": ["a"]},
    ],
)
def test_rejects_dependency_cycle(tmp_path: Path, edges: dict[str, list[str]]):
    nodes_yaml = "\n".join(f"  {n}:\n    subagent: general-purpose\n    depends_on: {deps}" for n, deps in edges.items())
    f = _write(
        tmp_path,
        "cyclic",
        f"""
description: cyclic
nodes:
{nodes_yaml}
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


def test_rejects_invalid_filename(tmp_path: Path):
    # Uppercase / underscores are not hyphen-case
    f = _write(
        tmp_path,
        "Bad_Name",
        """
description: bad name
nodes:
  only:
    subagent: general-purpose
""",
    )
    assert parse_chain_file(f, ChainCategory.PUBLIC) is None


def test_optional_prompt_parsed(tmp_path: Path):
    f = _write(
        tmp_path,
        "withprompt",
        """
description: prompt
nodes:
  only:
    subagent: general-purpose
    prompt: "do {input} with {node_outputs.prev}"
""",
    )
    chain = parse_chain_file(f, ChainCategory.PUBLIC)
    assert chain is not None
    assert chain.nodes["only"].prompt == "do {input} with {node_outputs.prev}"


def test_returns_none_for_missing_file(tmp_path: Path):
    assert parse_chain_file(tmp_path / "nonexistent.yaml", ChainCategory.PUBLIC) is None
