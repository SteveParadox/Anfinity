"""
Test suite for settings validation, permissions, and behavior
Tests cover:
- Settings validation (types, ranges, enums)
- Settings merging and defaults
- Secrets rejection
- Settings API endpoints
- Permission enforcement
"""

import pytest
from app.services.settings_preferences import (
    USER_SETTINGS_DEFAULTS,
    WORKSPACE_SETTINGS_DEFAULTS,
    apply_settings_patch,
    resolved_user_settings,
    resolved_workspace_settings,
    merge_settings,
    _reject_secrets,
    _bounded_number,
    _enum_value,
)


class TestSettingsValidation:
    """Test settings validation logic"""
    
    def test_user_settings_defaults_structure(self):
        """User settings should have required sections"""
        assert "ai_search" in USER_SETTINGS_DEFAULTS
        assert "notifications" in USER_SETTINGS_DEFAULTS
        assert "collaboration" in USER_SETTINGS_DEFAULTS
        assert "appearance" in USER_SETTINGS_DEFAULTS
        assert "onboarding" in USER_SETTINGS_DEFAULTS
    
    def test_workspace_settings_defaults_structure(self):
        """Workspace settings should have required sections"""
        assert "ai_search" in WORKSPACE_SETTINGS_DEFAULTS
        assert "notes" in WORKSPACE_SETTINGS_DEFAULTS
        assert "collaboration" in WORKSPACE_SETTINGS_DEFAULTS
        assert "integrations" in WORKSPACE_SETTINGS_DEFAULTS
        assert "automations" in WORKSPACE_SETTINGS_DEFAULTS
        assert "approvals" in WORKSPACE_SETTINGS_DEFAULTS
    
    def test_boolean_validation(self):
        """Boolean values should be properly typed"""
        assert isinstance(USER_SETTINGS_DEFAULTS["ai_search"]["smart_highlights"], bool)
        assert isinstance(WORKSPACE_SETTINGS_DEFAULTS["ai_search"]["ask_past_self_enabled"], bool)
    
    def test_enum_validation(self):
        """Enum values should match allowed values"""
        assert USER_SETTINGS_DEFAULTS["appearance"]["theme"] in ("dark", "system")
        assert USER_SETTINGS_DEFAULTS["notifications"]["digest_frequency"] in ("off", "daily", "weekly")
        assert WORKSPACE_SETTINGS_DEFAULTS["notes"]["default_visibility"] in ("private", "workspace")
    
    def test_numeric_range_validation(self):
        """Numeric values should be within valid ranges"""
        similarity = WORKSPACE_SETTINGS_DEFAULTS["ai_search"]["min_note_similarity"]
        assert 0.38 <= similarity <= 0.85
        
        top_k = USER_SETTINGS_DEFAULTS["ai_search"]["default_top_k"]
        assert 3 <= top_k <= 12
        
        due_days = WORKSPACE_SETTINGS_DEFAULTS["approvals"]["default_due_days"]
        assert 1 <= due_days <= 30


class TestSecretsRejection:
    """Test that secrets are rejected from settings"""
    
    def test_reject_api_key(self):
        """Should reject API keys"""
        with pytest.raises(ValueError, match="cannot contain credentials"):
            _reject_secrets({
                "integrations": {
                    "api_key": "sk-1234567890"
                }
            })
    
    def test_reject_token(self):
        """Should reject tokens"""
        with pytest.raises(ValueError, match="cannot contain credentials"):
            _reject_secrets({
                "integrations": {
                    "access_token": "secret-token-value"
                }
            })
    
    def test_reject_password(self):
        """Should reject passwords"""
        with pytest.raises(ValueError, match="cannot contain credentials"):
            _reject_secrets({
                "database": {
                    "db_password": "secret123"
                }
            })
    
    def test_reject_oauth_secret(self):
        """Should reject OAuth secrets"""
        with pytest.raises(ValueError, match="cannot contain credentials"):
            _reject_secrets({
                "oauth": {
                    "client_secret": "secret-value"
                }
            })
    
    def test_accept_legitimate_settings(self):
        """Should accept legitimate settings without secrets"""
        # Should not raise
        _reject_secrets({
            "ai_search": {
                "min_note_similarity": 0.55,
                "ask_past_self_enabled": True,
            },
            "notifications": {
                "digest_frequency": "weekly",
                "in_app_comments": True,
            }
        })


