"""Worker health monitoring and status broadcasting.

Provides:
- Auto-broadcasting of worker health metrics
- Task execution tracking
- Performance monitoring
"""

import logging
import time
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)


class WorkerHealthMonitor:
    """Tracks and reports worker health metrics."""
    
    def __init__(self, worker_id: str):
        """Initialize monitor for a worker.
        
        Args:
            worker_id: Unique worker identifier
        """
        self.worker_id = worker_id
        self.started_at = time.time()
        self.tasks_processed = 0
        self.tasks_failed = 0
        self.total_task_time = 0
        self.memory_usage_mb = 0
    
    def record_task_completed(self, duration_seconds: float):
        """Record a task completion.
        
        Args:
            duration_seconds: How long the task took
        """
        self.tasks_processed += 1
        self.total_task_time += duration_seconds
    
    def record_task_failed(self, duration_seconds: float):
        """Record a task failure.
        
        Args:
            duration_seconds: How long before it failed
        """
        self.tasks_failed += 1
        self.total_task_time += duration_seconds
    
    def get_status(self) -> Dict[str, Any]:
        """Get current worker status.
        
        Returns:
            Dictionary with health metrics
        """
        uptime_seconds = int(time.time() - self.started_at)
        
        # Get memory usage
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
        except:
            memory_mb = 0
        
        return {
            "worker_id": self.worker_id,
            "status": "healthy",
            "uptime_seconds": uptime_seconds,
            "tasks_processed": self.tasks_processed,
            "tasks_failed": self.tasks_failed,
            "success_rate": (
                (self.tasks_processed / (self.tasks_processed + self.tasks_failed) * 100)
                if (self.tasks_processed + self.tasks_failed) > 0 else 0
            ),
            "avg_task_duration_seconds": (
                self.total_task_time / self.tasks_processed
                if self.tasks_processed > 0 else 0
            ),
            "memory_usage_mb": memory_mb,
            "timestamp": time.time(),
        }


# Global monitor instance per worker
_monitor: WorkerHealthMonitor = None


def get_health_monitor() -> WorkerHealthMonitor:
    """Get or create health monitor for this worker.
    
    Returns:
        WorkerHealthMonitor instance
    """
    global _monitor
    if _monitor is None:
        worker_id = os.environ.get("CELERY_WORKER_NAME", "worker-default")
        _monitor = WorkerHealthMonitor(worker_id)
    return _monitor


# Celery configuration hooks
def setup_health_monitoring():
    """Setup health monitoring for Celery worker.
    
    Call this during worker startup.
    """
    try:
        from app.tasks.worker import celery_app, task_prerun, task_postrun
        
        # The signals are already defined in worker.py
        # Just ensure monitor is initialized
        monitor = get_health_monitor()
        logger.info(f"Health monitoring enabled for {monitor.worker_id}")
    except ImportError:
        logger.warning("Could not setup health monitoring")


# Task hooks for monitoring
def on_task_started(task_id: str, **kwargs):
    """Called when a task starts."""
    monitor = get_health_monitor()
    # Track start time on the task context
    pass


def on_task_success(result: Any, task_id: str, **kwargs):
    """Called when a task completes successfully."""
    monitor = get_health_monitor()
    # Simple increment - actual timing should come from task decorator
    monitor.record_task_completed(duration_seconds=0)


def on_task_failure(exc: Exception, task_id: str, **kwargs):
    """Called when a task fails."""
    monitor = get_health_monitor()
    monitor.record_task_failed(duration_seconds=0)


# Periodic task to broadcast health status
async def broadcast_worker_health():
    """Periodically broadcast worker health status.
    
    Should be called every 30 seconds from a periodic task.
    """
    from app.events import broadcast_worker_health as broadcast
    
    monitor = get_health_monitor()
    status = monitor.get_status()
    
    try:
        await broadcast(
            worker_id=status["worker_id"],
            status=status.get("status", "healthy"),
            tasks_active=0,  # Would need to query from celery
            tasks_processed=status["tasks_processed"],
            uptime_seconds=status["uptime_seconds"],
        )
        logger.debug(f"Broadcast health for {status['worker_id']}")
    except Exception as e:
        logger.warning(f"Could not broadcast health: {e}")


from typing import Any
