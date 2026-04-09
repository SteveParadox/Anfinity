"""Retrieval cross-check validator for STEP 4 quality assurance."""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional, Any
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class RetrievalQualityIssue:
    """Quality issue detected during retrieval validation."""
    issue_type: str  # "below_threshold", "conflict", "low_diversity"
    severity: str  # "critical", "warning", "info"
    chunk_id: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DiversityAnalysis:
    """Analysis of source diversity in retrieved chunks."""
    unique_documents: int
    documents: Dict[str, int]  # document_id -> chunk_count
    diversity_score: float  # 0-1 (higher = more diverse)
    is_diverse: bool  # True if >= 2 documents or high diversity_score


@dataclass
class ConflictAnalysis:
    """Analysis of potential conflicts between chunks."""
    has_conflicts: bool
    conflict_count: int
    conflicts: List[Dict[str, Any]]  # Details of each conflict
    conflict_keywords: Set[str]  # Keywords that appear in conflicting chunks


@dataclass
class RetrievalValidation:
    """Complete validation result for retrieved chunks."""
    original_chunk_count: int
    filtered_chunk_count: int
    chunks_filtered: int
    below_threshold_chunks: List[str]  # chunk_ids removed
    
    diversity: DiversityAnalysis
    conflicts: ConflictAnalysis
    quality_issues: List[RetrievalQualityIssue]
    
    confidence_adjustment: float  # Multiplier for original confidence
    adjusted_confidence: float  # confidence * adjustment
    quality_status: str  # "excellent", "good", "fair", "poor"


