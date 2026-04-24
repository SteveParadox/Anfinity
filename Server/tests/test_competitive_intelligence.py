from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.database.models import CompetitiveAnalysis, CompetitiveSnapshot, CompetitiveSource
from app.services.competitive_intelligence import (
    CompetitiveIntelligenceService,
    ExtractedContent,
    JinaReaderExtractor,
    build_urgent_slack_payload,
    calibrate_analysis,
    classify_change,
    evaluate_conditions,
    normalize_extracted_text,
    prepare_diff_payload,
    sha256_text,
    should_trigger_immediate,
)


def test_normalization_and_hash_are_stable_for_reader_wrappers() -> None:
    raw = "Title: Pricing\r\nURL Source: https://example.com/pricing\r\n\r\nMarkdown Content:\n\nNew Pro plan   launched\n\n\n"
    normalized = normalize_extracted_text(raw)

    assert "URL Source:" not in normalized
    assert "Markdown Content:" not in normalized
    assert normalized == "Title: Pricing\n\nNew Pro plan launched"
    assert sha256_text(normalized) == sha256_text("Title: Pricing\n\nNew Pro plan launched")


def test_normalization_ignores_volatile_wrappers_and_tracking_urls() -> None:
    first = """
URL Source: https://example.com
Markdown Content:
Last updated: April 24, 2026
![hero](https://cdn.example.com/hero.png)
[Pricing page](https://example.com/pricing?utm_source=email)
Enterprise plan now includes SOC 2 reports.
"""
    second = """
URL Source: https://example.com
Markdown Content:
Last updated: April 25, 2026
![hero](https://cdn.example.com/hero-v2.png)
[Pricing page](https://example.com/pricing?utm_source=linkedin)
Enterprise plan now includes SOC 2 reports.
"""

    assert normalize_extracted_text(first) == normalize_extracted_text(second)


def test_jina_reader_url_is_plain_reader_prefix_without_browser_fallback() -> None:
    extractor = JinaReaderExtractor(base_url="https://r.jina.ai")

    assert extractor.build_reader_url("https://competitor.example/jobs") == "https://r.jina.ai/https://competitor.example/jobs"


def test_change_detection_short_circuits_identical_hashes() -> None:
    digest = sha256_text("same content")

    assert classify_change(None, digest) == "baseline"
    assert classify_change(digest, digest) == "unchanged"
    assert classify_change(digest, sha256_text("different content")) == "changed"


def test_diff_payload_focuses_on_changed_sections() -> None:
    previous = "Pricing\nStarter is $10\nCareers\nBackend engineer"
    current = "Pricing\nStarter is $19\nCareers\nBackend engineer\nMachine learning engineer\nML platform engineer"

    diff = prepare_diff_payload(previous, current, previous_hash="old", current_hash="new")

    payload = diff.to_model_payload()
    assert payload["current_hash"] == "new"
    assert any("Machine learning engineer" in section for section in diff.added_sections)
    assert any("Starter is $19" in item["after"] for item in diff.changed_sections)


def test_diff_payload_filters_boilerplate_and_caps_noise() -> None:
    previous = "\n".join(["Home", "Login", "Privacy Policy", "Feature A is available"])
    noisy_current = "\n".join(
        ["Home", "Login", "Privacy Policy", *[f"Cookie preference {index}" for index in range(50)], "Feature A is now generally available for enterprise teams"]
    )

    diff = prepare_diff_payload(previous, noisy_current, previous_hash="old", current_hash="new")
    payload = diff.to_model_payload()

    assert payload["stats"]["total_diff_chars"] <= 7_000
    assert not any("Privacy Policy" in section for section in diff.added_sections)
    assert any("generally available" in section.lower() for section in diff.added_sections + [item["after"] for item in diff.changed_sections])


def test_analysis_calibration_requires_evidence_and_sets_urgency() -> None:
    analysis = calibrate_analysis(
        {
            "headline_summary": "Pricing changed",
            "overall_urgency": 0.2,
            "findings": [
                {
                    "category": "pricing",
                    "signal": "Enterprise tier price increased",
                    "evidence": "Enterprise now starts at $999/month",
                    "likely_implication": "Packaging pressure in enterprise segment",
                    "urgency_score": 0.3,
                },
                {
                    "category": "hiring",
                    "signal": "Vague hiring motion",
                    "evidence": "",
                    "likely_implication": "Unsupported",
                    "urgency_score": 0.9,
                },
            ],
        }
    )

    assert analysis["overall_urgency"] >= 0.68
    assert analysis["urgency_label"] == "medium"
    assert analysis["findings"][1]["urgency_score"] <= 0.35


