"""Subagent registry for managing available subagents."""

import logging
from dataclasses import replace
from typing import Any

from deerflow.config.subagents_user_config import (
    list_user_subagent_names,
    load_user_subagent,
)
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.storage import get_or_new_subagent_storage

logger = logging.getLogger(__name__)


def _resolve_subagents_app_config(app_config: Any | None = None):
    if app_config is None:
        from deerflow.config.subagents_config import get_subagents_app_config

        return get_subagents_app_config()
    return getattr(app_config, "subagents", app_config)


def _build_custom_subagent_config(name: str, *, app_config: Any | None = None) -> SubagentConfig | None:
    """Build a SubagentConfig from config.yaml custom_agents or YAML file discovery.

    Resolution order (config.yaml overrides YAML files, mirroring chains/skills
    shadow semantics):
        1. config.yaml ``subagents.custom_agents`` section
        2. YAML files discovered under ``subagents/{public,custom}/``

    Args:
        name: The name of the custom subagent.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve from.

    Returns:
        SubagentConfig if found, None otherwise.
    """
    # 1. Check config.yaml custom_agents first (highest precedence)
    subagents_config = _resolve_subagents_app_config(app_config)
    custom = subagents_config.custom_agents.get(name)
    if custom is not None:
        return SubagentConfig(
            name=name,
            description=custom.description,
            system_prompt=custom.system_prompt,
            tools=custom.tools,
            disallowed_tools=custom.disallowed_tools,
            exclusive_tools=custom.exclusive_tools,
            skills=custom.skills,
            skills_on_demand=custom.skills_on_demand,
            model=custom.model,
            max_turns=custom.max_turns,
            timeout_seconds=custom.timeout_seconds,
            keep_alive=custom.keep_alive,
            workflow=custom.workflow,
        )

    # 2. Fall back to YAML file discovery
    storage_kwargs = {"app_config": app_config} if app_config is not None else {}
    storage = get_or_new_subagent_storage(**storage_kwargs)
    return storage.load_subagent(name)


def get_subagent_config(
    name: str,
    *,
    user_id: str | None = None,
    app_config: Any | None = None,
) -> SubagentConfig | None:
    """Get a subagent configuration by name, with config.yaml overrides applied.

    Resolution order (mirrors Codex's config layering):
    1. Built-in subagent (general-purpose) - name reserved, never shadowed
    2. Per-user subagent files (shadows the global custom layer) - only when
       ``user_id`` is provided
    3. Custom subagents from config.yaml custom_agents section / shared YAML files
    4. Per-agent overrides from config.yaml agents section (timeout, max_turns, model, skills, keep_alive)

    Args:
        name: The name of the subagent.
        user_id: Optional user id; when set, per-user subagent files are
            consulted between the built-in and global custom layers so a
            per-user definition shadows the global one. ``None`` (the default)
            is byte-identical to the legacy behavior.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve overrides from.

    Returns:
        SubagentConfig if found (with any config.yaml overrides applied), None otherwise.
    """
    # Step 1: Look up built-in, then per-user (shadows global), then global custom.
    # Built-in names are reserved: a per-user file named after a built-in is
    # never consulted (validate_subagent_name also rejects such names at write time).
    config = BUILTIN_SUBAGENTS.get(name)
    if config is None and user_id is not None:
        config = load_user_subagent(name, user_id=user_id)
    if config is None:
        config = _build_custom_subagent_config(name, app_config=app_config)
    if config is None:
        return None

    # Step 2: Apply per-agent overrides from config.yaml agents section.
    # Only explicit per-agent overrides are applied here. Global defaults
    # (timeout_seconds, max_turns at the top level) apply to built-in agents
    # but must NOT override custom agents' own values — custom agents define
    # their own defaults in the custom_agents section.
    subagents_config = _resolve_subagents_app_config(app_config)
    is_builtin = name in BUILTIN_SUBAGENTS
    agent_override = subagents_config.agents.get(name)

    overrides = {}

    # Timeout: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.timeout_seconds is not None:
        if agent_override.timeout_seconds != config.timeout_seconds:
            logger.debug("Subagent '%s': timeout overridden (%ss -> %ss)", name, config.timeout_seconds, agent_override.timeout_seconds)
            overrides["timeout_seconds"] = agent_override.timeout_seconds
    elif is_builtin and subagents_config.timeout_seconds != config.timeout_seconds:
        logger.debug("Subagent '%s': timeout from global default (%ss -> %ss)", name, config.timeout_seconds, subagents_config.timeout_seconds)
        overrides["timeout_seconds"] = subagents_config.timeout_seconds

    # Max turns: per-agent override > global default (builtins only) > config's own value
    if agent_override is not None and agent_override.max_turns is not None:
        if agent_override.max_turns != config.max_turns:
            logger.debug("Subagent '%s': max_turns overridden (%s -> %s)", name, config.max_turns, agent_override.max_turns)
            overrides["max_turns"] = agent_override.max_turns
    elif is_builtin and subagents_config.max_turns is not None and subagents_config.max_turns != config.max_turns:
        logger.debug("Subagent '%s': max_turns from global default (%s -> %s)", name, config.max_turns, subagents_config.max_turns)
        overrides["max_turns"] = subagents_config.max_turns

    # Model: per-agent override only (no global default for model)
    effective_model = subagents_config.get_model_for(name)
    if effective_model is not None and effective_model != config.model:
        logger.debug("Subagent '%s': model overridden (%s -> %s)", name, config.model, effective_model)
        overrides["model"] = effective_model

    # Skills: per-agent override only (no global default for skills)
    effective_skills = subagents_config.get_skills_for(name)
    if effective_skills is not None and effective_skills != config.skills:
        logger.debug("Subagent '%s': skills overridden (%s -> %s)", name, config.skills, effective_skills)
        overrides["skills"] = effective_skills

    # On-demand skills: per-agent override only (no global default)
    effective_skills_on_demand = subagents_config.get_skills_on_demand_for(name)
    if effective_skills_on_demand is not None and effective_skills_on_demand != config.skills_on_demand:
        logger.debug("Subagent '%s': skills_on_demand overridden (%s -> %s)", name, config.skills_on_demand, effective_skills_on_demand)
        overrides["skills_on_demand"] = effective_skills_on_demand

    # Keep alive: per-agent override only (no global default)
    effective_keep_alive = subagents_config.get_keep_alive_for(name)
    if effective_keep_alive is not None and effective_keep_alive != config.keep_alive:
        logger.debug("Subagent '%s': keep_alive overridden (%s -> %s)", name, config.keep_alive, effective_keep_alive)
        overrides["keep_alive"] = effective_keep_alive

    if overrides:
        config = replace(config, **overrides)

    return config


