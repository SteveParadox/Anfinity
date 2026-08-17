"""Retrieval-grounded onboarding curriculum generation."""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Note, User as DBUser
from app.services.llm_service import LLMService, get_llm_service
from app.services.postgresql_search import get_postgresql_search_service
from app.services.semantic_search import get_semantic_search_service

logger = logging.getLogger(__name__)


class RoleQueryBundle(BaseModel):
    """Deterministic query bundle for one onboarding role."""

    role_input: str
    normalized_role: str
    role_label: str
    prioritized_queries: List[str] = Field(default_factory=list)
    fallback_queries: List[str] = Field(default_factory=list)

    def all_queries(self) -> List[str]:
        ordered = self.prioritized_queries + self.fallback_queries
        seen: set[str] = set()
        deduped: List[str] = []
        for item in ordered:
            normalized = " ".join(str(item).split()).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped


class OnboardingCandidateNote(BaseModel):
    """Grounding note candidate sent to the curriculum generator and UI."""

    note_id: str
    title: str
    excerpt: str
    summary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    note_type: str = "note"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    semantic_score: float = Field(default=0.0, ge=0.0, le=1.0)
    popularity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    freshness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ranking_score: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_queries: List[str] = Field(default_factory=list)
    query_hits: int = 0
    popularity_count: int = 0
    grounding_sources: List[str] = Field(default_factory=list)


class OnboardingReadingItem(BaseModel):
    note_id: str
    title: str
    reason: str


class OnboardingWeek(BaseModel):
    week_number: int = Field(..., ge=1, le=4)
    theme: str
    objectives: List[str] = Field(default_factory=list)
    support_note_ids: List[str] = Field(default_factory=list)
    reading_list: List[OnboardingReadingItem] = Field(default_factory=list)
    concept_checkpoints: List[str] = Field(default_factory=list)


class OnboardingGlossaryEntry(BaseModel):
    term: str
    definition: str
    support_note_ids: List[str] = Field(default_factory=list)


class OnboardingGroundingMetadata(BaseModel):
    candidate_note_count: int = 0
    model_candidate_note_count: int = 0
    selected_note_count: int = 0
    grounding_confidence: str = Field(default="low", pattern="^(high|medium|low)$")
    insufficient_content: bool = False
    warnings: List[str] = Field(default_factory=list)
    role_queries: List[str] = Field(default_factory=list)
    fallback_queries: List[str] = Field(default_factory=list)
    used_note_ids: List[str] = Field(default_factory=list)


class OnboardingCurriculum(BaseModel):
    role_input: str
    role: str
    normalized_role: str
    summary: str
    weeks: List[OnboardingWeek] = Field(default_factory=list, min_length=4, max_length=4)
    glossary: List[OnboardingGlossaryEntry] = Field(default_factory=list)
    grounding: OnboardingGroundingMetadata
    candidate_notes: List[OnboardingCandidateNote] = Field(default_factory=list)


class _DraftReadingItem(BaseModel):
    note_id: Optional[str] = None
    title: Optional[str] = None
    reason: Optional[str] = None


class _DraftWeek(BaseModel):
    week_number: Optional[int] = None
    theme: Optional[str] = None
    objectives: List[str] = Field(default_factory=list)
    support_note_ids: List[str] = Field(default_factory=list)
    reading_list: List[_DraftReadingItem] = Field(default_factory=list)
    concept_checkpoints: List[str] = Field(default_factory=list)


class _DraftGlossaryEntry(BaseModel):
    term: Optional[str] = None
    definition: Optional[str] = None
    support_note_ids: List[str] = Field(default_factory=list)


class _DraftCurriculum(BaseModel):
    summary: Optional[str] = None
    weeks: List[_DraftWeek] = Field(default_factory=list)
    glossary: List[_DraftGlossaryEntry] = Field(default_factory=list)


ROLE_QUERY_LIBRARY: Dict[str, Dict[str, Any]] = {
    "engineer": {
        "label": "Engineer",
        "aliases": {"software engineer", "developer", "engineering", "backend engineer", "frontend engineer"},
        "keywords": {"engineer", "developer", "software", "backend", "frontend", "platform", "devops", "sre"},
        "prioritized_queries": [
            "technical architecture",
            "deployment process",
            "engineering standards",
            "service ownership",
            "incident handling",
        ],
        "fallback_queries": [
            "system design decisions",
            "developer workflow",
            "release checklist",
            "coding conventions",
        ],
    },
    "product_manager": {
        "label": "Product Manager",
        "aliases": {"pm", "product", "product lead"},
        "keywords": {"product", "pm", "roadmap", "planning", "growth"},
        "prioritized_queries": [
            "product strategy",
            "roadmap priorities",
            "customer problems",
            "decision-making process",
            "feature launch process",
        ],
        "fallback_queries": [
            "stakeholder updates",
            "metrics and success criteria",
            "product review cadence",
            "planning process",
        ],
    },
    "designer": {
        "label": "Designer",
        "aliases": {"product designer", "ux designer", "ui designer", "design"},
        "keywords": {"designer", "design", "ux", "ui", "research", "visual"},
        "prioritized_queries": [
            "design system",
            "user research findings",
            "interaction patterns",
            "accessibility standards",
            "design review process",
        ],
        "fallback_queries": [
            "brand guidelines",
            "prototype workflow",
            "handoff expectations",
            "usability insights",
        ],
    },
    "customer_success": {
        "label": "Customer Success",
        "aliases": {"customer support", "support", "csm", "success manager"},
        "keywords": {"support", "success", "customer", "implementation", "renewal"},
        "prioritized_queries": [
            "customer onboarding process",
            "support escalation path",
            "common customer issues",
            "success metrics",
            "customer health signals",
        ],
        "fallback_queries": [
            "implementation checklist",
            "renewal risks",
            "knowledge base standards",
            "customer communication patterns",
        ],
    },
    "sales": {
        "label": "Sales",
        "aliases": {"account executive", "ae", "revenue", "business development"},
        "keywords": {"sales", "account", "revenue", "pipeline", "buyer", "deal"},
        "prioritized_queries": [
            "sales process",
            "buyer objections",
            "pricing and packaging",
            "customer use cases",
            "competitive positioning",
        ],
        "fallback_queries": [
            "pipeline stages",
            "discovery questions",
            "demo flow",
            "deal qualification standards",
        ],
    },
    "manager": {
        "label": "Manager",
        "aliases": {"team lead", "lead", "engineering manager", "people manager"},
        "keywords": {"manager", "lead", "leadership", "people", "planning", "staffing"},
        "prioritized_queries": [
            "team responsibilities",
            "planning cadence",
            "decision ownership",
            "cross-functional collaboration",
            "risk management",
        ],
        "fallback_queries": [
            "operating rhythms",
            "goals and metrics",
            "staffing context",
            "meeting rituals",
        ],
    },
}

