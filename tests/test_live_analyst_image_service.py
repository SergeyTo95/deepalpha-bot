from services.live_analyst_image_service import analyze_live_image, IMAGE_PROVIDER_UNAVAILABLE_FALLBACK
from services.llm_service import ProviderUnavailableText


def test_live_image_stops_after_permission_denied_without_crops():
    result = analyze_live_image(b"img", llm_call=lambda prompt: ProviderUnavailableText("", "permission_denied"))
    assert result["provider_error"] == "permission_denied"
    assert result["crops_attempted"] == 0
    assert result["nested_crops_attempted"] == 0
    assert result["text"] == IMAGE_PROVIDER_UNAVAILABLE_FALLBACK


def test_live_image_stops_after_quota_exceeded():
    result = analyze_live_image(b"img", llm_call=lambda prompt: ProviderUnavailableText("", "quota_exceeded"))
    assert result["provider_error"] == "quota_exceeded"
    assert result["crops_attempted"] == 0


def test_generic_image_fallback_still_attempts_crops():
    result = analyze_live_image(b"img", llm_call=lambda prompt: "")
    assert result["provider_error"] is None
    assert result["crops_attempted"] == 1
    assert result["nested_crops_attempted"] == 1
