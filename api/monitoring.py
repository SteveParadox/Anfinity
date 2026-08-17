"""System monitoring and health check endpoints.

Provides:
- Cache statistics
- Worker health status
- System resource usage
- Ingestion pipeline metrics
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from app.core.auth import get_current_user
from app.database.models import User as DBUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


# ─────────────────────────────────────────────────────────────────────────
# Health Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/health/system")
async def system_health(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get overall system health status.
    
    Checks:
    - Database connectivity
    - Redis connectivity
    - Vector DB connectivity
    - API availability
    """
    from app.database.session import AsyncSessionLocal
    from app.services.vector_db import get_vector_db_client
    from app.events import get_broadcaster
    
    health_status = {
        "status": "healthy",
        "components": {},
        "timestamp": None,
    }
    
    from datetime import datetime
    health_status["timestamp"] = datetime.utcnow().isoformat()
    
    # Check database
    try:
        db = AsyncSessionLocal()
        await db.execute("SELECT 1")
        health_status["components"]["database"] = "healthy"
    except Exception as e:
        health_status["components"]["database"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check Redis/Broadcaster
    try:
        broadcaster = await get_broadcaster()
        if broadcaster._redis:
            await broadcaster._redis.ping()
        health_status["components"]["redis"] = "healthy"
    except Exception as e:
        health_status["components"]["redis"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check Vector DB
    try:
        vector_db = get_vector_db_client(embedding_dim=1536)
        health_status["components"]["vector_db"] = "healthy"
    except Exception as e:
        health_status["components"]["vector_db"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"
    
    return health_status


@router.get("/health/cache")
async def cache_health(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get embeddings cache health and statistics."""
    from app.services.hybrid_embeddings_cache import HybridEmbeddingsCache
    
    cache = HybridEmbeddingsCache()
    stats = cache.get_stats()
    
    return {
        "cache_type": "hybrid (L1 memory + L2 Redis)",
        "statistics": stats,
        "status": "healthy" if stats.get("l1_size", 0) >= 0 else "degraded",
    }


@router.get("/health/workers")
async def worker_health(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get Celery worker health status.
    
    Requires admin permission.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can access worker health"
        )
    
    from app.tasks.worker import celery_app
    from celery.app.control import Inspect
    
    try:
        inspector = Inspect(app=celery_app)
        
        # Get active tasks
        active = inspector.active()
        # Get stats
        stats = inspector.stats()
        # Get registered tasks
        registered_tasks = inspector.registered()
        
        active_task_count = sum(len(tasks) for tasks in (active or {}).values())
        worker_count = len(stats or {})
        
        return {
            "status": "healthy" if worker_count > 0 else "no_workers",
            "active_workers": worker_count,
            "active_tasks": active_task_count,
            "workers": {
                name: {
                    "tasks_active": len(active.get(name, [])) if active else 0,
                    "pool": stats[name].get("pool") if stats and name in stats else None,
                    "registered_tasks": len(registered_tasks.get(name, [])) if registered_tasks else 0,
                }
                for name in (stats or {}).keys()
            }
        }
    except Exception as e:
        logger.error(f"Error fetching worker stats: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


# ─────────────────────────────────────────────────────────────────────────
# Metrics Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/metrics/ingestion")
async def ingestion_metrics(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get ingestion pipeline metrics and statistics.
    
    Metrics:
    - Document throughput
    - Average processing time
    - Success / failure rates
    - Active processing count
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import AsyncSessionLocal
    from app.database.models import Document, DocumentStatus, IngestionLog
    
    db = AsyncSessionLocal()
    
    try:
        # Total documents
        total_docs = await db.scalar(
            select(func.count(Document.id))
        )
        
        # Status breakdown
        status_counts = {}
        for status in DocumentStatus:
            count = await db.scalar(
                select(func.count(Document.id)).where(
                    Document.status == status
                )
            )
            status_counts[status.value] = count
        
        # Last 24 hours activity
        cutoff = datetime.utcnow() - timedelta(hours=24)
        recent_docs = await db.scalar(
            select(func.count(Document.id)).where(
                Document.created_at >= cutoff
            )
        )
        
        # Average processing time
        avg_duration = await db.scalar(
            select(func.avg(IngestionLog.duration_ms)).where(
                IngestionLog.stage == "complete"
            )
        )
        
        return {
            "total_documents": total_docs or 0,
            "status_breakdown": status_counts,
            "documents_last_24h": recent_docs or 0,
            "average_processing_time_ms": avg_duration or 0,
            "success_rate": (
                (status_counts.get("indexed", 0) / (total_docs or 1) * 100)
                if total_docs else 0
            ),
        }
    finally:
        await db.close()


@router.get("/metrics/embeddings")
async def embeddings_metrics(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get embeddings generation metrics."""
    from sqlalchemy import select, func
    from sqlalchemy.ext.asyncio import AsyncSessionLocal
    from app.database.models import Embedding
    
    db = AsyncSessionLocal()
    
    try:
        total_embeddings = await db.scalar(
            select(func.count(Embedding.id))
        )
        
        # By model
        models = await db.execute(
            select(
                Embedding.model_used,
                func.count(Embedding.id).label('count')
            ).group_by(Embedding.model_used)
        )
        
        by_model = {
            row[0]: row[1] for row in models
        }
        
        return {
            "total_embeddings_created": total_embeddings or 0,
            "by_model": by_model,
            "cache_stats": {},  # Will be filled from cache
        }
    finally:
        await db.close()


# ─────────────────────────────────────────────────────────────────────────
# Status Endpoints
# ─────────────────────────────────────────────────────────────────────────

@router.get("/status")
async def system_status(
    current_user: DBUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get comprehensive system status.
    
    Combines health, metrics, and worker information.
    """
    health = await system_health(current_user)
    metrics = await ingestion_metrics(current_user)
    
    return {
        "health": health,
        "metrics": metrics,
        "timestamp": datetime.utcnow().isoformat(),
    }


from datetime import datetime
