from datetime import datetime, timedelta

from services.velia_mobile_auth_service import (
    PERSISTENT_REFRESH_EXPIRES_AT,
    _resolve_refresh_expiry,
    format_pairing_code,
    normalize_pairing_code,
)


def test_pairing_code_normalization_accepts_display_format():
    assert normalize_pairing_code("ABCD-EFGH-JKLM-NPQR") == "ABCDEFGHJKLMNPQR"


def test_pairing_code_format_is_readable_and_reversible():
    raw = "ABCDEFGHJKLMNPQR"
    formatted = format_pairing_code(raw)
    assert formatted == "ABCD-EFGH-JKLM-NPQR"
    assert normalize_pairing_code(formatted) == raw


def test_pairing_code_normalization_drops_spaces_and_punctuation():
    assert normalize_pairing_code(" abcd efgh.jklm_npqr ") == "ABCDEFGHJKLMNPQR"


def test_mobile_sessions_are_persistent_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_MOBILE_PERSISTENT_SESSIONS", raising=False)
    monkeypatch.delenv("VELIA_MOBILE_REFRESH_TTL_DAYS", raising=False)

    now = datetime(2026, 8, 2, 12, 0, 0)

    assert _resolve_refresh_expiry(now) == PERSISTENT_REFRESH_EXPIRES_AT
    assert (
        _resolve_refresh_expiry(now, now + timedelta(days=3))
        == PERSISTENT_REFRESH_EXPIRES_AT
    )


def test_finite_mobile_session_mode_preserves_absolute_expiry(monkeypatch):
    monkeypatch.setenv("VELIA_MOBILE_PERSISTENT_SESSIONS", "false")
    monkeypatch.setenv("VELIA_MOBILE_REFRESH_TTL_DAYS", "30")

    now = datetime(2026, 8, 2, 12, 0, 0)
    existing_expiry = now + timedelta(days=7)

    assert _resolve_refresh_expiry(now) == now + timedelta(days=30)
    assert _resolve_refresh_expiry(now, existing_expiry) == existing_expiry
