"""Automation configuration validation and internal action execution."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, EntityType, log_audit_event
from app.database.models import (
    ApprovalWorkflowPriority,
    ApprovalWorkflowStatus,
    Automation,
    Note,
    NoteApprovalTransition,
    User as DBUser,
    UserNotification,
    UserNotificationType,
    Workspace,
)
from app.services.automation_registry import (
    APPROVAL_PRIORITIES,
    BACKEND_ACTION_TYPES,
    HTTP_METHODS,
    NOTE_TYPES,
    SUPPORTED_ACTION_TYPES,
    SUPPORTED_CONDITION_OPERATORS,
    SUPPORTED_TRIGGER_TYPES,
)


FIELD_PATH_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*$")
BLOCKED_FIELD_PATH_SEGMENTS = {"__proto__", "prototype", "constructor"}


def validate_automation_config(*, trigger_type: str, conditions: Any, actions: Any) -> None:
    if trigger_type not in SUPPORTED_TRIGGER_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported trigger type")

    if not isinstance(conditions, list):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Automation conditions must be a list")
    for condition in conditions:
        validate_condition(condition)

    if not isinstance(actions, list) or not actions:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Automation actions must be a non-empty list")
    for action in actions:
        validate_action(action)


def validate_condition(condition: Any) -> None:
    if not isinstance(condition, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Condition must be an object")

    if "path" in condition or "operator" in condition:
        field_path = str(condition.get("path") or "")
        operator = str(condition.get("operator") or "")
        if not _is_valid_field_path(field_path):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid condition field path: {field_path}")
        if operator not in SUPPORTED_CONDITION_OPERATORS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported condition operator: {operator}")
        return

    group_keys = [key for key in ("all", "any", "not") if key in condition]
    if not group_keys:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Condition group must include all, any, or not")

    for key in ("all", "any"):
        if key in condition:
            children = condition[key]
            if not isinstance(children, list) or not children:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Condition group {key} must be a non-empty list")
            for child in children:
                validate_condition(child)

    if "not" in condition:
        validate_condition(condition["not"])


def validate_action(action: Any) -> None:
    if not isinstance(action, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Action must be an object")

    action_type = str(action.get("type") or "")
    if action_type not in SUPPORTED_ACTION_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unsupported action type: {action_type}")
    if not isinstance(action.get("config", {}), dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Action config must be an object")
    validate_action_config(action_type, action.get("config", {}))


def validate_action_config(action_type: str, config: Mapping[str, Any]) -> None:
    if action_type == "send_notification":
        _require_string_list(config, "recipientUserIds")
        _require_string(config, "title")
        _require_string(config, "message")
    elif action_type == "create_note":
        _require_string(config, "title")
        _require_string(config, "content")
        _optional_string_list(config, "tags")
        _optional_one_of(config, "noteType", NOTE_TYPES)
    elif action_type == "update_note":
        _require_string(config, "noteId")
        _optional_string(config, "title")
        _optional_string(config, "content")
        _optional_string_list(config, "tags")
        _optional_one_of(config, "noteType", NOTE_TYPES)
    elif action_type == "append_note_content":
        _require_string(config, "noteId")
        _require_string(config, "content")
    elif action_type in {"add_note_tags", "remove_note_tags"}:
        _require_string(config, "noteId")
        _require_string_list(config, "tags")
    elif action_type == "set_note_type":
        _require_string(config, "noteId")
        _require_one_of(config, "noteType", NOTE_TYPES)
    elif action_type == "link_notes":
        _require_string(config, "sourceNoteId")
        _require_string(config, "targetNoteId")
    elif action_type == "submit_for_approval":
        _require_string(config, "noteId")
        _optional_one_of(config, "priority", APPROVAL_PRIORITIES)
        _optional_string(config, "comment")
    elif action_type == "approve_note":
        _require_string(config, "noteId")
        _optional_string(config, "comment")
    elif action_type in {"reject_note", "request_approval_changes"}:
        _require_string(config, "noteId")
        _require_string(config, "comment")
    elif action_type == "call_webhook":
        _require_string(config, "url")
        _validate_action_url(str(config["url"]))
        _optional_one_of(config, "method", HTTP_METHODS)
        if "headers" in config and not isinstance(config["headers"], Mapping):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Action field headers must be an object")
        if "body" in config and not isinstance(config["body"], (str, list, dict)):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Action field body must be a string, array, or object")
    elif action_type == "send_email":
        _require_string_list(config, "to")
        _require_string(config, "subject")
        _require_string(config, "body")
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No config validator registered for action {action_type}")


def serialize_automation(automation: Automation) -> Dict[str, Any]:
    return {
        "id": str(automation.id),
        "workspace_id": str(automation.workspace_id),
        "name": automation.name,
        "trigger_type": automation.trigger_type,
        "conditions": list(automation.conditions or []),
        "actions": list(automation.actions or []),
        "enabled": bool(automation.enabled),
        "created_at": automation.created_at.isoformat() if automation.created_at else None,
        "updated_at": automation.updated_at.isoformat() if automation.updated_at else None,
    }


async def execute_backend_action(
    db: AsyncSession,
    *,
    action_type: str,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    automation_id: str,
    action_id: Optional[str] = None,
) -> Dict[str, Any]:
    if action_type not in BACKEND_ACTION_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action {action_type} is not backend-executable")

    workspace_id = UUID(str(_get_path(context, "workspace.id")))
    workspace = await _load_workspace(db, workspace_id)

    if action_type == "send_notification":
        return await _send_notification(db, workspace=workspace, config=config, context=context, automation_id=automation_id, action_id=action_id)
    if action_type == "create_note":
        return await _create_note(db, workspace=workspace, config=config, context=context, automation_id=automation_id)
    if action_type == "update_note":
        return await _update_note(db, workspace=workspace, config=config, context=context, automation_id=automation_id)
    if action_type == "append_note_content":
        return await _append_note_content(db, workspace=workspace, config=config, context=context, automation_id=automation_id)
    if action_type == "add_note_tags":
        return await _mutate_note_tags(db, workspace=workspace, config=config, add=True, automation_id=automation_id)
    if action_type == "remove_note_tags":
        return await _mutate_note_tags(db, workspace=workspace, config=config, add=False, automation_id=automation_id)
    if action_type == "set_note_type":
        return await _set_note_type(db, workspace=workspace, config=config, automation_id=automation_id)
    if action_type == "link_notes":
        return await _link_notes(db, workspace=workspace, config=config, automation_id=automation_id)
    if action_type == "submit_for_approval":
        return await _transition_approval(db, workspace=workspace, config=config, context=context, target_status=ApprovalWorkflowStatus.SUBMITTED, automation_id=automation_id)
    if action_type == "approve_note":
        return await _transition_approval(db, workspace=workspace, config=config, context=context, target_status=ApprovalWorkflowStatus.APPROVED, automation_id=automation_id)
    if action_type == "reject_note":
        return await _transition_approval(db, workspace=workspace, config=config, context=context, target_status=ApprovalWorkflowStatus.REJECTED, automation_id=automation_id)
    if action_type == "request_approval_changes":
        return await _transition_approval(db, workspace=workspace, config=config, context=context, target_status=ApprovalWorkflowStatus.NEEDS_CHANGES, automation_id=automation_id)

    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"No handler registered for action {action_type}")


async def _load_workspace(db: AsyncSession, workspace_id: UUID) -> Workspace:
    workspace = (await db.execute(select(Workspace).where(Workspace.id == workspace_id))).scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return workspace


async def _load_workspace_note(db: AsyncSession, *, workspace_id: UUID, note_id: str) -> Note:
    note = (await db.execute(select(Note).where(Note.id == UUID(str(note_id))))).scalar_one_or_none()
    if note is None or note.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace note not found")
    return note


async def _resolve_actor_id(db: AsyncSession, *, workspace: Workspace, context: Mapping[str, Any]) -> UUID:
    for path in ("user.id", "author.id"):
        raw = _get_path(context, path)
        if raw:
            try:
                user_id = UUID(str(raw))
            except ValueError:
                continue
            exists = (await db.execute(select(DBUser.id).where(DBUser.id == user_id))).scalar_one_or_none()
            if exists:
                return user_id
    return workspace.owner_id


async def _send_notification(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    automation_id: str,
    action_id: Optional[str],
) -> Dict[str, Any]:
    actor_id = await _resolve_actor_id(db, workspace=workspace, context=context)
    recipient_ids = _string_list(config.get("recipientUserIds"))
    created = 0
    for raw_user_id in recipient_ids:
        user_id = UUID(raw_user_id)
        db.add(
            UserNotification(
                user_id=user_id,
                actor_user_id=actor_id,
                workspace_id=workspace.id,
                note_id=_maybe_uuid(_get_path(context, "note.id")),
                notification_type=UserNotificationType.AUTOMATION,
                payload={
                    "title": str(config.get("title") or ""),
                    "message": str(config.get("message") or ""),
                    "automation_id": automation_id,
                    "action_id": action_id,
                },
            )
        )
        created += 1
    await db.flush()
    return {"notifications_created": created}


async def _create_note(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    automation_id: str,
) -> Dict[str, Any]:
    actor_id = await _resolve_actor_id(db, workspace=workspace, context=context)
    note_type = str(config.get("noteType") or "note")
    if note_type not in NOTE_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported note type")

    content = str(config.get("content") or "")
    note = Note(
        workspace_id=workspace.id,
        user_id=actor_id,
        title=str(config.get("title") or "Untitled automation note"),
        content=content,
        tags=_string_list(config.get("tags")),
        connections=[],
        note_type=note_type,
        word_count=len(content.split()),
    )
    db.add(note)
    await db.flush()
    await log_audit_event(
        db,
        action=AuditAction.NOTE_CREATED,
        actor_user_id=actor_id,
        workspace_id=workspace.id,
        note_id=note.id,
        entity_type=EntityType.NOTE,
        entity_id=note.id,
        metadata={"source": "automation", "automation_id": automation_id},
    )
    return {"note_id": str(note.id)}


async def _update_note(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    automation_id: str,
) -> Dict[str, Any]:
    note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("noteId") or ""))
    changed_fields: List[str] = []
    if "title" in config and config.get("title") is not None:
        note.title = str(config.get("title"))
        changed_fields.append("title")
    if "content" in config and config.get("content") is not None:
        note.content = str(config.get("content"))
        note.word_count = len(note.content.split())
        changed_fields.append("content")
    if "tags" in config and config.get("tags") is not None:
        note.tags = _string_list(config.get("tags"))
        changed_fields.append("tags")
    if "noteType" in config and config.get("noteType") is not None:
        note.note_type = _validate_note_type(config.get("noteType"))
        changed_fields.append("note_type")
    note.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await _audit_note_update(db, note=note, context=context, changed_fields=changed_fields, automation_id=automation_id)
    return {"note_id": str(note.id), "changed_fields": changed_fields}


async def _append_note_content(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    automation_id: str,
) -> Dict[str, Any]:
    note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("noteId") or ""))
    append_content = str(config.get("content") or "")
    separator = "\n\n" if note.content and append_content else ""
    note.content = f"{note.content or ''}{separator}{append_content}"
    note.word_count = len(note.content.split())
    note.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await _audit_note_update(db, note=note, context=context, changed_fields=["content"], automation_id=automation_id)
    return {"note_id": str(note.id), "appended": len(append_content)}


async def _mutate_note_tags(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    add: bool,
    automation_id: str,
) -> Dict[str, Any]:
    note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("noteId") or ""))
    current = [str(tag) for tag in (note.tags or [])]
    requested = _string_list(config.get("tags"))
    if add:
        note.tags = current + [tag for tag in requested if tag not in current]
    else:
        note.tags = [tag for tag in current if tag not in set(requested)]
    note.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return {"note_id": str(note.id), "tags": note.tags, "automation_id": automation_id}


async def _set_note_type(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    automation_id: str,
) -> Dict[str, Any]:
    note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("noteId") or ""))
    note.note_type = _validate_note_type(config.get("noteType"))
    note.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return {"note_id": str(note.id), "note_type": note.note_type, "automation_id": automation_id}


async def _link_notes(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    automation_id: str,
) -> Dict[str, Any]:
    source_note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("sourceNoteId") or ""))
    target_note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("targetNoteId") or ""))
    source_connections = {str(value) for value in (source_note.connections or [])}
    target_connections = {str(value) for value in (target_note.connections or [])}
    source_connections.add(str(target_note.id))
    target_connections.add(str(source_note.id))
    source_note.connections = sorted(source_connections)
    target_note.connections = sorted(target_connections)
    source_note.updated_at = datetime.now(timezone.utc)
    target_note.updated_at = source_note.updated_at
    await db.flush()
    return {"source_note_id": str(source_note.id), "target_note_id": str(target_note.id), "automation_id": automation_id}


async def _transition_approval(
    db: AsyncSession,
    *,
    workspace: Workspace,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    target_status: ApprovalWorkflowStatus,
    automation_id: str,
) -> Dict[str, Any]:
    note = await _load_workspace_note(db, workspace_id=workspace.id, note_id=str(config.get("noteId") or ""))
    actor_id = await _resolve_actor_id(db, workspace=workspace, context=context)
    from_status = _coerce_status(note.approval_status)
    priority = _coerce_priority(config.get("priority") or note.approval_priority or ApprovalWorkflowPriority.NORMAL)
    comment = str(config.get("comment") or "").strip() or None

    note.approval_status = target_status
    note.approval_priority = priority
    note.updated_at = datetime.now(timezone.utc)
    if target_status == ApprovalWorkflowStatus.SUBMITTED:
        note.approval_submitted_at = note.updated_at
        note.approval_submitted_by_user_id = actor_id
        note.approval_decided_at = None
        note.approval_decided_by_user_id = None
    else:
        note.approval_decided_at = note.updated_at
        note.approval_decided_by_user_id = actor_id

    db.add(
        NoteApprovalTransition(
            note_id=note.id,
            workspace_id=workspace.id,
            actor_user_id=actor_id,
            from_status=from_status,
            to_status=target_status,
            comment=comment,
            priority_snapshot=priority,
            due_at_snapshot=note.approval_due_at,
        )
    )
    await db.flush()
    return {
        "note_id": str(note.id),
        "from_status": from_status.value,
        "to_status": target_status.value,
        "automation_id": automation_id,
    }


async def _audit_note_update(
    db: AsyncSession,
    *,
    note: Note,
    context: Mapping[str, Any],
    changed_fields: Iterable[str],
    automation_id: str,
) -> None:
    actor_id = _maybe_uuid(_get_path(context, "user.id")) or _maybe_uuid(_get_path(context, "author.id"))
    await log_audit_event(
        db,
        action=AuditAction.NOTE_UPDATED,
        actor_user_id=actor_id,
        workspace_id=note.workspace_id,
        note_id=note.id,
        entity_type=EntityType.NOTE,
        entity_id=note.id,
        metadata={
            "source": "automation",
            "automation_id": automation_id,
            "changed_fields": list(changed_fields),
        },
    )


def _get_path(source: Mapping[str, Any], path: str) -> Any:
    current: Any = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _maybe_uuid(value: Any) -> Optional[UUID]:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _validate_note_type(value: Any) -> str:
    note_type = str(value or "")
    if note_type not in NOTE_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported note type")
    return note_type


def _coerce_status(value: Any) -> ApprovalWorkflowStatus:
    return value if isinstance(value, ApprovalWorkflowStatus) else ApprovalWorkflowStatus(str(value))


def _coerce_priority(value: Any) -> ApprovalWorkflowPriority:
    return value if isinstance(value, ApprovalWorkflowPriority) else ApprovalWorkflowPriority(str(value))


def _is_valid_field_path(path: str) -> bool:
    if not path or len(path) > 200 or not FIELD_PATH_RE.match(path):
        return False
    return not any(segment in BLOCKED_FIELD_PATH_SEGMENTS for segment in path.split("."))


def _require_string(config: Mapping[str, Any], key: str) -> None:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be a non-empty string")


def _optional_string(config: Mapping[str, Any], key: str) -> None:
    value = config.get(key)
    if value is not None and not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be a string")


def _require_string_list(config: Mapping[str, Any], key: str) -> None:
    value = config.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be a non-empty string list")


def _optional_string_list(config: Mapping[str, Any], key: str) -> None:
    value = config.get(key)
    if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be a string list")


def _require_one_of(config: Mapping[str, Any], key: str, allowed: frozenset[str]) -> None:
    value = config.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be one of: {', '.join(sorted(allowed))}")


def _optional_one_of(config: Mapping[str, Any], key: str, allowed: frozenset[str]) -> None:
    value = config.get(key)
    if value is not None and (not isinstance(value, str) or value not in allowed):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Action field {key} must be one of: {', '.join(sorted(allowed))}")


def _validate_action_url(value: str) -> None:
    if "{{" in value and "}}" in value:
        return

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Action field url must be an absolute http(s) URL or template")
