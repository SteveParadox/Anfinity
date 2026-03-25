"""Request/Response logging middleware."""
import time
import logging
from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details."""
        start_time = time.time()
        
        # Get client info
        client_ip = request.client.host if request.client else "unknown"
        
        # Log request
        logger.info(
            f"Request: {request.method} {request.url.path} from {client_ip}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client_ip": client_ip,
            }
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration = time.time() - start_time
            logger.error(
                f"Request failed: {request.method} {request.url.path} - {str(exc)}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration * 1000,
                    "status_code": 500,
                },
                exc_info=True,
            )
            raise

        duration = time.time() - start_time
        
        # Log response
        log_level = "info"
        if response.status_code >= 500:
            log_level = "error"
        elif response.status_code >= 400:
            log_level = "warning"

        log_func = getattr(logger, log_level)
        log_func(
            f"Response: {request.method} {request.url.path} {response.status_code} ({duration*1000:.0f}ms)",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration * 1000,
            }
        )

        # Add timing header
        response.headers["X-Process-Time"] = str(duration)
        
        return response
