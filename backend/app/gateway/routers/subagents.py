"""CRUD API for custom subagent types (the ``task``-tool delegation targets)."""

import asyncio
import dataclasses
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deerflow.config import get_app_config
from deerflow.config.subagents_user_config import (
    delete_user_subagent,
    load_user_subagent,
    save_user_subagent,
    validate_subagent_name,
)
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.registry import get_available_subagent_names, get_subagent_config
from deerflow.tools.tools import get_available_tools

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["subagents"])

BUILTIN_NAMES = set(BUILTIN_SUBAGENTS.keys())
MODEL_VALID_RANGE_MAX_TURNS = (1, 500)
MODEL_VALID_RANGE_TIMEOUT = (60, 7200)


class SubagentResponse(BaseModel):
    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str
    max_turns: int
    timeout_seconds: int
    readonly: bool
    source: str  # "builtin" | "global" | "user"
    overrides_global: bool = False


class SubagentsListResponse(BaseModel):
    subagents: list[SubagentResponse]


class SubagentCreateRequest(BaseModel):
    name: str
    description: str
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str = "inherit"
    max_turns: int = 50
    timeout_seconds: int = 900


class SubagentUpdateRequest(BaseModel):
    description: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    skills_on_demand: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    timeout_seconds: int | None = None


class SubagentCatalogsResponse(BaseModel):
    models: list[dict]
    tools: list[str]
    skills: list[dict]


def _to_response(name: str, *, user_id: str) -> SubagentResponse:
    if name in BUILTIN_NAMES:
        cfg = BUILTIN_SUBAGENTS[name]
        return SubagentResponse(
            name=name,
            description=cfg.description,
            system_prompt=cfg.system_prompt,
            tools=cfg.tools,
            skills=cfg.skills,
            skills_on_demand=cfg.skills_on_demand,
            model=cfg.model,
            max_turns=cfg.max_turns,
            timeout_seconds=cfg.timeout_seconds,
            readonly=True,
            source="builtin",
        )
    user_cfg = load_user_subagent(name, user_id=user_id)
    if user_cfg is not None:
        overrides = False
        global_cfg = get_subagent_config(name, app_config=None)
        if global_cfg is not None and global_cfg.name == name and name not in BUILTIN_NAMES:
            overrides = True
        return SubagentResponse(
            name=user_cfg.name,
            description=user_cfg.description,
            system_prompt=user_cfg.system_prompt,
            tools=user_cfg.tools,
            skills=user_cfg.skills,
            skills_on_demand=user_cfg.skills_on_demand,
            model=user_cfg.model,
            max_turns=user_cfg.max_turns,
            timeout_seconds=user_cfg.timeout_seconds,
            readonly=False,
            source="user",
            overrides_global=overrides,
        )
    global_cfg = get_subagent_config(name, app_config=None)
    if global_cfg is not None:
        return SubagentResponse(
            name=global_cfg.name,
            description=global_cfg.description,
            system_prompt=global_cfg.system_prompt,
            tools=global_cfg.tools,
            skills=global_cfg.skills,
            skills_on_demand=global_cfg.skills_on_demand,
            model=global_cfg.model,
            max_turns=global_cfg.max_turns,
            timeout_seconds=global_cfg.timeout_seconds,
            readonly=True,
            source="global",
        )
    raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")


def _list_all_responses(all_names: list[str], user_id: str) -> list[SubagentResponse]:
    """Build response objects for every name, skipping any that 404."""
    out: list[SubagentResponse] = []
    for n in all_names:
        try:
            out.append(_to_response(n, user_id=user_id))
        except HTTPException as e:
            if e.status_code == 404:
                continue
            raise
    return out


def _validate_fields(name, description, model, max_turns, timeout_seconds, tools, skills, catalogs) -> None:
    if not description:
        raise HTTPException(status_code=422, detail="description is required")
    app_models = {m["name"] for m in catalogs["models"]} | {"inherit"}
    if model not in app_models:
        raise HTTPException(status_code=422, detail=f"Unknown model '{model}'")
    if not (MODEL_VALID_RANGE_MAX_TURNS[0] <= max_turns <= MODEL_VALID_RANGE_MAX_TURNS[1]):
        raise HTTPException(status_code=422, detail=f"max_turns out of range {MODEL_VALID_RANGE_MAX_TURNS}")
    if not (MODEL_VALID_RANGE_TIMEOUT[0] <= timeout_seconds <= MODEL_VALID_RANGE_TIMEOUT[1]):
        raise HTTPException(status_code=422, detail=f"timeout_seconds out of range {MODEL_VALID_RANGE_TIMEOUT}")
    if tools is not None and not set(tools).issubset(catalogs["tools"]):
        raise HTTPException(status_code=422, detail="tools contains unknown name")
    if skills is not None and not set(skills).issubset(s["name"] for s in catalogs["skills"]):
        raise HTTPException(status_code=422, detail="skills contains unknown name")


def _build_catalogs() -> dict:
    try:
        app_cfg = get_app_config()
        model_list = [{"name": m.name, "label": getattr(m, "label", None) or m.name} for m in app_cfg.models]
    except Exception:
        app_cfg = None
        model_list = []
    model_list.insert(0, {"name": "inherit", "label": "Inherit parent"})
    try:
        tool_names = [t.name for t in get_available_tools()]
    except Exception:
        tool_names = []
    try:
        from deerflow.skills.storage import get_or_new_skill_storage

        skills_list = [{"name": s.name, "description": s.description} for s in get_or_new_skill_storage(app_config=app_cfg).load_skills(enabled_only=False)]
    except Exception:
        skills_list = []
    return {"models": model_list, "tools": tool_names, "skills": skills_list}


