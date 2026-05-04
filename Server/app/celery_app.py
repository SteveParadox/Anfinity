"""Central Celery application instance.

This module provides the single, shared Celery app instance for the entire
application. All tasks should import from here instead of creating their own
Celery() instances.

Usage:
    from app.celery_app import celery_app

    @celery_app.task(bind=True)
    def my_task(self, arg1, arg2):
        pass
"""

from celery import Celery

from app.config import settings

# Create the single, centralized Celery app instance
# Use Redis for broker and a simpler backend to avoid conflicts
celery_app = Celery(
    "app",
    broker=settings.REDIS_URL,
    # Use 'rpc' backend which is designed for this use case
    backend="rpc://",
)

# Configuration
celery_app.conf.update(
    # Serialization settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # Timezone settings
    timezone="UTC",
    enable_utc=True,
    
    # Task settings
    task_track_started=True,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_retry_delay=settings.CELERY_TASK_DEFAULT_RETRY_DELAY,
    result_expires=settings.CELERY_RESULT_EXPIRES,
    
    # Queue and exchange settings (CRITICAL for Redis/Kombu)
    task_default_queue="celery",
    task_default_exchange="celery",
    task_default_exchange_type="direct",
    task_default_routing_key="celery",
    
    # Queue declarations
    task_queues={
        "celery": {
            "exchange": "celery",
            "exchange_type": "direct",
            "routing_key": "celery",
            "durable": True,
        },
        "default": {
            "exchange": "default",
            "exchange_type": "direct",
            "routing_key": "default",
            "durable": True,
        },
        "processing": {
            "exchange": "processing",
            "exchange_type": "direct",
            "routing_key": "processing",
            "durable": True,
        },
        "enrichment": {
            "exchange": "enrichment",
            "exchange_type": "direct",
            "routing_key": "enrichment",
            "durable": True,
        },
        "connectors": {
            "exchange": "connectors",
            "exchange_type": "direct",
            "routing_key": "connectors",
            "durable": True,
        },
        "maintenance": {
            "exchange": "maintenance",
            "exchange_type": "direct",
            "routing_key": "maintenance",
            "durable": True,
        },
    },

    # Route long-running or bursty work away from the default queue so workers
    # can be scaled independently by workload class.
    task_routes={
        "app.tasks.worker.process_document": {"queue": "processing", "routing_key": "processing"},
        "app.tasks.worker.process_paste_content": {"queue": "processing", "routing_key": "processing"},
        "app.tasks.worker.process_voice_input": {"queue": "processing", "routing_key": "processing"},
        "generate_embeddings_document": {"queue": "processing", "routing_key": "processing"},
        "generate_embeddings_workspace": {"queue": "processing", "routing_key": "processing"},
        "embed_chunks": {"queue": "processing", "routing_key": "processing"},
        "generate_note_embedding": {"queue": "enrichment", "routing_key": "enrichment"},
        "generate_workspace_note_embeddings": {"queue": "enrichment", "routing_key": "enrichment"},
        "generate_note_summary": {"queue": "enrichment", "routing_key": "enrichment"},
        "generate_workspace_note_summaries": {"queue": "enrichment", "routing_key": "enrichment"},
        "run_note_auto_tagging_pipeline": {"queue": "enrichment", "routing_key": "enrichment"},
        "classify_note_tags": {"queue": "enrichment", "routing_key": "enrichment"},
        "classify_note_decay": {"queue": "enrichment", "routing_key": "enrichment"},
        "app.tasks.worker.sync_connector": {"queue": "connectors", "routing_key": "connectors"},
        "app.tasks.worker.sync_all_connectors": {"queue": "connectors", "routing_key": "connectors"},
        "app.tasks.dlq.move_to_dlq": {"queue": "maintenance", "routing_key": "maintenance"},
        "app.tasks.worker.delete_vector_ids": {"queue": "maintenance", "routing_key": "maintenance"},
        "app.tasks.worker.delete_document_vectors": {"queue": "maintenance", "routing_key": "maintenance"},
    },
    
    # Worker settings. Default to "solo" for local Windows stability; production
    # deployments should set CELERY_WORKER_POOL to a concurrent pool.
    worker_pool=settings.CELERY_WORKER_POOL,
    worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
    worker_max_tasks_per_child=settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
    
    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    broker_transport_options={
        "visibility_timeout": settings.CELERY_BROKER_VISIBILITY_TIMEOUT,
        "socket_timeout": settings.CELERY_BROKER_SOCKET_TIMEOUT,
        "socket_connect_timeout": settings.CELERY_BROKER_SOCKET_CONNECT_TIMEOUT,
    },
    
    # Rate limiting
    worker_disable_rate_limits=False,
)

# Auto-discover tasks from all modules
# Note: This must be called AFTER app creation and BEFORE task imports
celery_app.autodiscover_tasks(["app.tasks"])

# Explicitly import task modules to ensure they're loaded and decorated tasks are registered
# This is necessary because task decorators don't automatically register tasks until imported
try:
    from app.tasks import worker as worker_tasks
    from app.tasks import embeddings as embeddings_tasks
    from app.tasks import conflict_detection as conflict_detection_tasks
    from app.tasks import dlq as dlq_tasks
    from app.tasks import note_embeddings as note_embeddings_tasks
    from app.tasks import note_summaries as note_summaries_tasks
    from app.tasks import connection_suggestions as connection_suggestions_tasks
    from app.tasks import note_auto_tagging as note_auto_tagging_tasks
    from app.tasks import competitive_intelligence as competitive_intelligence_tasks
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("Failed to import some task modules: %s", e)
