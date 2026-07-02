from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.config.modes_config import ModePresetConfig

router = APIRouter(prefix="/api", tags=["modes"])


class ModePresetResponse(BaseModel):
    """API view of a single mode preset, mirroring ``ModePresetConfig``."""

    name: str = Field(..., description="Unique mode identifier (e.g. flash, thinking, pro, ultra)")
    thinking_enabled: bool = Field(..., description="Whether the mode enables extended model thinking")
    is_plan_mode: bool = Field(..., description="Whether the mode enables plan / todo-list mode")
    subagent_enabled: bool = Field(..., description="Whether the mode enables the `task` (subagent delegation) tool")
    reasoning_effort: str | None = Field(..., description="Default reasoning effort for this mode: minimal/low/medium/high (None = unset)")
    icon: str = Field(..., description="Icon name for frontend rendering; unknown names fall back to a default icon")
    label: str | None = Field(default=None, description="Optional display label overriding the i18n fallback")
    description: str | None = Field(default=None, description="Optional tooltip description overriding the i18n fallback")

    @classmethod
    def from_config(cls, preset: ModePresetConfig) -> "ModePresetResponse":
        return cls(
            name=preset.name,
            thinking_enabled=preset.thinking_enabled,
            is_plan_mode=preset.is_plan_mode,
            subagent_enabled=preset.subagent_enabled,
            reasoning_effort=preset.reasoning_effort,
            icon=preset.icon,
            label=preset.label,
            description=preset.description,
        )


class ModesConfigResponse(BaseModel):
    """API view of the modes configuration, served to the frontend mode selector."""

    presets: list[ModePresetResponse] = Field(..., description="Available mode presets shown in the frontend mode selector")
    default: str = Field(..., description="Default mode name when the user has not selected one")


@router.get(
    "/modes/config",
    response_model=ModesConfigResponse,
    summary="Get Modes Configuration",
    description="Returns the mode presets configured in config.yaml, used by the frontend to render the mode selector dynamically.",
)
async def get_modes_config(
    config: AppConfig = Depends(get_config),
) -> ModesConfigResponse:
    return ModesConfigResponse(
        presets=[ModePresetResponse.from_config(p) for p in config.modes.presets],
        default=config.modes.default,
    )
