"""Subagent configuration definitions."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig


@dataclass
class SubagentConfig:
    """Configuration for a subagent.

    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        skills: Optional list of skill names to load. If None or [], no skills are loaded. Only loads when explicitly set to a non-empty list.
               If an empty list, no skills are loaded.
        skills_on_demand: Optional list of skill names to expose on demand. If None or [],
                no on-demand skills are listed. On-demand skills are NOT injected into the
                context up front - only a catalog entry (name + description + container
                location) is added to the system prompt, and the subagent reads the skill's
                SKILL.md via ``read_file`` when the task matches (mirrors the lead agent's
                progressive-loading pattern). ``skills_on_demand`` is independent of
                ``skills``: a name may appear in either or both (in both = full content
                injected AND listed in the catalog, which is redundant but not an error).
                Does not apply to workflow subagents.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum agent turns before stopping. Built-in agents use the
            value set here (general-purpose=150, bash=60) unless the global
            ``subagents.max_turns`` is set.
        timeout_seconds: Bare fallback execution-time cap. For built-in agents the
            effective limit is the global ``subagents.timeout_seconds`` (default
            1800 = 30 min), layered on by the registry; this 900 only applies
            when no differing global value exists.
        workflow: Optional ``"module.path:object"`` reference to a LangGraph
            workflow factory. When set, the subagent is built from that workflow
            instead of via ``create_agent`` — for scenarios that need a strict,
            deterministic node/edge flow. The factory is resolved with the same
            ``resolve_variable`` loader used for ``config.tools[].use`` and must
            satisfy the workflow-subagent contract (see backend/AGENTS.md): a
            callable ``build_graph(*, model, tools, config) -> StateGraph`` over
            a ``ThreadState``-compatible schema. ``system_prompt``/``skills``/
            ``tools`` filtering do not apply to workflow subagents.
    """

    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    exclusive_tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900
    workflow: str | None = None


def _default_model_name(app_config: "AppConfig") -> str:
    if not app_config.models:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")
    return app_config.models[0].name


def resolve_subagent_model_name(config: SubagentConfig, parent_model: str | None, *, app_config: "AppConfig | None" = None) -> str:
    """Resolve the effective model name a subagent should use."""
    if config.model != "inherit":
        return config.model

    if parent_model is not None:
        return parent_model

    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()
    return _default_model_name(app_config)
