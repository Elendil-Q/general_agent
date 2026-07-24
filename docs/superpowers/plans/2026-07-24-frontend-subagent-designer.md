# Frontend Subagent Designer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a frontend designer + backend CRUD API so users can create, edit, and delete custom subagent types (the `task`-tool delegation targets), stored per-user as single YAML files and resolved at runtime via an additive per-user shadow layer.

**Architecture:** New backend module `config/subagents_user_config.py` (per-user YAML storage via the existing `parse_subagent_file`), an additive `user_id` kwarg threaded through `subagents/registry.py` and two runtime call sites (`task_tool`, lead-agent prompt), and a new `gateway/routers/subagents.py` CRUD router mirroring `routers/agents.py`. The frontend mirrors the existing `/workspace/agents` feature: `core/subagents/` (api/hooks/types), a gallery + designer form, sidebar entry, and i18n.

**Tech Stack:** Python 3.11+/FastAPI/Pydantic/YAML (backend); Next.js 16/React 19/TypeScript/TanStack Query/Shadcn UI (frontend).

## Global Constraints

- Backend tests are TDD-mandatory (`backend/tests/`, `cd backend && make test`). Pytest, monkeypatch for registry/`task_tool` isolines.
- ruff is enforced — run `cd backend && make format` before push; `make lint` must pass.
- Frontend: `@/*` path alias → `src/*`; imports alphabetized, inline type imports (`import { type Foo }`); `cn()` for conditional classes; `ui/` components are generated (don't hand-edit).
- Subagent YAML format is single-file (flat `.yaml`), parsed by existing `parse_subagent_file` — do NOT introduce a dir+SOUL.md layout.
- Precedence (shadow rule): built-in names reserved (validation rejects) → per-user shadows global → global. `user_id=None` paths byte-identical to today.
- `disallowed_tools` default `["task"]` is applied by the registry at build time, never persisted in the YAML.
- Per-user subagent storage path: `users/{user_id}/subagents/{name}.yaml` (add `Paths.user_subagent_dir` + `Paths.user_subagents_dir` helpers).
- Built-in subagents (`general-purpose`, `bash`) are read-only in the UI; writes 403.
- The spec lives at `docs/superpowers/specs/2026-07-24-frontend-subagent-designer-design.md` — read it for rationale.

## File Structure

### Backend (create/modify)

- **Create** `backend/packages/harness/deerflow/config/subagents_user_config.py` — per-user YAML storage (list/load/save/delete + name validation). Mirrors `agents_config.py` file conventions but flat-file.
- **Modify** `backend/packages/harness/deerflow/config/paths.py` — add `user_subagents_dir(user_id)` and `user_subagent_dir(user_id, name)` helpers.
- **Modify** `backend/packages/harness/deerflow/subagents/registry.py` — add optional `user_id` kwarg to `get_subagent_config`, `get_subagent_names`, `get_available_subagent_names`; per-user layer consulted before global custom lookup.
- **Modify** `backend/packages/harness/deerflow/tools/builtins/task_tool.py` — pass `user_id=resolve_runtime_user_id(runtime)` to `get_subagent_config` + `get_available_subagent_names`.
- **Modify** `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` — pass `user_id` into `get_available_subagent_names` (signature plumbed from caller).
- **Create** `backend/app/gateway/routers/subagents.py` — CRUD + name-check + catalogs router, mirroring `routers/agents.py`.
- **Modify** `backend/app/gateway/router.py` (or wherever routers register) — include the new subagents router.

### Frontend (create/modify)

- **Create** `frontend/src/core/subagents/types.ts` — `Subagent`, `SubagentCatalogs`, request types.
- **Create** `frontend/src/core/subagents/api.ts` — fetcher functions + error classes.
- **Create** `frontend/src/core/subagents/hooks.ts` — TanStack Query hooks.
- **Create** `frontend/src/core/subagents/index.ts` — barrel.
- **Create** `frontend/src/app/workspace/subagents/page.tsx` — gallery.
- **Create** `frontend/src/app/workspace/subagents/new/page.tsx` — new designer.
- **Create** `frontend/src/app/workspace/subagents/[name]/page.tsx` — edit / read-only.
- **Create** `frontend/src/components/workspace/subagents/subagent-gallery.tsx`
- **Create** `frontend/src/components/workspace/subagents/subagent-card.tsx`
- **Create** `frontend/src/components/workspace/subagents/subagent-designer-form.tsx` — shared form + YAML preview.
(Detailed task bodies follow in subsequent sections.)

### Task 1: Paths helpers

**Files:**
- Modify: `backend/packages/harness/deerflow/config/paths.py` (add to `Paths` class after `user_agent_memory_file`, ~line 225)
- Test: `backend/tests/test_paths.py` (add cases) — verify existing test file; if none, add functions to `tests/test_config_paths.py` or create `tests/test_paths.py`. Run `cd backend && pytest tests/test_paths.py -k user_subagent -v` after.

**Interfaces:**
- Consumes: `Paths.user_dir(user_id)` (already exists, line 180) and `_validate_user_id`.
- Produces: `Paths.user_subagents_dir(user_id) -> Path` = `{base_dir}/users/{user_id}/subagents/`; `Paths.user_subagent_dir(user_id, name) -> Path` = `.../subagents/{name.lower()}.yaml`-style dir member (flat-file path, not a directory). Naming of the subagent file lives here so storage code stays format-agnostic.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_paths.py` (create if absent; mirror existing path-test style):

```python
from deerflow.config.paths import get_paths


def test_user_subagents_dir():
    paths = get_paths()
    p = paths.user_subagents_dir("default")
    assert p == paths.user_dir("default") / "subagents"


def test_user_subagent_dir_is_flat_file_path():
    paths = get_paths()
    p = paths.user_subagent_dir("default", "Deep-Researcher")
    # flat-file: the path is the .yaml file itself, name lower-cased
    assert p == paths.user_subagents_dir("default") / "deep-researcher.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_paths.py -k user_subagent -v`
Expected: FAIL with `AttributeError: 'Paths' object has no attribute 'user_subagents_dir'`

- [ ] **Step 3: Write minimal implementation**

Insert into `Paths` class in `paths.py` right after `user_agent_memory_file` (line 224):

```python
    def user_subagents_dir(self, user_id: str) -> Path:
        """Per-user root for that user's custom subagent definitions: `{base_dir}/users/{user_id}/subagents/`."""
        return self.user_dir(user_id) / "subagents"

    def user_subagent_dir(self, user_id: str, name: str) -> Path:
        """Per-user flat-file subagent definition path: `{base_dir}/users/{user_id}/subagents/{name}.yaml`."""
        return self.user_subagents_dir(user_id) / f"{name.lower()}.yaml"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_paths.py -k user_subagent -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add tests/test_paths.py packages/harness/deerflow/config/paths.py
git commit -m "feat(paths): add per-user subagent directory helpers"
```

### Task 2: Per-user subagent storage module

**Files:**
- Create: `backend/packages/harness/deerflow/config/subagents_user_config.py`
- Test: `backend/tests/test_subagents_user_config.py`

**Interfaces:**
- Consumes: `Paths.user_subagents_dir` / `user_subagent_dir` (Task 1); `deerflow.subagents.config.SubagentConfig`; `deerflow.subagents.storage.parse_subagent_file` for reads; `deerflow.subagents.builtins.BUILTIN_SUBAGENTS` for name reservation.
- Produces:
  - `validate_subagent_name(name: str | None) -> str | None` — returns validated lowercased name or `None` if invalid/empty/builtin.
  - `list_user_subagents(user_id: str) -> list[SubagentConfig]` — scan dir, parse YAMLs, skip unreadable.
  - `list_user_subagent_names(user_id: str) -> list[str]` — names only.
  - `load_user_subagent(name: str, user_id: str) -> SubagentConfig | None`
  - `save_user_subagent(config: SubagentConfig, user_id: str) -> None` — write flat YAML, omitting `None` fields; never writes `disallowed_tools`.
  - `delete_user_subagent(name: str, user_id: str) -> bool`

Storage layout per file: plain YAML with the `SubagentConfig` field names as keys (`name, description, system_prompt, tools, disallowed_tools, exclusive_tools, skills, skills_on_demand, model, max_turns, timeout_seconds, workflow`). `parse_subagent_file` already reads these keys.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_subagents_user_config.py`:

```python
import pytest

from deerflow.config.subagents_user_config import (
    delete_user_subagent,
    list_user_subagent_names,
    list_user_subagents,
    load_user_subagent,
    save_user_subagent,
    validate_subagent_name,
)
from deerflow.subagents.config import SubagentConfig


def test_validate_subagent_name_rejects_builtins():
    assert validate_subagent_name("general-purpose") is None
    assert validate_subagent_name("bash") is None
    assert validate_subagent_name("") is None
    assert validate_subagent_name("bad name!") is None
    assert validate_subagent_name("deep-researcher") == "deep-researcher"
    assert validate_subagent_name("Deep-Researcher") == "deep-researcher"


def test_save_and_load_user_subagent_round_trip(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "get_paths", lambda: type("P", (), {"user_subagents_dir": staticmethod(lambda uid: tmp_path), "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml")})())
    cfg = SubagentConfig(name="deep-researcher", description="research", system_prompt="be terse", tools=["read_file"], model="inherit", max_turns=50, timeout_seconds=900)
    save_user_subagent(cfg, "default")

    loaded = load_user_subagent("deep-researcher", "default")
    assert loaded is not None
    assert loaded.name == "deep-researcher"
    assert loaded.system_prompt == "be terse"
    assert loaded.tools == ["read_file"]


