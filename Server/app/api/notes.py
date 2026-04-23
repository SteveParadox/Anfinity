"""Notes API routes for user note management."""
from typing import Optional, List, Any, Dict, Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID
import logging
import re
from difflib import SequenceMatcher
try:
    from diff_match_patch import diff_match_patch
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    diff_match_patch = None
from fastapi import APIRouter, Depends, HTTPException, Response, Request, status, Query, BackgroundTasks
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, false, func, text

from app.database.session import get_db, AsyncSessionLocal
from app.database.models import ApprovalWorkflowPriority, ApprovalWorkflowStatus, Note, NoteCollaborator, NoteCollaborationRole, NoteConnectionSuggestion, NoteInvite, NoteInviteStatus, NoteVersion, User as DBUser, WorkspaceSection
from app.core.auth import get_current_user
from app.core.audit import AuditRequestContext, audit
from app.core.permissions import ensure_workspace_permission, get_bulk_workspace_permissions_for_user
from app.services.graph_service import get_graph_service
from app.services.note_access import ensure_note_permission, resolve_note_access
from app.services.note_comments import (
    create_note_comment,
    list_note_comments,
    load_note_comment_or_404,
    serialize_note_comment,
    set_comment_resolution,
    toggle_comment_reaction,
)
from app.services.note_contributions import build_note_contribution_breakdown, list_note_contributions
from app.services.note_invites import (
    DEFAULT_NOTE_INVITE_TTL,
    accept_note_invite,
    create_note_invite,
    expire_note_invite_if_needed,
    get_note_invite_by_token,
    get_note_with_bypass,
    revoke_note_invite,
    temporary_rls_bypass,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notes", tags=["Notes"])

# Helper function for lazy task imports to avoid Celery import hang at startup
def queue_note_embedding(note_id: str) -> None:
    """Queue note embedding generation (lazy import to avoid startup hang)."""
    try:
        from app.tasks.note_embeddings import generate_note_embedding
        generate_note_embedding.delay(str(note_id))
    except Exception as e:
        # Log but don't fail if task queueing fails
        print(f"Warning: Failed to queue embedding task: {e}")

def queue_note_summary(note_id: str) -> None:
    """Queue note summary generation (lazy import to avoid startup hang)."""
    try:
        from app.tasks.note_summaries import generate_note_summary
        generate_note_summary.delay(str(note_id))
    except Exception as e:
        # Log but don't fail if task queueing fails
        print(f"Warning: Failed to queue summary task: {e}")


def queue_note_connection_suggestions(note_id: str) -> None:
    """Queue async note-connection suggestions (lazy import to avoid startup hangs)."""
    try:
        from app.tasks.connection_suggestions import generate_connection_suggestions
        generate_connection_suggestions.delay(str(note_id))
    except Exception as e:
        print(f"Warning: Failed to queue connection suggestion task: {e}")


# ==================== Schemas ====================

class NoteCreate(BaseModel):
    """Schema for creating a note."""
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1)
    workspace_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    note_type: str = Field(default='note', pattern='^(note|web-clip|document|voice|ai-generated)$')


class NoteUpdate(BaseModel):
    """Schema for updating a note."""
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    connections: Optional[List[str]] = None
    note_type: Optional[str] = Field(None, pattern='^(note|web-clip|document|voice|ai-generated)$')


class NoteCollaborationSync(BaseModel):
    """Schema for lightweight collaborative content persistence."""
    content: str = Field(default="")
    base_content: Optional[str] = None


class NoteResponse(BaseModel):
    """Response schema for a note."""
    id: str
    workspace_id: Optional[str]
    user_id: str
    title: str
    content: str
    summary: Optional[str]
    tags: List[str]
    connections: List[str]
    note_type: str
    word_count: int
    ai_generated: bool
    confidence_score: Optional[float]
    source_url: Optional[str]
    approval_status: ApprovalWorkflowStatus = ApprovalWorkflowStatus.DRAFT
    approval_priority: ApprovalWorkflowPriority = ApprovalWorkflowPriority.NORMAL
    approval_due_at: Optional[str] = None
    approval_submitted_at: Optional[str] = None
    approval_submitted_by_user_id: Optional[str] = None
    approval_decided_at: Optional[str] = None
    approval_decided_by_user_id: Optional[str] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class NoteListResponse(BaseModel):
    """Paginated notes response."""
    items: List[NoteResponse]
    total: int
    page: int
    page_size: int


class SuggestedNoteSnippet(BaseModel):
    id: str
    title: str
    content_preview: str
    tags: List[str] = Field(default_factory=list)
    created_at: str


class ConnectionSuggestionResponse(BaseModel):
    id: str
    workspace_id: str
    note_id: str
    suggested_note: SuggestedNoteSnippet
    similarity_score: float
    reason: str
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    responded_at: Optional[str] = None
    created_at: str


class ConnectionSuggestionActionResponse(BaseModel):
    success: bool
    suggestion_id: str
    note_id: str
    status: str
    connections: List[str] = Field(default_factory=list)


class NoteVersionDiffSegmentResponse(BaseModel):
    type: str
    text: str
    word_count: int


class NoteVersionResponse(BaseModel):
    id: str
    note_id: str
    workspace_id: Optional[str]
    user_id: str
    version_number: int
    change_reason: str
    restored_from_version_id: Optional[str] = None
    title: str
    content: str
    summary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    connections: List[str] = Field(default_factory=list)
    note_type: str
    source_url: Optional[str] = None
    word_count: int
    diff_segments: List[NoteVersionDiffSegmentResponse] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class NoteVersionRestoreResponse(BaseModel):
    note: NoteResponse
    restored_version: NoteVersionResponse


class NoteInviteCreateRequest(BaseModel):
    invitee_email: Optional[EmailStr] = None
    invitee_user_id: Optional[str] = None
    role: NoteCollaborationRole = NoteCollaborationRole.VIEWER
    expires_in_days: Optional[int] = Field(default=7, ge=1, le=30)
    message: Optional[str] = Field(default=None, max_length=1000)


class NoteInviteAcceptRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=255)


class NoteInviteResponse(BaseModel):
    id: str
    note_id: str
    inviter_user_id: Optional[str] = None
    invitee_email: Optional[str] = None
    invitee_user_id: Optional[str] = None
    role: NoteCollaborationRole
    status: NoteInviteStatus
    expires_at: str
    accepted_at: Optional[str] = None
    revoked_at: Optional[str] = None
    message: Optional[str] = None
    created_at: str
    updated_at: str


class NoteInviteCreateResponse(BaseModel):
    invite: Optional[NoteInviteResponse] = None
    invite_token: Optional[str] = None
    created: bool
    collaborator_updated: bool = False
    collaborator_role: Optional[NoteCollaborationRole] = None


class NoteInviteResolveResponse(BaseModel):
    invite: NoteInviteResponse
    note_title: str
    can_accept: bool


class NoteInviteAcceptResponse(BaseModel):
    invite: NoteInviteResponse
    note: NoteResponse
    can_update: bool


