"""Knowledge graph API routes backed by persisted graph nodes and edges."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_active_user, get_workspace_context
from app.database.models import User as DBUser
from app.database.session import get_db
from app.services.graph_service import get_graph_service

router = APIRouter(prefix="/knowledge-graph", tags=["Knowledge Graph"])


class GraphNodeResponse(BaseModel):
    id: str
    type: str
    label: str
    value: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphEdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    type: str
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphStatsResponse(BaseModel):
    total_nodes: int = 0
    total_edges: int = 0
    node_types: Dict[str, int] = Field(default_factory=dict)
    edge_types: Dict[str, int] = Field(default_factory=dict)


class KnowledgeGraphResponse(BaseModel):
    nodes: List[GraphNodeResponse]
    edges: List[GraphEdgeResponse]
    stats: KnowledgeGraphStatsResponse


@router.get("/{workspace_id}", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    workspace_id: UUID,
    node_types: Optional[List[str]] = Query(None),
    edge_types: Optional[List[str]] = Query(None),
    search: Optional[str] = Query(None),
    min_weight: float = Query(0.0, ge=0.0),
    include_isolated: bool = Query(True),
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeGraphResponse:
    await get_workspace_context(workspace_id, current_user, db)

    graph_data = await get_graph_service().build_graph_data(
        db=db,
        workspace_id=workspace_id,
        filters={
            "node_types": node_types,
            "edge_types": edge_types,
            "search": search,
            "min_weight": min_weight,
            "include_isolated": include_isolated,
        },
    )

    return KnowledgeGraphResponse(**graph_data)