GENERAL_ONBOARDING_QUERIES: List[str] = [
    "workspace overview",
    "team responsibilities",
    "decision records",
    "operating processes",
]

ROLE_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "associate",
    "consultant",
    "for",
    "general",
    "head",
    "ii",
    "iii",
    "intern",
    "junior",
    "lead",
    "manager",
    "of",
    "principal",
    "role",
    "senior",
    "specialist",
    "staff",
    "team",
    "the",
}

GENERIC_NOTE_TITLES = {
    "meeting notes",
    "misc",
    "notes",
    "scratchpad",
    "todo",
    "todo list",
    "untitled",
    "untitled note",
}

GENERIC_CURRICULUM_PATTERNS = (
    "basics",
    "company overview",
    "general onboarding",
    "getting started",
    "introduction",
    "intro",
    "onboarding overview",
    "orientation",
    "ramp up",
    "ramp-up",
)


class OnboardingAcceleratorService:
    """Explicit retrieval and curriculum generation workflow for onboarding."""

    SEMANTIC_QUERY_LIMIT = 12
    POPULARITY_LIMIT = 18
    FINAL_CANDIDATE_LIMIT = 50
    MAX_MODEL_CONTEXT_CHARS = 22000
    MAX_MODEL_NOTE_CONTEXT_CHARS = 240
    MAX_MODEL_NOTE_TAGS = 4
    MAX_MODEL_NOTE_QUERIES = 2
    MAX_GLOSSARY_ENTRIES = 12
    RECENCY_HALF_LIFE_DAYS = 45

    def __init__(
        self,
        *,
        llm_service: Optional[LLMService] = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_service(
            openai_model=settings.ONBOARDING_ACCELERATOR_MODEL,
            primary_provider="openai",
            use_fallback=False,
        )
        self.semantic_search_service = get_semantic_search_service()
        self.postgresql_search_service = get_postgresql_search_service()

    def build_role_query_bundle(self, role: str) -> RoleQueryBundle:
        normalized_input = self._normalize_role_key(role)
        resolved_key = self._resolve_role_key(normalized_input)
        config = ROLE_QUERY_LIBRARY.get(resolved_key)
        if config is None:
            role_label = self._humanize_role_label(role)
            focus_terms = self._extract_role_focus_terms(role)
            primary_focus = focus_terms[0] if focus_terms else role_label.lower()
            prioritized_queries = [
                f"{role_label} responsibilities",
                f"{primary_focus} workflow",
                f"{primary_focus} standards",
                f"{role_label} handoffs",
            ]
            fallback_queries = [
                f"{primary_focus} systems",
                f"{primary_focus} process",
                *GENERAL_ONBOARDING_QUERIES,
            ]
            normalized_role = normalized_input or "general"
            resolved_label = role_label
        else:
            resolved_label = str(config["label"])
            normalized_role = resolved_key
            prioritized_queries = list(config["prioritized_queries"])
            fallback_queries = list(config["fallback_queries"]) + list(GENERAL_ONBOARDING_QUERIES)

        return RoleQueryBundle(
            role_input=(role or "").strip(),
            normalized_role=normalized_role,
            role_label=resolved_label,
            prioritized_queries=self._dedupe_texts(prioritized_queries),
            fallback_queries=self._dedupe_texts(fallback_queries),
        )

    async def generate_curriculum(
        self,
        *,
        workspace_id: UUID,
        user: DBUser,
        role: str,
        db: AsyncSession,
    ) -> OnboardingCurriculum:
        role_bundle = self.build_role_query_bundle(role)
        candidate_notes = await self.retrieve_candidate_notes(
            workspace_id=workspace_id,
            user_id=user.id,
            role_bundle=role_bundle,
            db=db,
        )
        warnings: List[str] = []
        if not candidate_notes:
            warnings.append("No strong workspace notes were found for this role. Returning a minimal grounded plan.")
            return self._build_sparse_fallback_curriculum(role_bundle, candidate_notes, warnings)

        model_candidates = self._cap_candidates_for_model(candidate_notes)
        insufficient_content = len(model_candidates) < 6
        if insufficient_content:
            warnings.append("Workspace content is sparse for this role, so the curriculum may be shallow.")

        try:
            draft_payload = await self._generate_curriculum_draft(role_bundle, model_candidates, insufficient_content)
            curriculum = self._repair_curriculum(
                role_bundle=role_bundle,
                candidates=model_candidates,
                raw_payload=draft_payload,
                warnings=warnings,
            )
        except Exception as exc:
            logger.warning("Onboarding curriculum generation fell back to deterministic mode: %s", exc)
            warnings.append(f"Model generation was unavailable, so the curriculum uses deterministic fallback logic: {exc}")
            curriculum = self._build_sparse_fallback_curriculum(role_bundle, model_candidates, warnings)

        curriculum.grounding.candidate_note_count = len(candidate_notes)
        curriculum.grounding.model_candidate_note_count = len(model_candidates)
        curriculum.candidate_notes = model_candidates
        curriculum.grounding.warnings = self._dedupe_texts(
            [
                *curriculum.grounding.warnings,
                *self._build_grounding_warnings(model_candidates, curriculum.grounding.grounding_confidence),
            ]
        )
        return curriculum

    async def retrieve_candidate_notes(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        role_bundle: RoleQueryBundle,
        db: AsyncSession,
    ) -> List[OnboardingCandidateNote]:
        semantic_hits = await self._retrieve_semantic_hits(
            workspace_id=workspace_id,
            user_id=user_id,
            queries=role_bundle.all_queries(),
            db=db,
        )
        popularity_stats = await self._retrieve_popularity_stats(workspace_id=workspace_id, db=db)
        note_ids = {
            *semantic_hits.keys(),
            *popularity_stats.keys(),
        }
        if not note_ids:
            return []

        notes = await self._load_notes(workspace_id=workspace_id, note_ids=note_ids, db=db)
        if not notes:
            return []

        max_popularity = max((stats["interaction_count"] for stats in popularity_stats.values()), default=0)
        candidates: List[OnboardingCandidateNote] = []
        for note in notes:
            note_id = str(note.id)
            hit_group = semantic_hits.get(note_id, [])
            popularity = popularity_stats.get(note_id, {})
            matched_queries = self._dedupe_texts([str(hit["query"]) for hit in hit_group])
            semantic_score = self._calculate_semantic_score(hit_group)
            freshness_score = self._calculate_recency_score(note.updated_at or note.created_at)
            completeness_score = self._calculate_completeness_score(note)
            popularity_count = int(popularity.get("interaction_count", 0) or 0)
            popularity_score = self._calculate_popularity_score(
                count=popularity_count,
                max_popularity=max_popularity,
                last_interaction=popularity.get("last_interaction"),
            )
            grounding_sources: List[str] = []
            if hit_group:
                grounding_sources.append("semantic")
            if popularity_count > 0:
                grounding_sources.append("popular")
            if not grounding_sources:
                continue

            if semantic_score < 0.18 and popularity_score < 0.32 and completeness_score < 0.40:
                continue

            if self._is_low_signal_note(
                note,
                semantic_score=semantic_score,
                popularity_score=popularity_score,
                completeness_score=completeness_score,
            ):
                continue

            excerpt = self._build_excerpt(note=note, hit_group=hit_group)
            candidate = OnboardingCandidateNote(
                note_id=note_id,
                title=(note.title or "Untitled note").strip(),
                excerpt=excerpt,
                summary=(note.summary or None),
                tags=[str(tag) for tag in (note.tags or []) if str(tag).strip()],
                note_type=str(note.note_type or "note"),
                created_at=self._format_datetime(note.created_at),
                updated_at=self._format_datetime(note.updated_at or note.created_at),
                semantic_score=round(semantic_score, 4),
                popularity_score=round(popularity_score, 4),
                freshness_score=round(freshness_score, 4),
                completeness_score=round(completeness_score, 4),
                matched_queries=matched_queries,
                query_hits=len(matched_queries),
                popularity_count=popularity_count,
                grounding_sources=grounding_sources,
            )
            candidate.ranking_score = round(
                self._calculate_candidate_ranking_score(
                    candidate=candidate,
                    total_queries=len(role_bundle.all_queries()),
                ),
                4,
            )
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (item.ranking_score, item.semantic_score, item.popularity_count, item.freshness_score),
            reverse=True,
        )
        return self._select_diverse_candidates(candidates, limit=self.FINAL_CANDIDATE_LIMIT)

    async def _generate_curriculum_draft(
        self,
        role_bundle: RoleQueryBundle,
        candidates: Sequence[OnboardingCandidateNote],
        insufficient_content: bool,
    ) -> Mapping[str, Any]:
        system_prompt = (
            "You are generating a 4-week onboarding curriculum for a workspace.\n"
            "You must stay strictly grounded in the provided candidate notes.\n"
            "Never invent documents, systems, processes, tools, or terminology that are not supported by the notes.\n"
            "Use only note IDs from the candidate set.\n"
            "If the workspace content is sparse, say so clearly and keep the curriculum modest.\n"
            "Return JSON only."
        )
        candidate_payload = [self._serialize_candidate_for_model(candidate) for candidate in candidates]
        candidate_payload_json = json.dumps(candidate_payload, ensure_ascii=True, separators=(",", ":"))
        while len(candidate_payload_json) > self.MAX_MODEL_CONTEXT_CHARS and len(candidate_payload) > 8:
            candidate_payload = candidate_payload[:-1]
            candidate_payload_json = json.dumps(candidate_payload, ensure_ascii=True, separators=(",", ":"))
        user_prompt = (
            f"Target role: {role_bundle.role_label}\n"
            f"Normalized role: {role_bundle.normalized_role}\n"
            f"Prioritized queries: {role_bundle.prioritized_queries}\n"
            f"Fallback queries: {role_bundle.fallback_queries}\n"
            f"Insufficient content signal: {insufficient_content}\n\n"
            "Candidate notes JSON:\n"
            f"{candidate_payload_json}\n\n"
            "Return a JSON object with this exact shape:\n"
            "{\n"
            '  "summary": "short grounded summary",\n'
            '  "weeks": [\n'
            "    {\n"
            '      "week_number": 1,\n'
            '      "theme": "grounded theme",\n'
            '      "objectives": ["..."],\n'
            '      "support_note_ids": ["candidate-note-id"],\n'
            '      "reading_list": [{"note_id": "candidate-note-id", "reason": "why this note matters"}],\n'
            '      "concept_checkpoints": ["question or checkpoint"]\n'
            "    }\n"
            "  ],\n"
            '  "glossary": [{"term": "workspace term", "definition": "grounded definition", "support_note_ids": ["candidate-note-id"]}]\n'
            "}\n\n"
            "Rules:\n"
            "1. Exactly 4 weeks.\n"
            "2. Every week must reference real candidate note IDs.\n"
            "3. Reading lists must be grounded in actual note content.\n"
            "4. Do not add generic corporate onboarding filler.\n"
            "5. Balance foundational material first, then deeper execution details.\n"
            "6. Glossary terms should come from the note content when possible.\n"
            "7. If evidence is weak, say so in the summary and use fewer, more cautious objectives."
        )
        return await self.llm_service.async_openai_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=settings.ONBOARDING_ACCELERATOR_MODEL,
            temperature=0.15,
            max_tokens=settings.ONBOARDING_ACCELERATOR_MAX_TOKENS,
        )

    async def _retrieve_semantic_hits(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        queries: Sequence[str],
        db: AsyncSession,
    ) -> Dict[str, List[Dict[str, Any]]]:
        hits: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for query in queries:
            try:
                execution = await self.semantic_search_service.search(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    query=query,
                    limit=self.SEMANTIC_QUERY_LIMIT,
                    filters={},
                    db=db,
                    log_execution=False,
                    include_postgresql=True,
                    include_retriever=False,
                )
            except Exception as exc:
                logger.warning("Semantic onboarding retrieval failed for query=%s: %s", query, exc)
                continue

            for rank, result in enumerate(execution.results):
                if str(result.source_kind or "").lower() != "note":
                    continue
                note_id = str(result.document_id)
                hits[note_id].append(
                    {
                        "query": query,
                        "rank": rank,
                        "score": max(float(result.final_score or result.similarity_score or 0.0), 0.0),
                        "highlight": result.highlight or result.content[:280],
                    }
                )
        return hits

    async def _retrieve_popularity_stats(
        self,
        *,
        workspace_id: UUID,
        db: AsyncSession,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            raw_stats = await self.postgresql_search_service.get_interaction_stats(db=db, workspace_id=workspace_id)
        except Exception as exc:
            logger.warning("Interaction stats function unavailable for onboarding retrieval: %s", exc)
            raw_stats = await self._retrieve_popularity_stats_from_table(workspace_id=workspace_id, db=db)

        stats_by_note_id: Dict[str, Dict[str, Any]] = {}
        for item in raw_stats[: self.POPULARITY_LIMIT]:
            stats_by_note_id[str(item["note_id"])] = {
                "interaction_count": int(item.get("interaction_count", 0) or 0),
                "last_interaction": item.get("last_interaction"),
                "interaction_types": list(item.get("interaction_types") or []),
            }
        return stats_by_note_id

    async def _retrieve_popularity_stats_from_table(
        self,
        *,
        workspace_id: UUID,
        db: AsyncSession,
    ) -> List[Dict[str, Any]]:
        try:
            result = await db.execute(
                text(
                    """
                    SELECT
                        note_id,
                        COUNT(*) AS interaction_count,
                        MAX(created_at) AS last_interaction,
                        ARRAY_AGG(DISTINCT interaction_type) AS interaction_types
                    FROM note_interactions
                    WHERE workspace_id = :workspace_id
                    GROUP BY note_id
                    ORDER BY interaction_count DESC, last_interaction DESC
                    LIMIT :limit
                    """
                ),
                {
                    "workspace_id": workspace_id,
                    "limit": self.POPULARITY_LIMIT,
                },
            )
        except Exception as exc:
            logger.warning("Fallback note_interactions query unavailable: %s", exc)
            return []

        return [
            {
                "note_id": str(row[0]),
                "interaction_count": int(row[1] or 0),
                "last_interaction": row[2].isoformat() if row[2] else None,
                "interaction_types": list(row[3] or []),
            }
            for row in result.fetchall()
        ]

    async def _load_notes(
        self,
        *,
        workspace_id: UUID,
        note_ids: Iterable[str],
        db: AsyncSession,
    ) -> List[Note]:
        parsed_ids: List[UUID] = []
        for note_id in note_ids:
            try:
                parsed_ids.append(UUID(str(note_id)))
            except (TypeError, ValueError):
                continue
        if not parsed_ids:
            return []

        result = await db.execute(
            select(Note)
            .where(Note.workspace_id == workspace_id, Note.id.in_(parsed_ids))
        )
        return list(result.scalars().all())

    def _serialize_candidate_for_model(self, candidate: OnboardingCandidateNote) -> Dict[str, Any]:
        context_source = candidate.summary or candidate.excerpt
        return {
            "note_id": candidate.note_id,
            "title": self._truncate_text(candidate.title, limit=120),
            "context": self._truncate_text(context_source, limit=self.MAX_MODEL_NOTE_CONTEXT_CHARS),
            "tags": [self._truncate_text(tag, limit=32) for tag in candidate.tags[: self.MAX_MODEL_NOTE_TAGS]],
            "matched_queries": candidate.matched_queries[: self.MAX_MODEL_NOTE_QUERIES],
            "note_type": candidate.note_type,
            "rank_score": round(candidate.ranking_score, 3),
            "semantic_score": round(candidate.semantic_score, 3),
            "popularity_score": round(candidate.popularity_score, 3),
            "grounding_sources": candidate.grounding_sources,
        }

    def _repair_curriculum(
        self,
        *,
        role_bundle: RoleQueryBundle,
        candidates: Sequence[OnboardingCandidateNote],
        raw_payload: Mapping[str, Any],
        warnings: List[str],
    ) -> OnboardingCurriculum:
        try:
            draft = _DraftCurriculum.model_validate(raw_payload)
        except ValidationError as exc:
            warnings.append(f"Structured curriculum output was malformed and had to be rebuilt: {exc.errors()[0]['msg']}")
            return self._build_sparse_fallback_curriculum(role_bundle, candidates, warnings)

        candidate_map = {candidate.note_id: candidate for candidate in candidates}
        normalized_title_map = {self._normalize_text(candidate.title): candidate for candidate in candidates}
        fallback_groups = self._build_fallback_week_groups(candidates)
        weeks: List[OnboardingWeek] = []
        used_note_ids: List[str] = []

        for week_index in range(4):
            source_week = draft.weeks[week_index] if week_index < len(draft.weeks) else _DraftWeek()
            fallback_candidates = fallback_groups[week_index]
            reading_list = self._repair_reading_list(
                reading_list=source_week.reading_list,
                candidate_map=candidate_map,
                normalized_title_map=normalized_title_map,
                fallback_candidates=fallback_candidates,
            )
            support_note_ids = self._dedupe_texts(
                [
                    *[item.note_id for item in reading_list],
                    *[
                        note_id
                        for note_id in (source_week.support_note_ids or [])
                        if str(note_id) in candidate_map
                    ],
                ]
            )
            if not support_note_ids:
                support_note_ids = [item.note_id for item in reading_list]

            theme = self._clean_text(source_week.theme) or self._derive_week_theme(week_index + 1, support_note_ids, candidate_map)
            if self._is_generic_grounded_text(theme, support_note_ids=support_note_ids, candidate_map=candidate_map):
                theme = self._derive_week_theme(week_index + 1, support_note_ids, candidate_map)
            objectives = self._clean_items(source_week.objectives, limit=4)
            objectives = [
                item
                for item in objectives
                if not self._is_generic_grounded_text(item, support_note_ids=support_note_ids, candidate_map=candidate_map)
            ]
            if not objectives:
                objectives = self._build_fallback_objectives(theme=theme, support_note_ids=support_note_ids, candidate_map=candidate_map)
            checkpoints = self._clean_items(source_week.concept_checkpoints, limit=4)
            checkpoints = [
                item
                for item in checkpoints
                if not self._is_generic_grounded_text(item, support_note_ids=support_note_ids, candidate_map=candidate_map)
            ]
            if not checkpoints:
                checkpoints = self._build_fallback_checkpoints(support_note_ids=support_note_ids, candidate_map=candidate_map)

            weeks.append(
                OnboardingWeek(
                    week_number=week_index + 1,
                    theme=theme,
                    objectives=objectives,
                    support_note_ids=support_note_ids,
                    reading_list=reading_list,
                    concept_checkpoints=checkpoints,
                )
            )
            used_note_ids.extend(support_note_ids)

        glossary = self._repair_glossary(
            glossary=draft.glossary,
            candidate_map=candidate_map,
            fallback_candidates=candidates,
        )
        used_note_ids.extend(note_id for entry in glossary for note_id in entry.support_note_ids)

        grounding_confidence = self._determine_grounding_confidence(candidates)
        insufficient_content = len(candidates) < 6 or grounding_confidence == "low"
        summary = self._clean_text(draft.summary) or self._build_fallback_summary(role_bundle, candidates, insufficient_content)
        if self._is_generic_grounded_text(
            summary,
            support_note_ids=[candidate.note_id for candidate in candidates[:4]],
            candidate_map=candidate_map,
        ):
            summary = self._build_fallback_summary(role_bundle, candidates, insufficient_content)

        return OnboardingCurriculum(
            role_input=role_bundle.role_input,
            role=role_bundle.role_label,
            normalized_role=role_bundle.normalized_role,
            summary=summary,
            weeks=weeks,
            glossary=glossary,
            grounding=OnboardingGroundingMetadata(
                candidate_note_count=len(candidates),
                model_candidate_note_count=len(candidates),
                selected_note_count=len(self._dedupe_texts(used_note_ids)),
                grounding_confidence=grounding_confidence,
                insufficient_content=insufficient_content,
                warnings=list(warnings),
                role_queries=list(role_bundle.prioritized_queries),
                fallback_queries=list(role_bundle.fallback_queries),
                used_note_ids=self._dedupe_texts(used_note_ids),
            ),
            candidate_notes=list(candidates),
        )

    def _repair_reading_list(
        self,
        *,
        reading_list: Sequence[_DraftReadingItem],
        candidate_map: Mapping[str, OnboardingCandidateNote],
        normalized_title_map: Mapping[str, OnboardingCandidateNote],
        fallback_candidates: Sequence[OnboardingCandidateNote],
    ) -> List[OnboardingReadingItem]:
        repaired: List[OnboardingReadingItem] = []
        for item in reading_list or []:
            note = None
            if item.note_id and str(item.note_id) in candidate_map:
                note = candidate_map[str(item.note_id)]
            elif item.title:
                note = normalized_title_map.get(self._normalize_text(item.title)) or self._match_candidate_by_title(
                    item.title,
                    candidate_map.values(),
                )
            if note is None:
                continue
            reason = self._clean_text(item.reason) or self._build_fallback_reading_reason(note)
            if self._is_generic_reason(reason):
                reason = self._build_fallback_reading_reason(note)
            repaired.append(
                OnboardingReadingItem(
                    note_id=note.note_id,
                    title=note.title,
                    reason=reason,
                )
            )

        if not repaired:
            repaired = [
                OnboardingReadingItem(
                    note_id=note.note_id,
                    title=note.title,
                    reason=self._build_fallback_reading_reason(note),
                )
                for note in fallback_candidates[: min(4, len(fallback_candidates))]
            ]

        deduped: List[OnboardingReadingItem] = []
        seen: set[str] = set()
        for item in repaired:
            if item.note_id in seen:
                continue
            seen.add(item.note_id)
            deduped.append(item)
        return deduped[:6]

    def _repair_glossary(
        self,
        *,
        glossary: Sequence[_DraftGlossaryEntry],
        candidate_map: Mapping[str, OnboardingCandidateNote],
        fallback_candidates: Sequence[OnboardingCandidateNote],
    ) -> List[OnboardingGlossaryEntry]:
        repaired: List[OnboardingGlossaryEntry] = []
        seen_terms: set[str] = set()
        for entry in glossary[: self.MAX_GLOSSARY_ENTRIES]:
            term = self._clean_text(entry.term)
            definition = self._clean_text(entry.definition)
            support_note_ids = self._dedupe_texts(
                [note_id for note_id in entry.support_note_ids if str(note_id) in candidate_map]
            )
            if not term or not definition or not support_note_ids:
                continue
            if not self._term_supported_by_candidates(term, support_note_ids=support_note_ids, candidate_map=candidate_map):
                continue
            normalized_term = self._normalize_text(term)
            if normalized_term in seen_terms:
                continue
            seen_terms.add(normalized_term)
            repaired.append(
                OnboardingGlossaryEntry(
                    term=term,
                    definition=definition,
                    support_note_ids=support_note_ids,
                )
            )

        if repaired:
            return repaired

        fallback_entries: List[OnboardingGlossaryEntry] = []
        for candidate in fallback_candidates[:8]:
            if not candidate.tags:
                continue
            term = candidate.tags[0]
            normalized_term = self._normalize_text(term)
            if normalized_term in seen_terms:
                continue
            seen_terms.add(normalized_term)
            fallback_entries.append(
                OnboardingGlossaryEntry(
                    term=term,
                    definition=f"Appears in notes such as '{candidate.title}', which frame this concept in the workspace context.",
                    support_note_ids=[candidate.note_id],
                )
            )
            if len(fallback_entries) >= 5:
                break
        return fallback_entries

    def _build_sparse_fallback_curriculum(
        self,
        role_bundle: RoleQueryBundle,
        candidates: Sequence[OnboardingCandidateNote],
        warnings: Sequence[str],
    ) -> OnboardingCurriculum:
        fallback_groups = self._build_fallback_week_groups(candidates)
        weeks: List[OnboardingWeek] = []
        used_note_ids: List[str] = []
        for week_index in range(4):
            week_candidates = fallback_groups[week_index]
            support_note_ids = [candidate.note_id for candidate in week_candidates[: min(3, len(week_candidates))]]
            reading_list = [
                OnboardingReadingItem(
                    note_id=candidate.note_id,
                    title=candidate.title,
                    reason=self._build_fallback_reading_reason(candidate),
                )
                for candidate in week_candidates[: min(4, len(week_candidates))]
            ]
            theme = self._derive_week_theme(week_index + 1, support_note_ids, {candidate.note_id: candidate for candidate in candidates})
            weeks.append(
                OnboardingWeek(
                    week_number=week_index + 1,
                    theme=theme,
                    objectives=self._build_fallback_objectives(
                        theme=theme,
                        support_note_ids=support_note_ids,
                        candidate_map={candidate.note_id: candidate for candidate in candidates},
                    ),
                    support_note_ids=support_note_ids,
                    reading_list=reading_list,
                    concept_checkpoints=self._build_fallback_checkpoints(
                        support_note_ids=support_note_ids,
                        candidate_map={candidate.note_id: candidate for candidate in candidates},
                    ),
                )
            )
            used_note_ids.extend(support_note_ids)

        candidate_map = {candidate.note_id: candidate for candidate in candidates}
        glossary = self._repair_glossary(glossary=[], candidate_map=candidate_map, fallback_candidates=candidates)
        used_note_ids.extend(note_id for entry in glossary for note_id in entry.support_note_ids)

        return OnboardingCurriculum(
            role_input=role_bundle.role_input,
            role=role_bundle.role_label,
            normalized_role=role_bundle.normalized_role,
            summary=self._build_fallback_summary(role_bundle, candidates, len(candidates) < 6),
            weeks=weeks,
            glossary=glossary,
            grounding=OnboardingGroundingMetadata(
                candidate_note_count=len(candidates),
                model_candidate_note_count=len(candidates),
                selected_note_count=len(self._dedupe_texts(used_note_ids)),
                grounding_confidence=self._determine_grounding_confidence(candidates),
                insufficient_content=len(candidates) < 6,
                warnings=list(warnings),
                role_queries=list(role_bundle.prioritized_queries),
                fallback_queries=list(role_bundle.fallback_queries),
                used_note_ids=self._dedupe_texts(used_note_ids),
            ),
            candidate_notes=list(candidates),
        )

    def _build_fallback_week_groups(
        self,
        candidates: Sequence[OnboardingCandidateNote],
    ) -> List[List[OnboardingCandidateNote]]:
        if not candidates:
            return [[], [], [], []]

        groups: List[List[OnboardingCandidateNote]] = [[], [], [], []]
        for index, candidate in enumerate(candidates):
            bucket = min((index * 4) // max(len(candidates), 1), 3)
            groups[bucket].append(candidate)

        for index in range(1, 4):
            if not groups[index] and groups[index - 1]:
                groups[index].append(groups[index - 1][-1])
        return groups

    def _select_diverse_candidates(
        self,
        candidates: Sequence[OnboardingCandidateNote],
        *,
        limit: int,
    ) -> List[OnboardingCandidateNote]:
        remaining = list(candidates)
        selected: List[OnboardingCandidateNote] = []
        topic_counts: Counter[str] = Counter()
        query_counts: Counter[str] = Counter()

        while remaining and len(selected) < limit:
            best_index = 0
            best_score = -1.0
            for index, candidate in enumerate(remaining):
                adjusted_score = candidate.ranking_score
                topic_key = self._candidate_topic_key(candidate)
                dominant_query = candidate.matched_queries[0] if candidate.matched_queries else "__popular__"
                adjusted_score -= min(topic_counts[topic_key] * 0.09, 0.27)
                adjusted_score -= min(query_counts[dominant_query] * 0.03, 0.12)
                if candidate.semantic_score == 0.0 and "popular" in candidate.grounding_sources:
                    adjusted_score -= min(sum(1 for item in selected if "popular" in item.grounding_sources) * 0.02, 0.10)
                if adjusted_score > best_score:
                    best_score = adjusted_score
                    best_index = index

            candidate = remaining.pop(best_index)
            if self._is_near_duplicate(candidate, selected):
                continue
            if (
                len(selected) >= min(limit, 24)
                and best_score < 0.12
                and candidate.semantic_score < 0.16
                and candidate.popularity_score < 0.10
            ):
                break

            candidate.ranking_score = round(max(best_score, 0.0), 4)
            selected.append(candidate)
            topic_counts[self._candidate_topic_key(candidate)] += 1
            dominant_query = candidate.matched_queries[0] if candidate.matched_queries else "__popular__"
            query_counts[dominant_query] += 1

        return selected

    def _cap_candidates_for_model(
        self,
        candidates: Sequence[OnboardingCandidateNote],
    ) -> List[OnboardingCandidateNote]:
        selected: List[OnboardingCandidateNote] = []
        total_chars = 0
        for candidate in candidates[: self.FINAL_CANDIDATE_LIMIT]:
            candidate_chars = len(candidate.title) + len(candidate.excerpt) + sum(len(tag) for tag in candidate.tags)
            if selected and total_chars + candidate_chars > self.MAX_MODEL_CONTEXT_CHARS:
                break
            selected.append(candidate)
            total_chars += candidate_chars
        return selected

    def _calculate_semantic_score(self, hit_group: Sequence[Mapping[str, Any]]) -> float:
        if not hit_group:
            return 0.0
        scores = sorted((max(float(hit.get("score", 0.0) or 0.0), 0.0) for hit in hit_group), reverse=True)
        top_scores = scores[: min(3, len(scores))]
        best_score = top_scores[0]
        average_top_score = sum(top_scores) / len(top_scores)
        return min((best_score * 0.78) + (average_top_score * 0.22), 1.0)

    def _calculate_candidate_ranking_score(
        self,
        *,
        candidate: OnboardingCandidateNote,
        total_queries: int,
    ) -> float:
        query_coverage = min(candidate.query_hits / max(total_queries, 1), 1.0)
        return (
            (candidate.semantic_score * 0.55)
            + (candidate.popularity_score * 0.20)
            + (candidate.freshness_score * 0.10)
            + (candidate.completeness_score * 0.10)
            + (query_coverage * 0.05)
        )

    def _calculate_completeness_score(self, note: Note) -> float:
        summary_bonus = 0.30 if note.summary else 0.0
        word_count = max(int(note.word_count or 0), len((note.content or "").split()))
        word_bonus = min(math.log1p(word_count) / math.log1p(1200), 1.0) * 0.30
        tag_bonus = min(len(note.tags or []), 4) / 4 * 0.20
        content_bonus = min(len((note.content or "").strip()) / 1200, 1.0) * 0.20
        return min(summary_bonus + word_bonus + tag_bonus + content_bonus, 1.0)

    def _calculate_recency_score(self, dt: Optional[datetime]) -> float:
        if dt is None:
            return 0.0
        normalized = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_days = max((now - normalized.astimezone(timezone.utc)).days, 0)
        decay_constant = math.log(2) / self.RECENCY_HALF_LIFE_DAYS
        return math.exp(-decay_constant * age_days)

    def _calculate_popularity_score(
        self,
        *,
        count: int,
        max_popularity: int,
        last_interaction: Any,
    ) -> float:
        count_score = self._normalize_popularity(count, max_popularity=max_popularity)
        if count_score <= 0:
            return 0.0
        interaction_dt = self._parse_datetime(last_interaction)
        interaction_recency = self._calculate_recency_score(interaction_dt) if interaction_dt else 0.0
        return min((count_score * 0.82) + (interaction_recency * 0.18), 1.0)

    def _is_low_signal_note(
        self,
        note: Note,
        *,
        semantic_score: float,
        popularity_score: float,
        completeness_score: float,
    ) -> bool:
        normalized_title = self._normalize_text(note.title)
        word_count = max(int(note.word_count or 0), len((note.content or "").split()))
        generic_title = normalized_title in GENERIC_NOTE_TITLES or normalized_title.startswith("meeting notes")
        summary_present = bool((note.summary or "").strip())
        tag_count = len(note.tags or [])

        if generic_title and semantic_score < 0.25 and popularity_score < 0.55 and word_count < 140:
            return True
        if word_count < 35 and completeness_score < 0.25 and semantic_score < 0.20 and popularity_score < 0.55:
            return True
        if not summary_present and tag_count == 0 and word_count < 60 and semantic_score < 0.16:
            return True
        return False

    def _normalize_popularity(self, count: int, *, max_popularity: int) -> float:
        if count <= 0 or max_popularity <= 0:
            return 0.0
        return min(math.log1p(count) / math.log1p(max_popularity), 1.0)

    def _build_excerpt(
        self,
        *,
        note: Note,
        hit_group: Sequence[Mapping[str, Any]],
    ) -> str:
        highlight = ""
        if hit_group:
            best_hit = max(hit_group, key=lambda item: float(item.get("score", 0.0) or 0.0))
            highlight = str(best_hit.get("highlight") or "")
        summary = (note.summary or "").strip()
        content = re.sub(r"\s+", " ", (note.content or "").strip())
        excerpt_source = summary or highlight or content
        excerpt = excerpt_source[:420].strip()
        if excerpt_source and len(excerpt_source) > len(excerpt):
            excerpt = f"{excerpt}..."
        return excerpt or note.title or "Untitled note"

    def _candidate_topic_key(self, candidate: OnboardingCandidateNote) -> str:
        if candidate.tags:
            return self._normalize_text(candidate.tags[0])
        if candidate.matched_queries:
            return self._normalize_text(candidate.matched_queries[0])
        return self._normalize_text(candidate.note_type or "note")

    def _is_near_duplicate(
        self,
        candidate: OnboardingCandidateNote,
        selected: Sequence[OnboardingCandidateNote],
    ) -> bool:
        normalized_title = self._normalize_text(candidate.title)
        normalized_excerpt = self._normalize_text(candidate.excerpt)
        candidate_tag_set = {self._normalize_text(tag) for tag in candidate.tags if tag}
        for existing in selected:
            existing_title = self._normalize_text(existing.title)
            existing_excerpt = self._normalize_text(existing.excerpt)
            if normalized_title and normalized_title == self._normalize_text(existing.title):
                return True
            if normalized_excerpt and normalized_excerpt == existing_excerpt:
                return True

            if not normalized_excerpt or len(normalized_excerpt) < 80 or len(existing_excerpt) < 80:
                continue

            title_similarity = SequenceMatcher(None, normalized_title, existing_title).ratio() if normalized_title else 0.0
            excerpt_similarity = SequenceMatcher(None, normalized_excerpt, existing_excerpt).ratio()
            existing_tag_set = {self._normalize_text(tag) for tag in existing.tags if tag}
            same_tag_signal = bool(candidate_tag_set and existing_tag_set and candidate_tag_set == existing_tag_set)

            if excerpt_similarity >= 0.97:
                return True
            if excerpt_similarity >= 0.94 and (title_similarity >= 0.85 or same_tag_signal):
                return True
        return False

    def _derive_week_theme(
        self,
        week_number: int,
        support_note_ids: Sequence[str],
        candidate_map: Mapping[str, OnboardingCandidateNote],
    ) -> str:
        if not support_note_ids:
            return f"Week {week_number}: Available workspace material"

        tags: Counter[str] = Counter()
        for note_id in support_note_ids:
            candidate = candidate_map.get(note_id)
            if candidate is None:
                continue
            tags.update(self._normalize_text(tag) for tag in candidate.tags if tag)

        if tags:
            top_tag = tags.most_common(1)[0][0].replace("-", " ").strip()
            if top_tag:
                return f"Week {week_number}: {top_tag.title()}"

        first_note = candidate_map.get(support_note_ids[0])
        if first_note is not None:
            return f"Week {week_number}: {first_note.title}"
        return f"Week {week_number}: Workspace context"

    def _build_fallback_summary(
        self,
        role_bundle: RoleQueryBundle,
        candidates: Sequence[OnboardingCandidateNote],
        insufficient_content: bool,
    ) -> str:
        note_count = len(candidates)
        if insufficient_content:
            return (
                f"This {role_bundle.role_label.lower()} onboarding plan is based on {note_count} relevant workspace notes. "
                "The source material is limited, so the plan focuses on the clearest available topics instead of pretending the workspace is more complete than it is."
            )
        return (
            f"This 4-week {role_bundle.role_label.lower()} onboarding plan is grounded in {note_count} workspace notes, "
            "starting with the strongest foundational material and then moving into execution details supported by the retrieved notes."
        )

    def _build_fallback_objectives(
        self,
        *,
        theme: str,
        support_note_ids: Sequence[str],
        candidate_map: Mapping[str, OnboardingCandidateNote],
    ) -> List[str]:
        if not support_note_ids:
            return [
                f"Review the available material related to {theme.lower()}",
                "Identify where the workspace content is thin or missing",
            ]

        first_note = candidate_map.get(support_note_ids[0])
        objectives = [
            f"Understand the workspace context behind {theme.lower()}",
            "Read the highest-signal notes tied to this week's theme",
        ]
        if first_note is not None:
            objectives.append(f"Be able to explain the main idea captured in '{first_note.title}'")
        return objectives[:3]

    def _build_fallback_checkpoints(
        self,
        *,
        support_note_ids: Sequence[str],
        candidate_map: Mapping[str, OnboardingCandidateNote],
    ) -> List[str]:
        if not support_note_ids:
            return ["What evidence is missing from the workspace, and what should you confirm with teammates?"]

        note_titles = [candidate_map[note_id].title for note_id in support_note_ids if note_id in candidate_map][:2]
        checkpoints = [
            "Can you explain the main concepts in your own words using the source notes?",
            "Which decisions, processes, or terms repeat across this week's readings?",
        ]
        if note_titles:
            checkpoints.append(f"Which note best explains '{note_titles[0]}', and what is still unclear?")
        return checkpoints[:3]

    def _build_fallback_reading_reason(self, candidate: OnboardingCandidateNote) -> str:
        if candidate.matched_queries:
            return f"Strong match for onboarding queries such as '{candidate.matched_queries[0]}'."
        if candidate.popularity_count > 0:
            return "Frequently accessed in this workspace, so it likely reflects important operational context."
        return "Useful grounding note for the available workspace material."

    def _determine_grounding_confidence(self, candidates: Sequence[OnboardingCandidateNote]) -> str:
        if not candidates:
            return "low"
        top_candidates = list(candidates[: min(len(candidates), 10)])
        avg_semantic = sum(candidate.semantic_score for candidate in top_candidates) / len(top_candidates)
        semantic_coverage = sum(1 for candidate in top_candidates if candidate.semantic_score >= 0.30) / len(top_candidates)
        query_variety = min(
            len(
                {
                    query
                    for candidate in top_candidates
                    for query in candidate.matched_queries[:1]
                }
            )
            / 4,
            1.0,
        )
        count_factor = min(len(candidates) / 10, 1.0)
        confidence_score = (
            (avg_semantic * 0.45)
            + (semantic_coverage * 0.25)
            + (query_variety * 0.20)
            + (count_factor * 0.10)
        )
        if len(candidates) >= 10 and confidence_score >= 0.68:
            return "high"
        if len(candidates) >= 5 and confidence_score >= 0.42:
            return "medium"
        return "low"

    def _is_generic_grounded_text(
        self,
        text: str,
        *,
        support_note_ids: Sequence[str],
        candidate_map: Mapping[str, OnboardingCandidateNote],
    ) -> bool:
        normalized = self._normalize_text(text)
        if not normalized:
            return True
        token_set = {token for token in normalized.split() if len(token) > 3}
        support_terms: set[str] = set()
        for note_id in support_note_ids[:4]:
            candidate = candidate_map.get(note_id)
            if candidate is None:
                continue
            support_terms.update(self._extract_candidate_terms(candidate))

        has_support_overlap = bool(token_set & support_terms)
        generic_hit = any(pattern in normalized for pattern in GENERIC_CURRICULUM_PATTERNS)
        if generic_hit and not has_support_overlap:
            return True
        return len(token_set) <= 1 and not has_support_overlap

    def _is_generic_reason(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        if not normalized:
            return True
        return any(
            pattern in normalized
            for pattern in (
                "helpful context",
                "important for onboarding",
                "useful background",
                "good starting point",
            )
        )

    def _term_supported_by_candidates(
        self,
        term: str,
        *,
        support_note_ids: Sequence[str],
        candidate_map: Mapping[str, OnboardingCandidateNote],
    ) -> bool:
        normalized_term = self._normalize_text(term)
        if not normalized_term:
            return False
        term_tokens = {token for token in normalized_term.split() if len(token) > 2}
        for note_id in support_note_ids:
            candidate = candidate_map.get(note_id)
            if candidate is None:
                continue
            candidate_terms = self._extract_candidate_terms(candidate)
            if normalized_term in candidate_terms or term_tokens.issubset(candidate_terms):
                return True
        return False

    def _extract_candidate_terms(self, candidate: OnboardingCandidateNote) -> set[str]:
        terms: set[str] = set()
        for value in [
            candidate.title,
            candidate.excerpt,
            *(candidate.tags or []),
            *(candidate.matched_queries or []),
        ]:
            normalized = self._normalize_text(value)
            if not normalized:
                continue
            terms.update(token for token in normalized.split() if len(token) > 2)
            terms.add(normalized)
        return terms

    def _match_candidate_by_title(
        self,
        title: Optional[str],
        candidates: Iterable[OnboardingCandidateNote],
    ) -> Optional[OnboardingCandidateNote]:
        normalized_title = self._normalize_text(title)
        if not normalized_title:
            return None
        best_match: Optional[OnboardingCandidateNote] = None
        best_score = 0.0
        for candidate in candidates:
            score = SequenceMatcher(None, normalized_title, self._normalize_text(candidate.title)).ratio()
            if score > best_score:
                best_score = score
                best_match = candidate
        if best_score >= 0.94:
            return best_match
        return None

    def _build_grounding_warnings(
        self,
        candidates: Sequence[OnboardingCandidateNote],
        confidence: str,
    ) -> List[str]:
        if not candidates:
            return ["Grounding confidence is low because no strong workspace notes were retrieved."]

        semantic_candidates = [candidate for candidate in candidates if candidate.semantic_score >= 0.28]
        if confidence == "low":
            return [
                "Grounding confidence is low because the workspace has few strong role-specific matches or relies heavily on popularity signals.",
            ]
        if len(semantic_candidates) < max(3, min(len(candidates), 5)):
            return [
                "This curriculum leans on a small set of role-matched notes, so verify gaps with teammates as you read.",
            ]
        return []

    @staticmethod
    def _clean_items(values: Sequence[str], *, limit: int) -> List[str]:
        cleaned: List[str] = []
        for value in values or []:
            text_value = OnboardingAcceleratorService._clean_text(value)
            if not text_value or text_value in cleaned:
                continue
            cleaned.append(text_value)
            if len(cleaned) >= limit:
                break
        return cleaned

    @staticmethod
    def _clean_text(value: Optional[str]) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip())

    @staticmethod
    def _normalize_text(value: Optional[str]) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _normalize_role_key(role: str) -> str:
        return OnboardingAcceleratorService._normalize_text(role)

    @staticmethod
    def _humanize_role_label(role: str) -> str:
        cleaned = OnboardingAcceleratorService._clean_text(role) or "General Onboarding"
        return " ".join(part.capitalize() for part in cleaned.split())

    def _extract_role_focus_terms(self, role: str) -> List[str]:
        tokens = [
            token
            for token in self._normalize_role_key(role).split()
            if token and token not in ROLE_QUERY_STOPWORDS
        ]
        return self._dedupe_texts(tokens)

    def _resolve_role_key(self, normalized_role: str) -> str:
        if normalized_role in ROLE_QUERY_LIBRARY:
            return normalized_role
        for key, config in ROLE_QUERY_LIBRARY.items():
            aliases = {self._normalize_role_key(alias) for alias in config.get("aliases", set())}
            if normalized_role in aliases:
                return key
        role_tokens = set(normalized_role.split())
        best_match = normalized_role
        best_score = 0
        for key, config in ROLE_QUERY_LIBRARY.items():
            keyword_tokens = {
                *{self._normalize_role_key(alias) for alias in config.get("aliases", set())},
                *{self._normalize_role_key(keyword) for keyword in config.get("keywords", set())},
                self._normalize_role_key(key),
            }
            keyword_parts = {
                token
                for keyword in keyword_tokens
                for token in keyword.split()
                if token
            }
            overlap = len(role_tokens & keyword_parts)
            if overlap > best_score:
                best_match = key
                best_score = overlap
        if best_score > 0:
            return best_match
        return normalized_role

    @staticmethod
    def _dedupe_texts(values: Sequence[str]) -> List[str]:
        deduped: List[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = OnboardingAcceleratorService._clean_text(value)
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def _format_datetime(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            text_value = value.strip()
            if not text_value:
                return None
            try:
                return datetime.fromisoformat(text_value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    @staticmethod
    def _truncate_text(value: Optional[str], *, limit: int) -> str:
        cleaned = OnboardingAcceleratorService._clean_text(value)
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(limit - 3, 0)].rstrip()}..."


_onboarding_accelerator_service: Optional[OnboardingAcceleratorService] = None


def get_onboarding_accelerator_service() -> OnboardingAcceleratorService:
    """Return the singleton onboarding accelerator service."""
    global _onboarding_accelerator_service
    if _onboarding_accelerator_service is None:
        _onboarding_accelerator_service = OnboardingAcceleratorService()
    return _onboarding_accelerator_service
