from services.live_answer_composer_service import is_strict_non_market_composer


def test_strict_non_market_by_mode():
    assert is_strict_non_market_composer({"composer_mode": "technical_debug"})
    assert is_strict_non_market_composer({"composer_mode": "business"})


def test_strict_non_market_by_role():
    assert is_strict_non_market_composer({"system_role": "Act as an incident responder for ops"})


def test_market_mode_is_not_strict():
    assert not is_strict_non_market_composer({"composer_mode": "sports"})
