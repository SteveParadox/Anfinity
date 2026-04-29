"""Typed settings defaults, validation, and behavior helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User as DBUser, UserNotificationType, Workspace


# User settings defaults - personal preferences that don't affect shared data.
USER_SETTINGS_DEFAULTS: dict[str, Any] = {
    "ai_search": {
        "smart_highlights": True,
        "show_source_cards": True,
        "show_similarity_scores": True,
        # Number of notes to retrieve (3-12). Default 6 balances quality and latency.
        "default_top_k": 6,
    },
    "notifications": {
        "in_app_comments": True,
        "in_app_mentions": True,
        "in_app_replies": True,
        "in_app_approvals": True,
        # Default weekly avoids notification fatigue. Users can opt into daily/off.
        "digest_frequency": "weekly",
    },
    "collaboration": {
        "presence_visible": True,
        "show_collaborator_cursors": True,
        "allow_note_invites": True,
    },
    "appearance": {
        "theme": "dark",
        "density": "comfortable",
    },
    "onboarding": {
        "assistant_tips": True,
    },
}


WORKSPACE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "ai_search": {
        "ask_past_self_enabled": True,
        # Minimum semantic similarity for note retrieval (0.38-0.85).
        # 0.55 reduces low-quality source matches without making search too brittle.
        "min_note_similarity": 0.55,
        "source_cards_default": True,
    },
    "notes": {
        # Safer default: new notes start private unless the workspace opts into sharing.
        "default_visibility": "private",
        "auto_tagging_enabled": True,
        "summary_generation_enabled": True,
        "connection_suggestions_enabled": True,
        "decay_classification_enabled": True,
    },
    "collaboration": {
        "comment_threads_enabled": True,
        "mentions_enabled": True,
        "invite_policy": "members",
    },
    "integrations": {
        # Safer default: do not sync external systems until an admin enables it.
        "auto_sync_enabled": False,
        "sync_frequency": "hourly",
    },
    "automations": {
        "enabled": True,
        "notify_on_failure": True,
    },
    "approvals": {
        "enabled": True,
        "default_priority": "normal",
        # Most workflows need more time for review than 3 days.
        "default_due_days": 5,
    },
}


_ENUMS: dict[tuple[str, ...], set[str]] = {
    ("notifications", "digest_frequency"): {"off", "daily", "weekly"},
    ("appearance", "theme"): {"dark", "light", "system"},
    ("appearance", "density"): {"compact", "comfortable"},
    ("notes", "default_visibility"): {"private", "workspace"},
    ("collaboration", "invite_policy"): {"owners_admins", "members"},
    ("integrations", "sync_frequency"): {"manual", "hourly", "daily"},
    ("approvals", "default_priority"): {"low", "normal", "high", "critical"},
}

_RANGES: dict[tuple[str, ...], tuple[float, float]] = {
    ("ai_search", "min_note_similarity"): (0.38, 0.85),
    ("ai_search", "default_top_k"): (3, 12),
    ("approvals", "default_due_days"): (1, 30),
}

_SECRET_KEY_EXACT = frozenset(
    {
        "token",
        "secret",
        "password",
        "credential",
        "credentials",
        "authorization",
        "api_key",
        "api_token",
        "apikey",
        "access_token",
        "refresh_token",
        "bearer_token",
        "oauth_token",
        "client_secret",
        "private_key",
        "connection_string",
        "db_password",
        "database_password",
        "db_url",
        "database_url",
        "s3_key",
        "s3_secret",
        "aws_key",
        "aws_secret",
        "gcp_key",
        "azure_key",
        "sendgrid_key",
        "mailgun_key",
        "twilio_key",
    }
)
_SECRET_KEY_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_credential",
    "_credentials",
    "_api_key",
    "_private_key",
)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def resolved_user_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    return merge_settings(USER_SETTINGS_DEFAULTS, raw or {})


def resolved_workspace_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    return merge_settings(WORKSPACE_SETTINGS_DEFAULTS, raw or {})


def merge_settings(defaults: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Merge stored settings over defaults, dropping unknown and invalid keys.

    Reads are intentionally forgiving because old rows may contain stale JSON. We do not
    coerce strings like "false" into booleans, because that silently turns bad persisted
    data into surprising behavior. Bad stored values heal back to defaults.
    """
    merged = deepcopy(dict(defaults))
    if not _is_mapping(raw):
        return merged

    for section, default_section in defaults.items():
        raw_section = raw.get(section)
        if not _is_mapping(default_section) or not _is_mapping(raw_section):
            continue
        for key, default_value in default_section.items():
            if key not in raw_section:
                continue
            try:
                merged[section][key] = _coerce_setting_value((section, key), raw_section[key], default_value, strict=False)
            except ValueError:
                merged[section][key] = deepcopy(default_value)
    return merged


