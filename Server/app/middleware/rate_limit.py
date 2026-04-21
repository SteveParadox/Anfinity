"""Rate limiting middleware."""

import logging
import time
from typing import Dict, List, Optional

import redis.asyncio as redis
from fastapi import status
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger(__name__)
LOOPBACK_CLIENT_IPS = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def is_rate_limit_exempt_path(path: str) -> bool:
    """Return True when a path should bypass the rate limiter entirely."""
    return path in {"/health", "/", "/docs", "/redoc", "/openapi.json"}


def is_loopback_client(client_ip: Optional[str]) -> bool:
    """Detect local development traffic coming from the same machine."""
    if not client_ip:
        return False

    normalized_ip = client_ip.strip().lower()
    return normalized_ip in LOOPBACK_CLIENT_IPS


def should_skip_rate_limit(*, path: str, client_ip: Optional[str]) -> bool:
    """Centralize bypass rules so middleware and tests stay aligned."""
    if not settings.RATE_LIMIT_ENABLED:
        return True

    if is_rate_limit_exempt_path(path):
        return True

    return bool(
        settings.ENVIRONMENT == "development"
        and settings.RATE_LIMIT_SKIP_LOCALHOST_IN_DEVELOPMENT
        and is_loopback_client(client_ip)
    )


class RateLimiter:
    """Redis-backed rate limiter for production scalability."""

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
        redis_url: Optional[str] = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.redis_url = redis_url or settings.REDIS_URL
        self.redis_client: Optional[redis.Redis] = None
        self.requests: Dict[str, List[float]] = {}
        self.use_redis = False

    async def init(self):
        """Initialize Redis connection (call on app startup)."""
        try:
            self.redis_client = await redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            self.use_redis = True
            logger.info(f"Rate limiter using Redis: {self.redis_url}")
        except Exception as e:
            logger.warning(f"Could not connect to Redis for rate limiting: {e}. Falling back to in-memory.")
            self.use_redis = False

    async def close(self):
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()

    async def is_allowed(self, key: str) -> bool:
        """Check if request is allowed for the given key."""
        try:
            if self.use_redis and self.redis_client:
                return await self._is_allowed_redis(key)
            return self._is_allowed_memory(key)
        except Exception as e:
            logger.error(f"Rate limiter error: {e}. Allowing request.")
            return True

    async def _is_allowed_redis(self, key: str) -> bool:
        """Redis-backed rate limiting."""
        try:
            pipe = self.redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, self.window_seconds)
            results = await pipe.execute()
            current = results[0]
            return current <= self.max_requests
        except Exception as e:
            logger.error(f"Redis rate limit check error: {e}")
            return True

    def _is_allowed_memory(self, key: str) -> bool:
        """In-memory rate limiting (fallback)."""
        now = time.time()

        if key not in self.requests:
            self.requests[key] = []

        self.requests[key] = [
            timestamp for timestamp in self.requests[key]
            if now - timestamp < self.window_seconds
        ]

        if len(self.requests) > 10000:
            logger.warning("Rate limiter cache size exceeded 10000 keys. Clearing old entries.")
            self.requests = {k: v for k, v in self.requests.items() if v}

        if len(self.requests[key]) < self.max_requests:
            self.requests[key].append(now)
            return True

        return False

    async def get_remaining(self, key: str) -> int:
        """Get remaining requests for the key."""
        try:
            if self.use_redis and self.redis_client:
                current = await self.redis_client.get(key)
                current = int(current) if current else 0
            else:
                now = time.time()
                if key not in self.requests:
                    current = 0
                else:
                    self.requests[key] = [
                        timestamp for timestamp in self.requests[key]
                        if now - timestamp < self.window_seconds
                    ]
                    current = len(self.requests[key])

            return max(0, self.max_requests - current)
        except Exception as e:
            logger.error(f"Error getting remaining requests: {e}")
            return self.max_requests


rate_limiter = RateLimiter(
    max_requests=settings.RATE_LIMIT_REQUESTS,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
)


class RateLimitMiddleware:
    """Middleware for rate limiting."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        if should_skip_rate_limit(path=path, client_ip=client_ip):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        api_key = headers.get("x-api-key")
        key = f"rate_limit:{api_key or client_ip}"

        if not await rate_limiter.is_allowed(key):
            remaining = await rate_limiter.get_remaining(key)
            logger.warning(f"Rate limit exceeded for {key}")
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": "Rate limit exceeded",
                        "timestamp": time.time(),
                    }
                },
                headers={
                    "Retry-After": str(settings.RATE_LIMIT_WINDOW_SECONDS),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Limit": str(settings.RATE_LIMIT_REQUESTS),
                },
            )
            await response(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                remaining = await rate_limiter.get_remaining(key)
                response_headers = MutableHeaders(raw=message["headers"])
                response_headers["X-RateLimit-Remaining"] = str(remaining)
                response_headers["X-RateLimit-Limit"] = str(settings.RATE_LIMIT_REQUESTS)

            await send(message)

        await self.app(scope, receive, send_wrapper)