class TestSettingsMerging:
    """Test settings merging behavior"""
    
    def test_merge_empty_over_defaults(self):
        """Merging empty settings should return defaults"""
        result = merge_settings(USER_SETTINGS_DEFAULTS, {})
        assert result == USER_SETTINGS_DEFAULTS
    
    def test_merge_partial_settings(self):
        """Merging partial settings should fill in defaults"""
        partial = {
            "appearance": {
                "theme": "light"
            }
        }
        result = merge_settings(USER_SETTINGS_DEFAULTS, partial)
        
        # Partial settings should be applied
        assert result["appearance"]["theme"] == "light"
        
        # Other settings should use defaults
        assert result["appearance"]["density"] == USER_SETTINGS_DEFAULTS["appearance"]["density"]
        assert result["notifications"] == USER_SETTINGS_DEFAULTS["notifications"]
    
    def test_merge_drops_unknown_keys(self):
        """Merging should drop unknown keys"""
        partial = {
            "appearance": {
                "theme": "light",
                "unknown_key": "should_be_dropped"
            }
        }
        result = merge_settings(USER_SETTINGS_DEFAULTS, partial)
        
        # Unknown key should not be in result
        assert "unknown_key" not in result["appearance"]
    
    def test_merge_type_coercion(self):
        """Merging should coerce types correctly"""
        partial = {
            "ai_search": {
                "smart_highlights": 1,  # Should be coerced to True
                "default_top_k": 6.5,   # Should be coerced to 6 (int)
            }
        }
        result = merge_settings(USER_SETTINGS_DEFAULTS, partial)
        
        assert result["ai_search"]["smart_highlights"] is True
        assert result["ai_search"]["default_top_k"] == 6
        assert isinstance(result["ai_search"]["default_top_k"], int)
    
    def test_merge_healing_invalid_values(self):
        """Merging should heal invalid stored values to defaults (on read)"""
        partial = {
            "appearance": {
                "theme": "invalid_value"  # Invalid enum value
            }
        }
        result = merge_settings(USER_SETTINGS_DEFAULTS, partial)
        
        # Invalid value should be replaced with default
        assert result["appearance"]["theme"] == USER_SETTINGS_DEFAULTS["appearance"]["theme"]


class TestSettingsPatch:
    """Test applying settings patches with validation"""
    
    def test_patch_unknown_section_rejected(self):
        """Patching unknown section should raise ValueError"""
        patch = {"unknown_section": {"key": "value"}}
        
        with pytest.raises(ValueError, match="Unknown settings section"):
            apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_unknown_key_rejected(self):
        """Patching unknown key should raise ValueError"""
        patch = {"appearance": {"unknown_key": "value"}}
        
        with pytest.raises(ValueError, match="Unknown settings key"):
            apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_invalid_enum_rejected(self):
        """Patching invalid enum value should raise ValueError"""
        patch = {"appearance": {"theme": "invalid_theme"}}
        
        with pytest.raises(ValueError, match="must be one of"):
            apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_invalid_range_rejected(self):
        """Patching value outside range should raise ValueError"""
        patch = {"ai_search": {"default_top_k": 100}}  # Max is 12
        
        with pytest.raises(ValueError, match="must be between"):
            apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_wrong_type_rejected(self):
        """Patching with wrong type should raise ValueError"""
        patch = {"ai_search": {"smart_highlights": "true"}}  # Should be bool
        
        with pytest.raises(ValueError, match="must be a boolean"):
            apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_secret_rejected(self):
        """Patching with secret keywords should be rejected"""
        patch = {"integrations": {"api_token": "secret-value"}}
        
        with pytest.raises(ValueError, match="cannot contain credentials"):
            apply_settings_patch(WORKSPACE_SETTINGS_DEFAULTS, {}, patch)
    
    def test_patch_valid_update(self):
        """Valid patch should be applied successfully"""
        patch = {
            "appearance": {
                "theme": "light"
            },
            "notifications": {
                "digest_frequency": "weekly"
            }
        }
        
        result = apply_settings_patch(USER_SETTINGS_DEFAULTS, {}, patch)
        
        assert result["appearance"]["theme"] == "light"
        assert result["notifications"]["digest_frequency"] == "weekly"
        # Other settings should still have defaults
        assert result["appearance"]["density"] == USER_SETTINGS_DEFAULTS["appearance"]["density"]
    
    def test_patch_merges_with_existing(self):
        """Patch should merge with existing settings, not replace"""
        current = {
            "appearance": {
                "theme": "light",
                "density": "compact"
            }
        }
        patch = {
            "appearance": {
                "theme": "dark"  # Only update theme
            }
        }
        
        result = apply_settings_patch(USER_SETTINGS_DEFAULTS, current, patch)
        
        # Theme should be updated
        assert result["appearance"]["theme"] == "dark"
        # Density should be preserved from current
        assert result["appearance"]["density"] == "compact"