@router.get("/subagents", response_model=SubagentsListResponse, summary="List Subagents")
async def list_subagents_endpoint() -> SubagentsListResponse:
    user_id = get_effective_user_id()
    all_names = await asyncio.to_thread(get_available_subagent_names, user_id=user_id)
    items = await asyncio.to_thread(_list_all_responses, all_names, user_id)
    return SubagentsListResponse(subagents=items)


@router.get("/subagents/check", summary="Check Subagent Name")
async def check_subagent_name_endpoint(name: str) -> dict:
    available = validate_subagent_name(name) is not None
    if available:
        user_id = get_effective_user_id()
        if load_user_subagent(name, user_id=user_id) is not None:
            available = False
    return {"available": available, "name": (name.lower() if available else name)}


@router.get("/subagents/catalogs", response_model=SubagentCatalogsResponse, summary="Subagent Catalogs")
async def get_subagent_catalogs_endpoint() -> SubagentCatalogsResponse:
    catalogs = await asyncio.to_thread(_build_catalogs)
    return SubagentCatalogsResponse(**catalogs)


@router.get("/subagents/{name}", response_model=SubagentResponse, summary="Get Subagent")
async def get_subagent_endpoint(name: str) -> SubagentResponse:
    return await asyncio.to_thread(_to_response, name, user_id=get_effective_user_id())


@router.post("/subagents", response_model=SubagentResponse, status_code=201, summary="Create Subagent")
async def create_subagent_endpoint(request: SubagentCreateRequest) -> SubagentResponse:
    user_id = get_effective_user_id()
    catalogs = await asyncio.to_thread(_build_catalogs)
    normalized = validate_subagent_name(request.name)
    if normalized is None:
        raise HTTPException(status_code=422, detail="Invalid or reserved subagent name")
    if load_user_subagent(normalized, user_id=user_id) is not None:
        raise HTTPException(status_code=409, detail=f"Subagent '{normalized}' already exists")
    _validate_fields(normalized, request.description, request.model, request.max_turns, request.timeout_seconds, request.tools, request.skills, catalogs)
    cfg = SubagentConfig(
        name=normalized,
        description=request.description,
        system_prompt=request.system_prompt,
        tools=request.tools,
        skills=request.skills,
        skills_on_demand=request.skills_on_demand,
        model=request.model,
        max_turns=request.max_turns,
        timeout_seconds=request.timeout_seconds,
    )
    try:
        await asyncio.to_thread(save_user_subagent, cfg, user_id)
    except Exception as e:
        logger.error(f"Failed to create subagent '{normalized}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create subagent: {e}")
    return await asyncio.to_thread(_to_response, normalized, user_id=user_id)


@router.put("/subagents/{name}", response_model=SubagentResponse, summary="Update Subagent")
async def update_subagent_endpoint(name: str, request: SubagentUpdateRequest) -> SubagentResponse:
    user_id = get_effective_user_id()
    if name in BUILTIN_NAMES:
        raise HTTPException(status_code=403, detail="Built-in subagents are read-only")
    existing = load_user_subagent(name, user_id=user_id)
    if existing is None:
        # could be global - refuse to mutate global
        if get_subagent_config(name, app_config=None) is not None:
            raise HTTPException(status_code=403, detail="Global subagents are read-only; clone to your own name to edit")
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")
    catalogs = await asyncio.to_thread(_build_catalogs)
    # Direct assignment for clearable fields: the frontend sends ``null`` to
    # CLEAR system_prompt/tools/skills/skills_on_demand (e.g. unchecking every
    # tool means "inherit all"). Treating null as "preserve existing" would
    # silently drop the clear. description/model/max_turns/timeout_seconds stay
    # PATCH-style (only applied when provided) since null is never a meaningful
    # value for them. dataclasses.replace also carries name, disallowed_tools,
    # exclusive_tools and workflow from ``existing`` (fields the UI does not
    # expose), so a hand-authored per-user YAML is not stripped on save.
    updates: dict[str, object] = {
        "system_prompt": request.system_prompt,
        "tools": request.tools,
        "skills": request.skills,
        "skills_on_demand": request.skills_on_demand,
    }
    if request.description is not None:
        updates["description"] = request.description
    if request.model is not None:
        updates["model"] = request.model
    if request.max_turns is not None:
        updates["max_turns"] = request.max_turns
    if request.timeout_seconds is not None:
        updates["timeout_seconds"] = request.timeout_seconds
    updated = dataclasses.replace(existing, **updates)
    _validate_fields(existing.name, updated.description, updated.model, updated.max_turns, updated.timeout_seconds, updated.tools, updated.skills, catalogs)
    try:
        await asyncio.to_thread(save_user_subagent, updated, user_id)
    except Exception as e:
        logger.error(f"Failed to update subagent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update subagent: {e}")
    return await asyncio.to_thread(_to_response, existing.name, user_id=user_id)


@router.delete("/subagents/{name}", status_code=204, summary="Delete Subagent")
async def delete_subagent_endpoint(name: str) -> None:
    user_id = get_effective_user_id()
    if name in BUILTIN_NAMES:
        raise HTTPException(status_code=403, detail="Built-in subagents are read-only")
    if load_user_subagent(name, user_id=user_id) is None:
        if get_subagent_config(name, app_config=None) is not None:
            raise HTTPException(status_code=403, detail="Global subagents are read-only; cannot delete")
        raise HTTPException(status_code=404, detail=f"Subagent '{name}' not found")
    try:
        await asyncio.to_thread(delete_user_subagent, name, user_id)
    except Exception as e:
        logger.error(f"Failed to delete subagent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete subagent: {e}")
