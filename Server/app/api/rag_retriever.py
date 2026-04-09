"""RAG retrieval service with confidence scoring."""
import logging
import re
from typing import List, Dict, Any, Optional, Set, Union, Tuple
from dataclasses import dataclass
from statistics import mean
from uuid import UUID

logger = logging.getLogger(__name__)

from app.services.embeddings import get_embedding_service
from app.services.vector_db import get_vector_db_client


@dataclass
class RetrievedChunk:
    """Retrieved chunk with metadata."""
    chunk_id: str
    document_id: str
    text: str
    similarity: float
    chunk_index: int
    source_type: str
    metadata: Dict[str, Any]
    document_title: str = ""
    token_count: int = 0
    context_before: Optional[str] = None
    context_after: Optional[str] = None


@dataclass
class RagResult:
    """RAG retrieval result."""
    chunks: List[RetrievedChunk]
    avg_similarity: float
    unique_documents: int
    confidence: float
    query_variants: Optional[List[str]] = None
    discarded_chunks: Optional[List[str]] = None


class RAGRetriever:
    """Retrieval-Augmented Generation retriever.

    This version keeps the anti-false-match guardrails, but adds a graceful
    fallback ladder so the retriever does not "search itself to death" when
    lexical checks are too strict.
    """

    TECHNICAL_TERMS = {
        "api", "apis", "llm", "rag", "vector", "embedding", "embeddings",
        "model", "models", "prompt", "prompts", "timeout", "latency",
        "semantic", "search", "retrieval", "rerank", "reranking", "qdrant",
        "ollama", "phi", "mini", "token", "tokens", "sql", "python",
        "database", "postgres", "pgvector", "chunk", "chunks", "workspace",
    }

    STOPWORDS = {
        "the", "a", "an", "and", "or", "to", "of", "for", "in", "on",
        "at", "with", "from", "by", "is", "are", "was", "were", "be",
        "this", "that", "it", "as", "about", "into", "than", "then",
        "what", "which", "who", "when", "where", "why", "how", "do",
        "does", "did", "can", "could", "should", "would", "will", "i",
        "you", "we", "they", "he", "she", "them", "their", "our", "your",
        "tell", "show", "find", "explain", "give",
    }

    def __init__(
        self,
        similarity_threshold: float = 0.45,
        top_k: int = 10,
        max_documents: int = 5
    ):
        self.embedding_service = get_embedding_service()
        self.vector_db = get_vector_db_client()
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        self.max_documents = max_documents

    def retrieve(
        self,
        query: str,
        workspace_id: Union[str, UUID],
        collection_name: str = None,
        top_k: int = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> RagResult:
        """Retrieve relevant chunks for query."""
        top_k = top_k or self.top_k
        workspace_id_str = str(workspace_id)
        collection_name = collection_name or workspace_id_str

        try:
            query_variants = self._expand_query_variants(query)
            candidate_limit = max(top_k * 3, 12)
            candidates: List[RetrievedChunk] = []
            seen_chunk_ids: Set[str] = set()

            for idx, variant in enumerate(query_variants):
                query_vector = self.embedding_service.embed_query(variant)
                expected_dim = self.embedding_service.get_dimension()
                if len(query_vector) != expected_dim:
                    raise RuntimeError(
                        f"Query embedding dimension mismatch: "
                        f"got {len(query_vector)}D, expected {expected_dim}D."
                    )

                raw_results = self.vector_db.search_similar(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    workspace_id=workspace_id_str,
                    limit=candidate_limit,
                    # Allow more candidates so reranking has material to work with
                    score_threshold=max(self.similarity_threshold - 0.18, 0.18),
                )

                for result in raw_results:
                    payload = result.get("payload", {})
                    chunk_id = str(payload.get("chunk_id", result.get("id", "")))
                    if not chunk_id:
                        continue

                    similarity = float(result.get("similarity", 0.0))
                    if chunk_id in seen_chunk_ids:
                        for existing in candidates:
                            if existing.chunk_id == chunk_id and similarity > existing.similarity:
                                existing.similarity = similarity
                                existing.metadata = dict(existing.metadata or {})
                                existing.metadata["best_variant"] = variant
                                existing.metadata["variant_rank"] = idx
                        continue

                    seen_chunk_ids.add(chunk_id)
                    text = payload.get("text") or payload.get("chunk_text", "")
                    chunk = RetrievedChunk(
                        chunk_id=chunk_id,
                        document_id=str(payload.get("document_id", "unknown")),
                        text=text,
                        similarity=similarity,
                        chunk_index=payload.get("chunk_index", 0),
                        source_type=payload.get("source_type", "unknown"),
                        metadata=dict(payload),
                        document_title=payload.get("document_title", ""),
                        token_count=payload.get("token_count", 0) or 0,
                        context_before=payload.get("context_before"),
                        context_after=payload.get("context_after"),
                    )
                    chunk.metadata["best_variant"] = variant
                    chunk.metadata["variant_rank"] = idx
                    candidates.append(chunk)

            if not candidates:
                return RagResult(
                    chunks=[],
                    avg_similarity=0,
                    unique_documents=0,
                    confidence=0,
                    query_variants=query_variants,
                    discarded_chunks=[],
                )

            reranked_chunks, discarded_chunks = self._rerank_with_fallbacks(
                query=query,
                chunks=candidates,
                top_k=top_k,
            )

            logger.info(
                f"RAG retrieval: query='{query[:50]}' candidates={len(candidates)} "
                f"reranked={len(reranked_chunks)} discarded={len(discarded_chunks)}"
            )

            diverse_chunks = self._apply_diversity_cap(reranked_chunks, top_k=top_k)
            confidence = self._compute_confidence(diverse_chunks, query=query)

            return RagResult(
                chunks=diverse_chunks,
                avg_similarity=mean([c.similarity for c in diverse_chunks]) if diverse_chunks else 0,
                unique_documents=len(set(c.document_id for c in diverse_chunks)),
                confidence=confidence,
                query_variants=query_variants,
                discarded_chunks=discarded_chunks,
            )

        except Exception as e:
            error_msg = str(e)
            if "embedding" in error_msg.lower() or "provider" in error_msg.lower():
                logger.error("Embedding service failed during RAG retrieval: %s", e)
                raise

            logger.warning("Non-embedding error in RAG retrieval: %s", e)
            return RagResult(
                chunks=[],
                avg_similarity=0,
                unique_documents=0,
                confidence=0,
            )

    def _expand_query_variants(self, query: str) -> List[str]:
        """Generate grounded query variants.

        Keep this conservative. Over-expansion is one reason unrelated chunks
        can beat the user's actual intent.
        """
        base = " ".join(query.split())
        if not base:
            return []

        variants = [base]
        lowered = base.lower()
        word_count = len(base.split())
        token_count = len(self._tokenize(base))

        if word_count <= 2 and token_count <= 2 and not any(t in lowered for t in self.TECHNICAL_TERMS):
            variants.extend([
                f"{base} meaning",
                f"{base} topic",
            ])

        if "smaller model" in lowered and word_count <= 6:
            variants.extend([
                base.replace("smaller model", "smaller AI model"),
                base.replace("smaller model", "compact language model"),
            ])

        if word_count <= 4 and "model" in lowered and not any(term in lowered for term in ("ai", "llm", "language model", "embedding")):
            variants.append(f"{base} ai model")

        if word_count <= 6 and any(term in lowered for term in ("timeout", "latency", "slow")):
            variants.append(f"{base} performance")

        deduped: List[str] = []
        seen: Set[str] = set()
        for variant in variants:
            clean = variant.strip()
            if clean and clean not in seen:
                seen.add(clean)
                deduped.append(clean)
        return deduped[:2]

    def _tokenize(self, text: str) -> List[str]:
        return [
            token for token in re.findall(r"[a-zA-Z0-9_:-]+", text.lower())
            if len(token) > 1 and token not in self.STOPWORDS
        ]

    def _query_profile(self, query: str) -> Dict[str, Any]:
        tokens = self._tokenize(query)
        token_set = set(tokens)
        technical_hits = sum(1 for token in token_set if token in self.TECHNICAL_TERMS)
        quoted_terms = re.findall(r'"([^"]+)"', query)
        short_query = len(tokens) <= 2
        return {
            "tokens": tokens,
            "token_set": token_set,
            "technical": technical_hits >= 2,
            "short_query": short_query,
            "quoted_terms": [term.lower() for term in quoted_terms],
        }

    def _chunk_looks_narrative(self, text: str) -> bool:
        lowered = text.lower()
        dialogue_markers = lowered.count('"') + lowered.count("“") + lowered.count("”")
        prose_hits = sum(
            1 for term in (" he ", " she ", " kate ", " andy ", " lunch", " walked", " looked at his watch")
            if term in f" {lowered} "
        )
        return dialogue_markers >= 2 or prose_hits >= 2

    def _lexical_overlap_score(self, profile: Dict[str, Any], chunk_text: str) -> float:
        query_tokens = profile["token_set"]
        if not query_tokens:
            return 0.0

        chunk_tokens = set(self._tokenize(chunk_text))
        overlap = query_tokens & chunk_tokens

        if not overlap and profile["short_query"]:
            lowered = chunk_text.lower()
            for token in query_tokens:
                if token in lowered:
                    overlap.add(token)

        if not overlap:
            return 0.0

        denom = max(min(len(query_tokens), 3), 1)
        return min(len(overlap) / denom, 1.0)

    def _score_chunk(
        self,
        query: str,
        profile: Dict[str, Any],
        chunk: RetrievedChunk,
    ) -> float:
        text = chunk.text or ""
        lowered_text = text.lower()
        lexical = self._lexical_overlap_score(profile, text)

        exact_phrase_bonus = 0.0
        for term in profile["quoted_terms"]:
            if term in lowered_text:
                exact_phrase_bonus += 0.15

        if query.lower() in lowered_text and len(query.split()) <= 4:
            exact_phrase_bonus += 0.08

        if "smaller model" in query.lower() and "smaller model" in lowered_text:
            exact_phrase_bonus += 0.20
        if "ai model" in lowered_text or "language model" in lowered_text or "llm" in lowered_text:
            exact_phrase_bonus += 0.08

        narrative_penalty = 0.0
        if profile["technical"] and self._chunk_looks_narrative(text):
            narrative_penalty = 0.22 if lexical >= 0.10 else 0.35

        semantic_weight = 0.80 if profile["short_query"] else 0.72
        lexical_weight = 1.0 - semantic_weight
        combined = (chunk.similarity * semantic_weight) + (lexical * lexical_weight) + exact_phrase_bonus - narrative_penalty

        chunk.metadata = dict(chunk.metadata or {})
        chunk.metadata["lexical_overlap"] = round(lexical, 4)
        chunk.metadata["rerank_score"] = round(combined, 4)
        chunk.metadata["narrative_penalty"] = round(narrative_penalty, 4)
        return combined

    def _rerank_and_filter_candidates(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        top_k: int,
        mode: str = "strict",
    ) -> Tuple[List[RetrievedChunk], List[str]]:
        if not chunks:
            return [], []

        profile = self._query_profile(query)
        scored: List[Tuple[float, RetrievedChunk]] = []
        discarded: List[str] = []

        for chunk in chunks:
            score = self._score_chunk(query=query, profile=profile, chunk=chunk)
            scored.append((score, chunk))

        scored.sort(key=lambda item: item[0], reverse=True)

        kept: List[RetrievedChunk] = []
        base_threshold = self.similarity_threshold
        lexical_floor = 0.05 if mode == "strict" else 0.0
        min_similarity = (base_threshold - 0.02) if mode == "strict" else max(base_threshold - 0.15, 0.25)

        for score, chunk in scored:
            lexical = float((chunk.metadata or {}).get("lexical_overlap", 0.0))

            if chunk.similarity < min_similarity:
                discarded.append(chunk.chunk_id)
                continue

            if mode == "strict":
                if lexical == 0.0 and not profile["short_query"] and chunk.similarity < 0.65:
                    discarded.append(chunk.chunk_id)
                    continue
                if lexical < lexical_floor and score < (base_threshold - 0.05):
                    discarded.append(chunk.chunk_id)
                    continue
                if profile["technical"] and self._chunk_looks_narrative(chunk.text or "") and lexical < 0.15:
                    discarded.append(chunk.chunk_id)
                    continue
            else:
                # Relaxed mode: be much more lenient
                if profile["technical"] and self._chunk_looks_narrative(chunk.text or "") and chunk.similarity < 0.48:
                    discarded.append(chunk.chunk_id)
                    continue
                # Even zero-lexical chunks are acceptable in relaxed if similarity is reasonable
                if lexical == 0.0 and chunk.similarity < 0.40 and not profile["short_query"]:
                    discarded.append(chunk.chunk_id)
                    continue

            kept.append(chunk)
            if len(kept) >= top_k * 2:
                break

        if kept:
            top = kept[0]
            top_lexical = float((top.metadata or {}).get("lexical_overlap", 0.0))
            if mode == "strict" and top.similarity < max(self.similarity_threshold - 0.03, 0.42) and top_lexical < 0.05:
                return [], [chunk.chunk_id for _, chunk in scored[: min(5, len(scored))]]

        return kept[:top_k], discarded

    def _rerank_with_fallbacks(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        top_k: int,
    ) -> Tuple[List[RetrievedChunk], List[str]]:
        strict_kept, strict_discarded = self._rerank_and_filter_candidates(
            query=query,
            chunks=chunks,
            top_k=top_k,
            mode="strict",
        )
        if strict_kept:
            logger.info(f"RAG strict mode returned {len(strict_kept)} chunks")
            return strict_kept, strict_discarded

        logger.warning(f"RAG strict mode returned 0 chunks, trying relaxed mode")
        relaxed_kept, relaxed_discarded = self._rerank_and_filter_candidates(
            query=query,
            chunks=chunks,
            top_k=top_k,
            mode="relaxed",
        )
        if relaxed_kept:
            logger.info("RAG fallback: relaxed reranking kept %d chunks", len(relaxed_kept))
            return relaxed_kept, strict_discarded + relaxed_discarded

        fallback = sorted(chunks, key=lambda c: c.similarity, reverse=True)
        rescued: List[RetrievedChunk] = []
        profile = self._query_profile(query)
        for chunk in fallback:
            lexical = float((chunk.metadata or {}).get("lexical_overlap", 0.0))
            if profile["technical"] and self._chunk_looks_narrative(chunk.text or "") and chunk.similarity < 0.72:
                continue
            if chunk.similarity < max(self.similarity_threshold - 0.08, 0.35):
                continue
            chunk.metadata = dict(chunk.metadata or {})
            chunk.metadata["fallback_mode"] = "semantic_rescue"
            chunk.metadata["lexical_overlap"] = round(lexical, 4)
            rescued.append(chunk)
            if len(rescued) >= top_k:
                break

        if rescued:
            logger.info("RAG fallback: semantic rescue kept %d chunks", len(rescued))
            return rescued, strict_discarded + relaxed_discarded

        return [], strict_discarded + relaxed_discarded

    def _apply_diversity_cap(self, reranked_chunks: List[RetrievedChunk], top_k: int) -> List[RetrievedChunk]:
        if not reranked_chunks:
            return []

        unique_docs: Set[str] = set()
        diverse_chunks: List[RetrievedChunk] = []
        overflow: List[RetrievedChunk] = []

        for chunk in reranked_chunks:
            if chunk.document_id not in unique_docs and len(unique_docs) < self.max_documents:
                unique_docs.add(chunk.document_id)
                diverse_chunks.append(chunk)
            else:
                overflow.append(chunk)

        for chunk in overflow:
            if len(diverse_chunks) >= top_k:
                break
            diverse_chunks.append(chunk)

        return diverse_chunks[:top_k]

    def _compute_confidence(self, chunks: List[RetrievedChunk], query: str = "") -> float:
        if not chunks:
            return 0.0

        avg_similarity = mean(c.similarity for c in chunks)
        top_similarity = max(c.similarity for c in chunks)
        unique_docs = len(set(c.document_id for c in chunks))
        source_diversity = min(unique_docs / max(self.max_documents, 1), 1.0)

        confidence = (
            min(avg_similarity, 1.0) * 0.58 +
            min(top_similarity, 1.0) * 0.27 +
            source_diversity * 0.15
        )

        if any((c.metadata or {}).get("fallback_mode") == "semantic_rescue" for c in chunks):
            confidence *= 0.82

        if unique_docs == 1 and top_similarity >= 0.62:
            confidence = max(confidence, min(top_similarity * 0.78, 0.68))

        return min(max(confidence, 0.0), 1.0)

    def rank_by_relevance(
        self,
        chunks: List[RetrievedChunk],
        query: str = None
    ) -> List[RetrievedChunk]:
        if query:
            reranked, _ = self._rerank_with_fallbacks(query=query, chunks=chunks, top_k=len(chunks))
            return reranked
        return sorted(chunks, key=lambda c: c.similarity, reverse=True)


def get_rag_retriever(
    similarity_threshold: float = 0.45,
    top_k: int = 10
) -> RAGRetriever:
    """Get RAG retriever instance."""
    return RAGRetriever(
        similarity_threshold=similarity_threshold,
        top_k=top_k
    )
