from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import onboarding as onboarding_api
from app.services import onboarding_accelerator as onboarding_module


class _FakeLLMService:
    async def async_openai_json(self, **kwargs):
        return {
            "summary": "Grounded summary",
            "weeks": [],
            "glossary": [],
        }


def _make_service(monkeypatch: pytest.MonkeyPatch) -> onboarding_module.OnboardingAcceleratorService:
    monkeypatch.setattr(onboarding_module, "get_semantic_search_service", lambda: SimpleNamespace())
    monkeypatch.setattr(onboarding_module, "get_postgresql_search_service", lambda: SimpleNamespace())
    return onboarding_module.OnboardingAcceleratorService(llm_service=_FakeLLMService())


def _make_note(
    *,
    title: str,
    content: str,
    summary: str | None = None,
    tags: list[str] | None = None,
    note_type: str = "note",
    word_count: int | None = None,
    age_days: int = 0,
):
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(days=age_days + 3)
    updated_at = now - timedelta(days=age_days)
    return SimpleNamespace(
        id=uuid4(),
        title=title,
        content=content,
        summary=summary,
        tags=tags or [],
        note_type=note_type,
        word_count=word_count if word_count is not None else len(content.split()),
        created_at=created_at,
        updated_at=updated_at,
    )


def _make_candidate(
    *,
    title: str,
    ranking_score: float,
    semantic_score: float = 0.5,
    popularity_score: float = 0.0,
    freshness_score: float = 0.5,
    completeness_score: float = 0.5,
    tags: list[str] | None = None,
    matched_queries: list[str] | None = None,
    popularity_count: int = 0,
    grounding_sources: list[str] | None = None,
) -> onboarding_module.OnboardingCandidateNote:
    return onboarding_module.OnboardingCandidateNote(
        note_id=str(uuid4()),
        title=title,
        excerpt=f"{title} excerpt",
        summary=None,
        tags=tags or [],
        note_type="note",
        semantic_score=semantic_score,
        popularity_score=popularity_score,
        freshness_score=freshness_score,
        completeness_score=completeness_score,
        ranking_score=ranking_score,
        matched_queries=matched_queries or [],
        query_hits=len(matched_queries or []),
        popularity_count=popularity_count,
        grounding_sources=grounding_sources or ["semantic"],
    )


def test_role_query_builder_normalizes_engineer_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    bundle = service.build_role_query_bundle("Software Engineer")

    assert bundle.normalized_role == "engineer"
    assert bundle.role_label == "Engineer"
    assert "technical architecture" in bundle.prioritized_queries
    assert "deployment process" in bundle.prioritized_queries
    assert "engineering standards" in bundle.prioritized_queries
    assert "workspace overview" in bundle.fallback_queries


def test_role_query_builder_uses_keyword_resolution_for_engineering_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)

    bundle = service.build_role_query_bundle("Platform Reliability Engineer")

    assert bundle.normalized_role == "engineer"
    assert bundle.role_label == "Engineer"
    assert "technical architecture" in bundle.prioritized_queries


