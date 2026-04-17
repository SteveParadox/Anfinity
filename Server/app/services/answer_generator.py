from __future__ import annotations

"""Answer generation service with robust fallback behavior for STEP 4.

Key improvements:
- Do not fail the whole pipeline when cross-check filtering removes all chunks.
- Be gentler with short queries and semantic-only matches.
- Preserve strong high-similarity chunks even when lexical/domain heuristics are weak.
- Return a clean no-answer object only when there is truly no usable evidence.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app import config as app_config
from app.services.retrieval_cross_checker import RetrievalCrossChecker, RetrievalValidation
from app.services.retrieval_relevance import analyze_chunk_relevance, analyze_query_intent

logger = logging.getLogger(__name__)
settings = app_config.settings


def _ai_runtime():
    getter = getattr(app_config, "get_ai_runtime_config", None)
    return getter() if callable(getter) else getattr(settings, "ai_runtime", None)


def _ollama_headers() -> Dict[str, str]:
    getter = getattr(app_config, "get_ollama_request_headers", None)
    if callable(getter):
        return getter()
    return {"Content-Type": "application/json"}


@dataclass
class RetrievedChunk:
    """Retrieved chunk from STEP 3 retrieval."""

    chunk_id: str
    document_id: str
    similarity: float
    text: str
    source_type: str
    chunk_index: int
    document_title: str
    token_count: int
    context_before: Optional[str] = None
    context_after: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Citation:
    """Citation reference in generated answer."""

    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    similarity: float
    text_snippet: str


@dataclass
class ChunkQualityIssue:
    """Quality issue detected in retrieved chunk."""

    chunk_id: str
    issue_type: str
    severity: str
    message: str
    affected_document: str


@dataclass
class RetrievalCrossCheck:
    """Cross-check results for retrieved chunks."""

    filtered_chunks: List[RetrievedChunk]
    quality_issues: List[ChunkQualityIssue]
    diversity_score: float
    has_conflicts: bool
    conflict_details: List[Dict[str, Any]]
    high_quality_chunks: int
    low_quality_chunks: int
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


@dataclass
class GeneratedAnswer:
    """Generated answer with citations and metadata."""

    answer_text: str
    citations: List[Citation]
    confidence_score: float
    model_used: str
    tokens_used: int
    generation_time_ms: float
    average_similarity: float
    unique_documents: int
    metadata: Dict[str, Any]
    validation: Optional[RetrievalValidation] = None
    quality_check: Optional[RetrievalCrossCheck] = None
    cross_doc_agreement_score: float = 0.0
    top_k: int = 10


class AnswerGenerator:
    """Generate answers from retrieved chunks using Ollama only."""

    TECHNICAL_TERMS = {
        "ai", "llm", "embedding", "embeddings", "model", "models", "semantic",
        "search", "timeout", "latency", "retrieval", "qdrant", "ollama", "phi",
        "token", "tokens", "sql", "python", "database", "postgres", "pgvector",
        "api", "apis", "chunk", "chunks", "workspace", "index", "indexing",
    }

    def __init__(
        self,
        model: Optional[str] = None,
        openai_model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        openai_api_key: Optional[str] = None,
        ollama_base_url: Optional[str] = None,
        similarity_threshold: float = 0.5,
        min_unique_documents: int = 1,
        detect_conflicts: bool = True,
        ollama_timeout: Optional[int] = None,
    ):
        runtime = _ai_runtime()
        llm_runtime = getattr(runtime, "llm", None)
        ollama_runtime = getattr(runtime, "ollama", None)

        self.model = model or getattr(llm_runtime, "ollama_model", getattr(settings, "OLLAMA_MODEL", "phi3:mini"))
        self.openai_model = openai_model or getattr(
            llm_runtime,
            "openai_model",
            getattr(settings, "OPENAI_MODEL", "gpt-4o-mini"),
        )
        self.temperature = temperature if temperature is not None else getattr(
            llm_runtime,
            "temperature",
            getattr(settings, "LLM_TEMPERATURE", 0.3),
        )
        self.max_tokens = max_tokens or getattr(llm_runtime, "max_tokens", getattr(settings, "LLM_MAX_TOKENS", 1000))
        self.ollama_base_url = (
            ollama_base_url or getattr(ollama_runtime, "base_url", getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"))
        ).rstrip("/")
        configured_fallback_model = (
            getattr(ollama_runtime, "fallback_model", None)
            or getattr(settings, "OLLAMA_FALLBACK_MODEL", None)
            or None
        )
        self.ollama_fallback_model = configured_fallback_model or (
            "phi3:mini" if self.model != "phi3:mini" else self.model
        )
        self.similarity_threshold = similarity_threshold
        self.min_unique_documents = min_unique_documents
        self.detect_conflicts = detect_conflicts
        self.ollama_timeout = ollama_timeout or getattr(
            ollama_runtime,
            "timeout",
            getattr(settings, "OLLAMA_TIMEOUT", 150),
        )
        self.ollama_connect_timeout = float(
            getattr(ollama_runtime, "connect_timeout", getattr(settings, "OLLAMA_CONNECT_TIMEOUT", 10)) or 10
        )
        self.ollama_read_timeout = float(
            getattr(ollama_runtime, "read_timeout", getattr(settings, "OLLAMA_READ_TIMEOUT", self.ollama_timeout))
            or self.ollama_timeout
        )
        self.ollama_write_timeout = float(
            getattr(ollama_runtime, "write_timeout", getattr(settings, "OLLAMA_WRITE_TIMEOUT", 30)) or 30
        )
        self.ollama_pool_timeout = float(
            getattr(ollama_runtime, "pool_timeout", getattr(settings, "OLLAMA_POOL_TIMEOUT", 30)) or 30
        )
        self.max_context_chunks = min(4, max(2, getattr(settings, "RAG_MAX_CONTEXT_CHUNKS", 4)))
        self.max_chunk_chars = max(600, getattr(settings, "RAG_MAX_CHUNK_CHARS", 1200))
        self.max_total_context_chars = max(
            self.max_chunk_chars,
            int(getattr(settings, "RAG_MAX_TOTAL_CONTEXT_CHARS", self.max_context_chunks * self.max_chunk_chars) or (self.max_context_chunks * self.max_chunk_chars)),
        )
        self.compact_context_chars = max(
            max(1200, self.max_chunk_chars),
            int(getattr(settings, "RAG_COMPACT_CONTEXT_CHARS", max(2200, self.max_total_context_chars // 2)) or max(2200, self.max_total_context_chars // 2)),
        )
        self.min_answer_confidence = float(getattr(settings, "RAG_MIN_ANSWER_CONFIDENCE", 45.0))

        self.cross_checker = RetrievalCrossChecker(
            similarity_threshold=similarity_threshold,
            min_diversity_documents=min_unique_documents,
            conflict_detection_enabled=detect_conflicts,
        )

        logger.info(
            "AnswerGenerator initialized: model=%s fallback_model=%s url=%s timeouts(connect=%.1fs, read=%.1fs, write=%.1fs, pool=%.1fs) threshold=%.2f",
            self.model,
            self.ollama_fallback_model,
            self.ollama_base_url,
            self.ollama_connect_timeout,
            self.ollama_read_timeout,
            self.ollama_write_timeout,
            self.ollama_pool_timeout,
            self.similarity_threshold,
        )

    async def generate(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        include_citations: bool = True,
        citation_style: str = "inline",
        top_k: int = 10,
    ) -> GeneratedAnswer:
        """Generate answer from query and retrieved chunks."""
        if not chunks:
            return self._build_no_answer(
                reason="no_chunks_provided",
                query=query,
                generation_time_ms=0.0,
                quality_check=None,
            )

        start_time = time.time()

        quality_check = self._perform_cross_check(query, chunks)
        filtered_chunks = list(quality_check.filtered_chunks)

        if not filtered_chunks:
            logger.warning("All chunks filtered out, applying fallback strategy")
            fallback_chunks = self._fallback_chunks(query, chunks)
            if fallback_chunks:
                filtered_chunks = fallback_chunks
                quality_check.filtered_chunks = fallback_chunks
                quality_check.high_quality_chunks = len(fallback_chunks)
                quality_check.low_quality_chunks = max(0, len(chunks) - len(fallback_chunks))
                quality_check.fallback_used = True
                quality_check.fallback_reason = "cross_check_filtered_everything"
            else:
                generation_time_ms = (time.time() - start_time) * 1000
                return self._build_no_answer(
                    reason="all_chunks_filtered_out",
                    query=query,
                    generation_time_ms=generation_time_ms,
                    quality_check=quality_check,
                )

        filtered_chunks = self._select_chunks_for_generation(query, filtered_chunks)

        if not filtered_chunks:
            generation_time_ms = (time.time() - start_time) * 1000
            return self._build_no_answer(
                reason="no_chunks_after_context_trim",
                query=query,
                generation_time_ms=generation_time_ms,
                quality_check=quality_check,
            )

        context = self._build_context(filtered_chunks, include_citations)
        system_prompt = self._build_system_prompt(citation_style)
        user_prompt = self._build_user_prompt(query, context, filtered_chunks)

        try:
            answer_text, tokens_used, model_used = await self._call_llm(system_prompt, user_prompt)
        except Exception as exc:
            logger.warning("Primary Ollama generation failed, retrying with compact context: %s", exc)
            compact_chunks = self._select_chunks_for_generation(query, filtered_chunks, compact=True)
            compact_context = self._build_context(compact_chunks, include_citations)
            compact_user_prompt = self._build_user_prompt(query, compact_context, compact_chunks)
            try:
                answer_text, tokens_used, model_used = await self._call_llm(
                    system_prompt,
                    compact_user_prompt,
                    model_override=self.ollama_fallback_model,
                    max_tokens_override=max(250, int(self.max_tokens * 0.6)),
                    num_ctx_override=max(1536, self.max_context_chunks * 768),
                )
                filtered_chunks = compact_chunks
                quality_check.filtered_chunks = compact_chunks
                quality_check.high_quality_chunks = len(compact_chunks)
                quality_check.low_quality_chunks = max(0, len(chunks) - len(compact_chunks))
                quality_check.fallback_used = True
                quality_check.fallback_reason = "compact_llm_retry"
            except Exception as retry_exc:
                logger.warning(
                    "Compact Ollama retry failed, using extractive grounded fallback: %s",
                    retry_exc,
                )
                return self._build_extractive_grounded_answer(
                    query=query,
                    chunks=filtered_chunks,
                    quality_check=quality_check,
                    top_k=top_k,
                    include_citations=include_citations,
                    llm_error=retry_exc,
                )

        citations = self._extract_citations(filtered_chunks, answer_text) if include_citations else []
        cross_doc_agreement_score = self._calculate_cross_doc_agreement(filtered_chunks, quality_check)
        confidence = self._calculate_confidence_step5(
            filtered_chunks,
            quality_check,
            top_k,
            cross_doc_agreement_score,
        )

        generation_time_ms = (time.time() - start_time) * 1000
        average_similarity = sum(c.similarity for c in filtered_chunks) / len(filtered_chunks)
        unique_documents = len(set(c.document_id for c in filtered_chunks))

        metadata = {
            "query_length": len(query),
            "chunks_used": len(filtered_chunks),
            "chunks_filtered": max(0, len(chunks) - len(filtered_chunks)),
            "unique_documents": unique_documents,
            "average_similarity": round(average_similarity, 3),
            "max_similarity": max(c.similarity for c in filtered_chunks),
            "min_similarity": min(c.similarity for c in filtered_chunks),
            "response_length": len(answer_text),
            "citations_count": len(citations),
            "model": model_used,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "context_chars": len(context),
            "quality_issues_found": len(quality_check.quality_issues),
            "has_conflicts": quality_check.has_conflicts,
            "diversity_score": round(quality_check.diversity_score, 3),
            "high_quality_chunks": quality_check.high_quality_chunks,
            "low_quality_chunks": quality_check.low_quality_chunks,
            "cross_doc_agreement_score": round(cross_doc_agreement_score, 3),
            "top_k_used": top_k,
            "fallback_used": quality_check.fallback_used,
            "fallback_reason": quality_check.fallback_reason,
        }

        return GeneratedAnswer(
            answer_text=answer_text,
            citations=citations,
            confidence_score=confidence,
            model_used=model_used,
            tokens_used=tokens_used,
            generation_time_ms=round(generation_time_ms, 2),
            average_similarity=round(average_similarity, 3),
            unique_documents=unique_documents,
            metadata=metadata,
            quality_check=quality_check,
            cross_doc_agreement_score=round(cross_doc_agreement_score, 3),
            top_k=top_k,
        )

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model_override: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        num_ctx_override: Optional[int] = None,
    ) -> Tuple[str, int, str]:
        model_name = model_override or self.model
        logger.info(
            "Calling Ollama model=%s url=%s timeouts(connect=%.1fs, read=%.1fs, write=%.1fs, pool=%.1fs)",
            model_name,
            self.ollama_base_url,
            self.ollama_connect_timeout,
            self.ollama_read_timeout,
            self.ollama_write_timeout,
            self.ollama_pool_timeout,
        )
        try:
            answer_text = await self._ollama_generate(
                system_prompt,
                user_prompt,
                model_override=model_override,
                max_tokens_override=max_tokens_override,
                num_ctx_override=num_ctx_override,
            )
            return answer_text, 0, model_name
        except Exception as exc:
            logger.error("Ollama inference failed: %s", exc, exc_info=True)
            raise RuntimeError(
                f"Ollama inference failed (exclusive mode, no fallback). Error: {type(exc).__name__}: {exc}"
            ) from exc

    async def _ollama_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model_override: Optional[str] = None,
        max_tokens_override: Optional[int] = None,
        num_ctx_override: Optional[int] = None,
    ) -> str:
        url = f"{self.ollama_base_url}/api/chat"
        model_name = model_override or self.model
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens_override or self.max_tokens,
                "num_ctx": num_ctx_override or max(2048, self.max_context_chunks * 1024),
            },
        }

        timeout = httpx.Timeout(
            connect=self.ollama_connect_timeout,
            read=self.ollama_read_timeout,
            write=self.ollama_write_timeout,
            pool=self.ollama_pool_timeout,
        )
        request_started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=_ollama_headers()) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        finally:
            elapsed_ms = (time.perf_counter() - request_started) * 1000
            logger.info(
                "Ollama request finished: model=%s elapsed_ms=%.1f payload_chars=%s",
                model_name,
                elapsed_ms,
                len(system_prompt) + len(user_prompt),
            )

        data = response.json()
        return data["message"]["content"]

    def _split_sentences(self, text: str) -> List[str]:
        parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
        return [
            re.sub(r"\s+", " ", part).strip()
            for part in parts
            if len(re.sub(r"\s+", " ", part).strip()) >= 30
        ]

    def _build_extractive_grounded_answer(
        self,
        *,
        query: str,
        chunks: List[RetrievedChunk],
        quality_check: Optional[RetrievalCrossCheck],
        top_k: int,
        include_citations: bool,
        llm_error: Exception,
    ) -> GeneratedAnswer:
        if quality_check:
            quality_check.fallback_used = True
            quality_check.fallback_reason = "extractive_grounded_fallback"

        query_terms = set(self._query_terms(query))
        candidates: List[Tuple[float, RetrievedChunk, str]] = []

        for chunk in chunks:
            sentences = self._split_sentences(chunk.text or "") or [(chunk.text or "").strip()]
            for sentence_index, sentence in enumerate(sentences[:4]):
                sentence_terms = set(self._query_terms(sentence))
                lexical_overlap = (
                    len(query_terms & sentence_terms) / max(len(query_terms), 1)
                    if query_terms else 0.0
                )
                relevance = analyze_chunk_relevance(
                    query,
                    sentence,
                    title=chunk.document_title,
                    tags=(chunk.metadata or {}).get("tags"),
                    metadata=chunk.metadata,
                    source_type=chunk.source_type,
                )
                evidence_score = max(
                    float((chunk.metadata or {}).get("generator_evidence_score", 0.0) or 0.0),
                    relevance.evidence_score,
                )
                score = (
                    lexical_overlap * 0.35
                    + evidence_score * 0.25
                    + float(chunk.similarity or 0.0) * 0.25
                    + relevance.domain_alignment * 0.10
                    + (0.05 if sentence_index == 0 else 0.0)
                )
                candidates.append((score, chunk, sentence))

        candidates.sort(key=lambda item: item[0], reverse=True)

        selected: List[Tuple[RetrievedChunk, str]] = []
        seen_docs: set[str] = set()
        seen_sentences: set[str] = set()
        max_sentences = 2 if len({str(chunk.document_id) for chunk in chunks}) <= 2 else 3

        for _, chunk, sentence in candidates:
            normalized_sentence = sentence.strip().lower()
            if not normalized_sentence or normalized_sentence in seen_sentences:
                continue
            doc_key = str(chunk.document_id)
            if doc_key in seen_docs and len(selected) >= max_sentences:
                continue
            selected.append((chunk, sentence.strip()))
            seen_docs.add(doc_key)
            seen_sentences.add(normalized_sentence)
            if len(selected) >= max_sentences:
                break

        if not selected and chunks:
            selected = [(chunks[0], (chunks[0].text or "").strip()[:240])]

        answer_parts: List[str] = []
        citations: List[Citation] = []
        for chunk, sentence in selected:
            cleaned = sentence.rstrip().rstrip(".!?")
            if include_citations and chunk.document_title:
                cleaned = f"{cleaned} [Source: {chunk.document_title}]"
            if cleaned:
                cleaned = f"{cleaned}."
            answer_parts.append(cleaned)
            if include_citations:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_title=chunk.document_title,
                        chunk_index=chunk.chunk_index,
                        similarity=chunk.similarity,
                        text_snippet=sentence[:200],
                    )
                )

        answer_text = " ".join(part for part in answer_parts if part).strip()
        if not answer_text:
            return self._build_no_answer(
                reason="extractive_fallback_empty",
                query=query,
                generation_time_ms=0.0,
                quality_check=quality_check,
            )

        cross_doc_agreement_score = self._calculate_cross_doc_agreement(chunks, quality_check)
        confidence = self._calculate_confidence_step5(
            chunks,
            quality_check,
            top_k,
            cross_doc_agreement_score,
        )
        confidence = max(0.0, min(round(confidence * 0.72, 1), 58.0))

        metadata = {
            "query_length": len(query),
            "chunks_used": len(chunks),
            "chunks_filtered": 0,
            "unique_documents": len({str(chunk.document_id) for chunk in chunks}),
            "response_length": len(answer_text),
            "citations_count": len(citations),
            "model": "extractive-grounded-fallback",
            "quality_issues_found": len(quality_check.quality_issues) if quality_check else 0,
            "has_conflicts": quality_check.has_conflicts if quality_check else False,
            "diversity_score": round(quality_check.diversity_score, 3) if quality_check else 0.0,
            "high_quality_chunks": quality_check.high_quality_chunks if quality_check else len(chunks),
            "low_quality_chunks": quality_check.low_quality_chunks if quality_check else 0,
            "cross_doc_agreement_score": round(cross_doc_agreement_score, 3),
            "top_k_used": top_k,
            "fallback_used": True,
            "fallback_reason": "extractive_grounded_fallback",
            "llm_error": type(llm_error).__name__,
        }

        return GeneratedAnswer(
            answer_text=answer_text,
            citations=citations,
            confidence_score=confidence,
            model_used="extractive-grounded-fallback",
            tokens_used=0,
            generation_time_ms=0.0,
            average_similarity=round(sum(c.similarity for c in chunks) / len(chunks), 3) if chunks else 0.0,
            unique_documents=len({str(chunk.document_id) for chunk in chunks}),
            metadata=metadata,
            quality_check=quality_check,
            cross_doc_agreement_score=round(cross_doc_agreement_score, 3),
            top_k=top_k,
        )

    def _perform_cross_check(self, query: str, chunks: List[RetrievedChunk]) -> RetrievalCrossCheck:
        quality_issues: List[ChunkQualityIssue] = []
        filtered_chunks: List[RetrievedChunk] = []
        intent = analyze_query_intent(query)

        query_terms = set(self._query_terms(query))
        is_short_query = len(query.split()) <= 2
        technical_query = len(query_terms & self.TECHNICAL_TERMS) >= 2

        for chunk in chunks:
            chunk_terms = set(self._query_terms(chunk.text)) if chunk.text else set()
            lexical_overlap = len(query_terms & chunk_terms) / max(len(query_terms), 1) if query_terms else 0.0
            relevance = analyze_chunk_relevance(
                query,
                chunk.text or "",
                title=chunk.document_title,
                tags=(chunk.metadata or {}).get("tags"),
                metadata=chunk.metadata,
                source_type=chunk.source_type,
            )
            metadata = dict(chunk.metadata or {})
            metadata["generator_lexical_overlap"] = round(max(lexical_overlap, relevance.lexical_overlap), 4)
            metadata["generator_domain_alignment"] = round(relevance.domain_alignment, 4)
            metadata["generator_evidence_score"] = round(relevance.evidence_score, 4)
            metadata["generator_off_topic"] = relevance.off_topic
            chunk.metadata = metadata

            narrative_mismatch = (
                technical_query
                and self._looks_like_wrong_domain(query, [chunk])
                and lexical_overlap < (0.10 if is_short_query else 0.15)
            )

            if chunk.similarity < self.similarity_threshold:
                quality_issues.append(
                    ChunkQualityIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="low_similarity",
                        severity="medium",
                        message=f"Chunk similarity {chunk.similarity:.3f} below threshold {self.similarity_threshold}",
                        affected_document=chunk.document_title,
                    )
                )
                continue

            if relevance.off_topic:
                quality_issues.append(
                    ChunkQualityIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="off_topic",
                        severity="high",
                        message="Chunk is strongly aligned to a conflicting topic/domain",
                        affected_document=chunk.document_title,
                    )
                )
                continue

            if (not is_short_query) and lexical_overlap < 0.05 and chunk.similarity < (self.similarity_threshold + 0.06):
                quality_issues.append(
                    ChunkQualityIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="low_lexical_support",
                        severity="high",
                        message="Chunk passed vector search but has almost no lexical support for the query",
                        affected_document=chunk.document_title,
                    )
                )
                continue

            if intent.is_domain_specific and relevance.evidence_score < 0.16 and relevance.domain_alignment < 0.12:
                quality_issues.append(
                    ChunkQualityIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="weak_domain_support",
                        severity="high",
                        message="Chunk has weak topical alignment to the query intent",
                        affected_document=chunk.document_title,
                    )
                )
                continue

            if narrative_mismatch and chunk.similarity < 0.70:
                quality_issues.append(
                    ChunkQualityIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="domain_mismatch",
                        severity="high",
                        message="Chunk looks narrative/literary while the query is technical",
                        affected_document=chunk.document_title,
                    )
                )
                continue

            filtered_chunks.append(chunk)

        if not filtered_chunks and chunks:
            best = max(chunks, key=lambda c: c.similarity)
            best_relevance = analyze_chunk_relevance(
                query,
                best.text or "",
                title=best.document_title,
                tags=(best.metadata or {}).get("tags"),
                metadata=best.metadata,
                source_type=best.source_type,
            )
            if not best_relevance.off_topic and best_relevance.evidence_score >= 0.20:
                logger.warning("Rescuing best chunk after filtering: %s", best.chunk_id)
                filtered_chunks = [best]

        high_quality_chunks = len(filtered_chunks)
        low_quality_chunks = max(0, len(chunks) - high_quality_chunks)
        unique_doc_ids = set(c.document_id for c in filtered_chunks)
        diversity_score = min(len(unique_doc_ids) / 3.0, 1.0)

        conflicts: List[Dict[str, Any]] = []
        has_conflicts = False
        if self.detect_conflicts and len(filtered_chunks) > 1:
            conflicts, has_conflicts = self._detect_conflicts(filtered_chunks)
            if has_conflicts:
                for conflict in conflicts:
                    quality_issues.append(
                        ChunkQualityIssue(
                            chunk_id=conflict["chunk_ids"][0],
                            issue_type="conflict",
                            severity="high",
                            message=conflict["description"],
                            affected_document=conflict["documents"],
                        )
                    )

        return RetrievalCrossCheck(
            filtered_chunks=filtered_chunks,
            quality_issues=quality_issues,
            diversity_score=diversity_score,
            has_conflicts=has_conflicts,
            conflict_details=conflicts,
            high_quality_chunks=high_quality_chunks,
            low_quality_chunks=low_quality_chunks,
        )

    def _fallback_chunks(self, query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        if not chunks:
            return []

        query_terms = set(self._query_terms(query))
        is_short_query = len(query.split()) <= 2
        scored: List[Tuple[float, RetrievedChunk]] = []

        for chunk in chunks:
            text = chunk.text or ""
            chunk_terms = set(self._query_terms(text))
            lexical_overlap = len(query_terms & chunk_terms) / max(len(query_terms), 1) if query_terms else 0.0
            substring_hit = 0.0
            lowered_query = query.strip().lower()
            lowered_text = text.lower()
            if lowered_query and (lowered_query in lowered_text or any(term in lowered_text for term in query_terms if len(term) >= 3)):
                substring_hit = 0.08

            score = chunk.similarity * 0.85 + lexical_overlap * 0.15 + substring_hit
            if is_short_query:
                score = chunk.similarity * 0.92 + lexical_overlap * 0.08 + substring_hit
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)
        rescued = [chunk for score, chunk in scored[:3] if chunk.similarity >= max(0.40, self.similarity_threshold - 0.08)]
        return rescued

    def _score_chunk_for_generation(self, chunk: RetrievedChunk) -> float:
        metadata = chunk.metadata or {}
        evidence_score = float(metadata.get("generator_evidence_score", 0.0) or 0.0)
        domain_alignment = float(metadata.get("generator_domain_alignment", 0.0) or 0.0)
        lexical_overlap = float(metadata.get("generator_lexical_overlap", 0.0) or 0.0)
        off_topic_penalty = 0.35 if metadata.get("generator_off_topic") else 0.0
        return (
            float(chunk.similarity or 0.0) * 0.45
            + evidence_score * 0.25
            + domain_alignment * 0.20
            + lexical_overlap * 0.10
            - off_topic_penalty
        )

    def _select_chunks_for_generation(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        *,
        compact: bool = False,
    ) -> List[RetrievedChunk]:
        del query  # relevance has already been attached during cross-check
        if not chunks:
            return []

        max_chunks = max(1, min(2, self.max_context_chunks)) if compact else self.max_context_chunks
        max_total_chars = self.compact_context_chars if compact else self.max_total_context_chars
        per_chunk_chars = max(500, min(self.max_chunk_chars, max_total_chars)) if compact else self.max_chunk_chars

        ranked = sorted(chunks, key=self._score_chunk_for_generation, reverse=True)
        selected: List[RetrievedChunk] = []
        seen_docs: set[str] = set()
        total_chars = 0

        # First pass prefers source diversity so we don't waste the prompt budget on near-duplicates.
        for chunk in ranked:
            doc_key = str(chunk.document_id)
            if doc_key in seen_docs:
                continue
            prepared = self._prepare_chunk_for_context(chunk, per_chunk_chars, max_total_chars - total_chars)
            if prepared is None:
                continue
            selected.append(prepared)
            seen_docs.add(doc_key)
            total_chars += len(prepared.text)
            if len(selected) >= max_chunks or total_chars >= max_total_chars:
                break

        # Second pass fills remaining budget with the strongest leftovers.
        if len(selected) < max_chunks and total_chars < max_total_chars:
            selected_keys = {str(item.chunk_id) for item in selected}
            for chunk in ranked:
                if str(chunk.chunk_id) in selected_keys:
                    continue
                prepared = self._prepare_chunk_for_context(chunk, per_chunk_chars, max_total_chars - total_chars)
                if prepared is None:
                    continue
                selected.append(prepared)
                total_chars += len(prepared.text)
                if len(selected) >= max_chunks or total_chars >= max_total_chars:
                    break

        return selected

    def _prepare_chunk_for_context(
        self,
        chunk: RetrievedChunk,
        per_chunk_chars: int,
        remaining_chars: int,
    ) -> Optional[RetrievedChunk]:
        if remaining_chars <= 180:
            return None

        text = (chunk.text or "").strip()
        if not text:
            return None

        char_budget = min(per_chunk_chars, remaining_chars)
        if len(text) > char_budget:
            text = text[: max(160, char_budget - 4)].rstrip() + " ..."

        context_before = (chunk.context_before or "")[:120].strip() or None
        context_after = (chunk.context_after or "")[:120].strip() or None
        total_payload_chars = len(text) + len(context_before or "") + len(context_after or "")
        if total_payload_chars > remaining_chars:
            overflow = total_payload_chars - remaining_chars
            if overflow > 0 and len(text) > 180:
                text = text[: max(160, len(text) - overflow - 4)].rstrip() + " ..."

        return RetrievedChunk(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            similarity=chunk.similarity,
            text=text,
            source_type=chunk.source_type,
            chunk_index=chunk.chunk_index,
            document_title=chunk.document_title,
            token_count=chunk.token_count,
            context_before=context_before,
            context_after=context_after,
            metadata=dict(chunk.metadata or {}),
        )

    def _build_context(self, chunks: List[RetrievedChunk], include_citations: bool) -> str:
        context_parts: List[str] = []
        for i, chunk in enumerate(chunks, 1):
            doc_ref = f"[Document {i}: {chunk.document_title}]"
            metadata_str = f" (Relevance: {chunk.similarity:.1%})" if include_citations else ""
            chunk_text = chunk.text
            if chunk.context_before:
                chunk_text = f"...{chunk.context_before}\n\n{chunk_text}"
            if chunk.context_after:
                chunk_text = f"{chunk_text}\n\n{chunk.context_after}..."
            context_parts.append(f"{doc_ref}{metadata_str}\n{chunk_text}\n")
        return "\n".join(context_parts)

    def _build_system_prompt(self, citation_style: str) -> str:
        return (
            "You are an enterprise knowledge assistant. Your role is to provide accurate, well-sourced answers using ONLY the provided documents.\n\n"
            "Keep the answer lean. Prefer 2 to 5 sentences unless the context truly requires more.\n\n"
            "CRITICAL RULES:\n"
            "1. Answer ONLY using information explicitly present in the provided context\n"
            "2. Do NOT invent page numbers, sections, or document structure\n"
            "3. If asked about something not in the context, say: 'I cannot find this information in the provided documents'\n"
            "4. Never speculate or use outside knowledge\n"
            "5. Reference sources by document title only"
        )

    def _build_user_prompt(self, query: str, context: str, chunks: List[RetrievedChunk]) -> str:
        return f"""QUESTION: {query}