def test_save_user_subagent_does_not_persist_disallowed_tools(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "get_paths", lambda: type("P", (), {"user_subagents_dir": staticmethod(lambda uid: tmp_path), "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml")})())
    save_user_subagent(SubagentConfig(name="x", description="d"), "default")
    text = (tmp_path / "x.yaml").read_text()
    assert "disallowed_tools" not in text


def test_list_user_subagents_skips_unreadable(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "get_paths", lambda: type("P", (), {"user_subagents_dir": staticmethod(lambda uid: tmp_path), "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml")})())
    save_user_subagent(SubagentConfig(name="good", description="d"), "default")
    (tmp_path / "bad.yaml").write_text("not: valid: yaml: :\n  :")
    names = list_user_subagent_names("default")
    assert "good" in names
    assert "bad" not in names


def test_delete_user_subagent(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setattr(paths_module, "get_paths", lambda: type("P", (), {"user_subagents_dir": staticmethod(lambda uid: tmp_path), "user_subagent_dir": staticmethod(lambda uid, n: tmp_path / f"{n}.yaml")})())
    save_user_subagent(SubagentConfig(name="x", description="d"), "default")
    assert delete_user_subagent("x", "default") is True
    assert load_user_subagent("x", "default") is None
    assert delete_user_subagent("x", "default") is False
```

Note: the `monkeypatch` of `get_paths` returns a throwaway object with the two attributes the module calls. The module body must obtain paths via `get_paths()` (not a cached import) so this monkeypatch works.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_subagents_user_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'deerflow.config.subagents_user_config'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/packages/harness/deerflow/config/subagents_user_config.py`:

```python
"""Per-user custom subagent type storage (flat .yaml files).

Mirrors the lead-agent per-user convention but stores subagents as single
YAML files (reusing the existing :func:`parse_subagent_file` parser). A
per-user entry resolves below built-ins and above the shared/global layer
in :mod:`deerflow.subagents.registry`; built-in names are reserved and
rejected here.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
import yaml

from deerflow.config.paths import get_paths
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.storage import parse_subagent_file

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SUBAGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def validate_subagent_name(name: str | None) -> str | None:
    """Return a lowercased valid subagent name, or ``None`` if invalid/builtin."""
    if not name:
        return None
    if not _SUBAGENT_NAME_PATTERN.fullmatch(name):
        return None
    lower = name.lower()
    if lower in BUILTIN_SUBAGENTS:
        return None
    return lower


def _user_subagents_dir(user_id: str) -> Path:
    return get_paths().user_subagents_dir(user_id)


def list_user_subagents(user_id: str) -> list[SubagentConfig]:
    """Load all per-user subagent configs for ``user_id`` (skip unreadable)."""
    directory = _user_subagents_dir(user_id)
    if not directory.exists():
        return []
    configs: list[SubagentConfig] = []
    for f in sorted(directory.glob("*.yaml")):
        try:
            cfg = parse_subagent_file(f)
        except Exception:
            logger.warning("Skipping unreadable user subagent file: %s", f, exc_info=True)
            continue
        if cfg is not None:
            configs.append(cfg)
    return configs


def list_user_subagent_names(user_id: str) -> list[str]:
    """Return the names of all per-user subagent configs (dedup by name)."""
    directory = _user_subagents_dir(user_id)
    if not directory.exists():
        return []
    names: list[str] = []
    seen: set[str] = set()
    for f in sorted(directory.glob("*.yaml")):
        stem = f.stem
        if stem not in seen:
            seen.add(stem)
            names.append(stem)
    return names


def load_user_subagent(name: str, user_id: str) -> SubagentConfig | None:
    """Load one per-user subagent by name, or ``None`` if missing/unreadable."""
    normalized = validate_subagent_name(name)
    if normalized is None:
        return None
    path = get_paths().user_subagent_dir(user_id, normalized)
    if not path.exists():
        return None
    try:
        return parse_subagent_file(path)
    except Exception:
        logger.warning("Unreadable user subagent file: %s", path, exc_info=True)
        return None


def save_user_subagent(config: SubagentConfig, user_id: str) -> None:
    """Write a per-user subagent config as a flat YAML file.

    Omits ``None`` fields. Never writes ``disallowed_tools``: the registry
    applies the default ``["task"]`` at build time (recursion guard).
    """
    normalized = config.name.lower()
    path = get_paths().user_subagent_dir(user_id, normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "name": normalized,
        "description": config.description,
    }
    for key, value in [
        ("system_prompt", config.system_prompt),
        ("tools", config.tools),
        ("exclusive_tools", config.exclusive_tools),
        ("skills", config.skills),
        ("skills_on_demand", config.skills_on_demand),
        ("model", config.model),
        ("max_turns", config.max_turns),
        ("timeout_seconds", config.timeout_seconds),
        ("workflow", config.workflow),
    ]:
        if value is not None:
            data[key] = value
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def delete_user_subagent(name: str, user_id: str) -> bool:
    """Delete a per-user subagent file; return True if it existed."""
    normalized = validate_subagent_name(name)
    if normalized is None:
        return False
    path = get_paths().user_subagent_dir(user_id, normalized)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
```

Why `validate_subagent_name` is also used in `load_user_subagent`/`delete`: it rejects builtin names, so `get_subagent_config("bash", user_id=...)` for a user cannot be hijacked by writing `bash.yaml`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_subagents_user_config.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Lint**

Run: `cd backend && make lint && make format`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd backend && git add packages/harness/deerflow/config/subagents_user_config.py tests/test_subagents_user_config.py
git commit -m "feat(subagents): add per-user subagent storage module"
```
- **Create** `frontend/tests/unit/core/subagents/api.test.ts`
- **Create** `frontend/tests/unit/core/subagents/subagent-designer-form.test.tsx`

### Task 3: Registry per-user shadow layer

**Files:**
- Modify: `backend/packages/harness/deerflow/subagents/registry.py` (functions `get_subagent_config`, `get_subagent_names`, `get_available_subagent_names`, signature ~line 63 / 152 / 176)
- Test: `backend/tests/test_subagent_registry_user_shadow.py` (new)

**Interfaces:**
- Consumes: `deerflow.config.subagents_user_config.load_user_subagent` / `list_user_subagent_names` (Task 2); `BUILTIN_SUBAGENTS`; existing `_build_custom_subagent_config`, `_resolve_subagents_app_config`.
- Produces: `get_subagent_config(name, *, user_id=None, app_config=None)`, `get_subagent_names(*, user_id=None, app_config=None)`, `get_available_subagent_names(*, user_id=None, app_config=None)`. New `user_id` kwarg optional with `None` default — legacy callers unaffected.

The existing `get_subagent_config` consults `BUILTIN_SUBAGENTS`, then `_build_custom_subagent_config` (global config.yaml + shared files), then applies config.yaml attribute overrides. The per-user layer is inserted **between** built-in and global so per-user shadows global. Built-in names remain reserved.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_subagent_registry_user_shadow.py`:

```python
import pytest

from deerflow.subagents import registry as registry_module
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import get_available_subagent_names, get_subagent_config, get_subagent_names


def _reset():
    registry_module._reset_subagents_config() if hasattr(registry_module, "_reset_subagents_config") else None


def test_per_user_shadows_global(monkeypatch, tmp_path):
    # global custom via shared storage disabled; emulate global via config.yaml? Use a fresh user file.
    def fake_load_user(name, *, user_id):
        if user_id == "u1" and name == "researcher":
            return SubagentConfig(name="researcher", description="user-desc", system_prompt="user")
        return None

    def fake_list_user_names(*, user_id):
        return ["researcher"] if user_id == "u1" else []

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    _reset()

    cfg = get_subagent_config("researcher", user_id="u1")
    assert cfg is not None and cfg.description == "user-desc"
    # without user_id: still resolves via global layer (may be None here)
    assert get_subagent_config("researcher") is None


def test_builtin_still_wins_over_user_file(monkeypatch):
    def fake_load_user(name, *, user_id):
        if name == "bash":
            return SubagentConfig(name="bash", description="user-stolen", system_prompt="x")
        return None

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    # validate path rejects builtin names, but registry hardens too:
    _reset()
    from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
    cfg = get_subagent_config("bash", user_id="u1")
    assert cfg is not None and cfg.description == BUILTIN_SUBAGENTS["bash"].description


def test_get_subagent_names_dedup_with_user_shadow(monkeypatch):
    def fake_list_user_names(*, user_id):
        return ["general-purpose", "researcher"] if user_id == "u1" else []

    def fake_load_user(name, *, user_id):
        if user_id == "u1" and name == "researcher":
            return SubagentConfig(name="researcher", description="u")
        return None

    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    _reset()

    names = get_subagent_names(user_id="u1")
    assert names.count("general-purpose") == 1  # builtin reserved, no dup
    assert "researcher" in names


def test_user_id_none_unchanged(monkeypatch):
    # no user files consulted when user_id is None
    called = {"load": 0, "list": 0}

    def fake_load_user(name, *, user_id):
        called["load"] += 1
        return None

    def fake_list_user_names(*, user_id):
        called["list"] += 1
        return []

    monkeypatch.setattr(registry_module, "load_user_subagent", fake_load_user)
    monkeypatch.setattr(registry_module, "list_user_subagent_names", fake_list_user_names)
    _reset()
    get_subagent_config("general-purpose")
    get_subagent_names()
    assert called == {"load": 0, "list": 0}
```

Note: `list_user_subagent_names` is imported at module top of `registry.py` (see Step 3), so the monkeypatch target is `registry_module.load_user_subagent`. The functions use keyword `user_id`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_subagent_registry_user_shadow.py -v`
Expected: FAIL — `TypeError: get_subagent_config() got an unexpected keyword argument 'user_id'`

- [ ] **Step 3: Write minimal implementation**

In `registry.py`, add imports near the existing `storage` import:

```python
from deerflow.config.subagents_user_config import (
    load_user_subagent,
    list_user_subagent_names,
)
```

Then modify the three functions. Current `get_subagent_config` (line 63):

```python
def get_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    ...
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)
    if config is None:
        return None
    # ... existing override pass