@pytest.mark.asyncio
async def test_retrieve_candidate_notes_blends_semantic_and_popularity(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    engineer_bundle = service.build_role_query_bundle("engineer")
    note_semantic = _make_note(
        title="Architecture Handbook",
        content="System architecture service boundaries deployment patterns and ownership guides.",
        summary="Architecture overview",
        tags=["architecture"],
        age_days=2,
    )
    note_popular = _make_note(
        title="Team Rituals",
        content="Daily standups weekly reviews incident syncs release rituals and onboarding notes.",
        summary="Operations rhythm",
        tags=["operations"],
        age_days=5,
    )
    note_both = _make_note(
        title="Deployment Runbook",
        content="Deployment process rollback steps release checks and production validation workflow.",
        summary="Release and deployment guide",
        tags=["deployment"],
        age_days=1,
    )
    note_filtered = _make_note(
        title="Short Stub",
        content="tiny",
        summary=None,
        tags=[],
        age_days=30,
    )
    note_by_id = {
        str(note_semantic.id): note_semantic,
        str(note_popular.id): note_popular,
        str(note_both.id): note_both,
        str(note_filtered.id): note_filtered,
    }

    async def fake_semantic_hits(**kwargs):
        return {
            str(note_semantic.id): [
                {"query": "technical architecture", "score": 0.81, "highlight": "architecture service boundaries"}
            ],
            str(note_both.id): [
                {"query": "deployment process", "score": 0.84, "highlight": "deployment process rollback steps"}
            ],
            str(note_filtered.id): [
                {"query": "engineering standards", "score": 0.12, "highlight": "tiny"}
            ],
        }

    async def fake_popularity_stats(**kwargs):
        return {
            str(note_popular.id): {"interaction_count": 14, "last_interaction": None, "interaction_types": ["search_click"]},
            str(note_both.id): {"interaction_count": 9, "last_interaction": None, "interaction_types": ["search_click"]},
        }

    async def fake_load_notes(*, note_ids, **kwargs):
        return [note_by_id[note_id] for note_id in note_ids if note_id in note_by_id]

    monkeypatch.setattr(service, "_retrieve_semantic_hits", fake_semantic_hits)
    monkeypatch.setattr(service, "_retrieve_popularity_stats", fake_popularity_stats)
    monkeypatch.setattr(service, "_load_notes", fake_load_notes)

    candidates = await service.retrieve_candidate_notes(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role_bundle=engineer_bundle,
        db=SimpleNamespace(),
    )

    assert [candidate.title for candidate in candidates] == [
        "Deployment Runbook",
        "Architecture Handbook",
        "Team Rituals",
    ]
    assert candidates[0].grounding_sources == ["semantic", "popular"]
    assert candidates[1].grounding_sources == ["semantic"]
    assert candidates[2].grounding_sources == ["popular"]
    assert all(candidate.title != "Short Stub" for candidate in candidates)


@pytest.mark.asyncio
async def test_retrieve_candidate_notes_caps_to_fifty(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    bundle = service.build_role_query_bundle("manager")
    notes = []
    semantic_hits = {}
    for index in range(70):
        note = _make_note(
            title=f"Manager Note {index}",
            content=f"Planning cadence and team responsibilities note {index} with enough words to stay relevant.",
            summary=f"Summary {index}",
            tags=[f"topic-{index}"],
            age_days=index % 7,
        )
        notes.append(note)
        semantic_hits[str(note.id)] = [{"query": "planning cadence", "score": max(0.3, 0.95 - (index * 0.01)), "highlight": note.summary}]

    async def fake_semantic_hits(**kwargs):
        return semantic_hits

    async def fake_popularity_stats(**kwargs):
        return {}

    async def fake_load_notes(**kwargs):
        return notes

    monkeypatch.setattr(service, "_retrieve_semantic_hits", fake_semantic_hits)
    monkeypatch.setattr(service, "_retrieve_popularity_stats", fake_popularity_stats)
    monkeypatch.setattr(service, "_load_notes", fake_load_notes)

    candidates = await service.retrieve_candidate_notes(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role_bundle=bundle,
        db=SimpleNamespace(),
    )

    assert len(candidates) == 50
    assert candidates[0].ranking_score >= candidates[-1].ranking_score


@pytest.mark.asyncio
async def test_retrieve_candidate_notes_filters_low_signal_popular_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    bundle = service.build_role_query_bundle("engineer")
    strong_note = _make_note(
        title="Architecture Overview",
        content="Detailed service boundaries deployment topology standards and ownership model for the platform.",
        summary="Architecture notes",
        tags=["architecture"],
        age_days=1,
    )
    noisy_note = _make_note(
        title="Meeting Notes",
        content="follow up later",
        summary=None,
        tags=[],
        age_days=2,
    )

    async def fake_semantic_hits(**kwargs):
        return {
            str(strong_note.id): [{"query": "technical architecture", "score": 0.82, "highlight": strong_note.summary}],
        }

    async def fake_popularity_stats(**kwargs):
        return {
            str(noisy_note.id): {"interaction_count": 25, "last_interaction": datetime.now(timezone.utc).isoformat(), "interaction_types": ["search_click"]},
        }

    async def fake_load_notes(*, note_ids, **kwargs):
        note_by_id = {
            str(strong_note.id): strong_note,
            str(noisy_note.id): noisy_note,
        }
        return [note_by_id[note_id] for note_id in note_ids if note_id in note_by_id]

    monkeypatch.setattr(service, "_retrieve_semantic_hits", fake_semantic_hits)
    monkeypatch.setattr(service, "_retrieve_popularity_stats", fake_popularity_stats)
    monkeypatch.setattr(service, "_load_notes", fake_load_notes)

    candidates = await service.retrieve_candidate_notes(
        workspace_id=uuid4(),
        user_id=uuid4(),
        role_bundle=bundle,
        db=SimpleNamespace(),
    )

    assert [candidate.title for candidate in candidates] == ["Architecture Overview"]


@pytest.mark.asyncio
async def test_generate_curriculum_draft_uses_compact_serialized_context(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _CapturingLLM:
        async def async_openai_json(self, **kwargs):
            captured.update(kwargs)
            return {"summary": "Grounded", "weeks": [], "glossary": []}

    monkeypatch.setattr(onboarding_module, "get_semantic_search_service", lambda: SimpleNamespace())
    monkeypatch.setattr(onboarding_module, "get_postgresql_search_service", lambda: SimpleNamespace())
    service = onboarding_module.OnboardingAcceleratorService(llm_service=_CapturingLLM())
    bundle = service.build_role_query_bundle("engineer")
    candidates = [
        onboarding_module.OnboardingCandidateNote(
            note_id=str(uuid4()),
            title="Architecture Overview",
            excerpt="x" * 600,
            summary="y" * 400,
            tags=["architecture", "services", "ownership", "deployment", "extra-tag"],
            note_type="document",
            semantic_score=0.85,
            popularity_score=0.42,
            freshness_score=0.61,
            completeness_score=0.84,
            ranking_score=0.8,
            matched_queries=["technical architecture", "deployment process", "engineering standards"],
            query_hits=3,
            popularity_count=9,
            grounding_sources=["semantic", "popular"],
        )
    ]

    await service._generate_curriculum_draft(bundle, candidates, insufficient_content=False)

    prompt = captured["user_prompt"]
    candidate_json = prompt.split("Candidate notes JSON:\n", 1)[1].split("\n\nReturn a JSON object", 1)[0]
    assert '"context"' in candidate_json
    assert '"summary"' not in candidate_json
    assert '"matched_queries":["technical architecture","deployment process"]' in candidate_json
    assert len(prompt) < 5000


def test_repair_curriculum_rejects_hallucinated_note_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    role_bundle = service.build_role_query_bundle("designer")
    candidates = [
        _make_candidate(title="Design System", ranking_score=0.8, tags=["design-system"], matched_queries=["design system"]),
        _make_candidate(title="Research Notes", ranking_score=0.7, tags=["research"], matched_queries=["user research findings"]),
        _make_candidate(title="Accessibility Review", ranking_score=0.65, tags=["accessibility"], matched_queries=["accessibility standards"]),
    ]
    candidate_ids = {candidate.note_id for candidate in candidates}

    repaired = service._repair_curriculum(
        role_bundle=role_bundle,
        candidates=candidates,
        raw_payload={
            "summary": "Grounded summary",
            "weeks": [
                {
                    "week_number": 1,
                    "theme": "Generic intro",
                    "support_note_ids": ["missing-note-id"],
                    "reading_list": [{"note_id": "missing-note-id", "reason": "Hallucinated"}],
                    "objectives": ["Understand the basics"],
                    "concept_checkpoints": ["What matters?"],
                }
            ],
            "glossary": [
                {"term": "Ghost System", "definition": "Invented", "support_note_ids": ["missing-note-id"]}
            ],
        },
        warnings=[],
    )

    assert len(repaired.weeks) == 4
    assert repaired.weeks[0].reading_list
    assert repaired.weeks[0].reading_list[0].note_id in candidate_ids
    assert set(repaired.grounding.used_note_ids).issubset(candidate_ids)
    assert all(entry.support_note_ids for entry in repaired.glossary) or not repaired.glossary


def test_repair_curriculum_replaces_generic_theme_and_unsupported_glossary(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    role_bundle = service.build_role_query_bundle("engineer")
    candidates = [
        onboarding_module.OnboardingCandidateNote(
            note_id=str(uuid4()),
            title="System Architecture",
            excerpt="Service boundaries, deployment flow, and ownership model for the core platform.",
            summary="Architecture and deployment",
            tags=["architecture"],
            note_type="document",
            semantic_score=0.88,
            popularity_score=0.33,
            freshness_score=0.72,
            completeness_score=0.85,
            ranking_score=0.83,
            matched_queries=["technical architecture"],
            query_hits=1,
            popularity_count=5,
            grounding_sources=["semantic"],
        )
    ]

    repaired = service._repair_curriculum(
        role_bundle=role_bundle,
        candidates=candidates,
        raw_payload={
            "summary": "Introduction to the company and getting started.",
            "weeks": [
                {
                    "week_number": 1,
                    "theme": "Introduction",
                    "support_note_ids": [candidates[0].note_id],
                    "reading_list": [{"note_id": candidates[0].note_id, "reason": "Helpful context"}],
                    "objectives": ["Understand the basics"],
                    "concept_checkpoints": ["What did you learn?"],
                }
            ],
            "glossary": [
                {"term": "Ghost Platform", "definition": "Invented concept", "support_note_ids": [candidates[0].note_id]}
            ],
        },
        warnings=[],
    )

    assert repaired.weeks[0].theme != "Introduction"
    assert repaired.weeks[0].reading_list[0].reason != "Helpful context"
    assert all(entry.term != "Ghost Platform" for entry in repaired.glossary)


@pytest.mark.asyncio
async def test_generate_curriculum_degrades_honestly_for_sparse_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service(monkeypatch)
    role_bundle = service.build_role_query_bundle("sales")

    async def fake_retrieve_candidate_notes(**kwargs):
        return []

    monkeypatch.setattr(service, "retrieve_candidate_notes", fake_retrieve_candidate_notes)

    curriculum = await service.generate_curriculum(
        workspace_id=uuid4(),
        user=SimpleNamespace(id=uuid4()),
        role=role_bundle.role_input or "sales",
        db=SimpleNamespace(),
    )

    assert curriculum.grounding.insufficient_content is True
    assert curriculum.grounding.grounding_confidence == "low"
    assert "limited" in curriculum.summary.lower() or "minimal" in curriculum.summary.lower()
    assert len(curriculum.weeks) == 4


@pytest.mark.asyncio
async def test_onboarding_api_enforces_workspace_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_permission(*, section, action, **kwargs):
        calls.append((section.value if hasattr(section, "value") else str(section), action))
        return None

    expected = onboarding_module.OnboardingCurriculum(
        role_input="engineer",
        role="Engineer",
        normalized_role="engineer",
        summary="Grounded",
        weeks=[
            onboarding_module.OnboardingWeek(
                week_number=index,
                theme=f"Week {index}",
                objectives=["Read the source notes"],
                support_note_ids=[],
                reading_list=[],
                concept_checkpoints=["Checkpoint"],
            )
            for index in range(1, 5)
        ],
        glossary=[],
        grounding=onboarding_module.OnboardingGroundingMetadata(),
        candidate_notes=[],
    )

    class _FakeService:
        async def generate_curriculum(self, **kwargs):
            return expected

    monkeypatch.setattr(onboarding_api, "ensure_workspace_permission", fake_permission)
    monkeypatch.setattr(onboarding_api, "get_onboarding_accelerator_service", lambda: _FakeService())

    response = await onboarding_api.generate_onboarding_curriculum(
        payload=onboarding_api.OnboardingCurriculumRequest(workspace_id=uuid4(), role="engineer"),
        current_user=SimpleNamespace(id=uuid4()),
        db=SimpleNamespace(),
    )

    assert response == expected
    assert calls == [("notes", "view"), ("chat", "create")]
