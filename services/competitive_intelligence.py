"""Competitive intelligence extraction, change detection, and alert routing.

The pipeline is intentionally change-first:

1. Extract readable page text through Jina Reader.
2. Normalize text deterministically and compute SHA-256.
3. Short-circuit unchanged content before any model, note, workflow, or Slack work.
4. Analyze only changed snapshots with GPT-4o-mini.
5. Dispatch high-urgency findings immediately, with duplicate-alert protection.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import (
    Automation,
    CompetitiveAnalysis,
    CompetitiveSnapshot,
    CompetitiveSource,
    Connector,
)
from app.services.automation_registry import BACKEND_ACTION_TYPES
from app.services.automations import execute_backend_action
from app.services.integrations.orchestrator import post_slack_message
from app.services.integrations.providers import IntegrationProvider
from app.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

COMPETITIVE_URGENT_TRIGGER = "competitive_intelligence.urgent_finding"
ALLOWED_URL_SCHEMES = {"http", "https"}
MAX_DIFF_ITEMS = 14
MAX_DIFF_ITEM_CHARS = 1_400
MAX_DIFF_TOTAL_CHARS = 7_000
MAX_PROMPT_CHARS = 18_000
GENERIC_LINES = {
    "home",
    "login",
    "log in",
    "sign up",
    "privacy policy",
    "terms of service",
    "cookie policy",
    "all rights reserved",
}
HIGH_URGENCY_CATEGORIES = {"pricing", "product_launch", "positioning", "security", "compliance", "enterprise"}
HIGH_URGENCY_KEYWORDS = {
    "price",
    "pricing",
    "launch",
    "launched",
    "announces",
    "enterprise",
    "security",
    "soc 2",
    "hipaa",
    "compliance",
    "general availability",
    "ga",
    "acquisition",
    "funding",
}
VOLATILE_LINE_RE = re.compile(
    r"^(last\s+updated|updated|generated|retrieved|accessed|copyright|©)\b[:\s-]*",
    re.IGNORECASE,
)
DATE_ONLY_RE = re.compile(
    r"^(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+\d{4}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedContent:
    source_url: str
    reader_url: str
    raw_text: str
    normalized_text: str
    content_hash: str
    content_length: int
    fetched_at: datetime


@dataclass(frozen=True)
class DiffPayload:
    previous_hash: Optional[str]
    current_hash: str
    added_sections: list[str] = field(default_factory=list)
    removed_sections: list[str] = field(default_factory=list)
    changed_sections: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "added_sections": self.added_sections,
            "removed_sections": self.removed_sections,
            "changed_sections": self.changed_sections,
            "stats": self.stats,
        }


@dataclass(frozen=True)
class RunResult:
    status: str
    source_id: str
    snapshot_id: Optional[str] = None
    analysis_id: Optional[str] = None
    content_hash: Optional[str] = None
    reason: Optional[str] = None
    model_called: bool = False
    workflow_dispatched: bool = False
    slack_dispatched: bool = False


def normalize_extracted_text(text: str) -> str:
    """Normalize Jina Reader output before hashing.

    This keeps substantive markdown/text stable while removing reader wrappers
    and whitespace volatility that should not create false positive changes.
    """

    if not text:
        return ""

    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    lines: list[str] = []
    for raw_line in value.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        line = _canonicalize_content_line(line)
        if not line:
            lines.append("")
            continue
        lowered = line.lower()
        if lowered.startswith("url source:"):
            continue
        if lowered in {"markdown content:", "text content:", "page content:"}:
            continue
        if lowered in GENERIC_LINES:
            continue
        if _is_volatile_line(line):
            continue
        lines.append(line)

    collapsed = "\n".join(lines)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip()


def _canonicalize_content_line(line: str) -> str:
    if not line:
        return ""
    line = re.sub(r"!\[[^\]]*]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)]\(([^)]*)\)", r"\1", line)
    line = re.sub(r"\s*[|]\s*", " | ", line)
    line = re.sub(r"^[\-*•]\s+", "- ", line)
    line = re.sub(r"\bhttps?://\S+", _canonicalize_url_match, line)
    return re.sub(r"\s+", " ", line).strip()


def _canonicalize_url_match(match: re.Match[str]) -> str:
    url = match.group(0).rstrip(".,;)")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _is_volatile_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if VOLATILE_LINE_RE.match(stripped):
        return True
    if DATE_ONLY_RE.match(stripped):
        return True
    if re.fullmatch(r"\d{1,2}[:/.-]\d{1,2}[:/.-]\d{2,4}", stripped):
        return True
    return False


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_change(previous_hash: Optional[str], current_hash: str) -> str:
    if not previous_hash:
        return "baseline"
    if previous_hash == current_hash:
        return "unchanged"
    return "changed"


class JinaReaderExtractor:
    """Readable page extraction backed by r.jina.ai, without browser overhead."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        max_raw_bytes: int = 5_242_880,
    ) -> None:
        self.base_url = (base_url or settings.COMPETITIVE_JINA_READER_BASE_URL).rstrip("/")
        self.timeout_seconds = int(timeout_seconds or settings.COMPETITIVE_JINA_TIMEOUT_SECONDS)
        self.max_retries = int(max_retries if max_retries is not None else settings.COMPETITIVE_JINA_MAX_RETRIES)
        self.max_raw_bytes = max_raw_bytes

    async def extract(self, url: str) -> ExtractedContent:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
            raise ValueError("Competitive source URL must be a fully qualified http(s) URL")

        reader_url = self.build_reader_url(url)
        headers = {
            "Accept": "text/plain",
            "User-Agent": "AnfinityCompetitiveIntelligence/1.0",
        }
        timeout = httpx.Timeout(connect=5.0, read=float(self.timeout_seconds), write=10.0, pool=5.0)
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
                    response = await client.get(reader_url)
                if response.status_code == 429 and attempt <= self.max_retries:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"), attempt)
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code >= 400:
                    raise ValueError(f"Jina Reader returned HTTP {response.status_code}")
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > self.max_raw_bytes:
                    raise ValueError(f"Jina Reader response exceeds {self.max_raw_bytes} bytes")
                raw_text = response.text
                if len(raw_text.encode("utf-8")) > self.max_raw_bytes:
                    raise ValueError(f"Jina Reader response exceeds {self.max_raw_bytes} bytes")
                normalized = normalize_extracted_text(raw_text)
                if not normalized:
                    raise ValueError("Jina Reader returned empty readable content")
                return ExtractedContent(
                    source_url=url,
                    reader_url=reader_url,
                    raw_text=raw_text,
                    normalized_text=normalized,
                    content_hash=sha256_text(normalized),
                    content_length=len(normalized),
                    fetched_at=datetime.now(timezone.utc),
                )
            except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if isinstance(exc, ValueError):
                    raise
                if attempt <= self.max_retries:
                    await asyncio.sleep(2**attempt)

        raise ValueError(f"Failed to extract {url}: {last_exc}") from last_exc

    def build_reader_url(self, url: str) -> str:
        return f"{self.base_url}/{url.strip()}"


