"""Request validation and rate limiting utilities."""
import time
from typing import Dict, Optional
from fastapi import Request, HTTPException, status
from functools import wraps

# Simple in-memory rate limiter
class RateLimiter:
    """Simple rate limiter using in-memory storage."""
    
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, list] = {}
    
    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed for the given key."""
        now = time.time()
        
        if key not in self.requests:
            self.requests[key] = []
        
        # Remove old requests outside the window
        self.requests[key] = [
            timestamp for timestamp in self.requests[key]
            if now - timestamp < self.window_seconds
        ]
        
        # Check if we're within limits
        if len(self.requests[key]) < self.max_requests:
            self.requests[key].append(now)
            return True
        
        return False
    
    def get_remaining(self, key: str) -> int:
        """Get remaining requests for the key."""
        now = time.time()
        
        if key not in self.requests:
            return self.max_requests
        
        # Remove old requests
        self.requests[key] = [
            timestamp for timestamp in self.requests[key]
            if now - timestamp < self.window_seconds
        ]
        
        return max(0, self.max_requests - len(self.requests[key]))


# Global rate limiter instance
rate_limiter = RateLimiter(max_requests=100, window_seconds=60)


async def rate_limit_check(request: Request) -> None:
    """Check rate limit for request IP."""
    client_ip = request.client.host if request.client else "unknown"
    
    if not rate_limiter.is_allowed(client_ip):
        remaining = rate_limiter.get_remaining(client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(60), "X-RateLimit-Remaining": str(remaining)}
        )


async def validate_request_size(request: Request, max_size_mb: int = 50) -> None:
    """Validate request body size."""
    content_length = request.headers.get("content-length")
    
    if content_length:
        max_bytes = max_size_mb * 1024 * 1024
        if int(content_length) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Request body exceeds {max_size_mb}MB limit"
            )
