"""Configuration for the chain pipeline subsystem."""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from deerflow.config.runtime_paths import project_root, resolve_path


def _legacy_chains_candidates() -> tuple[Path, ...]:
    """Return source-tree chains locations for monorepo compatibility."""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (repo_root / "chains",)


class ChainsConfig(BaseModel):
    """Configuration for the chain pipeline subsystem.

    Chains are flat ``.yaml`` files discovered under ``<root>/public/`` and
    ``<root>/custom/``. Unlike skills, chains are read-only specs (no install,
    no enabled-state, no sandbox mounting), so this config only carries the
    discovery root.
    """

    path: str | None = Field(
        default=None,
        description=("Path to the chains directory. If not specified, defaults to `chains` under the caller project root, falling back to the legacy repo-root location for monorepo compatibility."),
    )

    def get_chains_path(self) -> Path:
        """Resolve the chains directory path.

        Resolution order:
            1. Explicit ``path`` field
            2. ``DEER_FLOW_CHAINS_PATH`` environment variable
            3. ``chains`` under the caller project root (``project_root()``)
            4. Legacy repo-root candidates for monorepo compatibility

        When none of (3) or (4) exist on disk, the project-root default is
        returned so callers can surface a stable "no chains" location without
        raising.
        """
        if self.path:
            return resolve_path(self.path)
        if env_path := os.getenv("DEER_FLOW_CHAINS_PATH"):
            return resolve_path(env_path)

        project_default = project_root() / "chains"
        if project_default.is_dir():
            return project_default

        for candidate in _legacy_chains_candidates():
            if candidate.is_dir():
                return candidate

        return project_default
