"""Configuration for the subagent system loaded from config.yaml."""

import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field

from deerflow.config.runtime_paths import project_root, resolve_path

logger = logging.getLogger(__name__)


class SubagentOverrideConfig(BaseModel):
    """Per-agent configuration overrides."""

    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Timeout in seconds for this subagent (None = use global default)",
    )
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description="Maximum turns for this subagent (None = use global or builtin default)",
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description="Model name for this subagent (None = inherit from parent agent)",
    )
    skills: list[str] | None = Field(
        default=None,
        description="Skill names whitelist for this subagent (None = inherit all enabled skills, [] = no skills)",
    )
    skills_on_demand: list[str] | None = Field(
        default=None,
        description="Skill names to expose as on-demand for this subagent (catalog only; the subagent reads SKILL.md via read_file when needed). None = no on-demand skills, [] = no on-demand skills.",
    )
    keep_alive: bool | None = Field(
        default=None,
        description="When True, a completed subagent transitions to IDLE instead of COMPLETED, so a later follow_up(task_id, prompt) can revive it. None = keep the agent's own value.",
    )


class CustomSubagentConfig(BaseModel):
    """User-defined subagent type declared in config.yaml."""

    description: str = Field(
        description="When the lead agent should delegate to this subagent",
    )
    system_prompt: str | None = Field(
        default=None,
        description="System prompt that guides the subagent's behavior. Required for create_agent subagents; workflow subagents (see ``workflow``) omit it.",
    )
    tools: list[str] | None = Field(
        default=None,
        description="Tool names whitelist (None = inherit all tools from parent)",
    )
    disallowed_tools: list[str] | None = Field(
        default_factory=lambda: ["task", "ask_clarification", "present_files"],
        description="Tool names to deny",
    )
    exclusive_tools: list[str] | None = Field(
        default=None,
        description="Exclusive tool references resolved via resolve_variable (module.path:variable_name). These tools are loaded directly and are not subject to parent-agent tool inheritance.",
    )
    skills: list[str] | None = Field(
        default=None,
        description="Skill names whitelist (None = inherit all enabled skills, [] = no skills)",
    )
    skills_on_demand: list[str] | None = Field(
        default=None,
        description="Skill names to expose as on-demand (catalog only; the subagent reads SKILL.md via read_file when the task matches, mirroring the lead agent). None/[] = no on-demand skills. Independent of `skills`.",
    )
    model: str = Field(
        default="inherit",
        description="Model to use - 'inherit' uses parent's model",
    )
    max_turns: int = Field(
        default=50,
        ge=1,
        description="Maximum number of agent turns before stopping",
    )
    timeout_seconds: int = Field(
        default=900,
        ge=1,
        description="Maximum execution time in seconds",
    )
    keep_alive: bool = Field(
        default=False,
        description="When True, a COMPLETED subagent transitions to IDLE instead of being cleaned up, so a later follow_up(task_id, prompt) can revive it with full context.",
    )
    workflow: str | None = Field(
        default=None,
        description='"module.path:object" reference to a LangGraph workflow factory '
        "(e.g. 'deerflow.workflows.research_review:build_graph'). When set, the "
        "subagent runs that workflow instead of a create_agent graph. The factory "
        "signature is build_graph(*, model, tools, config) -> StateGraph.",
    )


def _legacy_subagents_candidates() -> tuple[Path, ...]:
    """Return source-tree subagents locations for monorepo compatibility."""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (repo_root / "subagents",)


