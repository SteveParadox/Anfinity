"""FEATURE 2: "ASK YOUR PAST SELF" CHAT - RAG Pipeline Chat Endpoint."""

import asyncio
import json
import logging
import re
from typing import AsyncGenerator, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.database.models import User as DBUser, Workspace, WorkspaceMember
from app.database.session import get_db
from app.services.top_k_retriever import get_top_k_retriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])
_OLLAMA_STREAM_CONCURRENCY = max(1, int(getattr(settings, "OLLAMA_MAX_CONCURRENT_REQUESTS", 2) or 2))
_OLLAMA_STREAM_SEMAPHORE = asyncio.Semaphore(_OLLAMA_STREAM_CONCURRENCY)


# ============================================================================
# STEP 2.1: Models & Types
# ============================================================================

class RAGSource(BaseModel):
    """Source document for RAG response with attribution."""
    noteId: str
    title: str
    excerpt: str
    createdAt: str
    similarity: float


class ChatMessage(BaseModel):
    """Single message in conversation."""
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str


class AskPastSelfRequest(BaseModel):
    """Request to chat with knowledge base."""
    workspace_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    history: Optional[List[ChatMessage]] = Field(
        default=None, description="Up to 4 prior exchanges for context"
    )
    top_k: int = Field(default=6, ge=1, le=20)
    similarity_threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class AskPastSelfResponse(BaseModel):
    """Complete RAG response with sources."""
    answer: str
    sources: List[RAGSource]
    confidence: str = Field(..., pattern="^(high|medium|low|not_found)$")
    followUpQuestions: List[str]


# ============================================================================
# STEP 2.2: Shared Helpers
# ============================================================================

async def _verify_workspace_access(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> Workspace:
    """
    Verify the user has access to the requested workspace.
    Raises HTTPException on any failure — call this *before* starting a stream.

    Returns the workspace on success.
    """
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    if workspace.owner_id != user.id:
        membership = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if not membership.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to workspace")

    return workspace


def _determine_confidence(sources: List[RAGSource]) -> str:
    """Map top-source similarity to a confidence tier."""
    if not sources:
        return "not_found"
    top = sources[0].similarity
    if top > 0.75:
        return "high"
    if top > 0.50:
        return "medium"
    return "low"


def _build_messages(
    system_prompt: str,
    query: str,
    history: Optional[List[ChatMessage]],
) -> List[dict]:
    """
    Assemble the full message list for the LLM.
    Keeps at most the last 4 exchanges (8 messages) of history.
    """
    messages: List[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(
            {"role": m.role, "content": m.content} for m in history[-8:]
        )
    messages.append({"role": "user", "content": query})
    return messages


# ============================================================================
# STEP 2.3: Context Retrieval
# ============================================================================

def extract_excerpt(content: str, query: str, max_length: int = 300) -> str:
    """
    Return the sentence from *content* that best matches *query*.
    Falls back to the first sentence when nothing matches.
    """
    sentences = re.split(r"[.!?]+", content)
    query_words = set(query.lower().split())

    best, best_score = sentences[0] if sentences else "", 0
    for sentence in sentences:
        score = sum(1 for w in query_words if w in sentence.lower())
        if score > best_score:
            best_score, best = score, sentence

    return best.strip()[:max_length]


async def retrieve_context(
    query: str,
    workspace_id: UUID,
    db: AsyncSession,
    k: int = 6,
    threshold: float = 0.3,
) -> List[RAGSource]:
    """
    Retrieve the top-k notes relevant to *query* from the given workspace.

    Runs the (potentially blocking) retriever in a thread executor so it
    never stalls the event loop.
    """
    try:
        retriever = get_top_k_retriever(db=db, top_k=k, similarity_threshold=threshold)

        # Offload sync retriever to a thread so the event loop stays clear.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: retriever.retrieve(
                query=query,
                workspace_id=workspace_id,
                top_k=k,
                similarity_threshold=threshold,
            ),
        )

        if not result.chunks:
            return []

        sources: List[RAGSource] = []
        for chunk in result.chunks:
            title = (
                getattr(chunk, "document_title", None)
                or "Untitled Note"
            )
            created_at = ""
            raw_date = getattr(chunk, "created_at", None)
            if raw_date:
                created_at = raw_date.isoformat() if hasattr(raw_date, "isoformat") else str(raw_date)

            sources.append(
                RAGSource(
                    noteId=str(chunk.document_id),
                    title=title,
                    excerpt=extract_excerpt(chunk.text, query),
                    createdAt=created_at,
                    similarity=float(chunk.similarity),
                )
            )

        return sources

    except Exception:
        logger.exception("Error retrieving context for query=%r workspace=%s", query, workspace_id)
        return []