def test_urgency_calibration_does_not_trigger_high_without_concrete_evidence() -> None:
    analysis = calibrate_analysis(
        {
            "headline_summary": "Messaging changed",
            "overall_urgency": 0.95,
            "findings": [
                {
                    "category": "marketing",
                    "signal": "Copy changed on the homepage",
                    "evidence": "A new tagline was added",
                    "likely_implication": "Could be positioning exploration",
                    "urgency_score": 0.9,
                }
            ],
        }
    )

    assert analysis["overall_urgency"] < 0.75
    assert not analysis["should_trigger_immediate_workflow"]


def test_high_urgency_threshold_and_slack_payload_are_explicit() -> None:
    source = CompetitiveSource(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Acme",
        url="https://acme.example/pricing",
        config={"slack_channel_id": "C123"},
    )
    analysis = CompetitiveAnalysis(
        id=uuid.uuid4(),
        source_id=source.id,
        snapshot_id=uuid.uuid4(),
        workspace_id=source.workspace_id,
        content_hash="abc",
        findings=[
            {
                "category": "pricing",
                "signal": "New enterprise pricing page",
                "evidence": "Enterprise starts at $999/month",
                "urgency_score": 0.91,
            }
        ],
        headline_summary="Acme changed enterprise pricing",
        overall_urgency=0.91,
        urgency_label="high",
    )

    payload = build_urgent_slack_payload(source=source, analysis=analysis)

    assert should_trigger_immediate({"overall_urgency": 0.91})
    assert payload["channel_id"] == "C123"
    assert "Acme changed enterprise pricing" in payload["title"]
    assert "Enterprise starts at $999/month" in payload["context"][1]


def test_backend_condition_evaluator_supports_urgent_finding_context() -> None:
    context = {
        "competitive": {
            "analysis": {"urgency_label": "high", "overall_urgency": 0.88},
            "source": {"name": "Acme"},
        }
    }

    assert evaluate_conditions(
        [
            {"path": "competitive.analysis.urgency_label", "operator": "equals", "value": "high"},
            {"path": "competitive.analysis.overall_urgency", "operator": "greater_than", "value": 0.75},
        ],
        context,
    )


@pytest.mark.asyncio
async def test_unchanged_run_short_circuits_without_downstream_calls() -> None:
    source_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    normalized = "Stable pricing page\nEnterprise plan includes SOC 2 reports."
    digest = sha256_text(normalized)
    source = CompetitiveSource(
        id=source_id,
        workspace_id=workspace_id,
        name="Acme",
        url="https://acme.example/pricing",
        last_content_hash=digest,
        last_processed_hash=digest,
        is_active=True,
        run_status="idle",
    )
    db = _FakeDb()
    analyzer = _CountingAnalyzer()
    workflow = _CountingWorkflow()
    slack = _CountingSlack()
    service = _FakeLeaseCompetitiveService(
        db,
        source=source,
        extractor=_StaticExtractor(normalized),
        analyzer=analyzer,
        workflow_dispatcher=workflow,
        slack_notifier=slack,
    )

    result = await service.run_source(source_id)

    assert result.status == "unchanged"
    assert result.model_called is False
    assert analyzer.calls == 0
    assert workflow.calls == 0
    assert slack.calls == 0
    assert len(db.added) == 1
    snapshot = db.added[0]
    assert snapshot.extraction_status == "unchanged"
    assert snapshot.normalized_content is None


@pytest.mark.asyncio
async def test_analysis_failure_preserves_authoritative_hash_for_retry() -> None:
    source_id = uuid.uuid4()
    old_text = "Old pricing page"
    new_text = "New pricing page with enterprise price $999"
    old_hash = sha256_text(old_text)
    source = CompetitiveSource(
        id=source_id,
        workspace_id=uuid.uuid4(),
        name="Acme",
        url="https://acme.example/pricing",
        last_content_hash=old_hash,
        last_processed_hash=old_hash,
        is_active=True,
        run_status="idle",
    )
    db = _FakeDb()
    service = _FakeLeaseCompetitiveService(
        db,
        source=source,
        extractor=_StaticExtractor(new_text),
        analyzer=_FailingAnalyzer(),
        workflow_dispatcher=_CountingWorkflow(),
        slack_notifier=_CountingSlack(),
    )

    result = await service.run_source(source_id)

    assert result.status == "failed"
    assert source.last_content_hash == old_hash
    assert source.last_processed_hash == old_hash
    assert source.run_status == "failed"
    statuses = [item.extraction_status for item in db.added if isinstance(item, CompetitiveSnapshot)]
    assert "changed" in statuses
    assert "failed" in statuses