def prepare_diff_payload(
    previous_text: str,
    current_text: str,
    *,
    previous_hash: Optional[str],
    current_hash: str,
) -> DiffPayload:
    previous_lines = _meaningful_lines(previous_text)
    current_lines = _meaningful_lines(current_text)
    matcher = difflib.SequenceMatcher(a=previous_lines, b=current_lines, autojunk=False)

    added: list[str] = []
    removed: list[str] = []
    changed: list[dict[str, str]] = []
    total_chars = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = _join_limited(previous_lines[i1:i2])
        after = _join_limited(current_lines[j1:j2])
        if not _is_informative_diff_item(before, after):
            continue
        if tag == "insert" and after:
            added.append(after)
            total_chars += len(after)
        elif tag == "delete" and before:
            removed.append(before)
            total_chars += len(before)
        elif tag == "replace" and (before or after):
            changed.append({"before": before, "after": after})
            total_chars += len(before) + len(after)
        if len(added) + len(removed) + len(changed) >= MAX_DIFF_ITEMS or total_chars >= MAX_DIFF_TOTAL_CHARS:
            break

    added = _rank_diff_strings(_dedupe_strings(added))[:MAX_DIFF_ITEMS]
    removed = _rank_diff_strings(_dedupe_strings(removed))[:MAX_DIFF_ITEMS]
    changed = _rank_changed_sections(changed)[:MAX_DIFF_ITEMS]
    return DiffPayload(
        previous_hash=previous_hash,
        current_hash=current_hash,
        added_sections=added,
        removed_sections=removed,
        changed_sections=changed,
        stats={
            "previous_lines": len(previous_lines),
            "current_lines": len(current_lines),
            "added_sections": len(added),
            "removed_sections": len(removed),
            "changed_sections": len(changed),
            "total_diff_chars": sum(len(item) for item in added + removed)
            + sum(len(item.get("before", "")) + len(item.get("after", "")) for item in changed),
        },
    )


