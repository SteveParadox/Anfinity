"""FEATURE 2: "ASK YOUR PAST SELF" CHAT - RAG Pipeline Chat Endpoint."""

import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_active_user
from app.database.models import User as DBUser, Workspace, WorkspaceMember, Document
from app.database.session import get_db
from app.services.answer_generator import get_answer_generator
from app.services.top_k_retriever import get_top_k_retriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])


# ============================================================================
# STPE 2.1: Models & Types
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
    history: Optional[List[ChatMessage]] = Field(default=None, description="Last 4 message exchanges for context")
    top_k: int = Field(default=6, ge=1, le=20)
    similarity_threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class AskPastSelfResponse(BaseModel):
    """Complete RAG response with sources."""
    answer: str
    sources: List[RAGSource]
    confidence: str = Field(..., pattern="^(high|medium|low|not_found)$")
    followUpQuestions: List[str]


# ============================================================================
# STEP 2.1: RAG Core Pipeline Functions
# ============================================================================

async def retrieve_context(
    query: str,
    workspace_id: UUID,
    db: AsyncSession,
    k: int = 6,
    threshold: float = 0.3,
) -> List[RAGSource]:
    """
    Retrieve top-k notes relevant to the query.
    
    Args:
        query: User's query text
        workspace_id: Workspace to search within
        db: Database session
        k: Number of results to retrieve
        threshold: Minimum similarity threshold (0.3 = 30%)
    
    Returns:
        List of RAG sources with attribution data
    """
    try:
        # Get retriever service
        retriever = get_top_k_retriever(db=db, top_k=k, similarity_threshold=threshold)
        
        # Retrieve documents
        result = retriever.retrieve(
            query=query,
            workspace_id=workspace_id,
            top_k=k,
            similarity_threshold=threshold,
        )
        
        if not result.chunks:
            return []
        
        # Get unique document IDs for metadata lookup
        doc_ids = {chunk.document_id for chunk in result.chunks}
        
        # Fetch document metadata (title, dates, etc)
        doc_stmt = select(Document).where(Document.id.in_(doc_ids))
        doc_result = await db.execute(doc_stmt)
        docs_by_id = {str(d.id): d for d in doc_result.scalars().all()}
        
        # Map to RAGSource format with complete metadata
        sources = []
        for chunk in result.chunks:
            doc = docs_by_id.get(str(chunk.document_id))
            
            # Determine title
            title = (doc.title if doc else None) or getattr(chunk, "document_title", None) or "Untitled Note"
            
            # Determine date - try multiple sources
            created_at = ""
            if doc and hasattr(doc, "created_at") and doc.created_at:
                created_at = doc.created_at.isoformat() if hasattr(doc.created_at, "isoformat") else str(doc.created_at)
            elif hasattr(chunk, "created_at") and chunk.created_at:
                created_at = chunk.created_at.isoformat() if hasattr(chunk.created_at, "isoformat") else str(chunk.created_at)
            
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
    except Exception as exc:
        logger.error("Error retrieving context: %s", exc, exc_info=True)
        return []


def extract_excerpt(content: str, query: str, max_length: int = 300) -> str:
    """
    Extract most relevant sentence from content based on query.
    
    Args:
        content: Full text content
        query: Query to match against
        max_length: Max excerpt length
    
    Returns:
        Relevant excerpt from content
    """
    import re
    sentences = re.split(r'[.!?]+', content)
    query_words = query.lower().split()
    
    # Find sentence with most query word matches
    best_sentence = sentences[0] if sentences else ""
    best_score = 0
    
    for sentence in sentences:
        score = sum(1 for word in query_words if word.lower() in sentence.lower())
        if score > best_score:
            best_score = score
            best_sentence = sentence
    
    return best_sentence.strip()[:max_length]


def build_rag_system_prompt(query: str, sources: List[RAGSource]) -> str:
    """
    Build system prompt with strict grounding instructions.
    
    Args:
        query: User's query
        sources: Retrieved sources from knowledge base
    
    Returns:
        System prompt for LLM
    """
    if not sources:
        return f'The user\'s knowledge base contains no notes relevant to: "{query}"'
    
    # Format sources with structured context
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
# STEP 2.2: Streaming API Route
# ============================================================================