@pytest.mark.asyncio
async def test_slack_failure_does_not_corrupt_completed_analysis_state() -> None:
    source_id = uuid.uuid4()
    old_text = "Old pricing page"
    new_text = "New pricing page with enterprise price $999"
    old_hash = sha256_text(old_text)
    new_hash = sha256_text(normalize_extracted_text(new_text))
    source = CompetitiveSource(
        id=source_id,
        workspace_id=uuid.uuid4(),
        name="Acme",
        url="https://acme.example/pricing",
        last_content_hash=old_hash,
        last_processed_hash=old_hash,
        is_active=True,
        run_status="idle",
    )
    db = _FakeDb()
    workflow = _CountingWorkflow()
    service = _FakeLeaseCompetitiveService(
        db,
        source=source,
        extractor=_StaticExtractor(new_text),
        analyzer=_HighUrgencyAnalyzer(),
        workflow_dispatcher=workflow,
        slack_notifier=_FailingSlack(),
    )

    result = await service.run_source(source_id)

    assert result.status == "analyzed"
    assert result.slack_dispatched is False
    assert source.last_content_hash == new_hash
    assert source.last_processed_hash == new_hash
    analysis = next(item for item in db.added if isinstance(item, CompetitiveAnalysis))
    assert analysis.slack_dispatch_status == "failed"
    assert "Slack dispatch failed" in analysis.error_message


class _FakeDb:
    def __init__(self) -> None:
        self.added = []

    def add(self, item) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()


class _FakeLeaseCompetitiveService(CompetitiveIntelligenceService):
    def __init__(self, *args, source: CompetitiveSource, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._source = source

    async def _acquire_source_lease(self, source_id, now):
        self._source.run_status = "processing"
        self._source.last_checked_at = now
        return self._source

    async def _find_previous_snapshot(self, source_id, content_hash):
        return None

    async def _find_existing_analysis(self, source_id, content_hash):
        return None


class _StaticExtractor:
    def __init__(self, text: str) -> None:
        self.text = text

    async def extract(self, url: str) -> ExtractedContent:
        normalized = normalize_extracted_text(self.text)
        return ExtractedContent(
            source_url=url,
            reader_url=f"https://r.jina.ai/{url}",
            raw_text=self.text,
            normalized_text=normalized,
            content_hash=sha256_text(normalized),
            content_length=len(normalized),
            fetched_at=datetime.now(timezone.utc),
        )


class _CountingAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, **kwargs):
        self.calls += 1
        return {
            "headline_summary": "Changed",
            "findings": [],
            "overall_urgency": 0.1,
            "urgency_label": "low",
            "should_trigger_immediate_workflow": False,
        }


class _FailingAnalyzer(_CountingAnalyzer):
    async def analyze(self, **kwargs):
        self.calls += 1
        raise RuntimeError("model unavailable")


class _HighUrgencyAnalyzer(_CountingAnalyzer):
    async def analyze(self, **kwargs):
        self.calls += 1
        return {
            "headline_summary": "Enterprise pricing changed",
            "findings": [
                {
                    "category": "pricing",
                    "signal": "Enterprise pricing changed",
                    "evidence": "Enterprise price $999",
                    "likely_implication": "Potential enterprise packaging shift",
                    "urgency_score": 0.91,
                    "urgency_label": "high",
                }
            ],
            "overall_urgency": 0.91,
            "urgency_label": "high",
            "should_trigger_immediate_workflow": True,
        }


class _CountingWorkflow:
    def __init__(self) -> None:
        self.calls = 0

    async def emit_urgent_finding(self, *args, **kwargs):
        self.calls += 1
        return {"automations_matched": 0, "actions_executed": 0, "actions_skipped": 0, "errors": []}


class _CountingSlack:
    def __init__(self) -> None:
        self.calls = 0

    async def post_urgent(self, *args, **kwargs):
        self.calls += 1
        return {"status": "posted"}


class _FailingSlack(_CountingSlack):
    async def post_urgent(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("Slack unavailable")
