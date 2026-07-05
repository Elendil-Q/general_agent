"""Chains API — list and inspect chain pipeline specs."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.gateway.deps import get_config
from deerflow.chains.storage.chain_storage import get_or_new_chain_storage
from deerflow.chains.types import Chain, ChainCategory
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chains"])


class ChainNodeResponse(BaseModel):
    """Response model for a single chain node."""

    name: str = Field(..., description="Node name (unique within the chain)")
    subagent: str = Field(..., description="Subagent name in the existing registry")
    depends_on: list[str] = Field(default_factory=list, description="Nodes that must complete before this one")
    prompt: str | None = Field(None, description="Optional prompt template ({input}, {node_outputs.<name>})")


class ChainResponse(BaseModel):
    """Response model for a chain."""

    name: str = Field(..., description="Chain name (filename sans extension)")
    description: str = Field(..., description="What the chain does")
    category: ChainCategory = Field(..., description="public or custom")
    nodes: list[ChainNodeResponse] = Field(..., description="Chain DAG nodes")


class ChainsListResponse(BaseModel):
    """Response model for listing chains."""

    chains: list[ChainResponse]


def _chain_to_response(chain: Chain) -> ChainResponse:
    return ChainResponse(
        name=chain.name,
        description=chain.description,
        category=chain.category,
        nodes=[
            ChainNodeResponse(
                name=name,
                subagent=node.subagent,
                depends_on=list(node.depends_on),
                prompt=node.prompt,
            )
            for name, node in chain.nodes.items()
        ],
    )


@router.get(
    "/chains",
    response_model=ChainsListResponse,
    summary="List All Chains",
    description="Retrieve a list of all available chain pipeline specs from public and custom directories.",
)
async def list_chains(config: AppConfig = Depends(get_config)) -> ChainsListResponse:
    try:
        chains = get_or_new_chain_storage(app_config=config).load_chains()
        return ChainsListResponse(chains=[_chain_to_response(c) for c in chains])
    except Exception as e:
        logger.error("Failed to load chains: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load chains: {e}")


@router.get(
    "/chains/{name}",
    response_model=ChainResponse,
    summary="Get Chain",
    description="Retrieve a single chain pipeline spec by name.",
)
async def get_chain(name: str, config: AppConfig = Depends(get_config)) -> ChainResponse:
    chain = get_or_new_chain_storage(app_config=config).load_chain(name)
    if chain is None:
        raise HTTPException(status_code=404, detail=f"Chain '{name}' not found")
    return _chain_to_response(chain)