async def stream_chat(
    query: str,
    workspace_id: UUID,
    user: DBUser,
    db: AsyncSession,
    history: Optional[List[ChatMessage]] = None,
    top_k: int = 6,
    threshold: float = 0.3,
) -> AsyncGenerator[str, None]:
    """
    Stream RAG chat response with source citations.
    Uses Ollama as primary, falls back to OpenAI on failure.
    
    Yields:
        JSON-encoded chunks with type='sources', type='token', or [DONE]
    """
    # Verify workspace access
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    membership = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if not membership.scalar_one_or_none() and workspace.owner_id != user.id:
        raise HTTPException(status_code=403, detail="No access to workspace")
    
    # Retrieve context (sources)
    sources = await retrieve_context(
        query=query,
        workspace_id=workspace_id,
        db=db,
        k=top_k,
        threshold=threshold,
    )
    
    # Determine confidence before generation
    if not sources:
        confidence = "not_found"
    elif sources[0].similarity > 0.75:
        confidence = "high"
    elif sources[0].similarity > 0.5:
        confidence = "medium"
    else:
        confidence = "low"
    
    # Build system prompt
    system_prompt = build_rag_system_prompt(query, sources)
    
    # Prepare messages with conversation history
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    
    # Add last 4 exchanges (8 messages) for context
    if history:
        history_messages = [
            {"role": msg.role, "content": msg.content}
            for msg in history[-8:]
        ]
        messages.extend(history_messages)
    
    # Add current query
    messages.append({"role": "user", "content": query})
    
    # Send sources first
    yield json.dumps({"type": "sources", "sources": [s.model_dump() for s in sources]})
    yield "\n"
    
    # Try Ollama first, fall back to OpenAI
    full_response = ""
    try:
        # Try streaming with Ollama
        from app.services.llm_service import OllamaClient
        
        ollama = OllamaClient(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.OLLAMA_TIMEOUT
        )
        
        if ollama.is_available():
            logger.info("Using Ollama for chat streaming")
            
            # Format messages for Ollama (simple prompt construction)
            prompt = "\n".join([
                f"{msg['role'].upper()}: {msg['content']}" 
                for msg in messages
            ])
            
            # Generate with Ollama (returns full text, not streaming)
            response_text, _ = ollama.generate(
                prompt=prompt,
                temperature=0.3,
                num_predict=1000,
            )
            
            # Chunk the response into smaller tokens for streaming effect
            full_response = response_text
            words = response_text.split()
            for word in words:
                word_chunk = word + " "
                yield json.dumps({"type": "token", "text": word_chunk})
                yield "\n"
            
        else:
            raise RuntimeError("Ollama not available")
            
    except Exception as ollama_error:
        logger.warning(f"Ollama failed, falling back to OpenAI: {ollama_error}")
        
        # Fall back to OpenAI
        try:
            from openai import OpenAI
            
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            
            # Stream tokens from OpenAI
            with client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=messages,
                temperature=0.3,
                max_tokens=1000,
                stream=True,
            ) as stream:
                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        full_response += token
                        yield json.dumps({"type": "token", "text": token})
                        yield "\n"
        except Exception as openai_error:
            logger.error(f"Both Ollama and OpenAI failed: {openai_error}")
            yield json.dumps({"type": "error", "message": f"LLM generation failed: {openai_error}"})
            yield "\n"
            return
    
    # Extract follow-up questions from response
    follow_up_questions = extract_follow_up_questions(full_response)
    
    yield json.dumps({"type": "done", "followUpQuestions": follow_up_questions})
    yield "\n"


def extract_follow_up_questions(response: str, max_questions: int = 3) -> List[str]:
    """
    Extract follow-up questions from LLM response.
    
    Looks for common patterns like:
    - "Follow-up questions:"
    - "You might also want to explore:"
    - "Some questions you could ask:"
    """
    import re
    
    # Common patterns for follow-up questions
    patterns = [
        r"follow.up questions?:?\s*([\s\S]*?)$",
        r"you might also want to explore:?\s*([\s\S]*?)$",
        r"some questions you could ask:?\s*([\s\S]*?)$",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, response, re.IGNORECASE)
        if match:
            text = match.group(1)
            # Split by newlines and clean up
            lines = [
                re.sub(r'^[\d\-.*]+\s*', '', line).strip()
                for line in text.split("\n")
            ]
            # Filter: must have meaningful content
            questions = [l for l in lines if len(l) > 10]
            return questions[:max_questions]
    
    return []


