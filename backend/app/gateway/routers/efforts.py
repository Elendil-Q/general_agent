from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.config.efforts_config import EffortFlagsConfig

router = APIRouter(prefix="/api", tags=["efforts"])


class EffortFlagsResponse(BaseModel):
    """API view of the flags for a single effort preset."""

    thinking_enabled: bool = Field(..., description="Whether the effort enables extended model thinking")
    is_plan_mode: bool = Field(..., description="Whether the effort enables plan / todo-list mode")
    subagent_enabled: bool = Field(..., description="Whether the effort enables the `task` (subagent delegation) tool")
    reasoning_effort: str | None = Field(..., description="Default reasoning effort for this effort: minimal/low/medium/high (None = unset)")

    @classmethod
    def from_config(cls, flags: EffortFlagsConfig) -> "EffortFlagsResponse":
        return cls(
            thinking_enabled=flags.thinking_enabled,
            is_plan_mode=flags.is_plan_mode,
            subagent_enabled=flags.subagent_enabled,
            reasoning_effort=flags.reasoning_effort,
        )


class EffortsConfigResponse(BaseModel):
    """API view of the efforts configuration, served to the frontend effort selector."""

    flash: EffortFlagsResponse = Field(..., description="Flash effort flags")
    thinking: EffortFlagsResponse = Field(..., description="Thinking effort flags")
    pro: EffortFlagsResponse = Field(..., description="Pro effort flags")
    ultra: EffortFlagsResponse = Field(..., description="Ultra effort flags")


@router.get(
    "/efforts/config",
    response_model=EffortsConfigResponse,
    summary="Get Efforts Configuration",
    description="Returns the effort flag presets configured in config.yaml, used by the frontend to render the effort selector dynamically.",
)
async def get_efforts_config(
    config: AppConfig = Depends(get_config),
) -> EffortsConfigResponse:
    return EffortsConfigResponse(
        flash=EffortFlagsResponse.from_config(config.efforts.flash),
        thinking=EffortFlagsResponse.from_config(config.efforts.thinking),
        pro=EffortFlagsResponse.from_config(config.efforts.pro),
        ultra=EffortFlagsResponse.from_config(config.efforts.ultra),
    )
