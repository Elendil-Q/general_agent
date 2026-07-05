"""Chain pipeline data types.

A *chain* is a YAML-configured DAG of subagent invocations. Each chain is a
single flat ``.yaml`` file under ``chains/{public,custom}/<name>.yaml``; the
filename (sans extension) is the chain name.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class ChainCategory(StrEnum):
    """Source category for a chain.

    - ``PUBLIC``: built-in chain bundled with the platform, read-only.
    - ``CUSTOM``: user-authored chain.
    """

    PUBLIC = "public"
    CUSTOM = "custom"


@dataclass
class ChainNode:
    """One node in a chain DAG.

    Attributes:
        subagent: Name of a subagent in the existing subagent registry
            (``subagents.custom_agents`` or a built-in). Resolved at runtime
            via ``get_subagent_config``.
        depends_on: Nodes that must complete before this one. ``[]`` (or
            omitted) means a root node — eligible to run from ``START`` and
            in parallel with other roots. Edges are derived from this field.
        prompt: Optional prompt template. Supports ``{input}`` (the user's
            original message) and ``{node_outputs.<name>}`` placeholders. When
            omitted, the upstream outputs + user input are passed through
            directly.
    """

    subagent: str
    depends_on: list[str] = field(default_factory=list)
    prompt: str | None = None


@dataclass
class Chain:
    """A parsed chain spec.

    Attributes:
        name: Chain name (derived from the filename).
        description: Human-readable summary (shown in autocomplete / API).
        nodes: Ordered mapping of node name -> ``ChainNode``.
        category: ``PUBLIC`` or ``CUSTOM``.
        chain_file: Path to the source ``.yaml`` file.
    """

    name: str
    description: str
    nodes: dict[str, ChainNode]
    category: ChainCategory
    chain_file: Path

    def __repr__(self) -> str:
        return f"Chain(name={self.name!r}, nodes={list(self.nodes)}, category={self.category!r})"