class NoteAccessResponse(BaseModel):
    note_id: str
    access_source: str
    can_view: bool
    can_update: bool
    can_delete: bool
    can_manage: bool
    collaborator_role: Optional[NoteCollaborationRole] = None


class NoteContributionBreakdownResponse(BaseModel):
    note_created: int
    note_updated: int
    note_restored: int
    thinking_contributions: int
    votes_cast: int


class NoteContributionResponse(BaseModel):
    note_id: str
    workspace_id: Optional[str]
    contributor_user_id: str
    contributor_name: Optional[str] = None
    contributor_email: Optional[str] = None
    contribution_count: int
    breakdown: NoteContributionBreakdownResponse
    first_contribution_at: Optional[str] = None
    last_contribution_at: Optional[str] = None


class NoteCommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=10000)


class NoteCommentAuthorResponse(BaseModel):
    id: str
    email: str
    name: str


class NoteCommentMentionResponse(BaseModel):
    id: str
    comment_id: str
    mentioned_user_id: str
    mention_token: str
    start_offset: int
    end_offset: int
    user: Optional[NoteCommentAuthorResponse] = None


class NoteCommentReactionResponse(BaseModel):
    emoji: str
    emoji_value: str
    count: int
    reacted_by_current_user: bool


class NoteCommentResponse(BaseModel):
    id: str
    note_id: str
    author_user_id: str
    parent_comment_id: Optional[str] = None
    depth: int
    body: str
    is_resolved: bool
    resolved_by_user_id: Optional[str] = None
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    author: Optional[NoteCommentAuthorResponse] = None
    resolved_by: Optional[NoteCommentAuthorResponse] = None
    mentions: List[NoteCommentMentionResponse] = Field(default_factory=list)
    reactions: List[NoteCommentReactionResponse] = Field(default_factory=list)
    replies: List["NoteCommentResponse"] = Field(default_factory=list)


if hasattr(NoteCommentResponse, "model_rebuild"):
    NoteCommentResponse.model_rebuild()
else:  # pragma: no cover - compatibility for older Pydantic
    NoteCommentResponse.update_forward_refs()


# ==================== Routes ====================

# ==================== Utility Functions ====================

def calculate_word_count(content: str) -> int:
    """Calculate word count from note content.
    
    Args:
        content: Note content text
        
    Returns:
        Number of words
    """
    if not content:
        return 0
    return len(content.strip().split())


def serialize_note(note: Note) -> NoteResponse:
    created_at = note.created_at or datetime.now(timezone.utc)
    updated_at = note.updated_at or created_at
    return NoteResponse(
        id=str(note.id),
        workspace_id=str(note.workspace_id) if note.workspace_id else None,
        user_id=str(note.user_id),
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=list(note.tags or []),
        connections=[str(connection_id) for connection_id in (note.connections or [])],
        note_type=note.note_type,
        word_count=note.word_count or 0,
        ai_generated=bool(note.ai_generated),
        confidence_score=note.confidence_score,
        source_url=note.source_url,
        approval_status=(
            note.approval_status
            if isinstance(note.approval_status, ApprovalWorkflowStatus)
            else ApprovalWorkflowStatus(str(note.approval_status or ApprovalWorkflowStatus.DRAFT.value))
        ),
        approval_priority=(
            note.approval_priority
            if isinstance(note.approval_priority, ApprovalWorkflowPriority)
            else ApprovalWorkflowPriority(str(note.approval_priority or ApprovalWorkflowPriority.NORMAL.value))
        ),
        approval_due_at=note.approval_due_at.isoformat() if note.approval_due_at else None,
        approval_submitted_at=note.approval_submitted_at.isoformat() if note.approval_submitted_at else None,
        approval_submitted_by_user_id=str(note.approval_submitted_by_user_id) if note.approval_submitted_by_user_id else None,
        approval_decided_at=note.approval_decided_at.isoformat() if note.approval_decided_at else None,
        approval_decided_by_user_id=str(note.approval_decided_by_user_id) if note.approval_decided_by_user_id else None,
        created_at=created_at.isoformat(),
        updated_at=updated_at.isoformat(),
    )


def serialize_note_invite(invite: NoteInvite) -> NoteInviteResponse:
    created_at = invite.created_at or datetime.now(timezone.utc)
    updated_at = invite.updated_at or created_at
    return NoteInviteResponse(
        id=str(invite.id),
        note_id=str(invite.note_id),
        inviter_user_id=str(invite.inviter_user_id) if invite.inviter_user_id else None,
        invitee_email=invite.invitee_email,
        invitee_user_id=str(invite.invitee_user_id) if invite.invitee_user_id else None,
        role=invite.role if isinstance(invite.role, NoteCollaborationRole) else NoteCollaborationRole(str(invite.role)),
        status=invite.status if isinstance(invite.status, NoteInviteStatus) else NoteInviteStatus(str(invite.status)),
        expires_at=invite.expires_at.isoformat(),
        accepted_at=invite.accepted_at.isoformat() if invite.accepted_at else None,
        revoked_at=invite.revoked_at.isoformat() if invite.revoked_at else None,
        message=invite.message,
        created_at=created_at.isoformat(),
        updated_at=updated_at.isoformat(),
    )


def serialize_note_contribution(summary) -> NoteContributionResponse:
    return NoteContributionResponse(
        note_id=str(summary.note_id),
        workspace_id=str(summary.workspace_id) if summary.workspace_id else None,
        contributor_user_id=str(summary.contributor_user_id),
        contributor_name=summary.contributor_name,
        contributor_email=summary.contributor_email,
        contribution_count=summary.contribution_count,
        breakdown=NoteContributionBreakdownResponse(**build_note_contribution_breakdown(summary)),
        first_contribution_at=summary.first_contribution_at.isoformat() if summary.first_contribution_at else None,
        last_contribution_at=summary.last_contribution_at.isoformat() if summary.last_contribution_at else None,
    )


async def load_note_or_404(note_id: UUID, db: AsyncSession) -> Note:
    note = await get_note_with_bypass(db, note_id)
    if note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    return note


def _tokenize_for_diff(text: str) -> List[str]:
    return re.findall(r"\S+|\s+", text or "")


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def parse_uuid_or_422(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field_name} format",
        ) from exc


def _normalize_string_list(values: Sequence[Any] | None) -> List[str]:
    normalized: List[str] = []
    for value in values or []:
        text_value = str(value).strip()
        if text_value and text_value not in normalized:
            normalized.append(text_value)
    return normalized


