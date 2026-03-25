"""FastAPI main application."""
from contextlib import asynccontextmanager
from typing import Optional
import time
import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.config import settings
from app.database.session import init_db
from app.api import auth, workspaces, documents, query, knowledge_graph, audit, connectors, ingestion, notes, embeddings, retrieval, answers, conflicts, dlq, monitoring, search, capture
from app.events import websocket_router
from app.middleware.logging import RequestLoggingMiddleware

# Setup logging
logger = logging.getLogger(__name__)

# Validate critical settings
if settings.ENVIRONMENT == "production":
    if not settings.JWT_SECRET or settings.JWT_SECRET == "":
        raise ValueError("JWT_SECRET must be set in production environment")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} in {settings.ENVIRONMENT} mode")
    
    # Initialize database
    await init_db()
    
    # Initialize rate limiter with Redis
    from app.middleware.rate_limit import rate_limiter
    await rate_limiter.init()
    
    yield
    
    # Shutdown
    logger.info("Shutting down gracefully")
    
    # Close rate limiter connection
    await rate_limiter.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-Powered Knowledge Operating System API",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# CORS middleware - configured from environment
cors_origins = []
if settings.ENVIRONMENT == "development":
    # Development: Allow localhost variants
    cors_origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173"
    ]
    if settings.CORS_ORIGINS:
        cors_origins.extend(list(settings.CORS_ORIGINS))
    cors_origins = list(set(cors_origins))  # Remove duplicates
    logger.info(f"CORS Origins (dev): {cors_origins}")
else:
    # Production: Use only configured origins
    cors_origins = list(settings.CORS_ORIGINS) if settings.CORS_ORIGINS else []
    if not cors_origins:
        logger.warning("⚠️  CORS_ORIGINS not configured for production. Only same-origin requests will be allowed.")
    else:
        logger.info(f"CORS Origins (prod): {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=settings.CORS_CREDENTIALS,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
    max_age=3600,  # Cache preflight requests for 1 hour
)

# Request logging middleware
app.add_middleware(RequestLoggingMiddleware)

# Rate limiting middleware
from app.middleware.rate_limit import RateLimitMiddleware
app.add_middleware(RateLimitMiddleware)

# Security headers middleware
from app.middleware.security import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)

# Standardized error response models
class ErrorResponse(JSONResponse):
    """Standard error response."""
    def __init__(self, status_code: int, detail: str, code: str = "INTERNAL_ERROR", metadata: dict = None):
        content = {
            "error": {
                "code": code,
                "message": detail,
                "timestamp": time.time(),
            }
        }
        if metadata:
            content["error"]["metadata"] = metadata
        super().__init__(status_code=status_code, content=content)


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors."""
    logger.warning(f"Validation error on {request.method} {request.url.path}: {exc}")
    return ErrorResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request validation failed",
        code="VALIDATION_ERROR",
        metadata={"errors": [{"field": str(err["loc"]), "message": err["msg"]} for err in exc.errors()]}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    
    # Don't expose error details in production
    detail = str(exc) if settings.DEBUG else "Internal server error"
    return ErrorResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=detail,
        code="INTERNAL_ERROR"
    )


# Include routers
app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(documents.router)
app.include_router(notes.router)
app.include_router(conflicts.router)
app.include_router(query.router)
app.include_router(retrieval.router)
app.include_router(answers.router)
app.include_router(knowledge_graph.router)
app.include_router(audit.router)
app.include_router(connectors.router)
app.include_router(ingestion.router)
app.include_router(embeddings.router)
app.include_router(search.router)  # Semantic search
app.include_router(capture.router)  # Content capture (URLs, code, data)
app.include_router(dlq.router)  # Dead Letter Queue management
app.include_router(monitoring.router)  # System monitoring
app.include_router(websocket_router)  # Real-time event streaming


# Health check
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT
    }


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs"
    }


# Test endpoint for rate limiting verification
@app.get("/test-ping")
async def test_ping():
    """Simple test endpoint for rate limiting verification.
    
    This endpoint IS subjected to rate limiting (NOT excluded like /health).
    Use this to test and verify that rate limiting returns 429 responses.
    
    Returns:
        Simple status response with timestamp.
    """
    return {
        "status": "ok",
        "timestamp": time.time(),
        "message": "Rate limiting applies to this endpoint"
    }


# LLM Service status endpoint
@app.get("/health/llm")
async def llm_status():
    """LLM service status endpoint.
    
    Shows availability of OpenAI and Ollama providers.
    Useful for monitoring fallback readiness.
    
    Returns:
        LLM service status with provider availability
    """
    from app.services.llm_service import get_llm_service
    
    llm_service = get_llm_service()
    status = llm_service.get_status()
    
    # Determine overall health
    is_healthy = status["openai_available"] or (status["ollama_available"] and status["fallback_enabled"])
    
    return {
        "status": "healthy" if is_healthy else "degraded",
        "llm": status,
        "timestamp": time.time()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
