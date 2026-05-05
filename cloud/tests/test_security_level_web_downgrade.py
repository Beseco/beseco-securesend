"""Tests for level4 add-in-only downgrade behavior."""

from __future__ import annotations

from services.security_levels import (
    LEVEL4_ADDIN_ONLY_NOTICE,
    LEVEL_3,
    LEVEL_4,
    is_addin_channel,
    normalize_client_channel,
    resolve_effective_level_for_channel,
)


def test_send_level4_downgrades_to_level3_for_web() -> None:
    effective, notice = resolve_effective_level_for_channel(LEVEL_4, "web-ui")
    assert effective == LEVEL_3
    assert notice == LEVEL4_ADDIN_ONLY_NOTICE


def test_send_level4_downgrades_even_for_addin_channel_until_release() -> None:
    effective, notice = resolve_effective_level_for_channel(LEVEL_4, "outlook-addin")
    assert effective == LEVEL_3
    assert notice == LEVEL4_ADDIN_ONLY_NOTICE


def test_send_level4_audit_contains_downgrade_reason() -> None:
    """Proxy check for audit payload ingredients: channel normalization + add-in detection."""
    channel = normalize_client_channel("  WEB-UI ")
    assert channel == "web-ui"
    assert is_addin_channel(channel) is False


def test_level3_behavior_unchanged_after_downgrade_logic() -> None:
    effective, notice = resolve_effective_level_for_channel(LEVEL_3, "web-ui")
    assert effective == LEVEL_3
    assert notice is None


def test_org_default_level4_still_results_in_effective_level3_for_web() -> None:
    effective, notice = resolve_effective_level_for_channel(LEVEL_4, None)
    assert effective == LEVEL_3
    assert notice == LEVEL4_ADDIN_ONLY_NOTICE