AVAILABLE SOURCE DOCUMENTS:
{self._build_source_list(chunks)}

CONTEXT (use ONLY this to answer):
{context}

INSTRUCTIONS:
1. Answer ONLY using the context above
2. Reference source document titles naturally when relevant
3. If the context does not contain enough information, say: \"I cannot find this information in the provided documents\"
4. Keep the answer concise and factual

ANSWER:"""

    def _build_source_list(self, chunks: List[RetrievedChunk]) -> str:
        sources: List[str] = []
        seen_docs: set[str] = set()
        for chunk in chunks:
            if chunk.document_id in seen_docs:
                continue
            seen_docs.add(chunk.document_id)
            sources.append(f"- {chunk.document_title}")
        return "\n".join(sources)

    def _extract_citations(self, chunks: List[RetrievedChunk], answer_text: str) -> List[Citation]:
        citations: List[Citation] = []
        cited_docs: set[str] = set()

        for chunk in chunks:
            if chunk.document_title and chunk.document_title.lower() in answer_text.lower():
                cited_docs.add(chunk.document_id)

        for chunk in chunks:
            if chunk.document_id in cited_docs:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_title=chunk.document_title,
                        chunk_index=chunk.chunk_index,
                        similarity=chunk.similarity,
                        text_snippet=chunk.text[:200],
                    )
                )

        if not citations:
            for chunk in chunks[:3]:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_title=chunk.document_title,
                        chunk_index=chunk.chunk_index,
                        similarity=chunk.similarity,
                        text_snippet=chunk.text[:200],
                    )
                )
        return citations

    def _calculate_cross_doc_agreement(
        self,
        chunks: List[RetrievedChunk],
        quality_check: Optional[RetrievalCrossCheck] = None,
    ) -> float:
        if not chunks or len(chunks) < 2:
            return 1.0
        if not quality_check or not quality_check.has_conflicts:
            return 1.0

        n = len(chunks)
        max_possible = n * (n - 1) / 2
        if max_possible == 0:
            return 1.0
        conflict_count = len(quality_check.conflict_details)
        return max(0.0, min(1.0, 1.0 - conflict_count / max_possible))

    def _calculate_confidence_step5(
        self,
        chunks: List[RetrievedChunk],
        quality_check: Optional[RetrievalCrossCheck],
        top_k: int = 10,
        cross_doc_agreement_score: float = 1.0,
    ) -> float:
        if not chunks:
            return 0.0

        average_similarity = sum(c.similarity for c in chunks) / len(chunks)
        unique_docs = len(set(c.document_id for c in chunks))
        source_diversity = min(1.0, unique_docs / max(min(top_k, 5), 1))
        avg_evidence = sum(float((c.metadata or {}).get("generator_evidence_score", 0.0)) for c in chunks) / len(chunks)
        avg_domain_alignment = sum(float((c.metadata or {}).get("generator_domain_alignment", 0.0)) for c in chunks) / len(chunks)

        confidence = (
            average_similarity * 0.45
            + source_diversity * 0.15
            + cross_doc_agreement_score * 0.15
            + avg_evidence * 0.15
            + avg_domain_alignment * 0.10
        ) * 100

        if quality_check and quality_check.fallback_used:
            confidence *= 0.88
        if avg_evidence < 0.20:
            confidence *= 0.55
        if avg_domain_alignment < 0.15:
            confidence *= 0.60

        confidence = max(0.0, min(100.0, confidence))
        return round(confidence, 1)

    def _build_no_answer(
        self,
        reason: str,
        query: str,
        generation_time_ms: float,
        quality_check: Optional[RetrievalCrossCheck],
    ) -> GeneratedAnswer:
        metadata = {
            "reason": reason,
            "query_length": len(query),
            "chunks_used": 0,
            "fallback_used": bool(quality_check.fallback_used) if quality_check else False,
            "fallback_reason": quality_check.fallback_reason if quality_check else None,
        }
        return GeneratedAnswer(
            answer_text="I couldn't find enough reliable information in your documents to answer this question.",
            citations=[],
            confidence_score=0.0,
            model_used="none",
            tokens_used=0,
            generation_time_ms=round(generation_time_ms, 2),
            average_similarity=0.0,
            unique_documents=0,
            metadata=metadata,
            quality_check=quality_check,
            cross_doc_agreement_score=0.0,
            top_k=0,
        )

    def _query_terms(self, text: str) -> List[str]:
        return [
            token for token in re.findall(r"[a-zA-Z0-9_:-]+", (text or "").lower())
            if len(token) > 2
        ]

    def _looks_like_wrong_domain(self, query: str, chunks: List[RetrievedChunk]) -> bool:
        joined = " ".join((c.text or "") for c in chunks).lower()
        dialogue_markers = joined.count('"') + joined.count("“") + joined.count("”")
        prose_hits = sum(
            1 for term in (" he ", " she ", " looked ", " walked ", " lunch ", " said ")
            if term in f" {joined} "
        )
        query_terms = set(self._query_terms(query))
        technical_query = len(query_terms & self.TECHNICAL_TERMS) >= 2
        return technical_query and (dialogue_markers >= 2 or prose_hits >= 2)

    def _detect_conflicts(self, chunks: List[RetrievedChunk]) -> Tuple[List[Dict[str, Any]], bool]:
        conflicts: List[Dict[str, Any]] = []
        contradiction_pairs = [
            ("not", "is"),
            ("cannot", "can"),
            ("impossible", "possible"),
            ("false", "true"),
            ("no", "yes"),
            ("disabled", "enabled"),
            ("off", "on"),
        ]

        for i, chunk1 in enumerate(chunks):
            for chunk2 in chunks[i + 1:]:
                if chunk1.document_id == chunk2.document_id:
                    continue
                text1_lower = (chunk1.text or "").lower()
                text2_lower = (chunk2.text or "").lower()
                for neg_term, pos_term in contradiction_pairs:
                    if (neg_term in text1_lower and pos_term not in text1_lower) and (
                        pos_term in text2_lower and neg_term not in text2_lower
                    ):
                        conflict = {
                            "chunk_ids": [chunk1.chunk_id, chunk2.chunk_id],
                            "document_ids": [chunk1.document_id, chunk2.document_id],
                            "documents": f"{chunk1.document_title} vs {chunk2.document_title}",
                            "type": "contradiction",
                            "description": (
                                f"Potential contradiction: '{chunk1.document_title}' contains '{neg_term}' "
                                f"while '{chunk2.document_title}' contains '{pos_term}'"
                            ),
                            "severity": "high",
                            "chunk1_snippet": (chunk1.text or "")[:100],
                            "chunk2_snippet": (chunk2.text or "")[:100],
                        }
                        if conflict not in conflicts:
                            conflicts.append(conflict)
        return conflicts, bool(conflicts)


_generator: Optional[AnswerGenerator] = None


def get_answer_generator(
    model: Optional[str] = None,
    openai_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    ollama_base_url: Optional[str] = None,
    similarity_threshold: float = 0.5,
    min_unique_documents: int = 1,
    detect_conflicts: bool = True,
    ollama_timeout: Optional[int] = None,
) -> AnswerGenerator:
    global _generator
    if _generator is None:
        _generator = AnswerGenerator(
            model=model,
            openai_model=openai_model,
            openai_api_key=openai_api_key,
            ollama_base_url=ollama_base_url,
            similarity_threshold=similarity_threshold,
            min_unique_documents=min_unique_documents,
            detect_conflicts=detect_conflicts,
            ollama_timeout=ollama_timeout,
        )
    return _generator


def reset_answer_generator() -> None:
    global _generator
    _generator = None
