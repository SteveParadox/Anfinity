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
    task_time_limit=3600,
    task_acks_late=True,
    
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
    },
    
    # Worker settings
    # NOTE: Using 'solo' pool to avoid billiard unpacking errors on Windows
    # Solo pool is synchronous (single-process) but more stable for this use case
    worker_pool="solo",
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    
    # Broker settings
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    
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
except ImportError as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("Failed to import some task modules: %s", e)
