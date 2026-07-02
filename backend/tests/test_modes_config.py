"""Tests for the modes configuration and the ``GET /api/modes/config`` endpoint."""

import asyncio
from types import SimpleNamespace

from app.gateway.routers import modes
from deerflow.config.app_config import AppConfig
from deerflow.config.modes_config import ModePresetConfig, ModesConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_app_config() -> AppConfig:
    """Minimal AppConfig with a sandbox (the only required field)."""
    return AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))


def test_default_presets_match_legacy_behavior():
    """The built-in defaults must be byte-for-byte equivalent to the pre-config
    hard-coded ``mode -> flags`` mapping that lived in ``frontend/src/core/threads/hooks.ts``."""
    cfg = ModesConfig()

    by_name = {p.name: p for p in cfg.presets}
    assert list(by_name) == ["flash", "thinking", "pro", "ultra"]
    assert cfg.default == "pro"

    flash = by_name["flash"]
    assert (flash.thinking_enabled, flash.is_plan_mode, flash.subagent_enabled) == (False, False, False)
    assert flash.reasoning_effort == "minimal"
    assert flash.icon == "zap"

    thinking = by_name["thinking"]
    assert (thinking.thinking_enabled, thinking.is_plan_mode, thinking.subagent_enabled) == (True, False, False)
    assert thinking.reasoning_effort == "low"
    assert thinking.icon == "lightbulb"

    pro = by_name["pro"]
    assert (pro.thinking_enabled, pro.is_plan_mode, pro.subagent_enabled) == (True, True, False)
    assert pro.reasoning_effort == "medium"
    assert pro.icon == "graduation-cap"

    ultra = by_name["ultra"]
    assert (ultra.thinking_enabled, ultra.is_plan_mode, ultra.subagent_enabled) == (True, True, True)
    assert ultra.reasoning_effort == "high"
    assert ultra.icon == "rocket"


def test_custom_presets_replace_defaults():
    """A ``modes:`` section fully replaces the built-in presets (does not merge)."""
    cfg = ModesConfig(
        presets=[
            ModePresetConfig(name="task-only", thinking_enabled=True, is_plan_mode=False, subagent_enabled=True, reasoning_effort="medium", icon="rocket"),
        ],
        default="task-only",
    )
    assert [p.name for p in cfg.presets] == ["task-only"]
    assert cfg.default == "task-only"
    assert cfg.presets[0].subagent_enabled is True


def test_app_config_includes_modes_section():
    """AppConfig exposes a ``modes`` field that defaults to the built-in presets."""
    app_cfg = _make_app_config()
    assert isinstance(app_cfg.modes, ModesConfig)
    assert [p.name for p in app_cfg.modes.presets] == ["flash", "thinking", "pro", "ultra"]
    assert app_cfg.modes.default == "pro"


def test_modes_parse_from_dict():
    """ModesConfig parses a raw dict the way AppConfig.from_file loads yaml."""
    raw = {
        "default": "flash",
        "presets": [
            {"name": "flash", "thinking_enabled": False, "is_plan_mode": False, "subagent_enabled": False, "reasoning_effort": "minimal", "icon": "zap"},
            {"name": "custom", "thinking_enabled": True, "is_plan_mode": False, "subagent_enabled": True, "icon": "sparkles", "label": "Custom", "description": "desc"},
        ],
    }
    cfg = ModesConfig(**raw)
    assert cfg.default == "flash"
    custom = next(p for p in cfg.presets if p.name == "custom")
    assert custom.subagent_enabled is True
    assert custom.reasoning_effort is None  # omitted -> default None
    assert custom.label == "Custom"
    assert custom.description == "desc"


def test_get_modes_config_returns_presets_and_default():
    """GET /api/modes/config maps AppConfig.modes -> ModesConfigResponse."""
    app_cfg = _make_app_config()
    result = asyncio.run(modes.get_modes_config(config=app_cfg))

    assert result.default == "pro"
    assert [p.name for p in result.presets] == ["flash", "thinking", "pro", "ultra"]

    ultra = next(p for p in result.presets if p.name == "ultra")
    assert ultra.subagent_enabled is True
    assert ultra.thinking_enabled is True
    assert ultra.is_plan_mode is True
    assert ultra.reasoning_effort == "high"
    assert ultra.icon == "rocket"
    # label/description are optional and None for the built-in presets
    assert ultra.label is None
    assert ultra.description is None


def test_get_modes_config_with_custom_presets():
    """The endpoint honors a custom modes config (e.g. a task-only mode)."""
    custom = ModesConfig(
        presets=[
            ModePresetConfig(name="task-only", thinking_enabled=True, is_plan_mode=False, subagent_enabled=True, reasoning_effort="medium", icon="rocket"),
        ],
        default="task-only",
    )
    mock_app_cfg = SimpleNamespace(modes=custom)

    result = asyncio.run(modes.get_modes_config(config=mock_app_cfg))

    assert result.default == "task-only"
    assert len(result.presets) == 1
    p = result.presets[0]
    assert p.name == "task-only"
    assert p.subagent_enabled is True
    assert p.is_plan_mode is False
    assert p.reasoning_effort == "medium"