```

Change signature to `(*, user_id=None, app_config=None)` and insert the per-user consult between BUILTIN and global custom:

```python
def get_subagent_config(name: str, *, user_id: str | None = None, app_config: Any | None = None) -> SubagentConfig | None:
    """... (docstring updated: user_id resolves per-user shadow layer) ..."""
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None and user_id is not None:
        config = load_user_subagent(name, user_id=user_id)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)
    if config is None:
        return None
    # existing config.yaml attribute-override pass applies on top of `config` unchanged
    ...
```

Update `get_subagent_names` (line 152) — add `user_id` kwarg, append user names after built-in and before/after storage merge (dedup keeping first occurrence; built-in wins):

```python
def get_subagent_names(*, user_id: str | None = None, app_config: Any | None = None) -> list[str]:
    """..."""
    names: list[str] = list(BUILTIN_SUBAGENTS.keys())
    if user_id is not None:
        for n in list_user_subagent_names(user_id=user_id):
            if n not in names:
                names.append(n)
    storage_kwargs = {"app_config": app_config} if app_config is not None else {}
    storage = get_or_new_subagent_storage(**storage_kwargs)
    for yaml_name in storage.list_names():
        if yaml_name not in names:
            names.append(yaml_name)
    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in subagents_config.custom_agents:
        if custom_name not in names:
            names.append(custom_name)
    return names
```

Update `get_available_subagent_names` (line 176) to accept and forward `user_id`:

```python
def get_available_subagent_names(*, user_id: str | None = None, app_config: Any | None = None) -> list[str]:
    """..."""
    names = get_subagent_names(user_id=user_id, app_config=app_config)
    try:
        # ... existing sandbox filter (bash availability etc.)
        ...
    except Exception:
        logger.debug(...)
        return names
    return names
```

Built-in names reserved: `BUILTIN_SUBAGENTS.get(name)` returns the built-in first, so a per-user file named `bash` is never consulted (built-in wins). `validate_subagent_name` in Task 2 also rejects built-in names at write time, so a `bash.yaml` user file can't be created via the API — but the registry hardens regardless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_subagent_registry_user_shadow.py -v`
Expected: PASS (all 4 tests)

Also run existing registry tests to confirm no regression:
Run: `cd backend && pytest tests/test_subagent_skills_config.py tests/test_subagent_timeout_config.py -v`
Expected: PASS (existing tests that call `get_subagent_config(name)` without `user_id` still work — kwarg optional)

- [ ] **Step 5: Lint**

Run: `cd backend && make lint && make format`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd backend && git add packages/harness/deerflow/subagents/registry.py tests/test_subagent_registry_user_shadow.py
git commit -m "feat(subagents): add per-user shadow layer to registry"
```

### Task 4: Runtime wiring (user_id threading)

**Files:**
- Modify: `backend/packages/harness/deerflow/tools/builtins/task_tool.py` (call sites ~line 270, 273)
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/prompt.py` (`_build_available_subagents_description` + its caller `_build_subagent_section`, plumbed from runtime user_id)
- Test: `backend/tests/test_task_tool_user_id.py` (new) + extend `tests/test_lead_prompt_subagents.py` if exists, else small new test.

**Interfaces:**
- Consumes: `Task 3` (`get_subagent_config(*, user_id=...)`, `get_available_subagent_names(*, user_id=...)`); `deerflow.runtime.user_context.resolve_runtime_user_id(runtime)`.
- Produces: functional wiring so that during a run, a per-user subagent type is discoverable by the lead's `task` tool and listed in the lead prompt.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_task_tool_user_id.py`:

```python
from deerflow.tools.builtins import task_tool as task_tool_module


def test_task_tool_threads_user_id_into_lookup(monkeypatch):
    captured = {}

    def fake_get_subagent_config(name, *, user_id=None, app_config=None):
        captured["user_id"] = user_id
        captured["name"] = name
        from deerflow.subagents.config import SubagentConfig
        return SubagentConfig(name=name, description="d")

    def fake_get_available_subagent_names(*, user_id=None, app_config=None):
        captured["names_user_id"] = user_id
        return [name_in_test]

    name_in_test = "researcher"

    class FakeRuntime:
        context = {"user_id": "u1", "app_config": None}

    monkeypatch.setattr(task_tool_module, "get_subagent_config", fake_get_subagent_config)
    monkeypatch.setattr(task_tool_module, "get_available_subagent_names", fake_get_available_subagent_names)
    monkeypatch.setattr(task_tool_module, "is_host_bash_allowed", lambda *a, **k: True)
    monkeypatch.setattr(task_tool_module, "_token_usage_cache_enabled", lambda *_a, **_k: False)

    # Minimal run of the task_tool body: the real one is a langchain @tool; invoke via its .func or .invoke.
    # See existing tests/test_task_tool_core_logic.py for the `_run_task_tool` helper pattern; reuse it.
    from tests.test_task_tool_core_logic import _run_task_tool

    _run_task_tool(task_tool_module.task_tool, runtime=FakeRuntime(), subagent_type="researcher", description="d", prompt="p")

    assert captured["user_id"] == "u1"
    assert captured["names_user_id"] in ("u1", None)  # available-names path only runs when app_config present; relax as needed
```

