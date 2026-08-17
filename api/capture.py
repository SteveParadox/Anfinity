"""Content capture API for URLs, code snippets, and data files."""
import hashlib
import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.database.models import User as DBUser, Document, SourceType
from app.core.auth import (
    get_current_active_user,
    get_workspace_context,
    WorkspaceContext,
    WorkspaceRole,
)
from app.ingestion.parsers.url import URLParser
from app.ingestion.parsers.code import CodeParser
from app.ingestion.parsers.data import DataParser
from app.ingestion.chunker import chunk_text
from app.tasks.worker import process_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/capture", tags=["capture"])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _hash_content(content: str) -> str:
    """Return a SHA-256 hex digest for *content* (used for deduplication)."""
    return hashlib.sha256(content.encode()).hexdigest()


def _rough_token_count(text: str) -> int:
    """Whitespace-split word count as a fast token estimate.

    Replace with a tiktoken call if precise billing / context-window budgeting
    is required.
    """
    return len(text.split())


# ─── Request / Response models ────────────────────────────────────────────────

class URLCaptureRequest(BaseModel):
    url: HttpUrl = Field(..., description="URL to capture")
    title: Optional[str] = Field(None, description="Optional custom title")
    tags: Optional[list[str]] = Field(default_factory=list, description="Optional tags")


class CodeCaptureRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Code snippet")
    language: str = Field(..., description="Programming language")
    title: Optional[str] = Field(None, description="Optional custom title")
    tags: Optional[list[str]] = Field(default_factory=list, description="Optional tags")


class DataCaptureRequest(BaseModel):
    data: str = Field(..., min_length=1, description="Data content (JSON or CSV)")
    data_type: str = Field(..., description="'json' or 'csv'")
    title: Optional[str] = Field(None, description="Optional custom title")
    tags: Optional[list[str]] = Field(default_factory=list, description="Optional tags")


class CaptureResponse(BaseModel):
    document_id: str
    title: str
    content_type: str
    chunk_count: int
    estimated_tokens: int
    status: str = "processing"


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/url",
    response_model=CaptureResponse,
    summary="Capture and ingest a URL",
)
async def capture_url(
    request: URLCaptureRequest,
    current_user: DBUser = Depends(get_current_active_user),
    workspace_ctx: WorkspaceContext = Depends(get_workspace_context),
    db: AsyncSession = Depends(get_db),
) -> CaptureResponse:
    """Fetch and parse content from a URL, then queue it for embedding."""

    # FIXED: require_role was never called in the original — any authenticated
    # user could capture into any workspace they could resolve the context for.
    workspace_ctx.require_role(WorkspaceRole.MEMBER)
    workspace_id = workspace_ctx.workspace_id
    url = str(request.url)

    try:
        logger.info("Capturing URL: %s (user=%s)", url, current_user.id)

        parser = URLParser()
        parsed = await parser.parse_url(url)

        title = request.title or parsed.title or url

        document = Document(
            workspace_id=workspace_id,
            title=title,
            source_type=SourceType.WEB_CLIP,
            source_metadata={
                "url": url,
                "author": parsed.author,
                # FIX: publish_date and site_name are in parsed.metadata, not attributes
                "publish_date": parsed.metadata.get("publish_date"),
                "site_name": parsed.metadata.get("site_name"),
                # FIXED: tags were accepted but silently discarded.
                "tags": request.tags or [],
            },
            storage_path=url,
        )

        db.add(document)
        await db.flush()  # obtain document.id before chunking

        chunks = chunk_text(
            parsed.text,
            chunk_size=512,
            chunk_overlap=100,
            metadata={"source_url": url},
        )

        document.content_hash = _hash_content(parsed.text)
        document.token_count = _rough_token_count(parsed.text)
        # chunk_count reflects what the worker will create; the chunks
        # themselves are persisted by process_document, not here.
        document.chunk_count = len(chunks)

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        # FIXED: original had no rollback — a failed chunk_text would leave a
        # half-written document row.
        await db.rollback()
        logger.error("Error capturing URL %s: %s", url, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to capture URL: {exc}",
        )

    # FIXED: align call signature with documents.py (single arg).
    process_document.delay(str(document.id))

    logger.info(
        "URL captured: %s (document=%s, chunks=%d)", title, document.id, len(chunks)
    )
    return CaptureResponse(
        document_id=str(document.id),
        title=title,
        content_type="text/url",
        chunk_count=len(chunks),
        estimated_tokens=document.token_count,
    )


