"""Semantic search orchestration with PostgreSQL-hybrid primary and retriever fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from app.database.models import Chunk, Document, Note, SearchLog, SearchQuery
except ImportError:
    # Some isolated tests stub only the note/search models. Document-chunk
    # hydration is optional and should degrade gracefully in that environment.
    from app.database.models import Note, SearchLog, SearchQuery

    Chunk = None
    Document = None
from app.services.embeddings import get_embedding_service
from app.services.postgresql_search import get_postgresql_search_service
from app.services.rag_retriever import RAGRetriever, RetrievedChunk, get_rag_retriever
from app.services.retrieval_relevance import analyze_chunk_relevance, analyze_query_intent
from app.services.search_highlights import SearchHighlightExtractor, SearchTextChunk, stable_content_fingerprint
try:
    from app.ingestion.source_locations import enrich_citation_metadata, source_location_payload
except Exception:  # pragma: no cover - isolated tests stub the app package
    def enrich_citation_metadata(metadata, *, document_title=None, source_type=None):
        enriched = dict(metadata or {})
        if source_type and not enriched.get("source_type"):
            enriched["source_type"] = source_type
        if document_title and not enriched.get("source_file_name"):
            enriched["source_file_name"] = document_title
        enriched.setdefault("citation_label", str(enriched.get("source_file_name") or document_title or "Untitled Document"))
        enriched.setdefault("source_location", {"citation_label": enriched["citation_label"]})
        return enriched

    def source_location_payload(metadata, *, document_title=None):
        enriched = enrich_citation_metadata(metadata, document_title=document_title)
        return dict(enriched.get("source_location") or {"citation_label": enriched.get("citation_label")})

logger = logging.getLogger(__name__)


class SemanticSearchResult:
    """Semantic search result with composite scoring."""

    def __init__(
        self,
        chunk_id: UUID,
        document_id: UUID,
        document_title: str,
        content: str,
        source_kind: str,
        source_type: str,
        chunk_index: int,
        created_at: datetime,
        interaction_count: int,
        similarity_score: float,
        vector_score: float = 0.0,
        text_score: float = 0.0,
        recency_score: float = 0.0,
        usage_score: float = 0.0,
        final_score: float = 0.0,
        highlight: str = "",
        tags: Optional[List[str]] = None,
        token_count: int = 0,
        context_before: Optional[str] = None,
        context_after: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        highlights: Optional[List[Dict[str, Any]]] = None,
        matched_chunks: Optional[List[Dict[str, Any]]] = None,
        confidence: str = "low",
        confidence_score: float = 0.0,
        match_summary: Optional[Dict[str, Any]] = None,
    ):
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.document_title = document_title
        self.content = content
        self.source_kind = source_kind
        self.source_type = source_type
        self.chunk_index = chunk_index
        self.created_at = created_at
        self.interaction_count = interaction_count
        self.similarity_score = similarity_score
        self.vector_score = vector_score
        self.text_score = text_score
        self.recency_score = recency_score
        self.usage_score = usage_score
        self.final_score = final_score
        self.highlight = highlight
        self.tags = tags or []
        self.token_count = token_count
        self.context_before = context_before
        self.context_after = context_after
        self.metadata = metadata or {}
        self.highlights = highlights or []
        self.matched_chunks = matched_chunks or []
        self.confidence = confidence
        self.confidence_score = confidence_score
        self.match_summary = match_summary or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "content": self.content,
            "source_kind": self.source_kind,
            "source_type": self.source_type,
            "chunk_index": self.chunk_index,
            "created_at": self.created_at.isoformat(),
            "interaction_count": self.interaction_count,
            "similarity_score": round(self.similarity_score, 4),
            "text_score": round(self.text_score, 4),
            "recency_score": round(self.recency_score, 4),
            "usage_score": round(self.usage_score, 4),
            "final_score": round(self.final_score, 4),
            "highlight": self.highlight,
            "highlights": self.highlights,
            "matched_chunks": self.matched_chunks,
            "confidence": self.confidence,
            "confidence_score": round(max(0.0, min(float(self.confidence_score or 0.0), 1.0)), 4),
            "match_summary": self.match_summary,
            "tags": self.tags,
            "token_count": int(self.token_count or 0),
            "context_before": self.context_before,
            "context_after": self.context_after,
            "metadata": self.metadata,
            "citation_label": (self.metadata or {}).get("citation_label"),
            "source_location": source_location_payload(self.metadata or {}, document_title=self.document_title),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SemanticSearchResult":
        created_at_raw = data.get("created_at")
        created_at = datetime.now(timezone.utc)
        if isinstance(created_at_raw, str):
            try:
                created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = datetime.now(timezone.utc)

        return cls(
            chunk_id=UUID(str(data["chunk_id"])),
            document_id=UUID(str(data["document_id"])),
            document_title=data.get("document_title", ""),
            content=data.get("content", ""),
            source_kind=data.get("source_kind", "note"),
            source_type=data.get("source_type", "note"),
            chunk_index=int(data.get("chunk_index", 0)),
            created_at=created_at,
            interaction_count=int(data.get("interaction_count", 0) or 0),
            similarity_score=float(data.get("similarity_score", 0.0) or 0.0),
            vector_score=float(data.get("vector_score", data.get("similarity_score", 0.0)) or 0.0),
            text_score=float(data.get("text_score", 0.0) or 0.0),
            recency_score=float(data.get("recency_score", 0.0) or 0.0),
            usage_score=float(data.get("usage_score", 0.0) or 0.0),
            final_score=float(data.get("final_score", 0.0) or 0.0),
            highlight=data.get("highlight", ""),
            tags=list(data.get("tags", []) or []),
            token_count=int(data.get("token_count", 0) or 0),
            context_before=data.get("context_before"),
            context_after=data.get("context_after"),
            metadata=dict(data.get("metadata", {}) or {}),
            highlights=list(data.get("highlights", []) or []),
            matched_chunks=list(data.get("matched_chunks", []) or []),
            confidence=data.get("confidence", "low"),
            confidence_score=float(data.get("confidence_score", 0.0) or 0.0),
            match_summary=dict(data.get("match_summary", {}) or {}),
        )


class SemanticSearchExecution:
    """Result envelope for one semantic-search execution."""

    def __init__(
        self,
        results: List[SemanticSearchResult],
        search_log_id: Optional[str] = None,
        strategy: str = "unknown",
    ) -> None:
        self.results = results
        self.search_log_id = search_log_id
        self.strategy = strategy


class SemanticSearchService:
    """Semantic search service with a Postgres-first strategy."""

    SIMILARITY_WEIGHT = 0.60
    RECENCY_WEIGHT = 0.25
    USAGE_WEIGHT = 0.15
    RECENCY_HALF_LIFE_DAYS = 28

    def __init__(self, rag_retriever: RAGRetriever, embedding_service) -> None:
        self.retriever = rag_retriever
        self.embedding_service = embedding_service
        self.postgresql_service = get_postgresql_search_service()
        self.highlight_extractor = SearchHighlightExtractor()

    async def search(
        self,
        workspace_id: UUID,
        user_id: UUID,
        query: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None,
        db: Optional[AsyncSession] = None,
        log_execution: bool = True,
        include_postgresql: bool = True,
        include_retriever: bool = True,
    ) -> SemanticSearchExecution:
        """Perform semantic search across notes and document chunks."""
        query = (query or "").strip()
        filters = self._normalize_filters(filters)
        limit = self._normalize_limit(limit)
        if not query:
            return SemanticSearchExecution(results=[], strategy="empty_query")

        logger.info("Starting semantic search: workspace=%s query=%s", workspace_id, query)
        search_started = datetime.now(timezone.utc)
        strategy = "no_results"
        ranked_results: List[SemanticSearchResult] = []
        postgres_results: List[SemanticSearchResult] = []
        fallback_results: List[SemanticSearchResult] = []

        if include_postgresql and db is not None:
            try:
                async with db.begin_nested():
                    postgres_results = await self._search_postgresql(
                        db=db,
                        workspace_id=workspace_id,
                        query=query,
                        limit=limit,
                        filters=filters,
                    )
            except Exception as exc:
                logger.warning(
                    "PostgreSQL hybrid search unavailable for workspace=%s; falling back: %s",
                    workspace_id,
                    exc,
                )

        if include_retriever:
            fallback_results = await self._search_retriever_fallback(
                workspace_id=workspace_id,
                user_id=user_id,
                query=query,
                limit=limit,
                filters=filters,
                db=db,
            )

        if postgres_results and fallback_results:
            ranked_results = self._rerank(self._dedupe_results([*postgres_results, *fallback_results]), query=query)
            strategy = "postgresql_hybrid_plus_retriever"
        elif postgres_results:
            ranked_results = self._rerank(postgres_results, query=query)
            strategy = "postgresql_hybrid"
        elif fallback_results:
            ranked_results = fallback_results
            ranked_results = self._rerank(ranked_results, query=query)
            strategy = "retriever_fallback"

        final_results = self._diversify_results(ranked_results, limit=limit)
        took_ms = int((datetime.now(timezone.utc) - search_started).total_seconds() * 1000)
        search_log_id = None
        if db is not None and log_execution:
            search_log_id = await self.log_search_execution(
                db=db,
                user_id=user_id,
                workspace_id=workspace_id,
                query=query,
                results=final_results,
                search_duration_ms=took_ms,
            )

        return SemanticSearchExecution(
            results=final_results,
            search_log_id=search_log_id,
            strategy=strategy,
        )

    async def _search_postgresql(
        self,
        db: AsyncSession,
        workspace_id: UUID,
        query: str,
        limit: int,
        filters: Dict[str, Any],
    ) -> List[SemanticSearchResult]:
        postgres_results = await self.postgresql_service.hybrid_search(
            db=db,
            query=query,
            workspace_id=workspace_id,
            limit=min(max(limit * 3, 15), 100),
            similarity_threshold=0.0,
        )

        normalized: List[SemanticSearchResult] = []
        for row in postgres_results:
            created_at = self._parse_datetime(row.get("updated_at") or row.get("created_at"))
            similarity_score = max(0.0, min(float(row.get("embedding_similarity", 0.0) or 0.0), 1.0))
            text_score = self._normalize_text_score(row.get("text_score", 0.0))
            usage_score = max(0.0, min(float(row.get("interaction_score", 0.0) or 0.0), 1.0))
            recency_score = self._calculate_recency_score(created_at)
            semantic_score = self._calculate_semantic_score(similarity_score, text_score)

            normalized.append(
                SemanticSearchResult(
                    chunk_id=UUID(str(row["note_id"])),
                    document_id=UUID(str(row["note_id"])),
                    document_title=row.get("title", ""),
                    content=row.get("content", ""),
                    source_kind="note",
                    source_type=row.get("note_type", "note"),
                    chunk_index=0,
                    created_at=created_at,
                    interaction_count=int(round(usage_score * 100)),
                    similarity_score=semantic_score,
                    vector_score=similarity_score,
                    text_score=text_score,
                    recency_score=recency_score,
                    usage_score=usage_score,
                    final_score=self._calculate_final_score(semantic_score, recency_score, usage_score),
                    highlight=row.get("highlight") or self._extract_highlight(row.get("content", ""), query),
                    token_count=int(row.get("token_count", 0) or 0),
                    metadata={
                        "source_kind": "note",
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                        "live_content_fingerprint": stable_content_fingerprint(
                            [str(row.get("title", "")), str(row.get("content", ""))]
                        ),
                    },
                )
            )

        await self._hydrate_result_tags(db, normalized)
        filtered = self._apply_result_filters(normalized, filters)
        refined = await self._refine_note_results_to_chunks(filtered, query=query)
        return self._rerank(self._dedupe_results(refined), query=query)

    async def _search_retriever_fallback(
        self,
        workspace_id: UUID,
        user_id: UUID,
        query: str,
        limit: int,
        filters: Dict[str, Any],
        db: Optional[AsyncSession],
    ) -> List[SemanticSearchResult]:
        rag_result = await asyncio.to_thread(
            self.retriever.retrieve,
            query=query,
            workspace_id=workspace_id,
            top_k=min(limit * 2, 60),
            filters=filters,
        )

        if not rag_result.chunks:
            logger.info("No semantic search results found for query=%s", query)
            return []

        enriched_results = await self._enrich_results(
            rag_result.chunks,
            user_id,
            workspace_id,
            query,
            db,
        )
        filtered = self._apply_result_filters(enriched_results, filters)
        return self._rerank(filtered, query=query)

    async def _enrich_results(
        self,
        chunks: List[RetrievedChunk],
        user_id: UUID,
        workspace_id: UUID,
        query: str,
        db: Optional[AsyncSession],
    ) -> List[SemanticSearchResult]:
        del user_id, workspace_id
        enriched: List[SemanticSearchResult] = []
        hydrated_chunks = await self._hydrate_document_chunks(db, chunks)

        for chunk in hydrated_chunks:
            try:
                similarity_score = max(0.0, min(chunk.similarity, 1.0))
                metadata = dict(chunk.metadata or {})
                metadata.setdefault("source_kind", "document")
                created_at = self._parse_datetime(metadata.get("created_at"))
                highlights, matched_chunks, confidence, confidence_score = self._build_document_evidence(
                    chunk=chunk,
                    query=query,
                    similarity_score=similarity_score,
                    metadata=metadata,
                )

                enriched.append(
                    SemanticSearchResult(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_title=chunk.document_title or metadata.get("document_title", ""),
                        content=chunk.text,
                        source_kind="document",
                        source_type=chunk.source_type,
                        chunk_index=chunk.chunk_index,
                        created_at=created_at,
                        interaction_count=int(metadata.get("interaction_count", 0) or 0),
                        similarity_score=similarity_score,
                        vector_score=similarity_score,
                        text_score=float(metadata.get("lexical_overlap", 0.0) or 0.0),
                        highlight=highlights[0]["text"] if highlights else "",
                        tags=list(metadata.get("tags", []) or []),
                        token_count=int(getattr(chunk, "token_count", 0) or metadata.get("token_count", 0) or 0),
                        context_before=getattr(chunk, "context_before", None) or metadata.get("context_before"),
                        context_after=getattr(chunk, "context_after", None) or metadata.get("context_after"),
                        metadata=metadata,
                        highlights=highlights,
                        matched_chunks=matched_chunks,
                        confidence=confidence,
                        confidence_score=confidence_score,
                        match_summary={
                            "strategy": "document_chunk_vector",
                            "matched_chunk_count": len(matched_chunks),
                            "highlight_count": len(highlights),
                        },
                    )
                )
            except Exception as exc:
                logger.warning("Error enriching fallback result: %s", exc)

        if db is not None:
            await self._hydrate_result_tags(db, enriched)
        return enriched

    async def _hydrate_document_chunks(
        self,
        db: Optional[AsyncSession],
        chunks: List[RetrievedChunk],
    ) -> List[RetrievedChunk]:
        """Hydrate authoritative chunk text/title from SQL when vector payloads are sparse."""
        if db is None or not chunks or Chunk is None or Document is None:
            return chunks

        chunk_ids: List[UUID] = []
        for chunk in chunks:
            try:
                chunk_ids.append(UUID(str(chunk.chunk_id)))
            except (TypeError, ValueError):
                continue

        if not chunk_ids:
            return chunks

        try:
            rows = await db.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(Chunk.id.in_(chunk_ids))
            )
        except Exception as exc:
            logger.warning("Failed to hydrate retriever fallback chunks: %s", exc)
            return chunks

        hydrated_by_id = {
            str(chunk_row.id): (chunk_row, document_row)
            for chunk_row, document_row in rows.all()
        }

        hydrated: List[RetrievedChunk] = []
        for chunk in chunks:
            row = hydrated_by_id.get(str(chunk.chunk_id))
            if row is None:
                hydrated.append(chunk)
                continue

            chunk_row, document_row = row
            merged_metadata = {
                **(chunk_row.chunk_metadata or {}),
                **(chunk.metadata or {}),
            }
            merged_metadata.setdefault("source_kind", "document")
            merged_metadata.setdefault("document_id", str(document_row.id))
            merged_metadata.setdefault("chunk_id", str(chunk_row.id))
            merged_metadata.setdefault("chunk_index", chunk_row.chunk_index)
            merged_metadata.setdefault("document_title", document_row.title or chunk.document_title)
            merged_metadata.setdefault("token_count", chunk_row.token_count or chunk.token_count or 0)
            merged_metadata.setdefault("context_before", chunk_row.context_before or chunk.context_before)
            merged_metadata.setdefault("context_after", chunk_row.context_after or chunk.context_after)
            if getattr(chunk_row, "created_at", None):
                merged_metadata.setdefault("created_at", chunk_row.created_at.isoformat())
            if getattr(document_row, "created_at", None):
                merged_metadata.setdefault("document_created_at", document_row.created_at.isoformat())
            merged_metadata = enrich_citation_metadata(
                merged_metadata,
                document_title=document_row.title or chunk.document_title,
                source_type=getattr(document_row.source_type, "value", str(document_row.source_type)),
            )

            hydrated.append(
                RetrievedChunk(
                    chunk_id=str(chunk_row.id),
                    document_id=str(document_row.id),
                    text=chunk_row.text or chunk.text,
                    similarity=chunk.similarity,
                    chunk_index=chunk_row.chunk_index,
                    source_type=getattr(document_row.source_type, "value", str(document_row.source_type)),
                    metadata=merged_metadata,
                    document_title=document_row.title or chunk.document_title,
                    token_count=chunk_row.token_count or chunk.token_count,
                    context_before=chunk_row.context_before or chunk.context_before,
                    context_after=chunk_row.context_after or chunk.context_after,
                )
            )

        return hydrated

    async def _refine_note_results_to_chunks(
        self,
        results: List[SemanticSearchResult],
        *,
        query: str,
    ) -> List[SemanticSearchResult]:
        """Turn whole-note candidates into chunk-evidenced note results."""
        if not results:
            return results

        note_results = [result for result in results if str(result.source_kind or "").lower() == "note"]
        if not note_results:
            return results

        chunks_by_note: Dict[str, List[SearchTextChunk]] = {}
        all_chunks: List[SearchTextChunk] = []
        max_chunk_embeddings = 80

        for result in note_results:
            chunks = self.highlight_extractor.chunk_note(
                note_id=str(result.document_id),
                title=result.document_title,
                content=result.content,
                tags=result.tags,
            )
            chunks_by_note[str(result.document_id)] = chunks
            remaining = max_chunk_embeddings - len(all_chunks)
            if remaining > 0:
                all_chunks.extend(chunks[:remaining])

        query_embedding = self._safe_embed_query(query)
        chunk_embeddings: Dict[str, List[float]] = {}
        if query_embedding and all_chunks and hasattr(self.embedding_service, "embed_batch"):
            try:
                embeddings = self.embedding_service.embed_batch([chunk.text for chunk in all_chunks])
                chunk_embeddings = {
                    chunk.chunk_id: vector
                    for chunk, vector in zip(all_chunks, embeddings)
                    if vector and len(vector) == len(query_embedding)
                }
            except Exception as exc:
                logger.warning("Chunk embedding refinement skipped: %s", exc)

        refined: List[SemanticSearchResult] = []
        for result in results:
            if str(result.source_kind or "").lower() != "note":
                refined.append(result)
                continue

            chunks = chunks_by_note.get(str(result.document_id), [])
            matched = self.highlight_extractor.score_chunks(
                query=query,
                chunks=chunks,
                query_embedding=query_embedding,
                chunk_embeddings=chunk_embeddings,
                note_semantic_score=result.vector_score or result.similarity_score,
                note_text_score=result.text_score,
                source_type=result.source_type,
                max_chunks=3,
            )
            if not matched:
                fallback_chunk = chunks[0] if chunks else None
                if fallback_chunk is not None:
                    fallback_highlights = self.highlight_extractor.extract_highlights(
                        query=query,
                        chunk=fallback_chunk,
                        source_type=result.source_type,
                        max_highlights=1,
                    )
                    result.highlights = [highlight.to_dict() for highlight in fallback_highlights]
                    result.highlight = result.highlights[0]["text"] if result.highlights else result.highlight
                    result.matched_chunks = []
                    result.confidence = "low"
                    result.confidence_score = min(result.final_score, 0.24)
                    result.final_score = min(result.final_score, 0.24)
                    result.match_summary = {
                        "strategy": "whole_note_candidate_low_evidence",
                        "matched_chunk_count": 0,
                        "highlight_count": len(result.highlights),
                    }
                refined.append(result)
                continue

            best = matched[0]
            highlight_dicts = [
                highlight.to_dict()
                for evidence in matched
                for highlight in evidence.highlights
            ][:4]
            matched_chunk_dicts = [evidence.to_dict() for evidence in matched]
            result.content = best.chunk.text
            result.chunk_index = best.chunk.chunk_index
            result.token_count = max(result.token_count, len(best.chunk.text.split()))
            result.highlight = highlight_dicts[0]["text"] if highlight_dicts else best.chunk.text[:240]
            result.highlights = highlight_dicts
            result.matched_chunks = matched_chunk_dicts
            result.confidence = best.confidence
            result.confidence_score = max(0.0, min(best.score, 1.0))
            result.metadata = {
                **(result.metadata or {}),
                "source_kind": "note",
                "matched_note_chunk_id": best.chunk.chunk_id,
                "source_offset_start": best.chunk.start_offset,
                "source_offset_end": best.chunk.end_offset,
                "heading": best.chunk.heading,
                "live_chunk_fingerprint": stable_content_fingerprint(
                    [str(result.document_id), best.chunk.text, str(best.chunk.start_offset), str(best.chunk.end_offset)]
                ),
            }
            result.match_summary = {
                "strategy": "note_chunk_refinement",
                "matched_chunk_count": len(matched),
                "highlight_count": len(highlight_dicts),
                "best_chunk_score": round(best.score, 4),
                "best_chunk_evidence": round(best.evidence_score, 4),
                "best_chunk_semantic": round(best.semantic_score, 4),
            }
            result.similarity_score = max(result.similarity_score, best.semantic_score * 0.88 + best.evidence_score * 0.12)
            result.final_score = max(
                0.0,
                min(
                    result.final_score * 0.55
                    + best.score * 0.35
                    + min(best.evidence_score, 1.0) * 0.10,
                    1.0,
                ),
            )
            refined.append(result)

        return refined

    def _build_document_evidence(
        self,
        *,
        chunk: RetrievedChunk,
        query: str,
        similarity_score: float,
        metadata: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], str, float]:
        start_offset = int(
            metadata.get("source_offset_start")
            or metadata.get("char_start")
            or metadata.get("start_offset")
            or 0
        )
        text_value = chunk.text or ""
        raw_heading = metadata.get("heading") or metadata.get("section") or metadata.get("heading_path")
        if isinstance(raw_heading, list):
            heading = " / ".join(str(part) for part in raw_heading if str(part).strip()) or None
        elif raw_heading:
            heading = str(raw_heading)
        else:
            heading = None
        search_chunk = SearchTextChunk(
            chunk_id=str(chunk.chunk_id),
            note_id=str(chunk.document_id),
            text=text_value,
            start_offset=start_offset,
            end_offset=start_offset + len(text_value),
            chunk_index=int(chunk.chunk_index or 0),
            heading=heading,
            metadata={
                **(metadata or {}),
                "title": chunk.document_title,
                "source_offset_start": start_offset,
                "source_offset_end": start_offset + len(text_value),
            },
        )
        matched = self.highlight_extractor.score_chunks(
            query=query,
            chunks=[search_chunk],
            note_semantic_score=similarity_score,
            note_text_score=float(metadata.get("lexical_overlap", 0.0) or 0.0),
            source_type=chunk.source_type,
            max_chunks=1,
        )
        if matched:
            evidence = matched[0]
            return (
                [highlight.to_dict() for highlight in evidence.highlights],
                [evidence.to_dict()],
                evidence.confidence,
                max(0.0, min(evidence.score, 1.0)),
            )

        highlights = self.highlight_extractor.extract_highlights(
            query=query,
            chunk=search_chunk,
            source_type=chunk.source_type,
            max_highlights=1,
        )
        confidence_score = min(max(similarity_score, 0.0), 0.24)
        return (
            [highlight.to_dict() for highlight in highlights],
            [],
            "low",
            confidence_score,
        )

    def _safe_embed_query(self, query: str) -> Optional[List[float]]:
        try:
            embedding = self.embedding_service.embed_query(query)
            if embedding:
                return embedding
        except Exception as exc:
            logger.warning("Query embedding unavailable for chunk refinement: %s", exc)
        return None

    def _rerank(self, results: List[SemanticSearchResult], query: str = "") -> List[SemanticSearchResult]:
        intent = analyze_query_intent(query)
        narrow_query = self._is_narrow_query(query)
        filtered_results: List[SemanticSearchResult] = []

        for result in results:
            semantic_score = self._calculate_semantic_score(
                result.vector_score or result.similarity_score,
                result.text_score,
            )
            result.similarity_score = semantic_score
            result.recency_score = self._calculate_recency_score(result.created_at)
            derived_usage_score = min(math.log1p(result.interaction_count) / math.log1p(100), 1.0)
            result.usage_score = max(result.usage_score, derived_usage_score)

            if semantic_score < 0.5:
                result.recency_score *= 0.3

            relevance = analyze_chunk_relevance(
                query,
                result.content,
                title=result.document_title,
                tags=result.tags,
                metadata=None,
                source_type=result.source_type,
            )

            result.final_score = self._calculate_final_score(
                semantic_score,
                result.recency_score,
                result.usage_score,
            )
            result.final_score = max(
                0.0,
                min(
                    result.final_score * 0.72
                    + relevance.evidence_score * 0.20
                    + relevance.domain_alignment * 0.08
                    - (0.35 if relevance.off_topic else 0.0),
                    1.0,
                ),
            )
            if result.confidence == "low" and relevance.evidence_score < 0.16:
                result.final_score *= 0.82

            has_highlight_evidence = self._has_grounded_highlight_evidence(result)
            matched_chunk_count = int((result.match_summary or {}).get("matched_chunk_count", 0) or 0)
            if not has_highlight_evidence:
                result.final_score = min(result.final_score, 0.20)
                result.confidence_score = min(result.confidence_score or result.final_score, 0.20)
                result.confidence = "low"
                if narrow_query or intent.is_domain_specific:
                    continue

            if matched_chunk_count == 0:
                result.final_score = min(result.final_score, 0.28)

            calibrated_confidence = max(0.0, min(result.confidence_score or result.final_score, result.final_score, 1.0))
            if relevance.evidence_score < 0.18:
                calibrated_confidence = min(calibrated_confidence, 0.44)
            if matched_chunk_count == 0:
                calibrated_confidence = min(calibrated_confidence, 0.30)
            result.confidence_score = calibrated_confidence
            result.confidence = (
                "high" if calibrated_confidence >= 0.72
                else "medium" if calibrated_confidence >= 0.46
                else "low"
            )

            if intent.is_domain_specific:
                if relevance.off_topic:
                    continue
                if relevance.evidence_score < 0.12 and result.final_score < 0.45:
                    continue
            if narrow_query and result.confidence == "low" and relevance.evidence_score < 0.18:
                continue
            elif result.final_score < 0.22 and result.confidence == "low":
                continue

            filtered_results.append(result)

        filtered_results.sort(key=lambda item: item.final_score, reverse=True)
        return filtered_results

    def _diversify_results(self, results: List[SemanticSearchResult], limit: int) -> List[SemanticSearchResult]:
        """Prevent one document/note from dominating final results."""
        if not results:
            return []

        per_document_counts: Dict[str, int] = {}
        primary: List[SemanticSearchResult] = []
        overflow: List[SemanticSearchResult] = []
        per_document_cap = 2

        for result in results:
            document_key = f"{result.source_kind}:{result.document_id}"
            count = per_document_counts.get(document_key, 0)
            if count < per_document_cap:
                primary.append(result)
                per_document_counts[document_key] = count + 1
            else:
                overflow.append(result)

        return [*primary, *overflow][:limit]

    def _has_grounded_highlight_evidence(self, result: SemanticSearchResult) -> bool:
        for highlight in result.highlights or []:
            text_value = str(highlight.get("text") or "").strip()
            if not text_value:
                continue
            if float(highlight.get("score") or 0.0) >= 0.12:
                return True
            if highlight.get("matched_terms"):
                return True
        return False

    def _is_narrow_query(self, query: str) -> bool:
        intent = analyze_query_intent(query)
        if intent.quoted_terms:
            return True
        content_terms = [term for term in intent.content_terms if term]
        if len(content_terms) >= 3:
            return True
        technical_terms = {
            "api", "apis", "oauth", "callback", "timeout", "latency", "embedding",
            "embeddings", "qdrant", "postgres", "pgvector", "chunk", "chunks",
            "rerank", "ranking", "typescript", "python", "react", "webhook",
        }
        return len(set(content_terms) & technical_terms) >= 2

    def _calculate_recency_score(self, created_at: datetime) -> float:
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            normalized_created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            normalized_created_at = created_at.astimezone(timezone.utc)
        age_days = max((now - normalized_created_at).days, 0)
        decay_constant = math.log(2) / self.RECENCY_HALF_LIFE_DAYS
        return math.exp(-decay_constant * age_days)

    def _calculate_final_score(
        self,
        similarity_score: float,
        recency_score: float,
        usage_score: float,
    ) -> float:
        return (
            (self.SIMILARITY_WEIGHT * similarity_score)
            + (self.RECENCY_WEIGHT * recency_score)
            + (self.USAGE_WEIGHT * usage_score)
        )

    def _calculate_semantic_score(self, similarity_score: float, text_score: float) -> float:
        """Blend vector and BM25 signals into the semantic component used for reranking."""
        normalized_similarity = max(0.0, min(similarity_score, 1.0))
        normalized_text = self._normalize_text_score(text_score)
        return max(0.0, min((normalized_similarity * 0.7) + (normalized_text * 0.3), 1.0))

    @staticmethod
    def _normalize_text_score(value: Any) -> float:
        try:
            score = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(score, 1.0))

    def _apply_result_filters(
        self,
        results: List[SemanticSearchResult],
        filters: Dict[str, Any],
    ) -> List[SemanticSearchResult]:
        filtered = results

        source_type = filters.get("source_type")
        if source_type:
            filtered = [result for result in filtered if result.source_type == source_type]

        tags = [str(tag).strip().lower() for tag in (filters.get("tags") or []) if str(tag).strip()]
        if tags:
            required_tags = set(tags)
            filtered = [
                result
                for result in filtered
                if required_tags.issubset({str(tag).strip().lower() for tag in (result.tags or [])})
            ]

        date_from = filters.get("date_from")
        if date_from:
            date_from_dt = self._parse_filter_datetime(date_from)
            if date_from_dt is not None:
                filtered = [result for result in filtered if result.created_at >= date_from_dt]
            else:
                logger.warning("Ignoring invalid date_from filter: %s", date_from)

        date_to = filters.get("date_to")
        if date_to:
            date_to_dt = self._parse_filter_datetime(date_to)
            if date_to_dt is not None:
                filtered = [result for result in filtered if result.created_at <= date_to_dt]
            else:
                logger.warning("Ignoring invalid date_to filter: %s", date_to)

        return filtered

    def _extract_highlight(self, content: str, query: str) -> str:
        if not content:
            return ""

        query_terms = [term.lower() for term in query.split() if len(term) > 3]
        if not query_terms:
            return content[:200] + "..." if len(content) > 200 else content

        content_lower = content.lower()
        earliest_match = None
        earliest_pos = len(content)

        for term in query_terms:
            pos = content_lower.find(term)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos
                earliest_match = pos

        if earliest_match is None:
            return content[:200] + "..." if len(content) > 200 else content

        start = max(0, earliest_match - 80)
        end = min(len(content), earliest_match + 200)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(content) else ""
        return prefix + content[start:end] + suffix

    async def _hydrate_result_tags(
        self,
        db: AsyncSession,
        results: List[SemanticSearchResult],
    ) -> None:
        if not results:
            return

        note_results = [result for result in results if str(result.source_kind or "").lower() == "note"]
        if not note_results:
            return

        note_ids = list({result.document_id for result in note_results})
        try:
            rows = await db.execute(
                select(Note.id, Note.tags).where(Note.id.in_(note_ids))
            )
        except Exception as exc:
            logger.warning("Failed to hydrate semantic-search tags: %s", exc)
            return

        tags_by_note_id = {
            str(row[0]): list(row[1] or [])
            for row in rows.fetchall()
        }
        for result in note_results:
            result.tags = tags_by_note_id.get(str(result.document_id), result.tags or [])

    @staticmethod
    def _dedupe_results(results: List[SemanticSearchResult]) -> List[SemanticSearchResult]:
        seen: set[tuple[str, str]] = set()
        deduped: List[SemanticSearchResult] = []
        for result in results:
            key = (str(result.chunk_id), str(result.document_id))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        return max(1, min(int(limit or 10), 50))

    @staticmethod
    def _normalize_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        normalized = dict(filters or {})
        tags = normalized.get("tags")
        if tags is None:
            return normalized
        normalized["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
        return normalized

    @staticmethod
    def _parse_filter_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def log_search_execution(
        self,
        db: AsyncSession,
        user_id: UUID,
        workspace_id: UUID,
        query: str,
        results: List[SemanticSearchResult],
        search_duration_ms: Optional[int] = None,
    ) -> Optional[str]:
        try:
            async with db.begin_nested():
                query_embedding = None
                try:
                    query_embedding = self.embedding_service.embed_query(query)
                except Exception as exc:
                    logger.warning("Failed to generate query embedding for analytics: %s", exc)

                search_query = SearchQuery(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    query_text=query,
                    query_embedding=json.dumps(query_embedding) if query_embedding else None,
                )
                db.add(search_query)
                await db.flush()

                if query_embedding:
                    await self._sync_query_embedding_vector(db, search_query.id, query_embedding)

                search_log = SearchLog(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    query_text=query,
                    result_chunk_ids=[str(result.chunk_id) for result in results],
                    result_count=len(results),
                    clicked_count=0,
                    search_duration_ms=search_duration_ms,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(search_log)
                await db.flush()
                return str(search_log.id)
        except Exception as exc:
            logger.warning("Failed to persist semantic-search analytics: %s", exc)
            return None

    async def _sync_query_embedding_vector(
        self,
        db: AsyncSession,
        query_id: UUID,
        query_embedding: List[float],
    ) -> None:
        try:
            dim = len(query_embedding)
            async with db.begin_nested():
                await db.execute(
                    text(
                        f"""
                        UPDATE search_queries
                        SET query_embedding_vector = CAST(:embedding AS vector({dim}))
                        WHERE id = :query_id
                        """
                    ),
                    {
                        "embedding": self._embedding_to_pg(query_embedding),
                        "query_id": query_id,
                    },
                )
        except Exception as exc:
            logger.warning("Query embedding vector sync skipped: %s", exc)

    @staticmethod
    def _embedding_to_pg(vec: List[float]) -> str:
        return f"[{','.join(map(str, vec))}]"

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)


def get_semantic_search_service(db: Optional[AsyncSession] = None) -> SemanticSearchService:
    """Create and return a configured SemanticSearchService."""
    del db
    retriever = get_rag_retriever()
    embedding_service = get_embedding_service()
    return SemanticSearchService(retriever, embedding_service)
