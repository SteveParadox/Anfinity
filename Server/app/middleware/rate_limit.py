"""Rate limiting middleware."""
import logging
import time
from typing import Dict, List, Optional

import redis.asyncio as redis
from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """Redis-backed rate limiter for production scalability."""
    
    def __init__(self, 
                 max_requests: int = 100, 
                 window_seconds: int = 60,
                 redis_url: Optional[str] = None):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.redis_url = redis_url or settings.REDIS_URL
        self.redis_client: Optional[redis.Redis] = None
        # Fallback to in-memory for development
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
            else:
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
        
        # Remove old requests outside the window
        self.requests[key] = [
            timestamp for timestamp in self.requests[key]
            if now - timestamp < self.window_seconds
        ]
        
        # Cleanup old keys to prevent memory leak
        if len(self.requests) > 10000:
            logger.warning("Rate limiter cache size exceeded 10000 keys. Clearing old entries.")
            self.requests = {k: v for k, v in self.requests.items() if v}
        
        # Check if we're within limits
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


# Global rate limiter instance
rate_limiter = RateLimiter(
    max_requests=settings.RATE_LIMIT_REQUESTS,
    window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting."""
    
    async def dispatch(self, request: Request, call_next):
        """Check rate limit and process request."""
        # Skip rate limiting for health checks
        if request.url.path in ['/health', '/', '/docs', '/redoc', '/openapi.json']:
            return await call_next(request)
        
        # Get client identifier (IP or API key)
        client_ip = request.client.host if request.client else "unknown"
        api_key = request.headers.get("x-api-key")
        key = f"rate_limit:{api_key or client_ip}"
        
        # Check rate limit
        if not await rate_limiter.is_allowed(key):
            remaining = await rate_limiter.get_remaining(key)
            logger.warning(f"Rate limit exceeded for {key}")
            return JSONResponse(
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
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = await rate_limiter.get_remaining(key)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(settings.RATE_LIMIT_REQUESTS)
        
        return response
