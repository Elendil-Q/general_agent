from pydantic import BaseModel, Field


class EffortFlagsConfig(BaseModel):
    """Functional flags for a single effort preset.

    Effort is a frontend concept: the user picks an "effort" in the input
    box, and the frontend derives the runtime flags
    (``thinking_enabled``, ``is_plan_mode``, ``subagent_enabled``,
    ``reasoning_effort``) from the matching preset before sending them to the
    backend. The backend never receives the effort name — only the derived
    flags in ``config.context``.
    """

    thinking_enabled: bool = Field(
        default=True,
        description="Whether the effort enables extended model thinking",
    )
    is_plan_mode: bool = Field(
        default=False,
        description="Whether the effort enables plan / todo-list mode",
    )
    subagent_enabled: bool = Field(
        default=False,
        description="Whether the effort enables the `task` (subagent delegation) tool",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Default reasoning effort for this effort: minimal/low/medium/high (None = leave unset)",
    )


def _default_flash() -> EffortFlagsConfig:
    return EffortFlagsConfig(
        thinking_enabled=False,
        is_plan_mode=False,
        subagent_enabled=False,
        reasoning_effort="minimal",
    )


def _default_thinking() -> EffortFlagsConfig:
    return EffortFlagsConfig(
        thinking_enabled=True,
        is_plan_mode=False,
        subagent_enabled=False,
        reasoning_effort="low",
    )


def _default_pro() -> EffortFlagsConfig:
    return EffortFlagsConfig(
        thinking_enabled=True,
        is_plan_mode=True,
        subagent_enabled=False,
        reasoning_effort="medium",
    )


def _default_ultra() -> EffortFlagsConfig:
    return EffortFlagsConfig(
        thinking_enabled=True,
        is_plan_mode=True,
        subagent_enabled=True,
        reasoning_effort="high",
    )


class EffortsConfig(BaseModel):
    """Configuration for effort presets served to the frontend.

    Effort is a frontend-only concept: the backend never receives an "effort"
    name, only the derived runtime flags. This config lets operators customize
    the functional flags of the four built-in efforts (flash / thinking / pro /
    ultra). The frontend fetches them via ``GET /api/efforts/config`` and
    renders the effort selector dynamically.

    With no ``efforts:`` section in ``config.yaml``, the four built-in
    defaults are used, preserving the pre-config behavior byte-for-byte.
    """

    flash: EffortFlagsConfig = Field(
        default_factory=_default_flash,
        description="Flash effort flags (fast and efficient, minimal reasoning)",
    )
    thinking: EffortFlagsConfig = Field(
        default_factory=_default_thinking,
        description="Thinking effort flags (reasoning before action)",
    )
    pro: EffortFlagsConfig = Field(
        default_factory=_default_pro,
        description="Pro effort flags (reasoning, planning and executing)",
    )
    ultra: EffortFlagsConfig = Field(
        default_factory=_default_ultra,
        description="Ultra effort flags (pro + subagent delegation)",
    )
