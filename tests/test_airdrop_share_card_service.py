import os

from services import airdrop_share_card_service as svc


def setup_function():
    svc._MEMORY_LATEST.clear()
    svc._MEMORY_CARDS.clear()
    os.environ["BOT_USERNAME"] = "DeepAlphaAI_bot"


def test_build_share_card_payload_creates_referral_link():
    payload = svc.build_share_card_payload(123, "quick", title="Will BTC rally?")
    assert payload["referral_code"]
    assert payload["referral_link"].startswith("https://t.me/DeepAlphaAI_bot?start=ref_")


def test_format_share_card_text_contains_branding_and_referral_link():
    payload = svc.build_share_card_payload(123, "quick", title="Market", score=72)
    text = svc.format_share_card_text(payload, "en")
    assert "DeepAlpha AI Insight" in text
    assert payload["referral_link"] in text


def test_unsafe_words_are_not_included():
    payload = svc.build_share_card_payload(
        123,
        "quick",
        title="guaranteed 100%",
        short_summary="ставь and покупай for easy profit",
        key_risk="no guaranteed free money",
    )
    text = svc.format_share_card_text(payload, "ru").lower()
    for word in ["ставь", "покупай", "guaranteed", "easy profit", "free money"]:
        assert word not in text
    assert "100%" not in text


def test_missing_score_decision_uses_safe_fallback():
    payload = svc.build_share_card_payload(123, "quick")
    assert payload["score"] is None
    assert payload["decision"] == "WATCH"
    assert "DeepAlpha market insight" == payload["title"]


def test_save_latest_and_get_latest_work():
    payload = svc.build_share_card_payload(123, "quick", title="A")
    svc.save_latest_share_card(123, payload)
    assert svc.get_latest_share_card(123)["share_id"] == payload["share_id"]


def test_record_share_card_generated_is_idempotent_for_same_share_id():
    payload = svc.build_share_card_payload(123, "quick", title="A")
    first = svc.record_share_card_generated(123, payload)
    second = svc.record_share_card_generated(123, payload)
    assert first["ok"] is True
    assert second["deduped"] is True
    assert svc.get_share_card_stats(123)["total_share_cards_generated"] == 1


def test_admin_stats_include_total_and_top_users():
    p1 = svc.build_share_card_payload(123, "quick", title="A")
    p2 = svc.build_share_card_payload(456, "quick", title="B")
    svc.record_share_card_generated(123, p1)
    svc.record_share_card_generated(456, p2)
    stats = svc.admin_get_share_card_stats()
    assert stats["total_share_cards_generated"] >= 2
    assert stats["top_share_card_users"]