class CompetitiveDiffAnalyzer:
    """GPT-4o-mini structured strategic signal extraction."""

    async def analyze(
        self,
        *,
        source_url: str,
        source_name: str,
        previous_hash: Optional[str],
        current_hash: str,
        diff: DiffPayload,
    ) -> dict[str, Any]:
        llm = get_llm_service(
            openai_model=settings.COMPETITIVE_ANALYSIS_MODEL,
            primary_provider="openai",
            use_fallback=False,
        )
        payload = diff.to_model_payload()
        system_prompt = (
            "You are a competitive-intelligence analyst. Analyze only the supplied page diff. "
            "Extract strategic signals grounded in concrete observed changes. Inference is allowed "
            "only when tied to evidence, such as '5 ML engineer job postings = likely AI push'. "
            "Do not use generic competitor lore. Return only valid JSON."
        )
        user_prompt = f"""Source: {source_name}
URL: {source_url}
Previous hash: {previous_hash or "none"}
Current hash: {current_hash}

Diff payload, capped to changed sections:
{json.dumps(payload, ensure_ascii=False)[:MAX_PROMPT_CHARS]}

Return a JSON object with exactly these keys:
- source_url
- headline_summary
- findings: array of objects with category, signal, evidence, likely_implication, urgency_score, urgency_label
- overall_urgency
- urgency_label
- should_trigger_immediate_workflow
- recommended_next_action

Urgency scoring:
- 0.80-1.00 high: pricing changes, major launches, positioning pivots, security/compliance/enterprise moves, large strategic hiring waves.
- 0.45-0.79 medium: meaningful feature, hiring, messaging, integration, or packaging changes.
- 0.00-0.44 low: small copy edits, routine pages, weak or ambiguous signals.

Evidence must quote or paraphrase only changed text visible in the diff."""
        analysis = await llm.async_openai_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=settings.COMPETITIVE_ANALYSIS_MODEL,
            temperature=0.1,
            max_tokens=1400,
        )
        return calibrate_analysis(analysis)