Notes: reuse the `_run_task_tool` harness already used in `tests/test_task_tool_core_logic.py` (see that file's helpers). On a first run if the helper isn't importable, copy the minimal harness (the existing file constructs a runtime stub and calls `task_tool_module.task_tool.invoke({...})`). Keep the test deterministic by patching `get_subagent_config` to a known config and stopping before any executor runs (patch `SubagentExecutor` to a noop that returns a terminal result, mirroring existing tests).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_task_tool_user_id.py -v`
Expected: FAIL — `captured` shows `user_id=None` because the current call sites don't pass `user_id`.

- [ ] **Step 3: Write minimal implementation**

In `task_tool.py`, import at top with the existing user-context imports:

```python
from deerflow.runtime.user_context import resolve_runtime_user_id
```

Then at the two call sites (lines ~270, 273) thread `user_id`:

```python
runtime_app_config = _get_runtime_app_config(runtime)
cache_token_usage = _token_usage_cache_enabled(runtime_app_config)
runtime_user_id = resolve_runtime_user_id(runtime)
available_subagent_names = (
    get_available_subagent_names(user_id=runtime_user_id, app_config=runtime_app_config)
    if runtime_app_config is not None
    else get_available_subagent_names(user_id=runtime_user_id)
)

config = (
    get_subagent_config(subagent_type, user_id=runtime_user_id, app_config=runtime_app_config)
    if runtime_app_config is not None
    else get_subagent_config(subagent_type, user_id=runtime_user_id)
)
if config is None:
    ...
```

In `agents/lead_agent/prompt.py`, the prompt builder `_build_subagent_section` currently takes `app_config`. It's called from a place that has runtime context; thread an optional `user_id` through. Trace the caller (likely in `agent` setup or where the prompt is assembled) and pass `resolve_runtime_user_id`-style user_id down to `_build_available_subagents_description` → `get_available_subagent_names(user_id=user_id, app_config=app_config)`. If the caller has no runtime handle, fall back to leaving `user_id=None` (prompt lists global+built-in only) — acceptable but document. Preferred: locate where the prompt is built during a run and thread user_id; otherwise pass `user_id=None` for now and add a follow-up note (mark this task partial and open a follow-up). The plan's success criterion #2 (usability without restart) requires the `task`-tool path to work; the prompt listing is a nicety. Treat the prompt change as "if reachable, thread it; otherwise leave None and note it."

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_task_tool_user_id.py tests/test_task_tool_core_logic.py -v`
Expected: PASS (new test passes; existing `test_task_tool_core_logic.py` should still pass — they monkeypatch the same symbols with signatures that now accept `user_id`; existing monkeypatch lambdas like `lambda _: None` would break because the new call passes `user_id=` and `app_config=` as keywords. Update those existing call sites to lambdas accepting `**, app_config`/`user_id` — see test file's fixture patterns.)

If `test_task_tool_core_logic.py` breaks: update its monkeypatch `get_subagent_config` lambdas from `lambda _: None` to `lambda name, **kw: None` (and `get_available_subagent_names` to `lambda **kw: [...]`). This is a mechanical variant of the existing monkeypatches — do it as part of this step.

- [ ] **Step 5: Lint**

Run: `cd backend && make lint && make format`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd backend && git add packages/harness/deerflow/tools/builtins/task_tool.py packages/harness/deerflow/agents/lead_agent/prompt.py tests/test_task_tool_user_id.py tests/test_task_tool_core_logic.py
git commit -m "feat(subagents): thread user_id through task_tool and lead prompt"
```


### Task 5: Subagents CRUD API router

**Files:**
- Create: `backend/app/gateway/routers/subagents.py`
- Modify: `backend/app/gateway/app.py` (register router, ~line 400 next to agents)
- Test: `backend/tests/test_routers_subagents.py`

**Interfaces:**
- Consumes: `subagents_user_config` (Task 2); `list_subagents`/`get_subagent_config`/`BUILTIN_SUBAGENTS` (Task 3); `get_app_config` for models; `get_available_tools` for tool names; skill list for skills catalog; `get_effective_user_id` for isolation.
- Produces: REST API `/api/subagents*` (list/get/create/update/delete/check/catalogs). No `agents_api.enabled` gate — subagent management is always on (keeps design intent simple; user isolation via filesystem).

Design: mirror `routers/agents.py` structure (Pydantic models, `asyncio.to_thread` for blocking FS, `_require_*` omitted). Response carries `readonly: bool` + `source: "builtin"|"global"|"user"`. Catalogs endpoint returns models/tools/skills for picker UI.

Determine subagent `source`/`readonly`: **built-in** (`name in BUILTIN_SUBAGENTS`) → `source="builtin", readonly=True`. **per-user** — exists in user's dir → `source="user", readonly=False`. **global** (not built-in, not per-user, but resolves via existing global layer) → `source="global", readonly=True`.

Export the global name-list helper from Task 3/4 (currently `get_subagent_names` already includes global + user + builtin; to check "is this name a global-only?": name resolves but no per-user file AND not builtin). Need a small helper.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routers_subagents.py` using `httpx` + the gateway ASGI app, or FastAPI `TestClient`. Mirror existing router test style in `backend/tests/` (look for `test_routers_agents.py` for the pattern — fixtures, client fixture, temp base dir). A minimal set:

```python
import pytest
from fastapi.testclient import TestClient

from deerflow.config import paths as paths_module
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS

# Reuse the test client fixture pattern from tests/test_routers_agents.py
# (it builds the app with a temp base_dir and a "default" user).


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
```

Reference `tests/test_routers_agents.py` for the `client` fixture (temp base dir, default user). If no such fixture exists, build one inline: construct the FastAPI app, override `get_paths()` via monkeypatch to a `tmp_path`, and create a `default` users dir.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_routers_subagents.py -v`
Expected: FAIL — `AttributeError` / 404 (router not mounted) / import error.

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/gateway/routers/subagents.py`:

```python
"""CRUD API for custom subagent types (the `task`-tool delegation targets)."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deerflow.config import get_app_config
from deerflow.config.subagents_user_config import (
    delete_user_subagent,
    list_user_subagents,
    load_user_subagent,
    save_user_subagent,
    validate_subagent_name,
)
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import get_available_subagent_names, get_subagent_config
from deerflow.tools.tools import get_available_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["subagents"])

BUILTIN_NAMES = set(BUILTIN_SUBAGENTS.keys())
MODEL_VALID_RANGE_MAX_TURNS = (1, 500)
MODEL_VALID_RANGE_TIMEOUT = (60, 7200)


class SubagentResponse(BaseModel):
    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str
    max_turns: int
    timeout_seconds: int
    readonly: bool
    source: str  # "builtin" | "global" | "user"
    overrides_global: bool = False


class SubagentsListResponse(BaseModel):
    subagents: list[SubagentResponse]


class SubagentCreateRequest(BaseModel):
    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900


class SubagentUpdateRequest(BaseModel):
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    timeout_seconds: int | None = None


class SubagentCatalogsResponse(BaseModel):
    models: list[dict]
    tools: list[str]
    skills: list[dict]


def _to_response(name: str, *, user_id: str) -> SubagentResponse:
    if name in BUILTIN_NAMES:
        cfg = BUILTIN_SUBAGENTS[name]
        return SubagentResponse(
            name=name, description=cfg.description, system_prompt=cfg.system_prompt,
            tools=cfg.tools, skills=cfg.skills, skills_on_demand=cfg.skills_on_demand,
            model=cfg.model, max_turns=cfg.max_turns, timeout_seconds=cfg.timeout_seconds,
            readonly=True, source="builtin",
        )
    user_cfg = load_user_subagent(name, user_id=user_id)
    if user_cfg is not None:
        overrides = False
        global_cfg = get_subagent_config(name, app_config=None)
        if global_cfg is not None and global_cfg.name == name and name not in BUILTIN_NAMES:
            overrides = True
        return SubagentResponse(
            name=user_cfg.name, description=user_cfg.description, system_prompt=user_cfg.system_prompt,
            tools=user_cfg.tools, skills=user_cfg.skills, skills_on_demand=user_cfg.skills_on_demand,
            model=user_cfg.model, max_turns=user_cfg.max_turns, timeout_seconds=user_cfg.timeout_seconds,
            readonly=False, source="user", overrides_global=overrides,
        )
    global_cfg = get_subagent_config(name, app_config=None)
    if global_cfg is not None:
        return SubagentResponse(
            name=global_cfg.name, description=global_cfg.description, system_prompt=global_cfg.system_prompt,
            tools=global_cfg.tools, skills=global_cfg.skills, skills_on_demand=global_cfg.skills_on_demand,
            model=global_cfg.model, max_turns=global_cfg.max_turns, timeout_seconds=global_cfg.timeout_seconds,
            readonly=True, source="global",
        )
    raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")


def _validate_fields(name: str, description: str, model: str, max_turns: int, timeout_seconds: int, tools, skills, catalogs) -> None:
    if not description:
        raise HTTPException(status_code=422, detail="description is required")
    app_models = {m["name"] for m in catalogs["models"]} | {"inherit"}
    if model not in app_models:
        raise HTTPException(status_code=422, detail=f"Unknown model '{model}'")
    if not (MODEL_VALID_RANGE_MAX_TURNS[0] <= max_turns <= MODEL_VALID_RANGE_MAX_TURNS[1]):
        raise HTTPException(status_code=422, detail=f"max_turns out of range {MODEL_VALID_RANGE_MAX_TURNS}")
    if not (MODEL_VALID_RANGE_TIMEOUT[0] <= timeout_seconds <= MODEL_VALID_RANGE_TIMEOUT[1]):
        raise HTTPException(status_code=422, detail=f"timeout_seconds out of range {MODEL_VALID_RANGE_TIMEOUT}")
    if tools is not None and not set(tools).issubset(catalogs["tools"]):
        raise HTTPException(status_code=422, detail="tools contains unknown name")
    if skills is not None and not set(skills).issubset(s["name"] for s in catalogs["skills"]):
        raise HTTPException(status_code=422, detail="skills contains unknown name")


def _build_catalogs() -> dict:
    # Models
    try:
        app_cfg = get_app_config()
        model_list = [{"name": m.name, "label": getattr(m, "label", None) or m.name} for m in app_cfg.models]
    except Exception:
        model_list = []
    model_list.insert(0, {"name": "inherit", "label": "Inherit parent"})
    # Tools
    try:
        tool_names = [t.name for t in get_available_tools()]
    except Exception:
        tool_names = []
    # Skills
    try:
        from deerflow.skills import get_skills_catalog  # adjust to real skill-listing helper
        skills_list = get_skills_catalog()  # list of {"name","description"}
    except Exception:
        skills_list = []
    return {"models": model_list, "tools": tool_names, "skills": skills_list}


@router.get("/subagents", response_model=SubagentsListResponse, summary="List Subagents")
async def list_subagents_endpoint() -> SubagentsListResponse:
    user_id = get_effective_user_id()
    all_names = await asyncio.to_thread(get_available_subagent_names, user_id=user_id)
    async def _build():
        out = []
        for n in all_names:
            try:
                out.append(_to_response(n, user_id=user_id))
            except HTTPException as e:
                if e.status_code == 404:
                    continue
                raise
        return out
    items = await asyncio.to_thread(lambda: [ _to_response(n, user_id=user_id) for n in all_names ])
    return SubagentsListResponse(subagents=items)


@router.get("/subagents/check", summary="Check Subagent Name")
async def check_subagent_name_endpoint(name: str) -> dict:
    try:
        app_cfg = get_app_config()
    except Exception:
        app_cfg = None
    available = validate_subagent_name(name) is not None
    if available:
        user_id = get_effective_user_id()
        # unavailable if a user file already exists with this name OR it's builtin
        if load_user_subagent(name, user_id=user_id) is not None:
            available = False
    return {"available": available, "name": (name.lower() if available else name)}


@router.get("/subagents/catalogs", response_model=SubagentCatalogsResponse, summary="Subagent Catalogs")
async def get_subagent_catalogs_endpoint() -> SubagentCatalogsResponse:
    catalogs = await asyncio.to_thread(_build_catalogs)
    return SubagentCatalogsResponse(**catalogs)


@router.get("/subagents/{name}", response_model=SubagentResponse, summary="Get Subagent")
async def get_subagent_endpoint(name: str) -> SubagentResponse:
    return await asyncio.to_thread(_to_response, name, user_id=get_effective_user_id())


@router.post("/subagents", response_model=SubagentResponse, status_code=201, summary="Create Subagent")
async def create_subagent_endpoint(request: SubagentCreateRequest) -> SubagentResponse:
    user_id = get_effective_user_id()
    catalogs = await asyncio.to_thread(_build_catalogs)
    normalized = validate_subagent_name(request.name)
    if normalized is None:
        raise HTTPException(status_code=422, detail="Invalid or reserved subagent name")
    if load_user_subagent(normalized, user_id=user_id) is not None:
        raise HTTPException(status_code=409, detail=f"Subagent '{normalized}' already exists")
    _validate_fields(normalized, request.description, request.model, request.max_turns, request.timeout_seconds, request.tools, request.skills, catalogs)
    cfg = SubagentConfig(
        name=normalized, description=request.description, system_prompt=request.system_prompt,
        tools=request.tools, skills=request.skills, skills_on_demand=request.skills_on_demand,
        model=request.model, max_turns=request.max_turns, timeout_seconds=request.timeout_seconds,
    )
    try:
        await asyncio.to_thread(save_user_subagent, cfg, user_id)
    except Exception as e:
        logger.error(f"Failed to create subagent '{normalized}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create subagent: {e}")
    return await asyncio.to_thread(_to_response, normalized, user_id=user_id)


@router.put("/subagents/{name}", response_model=SubagentResponse, summary="Update Subagent")
async def update_subagent_endpoint(name: str, request: SubagentUpdateRequest) -> SubagentResponse:
    user_id = get_effective_user_id()
    if name in BUILTIN_NAMES:
        raise HTTPException(status_code=403, detail="Built-in subagents are read-only")
    existing = load_user_subagent(name, user_id=user_id)
    if existing is None:
        # could be global — refuse to mutate global
        if get_subagent_config(name, app_config=None) is not None:
            raise HTTPException(status_code=403, detail="Global subagents are read-only; clone to your own name to edit")
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")
    catalogs = await asyncio.to_thread(_build_catalogs)
    updated = SubagentConfig(
        name=existing.name,
        description=request.description if request.description is not None else existing.description,
        system_prompt=request.system_prompt if request.system_prompt is not None else existing.system_prompt,
        tools=request.tools if request.tools is not None else existing.tools,
        skills=request.skills if request.skills is not None else existing.skills,
        skills_on_demand=request.skills_on_demand if request.skills_on_demand is not None else existing.skills_on_demand,
        model=request.model if request.model is not None else existing.model,
        max_turns=request.max_turns if request.max_turns is not None else existing.max_turns,
        timeout_seconds=request.timeout_seconds if request.timeout_seconds is not None else existing.timeout_seconds,
    )
    _validate_fields(existing.name, updated.description, updated.model, updated.max_turns, updated.timeout_seconds, updated.tools, updated.skills, catalogs)
    try:
        await asyncio.to_thread(save_user_subagent, updated, user_id)
    except Exception as e:
        logger.error(f"Failed to update subagent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update subagent: {e}")
    return await asyncio.to_thread(_to_response, existing.name, user_id=user_id)


@router.delete("/subagents/{name}", status_code=204, summary="Delete Subagent")
async def delete_subagent_endpoint(name: str) -> None:
    user_id = get_effective_user_id()
    if name in BUILTIN_NAMES:
        raise HTTPException(status_code=403, detail="Built-in subagents are read-only")
    if load_user_subagent(name, user_id=user_id) is None:
        if get_subagent_config(name, app_config=None) is not None:
            raise HTTPException(status_code=403, detail="Global subagents are read-only; cannot delete")
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")
    try:
        await asyncio.to_thread(delete_user_subagent, name, user_id)
    except Exception as e:
        logger.error(f"Failed to delete subagent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete subagent: {e}")
```

Caveats for the implementer:
- `get_skills_catalog` import needs verification: grep for the real skill-listing helper in `deerflow/skills/` and use that exact name. If it's elsewhere, import the right function. Do not invent a function.
- `get_available_tools()` returns `BaseTool` list; `.name` is the attribute. Run `cd backend && python -c "from deerflow.tools.tools import get_available_tools; print([t.name for t in get_available_tools()])"` to confirm the shape before finalizing.
- The `asyncio.to_thread(lambda: [...])` — lambdas can't be passed to `to_thread` directly in some pythons; use a module function `_build_list(all_names, user_id)` instead. Replace the lambda with a real helper.

Now register the router in `backend/app/gateway/app.py` next to the agents router (line ~400):

```python
from .routers import subagents
# ...
    # Subagents API is mounted at /api/subagents
    app.include_router(subagents.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_routers_subagents.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Lint**

Run: `cd backend && make lint && make format`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/gateway/routers/subagents.py app/gateway/app.py tests/test_routers_subagents.py
git commit -m "feat(subagents): add CRUD API router for custom subagent types"
```

### Task 6: Frontend core layer (types/api/hooks)

**Files:**
- Create: `frontend/src/core/subagents/types.ts`
- Create: `frontend/src/core/subagents/api.ts`
- Create: `frontend/src/core/subagents/hooks.ts`
- Create: `frontend/src/core/subagents/index.ts`
- Create: `frontend/tests/unit/core/subagents/api.test.ts`

**Interfaces:**
- Consumes: `@/core/api/fetcher` (`fetch`), `@/core/config` (`getBackendBaseURL`). Mirror `@/core/agents/api.ts` exactly for error classes + fetch building (see `agents/api.ts` lines 1-124: `AgentNameCheckError`, `AgentsApiDisabledError`, `isAgentsApiDisabledDetail`, fetch helpers, `checkAgentName`).
- Produces: `Subagent` interface, `SubagentCatalogs`, `CreateSubagentRequest`, `UpdateSubagentRequest`, api functions, query hooks.

Run `cat frontend/src/core/agents/api.ts` first to copy the fetch/error-class pattern verbatim (rename classes `Subagent*`). Path alias `@/*` → `src/*`.

The `i18n` namespace added in Task 9 is referenced by components (Tasks 7-8) — this task creates only the data layer. Keep this task focused on types/api/hooks; don't import i18n here.

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/unit/core/subagents/api.test.ts`. Mirror the style of existing `tests/unit/core/agents/api.test.ts` if it exists (run `ls frontend/tests/unit/core/agents/` to check); otherwise use a `fetch` mock:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "rstest";

import { createSubagent, getSubagentCatalogs, listSubagents } from "@/core/subagents/api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("subagents api", () => {
  it("listSubagents GETs /api/subagents and returns subagents array", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ subagents: [{ name: "general-purpose", source: "builtin", readonly: true } as any }] }), { status: 200 }),
    );
    const res = await listSubagents();
    expect(res.length).toBe(1);
    expect(res[0].name).toBe("general-purpose");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = (fetchMock.mock.calls[0][0] as string).replace(/^https?:\/\/[^/]+/, "");
    expect(url).toBe("/api/subagents");
  });

  it("createSubagent POSTs to /api/subagents", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ name: "r", source: "user", readonly: false } as any }), { status: 201 }));
    const res = await createSubagent({ name: "r", description: "d" });
    expect(res.name).toBe("r");
    const call = fetchMock.mock.calls[0];
    const url = (call[0] as string).replace(/^https?:\/\/[^/]+/, "");
    expect(url).toBe("/api/subagents");
    expect(call[1]?.method).toBe("POST");
  });

  it("getSubagentCatalogs returns models/tools/skills", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ models: [], tools: [], skills: [] }), { status: 200 }));
    const res = await getSubagentCatalogs();
    expect(res).toEqual({ models: [], tools: [], skills: [] });
  });
});
```

Confirm the test runner: `pnpm test` uses Rstest (see `frontend/AGENTS.md`). Check an existing unit test for exact import (`rstest` vs `vitest`) by reading `frontend/tests/unit/**` first — adjust the import if Rstest uses a different name.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && pnpm test tests/unit/core/subagents/api.test.ts`
Expected: FAIL — `Cannot find module '@/core/subagents/api'`

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/core/subagents/types.ts`:

```typescript
export type SubagentSource = "builtin" | "global" | "user";

export interface Subagent {
  name: string;
  description: string;
  system_prompt: string | null;
  tools: string[] | null;
  skills: string[] | null;
  skills_on_demand: string[] | null;
  model: string;
  max_turns: number;
  timeout_seconds: number;
  readonly: boolean;
  source: SubagentSource;
  overrides_global: boolean;
}

export interface SubagentCatalogs {
  models: { name: string; label: string }[];
  tools: string[];
  skills: { name: string; description?: string | null }[];
}

export interface CreateSubagentRequest {
  name: string;
  description: string;
  system_prompt?: string | null;
  tools?: string[] | null;
  skills?: string[] | null;
  skills_on_demand?: string[] | null;
  model?: string;
  max_turns?: number;
  timeout_seconds?: number;
}

export interface UpdateSubagentRequest {
  description?: string | null;
  system_prompt?: string | null;
  tools?: string[] | null;
  skills?: string[] | null;
  skills_on_demand?: string[] | null;
  model?: string | null;
  max_turns?: number | null;
  timeout_seconds?: number | null;
}
```

Create `frontend/src/core/subagents/api.ts` (copy the fetcher import + error-class pattern from `@/core/agents/api.ts`, rename to Subagent):

```typescript
import { type Subagent, type SubagentCatalogs, type CreateSubagentRequest, type UpdateSubagentRequest } from "./types";

function getBase(): string {
  // Mirror agents/api.ts — backend base URL resolution. Use the same helper.
  const { getBackendBaseURL } = require("@/core/config") as { getBackendBaseURL: () => string };
  return getBackendBaseURL();
}

// NOTE: replace the require() with a proper ESM import at the file top (this inline form is
// only for clarity here). Import order: external before internal per frontend code style.

export async function listSubagents(): Promise<Subagent[]> {
  const res = await fetch(`${getBase()}/api/subagents`);
  if (!res.ok) throw new Error(`Failed to load subagents: ${res.statusText}`);
  const data = (await res.json()) as { subagents: Subagent[] };
  return data.subagents;
}

export async function getSubagent(name: string): Promise<Subagent> {
  const res = await fetch(`${getBase()}/api/subagents/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Subagent '${name}' not found`);
  return (await res.json()) as Subagent;
}

export async function createSubagent(request: CreateSubagentRequest): Promise<Subagent> {
  const res = await fetch(`${getBase()}/api/subagents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (res.status === 422) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? "Invalid subagent fields");
  }
  if (res.status === 409) throw new Error("A subagent with that name already exists");
  if (!res.ok) throw new Error(`Failed to create subagent: ${res.statusText}`);
  return (await res.json()) as Subagent;
}

