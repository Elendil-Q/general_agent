"""Local-filesystem chain storage.

Mirrors the skill discovery pattern but simpler: chains are read-only flat
``.yaml`` files (no install, no history, no enabled-state, no editing API).

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
from pathlib import Path

from deerflow.chains.types import Chain, ChainCategory

logger = logging.getLogger(__name__)


class ChainStorage:
    """Discover and load chains from a local filesystem root."""

    def __init__(self, host_root: Path) -> None:
        self._host_root = host_root

    def get_chains_root_path(self) -> Path:
        return self._host_root

    def _iter_chain_files(self) -> Iterable[tuple[ChainCategory, Path]]:
        """Yield ``(category, chain_file)`` for every ``.yaml``/``.yml`` file.

        Iterates ``PUBLIC`` before ``CUSTOM`` so that, when both define a chain
        of the same name, the ``CUSTOM`` entry is seen last and wins the
        dedup-by-name overwrite in :meth:`load_chains`.
        """
        if not self._host_root.exists():
            return
        for category in ChainCategory:
            category_path = self._host_root / category.value
            if not category_path.exists() or not category_path.is_dir():
                continue
            for suffix in ("*.yaml", "*.yml"):
                for chain_file in sorted(category_path.glob(suffix)):
                    yield category, chain_file

    def load_chains(self) -> list[Chain]:
        """Discover all chains, dedup by name (custom over public), sort by name."""
        from deerflow.chains.parser import parse_chain_file

        chains_by_name: dict[str, Chain] = {}
        for category, chain_file in self._iter_chain_files():
            chain = parse_chain_file(chain_file, category=category)
            if chain:
                chains_by_name[chain.name] = chain

        return sorted(chains_by_name.values(), key=lambda c: c.name)

    def load_chain(self, name: str) -> Chain | None:
        """Load a single chain by name, or ``None`` if not found."""
        for chain in self.load_chains():
            if chain.name == name:
                return chain
        return None


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors get_or_new_skill_storage)
# ---------------------------------------------------------------------------

_default_chain_storage: ChainStorage | None = None
_default_chain_storage_config: object | None = None
_chain_storage_lock = threading.Lock()


def get_or_new_chain_storage(app_config=None) -> ChainStorage:
    """Return a ``ChainStorage`` instance — singleton when ``app_config`` is omitted.

    When ``app_config`` is provided, a fresh storage rooted at
    ``app_config.chains.get_chains_path()`` is returned (so per-request config
    from Gateway ``Depends(get_config)`` is respected without polluting the
    singleton). Otherwise the process singleton is built from
    ``get_app_config()`` and reused.
    """
    global _default_chain_storage, _default_chain_storage_config

    if app_config is not None:
        return ChainStorage(host_root=app_config.chains.get_chains_path())

    if _default_chain_storage is not None and _default_chain_storage_config is None:
        return _default_chain_storage

    from deerflow.config import get_app_config

    app_config_now = get_app_config()
    with _chain_storage_lock:
        if _default_chain_storage is None or _default_chain_storage_config is not app_config_now:
            _default_chain_storage = ChainStorage(host_root=app_config_now.chains.get_chains_path())
            _default_chain_storage_config = app_config_now
        return _default_chain_storage


def reset_chain_storage() -> None:
    """Clear the cached singleton (used in tests and hot-reload scenarios)."""
    global _default_chain_storage, _default_chain_storage_config
    with _chain_storage_lock:
        _default_chain_storage = None
        _default_chain_storage_config = None
