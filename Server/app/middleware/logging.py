"""Request/response logging middleware."""

import logging
import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware:
    """Middleware for logging HTTP requests and responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        status_code = 500

        logger.info(
            f"Request: {method} {path} from {client_ip}",
            extra={
                "method": method,
                "path": path,
                "client_ip": client_ip,
            },
        )

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code

            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(raw=message["headers"])
                headers["X-Process-Time"] = str(time.time() - start_time)

            await send(message)

            if message["type"] == "http.response.body" and not message.get("more_body", False):
                duration = time.time() - start_time
                log_level = "info"
                if status_code >= 500:
                    log_level = "error"
                elif status_code >= 400:
                    log_level = "warning"

                log_func = getattr(logger, log_level)
                log_func(
                    f"Response: {method} {path} {status_code} ({duration * 1000:.0f}ms)",
                    extra={
                        "method": method,
                        "path": path,
                        "status_code": status_code,
                        "duration_ms": duration * 1000,
                    },
                )

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            duration = time.time() - start_time
            logger.error(
                f"Request failed: {method} {path} - {str(exc)}",
                extra={
                    "method": method,
                    "path": path,
                    "duration_ms": duration * 1000,
                    "status_code": 500,
                },
                exc_info=True,
            )
            raise