def calibrate_analysis(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Clamp model output and apply deterministic urgency floors/caps."""

    findings = []
    max_finding_score = 0.0
    for item in analysis.get("findings") or []:
        if not isinstance(item, Mapping):
            continue
        category = str(item.get("category") or "other").strip().lower()[:80] or "other"
        score = _clamp_float(item.get("urgency_score"), 0.0, 1.0)
        evidence = str(item.get("evidence") or "").strip()
        signal = str(item.get("signal") or "").strip()
        implication = str(item.get("likely_implication") or "").strip()
        if category in HIGH_URGENCY_CATEGORIES and evidence and signal:
            score = max(score, 0.68)
        if not evidence:
            score = min(score, 0.35)
        if evidence and not _has_high_urgency_evidence(evidence, signal, category):
            score = min(score, 0.74)
        label = urgency_label(score)
        findings.append(
            {
                "category": category,
                "signal": signal[:700],
                "evidence": evidence[:900],
                "likely_implication": implication[:700],
                "urgency_score": score,
                "urgency_label": label,
            }
        )
        max_finding_score = max(max_finding_score, score)

    overall = max(_clamp_float(analysis.get("overall_urgency"), 0.0, 1.0), max_finding_score)
    if not findings:
        overall = min(overall, 0.3)
    if findings and not any(item["urgency_score"] >= settings.COMPETITIVE_HIGH_URGENCY_THRESHOLD for item in findings):
        overall = min(overall, 0.74)
    label = urgency_label(overall)
    return {
        "source_url": str(analysis.get("source_url") or ""),
        "headline_summary": str(analysis.get("headline_summary") or "Competitive page changed").strip()[:500],
        "findings": findings[:8],
        "overall_urgency": overall,
        "urgency_label": label,
        "should_trigger_immediate_workflow": bool(overall >= settings.COMPETITIVE_HIGH_URGENCY_THRESHOLD),
        "recommended_next_action": str(analysis.get("recommended_next_action") or "").strip()[:500],
    }


def urgency_label(score: float) -> str:
    if score >= settings.COMPETITIVE_HIGH_URGENCY_THRESHOLD:
        return "high"
    if score >= settings.COMPETITIVE_MEDIUM_URGENCY_THRESHOLD:
        return "medium"
    return "low"


def should_trigger_immediate(analysis: Mapping[str, Any]) -> bool:
    return _clamp_float(analysis.get("overall_urgency"), 0.0, 1.0) >= settings.COMPETITIVE_HIGH_URGENCY_THRESHOLD


def build_urgent_slack_payload(*, source: CompetitiveSource, analysis: CompetitiveAnalysis) -> dict[str, Any]:
    findings = list(analysis.findings or [])[:3]
    finding_lines = [
        f"- *{str(item.get('category') or 'signal').title()}*: {str(item.get('signal') or '').strip()}"
        for item in findings
        if str(item.get("signal") or "").strip()
    ]
    evidence = [
        f"Evidence: {str(item.get('evidence') or '').strip()[:240]}"
        for item in findings[:2]
        if str(item.get("evidence") or "").strip()
    ]
    body_parts = [
        f"*Source:* {source.name}",
        f"*Urgency:* {analysis.urgency_label} ({analysis.overall_urgency:.2f})",
        *(finding_lines or ["- Competitive page changed; review analysis details."]),
    ]
    return {
        "channel_id": str((source.config or {}).get("slack_channel_id") or ""),
        "title": f"Urgent competitive signal: {analysis.headline_summary or source.name}",
        "body": "\n".join(body_parts),
        "context": [source.url, *evidence],
        "buttons": [{"text": "Open source", "url": source.url, "action_id": "open_source"}],
        "unfurl_links": False,
    }


def _workflow_dispatch_status(result: Mapping[str, Any]) -> str:
    actions_executed = int(result.get("actions_executed") or 0)
    automations_matched = int(result.get("automations_matched") or 0)
    if result.get("errors") and actions_executed > 0:
        return "partial_failure"
    if result.get("errors"):
        return "failed"
    if actions_executed > 0:
        return "dispatched"
    if automations_matched == 0:
        return "no_matching_automation"
    return "no_backend_actions"


class CompetitiveWorkflowDispatcher:
    """Immediate backend automation dispatch for urgent competitive findings."""

    async def emit_urgent_finding(
        self,
        db: AsyncSession,
        *,
        source: CompetitiveSource,
        analysis: CompetitiveAnalysis,
    ) -> dict[str, Any]:
        context = _automation_context(source=source, analysis=analysis)
        rows = await db.execute(
            select(Automation).where(
                Automation.workspace_id == source.workspace_id,
                Automation.trigger_type == COMPETITIVE_URGENT_TRIGGER,
                Automation.enabled.is_(True),
            )
        )
        automations = list(rows.scalars().all())
        executed = 0
        skipped = 0
        errors: list[str] = []

        for automation in automations:
            if not evaluate_conditions(list(automation.conditions or []), context):
                skipped += 1
                continue
            for action in list(automation.actions or []):
                action_type = str(action.get("type") or "")
                if action_type not in BACKEND_ACTION_TYPES:
                    skipped += 1
                    continue
                try:
                    await execute_backend_action(
                        db,
                        action_type=action_type,
                        config=action.get("config") or {},
                        context=context,
                        automation_id=str(automation.id),
                        action_id=str(action.get("id") or ""),
                    )
                    executed += 1
                except Exception as exc:
                    logger.exception("Competitive automation action failed")
                    errors.append(str(exc))

        return {
            "automations_matched": len(automations),
            "actions_executed": executed,
            "actions_skipped": skipped,
            "errors": errors,
        }


class CompetitiveSlackNotifier:
    """Immediate Slack delivery through the configured workspace Slack connector."""

    async def post_urgent(
        self,
        db: AsyncSession,
        *,
        source: CompetitiveSource,
        analysis: CompetitiveAnalysis,
    ) -> dict[str, Any]:
        connector = (
            await db.execute(
                select(Connector)
                .where(
                    Connector.workspace_id == source.workspace_id,
                    Connector.connector_type == IntegrationProvider.SLACK.value,
                    Connector.is_active == 1,
                )
                .order_by(Connector.created_at.asc())
            )
        ).scalar_one_or_none()
        if connector is None:
            return {"status": "skipped", "reason": "no_active_slack_connector"}

        payload = build_urgent_slack_payload(source=source, analysis=analysis)
        if not payload.get("channel_id"):
            payload["channel_id"] = str((connector.config or {}).get("default_channel_id") or "")
        if not payload.get("channel_id"):
            return {"status": "skipped", "reason": "no_slack_channel_configured"}

        result = await post_slack_message(db, connector.id, payload)
        return {"status": "posted", "result": dict(result)}


class CompetitiveIntelligenceService:
    """End-to-end competitive intelligence runner."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        extractor: Optional[JinaReaderExtractor] = None,
        analyzer: Optional[CompetitiveDiffAnalyzer] = None,
        workflow_dispatcher: Optional[CompetitiveWorkflowDispatcher] = None,
        slack_notifier: Optional[CompetitiveSlackNotifier] = None,
    ) -> None:
        self.db = db
        self.extractor = extractor or JinaReaderExtractor()
        self.analyzer = analyzer or CompetitiveDiffAnalyzer()
        self.workflow_dispatcher = workflow_dispatcher or CompetitiveWorkflowDispatcher()
        self.slack_notifier = slack_notifier or CompetitiveSlackNotifier()

    async def run_source(self, source_id: UUID | str) -> RunResult:
        now = datetime.now(timezone.utc)
        source_uuid = UUID(str(source_id))
        source = await self._acquire_source_lease(source_uuid, now)
        if source is None:
            return RunResult(status="skipped", source_id=str(source_uuid), reason="source_processing_inactive_or_missing")

        try:
            extracted = await self.extractor.extract(source.url)
            decision = classify_change(source.last_content_hash, extracted.content_hash)

            if decision == "baseline":
                snapshot = self._snapshot(source, extracted, status="baseline", changed=False, store_content=True)
                source.last_content_hash = extracted.content_hash
                source.last_processed_hash = extracted.content_hash
                source.last_successful_fetch_at = extracted.fetched_at
                source.last_error = None
                source.run_status = "idle"
                source.lease_until = None
                await self.db.flush()
                return RunResult(
                    status="baseline",
                    source_id=str(source.id),
                    snapshot_id=str(snapshot.id),
                    content_hash=extracted.content_hash,
                    reason="first_successful_snapshot_recorded_without_analysis",
                )

            if decision == "unchanged":
                snapshot = self._snapshot(source, extracted, status="unchanged", changed=False, store_content=False)
                source.last_successful_fetch_at = extracted.fetched_at
                source.last_error = None
                source.run_status = "idle"
                source.lease_until = None
                await self.db.flush()
                return RunResult(
                    status="unchanged",
                    source_id=str(source.id),
                    snapshot_id=str(snapshot.id),
                    content_hash=extracted.content_hash,
                    reason="content_hash_unchanged_no_downstream_calls",
                )

            existing_analysis = await self._find_existing_analysis(source.id, extracted.content_hash)
            snapshot = self._snapshot(source, extracted, status="changed", changed=True, store_content=True)
            await self.db.flush()
            previous_snapshot = await self._find_previous_snapshot(source.id, source.last_content_hash)
            previous_text = previous_snapshot.normalized_content if previous_snapshot is not None else ""
            diff = prepare_diff_payload(
                previous_text or "",
                extracted.normalized_text,
                previous_hash=source.last_content_hash,
                current_hash=extracted.content_hash,
            )
            source.last_successful_fetch_at = extracted.fetched_at
            source.last_changed_at = extracted.fetched_at
            source.last_error = None

            if existing_analysis is not None:
                source.last_content_hash = extracted.content_hash
                source.last_processed_hash = extracted.content_hash
                source.run_status = "idle"
                source.lease_until = None
                await self.db.flush()
                return RunResult(
                    status="already_processed",
                    source_id=str(source.id),
                    snapshot_id=str(snapshot.id),
                    analysis_id=str(existing_analysis.id),
                    content_hash=extracted.content_hash,
                    reason="analysis_for_hash_already_exists",
                )

            analysis_payload = await self.analyzer.analyze(
                source_url=source.url,
                source_name=source.name,
                previous_hash=diff.previous_hash,
                current_hash=diff.current_hash,
                diff=diff,
            )
            analysis = CompetitiveAnalysis(
                source_id=source.id,
                snapshot_id=snapshot.id,
                previous_snapshot_id=previous_snapshot.id if previous_snapshot is not None else None,
                workspace_id=source.workspace_id,
                content_hash=extracted.content_hash,
                previous_content_hash=diff.previous_hash,
                diff_payload=diff.to_model_payload(),
                analysis_payload=analysis_payload,
                findings=list(analysis_payload.get("findings") or []),
                headline_summary=str(analysis_payload.get("headline_summary") or ""),
                overall_urgency=float(analysis_payload.get("overall_urgency") or 0.0),
                urgency_label=str(analysis_payload.get("urgency_label") or "low"),
                should_trigger_immediate_workflow=should_trigger_immediate(analysis_payload),
                workflow_dispatch_status="not_required",
                slack_dispatch_status="not_required",
                alert_dedupe_key=self._alert_dedupe_key(source.id, extracted.content_hash)
                if should_trigger_immediate(analysis_payload)
                else None,
                model_used=settings.COMPETITIVE_ANALYSIS_MODEL,
            )
            self.db.add(analysis)
            await self.db.flush()

            workflow_dispatched = False
            slack_dispatched = False
            if analysis.should_trigger_immediate_workflow:
                workflow_result = await self.workflow_dispatcher.emit_urgent_finding(
                    self.db,
                    source=source,
                    analysis=analysis,
                )
                analysis.workflow_dispatch_status = _workflow_dispatch_status(workflow_result)
                try:
                    slack_result = await self.slack_notifier.post_urgent(self.db, source=source, analysis=analysis)
                    analysis.slack_dispatch_status = str(slack_result.get("status") or "unknown")
                except Exception as exc:
                    logger.exception("Competitive Slack urgent delivery failed")
                    analysis.slack_dispatch_status = "failed"
                    analysis.error_message = f"Slack dispatch failed: {exc}"
                workflow_dispatched = analysis.workflow_dispatch_status in {"dispatched", "partial_failure"}
                slack_dispatched = analysis.slack_dispatch_status == "posted"

            source.last_processed_hash = extracted.content_hash
            source.last_content_hash = extracted.content_hash
            source.run_status = "idle"
            source.lease_until = None
            await self.db.flush()
            return RunResult(
                status="analyzed",
                source_id=str(source.id),
                snapshot_id=str(snapshot.id),
                analysis_id=str(analysis.id),
                content_hash=extracted.content_hash,
                model_called=True,
                workflow_dispatched=workflow_dispatched,
                slack_dispatched=slack_dispatched,
            )
        except Exception as exc:
            logger.exception("Competitive intelligence run failed for source %s", source.id)
            source.run_status = "failed"
            source.lease_until = None
            source.last_error = str(exc)
            self.db.add(
                CompetitiveSnapshot(
                    source_id=source.id,
                    workspace_id=source.workspace_id,
                    url=source.url,
                    extraction_status="failed",
                    is_changed=False,
                    content_length=0,
                    error_message=str(exc),
                )
            )
            await self.db.flush()
            return RunResult(
                status="failed",
                source_id=str(source.id),
                reason=str(exc),
            )

    async def _load_source(self, source_id: UUID) -> CompetitiveSource:
        source = (
            await self.db.execute(select(CompetitiveSource).where(CompetitiveSource.id == source_id))
        ).scalar_one_or_none()
        if source is None:
            raise ValueError("Competitive source not found")
        if not source.is_active:
            raise ValueError("Competitive source is inactive")
        return source

    async def _acquire_source_lease(self, source_id: UUID, now: datetime) -> Optional[CompetitiveSource]:
        lease_until = now + timedelta(minutes=settings.COMPETITIVE_SOURCE_LEASE_MINUTES)
        lease_result = await self.db.execute(
            update(CompetitiveSource)
            .where(
                CompetitiveSource.id == source_id,
                CompetitiveSource.is_active.is_(True),
                or_(
                    CompetitiveSource.run_status != "processing",
                    CompetitiveSource.lease_until.is_(None),
                    CompetitiveSource.lease_until <= now,
                ),
            )
            .values(run_status="processing", lease_until=lease_until, last_checked_at=now)
            .returning(CompetitiveSource.id)
        )
        if lease_result.scalar_one_or_none() is None:
            return None
        return await self._load_source(source_id)

    def _snapshot(
        self,
        source: CompetitiveSource,
        extracted: ExtractedContent,
        *,
        status: str,
        changed: bool,
        store_content: bool,
    ) -> CompetitiveSnapshot:
        snapshot = CompetitiveSnapshot(
            source_id=source.id,
            workspace_id=source.workspace_id,
            url=source.url,
            reader_url=extracted.reader_url,
            content_hash=extracted.content_hash,
            extraction_status=status,
            is_changed=changed,
            normalized_content=extracted.normalized_text if store_content else None,
            content_length=extracted.content_length,
        )
        self.db.add(snapshot)
        return snapshot

    async def _find_previous_snapshot(
        self,
        source_id: UUID,
        content_hash: Optional[str],
    ) -> Optional[CompetitiveSnapshot]:
        if not content_hash:
            return None
        return (
            await self.db.execute(
                select(CompetitiveSnapshot)
                .where(
                    CompetitiveSnapshot.source_id == source_id,
                    CompetitiveSnapshot.content_hash == content_hash,
                    CompetitiveSnapshot.normalized_content.is_not(None),
                )
                .order_by(CompetitiveSnapshot.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _find_existing_analysis(self, source_id: UUID, content_hash: str) -> Optional[CompetitiveAnalysis]:
        return (
            await self.db.execute(
                select(CompetitiveAnalysis).where(
                    CompetitiveAnalysis.source_id == source_id,
                    CompetitiveAnalysis.content_hash == content_hash,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _alert_dedupe_key(source_id: UUID, content_hash: str) -> str:
        return sha256_text(f"competitive-alert:{source_id}:{content_hash}")[:96]


def evaluate_conditions(conditions: list[Mapping[str, Any]], context: Mapping[str, Any]) -> bool:
    return all(evaluate_condition(condition, context) for condition in conditions or [])


def evaluate_condition(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if "path" in condition and "operator" in condition:
        actual = _resolve_path(context, str(condition.get("path") or ""))
        expected = condition.get("value")
        operator = str(condition.get("operator") or "")
        return _compare_condition(actual, expected, operator)
    if "all" in condition:
        return all(evaluate_condition(child, context) for child in condition.get("all") or [])
    if "any" in condition:
        return any(evaluate_condition(child, context) for child in condition.get("any") or [])
    if "not" in condition:
        return not evaluate_condition(condition.get("not") or {}, context)
    return False


def _automation_context(*, source: CompetitiveSource, analysis: CompetitiveAnalysis) -> dict[str, Any]:
    return {
        "workspace": {"id": str(source.workspace_id)},
        "competitive": {
            "source": {
                "id": str(source.id),
                "name": source.name,
                "url": source.url,
                "content_hash": analysis.content_hash,
            },
            "analysis": {
                "id": str(analysis.id),
                "headline_summary": analysis.headline_summary,
                "overall_urgency": analysis.overall_urgency,
                "urgency_label": analysis.urgency_label,
                "findings": analysis.findings or [],
            },
        },
        "payload": analysis.analysis_payload or {},
    }


def _resolve_path(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for segment in path.split("."):
        if not segment:
            return None
        if isinstance(current, Mapping):
            current = current.get(segment)
        else:
            return None
    return current


def _compare_condition(actual: Any, expected: Any, operator: str) -> bool:
    if operator == "exists":
        return actual is not None
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    actual_text = "" if actual is None else str(actual)
    expected_text = "" if expected is None else str(expected)
    if operator == "contains":
        return expected_text.lower() in actual_text.lower()
    if operator == "not_contains":
        return expected_text.lower() not in actual_text.lower()
    if operator == "greater_than":
        return _clamp_float(actual, float("-inf"), float("inf")) > _clamp_float(expected, float("-inf"), float("inf"))
    if operator == "less_than":
        return _clamp_float(actual, float("-inf"), float("inf")) < _clamp_float(expected, float("-inf"), float("inf"))
    if operator == "matches_regex":
        if len(expected_text) > 200 or len(actual_text) > 10_000:
            return False
        try:
            return re.search(expected_text, actual_text) is not None
        except re.error:
            return False
    return False


def _meaningful_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in normalize_extracted_text(text).splitlines():
        value = line.strip()
        if len(value) < 3:
            continue
        if value.lower() in GENERIC_LINES:
            continue
        lines.append(value)
    return lines


def _is_informative_diff_item(before: str, after: str) -> bool:
    text = f"{before}\n{after}".strip()
    if len(text) < 8:
        return False
    words = re.findall(r"[A-Za-z0-9$%]+", text)
    if len(words) < 3:
        return False
    lowered = text.lower()
    if all(line.lower() in GENERIC_LINES for line in text.splitlines() if line.strip()):
        return False
    if _is_volatile_line(text):
        return False
    return any(char.isalpha() for char in text) and not lowered.startswith(("skip to ", "back to top"))


def _diff_salience(text: str) -> int:
    lowered = text.lower()
    keyword_score = sum(3 for keyword in HIGH_URGENCY_KEYWORDS if keyword in lowered)
    numeric_score = min(3, len(re.findall(r"[$€£]\s?\d+|\d+\s?%|\b\d+\s+(?:engineers?|roles?|jobs?|customers?|users?)\b", lowered)))
    length_score = 1 if 40 <= len(text) <= MAX_DIFF_ITEM_CHARS else 0
    return keyword_score + numeric_score + length_score


def _rank_diff_strings(values: list[str]) -> list[str]:
    return sorted(values, key=lambda value: (_diff_salience(value), -len(value)), reverse=True)


def _rank_changed_sections(values: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        values,
        key=lambda item: (_diff_salience(f"{item.get('before', '')}\n{item.get('after', '')}"), -len(str(item))),
        reverse=True,
    )


def _has_high_urgency_evidence(evidence: str, signal: str, category: str) -> bool:
    text = f"{category} {signal} {evidence}".lower()
    if any(keyword in text for keyword in HIGH_URGENCY_KEYWORDS):
        return True
    return bool(re.search(r"[$€£]\s?\d+|\b\d+\s+(?:ml|ai|machine learning|engineers?|roles?|jobs?)\b", text))


def _join_limited(lines: list[str]) -> str:
    text = "\n".join(lines).strip()
    return text[:MAX_DIFF_ITEM_CHARS]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(minimum, min(maximum, number))


def _parse_retry_after(value: Optional[str], attempt: int) -> float:
    try:
        return max(0.0, min(float(value or ""), 30.0))
    except ValueError:
        return float(2**attempt)