@router.post(
    "/code",
    response_model=CaptureResponse,
    summary="Capture and ingest a code snippet",
)
async def capture_code(
    request: CodeCaptureRequest,
    current_user: DBUser = Depends(get_current_active_user),
    workspace_ctx: WorkspaceContext = Depends(get_workspace_context),
    db: AsyncSession = Depends(get_db),
) -> CaptureResponse:
    """Parse a code snippet and queue it for embedding."""

    workspace_ctx.require_role(WorkspaceRole.MEMBER)
    workspace_id = workspace_ctx.workspace_id

    try:
        logger.info(
            "Capturing code: %s (user=%s)", request.language, current_user.id
        )

        parser = CodeParser()
        parsed = parser.parse_code(request.code, request.language)

        title = request.title or parsed.title or f"{request.language} Code"

        document = Document(
            workspace_id=workspace_id,
            title=title,
            source_type=SourceType.UPLOAD,
            source_metadata={
                "code_language": request.language,
                "code_lines": len(request.code.splitlines()),
                "tags": request.tags or [],
            },
        )

        db.add(document)
        await db.flush()

        chunks = chunk_text(
            parsed.text,
            chunk_size=512,
            chunk_overlap=100,
            metadata={
                "language": request.language,
                "explanation": parsed.metadata.get("explanation"),
            },
        )

        document.content_hash = _hash_content(parsed.text)
        document.token_count = _rough_token_count(parsed.text)
        document.chunk_count = len(chunks)

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.error("Error capturing code: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to capture code: {exc}",
        )

    process_document.delay(str(document.id))

    logger.info(
        "Code captured: %s (document=%s, chunks=%d)", title, document.id, len(chunks)
    )
    return CaptureResponse(
        document_id=str(document.id),
        title=title,
        content_type="text/code",
        chunk_count=len(chunks),
        estimated_tokens=document.token_count,
    )


@router.post(
    "/data",
    response_model=CaptureResponse,
    summary="Capture and ingest data (JSON/CSV)",
)
async def capture_data(
    request: DataCaptureRequest,
    current_user: DBUser = Depends(get_current_active_user),
    workspace_ctx: WorkspaceContext = Depends(get_workspace_context),
    db: AsyncSession = Depends(get_db),
) -> CaptureResponse:
    """Parse JSON or CSV data and queue it for embedding."""

    workspace_ctx.require_role(WorkspaceRole.MEMBER)
    workspace_id = workspace_ctx.workspace_id

    allowed_data_types = {"json", "csv"}
    if request.data_type not in allowed_data_types:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"data_type must be one of {allowed_data_types}",
        )

    try:
        logger.info(
            "Capturing data: %s (user=%s)", request.data_type, current_user.id
        )

        parser = DataParser()
        parsed = parser.parse(request.data.encode("utf-8"), request.data_type)

        title = (
            request.title or parsed.title or f"{request.data_type.upper()} Dataset"
        )

        document = Document(
            workspace_id=workspace_id,
            title=title,
            source_type=SourceType.UPLOAD,
            source_metadata={
                "data_type": request.data_type,
                "insights": parsed.metadata.get("insights", []),
                "columns": parsed.metadata.get("columns", []),
                "tags": request.tags or [],
            },
        )

        db.add(document)
        await db.flush()

        chunks = chunk_text(
            parsed.text,
            chunk_size=512,
            chunk_overlap=100,
            metadata={"data_type": request.data_type},
        )

        document.content_hash = _hash_content(parsed.text)
        document.token_count = _rough_token_count(parsed.text)
        document.chunk_count = len(chunks)

        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.error("Error capturing data: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to capture data: {exc}",
        )

    process_document.delay(str(document.id))

    logger.info(
        "Data captured: %s (document=%s, chunks=%d)", title, document.id, len(chunks)
    )
    return CaptureResponse(
        document_id=str(document.id),
        title=title,
        content_type=f"application/{request.data_type}",
        chunk_count=len(chunks),
        estimated_tokens=document.token_count,
    )