"""Chain pipeline subsystem.

A *chain* is a YAML-configured DAG of subagent invocations, triggered by the
``/chain:<name>`` slash command. See ``chains/types.py`` for the data model
and ``chains/parser.py`` for the spec format.
"""

from deerflow.chains.types import Chain, ChainCategory, ChainNode

__all__ = [
    "Chain",
    "ChainCategory",
    "ChainNode",
]