# ============================================================================
# STEP 2.4: Prompt Construction
# ============================================================================

def build_rag_system_prompt(query: str, sources: List[RAGSource]) -> str:
    """Build a strictly-grounded system prompt from retrieved sources."""
    if not sources:
        return f'The user\'s knowledge base contains no notes relevant to: "{query}"'

    source_context = "\n".join(
        f"""
[SOURCE {i + 1}]
Note: "{s.title}"
Date: {s.createdAt}
Relevance: {round(s.similarity * 100)}%
Content: {s.excerpt}
---"""
        for i, s in enumerate(sources)
    )

    return f"""You are the user's personal AI assistant with access ONLY to their private notes.
Your job is to answer their question using ONLY information from the provided sources.

STRICT RULES:
1. ONLY use information from the provided sources. Never use general knowledge.
2. Cite every claim with [SOURCE N] inline.
3. If the sources don't contain enough information, say exactly that.
4. Refer to the user in second person ("you wrote", "your notes say").
5. Include the note title and date when referencing a source.
6. End with 2-3 follow-up questions the user might want to explore.

SOURCES FROM YOUR KNOWLEDGE BASE:
{source_context}

USER QUESTION: {query}

Answer (cite sources inline):"""


# ============================================================================
# STEP 2.5: LLM Generation (Ollama → OpenAI fallback, fully async)
# ============================================================================

async def _generate_with_ollama(messages: List[dict]) -> str:
    """
    Attempt generation via Ollama using the proper chat payload.
    Runs the blocking HTTP call in a thread executor.
    Raises RuntimeError if Ollama is unavailable or the call fails.
    """
    from app.services.llm_service import OllamaClient

    ollama = OllamaClient(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_MODEL,
        timeout=settings.OLLAMA_TIMEOUT,
    )
    if not ollama.is_available():
        raise RuntimeError("Ollama not available")

    loop = asyncio.get_event_loop()
    # Pass the structured messages list rather than a flat concatenated string.
    response_text, _ = await loop.run_in_executor(
        None,
        lambda: ollama.chat(  # use chat() not generate() for role-aware inference
            messages=messages,
            temperature=0.3,
            num_predict=1000,
        ),
    )
    return response_text


async def _stream_with_ollama(messages: List[dict]) -> AsyncGenerator[str, None]:
    """Stream chat chunks directly from Ollama for faster first-token latency."""
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "keep_alive": "10m",
        "options": {
            "temperature": 0.3,
            "num_predict": min(getattr(settings, "LLM_MAX_TOKENS", 1000), 1000),
        },
    }

    async with _OLLAMA_STREAM_SEMAPHORE:
        async with httpx.AsyncClient(timeout=float(settings.OLLAMA_TIMEOUT)) as client:
            async with client.stream("POST", f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    content = ((data.get("message") or {}).get("content") or "")
                    if content:
                        yield content
                    if data.get("done"):
                        break


async def _generate_with_openai(messages: List[dict]) -> str:
    """
    Fallback generation via OpenAI (async, non-blocking).
    Raises on any API error.
    """
    from openai import AsyncOpenAI  # async client — no run_in_executor needed

    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0)
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=1000,
    )
    return response.choices[0].message.content or ""


async def generate_answer(messages: List[dict]) -> str:
    """
    Generate an answer using Ollama, falling back to OpenAI on any failure.
    Both backends run asynchronously without blocking the event loop.
    """
    try:
        logger.info("Attempting LLM generation with Ollama")
        return await _generate_with_ollama(messages)
    except Exception as exc:
        logger.warning("Ollama failed (%s), falling back to OpenAI", exc)

    try:
        return await _generate_with_openai(messages)
    except Exception as exc:
        logger.error("OpenAI fallback also failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"All LLM backends failed: {exc}",
        ) from exc


def _chunk_text_for_stream(text: str, chunk_size: int = 48) -> List[str]:
    """Split non-streaming fallback text into UI-friendly chunks."""
    return [text[i:i + chunk_size] for i in range(0, len(text or ""), chunk_size)]


# ============================================================================
# STEP 2.6: Follow-up Question Extraction
# ============================================================================