class SubagentsAppConfig(BaseModel):
    """Configuration for the subagent system."""

    path: str | None = Field(
        default=None,
        description=("Path to the subagents directory for YAML file discovery. If not specified, defaults to `subagents` under the project root, falling back to the legacy repo-root location for monorepo compatibility."),
    )

    timeout_seconds: int = Field(
        default=1800,
        ge=1,
        description="Default timeout in seconds for built-in subagents (default: 1800 = 30 minutes); custom agents use their own timeout_seconds unless given a per-agent override",
    )
    max_turns: int | None = Field(
        default=None,
        ge=1,
        description="Optional default max-turn override for all subagents (None = keep builtin defaults)",
    )
    agents: dict[str, SubagentOverrideConfig] = Field(
        default_factory=dict,
        description="Per-agent configuration overrides keyed by agent name",
    )
    custom_agents: dict[str, CustomSubagentConfig] = Field(
        default_factory=dict,
        description="User-defined subagent types keyed by agent name",
    )

    def get_timeout_for(self, agent_name: str) -> int:
        """Get the effective timeout for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            The timeout in seconds, using per-agent override if set, otherwise global default.
        """
        override = self.agents.get(agent_name)
        if override is not None and override.timeout_seconds is not None:
            return override.timeout_seconds
        return self.timeout_seconds

    def get_model_for(self, agent_name: str) -> str | None:
        """Get the model override for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            Model name if overridden, None otherwise (subagent will inherit parent model).
        """
        override = self.agents.get(agent_name)
        if override is not None and override.model is not None:
            return override.model
        return None

    def get_max_turns_for(self, agent_name: str, builtin_default: int) -> int:
        """Get the effective max_turns for a specific agent."""
        override = self.agents.get(agent_name)
        if override is not None and override.max_turns is not None:
            return override.max_turns
        if self.max_turns is not None:
            return self.max_turns
        return builtin_default

    def get_skills_for(self, agent_name: str) -> list[str] | None:
        """Get the skills override for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            Skill names whitelist if overridden, None otherwise (subagent will inherit all enabled skills).
        """
        override = self.agents.get(agent_name)
        if override is not None and override.skills is not None:
            return override.skills
        return None

    def get_skills_on_demand_for(self, agent_name: str) -> list[str] | None:
        """Get the on-demand skills override for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            On-demand skill names if overridden, None otherwise (subagent will
            use whatever its own config declares, typically no on-demand skills).
        """
        override = self.agents.get(agent_name)
        if override is not None and override.skills_on_demand is not None:
            return override.skills_on_demand
        return None

    def get_keep_alive_for(self, agent_name: str) -> bool | None:
        """Get the keep_alive override for a specific agent.

        Args:
            agent_name: The name of the subagent.

        Returns:
            True/False if overridden, None otherwise (subagent will use its own value).
        """
        override = self.agents.get(agent_name)
        if override is not None and override.keep_alive is not None:
            return override.keep_alive
        return None

    def get_subagents_path(self) -> Path:
        """Resolve the subagents directory path for YAML file discovery.

        Resolution order:
            1. Explicit ``path`` field
            2. ``DEER_FLOW_SUBAGENTS_PATH`` environment variable
            3. ``subagents`` under the project root (``project_root()``)
            4. Legacy repo-root candidates for monorepo compatibility

        When none of (3) or (4) exist on disk, the project-root default is
        returned so callers can surface a stable "no subagents" location.
        """
        if self.path:
            return resolve_path(self.path)
        if env_path := os.getenv("DEER_FLOW_SUBAGENTS_PATH"):
            return resolve_path(env_path)

        project_default = project_root() / "subagents"
        if project_default.is_dir():
            return project_default

        for candidate in _legacy_subagents_candidates():
            if candidate.is_dir():
                return candidate

        return project_default


_subagents_config: SubagentsAppConfig = SubagentsAppConfig()


def get_subagents_app_config() -> SubagentsAppConfig:
    """Get the current subagents configuration."""
    return _subagents_config


def load_subagents_config_from_dict(config_dict: dict) -> None:
    """Load subagents configuration from a dictionary."""
    global _subagents_config
    _subagents_config = SubagentsAppConfig(**config_dict)

    overrides_summary = {}
    for name, override in _subagents_config.agents.items():
        parts = []
        if override.timeout_seconds is not None:
            parts.append(f"timeout={override.timeout_seconds}s")
        if override.max_turns is not None:
            parts.append(f"max_turns={override.max_turns}")
        if override.model is not None:
            parts.append(f"model={override.model}")
        if override.skills is not None:
            parts.append(f"skills={override.skills}")
        if override.skills_on_demand is not None:
            parts.append(f"skills_on_demand={override.skills_on_demand}")
        if override.keep_alive is not None:
            parts.append(f"keep_alive={override.keep_alive}")
        if parts:
            overrides_summary[name] = ", ".join(parts)

    custom_agents_names = list(_subagents_config.custom_agents.keys())

    if overrides_summary or custom_agents_names:
        logger.info(
            "Subagents config loaded: default timeout=%ss, default max_turns=%s, per-agent overrides=%s, custom_agents=%s",
            _subagents_config.timeout_seconds,
            _subagents_config.max_turns,
            overrides_summary or "none",
            custom_agents_names or "none",
        )