@router.post("/ask")
async def ask_past_self(
    request: AskPastSelfRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /chat/ask - Stream RAG chat with knowledge base.
    
    Streams JSON-encoded responses:
    - First: {type: "sources", sources: [...]}
    - Then: {type: "token", text: "..."}
    - Finally: {type: "done", followUpQuestions: [...]}
    
    Client reconstructs answer from tokens and displays sources.
    """
    from fastapi.responses import StreamingResponse
    
    async def response_generator():
        async for chunk in stream_chat(
            query=request.query,
            workspace_id=request.workspace_id,
            user=current_user,
            db=db,
            history=request.history,
            top_k=request.top_k,
            threshold=request.similarity_threshold,
        ):
            yield f"data: {chunk}\n\n"
    
    return StreamingResponse(
        response_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/ask/sync", response_model=AskPastSelfResponse)
async def ask_past_self_sync(
    request: AskPastSelfRequest,
    current_user: DBUser = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AskPastSelfResponse:
    """
    POST /chat/ask/sync - Non-streaming variant for simple use cases.
    Uses Ollama as primary, falls back to OpenAI.
    
    Returns complete response with answer, sources, confidence, and follow-up questions.
    """
    # Verify workspace access
    result = await db.execute(select(Workspace).where(Workspace.id == request.workspace_id))
    workspace = result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    membership = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == request.workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
    )
    if not membership.scalar_one_or_none() and workspace.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="No access to workspace")
    
    # Retrieve context
    sources = await retrieve_context(
        query=request.query,
        workspace_id=request.workspace_id,
        db=db,
        k=request.top_k,
        threshold=request.similarity_threshold,
    )
    
    # Determine confidence
    if not sources:
        confidence = "not_found"
        answer = "I couldn't find any notes in your knowledge base relevant to that question."
        follow_up_questions = []
    else:
        if sources[0].similarity > 0.75:
            confidence = "high"
        elif sources[0].similarity > 0.5:
            confidence = "medium"
        else:
            confidence = "low"
        
        # Build prompt
        system_prompt = build_rag_system_prompt(request.query, sources)
        
        # Prepare messages
        messages = [{"role": "system", "content": system_prompt}]
        if request.history:
            messages.extend([
                {"role": msg.role, "content": msg.content}
                for msg in request.history[-8:]
            ])
        messages.append({"role": "user", "content": request.query})
        
        # Try Ollama first, fall back to OpenAI
        answer = None
        try:
            from app.services.llm_service import OllamaClient
            
            ollama = OllamaClient(
                base_url=settings.OLLAMA_BASE_URL,
                model=settings.OLLAMA_MODEL,
                timeout=settings.OLLAMA_TIMEOUT
            )
            
            if ollama.is_available():
                logger.info("Using Ollama for chat generation")
                
                # Format messages for Ollama
                prompt = "\n".join([
                    f"{msg['role'].upper()}: {msg['content']}" 
                    for msg in messages
                ])
                
                answer, _ = ollama.generate(
                    prompt=prompt,
                    temperature=0.3,
                    num_predict=1000,
                )
            else:
                raise RuntimeError("Ollama not available")
                
        except Exception as ollama_error:
            logger.warning(f"Ollama failed, falling back to OpenAI: {ollama_error}")
            
            # Fall back to OpenAI
            try:
                from openai import OpenAI
                
                client = OpenAI(api_key=settings.OPENAI_API_KEY)
                response = client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=1000,
                )
                answer = response.choices[0].message.content or ""
            except Exception as openai_error:
                logger.error(f"Both Ollama and OpenAI failed: {openai_error}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate answer: {openai_error}"
                ) from openai_error
        
        follow_up_questions = extract_follow_up_questions(answer) if answer else []
    
    return AskPastSelfResponse(
        answer=answer or "Failed to generate answer",
        sources=sources,
        confidence=confidence,
        followUpQuestions=follow_up_questions,
    )