export async function updateSubagent(name: string, request: UpdateSubagentRequest): Promise<Subagent> {
  const res = await fetch(`${getBase()}/api/subagents/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw new Error(`Failed to update subagent: ${res.statusText}`);
  return (await res.json()) as Subagent;
}

export async function deleteSubagent(name: string): Promise<void> {
  const res = await fetch(`${getBase()}/api/subagents/${encodeURIComponent(name)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Failed to delete subagent: ${res.statusText}`);
}

export async function checkSubagentName(name: string): Promise<{ available: boolean; name: string }> {
  const res = await fetch(`${getBase()}/api/subagents/check?name=${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Name check failed: ${res.statusText}`);
  return (await res.json()) as { available: boolean; name: string };
}

export async function getSubagentCatalogs(): Promise<SubagentCatalogs> {
  const res = await fetch(`${getBase()}/api/subagents/catalogs`);
  if (!res.ok) throw new Error(`Failed to load subagent catalogs: ${res.statusText}`);
  return (await res.json()) as SubagentCatalogs;
}
```

Use real ESM imports at top (enforced order, inline type imports). Drop the `require()` — it's pseudocode here.

Create `frontend/src/core/subagents/hooks.ts` (mirror `agents/hooks.ts`):

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createSubagent, deleteSubagent, getSubagent, getSubagentCatalogs, listSubagents, updateSubagent } from "./api";
import { type CreateSubagentRequest, type UpdateSubagentRequest } from "./types";

export function useSubagents() {
  const { data, isLoading, error } = useQuery({ queryKey: ["subagents"], queryFn: () => listSubagents() });
  return { subagents: data ?? [], isLoading, error };
}

export function useSubagent(name: string | null | undefined) {
  const { data, isLoading, error } = useQuery({ queryKey: ["subagents", name], queryFn: () => getSubagent(name!), enabled: !!name });
  return { subagent: data ?? null, isLoading, error };
}

export function useSubagentCatalogs() {
  const { data, isLoading, error } = useQuery({ queryKey: ["subagent-catalogs"], queryFn: () => getSubagentCatalogs() });
  return { catalogs: data ?? null, isLoading, error };
}

export function useCreateSubagent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateSubagentRequest) => createSubagent(request),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["subagents"] }); },
  });
}

