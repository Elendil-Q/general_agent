from pydantic import BaseModel, Field


class ModePresetConfig(BaseModel):
    """Configuration for a single mode preset.

    Modes are a frontend concept: the user picks a "mode" in the input box, and
    the frontend derives the runtime flags (``thinking_enabled``,
    ``is_plan_mode``, ``subagent_enabled``, ``reasoning_effort``) from the
    matching preset before sending them to the backend. The backend never
    receives the mode name — only the derived flags in ``config.context``.
    """

    name: str = Field(..., description="Unique mode identifier (e.g. flash, thinking, pro, ultra)")
    thinking_enabled: bool = Field(default=True, description="Whether the mode enables extended model thinking")
    is_plan_mode: bool = Field(default=False, description="Whether the mode enables plan / todo-list mode")
    subagent_enabled: bool = Field(
        default=False,
        description="Whether the mode enables the `task` (subagent delegation) tool",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Default reasoning effort for this mode: minimal/low/medium/high (None = leave unset)",
    )
    icon: str = Field(
        default="sparkles",
        description="Icon name for frontend rendering; unknown names fall back to a default icon",
    )
    label: str | None = Field(default=None, description="Optional display label overriding the i18n fallback")
    description: str | None = Field(default=None, description="Optional tooltip description overriding the i18n fallback")


def _default_presets() -> list[ModePresetConfig]:
    """The four built-in presets, equivalent to the pre-config frontend behavior.

    These mirror the hard-coded ``mode -> flags`` mapping that lived in
    ``frontend/src/core/threads/hooks.ts`` before modes became config-driven:
    each mode is a strict superset of the one below it (Flash < Reasoning <
    Pro < Ultra), adding one capability per step.
    """
    return [
        ModePresetConfig(
            name="flash",
            thinking_enabled=False,
            is_plan_mode=False,
            subagent_enabled=False,
            reasoning_effort="minimal",
            icon="zap",
        ),
        ModePresetConfig(
            name="thinking",
            thinking_enabled=True,
            is_plan_mode=False,
            subagent_enabled=False,
            reasoning_effort="low",
            icon="lightbulb",
        ),
        ModePresetConfig(
            name="pro",
            thinking_enabled=True,
            is_plan_mode=True,
            subagent_enabled=False,
            reasoning_effort="medium",
            icon="graduation-cap",
        ),
        ModePresetConfig(
            name="ultra",
            thinking_enabled=True,
            is_plan_mode=True,
            subagent_enabled=True,
            reasoning_effort="high",
            icon="rocket",
        ),
    ]


class ModesConfig(BaseModel):
    """Configuration for mode presets served to the frontend.

    Modes are a frontend-only concept: the backend never receives a "mode"
    name, only the derived runtime flags. This config lets operators
    customize, add, or remove mode presets; the frontend fetches them via
    ``GET /api/modes/config`` and renders the mode selector dynamically.

    With no ``modes:`` section in ``config.yaml``, the four built-in presets
    are used, preserving the pre-config behavior byte-for-byte.
    """

    presets: list[ModePresetConfig] = Field(
        default_factory=_default_presets,
        description="Available mode presets shown in the frontend mode selector",
    )
    default: str = Field(
        default="pro",
        description="Default mode name when the user has not selected one (applied only when the model supports thinking; non-thinking models fall back to the first preset with thinking_enabled=false)",
    )
