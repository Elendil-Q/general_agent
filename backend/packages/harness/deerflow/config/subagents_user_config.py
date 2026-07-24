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

import yaml

from deerflow.config.paths import get_paths
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.storage import parse_subagent_file

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
    """Return the names of all per-user subagent configs (unreadable skipped)."""
    return [cfg.name for cfg in list_user_subagents(user_id)]


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