export function useUpdateSubagent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, request }: { name: string; request: UpdateSubagentRequest }) => updateSubagent(name, request),
    onSuccess: (_d, { name }) => {
      void qc.invalidateQueries({ queryKey: ["subagents"] });
      void qc.invalidateQueries({ queryKey: ["subagents", name] });
    },
  });
}

export function useDeleteSubagent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSubagent(name),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["subagents"] }); },
  });
}

export function useCheckSubagentName() {
  return useMutation({
    mutationFn: (name: string) => checkSubagentName(name),
  });
  // caller debounces with a small useEffect; see form task
}
```

Create `frontend/src/core/subagents/index.ts`:

```typescript
export { type Subagent, type SubagentCatalogs, type CreateSubagentRequest, type UpdateSubagentRequest, type SubagentSource } from "./types";
export { listSubagents, getSubagent, createSubagent, updateSubagent, deleteSubagent, checkSubagentName, getSubagentCatalogs } from "./api";
export { useSubagents, useSubagent, useSubagentCatalogs, useCreateSubagent, useUpdateSubagent, useDeleteSubagent, useCheckSubagentName } from "./hooks";
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && pnpm test tests/unit/core/subagents/api.test.ts`
Expected: PASS

- [ ] **Step 5: Lint + types**

Run: `cd frontend && pnpm lint && pnpm typecheck`
Expected: clean

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/core/subagents tests/unit/core/subagents/api.test.ts
git commit -m "feat(subagents): add frontend core data layer (types/api/hooks)"
```

### Task 7: Frontend gallery + card + route page

**Files:**
- Create: `frontend/src/components/workspace/subagents/subagent-gallery.tsx`
- Create: `frontend/src/components/workspace/subagents/subagent-card.tsx`
- Create: `frontend/src/app/workspace/subagents/page.tsx`

**Interfaces:**
- Consumes: Task 6 (`useSubagents`, `useDeleteSubagent`); `@/components/ui/*` (Button, Card, Badge, Dialog, Tooltip); `@/lib/utils` (`cn`); `lucide-react` icons; `next/navigation` router; i18n `t.subagents.*` (Task 9 — but the keys are simple strings; define placeholders here that Task 9 fills).
- Produces: a gallery listing subagents with new/edit/delete actions, cards showing name/description/source badge, read-only for built-in/global.

Mirror `@/components/workspace/agents/agent-gallery.tsx` + `agent-card.tsx` structure (read both files first). One deliberate divergence: **no chat button** (subagents are invoked via `task`, not standalone chats).

- [ ] **Step 1: Write the gallery component**

Create `subagent-gallery.tsx` (clone agent-gallery.tsx, swap data source + i18n keys + "New subagent" → `/workspace/subagents/new`):

```tsx
"use client";

import { BoxesIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useSubagents } from "@/core/subagents";
import { useI18n } from "@/core/i18n/hooks";

import { SubagentCard } from "./subagent-card";

export function SubagentGallery() {
  const { t } = useI18n();
  const { subagents, isLoading } = useSubagents();
  const router = useRouter();

  const handleNew = () => router.push("/workspace/subagents/new");

  return (
    <div className="flex size-full flex-col">
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.subagents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">{t.subagents.description}</p>
        </div>
        <Button onClick={handleNew}>
          <PlusIcon className="mr-1.5 h-4 w-4" />
          {t.subagents.newSubagent}
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">{t.common.loading}</div>
        ) : subagents.length === 0 ? (
          <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
            <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
              <BoxesIcon className="text-muted-foreground h-7 w-7" />
            </div>
            <div>
              <p className="font-medium">{t.subagents.emptyTitle}</p>
              <p className="text-muted-foreground mt-1 text-sm">{t.subagents.emptyDescription}</p>
            </div>
            <Button variant="outline" className="mt-2" onClick={handleNew}>
              <PlusIcon className="mr-1.5 h-4 w-4" />
              {t.subagents.newSubagent}
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {subagents.map((s) => (
              <SubagentCard key={s.name} subagent={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

Create `subagent-card.tsx` (clone agent-card.tsx, drop chat action, add source badge, keep edit+delete only when `!subagent.readonly`):

```tsx
"use client";

