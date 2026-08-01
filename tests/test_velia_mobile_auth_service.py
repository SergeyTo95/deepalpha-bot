from services.velia_mobile_auth_service import (
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
