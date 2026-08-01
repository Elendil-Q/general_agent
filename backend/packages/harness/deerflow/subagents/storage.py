"""Local-filesystem subagent storage.

Mirrors the chain discovery pattern: subagents are read-only flat ``.yaml`` files
discovered under ``<root>/public/`` and ``<root>/custom/``.

Layout::

    <root>/public/<name>.yaml
    <root>/custom/<name>.yaml

A ``custom/<name>.yaml`` shadows a ``public/<name>.yaml`` (custom wins on
name collision).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from deerflow.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


class SubagentCategory(StrEnum):
    """Category of subagent definition."""

    PUBLIC = "public"
    CUSTOM = "custom"


class SubagentStorage:
    """Discover and load custom subagents from a local filesystem root."""

    def __init__(self, host_root: Path) -> None:
        self._host_root = host_root

    def get_subagents_root_path(self) -> Path:
        return self._host_root

    def _iter_subagent_files(self) -> Iterable[tuple[SubagentCategory, Path]]:
        """Yield ``(category, subagent_file)`` for every ``.yaml``/``.yml`` file.

        Iterates ``PUBLIC`` before ``CUSTOM`` so that, when both define a
        subagent of the same name, the ``CUSTOM`` entry is seen last and wins
        the dedup-by-name overwrite in :meth:`load_subagents`.
        """
        if not self._host_root.exists():
            return
        for category in SubagentCategory:
            category_path = self._host_root / category.value
            if not category_path.exists() or not category_path.is_dir():
                continue
            for suffix in ("*.yaml", "*.yml"):
                for subagent_file in sorted(category_path.glob(suffix)):
                    yield category, subagent_file

    def load_subagents(self) -> dict[str, SubagentConfig]:
        """Discover all subagents, dedup by name (custom over public)."""
        subagents_by_name: dict[str, SubagentConfig] = {}
        for _category, subagent_file in self._iter_subagent_files():
            config = parse_subagent_file(subagent_file)
            if config is not None:
                subagents_by_name[config.name] = config
        return subagents_by_name

    def load_subagent(self, name: str) -> SubagentConfig | None:
        """Load a single subagent by name, or ``None`` if not found."""
        return self.load_subagents().get(name)

    def list_names(self) -> list[str]:
        """List all discovered subagent names."""
        return sorted(self.load_subagents().keys())


def parse_subagent_file(subagent_file: Path) -> SubagentConfig | None:
    """Parse a subagent YAML file into a :class:`SubagentConfig`.

    Args:
        subagent_file: Path to the ``.yaml``/``.yml`` file.

    Returns:
        :class:`SubagentConfig` if valid, ``None`` if parsing fails or
        the required ``name`` field is missing.
    """
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not available; cannot parse subagent files")
        return None

    try:
        content = subagent_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
    except Exception as e:
        logger.warning("Failed to parse subagent file %s: %s", subagent_file, e)
        return None

    if not isinstance(data, dict):
        logger.warning("Subagent file %s does not contain a YAML mapping", subagent_file)
        return None

    name = data.get("name")
    if not name:
        logger.warning("Subagent file %s missing required 'name' field", subagent_file)
        return None

    description = data.get("description", "")
    if not description:
        logger.warning("Subagent file %s missing 'description' field", subagent_file)

    # Build SubagentConfig from YAML fields
    return SubagentConfig(
        name=name,
        description=description,
        system_prompt=data.get("system_prompt"),
        tools=data.get("tools"),
        disallowed_tools=data.get("disallowed_tools", ["task"]),
        exclusive_tools=data.get("exclusive_tools"),
        skills=data.get("skills"),
        model=data.get("model", "inherit"),
        skills_on_demand=data.get("skills_on_demand"),
        max_turns=data.get("max_turns", 50),
        timeout_seconds=data.get("timeout_seconds", 900),
        workflow=data.get("workflow"),
        keep_alive=data.get("keep_alive", False),
        allow_subagents=data.get("allow_subagents", False),
    )


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors get_or_new_chain_storage)
# ---------------------------------------------------------------------------

_default_subagent_storage: SubagentStorage | None = None
_default_subagent_storage_config: object | None = None
_subagent_storage_lock = threading.Lock()


def get_or_new_subagent_storage(app_config=None) -> SubagentStorage:
    """Return a ``SubagentStorage`` instance — singleton when ``app_config`` is omitted.

    When ``app_config`` is provided, a fresh storage rooted at
    ``app_config.subagents.get_subagents_path()`` is returned (so per-request
    config from Gateway ``Depends(get_config)`` is respected without polluting
    the singleton). Otherwise the process singleton is built from
    ``get_app_config()`` and reused.
    """
    global _default_subagent_storage, _default_subagent_storage_config

    if app_config is not None:
        # app_config may be AppConfig (has .subagents) or SubagentsAppConfig directly
        if hasattr(app_config, "subagents") and hasattr(app_config.subagents, "get_subagents_path"):
            return SubagentStorage(host_root=app_config.subagents.get_subagents_path())
        return SubagentStorage(host_root=app_config.get_subagents_path())

    if _default_subagent_storage is not None and _default_subagent_storage_config is None:
        return _default_subagent_storage

    # Use get_subagents_app_config() (the module-level singleton) rather than
    # get_app_config(): the latter re-reads config.yaml on first call and runs
    # load_subagents_config_from_dict(), which would overwrite test-time
    # mutations to the singleton. The subagents path is already carried on the
    # SubagentsAppConfig, so we don't need the full AppConfig here.
    from deerflow.config.subagents_config import get_subagents_app_config

    subagents_config = get_subagents_app_config()
    with _subagent_storage_lock:
        if _default_subagent_storage is None or _default_subagent_storage_config is not subagents_config:
            _default_subagent_storage = SubagentStorage(host_root=subagents_config.get_subagents_path())
            _default_subagent_storage_config = subagents_config
        return _default_subagent_storage


def reset_subagent_storage() -> None:
    """Clear the cached singleton (used in tests and hot-reload scenarios)."""
    global _default_subagent_storage, _default_subagent_storage_config
    with _subagent_storage_lock:
        _default_subagent_storage = None
        _default_subagent_storage_config = None