import { BoxesIcon, PencilIcon, Trash2Icon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { type Subagent, useDeleteSubagent } from "@/core/subagents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

interface SubagentCardProps {
  subagent: Subagent;
}

export function SubagentCard({ subagent }: SubagentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const del = useDeleteSubagent();

  function handleEdit() {
    router.push(`/workspace/subagents/${encodeURIComponent(subagent.name)}`);
  }

  async function handleDelete() {
    try {
      await del.mutateAsync(subagent.name);
      toast.success(t.subagents.deleted);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setConfirmOpen(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">
              <span className="font-mono">[{subagent.name}]</span>
            </CardTitle>
            <CardDescription className="mt-1 line-clamp-2">{subagent.description}</CardDescription>
          </div>
          <Badge variant={subagent.readonly ? "secondary" : "default"}>
            {subagent.source}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {/* optional: show skills/tools/model chips */}
        <div className="text-muted-foreground text-xs">
          {t.subagents.model}: {subagent.model} · {t.subagents.maxTurns}: {subagent.max_turns}
        </div>
      </CardContent>
      <CardFooter className="gap-2">
        {!subagent.readonly && (
          <>
            <Button variant="outline" size="sm" onClick={handleEdit}>
              <PencilIcon className="mr-1.5 h-4 w-4" />
              {t.common.edit}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setConfirmOpen(true)}>
              <Trash2Icon className="mr-1.5 h-4 w-4" />
              {t.common.delete}
            </Button>
          </>
        )}
      </CardFooter>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.subagents.deleteConfirmTitle}</DialogTitle>
            <DialogDescription>{t.subagents.deleteConfirmDescription}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>{t.common.cancel}</Button>
            <Button variant="destructive" onClick={handleDelete}>{t.common.delete}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
```

Adjust Badge `variant` values to match the existing Shadcn Badge prop set (`"secondary"|"default"|"outline"|"destructive"`).

Create `frontend/src/app/workspace/subagents/page.tsx`:

```tsx
import { SubagentGallery } from "@/components/workspace/subagents/subagent-gallery";

export default function SubagentsPage() {
  return <SubagentGallery />;
}
```

- [ ] **Step 2: Run typecheck + lint**

Run: `cd frontend && pnpm typecheck && pnpm lint`
Expected: errors only from the not-yet-added i18n `subagents` namespace (Task 9 supplies it). If i18n keys are missing, Task 9 must precede typecheck passing — consider running Task 9 first, or add the namespace keys now as part of this task. Practical order: create the gallery referencing `t.subagents.*`, then in Task 9 add the keys; typecheck passes only after both — that's expected.

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/workspace/subagents src/app/workspace/subagents/page.tsx
git commit -m "feat(subagents): add gallery + card + route page"
```

### Task 8: Frontend designer form + new/edit pages

**Files:**
- Create: `frontend/src/components/workspace/subagents/subagent-designer-form.tsx`
- Create: `frontend/src/app/workspace/subagents/new/page.tsx`
- Create: `frontend/src/app/workspace/subagents/[name]/page.tsx`

**Interfaces:**
- Consumes: Task 6 (`useSubagent`, `useCreateSubagent`, `useUpdateSubagent`, `useSubagentCatalogs`, `useCheckSubagentName`); Shadcn form components (`Input`, `Textarea`, `Select`, a multiselect — check what `@/components/ui` offers; if no MultiSelect primitive, use the existing Combobox pattern from other settings panels or a simple checked list).
- Produces: a form with the field table from spec §5.4, live YAML preview, read-only mode for built-in/global, clone path.

Form fields: name (live check), description, system_prompt, model (select incl "inherit"), tools (multiselect), skills (multiselect), skills_on_demand (multiselect, advanced collapsible), max_turns (number [1,500]), timeout_seconds (number [60,7200]).

Read-only mode: when the loaded subagent has `readonly === true`, disable all inputs and hide the Save button; show a "Clone" button that navigates to `/workspace/subagents/new?clone={name}`.

- [ ] **Step 1: Write the form component**

Create `subagent-designer-form.tsx`. Shape:

```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
// use a multiselect: check components/ui for an existing one; otherwise a Checkbox list.
import { type Subagent, useCreateSubagent, useSubagentCatalogs, useUpdateSubagent } from "@/core/subagents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

const NAME_RE = /^[A-Za-z0-9-]+$/;

interface FormState {
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  tools: string[];
  skills: string[];
  skills_on_demand: string[];
  max_turns: number;
  timeout_seconds: number;
}

// Build the YAML preview text from form state (omit empty/None)
function yamlPreview(f: FormState): string {
  const lines: string[] = [`name: ${f.name || "<name>"}`, `description: "${f.description.replace(/"/g, '\\"')}"`];
  if (f.system_prompt) lines.push("system_prompt: |", ...f.system_prompt.split("\n").map((l) => `  ${l}`));
  if (f.tools.length) lines.push(`tools: [${f.tools.join(", ")}]`);
  if (f.skills.length) lines.push(`skills: [${f.skills.join(", ")}]`);
  if (f.skills_on_demand.length) lines.push(`skills_on_demand: [${f.skills_on_demand.join(", ")}]`);
  lines.push(`model: ${f.model}`);
  lines.push(`max_turns: ${f.max_turns}`);
  lines.push(`timeout_seconds: ${f.timeout_seconds}`);
  return lines.join("\n");
}

