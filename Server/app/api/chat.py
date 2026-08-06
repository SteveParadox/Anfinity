"""FEATURE 2: "ASK YOUR PAST SELF" CHAT - RAG Pipeline Chat Endpoint."""

import asyncio
import json
import logging
import re
from typing import AsyncGenerator, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_ollama_request_headers, settings
from app.core.auth import get_current_active_user
from app.core.permissions import ensure_workspace_permission
from app.database.models import User as DBUser, WorkspaceSection
from app.database.session import get_db
from app.services.strict_note_rag import (
    CitationStreamTransformer,
    GroundedAnswerStreamGuard,
    MIN_SUPPORTED_SCORE,
    REFUSAL_TEXT,
    StrictRAGResult,
    StrictRAGSource,
    calibrate_similarity_percent,
    cited_sources_from_answer,
    evaluate_sources,
    replace_source_markers,
    retrieve_strict_note_context,
)
from app.services.settings_preferences import get_workspace_ai_min_similarity, workspace_feature_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])
_OLLAMA_STREAM_CONCURRENCY = max(1, int(getattr(settings, "OLLAMA_MAX_CONCURRENT_REQUESTS", 2) or 2))
_OLLAMA_STREAM_SEMAPHORE = asyncio.Semaphore(_OLLAMA_STREAM_CONCURRENCY)


# ============================================================================
# STEP 2.1: Models & Types
# ============================================================================

class RAGSource(BaseModel):
    """Source document for RAG response with attribution."""
    sourceId: str = ""
    noteId: str
    title: str
    excerpt: str
    createdAt: str
    similarity: float
    similarityPercent: int = 0
    citation: str = ""
    chunkId: str = ""
    chunkIndex: int = 0
    supportLevel: str = "supported"


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
    answerStatus: str = Field(default="supported", pattern="^(supported|partial|refusal)$")


# ============================================================================
# STEP 2.2: Shared Helpers
# ============================================================================

async def _verify_workspace_access(
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
) -> None:
    """
    Verify the user has access to the requested workspace.
    Raises HTTPException on any failure — call this *before* starting a stream.

    Raises before any retrieval or streaming work if the user cannot use chat in the workspace.
    """
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=user,
        db=db,
        section=WorkspaceSection.CHAT,
        action="create",
    )
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=user,
        db=db,
        section=WorkspaceSection.NOTES,
        action="view",
    )
    if not await workspace_feature_enabled(db, workspace_id, "ai_search", "ask_past_self_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ask Your Past Self is disabled for this workspace.",
        )


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
    user: DBUser,
    db: AsyncSession,
    k: int = 6,
    threshold: float = 0.3,
) -> StrictRAGResult:
    """Retrieve live note-only evidence and gate answerability."""
    try:
        workspace_min_score = await get_workspace_ai_min_similarity(db, workspace_id)
        min_score = max(float(threshold or 0.0), workspace_min_score, MIN_SUPPORTED_SCORE)
        return await retrieve_strict_note_context(
            query=query,
            workspace_id=workspace_id,
            user=user,
            db=db,
            limit=k,
            min_score=min_score,
        )
    except Exception:
        logger.exception("Error retrieving context for query=%r workspace=%s", query, workspace_id)
        return StrictRAGResult(
            sources=[],
            answer_status="refusal",
            confidence="not_found",
            refusal_reason="retrieval_error",
        )


# ============================================================================
# STEP 2.4: Prompt Construction
# ============================================================================