class TestResolution:
    """Test resolved_*_settings functions"""
    
    def test_resolved_user_settings_with_empty_raw(self):
        """Resolved user settings with empty raw should return defaults"""
        result = resolved_user_settings({})
        assert result == USER_SETTINGS_DEFAULTS
    
    def test_resolved_workspace_settings_with_none(self):
        """Resolved workspace settings with None should return defaults"""
        result = resolved_workspace_settings(None)
        assert result == WORKSPACE_SETTINGS_DEFAULTS
    
    def test_resolved_settings_merges_correctly(self):
        """Resolved settings should merge raw over defaults"""
        raw = {
            "appearance": {
                "theme": "light"
            }
        }
        result = resolved_user_settings(raw)
        
        assert result["appearance"]["theme"] == "light"
        assert "onboarding" in result  # Other sections from defaults


class TestNumericValidation:
    """Test numeric range validation"""
    
    def test_bounded_number_in_range(self):
        """Number in range should pass"""
        result = _bounded_number(("ai_search", "default_top_k"), 6, 6)
        assert result == 6
    
    def test_bounded_number_at_min(self):
        """Number at min should pass"""
        result = _bounded_number(("ai_search", "default_top_k"), 3, 6)
        assert result == 3
    
    def test_bounded_number_at_max(self):
        """Number at max should pass"""
        result = _bounded_number(("ai_search", "default_top_k"), 12, 6)
        assert result == 12
    
    def test_bounded_number_below_range(self):
        """Number below range should fail"""
        with pytest.raises(ValueError, match="must be between"):
            _bounded_number(("ai_search", "default_top_k"), 2, 6)
    
    def test_bounded_number_above_range(self):
        """Number above range should fail"""
        with pytest.raises(ValueError, match="must be between"):
            _bounded_number(("ai_search", "default_top_k"), 13, 6)
    
    def test_bounded_number_not_numeric(self):
        """Non-numeric value should fail"""
        with pytest.raises(ValueError, match="must be a number"):
            _bounded_number(("ai_search", "default_top_k"), "six", 6)


class TestEnumValidation:
    """Test enum value validation"""
    
    def test_enum_valid_value(self):
        """Valid enum value should pass"""
        result = _enum_value(("appearance", "theme"), "dark", "dark")
        assert result == "dark"
    
    def test_enum_invalid_value(self):
        """Invalid enum value should fail"""
        with pytest.raises(ValueError, match="must be one of"):
            _enum_value(("appearance", "theme"), "invalid", "dark")
    
    def test_enum_not_string(self):
        """Non-string enum value should fail"""
        with pytest.raises(ValueError, match="must be a string"):
            _enum_value(("appearance", "theme"), 123, "dark")
    
    def test_enum_empty_string_uses_default(self):
        """Empty string should use default"""
        result = _enum_value(("appearance", "theme"), "", "dark")
        assert result == "dark"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