def _summarize_diff_segments(diff_segments: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    words_added = 0
    words_deleted = 0
    words_unchanged = 0
    added_segments = 0
    deleted_segments = 0
    unchanged_segments = 0

    for segment in diff_segments:
        segment_type = str(segment.get("type") or "unchanged")
        word_count = int(segment.get("word_count") or 0)
        if segment_type == "added":
            words_added += word_count
            added_segments += 1
        elif segment_type == "deleted":
            words_deleted += word_count
            deleted_segments += 1
        else:
            words_unchanged += word_count
            unchanged_segments += 1

    return {
        "words_added": words_added,
        "words_deleted": words_deleted,
        "words_unchanged": words_unchanged,
        "added_segments": added_segments,
        "deleted_segments": deleted_segments,
        "unchanged_segments": unchanged_segments,
        "changed_segments": added_segments + deleted_segments,
    }


def _build_note_version_metadata(
    *,
    note: Note,
    latest_version: Optional[NoteVersion],
    change_reason: str,
    restored_from_version_id: Optional[UUID],
    diff_segments: Sequence[Dict[str, Any]],
    extra_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    current_tags = _normalize_string_list(note.tags)
    previous_tags = _normalize_string_list(latest_version.tags if latest_version else [])
    current_connections = _normalize_string_list(note.connections)
    previous_connections = _normalize_string_list(latest_version.connections if latest_version else [])

    added_tags = [tag for tag in current_tags if tag not in previous_tags]
    removed_tags = [tag for tag in previous_tags if tag not in current_tags]
    added_connections = [value for value in current_connections if value not in previous_connections]
    removed_connections = [value for value in previous_connections if value not in current_connections]

    title_changed = latest_version is None or latest_version.title != note.title
    content_changed = latest_version is None or latest_version.content != note.content
    note_type_changed = latest_version is None or latest_version.note_type != note.note_type
    source_url_changed = latest_version is None or latest_version.source_url != note.source_url

    changed_fields: List[str] = []
    if latest_version is None:
        changed_fields.append("created")
    else:
        if title_changed:
            changed_fields.append("title")
        if content_changed:
            changed_fields.append("content")
        if added_tags or removed_tags:
            changed_fields.append("tags")
        if added_connections or removed_connections:
            changed_fields.append("connections")
        if note_type_changed:
            changed_fields.append("note_type")
        if source_url_changed:
            changed_fields.append("source_url")

    current_word_count = note.word_count or calculate_word_count(note.content)
    previous_word_count = latest_version.word_count if latest_version else 0
    diff_summary = _summarize_diff_segments(diff_segments)

    metadata: Dict[str, Any] = {
        "snapshot_kind": "initial" if latest_version is None else "restore" if change_reason == "restored" else "revision",
        "previous_version_number": latest_version.version_number if latest_version else None,
        "restored_from_version_id": str(restored_from_version_id) if restored_from_version_id else None,
        "changed_fields": changed_fields,
        "tag_delta": {
            "added": added_tags,
            "removed": removed_tags,
        },
        "connection_delta": {
            "added": added_connections,
            "removed": removed_connections,
        },
        "summary": {
            "title_changed": title_changed,
            "content_changed": content_changed,
            "tags_changed": bool(added_tags or removed_tags),
            "connections_changed": bool(added_connections or removed_connections),
            "note_type_changed": note_type_changed,
            "source_url_changed": source_url_changed,
            "word_count": current_word_count,
            "previous_word_count": previous_word_count,
            "word_delta": current_word_count - previous_word_count,
            **diff_summary,
        },
    }

    if extra_metadata:
        metadata.update(extra_metadata)

    return metadata


def _build_sequence_matcher_diff_segments(previous_text: str, current_text: str) -> List[Dict[str, Any]]:
    previous_tokens = _tokenize_for_diff(previous_text)
    current_tokens = _tokenize_for_diff(current_text)
    matcher = SequenceMatcher(a=previous_tokens, b=current_tokens, autojunk=False)

    segments: List[Dict[str, Any]] = []
    for opcode, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if opcode == "equal":
            text_value = "".join(current_tokens[b_start:b_end])
            segment_type = "unchanged"
        elif opcode == "insert":
            text_value = "".join(current_tokens[b_start:b_end])
            segment_type = "added"
        elif opcode == "delete":
            text_value = "".join(previous_tokens[a_start:a_end])
            segment_type = "deleted"
        else:
            deleted_text = "".join(previous_tokens[a_start:a_end])
            added_text = "".join(current_tokens[b_start:b_end])
            if deleted_text:
                segments.append(
                    {
                        "type": "deleted",
                        "text": deleted_text,
                        "word_count": _count_words(deleted_text),
                    }
                )
            if added_text:
                segments.append(
                    {
                        "type": "added",
                        "text": added_text,
                        "word_count": _count_words(added_text),
                    }
                )
            continue

        if text_value:
            segments.append(
                {
                    "type": segment_type,
                    "text": text_value,
                    "word_count": _count_words(text_value),
                }
            )

    return segments


def _build_diff_match_patch_segments(previous_text: str, current_text: str) -> List[Dict[str, Any]]:
    if diff_match_patch is None:
        return _build_sequence_matcher_diff_segments(previous_text, current_text)

    previous_tokens = _tokenize_for_diff(previous_text)
    current_tokens = _tokenize_for_diff(current_text)
    token_lookup: Dict[str, int] = {}
    token_array: List[str] = [""]

    def encode(tokens: List[str]) -> str:
        encoded: List[str] = []
        for token in tokens:
            token_index = token_lookup.get(token)
            if token_index is None:
                token_array.append(token)
                token_index = len(token_array) - 1
                token_lookup[token] = token_index
            encoded.append(chr(token_index))
        return "".join(encoded)

    previous_encoded = encode(previous_tokens)
    current_encoded = encode(current_tokens)

    dmp = diff_match_patch()
    diffs = dmp.diff_main(previous_encoded, current_encoded, False)
    dmp.diff_cleanupSemantic(diffs)

    segments: List[Dict[str, Any]] = []
    diff_type_map = {
        dmp.DIFF_DELETE: "deleted",
        dmp.DIFF_INSERT: "added",
        dmp.DIFF_EQUAL: "unchanged",
    }

    for operation, encoded_text in diffs:
        token_text = "".join(
            token_array[ord(character)]
            for character in encoded_text
            if ord(character) < len(token_array)
        )
        if not token_text:
            continue
        segments.append(
            {
                "type": diff_type_map.get(operation, "unchanged"),
                "text": token_text,
                "word_count": _count_words(token_text),
            }
        )

    return segments


def build_word_diff_segments(previous_text: str, current_text: str) -> List[Dict[str, Any]]:
    return _build_diff_match_patch_segments(previous_text, current_text)


async def get_latest_note_version(db: AsyncSession, note_id: UUID) -> Optional[NoteVersion]:
    version_result = await db.execute(
        select(NoteVersion)
        .where(NoteVersion.note_id == note_id)
        .order_by(NoteVersion.version_number.desc())
        .limit(1)
    )
    return version_result.scalar_one_or_none()


async def create_note_version_snapshot(
    db: AsyncSession,
    *,
    note: Note,
    user_id: UUID,
    change_reason: str,
    restored_from_version_id: Optional[UUID] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[NoteVersion]:
    latest_version = await get_latest_note_version(db, note.id)
    previous_content = latest_version.content if latest_version else ""
    version_number = (latest_version.version_number if latest_version else 0) + 1

    if latest_version is not None:
        comparable_fields = (
            latest_version.title == note.title,
            latest_version.content == note.content,
            _normalize_string_list(latest_version.tags) == _normalize_string_list(note.tags),
            _normalize_string_list(latest_version.connections) == _normalize_string_list(note.connections),
            latest_version.note_type == note.note_type,
            latest_version.source_url == note.source_url,
        )
        if all(comparable_fields):
            return None

    diff_segments = build_word_diff_segments(previous_content, note.content or "")
    version_metadata = _build_note_version_metadata(
        note=note,
        latest_version=latest_version,
        change_reason=change_reason,
        restored_from_version_id=restored_from_version_id,
        diff_segments=diff_segments,
        extra_metadata=extra_metadata,
    )
    version = NoteVersion(
        note_id=note.id,
        workspace_id=note.workspace_id,
        user_id=user_id,
        version_number=version_number,
        change_reason=change_reason,
        restored_from_version_id=restored_from_version_id,
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=list(note.tags or []),
        connections=[str(connection_id) for connection_id in (note.connections or [])],
        note_type=note.note_type,
        source_url=note.source_url,
        word_count=note.word_count or calculate_word_count(note.content),
        diff_segments=diff_segments,
        version_metadata=version_metadata,
    )
    db.add(version)
    await db.flush()
    return version


def serialize_note_version(version: NoteVersion) -> NoteVersionResponse:
    return NoteVersionResponse(
        id=str(version.id),
        note_id=str(version.note_id),
        workspace_id=str(version.workspace_id) if version.workspace_id else None,
        user_id=str(version.user_id),
        version_number=int(version.version_number or 0),
        change_reason=version.change_reason,
        restored_from_version_id=str(version.restored_from_version_id) if version.restored_from_version_id else None,
        title=version.title,
        content=version.content,
        summary=version.summary,
        tags=list(version.tags or []),
        connections=[str(connection_id) for connection_id in (version.connections or [])],
        note_type=version.note_type,
        source_url=version.source_url,
        word_count=version.word_count or 0,
        diff_segments=[
            NoteVersionDiffSegmentResponse(
                type=str(segment.get("type") or "unchanged"),
                text=str(segment.get("text") or ""),
                word_count=int(segment.get("word_count") or 0),
            )
            for segment in (version.diff_segments or [])
        ],
        metadata=dict(version.version_metadata or {}),
        created_at=(version.created_at or datetime.now(timezone.utc)).isoformat(),
    )


async def sync_note_search_index(db: AsyncSession, note_id: UUID) -> None:
    """Keep the PostgreSQL full-text search vector aligned with note content."""
    try:
        await db.execute(
            text(
                """
                UPDATE notes
                SET content_tsv = to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(content, ''))
                WHERE id = :note_id
                """
            ),
            {"note_id": note_id},
        )
    except Exception as exc:
        logger.warning("Skipping content_tsv sync for note %s: %s", note_id, exc)


async def sync_note_search_index_by_id(note_id: UUID) -> None:
    """Update the optional search index in a fresh session after note writes commit."""
    async with AsyncSessionLocal() as db:
        try:
            await sync_note_search_index(db, note_id)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.warning("Skipping async content_tsv sync for note %s: %s", note_id, exc)


async def sync_note_graph(db: AsyncSession, note: Note) -> None:
    """Best-effort graph sync that never blocks note persistence."""
    try:
        async with db.begin_nested():
            await get_graph_service().sync_note_to_graph(db, note)
    except Exception as exc:
        logger.warning("Skipping graph sync for note %s in workspace %s: %s", note.id, note.workspace_id, exc)


async def sync_note_graph_by_id(note_id: UUID) -> None:
    """Run graph sync in a fresh session so note creation can return quickly."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if not note or not note.workspace_id:
            return
        try:
            await get_graph_service().sync_note_to_graph(db, note)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.warning("Skipping async graph sync for note %s in workspace %s: %s", note_id, note.workspace_id, exc)


async def remove_note_graph(db: AsyncSession, workspace_id: UUID, note_id: UUID) -> None:
    """Best-effort graph cleanup that never blocks note deletion."""
    try:
        async with db.begin_nested():
            await get_graph_service().remove_note_from_graph(db, workspace_id, note_id)
    except Exception as exc:
        logger.warning("Skipping graph cleanup for note %s in workspace %s: %s", note_id, workspace_id, exc)


def serialize_connection_suggestion(suggestion: NoteConnectionSuggestion) -> ConnectionSuggestionResponse:
    suggested_note = suggestion.suggested_note
    preview = ""
    if suggested_note and suggested_note.content:
        preview = suggested_note.content[:160]
        if len(suggested_note.content) > 160:
            preview += "..."

    return ConnectionSuggestionResponse(
        id=str(suggestion.id),
        workspace_id=str(suggestion.workspace_id),
        note_id=str(suggestion.source_note_id),
        suggested_note=SuggestedNoteSnippet(
            id=str(suggested_note.id) if suggested_note else "",
            title=suggested_note.title if suggested_note else "Deleted note",
            content_preview=preview,
            tags=list(suggested_note.tags or []) if suggested_note else [],
            created_at=suggested_note.created_at.isoformat() if suggested_note and suggested_note.created_at else datetime.utcnow().isoformat(),
        ),
        similarity_score=float(suggestion.similarity_score or 0.0),
        reason=suggestion.reason,
        status=suggestion.status,
        metadata=dict(suggestion.suggestion_metadata or {}),
        responded_at=suggestion.responded_at.isoformat() if suggestion.responded_at else None,
        created_at=suggestion.created_at.isoformat() if suggestion.created_at else datetime.utcnow().isoformat(),
    )


def build_note_audit_context(request: Request, source: str, *, session_id: Optional[str] = None) -> AuditRequestContext:
    return AuditRequestContext.from_request(request, source=source, session_id=session_id)


# ==================== Endpoints ====================

@router.post("", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    note_data: NoteCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new note in a specific workspace.
    
    Args:
        note_data: Note creation data (includes workspace_id)
        current_user: Current authenticated user
        db: Database session
        
    Returns:
        Created note with workspace delegation
        
    Raises:
        HTTPException: If workspace_id is not provided or user lacks access
    """
    
    # Validate workspace - REQUIRED for workspace delegation
    if not note_data.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id is required when creating notes"
        )
    
    try:
        workspace_id = UUID(note_data.workspace_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid workspace_id format"
        )
    
    logger.info(f"📝 [NOTE CREATE] User {current_user.id} creating note in workspace {workspace_id}")
    
    await ensure_workspace_permission(
        workspace_id=workspace_id,
        user=current_user,
        db=db,
        section=WorkspaceSection.NOTES,
        action="create",
    )
    logger.debug(f"✅ [WORKSPACE VERIFIED] User can create notes in workspace {workspace_id}")
    
    # Create note
    new_note = Note(
        workspace_id=workspace_id,
        user_id=current_user.id,
        title=note_data.title,
        content=note_data.content,
        tags=note_data.tags,
        source_url=note_data.source_url,
        note_type=note_data.note_type,
        word_count=calculate_word_count(note_data.content),
        ai_generated=False,
    )
    
    db.add(new_note)
    await db.flush()
    await db.refresh(new_note)
    created_version = await create_note_version_snapshot(
        db,
        note=new_note,
        user_id=current_user.id,
        change_reason="created",
        extra_metadata={"trigger": "create_note"},
    )
    await audit.note_created(
        db,
        actor_user_id=current_user.id,
        workspace_id=workspace_id,
        note_id=new_note.id,
        metadata={
            "title": new_note.title,
            "note_type": new_note.note_type,
            "tag_count": len(new_note.tags or []),
            "word_count": new_note.word_count or 0,
            "version_id": str(created_version.id) if created_version is not None else None,
            "source": "api.notes.create_note",
        },
        context=build_note_audit_context(request, "api.notes.create_note"),
    )
    await db.commit()
    await db.refresh(new_note)

    # Queue post-create enrichment after the response so the control-plane note
    # save stays fast and a downstream service issue does not fail note creation.
    background_tasks.add_task(sync_note_graph_by_id, new_note.id)
    background_tasks.add_task(sync_note_search_index_by_id, new_note.id)
    background_tasks.add_task(queue_note_embedding, str(new_note.id))
    background_tasks.add_task(queue_note_connection_suggestions, str(new_note.id))

    if len(note_data.content.split()) > 20:
        background_tasks.add_task(queue_note_summary, str(new_note.id))
    
    return serialize_note(new_note)


@router.get("", response_model=NoteListResponse)
async def list_notes(
    workspace_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's notes with filtering.
    
    Args:
        workspace_id: Filter by workspace
        search: Search in title and content
        tags: Filter by tags
        page: Page number
        page_size: Items per page
        current_user: Current user
        db: Database session
        
    Returns:
        Paginated notes
    """
    workspace_uuid: Optional[UUID] = None
    if workspace_id:
        try:
            workspace_uuid = UUID(workspace_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid workspace_id format",
            )

    workspace_permissions = await get_bulk_workspace_permissions_for_user(db, current_user)
    accessible_workspace_ids = {
        UUID(workspace_key)
        for workspace_key, payload in workspace_permissions.items()
        if bool((payload.get("permissions") or {}).get("notes", {}).get("view"))
    }

    async with temporary_rls_bypass(db):
        query = (
            select(Note)
            .outerjoin(
                NoteCollaborator,
                and_(
                    NoteCollaborator.note_id == Note.id,
                    NoteCollaborator.user_id == current_user.id,
                ),
            )
            .distinct()
        )

        if workspace_uuid is not None:
            query = query.where(Note.workspace_id == workspace_uuid)
            if workspace_uuid not in accessible_workspace_ids:
                query = query.where(
                    or_(
                        Note.user_id == current_user.id,
                        NoteCollaborator.user_id == current_user.id,
                    )
                )
        else:
            workspace_scope = (
                Note.workspace_id.in_(list(accessible_workspace_ids))
                if accessible_workspace_ids
                else false()
            )
            query = query.where(
                or_(
                    Note.user_id == current_user.id,
                    NoteCollaborator.user_id == current_user.id,
                    workspace_scope,
                )
            )

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    Note.title.ilike(search_term),
                    Note.content.ilike(search_term),
                )
            )

        if tags:
            for tag in tags:
                query = query.where(Note.tags.contains([tag]))

        count_result = await db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar() or 0

        offset = (page - 1) * page_size
        result = await db.execute(
            query.order_by(Note.updated_at.desc()).offset(offset).limit(page_size)
        )
        notes = result.scalars().all()
    
    return NoteListResponse(
        items=[serialize_note(note) for note in notes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/invites/resolve", response_model=NoteInviteResolveResponse)
async def resolve_note_invite_endpoint(
    token: str = Query(..., min_length=20, max_length=255),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a note invite for the authenticated user before acceptance."""

    invite = await get_note_invite_by_token(db, token.strip())
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    await expire_note_invite_if_needed(invite, db)

    note = await get_note_with_bypass(db, invite.note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    normalized_invitee_email = (invite.invitee_email or "").strip().lower()
    normalized_user_email = (current_user.email or "").strip().lower()
    can_accept = (
        invite.status == NoteInviteStatus.PENDING
        and invite.expires_at > datetime.now(timezone.utc)
        and (invite.invitee_user_id is None or invite.invitee_user_id == current_user.id)
        and (not normalized_invitee_email or normalized_invitee_email == normalized_user_email)
    )

    return NoteInviteResolveResponse(
        invite=serialize_note_invite(invite),
        note_title=note.title,
        can_accept=can_accept,
    )


@router.post("/invites/accept", response_model=NoteInviteAcceptResponse)
async def accept_note_invite_endpoint(
    payload: NoteInviteAcceptRequest,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept a valid note collaboration invite."""

    invite, note, access = await accept_note_invite(
        payload.token.strip(),
        current_user,
        db,
        audit_context=build_note_audit_context(request, "api.notes.accept_note_invite"),
    )
    return NoteInviteAcceptResponse(
        invite=serialize_note_invite(invite),
        note=serialize_note(note),
        can_update=access.can_update,
    )


@router.get("/{note_id}/invites", response_model=List[NoteInviteResponse])
async def list_note_invites(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List invites for a note."""

    note_uuid = parse_uuid_or_422(note_id, "note_id")
    note_result = await db.execute(select(Note).where(Note.id == note_uuid))
    note = note_result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    await ensure_note_permission(note, current_user, db, "manage")

    invite_result = await db.execute(
        select(NoteInvite)
        .where(NoteInvite.note_id == note_uuid)
        .order_by(NoteInvite.created_at.desc())
    )
    invites = invite_result.scalars().all()
    serialized: list[NoteInviteResponse] = []
    for invite in invites:
        await expire_note_invite_if_needed(invite, db)
        serialized.append(serialize_note_invite(invite))
    return serialized


@router.post("/{note_id}/invites", response_model=NoteInviteCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_note_invite_endpoint(
    note_id: str,
    payload: NoteInviteCreateRequest,
    response: Response,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or refresh a note collaboration invite."""

    note_uuid = parse_uuid_or_422(note_id, "note_id")
    note_result = await db.execute(select(Note).where(Note.id == note_uuid))
    note = note_result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    await ensure_note_permission(note, current_user, db, "manage")

    if payload.invitee_email is None and payload.invitee_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either invitee_email or invitee_user_id is required",
        )

    invitee_user_uuid: Optional[UUID] = None
    if payload.invitee_user_id:
        try:
            invitee_user_uuid = UUID(payload.invitee_user_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid invitee_user_id format",
            )

    created_invite = await create_note_invite(
        note,
        current_user,
        str(payload.invitee_email) if payload.invitee_email is not None else None,
        invitee_user_uuid,
        payload.role,
        db,
        message=payload.message,
        expires_in=timedelta(days=payload.expires_in_days or int(DEFAULT_NOTE_INVITE_TTL.days)),
        audit_context=build_note_audit_context(request, "api.notes.create_note_invite"),
    )

    if created_invite.updated_collaborator is not None:
        response.status_code = status.HTTP_200_OK
        return NoteInviteCreateResponse(
            invite=None,
            invite_token=None,
            created=False,
            collaborator_updated=True,
            collaborator_role=created_invite.updated_collaborator.role,
        )

    response.status_code = status.HTTP_201_CREATED if created_invite.created else status.HTTP_200_OK
    return NoteInviteCreateResponse(
        invite=serialize_note_invite(created_invite.invite) if created_invite.invite is not None else None,
        invite_token=created_invite.token,
        created=created_invite.created,
        collaborator_updated=False,
        collaborator_role=None,
    )


@router.post("/{note_id}/invites/{invite_id}/revoke", response_model=NoteInviteResponse)
async def revoke_note_invite_endpoint(
    note_id: str,
    invite_id: str,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a pending note invite."""

    note_uuid = parse_uuid_or_422(note_id, "note_id")
    invite_uuid = parse_uuid_or_422(invite_id, "invite_id")

    note_result = await db.execute(select(Note).where(Note.id == note_uuid))
    note = note_result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    await ensure_note_permission(note, current_user, db, "manage")

    invite_result = await db.execute(
        select(NoteInvite).where(
            and_(
                NoteInvite.id == invite_uuid,
                NoteInvite.note_id == note_uuid,
            )
        )
    )
    invite = invite_result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    invite = await revoke_note_invite(
        invite,
        db,
        actor_user_id=current_user.id,
        workspace_id=note.workspace_id,
        audit_context=build_note_audit_context(request, "api.notes.revoke_note_invite"),
    )
    return serialize_note_invite(invite)


@router.get("/{note_id}/access", response_model=NoteAccessResponse)
async def get_note_access(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return effective note access for the current user."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    access = await ensure_note_permission(note, current_user, db, "view")
    return NoteAccessResponse(
        note_id=str(note.id),
        access_source=access.access_source,
        can_view=access.can_view,
        can_update=access.can_update,
        can_delete=access.can_delete,
        can_manage=access.can_manage,
        collaborator_role=access.collaborator_role,
    )


@router.get("/{note_id}", response_model=NoteResponse)
async def get_note(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get a specific note.
    
    Args:
        note_id: Note ID
        current_user: Current user
        db: Database session
        
    Returns:
        Note details
    """
    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "view")
    return serialize_note(note)


@router.get("/{note_id}/comments", response_model=List[NoteCommentResponse])
async def get_note_comments(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List threaded comments for one note."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "view")
    payload = await list_note_comments(db, note_id=note.id, current_user_id=current_user.id)
    return [NoteCommentResponse(**entry) for entry in payload]


@router.post("/{note_id}/comments", response_model=NoteCommentResponse, status_code=status.HTTP_201_CREATED)
async def create_note_comment_endpoint(
    note_id: str,
    payload: NoteCommentCreateRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new top-level comment on a note."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    comment = await create_note_comment(
        db,
        note=note,
        author=current_user,
        body=payload.body,
    )
    return NoteCommentResponse(**serialize_note_comment(comment, current_user_id=current_user.id))


@router.post("/{note_id}/comments/{comment_id}/replies", response_model=NoteCommentResponse, status_code=status.HTTP_201_CREATED)
async def create_note_reply_endpoint(
    note_id: str,
    comment_id: str,
    payload: NoteCommentCreateRequest,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a nested reply for an existing note comment."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    comment = await create_note_comment(
        db,
        note=note,
        author=current_user,
        body=payload.body,
        parent_comment_id=parse_uuid_or_422(comment_id, "comment_id"),
    )
    return NoteCommentResponse(**serialize_note_comment(comment, current_user_id=current_user.id))


@router.post("/{note_id}/comments/{comment_id}/reactions/{emoji}", response_model=NoteCommentResponse)
async def toggle_note_comment_reaction_endpoint(
    note_id: str,
    comment_id: str,
    emoji: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle one allowed emoji reaction for the current user."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    comment = await load_note_comment_or_404(
        db,
        note_id=note.id,
        comment_id=parse_uuid_or_422(comment_id, "comment_id"),
    )
    await toggle_comment_reaction(db, comment=comment, user_id=current_user.id, emoji=emoji)
    refreshed_comment = await load_note_comment_or_404(db, note_id=note.id, comment_id=comment.id)
    return NoteCommentResponse(**serialize_note_comment(refreshed_comment, current_user_id=current_user.id))


@router.post("/{note_id}/comments/{comment_id}/resolve", response_model=NoteCommentResponse)
async def resolve_note_comment_endpoint(
    note_id: str,
    comment_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a note comment thread as resolved."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    comment = await load_note_comment_or_404(
        db,
        note_id=note.id,
        comment_id=parse_uuid_or_422(comment_id, "comment_id"),
    )
    await set_comment_resolution(db, comment=comment, resolved=True, actor_user_id=current_user.id)
    refreshed_comment = await load_note_comment_or_404(db, note_id=note.id, comment_id=comment.id)
    return NoteCommentResponse(**serialize_note_comment(refreshed_comment, current_user_id=current_user.id))


@router.post("/{note_id}/comments/{comment_id}/unresolve", response_model=NoteCommentResponse)
async def unresolve_note_comment_endpoint(
    note_id: str,
    comment_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear the resolved state on a note comment thread."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    comment = await load_note_comment_or_404(
        db,
        note_id=note.id,
        comment_id=parse_uuid_or_422(comment_id, "comment_id"),
    )
    await set_comment_resolution(db, comment=comment, resolved=False, actor_user_id=current_user.id)
    refreshed_comment = await load_note_comment_or_404(db, note_id=note.id, comment_id=comment.id)
    return NoteCommentResponse(**serialize_note_comment(refreshed_comment, current_user_id=current_user.id))


@router.get("/{note_id}/contributions", response_model=List[NoteContributionResponse])
async def get_note_contributions(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated contribution attribution for one note."""

    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "view")
    contributions = await list_note_contributions(db, note.id)
    return [serialize_note_contribution(summary) for summary in contributions]


@router.get("/{note_id}/versions", response_model=List[NoteVersionResponse])
async def list_note_versions(
    note_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List immutable note versions for timeline and lineage views."""
    note_uuid = parse_uuid_or_422(note_id, "note_id")
    note = await load_note_or_404(note_uuid, db)
    await ensure_note_permission(note, current_user, db, "view")

    version_result = await db.execute(
        select(NoteVersion)
        .where(NoteVersion.note_id == note_uuid)
        .order_by(NoteVersion.version_number.desc(), NoteVersion.created_at.desc())
    )
    return [serialize_note_version(version) for version in version_result.scalars().all()]


@router.post("/{note_id}/versions/{version_id}/restore", response_model=NoteVersionRestoreResponse)
async def restore_note_version(
    note_id: str,
    version_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Restore a historical note version by creating a new current version."""
    note_uuid = parse_uuid_or_422(note_id, "note_id")
    version_uuid = parse_uuid_or_422(version_id, "version_id")

    note = await load_note_or_404(note_uuid, db)
    await ensure_note_permission(note, current_user, db, "update")

    version_result = await db.execute(
        select(NoteVersion).where(
            and_(
                NoteVersion.id == version_uuid,
                NoteVersion.note_id == note_uuid,
            )
        )
    )
    version = version_result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note version not found")

    current_state_matches = (
        note.title == version.title
        and note.content == version.content
        and list(note.tags or []) == list(version.tags or [])
        and [str(connection_id) for connection_id in (note.connections or [])]
        == [str(connection_id) for connection_id in (version.connections or [])]
        and note.note_type == version.note_type
        and note.source_url == version.source_url
    )
    if current_state_matches:
        restored_snapshot = await get_latest_note_version(db, note.id)
        return NoteVersionRestoreResponse(
            note=serialize_note(note),
            restored_version=serialize_note_version(restored_snapshot or version),
        )

    note.title = version.title
    note.content = version.content
    note.summary = version.summary
    note.tags = list(version.tags or [])
    note.connections = [str(connection_id) for connection_id in (version.connections or [])]
    note.note_type = version.note_type
    note.source_url = version.source_url
    note.word_count = version.word_count or calculate_word_count(version.content)
    note.updated_at = datetime.now(timezone.utc)

    await db.flush()

    restored_snapshot = await create_note_version_snapshot(
        db,
        note=note,
        user_id=current_user.id,
        change_reason="restored",
        restored_from_version_id=version.id,
        extra_metadata={
            "trigger": "restore_note_version",
            "restored_from_version_number": version.version_number,
        },
    )

    await audit.note_restored(
        db,
        actor_user_id=current_user.id,
        workspace_id=note.workspace_id,
        note_id=note.id,
        metadata={
            "restored_from_version_id": str(version.id),
            "restored_from_version_number": version.version_number,
            "restored_version_snapshot_id": str(restored_snapshot.id) if restored_snapshot is not None else None,
            "source": "api.notes.restore_note_version",
        },
        context=build_note_audit_context(request, "api.notes.restore_note_version"),
    )
    await db.commit()
    await db.refresh(note)
    background_tasks.add_task(sync_note_graph_by_id, note.id)
    background_tasks.add_task(sync_note_search_index_by_id, note.id)
    background_tasks.add_task(queue_note_embedding, str(note.id))
    background_tasks.add_task(queue_note_connection_suggestions, str(note.id))
    if len(note.content.split()) > 20:
        background_tasks.add_task(queue_note_summary, str(note.id))

    return NoteVersionRestoreResponse(
        note=serialize_note(note),
        restored_version=serialize_note_version(restored_snapshot or version),
    )


@router.patch("/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    updates: NoteUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a note.
    
    Args:
        note_id: Note ID
        updates: Fields to update
        current_user: Current user
        db: Database session
        
    Returns:
        Updated note
    """
    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "update")
    # Update fields
    semantic_fields_updated = False
    note_changed = False
    changed_fields: List[str] = []
    if updates.title is not None:
        if updates.title != note.title:
            note.title = updates.title
            semantic_fields_updated = True
            note_changed = True
            changed_fields.append("title")
    if updates.content is not None:
        if updates.content != note.content:
            note.content = updates.content
            note.word_count = calculate_word_count(updates.content)
            semantic_fields_updated = True
            note_changed = True
            changed_fields.append("content")
    if updates.tags is not None:
        normalized_tags = list(updates.tags or [])
        if normalized_tags != list(note.tags or []):
            note.tags = normalized_tags
            semantic_fields_updated = True
            note_changed = True
            changed_fields.append("tags")
    if updates.connections is not None:
        normalized_connections = [str(connection_id) for connection_id in (updates.connections or [])]
        if normalized_connections != [str(connection_id) for connection_id in (note.connections or [])]:
            note.connections = normalized_connections
            note_changed = True
            changed_fields.append("connections")
    if updates.note_type is not None:
        if updates.note_type != note.note_type:
            note.note_type = updates.note_type
            note_changed = True
            changed_fields.append("note_type")

    if not note_changed:
        return serialize_note(note)

    note.updated_at = datetime.now(timezone.utc)
    
    await db.flush()
    created_version = await create_note_version_snapshot(
        db,
        note=note,
        user_id=current_user.id,
        change_reason="updated",
        extra_metadata={"trigger": "update_note"},
    )
    await audit.note_updated(
        db,
        actor_user_id=current_user.id,
        workspace_id=note.workspace_id,
        note_id=note.id,
        metadata={
            "changed_fields": changed_fields,
            "semantic_fields_updated": semantic_fields_updated,
            "version_id": str(created_version.id) if created_version is not None else None,
            "word_count": note.word_count or 0,
            "source": "api.notes.update_note",
        },
        context=build_note_audit_context(request, "api.notes.update_note"),
    )
    await db.commit()
    await db.refresh(note)
    background_tasks.add_task(sync_note_graph_by_id, note.id)
    
    # Queue background tasks if content changed
    background_tasks.add_task(queue_note_connection_suggestions, str(note.id))
    if semantic_fields_updated:
        background_tasks.add_task(sync_note_search_index_by_id, note.id)
        background_tasks.add_task(queue_note_embedding, str(note.id))
        if len(note.content.split()) > 20:
            background_tasks.add_task(queue_note_summary, str(note.id))
    
    return serialize_note(note)


@router.patch("/{note_id}/collaboration", response_model=NoteResponse)
async def sync_note_collaboration(
    note_id: str,
    updates: NoteCollaborationSync,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Persist collaborative content without creating version or task storms."""
    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)

    await ensure_note_permission(note, current_user, db, "update")

    if updates.base_content is not None and updates.base_content != note.content:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Collaborative note content changed since this client last synced",
        )

    if updates.content == note.content:
        return serialize_note(note)

    note.content = updates.content
    note.word_count = calculate_word_count(updates.content)
    note.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await audit.note_collaboration_synced(
        db,
        actor_user_id=current_user.id,
        workspace_id=note.workspace_id,
        note_id=note.id,
        metadata={
            "content_length": len(note.content or ""),
            "word_count": note.word_count or 0,
            "source": "api.notes.sync_note_collaboration",
        },
        context=build_note_audit_context(request, "api.notes.sync_note_collaboration"),
    )
    await db.commit()
    await db.refresh(note)

    background_tasks.add_task(sync_note_search_index_by_id, note.id)

    return serialize_note(note)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: str,
    request: Request,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a note.
    
    Args:
        note_id: Note ID
        current_user: Current user
        db: Database session
    """
    note = await load_note_or_404(parse_uuid_or_422(note_id, "note_id"), db)
    await ensure_note_permission(note, current_user, db, "delete")
    await audit.note_deleted(
        db,
        actor_user_id=current_user.id,
        workspace_id=note.workspace_id,
        note_id=note.id,
        metadata={
            "title": note.title,
            "note_type": note.note_type,
            "word_count": note.word_count or 0,
            "source": "api.notes.delete_note",
        },
        context=build_note_audit_context(request, "api.notes.delete_note"),
    )
    
    workspace_id = note.workspace_id
    await db.delete(note)
    await db.flush()
    if workspace_id:
        await remove_note_graph(db, workspace_id, note.id)
    await db.commit()


@router.get("/{note_id}/connection-suggestions", response_model=List[ConnectionSuggestionResponse])
async def list_connection_suggestions(
    note_id: str,
    status_filter: str = Query("pending", alias="status"),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List persisted connection suggestions for a note."""
    try:
        note_uuid = parse_uuid_or_422(note_id, "note_id")
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note ID format")
    note_result = await db.execute(select(Note).where(Note.id == note_uuid))
    note = note_result.scalar_one_or_none()

    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Note has no workspace")

    await ensure_note_permission(note, current_user, db, "view")

    query = select(NoteConnectionSuggestion).where(NoteConnectionSuggestion.source_note_id == note.id)
    if status_filter:
        query = query.where(NoteConnectionSuggestion.status == status_filter)
    query = query.order_by(NoteConnectionSuggestion.similarity_score.desc(), NoteConnectionSuggestion.created_at.desc())

    result = await db.execute(query)
    suggestions = result.scalars().all()
    return [serialize_connection_suggestion(suggestion) for suggestion in suggestions]


@router.post("/{note_id}/connection-suggestions/{suggestion_id}/confirm", response_model=ConnectionSuggestionActionResponse)
async def confirm_connection_suggestion(
    note_id: str,
    suggestion_id: str,
    background_tasks: BackgroundTasks,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Confirm a suggested connection and persist the user signal."""
    try:
        note_uuid = parse_uuid_or_422(note_id, "note_id")
        suggestion_uuid = parse_uuid_or_422(suggestion_id, "suggestion_id")
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note or suggestion ID format")

    result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.id == suggestion_uuid,
            NoteConnectionSuggestion.source_note_id == note_uuid,
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection suggestion not found")

    source_note_result = await db.execute(select(Note).where(Note.id == suggestion.source_note_id))
    source_note = source_note_result.scalar_one_or_none()
    target_note_result = await db.execute(select(Note).where(Note.id == suggestion.suggested_note_id))
    target_note = target_note_result.scalar_one_or_none()

    if not source_note or not target_note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connected note no longer exists")
    if source_note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source note has no workspace")

    await ensure_note_permission(source_note, current_user, db, "update")

    source_connections = [str(connection_id) for connection_id in (source_note.connections or [])]
    target_connections = [str(connection_id) for connection_id in (target_note.connections or [])]

    if str(target_note.id) not in source_connections:
        source_connections.append(str(target_note.id))
    if str(source_note.id) not in target_connections:
        target_connections.append(str(source_note.id))

    source_note.connections = source_connections
    target_note.connections = target_connections

    suggestion.status = "confirmed"
    suggestion.responded_by = current_user.id
    suggestion.responded_at = datetime.utcnow()

    reverse_result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.source_note_id == target_note.id,
            NoteConnectionSuggestion.suggested_note_id == source_note.id,
        )
    )
    reverse_suggestion = reverse_result.scalar_one_or_none()
    if reverse_suggestion and reverse_suggestion.status == "pending":
        reverse_suggestion.status = "confirmed"
        reverse_suggestion.responded_by = current_user.id
        reverse_suggestion.responded_at = datetime.utcnow()

    await db.flush()
    await sync_note_graph(db, source_note)
    await sync_note_graph(db, target_note)
    await db.commit()
    background_tasks.add_task(queue_note_connection_suggestions, str(source_note.id))
    background_tasks.add_task(queue_note_connection_suggestions, str(target_note.id))

    return ConnectionSuggestionActionResponse(
        success=True,
        suggestion_id=str(suggestion.id),
        note_id=str(source_note.id),
        status=suggestion.status,
        connections=[str(connection_id) for connection_id in (source_note.connections or [])],
    )


@router.post("/{note_id}/connection-suggestions/{suggestion_id}/dismiss", response_model=ConnectionSuggestionActionResponse)
async def dismiss_connection_suggestion(
    note_id: str,
    suggestion_id: str,
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Dismiss a suggested connection and preserve that user signal."""
    try:
        note_uuid = parse_uuid_or_422(note_id, "note_id")
        suggestion_uuid = parse_uuid_or_422(suggestion_id, "suggestion_id")
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid note or suggestion ID format")

    result = await db.execute(
        select(NoteConnectionSuggestion).where(
            NoteConnectionSuggestion.id == suggestion_uuid,
            NoteConnectionSuggestion.source_note_id == note_uuid,
        )
    )
    suggestion = result.scalar_one_or_none()
    if not suggestion:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection suggestion not found")

    source_note_result = await db.execute(select(Note).where(Note.id == suggestion.source_note_id))
    source_note = source_note_result.scalar_one_or_none()
    if not source_note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source note not found")
    if source_note.workspace_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source note has no workspace")

    await ensure_note_permission(source_note, current_user, db, "update")

    suggestion.status = "dismissed"
    suggestion.responded_by = current_user.id
    suggestion.responded_at = datetime.utcnow()
    await db.flush()
    await db.commit()

    return ConnectionSuggestionActionResponse(
        success=True,
        suggestion_id=str(suggestion.id),
        note_id=str(source_note.id),
        status=suggestion.status,
        connections=[str(connection_id) for connection_id in (source_note.connections or [])],
    )


@router.get("/workspace/{workspace_id}", response_model=NoteListResponse)
async def get_workspace_notes(
    workspace_id: str,
    search: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: DBUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all notes in a workspace with proper authorization.
    
    Args:
        workspace_id: Workspace UUID
        search: Search in title and content
        tags: Filter by tags
        page: Page number
        page_size: Items per page
        current_user: Current user
        db: Database session
        
    Returns:
        Paginated notes for the workspace
    """
    workspace_uuid = UUID(workspace_id)
    await ensure_workspace_permission(
        workspace_id=workspace_uuid,
        user=current_user,
        db=db,
        section=WorkspaceSection.NOTES,
        action="view",
    )
    
    # Build base filters
    filters = [Note.workspace_id == workspace_uuid]
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        filters.append(
            or_(
                Note.title.ilike(search_term),
                Note.content.ilike(search_term)
            )
        )
    
    # Tags filter - notes must have ALL specified tags
    if tags:
        for tag in tags:
            filters.append(Note.tags.contains([tag]))
    
    # Build count query with all filters
    count_query = select(func.count()).select_from(Note).where(and_(*filters))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0
    
    # Build data query with all filters and pagination
    query = select(Note).where(and_(*filters)).order_by(Note.updated_at.desc())
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    notes = result.scalars().all()
    
    return NoteListResponse(
        items=[serialize_note(note) for note in notes],
        total=total,
        page=page,
        page_size=page_size,
    )
