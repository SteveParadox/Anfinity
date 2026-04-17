"""Security headers middleware."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


class SecurityHeadersMiddleware:
    """Add security headers to all HTTP responses.

    Implemented as plain ASGI middleware to avoid BaseHTTPMiddleware edge cases
    such as ``RuntimeError("No response returned.")`` during disconnects.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])

                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-site"
                headers["X-Permitted-Cross-Domain-Policies"] = "none"
                headers["Cache-Control"] = "no-store" if path.startswith("/auth") else headers.get("Cache-Control", "no-store, max-age=0")

                if settings.ENVIRONMENT == "production":
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; "
                        "base-uri 'none'; "
                        "frame-ancestors 'none'; "
                        "form-action 'self'; "
                        "connect-src 'self'"
                    )

            await send(message)

        await self.app(scope, receive, send_wrapper)
