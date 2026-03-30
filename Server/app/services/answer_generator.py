"""Answer generation service with LLM integration for STEP 4."""

import logging
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime

import openai

from app.config import settings
from app.services.retrieval_cross_checker import (
    RetrievalCrossChecker,
    RetrievalValidation
)

logger = logging.getLogger(__name__)


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
class ChunkQualityInssue:
    """Quality issue detected in retrieved chunk."""
    chunk_id: str
    issue_type: str  # "low_similarity", "conflict", "duplication"
    severity: str    # "low", "medium", "high"
    message: str
    affected_document: str


@dataclass
class RetrievalCrossCheck:
    """Cross-check results for retrieved chunks."""
    filtered_chunks: List[RetrievedChunk]  # Chunks after filtering
    quality_issues: List[ChunkQualityInssue]
    diversity_score: float                  # 0-1, based on unique documents
    has_conflicts: bool                     # Any contradictions detected
    conflict_details: List[Dict[str, Any]]  # Details of conflicts
    high_quality_chunks: int                # Chunks above threshold
    low_quality_chunks: int                 # Chunks below threshold


@dataclass
class GeneratedAnswer:
    """Generated answer with citations and metadata."""
    answer_text: str
    citations: List[Citation]
    confidence_score: float  # 0-100% percentage
    model_used: str
    tokens_used: int
    generation_time_ms: float
    average_similarity: float
    unique_documents: int
    metadata: Dict[str, Any]
    validation: Optional['RetrievalValidation'] = None  # Cross-check results
    quality_check: Optional['RetrievalCrossCheck'] = None
    cross_doc_agreement_score: float = 0.0  # 0-1 fraction of non-contradictory chunks
    top_k: int = 10  # Top-K value used for retrieval