def list_subagents(*, app_config: Any | None = None) -> list[SubagentConfig]:
    """List all available subagent configurations (with config.yaml overrides applied).

    Returns:
        List of all registered SubagentConfig instances (built-in + custom).
    """
    configs = []
    for name in get_subagent_names(app_config=app_config):
        config = get_subagent_config(name, app_config=app_config)
        if config is not None:
            configs.append(config)
    return configs


def get_subagent_names(
    *,
    user_id: str | None = None,
    app_config: Any | None = None,
) -> list[str]:
    """Get all available subagent names (built-in + per-user + YAML-discovered + config.yaml custom).

    Names are merged in resolution order, keeping the first occurrence so
    built-in names stay reserved and a per-user name shadows the global
    custom layer:
    1. Built-in subagents
    2. Per-user subagent files (only when ``user_id`` is provided)
    3. YAML-discovered subagents (shared public/custom files)
    4. config.yaml ``custom_agents`` section

    Args:
        user_id: Optional user id; when set, per-user subagent names are
            merged after the built-in layer. ``None`` is byte-identical to
            the legacy behavior.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve from.

    Returns:
        List of subagent names.
    """
    names: list[str] = list(BUILTIN_SUBAGENTS.keys())

    # Merge per-user subagents (shadows the global custom layer); built-in
    # names are already present so a colliding per-user name is skipped.
    if user_id is not None:
        for user_name in list_user_subagent_names(user_id=user_id):
            if user_name not in names:
                names.append(user_name)

    # Merge YAML-discovered subagents
    storage_kwargs = {"app_config": app_config} if app_config is not None else {}
    storage = get_or_new_subagent_storage(**storage_kwargs)
    for yaml_name in storage.list_names():
        if yaml_name not in names:
            names.append(yaml_name)

    # Merge custom_agents from config.yaml (config.yaml overrides YAML files)
    subagents_config = _resolve_subagents_app_config(app_config)
    for custom_name in subagents_config.custom_agents:
        if custom_name not in names:
            names.append(custom_name)

    return names


def get_available_subagent_names(
    *,
    user_id: str | None = None,
    app_config: Any | None = None,
) -> list[str]:
    """Get subagent names that should be exposed to the active runtime.

    Args:
        user_id: Optional user id forwarded to :func:`get_subagent_names` so
            per-user subagent names are included. ``None`` is byte-identical
            to the legacy behavior.
        app_config: Optional AppConfig or SubagentsAppConfig to resolve from.

    Returns:
        List of subagent names visible to the current runtime.
    """
    return get_subagent_names(user_id=user_id, app_config=app_config)