_FOLLOW_UP_PATTERNS = [
    re.compile(r"follow.up questions?:?\s*([\s\S]+?)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"you might also want to explore:?\s*([\s\S]+?)$", re.IGNORECASE | re.DOTALL),
    re.compile(r"some questions you could ask:?\s*([\s\S]+?)$", re.IGNORECASE | re.DOTALL),
]

_BULLET_PREFIX = re.compile(r"^[\d\-.*]+\s*")


def extract_follow_up_questions(response: str, max_questions: int = 3) -> List[str]:
    """
    Extract follow-up questions from the tail of an LLM response.
    Patterns are pre-compiled at module load time.
    """
    for pattern in _FOLLOW_UP_PATTERNS:
        match = pattern.search(response)
        if match:
            questions = [
                _BULLET_PREFIX.sub("", line).strip()
                for line in match.group(1).splitlines()
            ]
            return [q for q in questions if len(q) > 10][:max_questions]
    return []


# ============================================================================
# STEP 2.7: Streaming Generator
# ============================================================================

async def _rag_stream(
    query: str,
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
    history: Optional[List[ChatMessage]],
    top_k: int,
    threshold: float,
) -> AsyncGenerator[str, None]:
    """
    Core streaming generator.

    Workspace auth is verified by the route handler *before* this is called,
    so HTTPException cannot fire mid-stream and corrupt the SSE contract.

    Yields newline-delimited JSON chunks:
      {type: "sources", sources: [...]}
      {type: "token",   text: "..."}       ← one per word from Ollama / per chunk from OpenAI
      {type: "done",    followUpQuestions: [...]}
      {type: "error",   message: "..."}    ← only on unrecoverable failure
    """
    # --- Retrieve context --------------------------------------------------
    sources = await retrieve_context(query, workspace_id, db, k=top_k, threshold=threshold)
    yield json.dumps({"type": "sources", "sources": [s.model_dump() for s in sources]}) + "\n"

    # --- Build prompt & messages -------------------------------------------
    system_prompt = build_rag_system_prompt(query, sources)
    messages = _build_messages(system_prompt, query, history)

    # --- Generate answer ---------------------------------------------------
    full_response_parts: List[str] = []
    try:
        try:
            async for token in _stream_with_ollama(messages):
                full_response_parts.append(token)
                yield json.dumps({"type": "token", "text": token}) + "\n"
        except Exception as ollama_exc:
            logger.warning(
                "Streaming Ollama chat failed (%s), falling back to buffered generation",
                ollama_exc,
            )
            full_response = await generate_answer(messages)
            for chunk in _chunk_text_for_stream(full_response):
                full_response_parts.append(chunk)
                yield json.dumps({"type": "token", "text": chunk}) + "\n"
    except HTTPException as exc:
        yield json.dumps({"type": "error", "message": exc.detail}) + "\n"
        return

    # --- Done --------------------------------------------------------------
    full_response = "".join(full_response_parts)
    yield json.dumps({
        "type": "done",
        "followUpQuestions": extract_follow_up_questions(full_response),
    }) + "\n"


# ============================================================================
# STEP 2.8: Route Handlers
# ============================================================================

@router.post("/ask")
async def ask_past_self(
    request: AskPastSelfRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    POST /chat/ask — Stream RAG chat with the user's knowledge base.

    Auth is validated **before** the StreamingResponse is opened so that
    4xx errors are returned as proper HTTP responses, not buried in the stream.

    Event stream format:
      data: {type: "sources",  sources: [...]}
      data: {type: "token",    text: "..."}
      data: {type: "done",     followUpQuestions: [...]}
    """
    # Auth check outside the generator — HTTPException propagates cleanly here.
    await _verify_workspace_access(request.workspace_id, current_user, db)

    async def _event_stream() -> AsyncGenerator[str, None]:
        async for chunk in _rag_stream(
            query=request.query,
            workspace_id=request.workspace_id,
            user=current_user,
            db=db,
            history=request.history,
            top_k=request.top_k,
            threshold=request.similarity_threshold,
        ):
            yield f"data: {chunk}\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/ask/sync", response_model=AskPastSelfResponse)
async def ask_past_self_sync(
    request: AskPastSelfRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AskPastSelfResponse:
    """
    POST /chat/ask/sync — Non-streaming variant.
    Returns the complete response in a single JSON payload.
    """
    await _verify_workspace_access(request.workspace_id, current_user, db)

    sources = await retrieve_context(
        query=request.query,
        workspace_id=request.workspace_id,
        db=db,
        k=request.top_k,
        threshold=request.similarity_threshold,
    )

    confidence = _determine_confidence(sources)

    if confidence == "not_found":
        return AskPastSelfResponse(
            answer="I couldn't find any notes in your knowledge base relevant to that question.",
            sources=[],
            confidence="not_found",
            followUpQuestions=[],
        )

    system_prompt = build_rag_system_prompt(request.query, sources)
    messages = _build_messages(system_prompt, request.query, request.history)

    answer = await generate_answer(messages)  # raises HTTP 502 on total failure

    return AskPastSelfResponse(
        answer=answer,
        sources=sources,
        confidence=confidence,
        followUpQuestions=extract_follow_up_questions(answer),
    )