class AnswerGenerator:
    """
    Generates answers from retrieved chunks using LLM integration.
    
    Pipeline:
    1. Accept retrieved chunks from STEP 3 (TopKRetriever)
    2. Build prompt with context and instructions
    3. Call LLM (OpenAI GPT-4)
    4. Extract citations from answer
    5. Calculate confidence score
    6. Return structured answer
    """
    
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        max_tokens: int = 1500,
        openai_api_key: Optional[str] = None,
        similarity_threshold: float = 0.5,
        min_unique_documents: int = 1,
        detect_conflicts: bool = True
    ):
        """
        Initialize answer generator.
        
        Args:
            model: LLM model name (e.g., "gpt-4o-mini", "gpt-4")
            temperature: LLM temperature (0.0-1.0, lower = more deterministic)
            max_tokens: Maximum tokens in response
            openai_api_key: OpenAI API key (uses settings if not provided)
            similarity_threshold: Minimum similarity score to use chunk (0-1)
            min_unique_documents: Minimum unique documents for confidence
            detect_conflicts: Whether to detect conflicting chunks
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_key = openai_api_key or settings.OPENAI_API_KEY
        self.similarity_threshold = similarity_threshold
        self.min_unique_documents = min_unique_documents
        self.detect_conflicts = detect_conflicts
        
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
        self.client = openai.OpenAI(api_key=self.api_key)
        
        # Initialize cross-checker for quality validation
        self.cross_checker = RetrievalCrossChecker(
            similarity_threshold=similarity_threshold,
            min_diversity_documents=min_unique_documents,
            conflict_detection_enabled=detect_conflicts
        )
    
    def generate(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        include_citations: bool = True,
        citation_style: str = "inline",
        top_k: int = 10
    ) -> GeneratedAnswer:
        """
        Generate answer from query and retrieved chunks.
        
        STEP 5 Confidence Scoring Formula:
        confidence = (
            average_similarity * 0.6 +           # mean cosine similarity
            min(1.0, source_count / K) * 0.3 +   # unique docs normalized by top-k
            cross_doc_agreement_score * 0.1      # fraction of non-contradictory chunks
        )
        Result is scaled to 0-100%.
        
        Includes cross-checking:
        - Filters chunks by similarity threshold
        - Analyzes diversity (unique documents)
        - Detects conflicting information
        - Adjusts confidence based on quality checks
        
        Args:
            query: User query
            chunks: Retrieved chunks from STEP 3
            include_citations: Whether to include citations
            citation_style: "inline" or "footnote"
            top_k: Top-K value used for retrieval (default 10)
            
        Returns:
            GeneratedAnswer with answer text, citations, and metadata
            
        Raises:
            ValueError: If no chunks provided
            openai.APIError: If LLM call fails
        """
        if not chunks:
            raise ValueError("No chunks provided for answer generation")
        
        start_time = time.time()
        
        # STEP 1: Cross-check retrieved chunks
        quality_check = self._perform_cross_check(chunks)
        
        # Use filtered chunks for answer generation
        filtered_chunks = quality_check.filtered_chunks
        
        if not filtered_chunks:
            logger.warning(f"All chunks filtered out for query '{query[:50]}...' due to low similarity")
            raise ValueError(f"No chunks passed quality threshold ({self.similarity_threshold})")
        
        # Build context from filtered chunks
        context = self._build_context(filtered_chunks, include_citations)
        
        # Build prompts
        system_prompt = self._build_system_prompt(citation_style)
        user_prompt = self._build_user_prompt(query, context, filtered_chunks)
        
        # Call LLM
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            
            answer_text = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            
            logger.info(f"Generated answer for query '{query[:50]}...' ({tokens_used} tokens)")
            
        except openai.APIError as e:
            logger.error(f"LLM API error: {str(e)}")
            raise
        
        # Extract citations
        citations = self._extract_citations(filtered_chunks, answer_text) if include_citations else []
        
        # Calculate cross-document agreement score
        cross_doc_agreement_score = self._calculate_cross_doc_agreement(filtered_chunks, quality_check)
        
        # Calculate STEP 5 confidence score (0-100%)
        confidence = self._calculate_confidence_step5(
            filtered_chunks,
            quality_check,
            top_k,
            cross_doc_agreement_score
        )
        
        # Calculate generation time
        generation_time_ms = (time.time() - start_time) * 1000
        
        # Calculate statistics
        average_similarity = sum(c.similarity for c in filtered_chunks) / len(filtered_chunks) if filtered_chunks else 0.0
        unique_documents = len(set(c.document_id for c in filtered_chunks))
        
        # Build metadata
        metadata = {
            "query_length": len(query),
            "chunks_used": len(filtered_chunks),
            "chunks_filtered": len(chunks) - len(filtered_chunks),
            "unique_documents": unique_documents,
            "average_similarity": round(average_similarity, 3),
            "max_similarity": max(c.similarity for c in filtered_chunks) if filtered_chunks else 0.0,
            "min_similarity": min(c.similarity for c in filtered_chunks) if filtered_chunks else 0.0,
            "response_length": len(answer_text),
            "citations_count": len(citations),
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "quality_issues_found": len(quality_check.quality_issues),
            "has_conflicts": quality_check.has_conflicts,
            "diversity_score": round(quality_check.diversity_score, 3),
            "high_quality_chunks": quality_check.high_quality_chunks,
            "low_quality_chunks": quality_check.low_quality_chunks,
            "cross_doc_agreement_score": round(cross_doc_agreement_score, 3),
            "top_k_used": top_k
        }
        
        return GeneratedAnswer(
            answer_text=answer_text,
            citations=citations,
            confidence_score=confidence,
            model_used=self.model,
            tokens_used=tokens_used,
            generation_time_ms=generation_time_ms,
            average_similarity=round(average_similarity, 3),
            unique_documents=unique_documents,
            metadata=metadata,
            quality_check=quality_check,
            cross_doc_agreement_score=round(cross_doc_agreement_score, 3),
            top_k=top_k
        )
    
    
    def _perform_cross_check(self, chunks: List[RetrievedChunk]) -> RetrievalCrossCheck:
        """
        Perform comprehensive quality checks on retrieved chunks.
        
        Checks:
        1. Similarity threshold filtering
        2. Document diversity analysis
        3. Conflict detection
        4. Quality scoring
        
        Returns:
            RetrievalCrossCheck with results and filtered chunks
        """
        quality_issues: List[ChunkQualityInssue] = []
        filtered_chunks: List[RetrievedChunk] = []
        
        # STEP 1: Filter by similarity threshold
        for chunk in chunks:
            if chunk.similarity < self.similarity_threshold:
                quality_issues.append(
                    ChunkQualityInssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="low_similarity",
                        severity="medium",
                        message=f"Chunk similarity {chunk.similarity:.3f} below threshold {self.similarity_threshold}",
                        affected_document=chunk.document_title
                    )
                )
            else:
                filtered_chunks.append(chunk)
        
        high_quality_chunks = len(filtered_chunks)
        low_quality_chunks = len(chunks) - high_quality_chunks
        
        # STEP 2: Analyze diversity
        unique_doc_ids = set(c.document_id for c in filtered_chunks)
        diversity_score = min(len(unique_doc_ids) / 3.0, 1.0)  # Max at 3 documents
        
        # STEP 3: Detect conflicts
        conflicts: List[Dict[str, Any]] = []
        has_conflicts = False
        
        if self.detect_conflicts and len(filtered_chunks) > 1:
            conflicts, has_conflicts = self._detect_conflicts(filtered_chunks)
            
            if has_conflicts:
                logger.warning(f"Detected {len(conflicts)} conflicting chunks")
                for conflict in conflicts:
                    quality_issues.append(
                        ChunkQualityInssue(
                            chunk_id=conflict["chunk_ids"][0],
                            issue_type="conflict",
                            severity="high",
                            message=conflict["description"],
                            affected_document=conflict["documents"]
                        )
                    )
        
        logger.info(
            f"Cross-check complete: {high_quality_chunks} high-quality, "
            f"{low_quality_chunks} filtered, "
            f"{len(unique_doc_ids)} unique documents, "
            f"conflicts: {has_conflicts}"
        )
        
        return RetrievalCrossCheck(
            filtered_chunks=filtered_chunks,
            quality_issues=quality_issues,
            diversity_score=diversity_score,
            has_conflicts=has_conflicts,
            conflict_details=conflicts,
            high_quality_chunks=high_quality_chunks,
            low_quality_chunks=low_quality_chunks
        )
    
    def _filter_by_similarity_threshold(
        self,
        chunks: List[RetrievedChunk],
        threshold: Optional[float] = None
    ) -> tuple[List[RetrievedChunk], List[RetrievedChunk]]:
        """
        Filter chunks by similarity threshold.
        
        Args:
            chunks: Chunks to filter
            threshold: Similarity threshold (uses self.similarity_threshold if None)
            
        Returns:
            Tuple of (passing_chunks, filtered_chunks)
        """
        threshold = threshold or self.similarity_threshold
        
        passing = [c for c in chunks if c.similarity >= threshold]
        filtered = [c for c in chunks if c.similarity < threshold]
        
        return passing, filtered
    
    def _analyze_diversity(self, chunks: List[RetrievedChunk]) -> Dict[str, Any]:
        """
        Analyze source diversity of chunks.
        
        Returns:
            Diversity metrics
        """
        if not chunks:
            return {
                "unique_documents": 0,
                "diversity_score": 0.0,
                "document_distribution": {},
                "meets_minimum": False
            }
        
        unique_doc_ids = set(c.document_id for c in chunks)
        doc_distribution = {}
        
        for chunk in chunks:
            doc_id = chunk.document_id
            if doc_id not in doc_distribution:
                doc_distribution[doc_id] = {
                    "title": chunk.document_title,
                    "count": 0,
                    "avg_similarity": 0.0,
                    "similarities": []
                }
            doc_distribution[doc_id]["count"] += 1
            doc_distribution[doc_id]["similarities"].append(chunk.similarity)
        
        # Calculate average similarity per document
        for doc_id, info in doc_distribution.items():
            info["avg_similarity"] = sum(info["similarities"]) / len(info["similarities"])
        
        # Diversity score (normalized to 3 documents)
        diversity_score = min(len(unique_doc_ids) / 3.0, 1.0)
        meets_minimum = len(unique_doc_ids) >= self.min_unique_documents
        
        return {
            "unique_documents": len(unique_doc_ids),
            "diversity_score": round(diversity_score, 3),
            "document_distribution": doc_distribution,
            "meets_minimum": meets_minimum
        }
    
    def _detect_conflicts(
        self,
        chunks: List[RetrievedChunk]
    ) -> tuple[List[Dict[str, Any]], bool]:
        """
        Detect conflicting or contradictory information in chunks.
        
        Simple conflict detection based on:
        - Opposite terms (not, no vs yes)
        - Numerical contradictions
        - Topic/source mismatch
        
        Returns:
            Tuple of (conflict_list, has_conflicts)
        """
        conflicts: List[Dict[str, Any]] = []
        
        # Common contradiction keywords
        contradiction_pairs = [
            ("not", "is"),
            ("cannot", "can"),
            ("impossible", "possible"),
            ("false", "true"),
            ("no", "yes"),
            ("disabled", "enabled"),
            ("off", "on")
        ]
        
        # Check each pair of chunks for contradictions
        for i, chunk1 in enumerate(chunks):
            for chunk2 in chunks[i+1:]:
                # Skip if from same document (likely complementary)
                if chunk1.document_id == chunk2.document_id:
                    continue
                
                text1_lower = chunk1.text.lower()
                text2_lower = chunk2.text.lower()
                
                # Simple contradiction detection
                for neg_term, pos_term in contradiction_pairs:
                    has_neg_in_1 = neg_term in text1_lower
                    has_pos_in_1 = pos_term in text1_lower
                    has_neg_in_2 = neg_term in text2_lower
                    has_pos_in_2 = pos_term in text2_lower
                    
                    # Detect patterns like "not ... is" vs "is ..."
                    if (has_neg_in_1 and not has_neg_in_2) and \
                       (has_pos_in_2 and not has_pos_in_1):
                        
                        # Extract relevant text chunks
                        doc1_title = chunk1.document_title
                        doc2_title = chunk2.document_title
                        
                        conflict = {
                            "chunk_ids": [chunk1.chunk_id, chunk2.chunk_id],
                            "document_ids": [chunk1.document_id, chunk2.document_id],
                            "documents": f"{doc1_title} vs {doc2_title}",
                            "type": "contradiction",
                            "description": f"Potential contradiction: '{doc1_title}' contains '{neg_term}' while '{doc2_title}' contains '{pos_term}'",
                            "severity": "high",
                            "chunk1_snippet": chunk1.text[:100],
                            "chunk2_snippet": chunk2.text[:100]
                        }
                        
                        # Avoid duplicates
                        if conflict not in conflicts:
                            conflicts.append(conflict)
        
        has_conflicts = len(conflicts) > 0
        
        if has_conflicts:
            logger.warning(f"Detected {len(conflicts)} potential conflicts in retrieved chunks")
        
        return conflicts, has_conflicts
    

    def _build_context(
        self,
        chunks: List[RetrievedChunk],
        include_citations: bool
    ) -> str:
        """Build context string from chunks."""
        context_parts = []
        
        for i, chunk in enumerate(chunks, 1):
            # Build document reference
            doc_ref = f"[Document {i}: {chunk.document_title}]"
            
            # Add metadata if requested
            if include_citations:
                metadata_str = f" (Relevance: {chunk.similarity:.1%})"
            else:
                metadata_str = ""
            
            # Add chunk text with optional context
            chunk_text = chunk.text
            if chunk.context_before and len(chunk.context_before) > 0:
                chunk_text = f"...{chunk.context_before}\n\n{chunk_text}"
            if chunk.context_after and len(chunk.context_after) > 0:
                chunk_text = f"{chunk_text}\n\n{chunk.context_after}..."
            
            # Combine
            context_parts.append(
                f"{doc_ref}{metadata_str}\n{chunk_text}\n"
            )
        
        return "\n".join(context_parts)
    
    def _build_system_prompt(self, citation_style: str) -> str:
        """
        Build system prompt for LLM (STEP 6).
        
        Enterprise knowledge assistant prompt that emphasizes source attribution
        and accurate knowledge transfer from provided documents.
        """
        return """You are an enterprise knowledge assistant. Your role is to provide accurate, well-sourced answers to questions using provided documents.

