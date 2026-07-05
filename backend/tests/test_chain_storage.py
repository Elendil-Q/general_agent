"""Tests for chain storage discovery (flat yaml, custom over public)."""

from __future__ import annotations

from pathlib import Path

from deerflow.chains.storage.chain_storage import ChainStorage, reset_chain_storage
from deerflow.chains.types import ChainCategory


def _write(root: Path, category: str, name: str, body: str) -> None:
    cat_dir = root / category
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / f"{name}.yaml").write_text(body, encoding="utf-8")


def _chain_yaml(name: str, deps: dict[str, list[str]] | None = None) -> str:
    nodes = deps or {"only": []}
    nodes_yaml = "\n".join(f"  {n}:\n    subagent: general-purpose\n    depends_on: {d}" for n, d in nodes.items())
    return f"description: chain {name}\nnodes:\n{nodes_yaml}\n"


def test_loads_chains_from_public(tmp_path: Path):
    _write(tmp_path, "public", "alpha", _chain_yaml("alpha"))
    _write(tmp_path, "public", "beta", _chain_yaml("beta"))
    storage = ChainStorage(host_root=tmp_path)
    chains = storage.load_chains()
    assert [c.name for c in chains] == ["alpha", "beta"]
    assert all(c.category is ChainCategory.PUBLIC for c in chains)


def test_custom_overrides_public(tmp_path: Path):
    _write(tmp_path, "public", "shared", _chain_yaml("public-version"))
    _write(tmp_path, "custom", "shared", _chain_yaml("custom-version"))
    storage = ChainStorage(host_root=tmp_path)
    chains = storage.load_chains()
    shared = [c for c in chains if c.name == "shared"]
    assert len(shared) == 1
    assert shared[0].category is ChainCategory.CUSTOM
    assert shared[0].description == "chain custom-version"


def test_load_single_by_name(tmp_path: Path):
    _write(tmp_path, "public", "alpha", _chain_yaml("alpha"))
    storage = ChainStorage(host_root=tmp_path)
    assert storage.load_chain("alpha") is not None
    assert storage.load_chain("nonexistent") is None


def test_empty_root_returns_empty(tmp_path: Path):
    storage = ChainStorage(host_root=tmp_path / "missing")
    assert storage.load_chains() == []


def test_supports_yml_suffix(tmp_path: Path):
    (tmp_path / "public").mkdir(parents=True)
    (tmp_path / "public" / "old.yml").write_text(_chain_yaml("old"), encoding="utf-8")
    storage = ChainStorage(host_root=tmp_path)
    chains = storage.load_chains()
    assert len(chains) == 1
    assert chains[0].name == "old"


def test_invalid_chain_skipped(tmp_path: Path):
    _write(tmp_path, "public", "good", _chain_yaml("good"))
    # invalid: no nodes
    (tmp_path / "public" / "bad.yaml").write_text("description: bad\nnodes: {}\n", encoding="utf-8")
    storage = ChainStorage(host_root=tmp_path)
    chains = storage.load_chains()
    assert [c.name for c in chains] == ["good"]


def teardown_module():
    # Ensure the process singleton doesn't leak between test modules.
    reset_chain_storage()
