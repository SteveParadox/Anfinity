"""Knowledge graph API routes backed by persisted graph nodes, edges, and clusters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


class GraphClusterResponse(BaseModel):
    id: str
    key: str
    label: str
    description: str
    importance: float = 1.0
    node_ids: List[str] = Field(default_factory=list)
    node_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraphStatsResponse(BaseModel):
    total_nodes: int = 0
    total_edges: int = 0
    total_clusters: int = 0
    node_types: Dict[str, int] = Field(default_factory=dict)
    edge_types: Dict[str, int] = Field(default_factory=dict)


class KnowledgeGraphResponse(BaseModel):
    nodes: List[GraphNodeResponse]
    edges: List[GraphEdgeResponse]
    clusters: List[GraphClusterResponse] = Field(default_factory=list)
    stats: KnowledgeGraphStatsResponse


class ClusterInputNodeResponse(BaseModel):
    id: str
    type: str
    label: str
    value: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: List[float]


class ClusterInputResponse(BaseModel):
    workspace_id: str
    nodes: List[ClusterInputNodeResponse]
    stats: Dict[str, Any] = Field(default_factory=dict)


class GraphClusterMemberRequest(BaseModel):
    node_id: str
    score: float = 0.0
    rank: int = 0


class GraphClusterSyncItemRequest(BaseModel):
    key: str
    members: List[GraphClusterMemberRequest] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GraphClusterSyncRequest(BaseModel):
    k: int = Field(..., ge=0)
    algorithm: str = "kmeans++"
    clusters: List[GraphClusterSyncItemRequest] = Field(default_factory=list)


class GraphClusterSyncResponse(BaseModel):
    workspace_id: str
    clusters_saved: int
    nodes_clustered: int


class WorkspaceListResponse(BaseModel):
    workspace_ids: List[str] = Field(default_factory=list)


async def require_graph_sync_token(request: Request) -> None:
    expected_token = settings.GRAPH_CLUSTER_SYNC_TOKEN
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Graph cluster sync token is not configured",
        )
    provided_token = request.headers.get("x-graph-sync-token")
    if not provided_token or provided_token != expected_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid graph sync token")


@router.get("/internal/workspaces", response_model=WorkspaceListResponse, dependencies=[Depends(require_graph_sync_token)])
async def list_graph_workspaces(db: AsyncSession = Depends(get_db)) -> WorkspaceListResponse:
    workspace_ids = await get_graph_service().list_workspace_ids(db)
    return WorkspaceListResponse(workspace_ids=[str(workspace_id) for workspace_id in workspace_ids])


@router.get(
    "/internal/{workspace_id}/cluster-input",
    response_model=ClusterInputResponse,
    dependencies=[Depends(require_graph_sync_token)],
)
async def get_internal_cluster_input(
    workspace_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ClusterInputResponse:
    payload = await get_graph_service().build_cluster_input(db=db, workspace_id=workspace_id)
    return ClusterInputResponse(**payload)


@router.post(
    "/internal/{workspace_id}/clusters",
    response_model=GraphClusterSyncResponse,
    dependencies=[Depends(require_graph_sync_token)],
)
async def sync_internal_workspace_clusters(
    workspace_id: UUID,
    request: GraphClusterSyncRequest,
    db: AsyncSession = Depends(get_db),
) -> GraphClusterSyncResponse:
    payload = await get_graph_service().sync_workspace_clusters(
        db=db,
        workspace_id=workspace_id,
        clusters=[cluster.model_dump() for cluster in request.clusters],
        algorithm_metadata={"algorithm": request.algorithm, "k": request.k},
    )
    return GraphClusterSyncResponse(**payload)


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


@router.get("/{workspace_id}/cluster-input", response_model=ClusterInputResponse)
async def get_cluster_input(
    workspace_id: UUID,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ClusterInputResponse:
    await get_workspace_context(workspace_id, current_user, db)
    payload = await get_graph_service().build_cluster_input(db=db, workspace_id=workspace_id)
    return ClusterInputResponse(**payload)
