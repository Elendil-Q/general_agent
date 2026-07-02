"""Tests for the efforts configuration and the ``GET /api/efforts/config`` endpoint."""

import asyncio
from types import SimpleNamespace

from app.gateway.routers import efforts
from deerflow.config.app_config import AppConfig
from deerflow.config.efforts_config import EffortFlagsConfig, EffortsConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_app_config() -> AppConfig:
    """Minimal AppConfig with a sandbox (the only required field)."""
    return AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))


def test_default_efforts_match_legacy_behavior():
    """The built-in defaults must be byte-for-byte equivalent to the pre-config
    hard-coded ``effort -> flags`` mapping."""
    cfg = EffortsConfig()

    assert (cfg.flash.thinking_enabled, cfg.flash.is_plan_mode, cfg.flash.subagent_enabled) == (False, False, False)
    assert cfg.flash.reasoning_effort == "minimal"

    assert (cfg.thinking.thinking_enabled, cfg.thinking.is_plan_mode, cfg.thinking.subagent_enabled) == (True, False, False)
    assert cfg.thinking.reasoning_effort == "low"

    assert (cfg.pro.thinking_enabled, cfg.pro.is_plan_mode, cfg.pro.subagent_enabled) == (True, True, False)
    assert cfg.pro.reasoning_effort == "medium"

    assert (cfg.ultra.thinking_enabled, cfg.ultra.is_plan_mode, cfg.ultra.subagent_enabled) == (True, True, True)
    assert cfg.ultra.reasoning_effort == "high"


def test_custom_effort_flags_override():
    """Operators can override the flags of any of the four built-in efforts."""
    cfg = EffortsConfig(
        pro=EffortFlagsConfig(
            thinking_enabled=True,
            is_plan_mode=False,
            subagent_enabled=True,
            reasoning_effort="medium",
        ),
    )
    # Pro overridden
    assert cfg.pro.is_plan_mode is False
    assert cfg.pro.subagent_enabled is True
    # Others stay at defaults
    assert cfg.flash.subagent_enabled is False
    assert cfg.ultra.subagent_enabled is True


def test_app_config_includes_efforts():
    """AppConfig exposes an ``efforts`` field that defaults to the built-in presets."""
    app_cfg = _make_app_config()
    assert isinstance(app_cfg.efforts, EffortsConfig)
    assert app_cfg.efforts.ultra.subagent_enabled is True


def test_efforts_parse_from_dict():
    """EffortsConfig parses a raw dict the way AppConfig.from_file loads yaml."""
    raw = {
        "pro": {
            "thinking_enabled": True,
            "is_plan_mode": False,
            "subagent_enabled": True,
            "reasoning_effort": "medium",
        },
    }
    cfg = EffortsConfig(**raw)
    assert cfg.pro.subagent_enabled is True
    assert cfg.pro.is_plan_mode is False
    # Omitted efforts keep defaults
    assert cfg.flash.subagent_enabled is False
    assert cfg.ultra.subagent_enabled is True


def test_get_efforts_config_returns_four_fixed_fields():
    """GET /api/efforts/config returns the four fixed effort flag bundles."""
    app_cfg = _make_app_config()
    result = asyncio.run(efforts.get_efforts_config(config=app_cfg))

    assert result.flash.thinking_enabled is False
    assert result.thinking.subagent_enabled is False
    assert result.pro.is_plan_mode is True
    assert result.ultra.subagent_enabled is True
    assert result.ultra.reasoning_effort == "high"


def test_get_efforts_config_with_custom_flags():
    """The endpoint honors custom effort flags (e.g. pro with task enabled)."""
    custom = EffortsConfig(
        pro=EffortFlagsConfig(
            thinking_enabled=True,
            is_plan_mode=False,
            subagent_enabled=True,
            reasoning_effort="medium",
        ),
    )
    mock_app_cfg = SimpleNamespace(efforts=custom)

    result = asyncio.run(efforts.get_efforts_config(config=mock_app_cfg))

    assert result.pro.is_plan_mode is False
    assert result.pro.subagent_enabled is True
    assert result.pro.reasoning_effort == "medium"
    assert result.flash.subagent_enabled is False
    assert result.ultra.subagent_enabled is True