def build_rag_system_prompt(query: str, sources: List[StrictRAGSource | RAGSource]) -> str:
    """Build a strictly-grounded system prompt from retrieved sources."""
    if not sources:
        return (
            "The user's knowledge base does not contain any relevant notes for this question. "
            "Do not answer with general knowledge."
        )

    source_context = "\n".join(
        f"""
[{getattr(s, "source_id", "") or getattr(s, "sourceId", "") or f"S{i + 1}"}]
Note title: "{s.title}"
Note date: {getattr(s, "created_at", "") or getattr(s, "createdAt", "")}
Similarity: {getattr(s, "similarity_percent", 0) or getattr(s, "similarityPercent", 0) or round(s.similarity * 100)}%
Matched note excerpt: {s.excerpt}
---"""
        for i, s in enumerate(sources)
    )

    return f"""You are the user's personal AI assistant with access ONLY to their private notes.
Your job is to answer their question using ONLY information from the provided sources.

STRICT RULES:
1. ONLY use information from the provided sources. Never use general knowledge.
2. Cite every factual claim inline with the source marker, for example [S1].
3. Use only the source markers listed below. Never invent note titles, dates, or similarity values.
4. If the sources don't contain enough information, say exactly: "{REFUSAL_TEXT}"
5. Never invent facts, dates, or explanations that are not explicitly supported by the sources.
6. Refer to the user in second person ("you wrote", "your notes say").
7. Keep the answer focused and grounded in the cited notes.
8. Do not cite sources you did not use.
9. Do not answer from memory or world knowledge even if you know the topic.
10. If a sentence cannot be supported by a source marker, omit the sentence.

LIVE NOTE EVIDENCE FROM THE USER'S ACCESSIBLE NOTES:
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
        async with httpx.AsyncClient(
            timeout=float(settings.OLLAMA_TIMEOUT),
            headers=get_ollama_request_headers(),
        ) as client:
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

    client_kwargs = {
        "api_key": settings.OPENAI_API_KEY,
        "timeout": 30.0,
    }
    if getattr(settings, "OPENAI_BASE_URL", None):
        client_kwargs["base_url"] = settings.OPENAI_BASE_URL

    client = AsyncOpenAI(**client_kwargs)
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


def _api_sources(sources: List[StrictRAGSource]) -> List[dict]:
    """Serialize only server-validated note sources for the client."""
    return [source.to_api_dict() for source in sources]


def _sse_event(event: str, payload: dict) -> str:
    """Emit a named SSE event while preserving the legacy payload type field."""
    body = dict(payload)
    body.setdefault("type", event)
    return f"event: {event}\ndata: {json.dumps(body)}\n\n"


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

    Yields named SSE chunks:
      event: start   data: {type, correlationId, status}
      event: token   data: {type, text, correlationId}
      event: sources data: {type, sources, answerStatus, confidence, correlationId}
      event: done    data: {type, followUpQuestions, answerStatus, confidence, correlationId}
      event: error   data: {type, message, correlationId}
    """
    correlation_id = str(uuid4())
    yield _sse_event("start", {"correlationId": correlation_id, "status": "retrieving"})

    retrieval = await retrieve_context(
        query=query,
        workspace_id=workspace_id,
        user=user,
        db=db,
        k=top_k,
        threshold=threshold,
    )

    if not retrieval.can_answer:
        logger.info(
            "ask_past_self_refused",
            extra={
                "correlation_id": correlation_id,
                "workspace_id": str(workspace_id),
                "user_id": str(getattr(user, "id", "")),
                "reason": retrieval.refusal_reason,
                **retrieval.diagnostics,
            },
        )
        yield _sse_event("token", {"text": REFUSAL_TEXT, "correlationId": correlation_id})
        yield _sse_event(
            "sources",
            {
                "sources": [],
                "answerStatus": "refusal",
                "confidence": "not_found",
                "correlationId": correlation_id,
            },
        )
        yield _sse_event(
            "done",
            {
                "followUpQuestions": [],
                "answerStatus": "refusal",
                "confidence": "not_found",
                "correlationId": correlation_id,
            },
        )
        return

    # --- Build prompt & messages -------------------------------------------
    sources = retrieval.sources
    system_prompt = build_rag_system_prompt(query, sources)
    messages = _build_messages(system_prompt, query, history)
    citation_transformer = CitationStreamTransformer(sources)
    grounding_guard = GroundedAnswerStreamGuard(sources)

    # --- Generate answer ---------------------------------------------------
    full_response_parts: List[str] = []
    try:
        try:
            async for token in _stream_with_ollama(messages):
                full_response_parts.append(token)
                grounded_token = grounding_guard.feed(token)
                display_token = citation_transformer.feed(grounded_token)
                if display_token:
                    yield _sse_event("token", {"text": display_token, "correlationId": correlation_id})
        except Exception as ollama_exc:
            logger.warning(
                "Streaming Ollama chat failed (%s), falling back to buffered generation",
                ollama_exc,
            )
            full_response = await generate_answer(messages)
            for chunk in _chunk_text_for_stream(full_response):
                full_response_parts.append(chunk)
                grounded_chunk = grounding_guard.feed(chunk)
                display_chunk = citation_transformer.feed(grounded_chunk)
                if display_chunk:
                    yield _sse_event("token", {"text": display_chunk, "correlationId": correlation_id})
        grounded_tail = grounding_guard.flush()
        if not grounding_guard.released_any and REFUSAL_TEXT not in "".join(full_response_parts):
            logger.warning(
                "ask_past_self_suppressed_uncited_answer",
                extra={
                    "correlation_id": correlation_id,
                    "workspace_id": str(workspace_id),
                    "user_id": str(getattr(user, "id", "")),
                },
            )
            grounded_tail = REFUSAL_TEXT
        tail = citation_transformer.feed(grounded_tail) + citation_transformer.flush()
        if tail:
            yield _sse_event("token", {"text": tail, "correlationId": correlation_id})
    except HTTPException as exc:
        yield _sse_event("error", {"message": exc.detail, "correlationId": correlation_id})
        return

    # --- Done --------------------------------------------------------------
    full_response = "".join(full_response_parts)
    cited_sources = cited_sources_from_answer(full_response, sources)
    answer_status = retrieval.answer_status
    confidence = retrieval.confidence
    if not cited_sources and REFUSAL_TEXT not in full_response:
        logger.warning(
            "ask_past_self_answer_without_valid_citations",
            extra={
                "correlation_id": correlation_id,
                "workspace_id": str(workspace_id),
                "user_id": str(getattr(user, "id", "")),
            },
        )
        answer_status = "refusal"
        confidence = "not_found"

    yield _sse_event(
        "sources",
        {
            "sources": _api_sources(cited_sources),
            "answerStatus": answer_status,
            "confidence": confidence,
            "correlationId": correlation_id,
        },
    )
    yield _sse_event(
        "done",
        {
            "followUpQuestions": extract_follow_up_questions(full_response),
            "answerStatus": answer_status,
            "confidence": confidence,
            "correlationId": correlation_id,
        },
    )


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
      event: start   data: {type, correlationId, status}
      event: token   data: {type, text, correlationId}
      event: sources data: {type, sources, answerStatus, confidence, correlationId}
      event: done    data: {type, followUpQuestions, answerStatus, confidence, correlationId}
      event: error   data: {type, message, correlationId}
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
            yield chunk

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

    retrieval = await retrieve_context(
        query=request.query,
        workspace_id=request.workspace_id,
        user=current_user,
        db=db,
        k=request.top_k,
        threshold=request.similarity_threshold,
    )

    if not retrieval.can_answer:
        return AskPastSelfResponse(
            answer=REFUSAL_TEXT,
            sources=[],
            confidence="not_found",
            followUpQuestions=[],
            answerStatus="refusal",
        )

    sources = retrieval.sources
    system_prompt = build_rag_system_prompt(request.query, sources)
    messages = _build_messages(system_prompt, request.query, request.history)

    raw_answer = await generate_answer(messages)  # raises HTTP 502 on total failure
    cited_sources = cited_sources_from_answer(raw_answer, sources)
    if not cited_sources and REFUSAL_TEXT not in raw_answer:
        logger.warning(
            "ask_past_self_sync_uncited_answer_refused",
            extra={
                "workspace_id": str(request.workspace_id),
                "user_id": str(getattr(current_user, "id", "")),
            },
        )
        return AskPastSelfResponse(
            answer=REFUSAL_TEXT,
            sources=[],
            confidence="not_found",
            followUpQuestions=[],
            answerStatus="refusal",
        )
    answer = replace_source_markers(raw_answer, sources)

    return AskPastSelfResponse(
        answer=answer,
        sources=[RAGSource(**source.to_api_dict()) for source in cited_sources],
        confidence="not_found" if REFUSAL_TEXT in raw_answer else retrieval.confidence,
        followUpQuestions=extract_follow_up_questions(raw_answer),
        answerStatus="refusal" if REFUSAL_TEXT in raw_answer else retrieval.answer_status,
    )