CORE PRINCIPLES:
1. Use ONLY information from provided source materials
2. Provide source attribution for every claim
3. If information is insufficient, explicitly state limitations
4. Maintain accuracy and avoid speculation
5. Present information clearly and concisely
6. Highlight multiple sources when available

RESPONSE FORMAT:
- Start with a direct answer to the question
- Include source references throughout (e.g., "DocA section 3.2", "Meeting transcript 12/01")
- For complex topics, organize by source to show multiple perspectives
- End with a summary of key sources

CITATION EXAMPLES:
✓ "According to the API documentation (section 3.2), rate limits are enforced per API key."
✓ "The meeting transcript from 12/01 indicates the team completed Phase 2."
✓ "Multiple sources agree on this point: Design doc (p.5) and meeting notes (12/01)."

SOURCE FORMAT: Use document titles and sections/line numbers when available."""
    
    def _build_user_prompt(
        self,
        query: str,
        context: str,
        chunks: List[RetrievedChunk]
    ) -> str:
        """
        Build user prompt for LLM (STEP 6).
        
        Includes:
        - Source list with document_id + chunk_index + context info
        - User query
        - Retrieved context
        - Instructions for source attribution
        """
        # Build source list with metadata
        source_list = self._build_source_list(chunks)
        
        return f"""You are answering the following question based on the source materials listed below.

