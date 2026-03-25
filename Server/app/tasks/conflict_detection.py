"""Conflict detection Celery tasks."""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database.session import SyncSessionLocal
from app.database.models import ConflictReport, Workspace
from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI client — lazy-loaded once at module level
# ---------------------------------------------------------------------------
_openai_client = None


def get_openai_client():
    """Return a cached OpenAI client, or None if unavailable."""
    global _openai_client
    if _openai_client is None:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        except ImportError:
            logger.warning("OpenAI library not installed — conflict detection disabled.")
        except Exception as exc:
            logger.error("Failed to initialise OpenAI client: %s", exc)
    return _openai_client


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_notes_by_max_similarity(
    db: Session,
    workspace_id: UUID,
    min_similarity: float = 0.70,
    max_similarity: float = 0.96,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Call the PostgreSQL conflict-candidate function and return typed dicts.

    FIX 17: Validate that every expected key is present before returning so
    callers never hit a KeyError on a missing column.
    """
    REQUIRED_KEYS = {
        "note_a_id", "note_a_title", "note_a_content", "note_a_date",
        "note_b_id", "note_b_title", "note_b_content", "note_b_date",
        "similarity",
    }

    try:
        result = db.execute(
            text("""
                SELECT
                    note_a_id, note_a_title, note_a_content, note_a_date,
                    note_b_id, note_b_title, note_b_content, note_b_date,
                    similarity
                FROM find_conflict_candidates(
                    :workspace_id,
                    :min_similarity,
                    :max_similarity,
                    :limit
                )
            """),
            {
                "workspace_id": str(workspace_id),
                "min_similarity": min_similarity,
                "max_similarity": max_similarity,
                "limit": limit,
            },
        )
        rows = [dict(row._mapping) for row in result]

        # Validate shape — drop rows that are missing expected columns
        valid = []
        for row in rows:
            missing = REQUIRED_KEYS - row.keys()
            if missing:
                logger.warning("Conflict candidate row missing keys %s — skipping", missing)
                continue
            valid.append(row)

        return valid

    except Exception as exc:
        logger.error("Error finding conflict candidates for workspace %s: %s", workspace_id, exc)
        return []


# ---------------------------------------------------------------------------
# GPT-4o analysis
# ---------------------------------------------------------------------------

def analyze_conflict_pair(
    note_a_title: str,
    note_a_content: str,
    note_a_date: datetime,
    note_b_title: str,
    note_b_content: str,
    note_b_date: datetime,
    similarity_score: float,
) -> Optional[Dict[str, Any]]:
    """Use GPT-4o to determine whether two notes contain a meaningful conflict."""
    client = get_openai_client()
    if not client:
        logger.warning("OpenAI client unavailable — skipping conflict analysis.")
        return None

    prompt = f"""You are analysing two personal notes for logical contradictions or inconsistencies.

NOTE A (written {note_a_date.strftime('%Y-%m-%d')}):
Title: {note_a_title}
Content: {note_a_content[:1500]}

NOTE B (written {note_b_date.strftime('%Y-%m-%d')}):
Title: {note_b_title}
Content: {note_b_content[:1500]}

TASK: Determine if these notes contain a meaningful contradiction, inconsistency, or changed belief.

Ignore: stylistic differences, complementary perspectives, or evolution of ideas explained in the text.
Flag: factual contradictions, directly opposing claims, numerical inconsistencies, or beliefs that directly conflict.

Respond ONLY with valid JSON (no markdown, no code blocks):
{{
  "is_conflict": boolean,
  "conflict_type": "factual" | "opinion" | "numerical" | "date" | null,
  "severity": "low" | "medium" | "high" | null,
  "summary": "One sentence explaining the conflict or null",
  "quote_a": "Most relevant conflicting excerpt from Note A (max 100 chars) or null",
  "quote_b": "Most relevant conflicting excerpt from Note B (max 100 chars) or null"
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=300,
            timeout=30,
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse GPT response as JSON: %s", exc)
        return None
    except Exception as exc:
        logger.error("GPT-4o analysis error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=2)
def run_conflict_detection(self, workspace_id: str):
    """Find semantically similar note pairs and detect conflicts with GPT-4o.

    Pipeline:
    1. Find candidate pairs via PostgreSQL pgvector similarity.
    2. Filter out already-analysed pairs.
    3. Analyse each new pair with GPT-4o.
    4. Persist ConflictReport rows.
    """
    logger.info("🔍 [TASK START] run_conflict_detection - Workspace: %s, Task ID: %s", workspace_id, self.request.id)
    
    workspace_uuid = UUID(workspace_id)
    db = SyncSessionLocal()
    start_time = datetime.utcnow()

    try:
        logger.info("[ConflictDetection] Starting for workspace %s", workspace_id)
        logger.debug("🔄 [CANDIDATES] Fetching similar note pairs with similarity 0.70-0.96")

        candidates = get_notes_by_max_similarity(
            db=db,
            workspace_id=workspace_uuid,
            min_similarity=0.70,
            max_similarity=0.96,
            limit=50,
        )
        logger.info("📊 [CANDIDATES FOUND] %d candidate pairs identified", len(candidates))

        if not candidates:
            logger.info("[ConflictDetection] No candidates for workspace %s", workspace_id)
            logger.info("✅ [TASK COMPLETE] No conflicts to analyze - Task ID: %s", self.request.id)
            return {
                "status": "success",
                "workspace_id": workspace_id,
                "candidates_analysed": 0,
                "conflicts_found": 0,
                "message": "No candidates found",
            }

        logger.info("[ConflictDetection] %d candidate pairs found", len(candidates))

        # ── FIX 15: Robust deduplication ──────────────────────────────────
        logger.debug("🔍 [DEDUPLICATION] Fetching existing conflict reports for deduplication")
        existing_reports = db.query(ConflictReport).filter(
            ConflictReport.workspace_id == workspace_uuid
        ).all()
        logger.info("📋 [EXISTING REPORTS] %d existing conflict reports found", len(existing_reports))

        existing_pairs: set = set()
        for report in existing_reports:
            a, b = str(report.note_id_a), str(report.note_id_b)
            # Store canonical (min, max) form
            existing_pairs.add((min(a, b), max(a, b)))

        new_candidates = [
            c for c in candidates
            if (min(str(c["note_a_id"]), str(c["note_b_id"])),
                max(str(c["note_a_id"]), str(c["note_b_id"]))) not in existing_pairs
        ]

        logger.info("[ConflictDetection] %d new pairs to analyse", len(new_candidates))

        conflicts_found = 0
        severity_counts: Dict[str, int] = {"high": 0, "medium": 0, "low": 0}

        logger.debug("🔄 [ANALYSIS] Starting GPT-4o conflict analysis for %d pairs", len(new_candidates))
        for i, pair in enumerate(new_candidates):
            try:
                logger.debug("⚙️ [ANALYZE PAIR %d/%d] Analyzing: '%s' vs '%s'", i+1, len(new_candidates), pair["note_a_title"][:30], pair["note_b_title"][:30])
                
                analysis = analyze_conflict_pair(
                    note_a_title=pair["note_a_title"],
                    note_a_content=pair["note_a_content"],
                    note_a_date=pair["note_a_date"],
                    note_b_title=pair["note_b_title"],
                    note_b_content=pair["note_b_content"],
                    note_b_date=pair["note_b_date"],
                    similarity_score=pair["similarity"],
                )

                if not analysis or not analysis.get("is_conflict"):
                    logger.debug("✓ [PAIR %d] No conflict detected", i+1)
                    continue

                severity = analysis.get("severity") or "medium"
                logger.info("⚠️ [CONFLICT FOUND] Severity: %s - Pair %d/%d", severity, i+1, len(new_candidates))
                
                db.add(ConflictReport(
                    workspace_id=workspace_uuid,
                    note_id_a=UUID(str(pair["note_a_id"])),
                    note_id_b=UUID(str(pair["note_b_id"])),
                    conflict_type=analysis.get("conflict_type") or "unknown",
                    conflict_summary=analysis.get("summary") or "Potential conflict detected",
                    conflict_quote_a=analysis.get("quote_a"),
                    conflict_quote_b=analysis.get("quote_b"),
                    similarity_score=float(pair["similarity"]),
                    severity=severity,
                    status="pending",
                ))
                conflicts_found += 1
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

                logger.debug(
                    "[ConflictDetection] %s conflict: '%s' vs '%s'",
                    severity,
                    pair["note_a_title"][:30],
                    pair["note_b_title"][:30],
                )

            except Exception as exc:
                logger.error(
                    "❌ [ANALYSIS ERROR] Error analysing pair %s vs %s: %s - Pair %d/%d",
                    pair.get("note_a_id"), pair.get("note_b_id"), exc, i+1, len(new_candidates),
                )

        db.commit()
        logger.debug("💾 [DB COMMIT] All conflict reports saved")

        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        logger.info(
            "[ConflictDetection] Done for workspace %s: %d pairs, %d conflicts "
            "(%d high / %d medium / %d low) in %dms",
            workspace_id,
            len(new_candidates),
            conflicts_found,
            severity_counts.get("high", 0),
            severity_counts.get("medium", 0),
            severity_counts.get("low", 0),
            duration_ms,
        )
        logger.info("✅ [TASK SUCCESS] Conflict detection completed - Workspace: %s, Conflicts: %d - Task ID: %s", workspace_id, conflicts_found, self.request.id)

        return {
            "status": "success",
            "workspace_id": workspace_id,
            "candidates_analysed": len(new_candidates),
            "conflicts_found": conflicts_found,
            "severity_breakdown": severity_counts,
            "duration_ms": duration_ms,
        }

    except Exception as exc:
        logger.error("[ConflictDetection] Error for workspace %s: %s - Task ID: %s", workspace_id, exc, self.request.id, exc_info=True)
        logger.error("❌ [TASK ERROR] Conflict detection failed - Task ID: %s", self.request.id, exc_info=True)
        
        if self.request.retries < self.max_retries:
            countdown = 300 * (self.request.retries + 1)
            logger.warning("⏳ [RETRY SCHEDULED] Attempt %d/%d in %d seconds - Task ID: %s", self.request.retries + 1, self.max_retries, countdown, self.request.id)
            raise self.retry(exc=exc, countdown=countdown)
        
        logger.error("❌ [MAX RETRIES EXCEEDED] Conflict detection permanently failed - Task ID: %s", self.request.id)
        return {"status": "failed", "workspace_id": workspace_id, "error": str(exc)}

    finally:
        db.close()
        logger.debug("🧹 [CLEANUP] Database session closed")


@celery_app.task
def run_conflict_detection_for_all_workspaces():
    """Queue conflict detection for every workspace. Called nightly."""
    logger.info("🌙 [TASK START] run_conflict_detection_for_all_workspaces - Nightly run")
    
    db = SyncSessionLocal()
    start_time = datetime.utcnow()

    try:
        logger.info("[ConflictDetection] Nightly run — queuing all workspaces")
        logger.debug("🔍 [DB QUERY] Fetching all workspaces from database")
        
        workspaces = db.query(Workspace).all()
        logger.info("📊 [WORKSPACES FOUND] %d workspaces found for processing", len(workspaces))

        if not workspaces:
            logger.info("[ConflictDetection] No workspaces found")
            logger.info("✅ [TASK COMPLETE] No workspaces to process")
            return {"status": "success", "workspaces_processed": 0}

        logger.debug("📋 [QUEUING] Queuing conflict detection tasks for %d workspaces", len(workspaces))
        for i, ws in enumerate(workspaces):
            logger.debug("📤 [QUEUE %d/%d] Queuing conflict detection for workspace: %s", i+1, len(workspaces), ws.id)
            run_conflict_detection.delay(str(ws.id))

        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        logger.info(
            "[ConflictDetection] Queued %d workspaces in %dms",
            len(workspaces), duration_ms,
        )
        logger.info("✅ [TASK SUCCESS] All workspace conflict detection tasks queued - Total: %d workspaces, Duration: %dms", len(workspaces), duration_ms)
        
        return {
            "status": "success",
            "workspaces_queued": len(workspaces),
            "duration_ms": duration_ms,
        }

    except Exception as exc:
        logger.error("[ConflictDetection] Master task failed: %s", exc, exc_info=True)
        logger.error("❌ [TASK ERROR] Nightly conflict detection failed - Error: %s", exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}

    finally:
        db.close()
        logger.debug("🧹 [CLEANUP] Database session closed")


@celery_app.task
def cleanup_old_resolved_conflicts(days: int = 90):
    """Delete resolved/dismissed conflicts older than *days* days.

    FIX 14 & 16: Removed the dead `threshold` variable; use only
    `threshold_date` which feeds directly into the WHERE clause.
    """
    logger.info("🧹 [TASK START] cleanup_old_resolved_conflicts - Threshold: %d days", days)
    
    db = SyncSessionLocal()
    start_time = datetime.utcnow()

    try:
        logger.debug("📅 [THRESHOLD] Calculating threshold date: %d days ago", days)
        threshold_date = datetime.utcnow() - timedelta(days=days)
        logger.debug("📅 [THRESHOLD DATE] Threshold: %s", threshold_date)

        # Count first so we can report accurately
        logger.debug("📊 [COUNT] Counting old resolved/dismissed conflicts")
        before_count = db.query(ConflictReport).filter(
            and_(
                ConflictReport.status.in_(["resolved", "dismissed"]),
                ConflictReport.resolved_at < threshold_date,
            )
        ).count()
        logger.info("📊 [COUNT RESULT] Found %d conflicts older than %d days", before_count, days)

        logger.debug("🗑️ [DELETE] Starting deletion of old resolved conflicts")
        deleted = db.query(ConflictReport).filter(
            and_(
                ConflictReport.status.in_(["resolved", "dismissed"]),
                ConflictReport.resolved_at < threshold_date,
            )
        ).delete(synchronize_session=False)

        db.commit()
        logger.info("✅ [DELETED] Deleted %d of %d old resolved conflicts", deleted, before_count)

        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        logger.info(
            "[ConflictDetection] Cleanup: deleted %d of %d old resolved conflicts (threshold: %d days) in %dms",
            deleted, before_count, days, duration_ms,
        )
        logger.info("✅ [TASK SUCCESS] Cleanup completed - Deleted: %d conflicts, Duration: %dms", deleted, duration_ms)
        
        return {"status": "success", "deleted_count": deleted, "threshold_days": days, "duration_ms": duration_ms}

    except Exception as exc:
        logger.error("[ConflictDetection] Cleanup failed: %s", exc, exc_info=True)
        logger.error("❌ [TASK ERROR] Cleanup failed - Error: %s", exc, exc_info=True)
        db.rollback()
        return {"status": "failed", "error": str(exc)}

    finally:
        db.close()
        logger.debug("🧹 [CLEANUP] Database session closed")