class RetrievalCrossChecker:
    """
    Cross-checks retrieved chunks for quality assurance.
    
    Validates:
    1. Similarity threshold compliance
    2. Source diversity
    3. Conflict detection
    4. Confidence adjustment
    """
    
    def __init__(
        self,
        similarity_threshold: float = 0.5,
        min_diversity_documents: int = 2,
        conflict_detection_enabled: bool = True,
        max_diversity_penalization: float = 0.95
    ):
        """
        Initialize cross-checker.
        
        Args:
            similarity_threshold: Minimum similarity score (0-1)
            min_diversity_documents: Minimum unique documents for good diversity
            conflict_detection_enabled: Enable conflict detection
            max_diversity_penalization: Minimum confidence if low diversity
        """
        self.similarity_threshold = similarity_threshold
        self.min_diversity_documents = min_diversity_documents
        self.conflict_detection_enabled = conflict_detection_enabled
        self.max_diversity_penalization = max_diversity_penalization
    
    def validate(
        self,
        chunks: List[Any],
        original_confidence: float,
        query: str = ""
    ) -> RetrievalValidation:
        """
        Validate retrieved chunks.
        
        Args:
            chunks: List of RetrievedChunk objects
            original_confidence: Original confidence score
            query: Original query (for conflict detection)
            
        Returns:
            RetrievalValidation with all analysis results
        """
        original_count = len(chunks)
        
        # Step 1: Filter by similarity threshold
        filtered_chunks, filtered_out = self._filter_by_similarity(chunks)
        
        # Step 2: Analyze diversity
        diversity = self._analyze_diversity(filtered_chunks)
        
        # Step 3: Detect conflicts
        conflicts = self._detect_conflicts(filtered_chunks, query) if self.conflict_detection_enabled else ConflictAnalysis(
            has_conflicts=False,
            conflict_count=0,
            conflicts=[],
            conflict_keywords=set()
        )
        
        # Step 4: Generate quality issues
        issues = self._generate_quality_issues(
            filtered_chunks,
            filtered_out,
            diversity,
            conflicts
        )
        
        # Step 5: Adjust confidence
        confidence_adjustment, quality_status = self._adjust_confidence(
            original_confidence,
            diversity,
            conflicts,
            len(filtered_chunks)
        )
        
        adjusted_confidence = original_confidence * confidence_adjustment
        
        validation = RetrievalValidation(
            original_chunk_count=original_count,
            filtered_chunk_count=len(filtered_chunks),
            chunks_filtered=len(filtered_out),
            below_threshold_chunks=filtered_out,
            diversity=diversity,
            conflicts=conflicts,
            quality_issues=issues,
            confidence_adjustment=confidence_adjustment,
            adjusted_confidence=adjusted_confidence,
            quality_status=quality_status
        )
        
        logger.info(
            f"Validation: {original_count} → {len(filtered_chunks)} chunks, "
            f"confidence: {original_confidence:.2f} → {adjusted_confidence:.2f}, "
            f"status: {quality_status}"
        )
        
        return validation
    
    def _filter_by_similarity(
        self,
        chunks: List[Any]
    ) -> Tuple[List[Any], List[str]]:
        """
        Filter chunks by similarity threshold.
        
        Args:
            chunks: List of RetrievedChunk objects
            
        Returns:
            Tuple of (filtered_chunks, filtered_out_ids)
        """
        filtered = []
        filtered_out = []
        
        for chunk in chunks:
            if chunk.similarity >= self.similarity_threshold:
                filtered.append(chunk)
            else:
                filtered_out.append(str(chunk.chunk_id))
                logger.debug(
                    f"Filtered out chunk {chunk.chunk_id}: "
                    f"similarity {chunk.similarity:.2f} < {self.similarity_threshold}"
                )
        
        return filtered, filtered_out
    
    def _analyze_diversity(self, chunks: List[Any]) -> DiversityAnalysis:
        """
        Analyze source diversity of chunks.
        
        Args:
            chunks: List of RetrievedChunk objects
            
        Returns:
            DiversityAnalysis object
        """
        doc_counts = defaultdict(int)
        
        for chunk in chunks:
            doc_counts[str(chunk.document_id)] += 1
        
        unique_documents = len(doc_counts)
        
        # Calculate diversity score (0-1)
        # More documents and better distribution = higher score
        if not chunks:
            diversity_score = 0.0
        elif unique_documents == 1:
            diversity_score = 0.0  # All from one doc
        else:
            # Measure distribution balance
            max_chunks_per_doc = max(doc_counts.values())
            balance = 1.0 - (max_chunks_per_doc / len(chunks))
            diversity_score = min(1.0, (unique_documents / 5.0) * (balance + 1.0) / 2.0)
        
        is_diverse = (
            unique_documents >= self.min_diversity_documents or
            diversity_score >= 0.7
        )
        
        logger.debug(
            f"Diversity: {unique_documents} docs, score {diversity_score:.2f}, "
            f"diverse: {is_diverse}"
        )
        
        return DiversityAnalysis(
            unique_documents=unique_documents,
            documents=dict(doc_counts),
            diversity_score=diversity_score,
            is_diverse=is_diverse
        )
    
    def _detect_conflicts(
        self,
        chunks: List[Any],
        query: str = ""
    ) -> ConflictAnalysis:
        """
        Detect potential conflicts between chunks.
        
        Uses simple heuristics to find contradictions:
        - Opposite keywords (e.g., "always" vs "never")
        - Contradictory claims
        - Numerical conflicts (e.g., different numbers for same fact)
        
        Args:
            chunks: List of RetrievedChunk objects
            query: Original query for context
            
        Returns:
            ConflictAnalysis object
        """
        conflicts = []
        conflict_keywords = set()
        
        if len(chunks) < 2:
            return ConflictAnalysis(
                has_conflicts=False,
                conflict_count=0,
                conflicts=[],
                conflict_keywords=set()
            )
        
        # Extract key terms from each chunk
        chunk_texts = [(str(c.chunk_id), str(c.text).lower()) for c in chunks]
        
        # Simple conflict detection: Check for contradictory phrases
        contradictions = [
            ("always", "never"),
            ("required", "optional"),
            ("true", "false"),
            ("yes", "no"),
            ("must", "should not"),
            ("guaranteed", "not guaranteed"),
            ("correct", "incorrect"),
            ("right", "wrong"),
        ]
        
        conflicts_found = defaultdict(list)
        
        for i, (id1, text1) in enumerate(chunk_texts):
            for id2, text2 in chunk_texts[i+1:]:
                # Check for contradictory phrases
                for term1, term2 in contradictions:
                    has_term1_1 = term1 in text1
                    has_term2_1 = term2 in text1
                    has_term1_2 = term1 in text2
                    has_term2_2 = term2 in text2
                    
                    # Potential conflict
                    if (has_term1_1 and has_term2_2) or (has_term2_1 and has_term1_2):
                        conflict_key = f"{id1}_vs_{id2}"
                        if conflict_key not in conflicts_found:
                            conflicts.append({
                                "chunk1_id": id1,
                                "chunk2_id": id2,
                                "contradiction": (term1, term2),
                                "text1_snippet": text1[:100],
                                "text2_snippet": text2[:100]
                            })
                            conflicts_found[conflict_key] = True
                            conflict_keywords.add(term1)
                            conflict_keywords.add(term2)
        
        has_conflicts = len(conflicts) > 0
        
        if has_conflicts:
            logger.warning(
                f"Detected {len(conflicts)} potential conflicts between chunks"
            )
        
        return ConflictAnalysis(
            has_conflicts=has_conflicts,
            conflict_count=len(conflicts),
            conflicts=conflicts,
            conflict_keywords=conflict_keywords
        )
    
    def _generate_quality_issues(
        self,
        filtered_chunks: List[Any],
        filtered_out_ids: List[str],
        diversity: DiversityAnalysis,
        conflicts: ConflictAnalysis
    ) -> List[RetrievalQualityIssue]:
        """
        Generate quality issues from validation results.
        
        Args:
            filtered_chunks: Chunks after filtering
            filtered_out_ids: Chunks removed by threshold
            diversity: Diversity analysis
            conflicts: Conflict analysis
            
        Returns:
            List of RetrievalQualityIssue objects
        """
        issues = []
        
        # Issue 1: Chunks below threshold
        if filtered_out_ids:
            issues.append(RetrievalQualityIssue(
                issue_type="below_threshold",
                severity="info",
                message=f"Filtered out {len(filtered_out_ids)} chunks below similarity threshold",
                details={
                    "count": len(filtered_out_ids),
                    "threshold": self.similarity_threshold,
                    "chunk_ids": filtered_out_ids[:5]  # First 5
                }
            ))
        
        # Issue 2: Low diversity
        if not diversity.is_diverse and len(filtered_chunks) > 0:
            issues.append(RetrievalQualityIssue(
                issue_type="low_diversity",
                severity="warning",
                message=f"Answer supported by only {diversity.unique_documents} document(s). "
                        f"Prefer multiple sources for higher confidence.",
                details={
                    "unique_documents": diversity.unique_documents,
                    "min_recommended": self.min_diversity_documents,
                    "diversity_score": diversity.diversity_score
                }
            ))
        
        # Issue 3: Conflicts detected
        if conflicts.has_conflicts:
            issues.append(RetrievalQualityIssue(
                issue_type="conflict",
                severity="warning",
                message=f"Detected {conflicts.conflict_count} potential conflict(s) between chunks. "
                        f"Answer may be contradictory.",
                details={
                    "conflict_count": conflicts.conflict_count,
                    "conflict_keywords": list(conflicts.conflict_keywords)[:10],
                    "conflicts": conflicts.conflicts[:3]  # First 3
                }
            ))
        
        return issues
    
    def _adjust_confidence(
        self,
        original_confidence: float,
        diversity: DiversityAnalysis,
        conflicts: ConflictAnalysis,
        chunk_count: int
    ) -> Tuple[float, str]:
        """
        Adjust confidence based on validation results.
        
        Args:
            original_confidence: Original confidence score
            diversity: Diversity analysis
            conflicts: Conflict analysis
            chunk_count: Number of chunks after filtering
            
        Returns:
            Tuple of (adjustment_multiplier, quality_status)
        """
        adjustment = 1.0
        status = "good"
        
        # Adjustment 1: Low chunk count after filtering
        if chunk_count == 0:
            adjustment *= 0.0
            status = "poor"
        elif chunk_count == 1:
            adjustment *= 0.7
            status = "fair"
        elif chunk_count < 3:
            adjustment *= 0.85
            if status == "good":
                status = "fair"
        else:
            adjustment *= 1.0  # No penalty for 3+ chunks
        
        # Adjustment 2: Low diversity
        if not diversity.is_diverse:
            if diversity.unique_documents == 1:
                adjustment *= 0.75  # 25% penalty for single source
            else:
                adjustment *= 0.9  # 10% penalty for low diversity
            if status == "good":
                status = "fair"
        else:
            adjustment *= 1.0  # Bonus for good diversity
        
        # Adjustment 3: Conflicts detected
        if conflicts.has_conflicts:
            conflict_penalty = 0.7 ** conflicts.conflict_count  # Exponential penalty
            adjustment *= max(conflict_penalty, 0.5)  # At least 50% confidence
            status = "poor"
        
        # Adjustment 4: Diversity score bonus
        if diversity.diversity_score > 0.8:
            adjustment = min(1.0, adjustment * 1.05)  # 5% bonus
            if status != "poor":
                status = "excellent"
        
        # Final status determination
        adjusted = original_confidence * adjustment
        if adjusted > 0.85:
            final_status = "excellent"
        elif adjusted > 0.70:
            final_status = "good"
        elif adjusted > 0.50:
            final_status = "fair"
        else:
            final_status = "poor"
        
        # Use worst judgment
        if status == "poor" or final_status == "poor":
            status = "poor"
        elif status == "fair" or final_status == "fair":
            status = "fair"
        elif status == "excellent" and final_status == "excellent":
            status = "excellent"
        else:
            status = final_status
        
        # Clamp adjustment to 0.0-1.0
        adjustment = max(0.0, min(1.0, adjustment))
        
        logger.debug(
            f"Confidence adjustment: {original_confidence:.2f} × {adjustment:.2f} "
            f"= {original_confidence * adjustment:.2f} ({status})"
        )
        
        return adjustment, status