def apply_settings_patch(defaults: Mapping[str, Any], current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a partial settings update without overwriting unrelated preferences.

    Writes are strict: unknown sections/keys, invalid enum values, invalid types, and
    credential-looking fields are rejected instead of being saved or silently ignored.
    """
    validate_settings_patch(defaults, patch)

    next_settings = merge_settings(defaults, current)
    for section, section_patch in patch.items():
        for key, value in section_patch.items():
            default_value = defaults[section][key]
            next_settings[section][key] = _coerce_setting_value((section, key), value, default_value, strict=True)
    return next_settings


def validate_settings_patch(defaults: Mapping[str, Any], patch: Mapping[str, Any]) -> None:
    if not _is_mapping(patch):
        raise ValueError("Settings patch must be an object")

    _reject_secrets(patch)

    for section, section_patch in patch.items():
        if section not in defaults:
            raise ValueError(f"Unknown settings section: {section}")
        if not _is_mapping(section_patch):
            raise ValueError(f"Settings section must be an object: {section}")
        default_section = defaults[section]
        if not _is_mapping(default_section):
            raise ValueError(f"Settings section is not configurable: {section}")
        for key in section_patch:
            if key not in default_section:
                raise ValueError(f"Unknown settings key: {section}.{key}")


def settings_patch_paths(patch: Mapping[str, Any]) -> list[str]:
    """Return dot-paths changed by a patch for audit logs without storing values."""
    if not _is_mapping(patch):
        return []
    paths: list[str] = []
    for section, section_patch in patch.items():
        if not _is_mapping(section_patch):
            paths.append(str(section))
            continue
        for key in section_patch:
            paths.append(f"{section}.{key}")
    return sorted(paths)


def _coerce_setting_value(path: tuple[str, str], value: Any, default: Any, *, strict: bool) -> Any:
    if isinstance(default, bool):
        return _bool_value(path, value, default, strict=strict)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(_bounded_number(path, value, default, strict=strict))
    if isinstance(default, float):
        return float(_bounded_number(path, value, default, strict=strict))
    if isinstance(default, str):
        return _enum_value(path, value, default, strict=strict)
    if strict:
        raise ValueError(f"Unsupported settings type for {'.'.join(path)}")
    return deepcopy(default)


def _bool_value(path: tuple[str, str], value: Any, default: bool, *, strict: bool) -> bool:
    if isinstance(value, bool):
        return value
    if strict:
        raise ValueError(f"{'.'.join(path)} must be a boolean")
    return default


def _bounded_number(path: tuple[str, str], value: Any, default: float | int, *, strict: bool) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        if strict:
            raise ValueError(f"{'.'.join(path)} must be a number")
        return default
    low, high = _RANGES.get(path, (float("-inf"), float("inf")))
    if value < low or value > high:
        if strict:
            raise ValueError(f"{'.'.join(path)} must be between {low:g} and {high:g}")
        return default
    return value


def _enum_value(path: tuple[str, str], value: Any, default: str, *, strict: bool) -> str:
    if not isinstance(value, str) or not value:
        if strict:
            raise ValueError(f"{'.'.join(path)} must be a non-empty string")
        return default
    allowed = _ENUMS.get(path)
    if allowed is not None and value not in allowed:
        if strict:
            raise ValueError(f"{'.'.join(path)} must be one of: {', '.join(sorted(allowed))}")
        return default
    return value


def _normalise_key(key: object) -> str:
    return str(key).strip().lower().replace("-", "_").replace(".", "_")


def _looks_like_secret_key(key: object) -> bool:
    normalised = _normalise_key(key)
    return normalised in _SECRET_KEY_EXACT or any(normalised.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES)


def _reject_secrets(settings: Mapping[str, Any]) -> None:
    """Reject credential-looking keys anywhere in a settings payload.

    Product settings are sent back to clients. OAuth/API credentials belong in encrypted
    connector tables or server-side config, not in a JSONB preferences blob.
    """

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                key_path = (*path, str(key))
                if _looks_like_secret_key(key):
                    raise ValueError(
                        "Settings cannot contain credentials. "
                        f"Found '{'.'.join(key_path)}'. Store secrets in config.py or encrypted tables instead."
                    )
                visit(child, key_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    visit(settings, ())


def notification_setting_key(notification_type: UserNotificationType | str) -> str:
    raw = notification_type.value if isinstance(notification_type, UserNotificationType) else str(notification_type)
    if raw == UserNotificationType.NOTE_COMMENT.value:
        return "in_app_comments"
    if raw == UserNotificationType.COMMENT_MENTION.value:
        return "in_app_mentions"
    if raw == UserNotificationType.COMMENT_REPLY.value:
        return "in_app_replies"
    if raw.startswith("approval_"):
        return "in_app_approvals"
    return ""


async def user_allows_notification(
    db: AsyncSession,
    user_id: UUID,
    notification_type: UserNotificationType | str,
) -> bool:
    key = notification_setting_key(notification_type)
    if not key:
        return True
    result = await db.execute(select(DBUser.settings).where(DBUser.id == user_id))
    raw_settings = result.scalar_one_or_none() or {}
    settings = resolved_user_settings(raw_settings)
    return bool(settings["notifications"].get(key, True))


async def filter_notification_recipients(
    db: AsyncSession,
    recipient_ids: set[UUID],
    notification_type: UserNotificationType | str,
) -> set[UUID]:
    if not recipient_ids:
        return set()

    key = notification_setting_key(notification_type)
    if not key:
        return set(recipient_ids)

    result = await db.execute(select(DBUser.id, DBUser.settings).where(DBUser.id.in_(recipient_ids)))
    allowed: set[UUID] = set()
    for user_id, raw_settings in result.all():
        settings = resolved_user_settings(raw_settings or {})
        if bool(settings["notifications"].get(key, True)):
            allowed.add(user_id)
    return allowed


async def get_workspace_ai_min_similarity(db: AsyncSession, workspace_id: UUID) -> float:
    result = await db.execute(select(Workspace.settings).where(Workspace.id == workspace_id))
    raw_settings = result.scalar_one_or_none() or {}
    settings = resolved_workspace_settings(raw_settings)
    return float(settings["ai_search"]["min_note_similarity"])


async def workspace_feature_enabled(
    db: AsyncSession,
    workspace_id: UUID,
    section: str,
    key: str,
) -> bool:
    default_section = WORKSPACE_SETTINGS_DEFAULTS.get(section)
    if not _is_mapping(default_section) or key not in default_section:
        return False

    default_value = default_section[key]
    if not isinstance(default_value, bool):
        return False

    result = await db.execute(select(Workspace.settings).where(Workspace.id == workspace_id))
    raw_settings = result.scalar_one_or_none() or {}
    settings = resolved_workspace_settings(raw_settings)
    return bool(settings[section][key])