export function SubagentDesignerForm({
  initial,
  mode,
}: {
  initial: Subagent | null;          // null = create mode
  mode: "create" | "edit";
}) {
  const { t } = useI18n();
  const router = useRouter();
  const { catalogs } = useSubagentCatalogs();
  const createMut = useCreateSubagent();
  const updateMut = useUpdateSubagent();
  const [advanced, setAdvanced] = useState(false);

  const readonly = mode === "edit" && (initial?.readonly ?? false);

  const [form, setForm] = useState<FormState>(() => ({
    name: initial?.name ?? "",
    description: initial?.description ?? "",
    system_prompt: initial?.system_prompt ?? "",
    model: initial?.model ?? "inherit",
    tools: initial?.tools ?? [],
    skills: initial?.skills ?? [],
    skills_on_demand: initial?.skills_on_demand ?? [],
    max_turns: initial?.max_turns ?? 50,
    timeout_seconds: initial?.timeout_seconds ?? 900,
  }));

  // name validation + availability (debounced)
  const [nameError, setNameError] = useState("");
  useEffect(() => {
    if (mode !== "create") return;
    if (!form.name) { setNameError(""); return; }
    if (!NAME_RE.test(form.name)) { setNameError(t.subagents.nameInvalid); return; }
    let cancelled = false;
    const id = window.setTimeout(async () => {
      try {
        const { checkSubagentName } = await import("@/core/subagents");
        const r = await checkSubagentName(form.name);
        if (!cancelled) setNameError(r.available ? "" : t.subagents.nameTaken);
      } catch { if (!cancelled) setNameError(""); }
    }, 400);
    return () => { cancelled = true; window.clearTimeout(id); };
  }, [form.name, mode, t]);

  function set<K extends keyof FormState>(k: K, v: FormState[K]) { setForm((f) => ({ ...f, [k]: v })); }

  async function handleSubmit() {
    if (mode === "create") {
      if (!NAME_RE.test(form.name) || nameError) { toast.error(t.subagents.nameInvalid); return; }
      try {
        const created = await createMut.mutateAsync({
          name: form.name, description: form.description, system_prompt: form.system_prompt || null,
          tools: form.tools.length ? form.tools : null, skills: form.skills.length ? form.skills : null,
          skills_on_demand: form.skills_on_demand.length ? form.skills_on_demand : null,
          model: form.model, max_turns: form.max_turns, timeout_seconds: form.timeout_seconds,
        });
        router.push(`/workspace/subagents/${encodeURIComponent(created.name)}`);
      } catch (e) { toast.error((e as Error).message); }
    } else {
      const name = initial!.name;
      try {
        await updateMut.mutateAsync({
          name,
          request: {
            description: form.description, system_prompt: form.system_prompt || null,
            tools: form.tools.length ? form.tools : null, skills: form.skills.length ? form.skills : null,
            skills_on_demand: form.skills_on_demand.length ? form.skills_on_demand : null,
            model: form.model, max_turns: form.max_turns, timeout_seconds: form.timeout_seconds,
          },
        });
        toast.success(t.subagents.saved);
      } catch (e) { toast.error((e as Error).message); }
    }
  }

  function handleClone() {
    router.push(`/workspace/subagents/new?clone=${encodeURIComponent(initial!.name)}`);
  }

  return (
    <div className="grid grid-cols-1 gap-6 p-6 lg:grid-cols-[1fr_420px]">
      <div className="space-y-4">
        {/* name (create only) */}
        {mode === "create" && (
          <div>
            <Label>{t.subagents.fieldName}</Label>
            <Input value={form.name} disabled={readonly || mode !== "create"} onChange={(e) => set("name", e.target.value)} />
            {nameError && <p className="text-destructive text-sm">{nameError}</p>}
          </div>
        )}
        <div>
          <Label>{t.subagents.fieldDescription}</Label>
          <Textarea value={form.description} disabled={readonly} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div>
          <Label>{t.subagents.fieldSystemPrompt}</Label>
          <Textarea rows={10} className="font-mono" value={form.system_prompt} disabled={readonly} onChange={(e) => set("system_prompt", e.target.value)} />
        </div>
        <div>
          <Label>{t.subagents.fieldModel}</Label>
          <Select value={form.model} disabled={readonly} onValueChange={(v) => set("model", v)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {(catalogs?.models ?? []).map((m) => (
                <SelectItem key={m.name} value={m.name}>{m.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {/* tools multiselect — use existing primitive */}
        {/* skills multiselect */}
        {/* advanced: skills_on_demand (toggle) */}
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setAdvanced((a) => !a)}>
            {advanced ? "−" : "+"} {t.subagents.advanced}
          </Button>
          {advanced && (<span>{t.subagents.fieldSkillsOnDemand}</span>)}
        </div>
        <div className="flex gap-4">
          <div>
            <Label>{t.subagents.fieldMaxTurns}</Label>
            <Input type="number" min={1} max={500} value={form.max_turns} disabled={readonly} onChange={(e) => set("max_turns", Number(e.target.value))} />
          </div>
          <div>
            <Label>{t.subagents.fieldTimeout}</Label>
            <Input type="number" min={60} max={7200} value={form.timeout_seconds} disabled={readonly} onChange={(e) => set("timeout_seconds", Number(e.target.value))} />
          </div>
        </div>
        <div className="flex gap-2">
          {!readonly && (
            <Button onClick={handleSubmit} disabled={createMut.isPending || updateMut.isPending}>
              {mode === "create" ? t.subagents.create : t.common.save}
            </Button>
          )}
          {readonly && initial && (
            <Button variant="outline" onClick={handleClone}>{t.subagents.clone}</Button>
          )}
          <Button variant="outline" onClick={() => router.push("/workspace/subagents")}>{t.common.back}</Button>
        </div>
      </div>
      <div>
        <Label>{t.subagents.yamlPreview}</Label>
        <pre className="bg-muted mt-2 overflow-x-auto rounded p-3 text-xs">{yamlPreview(form)}</pre>
      </div>
    </div>
  );
}
```

Implement the tools/skills multiselects using whatever primitive `@/components/ui` already has (grep first: `ls frontend/src/components/ui | grep -i select`). If only a single Select exists, build a simple multiselect as a popover with a Checkbox list (there is `@/components/ui/checkbox` already). Don't invent a new primitive.

Create `frontend/src/app/workspace/subagents/new/page.tsx`:

```tsx
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useSubagent } from "@/core/subagents";
import { SubagentDesignerForm } from "@/components/workspace/subagents/subagent-designer-form";

export default function NewSubagentPage() {
  const params = useSearchParams();
  const cloneName = params.get("clone");
  const { subagent } = useSubagent(cloneName);  // null if no clone param
  return <SubagentDesignerForm initial={subagent} mode="create" />;
}
```

Create `frontend/src/app/workspace/subagents/[name]/page.tsx`:

```tsx
"use client";

import { useSubagent } from "@/core/subagents";
import { SubagentDesignerForm } from "@/components/workspace/subagents/subagent-designer-form";

export default function EditSubagentPage({ params }: { params: { name: string } }) {
  const { subagent } = useSubagent(params.name);
  if (!subagent) return null;  // loading handled by hook
  return <SubagentDesignerForm initial={subagent} mode="edit" />;
}
```

- [ ] **Step 2: Run typecheck + lint (after Task 9 i18n keys exist)**

Run: `cd frontend && pnpm typecheck && pnpm lint`
Expected: clean once i18n namespace + Sidebar entry are in place (Tasks 9-10 order matters; run 9 before final typecheck).

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/components/workspace/subagents/subagent-designer-form.tsx src/app/workspace/subagents/new/page.tsx src/app/workspace/subagents/[name]/page.tsx
git commit -m "feat(subagents): add designer form + new/edit pages"
```

### Task 9: Sidebar entry + i18n

**Files:**
- Modify: `frontend/src/components/workspace/workspace-nav-chat-list.tsx` (add Subagents entry near the Agents entry, ~line 31-34)
- Modify: `frontend/src/core/i18n/locales/en-US.ts` (add `sidebar.subagents` + `subagents` namespace)
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts` (matching keys)
- Modify: `frontend/src/core/i18n/locales/types.ts` (add the type fields)

**Interfaces:**
- Consumes: the `sidebar` and `agents` namespaces as templates; lucide `BoxesIcon`.
- Produces: a sidebar "Subagents" link (`/workspace/subagents`) and the `t.subagents.*` keys used by Tasks 7-8.

- [ ] **Step 1: Add the sidebar entry**

In `workspace-nav-chat-list.tsx`, after the Agents `SidebarMenuButton` block (around line 31-34), add:

```tsx
<SidebarMenuButton asChild isActive={pathname.startsWith("/workspace/subagents")}>
  <Link className="text-muted-foreground" href="/workspace/subagents">
    <BoxesIcon />
    <span>{t.sidebar.subagents}</span>
  </Link>
</SidebarMenuButton>
```

Add `BoxesIcon` to the lucide import at the top of the file.

- [ ] **Step 2: Add i18n keys**

In `en-US.ts`, in the `sidebar` object (line ~176), add `subagents: "Subagents"`.

After the `agents` namespace (line ~186), add a new `subagents` namespace. Keys used by Tasks 7-8:

```typescript
  subagents: {
    title: "Subagents",
    description: "Design and manage custom subagent types the lead agent delegates to via the task tool.",
    newSubagent: "New Subagent",
    emptyTitle: "No subagents yet",
    emptyDescription: "Create a subagent type to specialize how the lead agent delegates work.",
    fieldName: "Name",
    fieldDescription: "Description",
    fieldSystemPrompt: "System Prompt",
    fieldModel: "Model",
    fieldMaxTurns: "Max Turns",
    fieldTimeout: "Timeout (seconds)",
    fieldSkillsOnDemand: "Skills on demand",
    advanced: "Advanced",
    model: "Model",
    maxTurns: "Max turns",
    deleted: "Subagent deleted",
    deleteConfirmTitle: "Delete subagent?",
    deleteConfirmDescription: "This subagent type will no longer be available.",
    nameInvalid: "Name must match /^[A-Za-z0-9-]+$/ and not be reserved.",
    nameTaken: "Name already in use.",
    saved: "Saved",
    create: "Create",
    clone: "Clone",
    yamlPreview: "YAML preview",
  },
```

Adjust to the exact key-set the components actually reference (cross-check Tasks 7-8 usages). Mirror in `zh-CN.ts` with Chinese strings.

In `types.ts`, add the matching `subagents` interface with each key's type (`string` for static, `(description: string) => string` for functions).

- [ ] **Step 3: Run typecheck + lint**

Run: `cd frontend && pnpm typecheck && pnpm lint`
Expected: clean (this is the gate that makes Tasks 7-8 typecheck pass).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/workspace/workspace-nav-chat-list.tsx src/core/i18n/locales/en-US.ts src/core/i18n/locales/zh-CN.ts src/core/i18n/locales/types.ts
git commit -m "feat(subagents): add sidebar entry + i18n namespace"
```

### Task 10: Frontend form component test + final smoke check

**Files:**
- Create: `frontend/tests/unit/core/subagents/subagent-designer-form.test.tsx`

**Interfaces:**
- Consumes: the form from Task 8; `@testing-library/react` or whatever the existing rstest setup uses (check `frontend/tests/unit/**` for the pattern). Possibly Rstest's own render API.

- [ ] **Step 1: Write the form test**

Check what render/assertion utilities the existing unit tests use (`tests/units` will show whether they use `@testing-library/react`, a local render helper, etc.) before writing. A representative shape:

```tsx
import { describe, expect, it } from "rstest";
import { render, screen } from "@testing-library/react"; // adjust to actual lib
import { SubagentDesignerForm } from "@/components/workspace/subagents/subagent-designer-form";

// Mock the hooks + i18n. Inspect existing tests for the mock pattern used in this repo.

describe("SubagentDesignerForm", () => {
  it("disables inputs in read-only mode for a built-in", () => {
    render(<SubagentDesignerForm initial={{ name: "bash", readonly: true, source: "builtin", ... } as any} mode="edit" />);
    // save button absent, clone button present
    expect(screen.queryByText("Save")).toBeNull();
    expect(screen.getByText(/clone/i)).toBeTruthy();
  });

  it("shows name taken error after typing a reserved name", async () => {
    // mock checkSubagentName to return { available: false } for "bash"
    // ... render create mode, type "bash", assert error text appears (debounced — may need fake timers)
  });
});
```

Adapt the utilities to what actually exists; don't invent a renderer.

- [ ] **Step 2: Run tests**

Run: `cd frontend && pnpm test`
Expected: PASS (existing + new)

- [ ] **Step 3: Final repo checks**

Run the full gates:
- `cd backend && make lint && make format && make test`
- `cd frontend && pnpm check && pnpm test`
Expected: clean across both. If `make format` reformats any new file, recommit the formatting.

- [ ] **Step 4: Final commit**

```bash
cd frontend && git add tests/unit/core/subagents/subagent-designer-form.test.tsx
git commit -m "test(subagents): add designer form component tests"
# backend formatting if needed
cd ../.. && git add -A && git commit --amend --no-edit 2>/dev/null || true
```

Update docs: add a note to `frontend/AGENTS.md` that a `/workspace/subagents` designer exists (one line under "Source Layout" /routes), and to `backend/AGENTS.md` that a per-user subagent storage + router exists — per the repo's Documentation update policy.

```bash
git add frontend/AGENTS.md backend/AGENTS.md docs/superpowers/specs/2026-07-24-frontend-subagent-designer-design.md
git commit -m "docs: document subagent designer feature in AGENTS.md"
```