QUESTION: {query}

SOURCE MATERIALS:
{source_list}

RETRIEVED CONTEXT:
{context}

INSTRUCTIONS:
1. Use the source materials above to construct your answer
2. Reference sources by title and section information (e.g., "DocA (section 3.2)", "Meeting transcript 12/01")
3. If the answer spans multiple sources, attribute each claim
4. If the sources don't contain sufficient information, state this clearly
5. Organize your answer logically, grouping related information

ANSWER:"""
    
    def _build_source_list(self, chunks: List[RetrievedChunk]) -> str:
        """
        Build formatted source list for user prompt (STEP 6).
        
        Format:
        - DocA (section 3.2) [chunk_index: 5]
        - DocB (line 44) [chunk_index: 12]
        - Meeting transcript 12/01 [chunk_index: 3]
        
        Returns:
            Formatted source list string
        """
        sources = []
        seen_docs = set()
        
        for chunk in chunks:
            doc_key = f"{chunk.document_id}_{chunk.chunk_index}"
            if doc_key in seen_docs:
                continue
            seen_docs.add(doc_key)
            
            # Get source type indicator
            section_info = ""
            if chunk.source_type == "slack":
                section_info = f"(message, {chunk.chunk_index})"
            elif chunk.source_type == "email":
                section_info = f"(email, line {chunk.chunk_index * 10})"
            elif chunk.source_type == "github":
                section_info = f"(code, line {chunk.chunk_index * 50})"
            else:
                # Default: assume section numbering
                section_num = chunk.chunk_index // 2 + 1
                line_num = chunk.chunk_index * 10 + 5
                section_info = f"(section {section_num}, line {line_num})"
            
            source_entry = f"- {chunk.document_title} {section_info} [document_id: {chunk.document_id}, chunk_index: {chunk.chunk_index}]"
            sources.append(source_entry)
        
        return "\n".join(sources)

    
    def _extract_citations(
        self,
        chunks: List[RetrievedChunk],
        answer_text: str
    ) -> List[Citation]:
        """Extract citations from answer text and retrieved chunks.
        
        Creates citations for chunks that are referenced in the answer
        based on document titles and indices.
        """
        citations = []
        
        # Build citation mapping
        cited_docs = set()
        for chunk in chunks:
            # Check if document title appears in answer (simple heuristic)
            if chunk.document_title.lower() in answer_text.lower():
                cited_docs.add(chunk.document_id)
        
        # Create citations
        for chunk in chunks:
            if chunk.document_id in cited_docs:
                citation = Citation(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title,
                    chunk_index=chunk.chunk_index,
                    similarity=chunk.similarity,
                    text_snippet=chunk.text[:200]
                )
                citations.append(citation)
        
        # If no citations found by heuristic, include top chunks
        if not citations and chunks:
            for chunk in chunks[:3]:  # Top 3 chunks
                citation = Citation(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    document_title=chunk.document_title,
                    chunk_index=chunk.chunk_index,
                    similarity=chunk.similarity,
                    text_snippet=chunk.text[:200]
                )
                citations.append(citation)
        
        return citations
    
    def _calculate_cross_doc_agreement(
        self,
        chunks: List[RetrievedChunk],
        quality_check: Optional[RetrievalCrossCheck] = None
    ) -> float:
        """
        Calculate cross-document agreement score.
        
        This is the fraction of chunks that do NOT contradict each other.
        
        Formula:
            If no conflicts: score = 1.0
            If conflicts: score = 1.0 - (conflict_count / max_possible_conflicts)
        
        where max_possible_conflicts = N * (N-1) / 2 for N chunks
        
        Args:
            chunks: Chunks to analyze
            quality_check: Quality check results with conflicts
            
        Returns:
            Agreement score 0-1
        """
        if not chunks or len(chunks) < 2:
            return 1.0  # Single chunk = perfect agreement
        
        if not quality_check or not quality_check.has_conflicts:
            return 1.0  # No conflicts = perfect agreement
        
        # Calculate maximum possible conflicts (all pairs)
        n = len(chunks)
        max_possible_conflicts = n * (n - 1) / 2
        
        # Get actual conflict count
        conflict_count = len(quality_check.conflict_details)
        
        # Calculate agreement score
        if max_possible_conflicts == 0:
            return 1.0
        
        agreement_score = 1.0 - (conflict_count / max_possible_conflicts)
        
        # Ensure within valid range
        agreement_score = max(0.0, min(1.0, agreement_score))
        
        logger.debug(
            f"Cross-doc agreement: {len(chunks)} chunks, {conflict_count} conflicts, "
            f"score: {round(agreement_score, 3)}"
        )
        
        return agreement_score
    
    def _calculate_confidence_step5(
        self,
        chunks: List[RetrievedChunk],
        quality_check: Optional[RetrievalCrossCheck],
        top_k: int = 10,
        cross_doc_agreement_score: float = 1.0
    ) -> float:
        """
        Calculate confidence score using STEP 5 formula (0-100%).
        
        Formula:
            confidence = (
                average_similarity * 0.6 +           # mean cosine similarity (60%)
                min(1.0, source_count / K) * 0.3 +   # unique docs normalized by K (30%)
                cross_doc_agreement_score * 0.1      # fraction of non-contradictory chunks (10%)
            )
        
        Result is scaled to 0-100%.
        
        Args:
            chunks: Filtered chunks used for answer (after similarity threshold filter)
            quality_check: Quality check results
            top_k: Top-K value used for retrieval (e.g., 10, 20)
            cross_doc_agreement_score: Agreement score 0-1
            
        Returns:
            Confidence percentage 0-100
        """
        if not chunks:
            return 0.0
        
        # Factor 1: Average similarity (60% weight)
        similarities = [c.similarity for c in chunks]
        average_similarity = sum(similarities) / len(similarities)
        
        # Factor 2: Source count normalized by K (30% weight)
        unique_docs = len(set(c.document_id for c in chunks))
        source_diversity = min(1.0, unique_docs / top_k)
        
        # Factor 3: Cross-document agreement (10% weight)
        # cross_doc_agreement_score already 0-1
        
        # Calculate confidence (0-1 scale)
        confidence_0_to_1 = (
            average_similarity * 0.6 +
            source_diversity * 0.3 +
            cross_doc_agreement_score * 0.1
        )
        
        # Scale to 0-100
        confidence_percentage = confidence_0_to_1 * 100
        
        # Ensure within valid range
        confidence_percentage = max(0.0, min(100.0, confidence_percentage))
        
        logger.info(
            f"STEP 5 Confidence: {round(confidence_percentage, 1)}% "
            f"(avg_sim={round(average_similarity, 3)}, "
            f"source_div={round(source_diversity, 3)}, "
            f"agreement={round(cross_doc_agreement_score, 3)})"
        )
        
        return round(confidence_percentage, 1)
    

        """
        Calculate confidence score (0-1) for generated answer.
        
        Base Factors (100%):
        - Average similarity score (50% weight)
        - Number of unique documents (25% weight)
        - Maximum similarity (25% weight)
        
        Adjustments:
        - Conflict penalty (-30% if conflicts detected)
        - Low diversity penalty (-10% if single source)
        - Low quality chunk penalty (-5% per filtered chunk, max -20%)
        
        Args:
            chunks: Chunks used for answer (after filtering)
            quality_check: Quality check results (optional)
            
        Returns:
            Confidence score 0-1
        """
        if not chunks:
            return 0.0
        
        similarities = [c.similarity for c in chunks]
        unique_docs = len(set(c.document_id for c in chunks))
        
        # Base confidence (original formula)
        avg_similarity = sum(similarities) / len(similarities)
        doc_diversity = min(unique_docs / 3.0, 1.0)
        max_similarity = max(similarities)
        
        confidence = (
            avg_similarity * 0.5 +
            doc_diversity * 0.25 +
            max_similarity * 0.25
        )
        
        # Adjustments based on quality checks
        if quality_check:
            # ADJUSTMENT 1: Conflict penalty
            if quality_check.has_conflicts:
                conflict_penalty = 0.30 * len(quality_check.conflict_details)
                confidence -= min(conflict_penalty, 0.30)  # Max -30% penalty
                logger.warning(
                    f"Reducing confidence by {round(min(conflict_penalty, 0.30), 3)} "
                    f"due to {len(quality_check.conflict_details)} conflicts"
                )
            
            # ADJUSTMENT 2: Diversity penalty
            if quality_check.diversity_score < 0.5:
                diversity_penalty = 0.10 * (1 - quality_check.diversity_score)
                confidence -= diversity_penalty
                logger.warning(
                    f"Reducing confidence by {round(diversity_penalty, 3)} "
                    f"due to low diversity (single source)"
                )
            
            # ADJUSTMENT 3: Low quality chunks penalty
            if quality_check.low_quality_chunks > 0:
                # Penalize per filtered chunk, max 20%
                filtered_penalty = min(quality_check.low_quality_chunks * 0.05, 0.20)
                confidence -= filtered_penalty
                logger.info(
                    f"Reducing confidence by {round(filtered_penalty, 3)} "
                    f"due to {quality_check.low_quality_chunks} filtered chunks"
                )
        
        # Ensure result is in valid range
        confidence = max(0.0, min(1.0, confidence))
        
        return round(confidence, 3)


# Singleton instance
_generator: Optional[AnswerGenerator] = None


def get_answer_generator(
    model: str = "gpt-4o-mini",
    openai_api_key: Optional[str] = None,
    similarity_threshold: float = 0.5,
    min_unique_documents: int = 1,
    detect_conflicts: bool = True
) -> AnswerGenerator:
    """Get or create answer generator instance.
    
    Args:
        model: LLM model name
        openai_api_key: OpenAI API key
        similarity_threshold: Minimum similarity for chunks
        min_unique_documents: Minimum unique documents required
        detect_conflicts: Whether to detect conflicting chunks
        
    Returns:
        AnswerGenerator instance
    """
    global _generator
    
    if _generator is None:
        _generator = AnswerGenerator(
            model=model,
            openai_api_key=openai_api_key,
            similarity_threshold=similarity_threshold,
            min_unique_documents=min_unique_documents,
            detect_conflicts=detect_conflicts
        )
    
    return _generator


def reset_answer_generator():
    """Reset singleton generator (for testing)."""
    global _generator
    _generator = None
