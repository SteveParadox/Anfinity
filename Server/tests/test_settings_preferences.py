from __future__ import annotations

import pytest

from app.database.models import UserNotificationType
from app.services.settings_preferences import (
    USER_SETTINGS_DEFAULTS,
    WORKSPACE_SETTINGS_DEFAULTS,
    apply_settings_patch,
    merge_settings,
    notification_setting_key,
    resolved_user_settings,
    resolved_workspace_settings,
)


def test_user_settings_defaults_are_resolved() -> None:
    settings = resolved_user_settings(None)

    assert settings["ai_search"]["show_source_cards"] is True
    assert settings["notifications"]["in_app_mentions"] is True
    assert settings["appearance"]["density"] == "comfortable"


def test_workspace_settings_defaults_are_resolved() -> None:
    settings = resolved_workspace_settings({})

    assert settings["ai_search"]["ask_past_self_enabled"] is True
    assert settings["ai_search"]["min_note_similarity"] == 0.46
    assert settings["notes"]["auto_tagging_enabled"] is True
    assert settings["automations"]["enabled"] is True


def test_partial_user_patch_preserves_unrelated_settings() -> None:
    current = {"notifications": {"in_app_mentions": False}}

    patched = apply_settings_patch(
        USER_SETTINGS_DEFAULTS,
        current,
        {"ai_search": {"default_top_k": 9}},
    )

    assert patched["ai_search"]["default_top_k"] == 9
    assert patched["notifications"]["in_app_mentions"] is False
    assert patched["notifications"]["in_app_comments"] is True


def test_workspace_patch_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Unknown settings key"):
        apply_settings_patch(
            WORKSPACE_SETTINGS_DEFAULTS,
            {},
            {"notes": {"imaginary_feature": True}},
        )


def test_workspace_patch_rejects_out_of_range_similarity() -> None:
    with pytest.raises(ValueError, match="min_note_similarity"):
        apply_settings_patch(
            WORKSPACE_SETTINGS_DEFAULTS,
            {},
            {"ai_search": {"min_note_similarity": 0.2}},
        )


def test_stored_invalid_settings_heal_to_defaults_on_read() -> None:
    merged = merge_settings(
        WORKSPACE_SETTINGS_DEFAULTS,
        {
            "ai_search": {"min_note_similarity": 99},
            "notes": {"default_visibility": "everyone"},
            "extra": {"ignored": True},
        },
    )

    assert merged["ai_search"]["min_note_similarity"] == WORKSPACE_SETTINGS_DEFAULTS["ai_search"]["min_note_similarity"]
    assert merged["notes"]["default_visibility"] == WORKSPACE_SETTINGS_DEFAULTS["notes"]["default_visibility"]
    assert "extra" not in merged


def test_notification_types_map_to_user_preferences() -> None:
    assert notification_setting_key(UserNotificationType.NOTE_COMMENT) == "in_app_comments"
    assert notification_setting_key(UserNotificationType.COMMENT_MENTION) == "in_app_mentions"
    assert notification_setting_key(UserNotificationType.COMMENT_REPLY) == "in_app_replies"
    assert notification_setting_key(UserNotificationType.APPROVAL_SUBMITTED) == "in_app_approvals"
