"""Parse a chain ``.yaml`` spec file into a :class:`Chain`."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from deerflow.chains.types import Chain, ChainCategory, ChainNode

logger = logging.getLogger(__name__)

_CHAIN_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VALID_YAML_SUFFIXES = (".yaml", ".yml")


def parse_chain_file(chain_file: Path, category: ChainCategory) -> Chain | None:
    """Parse a chain YAML file.

    The filename (sans extension) is the canonical chain name and must be
    hyphen-case. The YAML body must declare ``description`` (non-empty string)
    and ``nodes`` (non-empty mapping of node name -> node spec).

    Topology is validated: every ``depends_on`` must reference an existing
    node, and the dependency graph must be acyclic.

    Returns the parsed ``Chain`` on success, or ``None`` on any parse or
    validation failure (the error is logged; never raises).
    """
    try:
        if not chain_file.exists() or chain_file.suffix not in _VALID_YAML_SUFFIXES:
            return None
        content = chain_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except (yaml.YAMLError, OSError):
        logger.exception("Failed to read chain file %s", chain_file)
        return None

    if not isinstance(data, dict):
        logger.error("Chain file %s is not a YAML mapping", chain_file)
        return None

    name = chain_file.stem
    if not _CHAIN_NAME_PATTERN.fullmatch(name):
        logger.error(
            "Chain file %s has an invalid name (must be hyphen-case, lowercase): %r",
            chain_file,
            name,
        )
        return None

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        logger.error("Chain file %s is missing a non-empty 'description' string", chain_file)
        return None
    description = description.strip()

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        logger.error("Chain file %s 'nodes' must be a non-empty mapping", chain_file)
        return None

    nodes: dict[str, ChainNode] = {}
    for node_name, raw_node in raw_nodes.items():
        node_name = str(node_name)
        if not _CHAIN_NAME_PATTERN.fullmatch(node_name):
            logger.error(
                "Chain file %s has an invalid node name (must be hyphen-case): %r",
                chain_file,
                node_name,
            )
            return None
        if not isinstance(raw_node, dict):
            logger.error("Chain file %s node %r must be a mapping", chain_file, node_name)
            return None

        subagent = raw_node.get("subagent")
        if not isinstance(subagent, str) or not subagent.strip():
            logger.error("Chain file %s node %r is missing a 'subagent' string", chain_file, node_name)
            return None

        depends_on_raw = raw_node.get("depends_on", [])
        if not isinstance(depends_on_raw, list):
            logger.error("Chain file %s node %r 'depends_on' must be a list", chain_file, node_name)
            return None
        depends_on = [str(dep) for dep in depends_on_raw]

        prompt = raw_node.get("prompt")
        if prompt is not None and not isinstance(prompt, str):
            logger.error("Chain file %s node %r 'prompt' must be a string", chain_file, node_name)
            return None

        nodes[node_name] = ChainNode(subagent=subagent.strip(), depends_on=depends_on, prompt=prompt)

    if not _validate_topology(chain_file, nodes):
        return None

    return Chain(
        name=name,
        description=description,
        nodes=nodes,
        category=category,
        chain_file=chain_file,
    )


def _validate_topology(chain_file: Path, nodes: dict[str, ChainNode]) -> bool:
    """Validate that every ``depends_on`` references an existing node and the graph is acyclic."""
    for node_name, node in nodes.items():
        for dep in node.depends_on:
            if dep not in nodes:
                logger.error(
                    "Chain file %s node %r depends_on unknown node %r",
                    chain_file,
                    node_name,
                    dep,
                )
                return False

    # Cycle detection via DFS coloring. Edges: node -> its dependencies.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}

    def visit(n: str) -> bool:
        color[n] = GRAY
        for dep in nodes[n].depends_on:
            if color[dep] == GRAY:
                logger.error("Chain file %s has a dependency cycle through node %r", chain_file, dep)
                return False
            if color[dep] == WHITE and not visit(dep):
                return False
        color[n] = BLACK
        return True

    return all(visit(n) for n in nodes if color[n] == WHITE)
