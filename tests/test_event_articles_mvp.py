from services.event_article_service import create_article_payload_from_analysis, build_article_share_url


def test_create_article_from_analysis_payload():
    article = create_article_payload_from_analysis(
        {
            "question": "Will BTC close above 100k?",
            "url": "https://polymarket.com/event/btc",
            "display_prediction": "edge candidate",
            "market_probability": "52%",
            "reasoning": "Catalyst watch",
        },
        author_id=7,
    )
    assert article["author_id"] == 7
    assert article["title"] == "Will BTC close above 100k?"
    assert article["market_url"] == "https://polymarket.com/event/btc"
    assert article["status"] == "published"


def test_article_view_includes_title_author_market_url():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert "<b>{title}</b>" in source
    assert "Author: @" in source
    assert "Market: {market_url}" in source


def test_donation_button_points_to_article_post_donation_flow():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert "tab=donate&author={author_id}&post={post_id}" in source


def test_share_button_exists():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert "📤 Share article" in source
    assert "post_share_{post_id}" in source
    assert "share/url" in build_article_share_url("DeepAlphaAI_bot", 44, "Article")


def test_deleted_draft_articles_are_not_publicly_visible():
    source = open("db/database.py", encoding="utf-8").read()
    assert "is_deleted = 0 AND COALESCE(status, 'published') = 'published'" in source


def test_unsafe_words_are_filtered_from_auto_article_text():
    article = create_article_payload_from_analysis(
        {"question": "ставь now", "reasoning": "гарантия and 100%"},
        author_id=7,
    )
    combined = " ".join(str(v).lower() for v in article.values())
    assert "ставь" not in combined
    assert "гарантия" not in combined
    assert "100%" not in combined

def test_article_command_checks_daily_post_limit():
    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index('async def article_command_handler')
    end = source.index('@dp.callback_query_handler(lambda c: c.data.startswith("pub_article_"))', start)
    handler = source[start:end]
    assert "can_author_post_today(uid)" in handler
    assert "Дневной лимит публикаций исчерпан" in handler
    assert "Daily post limit reached" in handler


def test_article_deep_link_uses_html_parse_mode():
    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index('elif args.startswith("article_")')
    end = source.index('elif args.startswith("profile_")', start)
    block = source[start:end]
    assert '_format_author_post(post, message.from_user.id, show_author=True)' in block
    assert 'parse_mode="HTML"' in block


def test_post_and_article_view_uses_html_parse_mode():
    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index('async def post_view_handler')
    end = source.index('# ═══════════════════════════════════════════\n# CALLBACKS: PROFILE / BADGES', start)
    handler = source[start:end]
    assert '_format_author_post(post, uid, show_author=True)' in handler
    assert 'message.answer(text, parse_mode="HTML", reply_markup=kb)' in handler


def test_legacy_author_post_fields_are_escaped_for_html_parse_mode():
    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index('q = _escape(post.get("question", ""))')
    end = source.index('donations_line = ""', start)
    legacy_branch = source[start:end]
    assert 'category = _escape(post.get("category", ""))' in legacy_branch
    assert 'safe_username = _escape(author_username)' in legacy_branch
    assert 'safe_first_name = _escape(author_first_name)' in legacy_branch
    assert 'f"📢 {safe_first_name}' in legacy_branch


def test_manual_publish_share_hub_uses_real_referral_code_helper():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert '_share_hub_keyboard(post_id, payload.get("title"), str(uid))' not in source
    start = source.index('async def manual_article_preview_callback')
    end = source.index('@dp.message_handler(state=AuthorStates.waiting_article_edit_title)', start)
    block = source[start:end]
    assert 'get_or_create_referral_code' in block
    assert 'referral_code = None' in block
    assert '_share_hub_keyboard(post_id, payload.get("title"), referral_code)' in block


def test_webapp_articles_routing_happens_inside_init_before_render_authed():
    source = open("webapp/app.js", encoding="utf-8").read()
    assert 'if (new URLSearchParams(location.search).get("tab") === "articles") {\n  renderArticlesPage();\n}' not in source
    init_start = source.index('async function init()')
    init_end = source.index('init();', init_start)
    init_block = source[init_start:init_end]
    assert 'const tab = new URLSearchParams(location.search).get("tab");' in init_block
    assert 'return renderArticlesPage();' in init_block
    assert init_block.index('return renderArticlesPage();') < init_block.index('renderAuthed(summaryResp.data, lang);')
    assert source.index('async function renderArticlesPage') < source.index('async function init()')


def test_manual_article_preview_edit_buttons_have_handlers():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert 'waiting_article_edit_title' in source
    assert 'waiting_article_edit_body' in source
    assert 'waiting_article_edit_image' in source
    assert 'manual_article_edit_title_handler' in source
    assert 'manual_article_edit_body_handler' in source
    assert 'manual_article_edit_image_photo' in source
    assert 'manual_article_edit_image_skip' in source
