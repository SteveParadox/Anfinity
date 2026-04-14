"""Security headers middleware."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # Enable XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        
        # Control how much information is shared with other domains
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Modern browsers only send Referer header when following same protocol
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/auth") else response.headers.get("Cache-Control", "no-store, max-age=0")
        
        # HSTS (only in production with HTTPS)
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # Content Security Policy
        if settings.ENVIRONMENT == "production":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'; "
                "form-action 'self'; "
                "connect-src 'self'"
            )
        
        return response
