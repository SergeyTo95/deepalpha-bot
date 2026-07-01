from services import user_analyst_profile_service as svc


def setup_function():
    svc._MEMORY_PROFILES.clear()


def test_default_profile_is_valid():
    p = svc.get_user_analyst_profile(1)
    assert p["risk_style"] == "balanced"
    assert p["answer_depth"] == "normal"
    assert p["primary_goal"] == "find_opportunities"
    assert p["preferred_domains"] == ["crypto", "sports", "esports", "politics", "polymarket"]


def test_invalid_risk_style_falls_back_to_balanced():
    assert svc.update_user_analyst_profile(2, risk_style="wild")["risk_style"] == "balanced"


def test_invalid_answer_depth_falls_back_to_normal():
    assert svc.update_user_analyst_profile(3, answer_depth="essay")["answer_depth"] == "normal"


def test_invalid_primary_goal_falls_back_to_find_opportunities():
    assert svc.update_user_analyst_profile(4, primary_goal="profit")["primary_goal"] == "find_opportunities"


def test_preferred_domains_are_deduped():
    p = svc.update_user_analyst_profile(5, preferred_domains=["crypto", "sports", "crypto"])
    assert p["preferred_domains"] == ["crypto", "sports"]


def test_invalid_domains_are_removed():
    p = svc.update_user_analyst_profile(6, preferred_domains=["crypto", "casino", "macro"])
    assert p["preferred_domains"] == ["crypto", "macro"]


def test_empty_domains_fallback_to_defaults():
    p = svc.update_user_analyst_profile(7, preferred_domains=[])
    assert p["preferred_domains"] == svc.DEFAULT_PREFERRED_DOMAINS


def test_format_user_analyst_profile_contains_risk_depth_goal_domains():
    text = svc.format_user_analyst_profile(8, "en")
    assert "Risk:" in text
    assert "Answer depth:" in text
    assert "Goal:" in text
    assert "Markets:" in text


def test_aggressive_risk_does_not_disable_safety_wording_rules():
    svc.update_user_analyst_profile(9, risk_style="aggressive")
    block = svc.build_user_analyst_profile_prompt_block(9)
    assert "risk_style: aggressive" in block
    assert "never imply guaranteed win" in block
    assert "financial advice" in block


def test_profile_prompt_block_contains_never_promise_profit():
    block = svc.build_user_analyst_profile_prompt_block(10)
    assert "Never promise profit" in block


def test_reset_user_analyst_profile_does_not_raise_and_restores_defaults():
    svc.update_user_analyst_profile(
        11,
        risk_style="aggressive",
        answer_depth="deep",
        primary_goal="check_my_idea",
        preferred_domains=["macro", "general_events"],
    )
    p = svc.reset_user_analyst_profile(11)
    assert p["risk_style"] == "balanced"
    assert p["answer_depth"] == "normal"
    assert p["primary_goal"] == "find_opportunities"
    assert p["preferred_domains"] == svc.DEFAULT_PREFERRED_DOMAINS


def test_parse_analyst_profile_set_callback_accepts_expected_callbacks():
    assert svc.parse_analyst_profile_set_callback("analyst_profile_set:risk_style:balanced") == ("risk_style", "balanced")
    assert svc.parse_analyst_profile_set_callback("analyst_profile_set:answer_depth:deep") == ("answer_depth", "deep")
    assert svc.parse_analyst_profile_set_callback("analyst_profile_set:primary_goal:check_my_idea") == ("primary_goal", "check_my_idea")
