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
    assert 'send_author_article(message, post, message.from_user.id' in block
    assert 'get_author_post_keyboard(message.from_user.id, post)' in block


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
    assert 'referral_code = str(uid)' not in source
    assert '_share_hub_keyboard(post_id, payload.get("title"), referral_code)' in block


def test_webapp_articles_routing_happens_inside_init_before_render_authed():
    source = open("webapp/app.js", encoding="utf-8").read()
    assert 'if (new URLSearchParams(location.search).get("tab") === "articles") {\n  renderArticlesPage();\n}' not in source
    init_start = source.index('async function init()')
    init_end = source.index('init();', init_start)
    init_block = source[init_start:init_end]
    assert 'const params = new URLSearchParams(location.search);' in init_block
    assert 'const tab = params.get("tab");' in init_block
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


def test_webapp_article_query_param_opens_detail():
    source = open("webapp/app.js", encoding="utf-8").read()
    assert 'new URLSearchParams(location.search).get("article")' in source
    assert 'const initialArticleId =' in source
    assert 'async function openArticleDetail(id)' in source
    render_start = source.index('async function renderArticlesPage')
    render_end = source.index('async function init()', render_start)
    render_block = source[render_start:render_end]
    assert 'callArticleDetail(id)' in render_block
    assert 'if (initialArticleId)' in render_block
    assert 'openArticleDetail(initialArticleId)' in render_block


def test_articles_public_init_before_dashboard_auth():
    source = open("webapp/app.js", encoding="utf-8").read()
    init_start = source.index('async function init()')
    init_end = source.index('init();', init_start)
    init_block = source[init_start:init_end]
    assert init_block.index('if (tab === "articles")') < init_block.index('const me = await callMe();')
    assert 'return renderGuest(guestLangFallback());' in init_block


def test_webapp_article_view_and_share_do_not_send_user_id():
    source = open("webapp/app.js", encoding="utf-8").read()
    assert 'JSON.stringify({ user_id' not in source
    assert '/view`' in source and 'JSON.stringify({})' in source
    assert '/share`' in source


def test_web_article_view_share_use_server_session_identity():
    source = open("web.py", encoding="utf-8").read()
    assert 'def _get_authenticated_web_user_id(request)' in source
    assert 'request.cookies.get("deepalpha_session"' in source
    assert 'get_user_by_session(token)' in source
    view_start = source.index('async def handle_article_view_api')
    share_start = source.index('async def handle_article_share_api')
    view_block = source[view_start:share_start]
    assert 'await request.json' not in view_block
    assert '_get_authenticated_web_user_id(request)' in view_block
    assert 'award_article_unique_view_points' in view_block
    share_block = source[share_start:source.index('async def handle_article_cover_api', share_start)]
    assert '_get_authenticated_web_user_id(request)' in share_block
    assert 'award_article_shared_points' in share_block
    assert 'increment_post_share(post_id)' in share_block


def test_article_cover_endpoint_and_payload_are_token_safe():
    source = open("web.py", encoding="utf-8").read()
    assert 'handle_article_cover_api' in source
    assert 'cover = f"/api/articles/{int(post.get(\'id\'))}/cover"' in source
    payload_start = source.index('def _article_api_payload')
    payload_end = source.index('async def handle_articles_api', payload_start)
    payload = source[payload_start:payload_end]
    assert 'BOT_TOKEN' not in payload
    assert 'cover_image_file_id' in payload


def test_telegram_article_compact_rendering_and_full_link():
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert 'async def send_author_article' in source
    assert 'body_limit=220' in source
    assert 'body_limit=1500' in source
    assert '?tab=articles&article={post_id}' in source
    assert '_plain_article_excerpt' in source


def test_author_posts_profile_query_includes_article_fields_and_filters():
    source = open("db/database.py", encoding="utf-8").read()
    start = source.index('def get_author_posts')
    end = source.index('def delete_author_post', start)
    block = source[start:end]
    assert 'body_text' in block
    assert 'cover_image_file_id' in block
    assert "COALESCE(status, 'published') = 'published'" in block
    assert 'COALESCE(published_to_profile, 1) = 1' in block


def test_manual_article_db_safety_source_checks():
    source = open("db/database.py", encoding="utf-8").read()
    start = source.index('def create_manual_article')
    end = source.index('def update_article_fields', start)
    block = source[start:end]
    assert 'conn.rollback()' in block
    assert 'if not title or not body_text' in block
    assert 'sanitize_article_text' in block
    update_start = source.index('def update_article_fields')
    update_end = source.index('def _article_row_to_dict', update_start)
    update_block = source[update_start:update_end]
    assert 'allowed = {' in update_block
    assert 'json.dumps(updates["attached_analysis_json"]' in update_block
    assert 'conn.rollback()' in update_block


def _load_telegram_bot_for_article_formatters(monkeypatch):
    import html
    import json
    from typing import Any
    from services.event_article_service import sanitize_article_text

    source = open("telegram_bot.py", encoding="utf-8").read()
    start = source.index("def _escape_article_html")
    end = source.index("def _format_author_post", start)
    namespace = {
        "html": html,
        "json": json,
        "Any": Any,
        "sanitize_article_text": sanitize_article_text,
    }
    exec(source[start:end], namespace)

    class Helpers:
        _escape_article_html = staticmethod(namespace["_escape_article_html"])
        _plain_article_excerpt = staticmethod(namespace["_plain_article_excerpt"])
        _attached_analysis_summary = staticmethod(namespace["_attached_analysis_summary"])
        _format_author_article_compact = staticmethod(namespace["_format_author_article_compact"])

    return Helpers


def _html_tags_balanced(value: str) -> bool:
    return value.count("<b>") == value.count("</b>")


def test_article_formatter_escapes_html_characters_runtime(monkeypatch):
    tb = _load_telegram_bot_for_article_formatters(monkeypatch)
    output = tb._format_author_article_compact({
        "title": "BTC < 100k & ETH > 5k",
        "body_text": "<b>fake bold</b> A & B",
    }, uid=1, show_author=False)
    assert "<b>BTC &lt; 100k &amp; ETH &gt; 5k</b>" in output
    assert "&lt;b&gt;fake bold&lt;/b&gt; A &amp; B" in output
    assert "<b>fake bold</b>" not in output
    assert _html_tags_balanced(output)


def test_long_article_without_cover_uses_plain_text_truncation_runtime(monkeypatch):
    tb = _load_telegram_bot_for_article_formatters(monkeypatch)
    body = ("<tag> & text\n" * 700)[:8000]
    output = tb._format_author_article_compact({"title": "Long < Article", "body_text": body}, uid=1, show_author=False)
    assert len(output) < 3200
    assert "&lt;tag&gt; &amp; text" in output
    assert "<tag>" not in output
    assert _html_tags_balanced(output)


def test_long_article_with_cover_caption_is_safe_runtime(monkeypatch):
    tb = _load_telegram_bot_for_article_formatters(monkeypatch)
    body = ("<tag> & text\n" * 700)[:8000]
    output = tb._format_author_article_compact(
        {"title": "Cover < Article", "body_text": body, "cover_image_file_id": "abc"},
        uid=1,
        show_author=False,
        body_limit=220,
        title_limit=100,
        market_limit=90,
        analysis_field_limit=55,
        max_analysis_fields=2,
    )
    assert len(output) < 850
    assert "&lt;tag&gt; &amp; text" in output
    assert "<tag>" not in output
    assert _html_tags_balanced(output)
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert '?tab=articles&article={post_id}' in source


def test_attached_analysis_summary_escapes_user_html_runtime(monkeypatch):
    tb = _load_telegram_bot_for_article_formatters(monkeypatch)
    output = tb._attached_analysis_summary({"attached_analysis": {
        "question": "<script> A & B",
        "display_prediction": "A & B",
        "confidence": "<b>bad</b>",
        "market_probability": "55% & rising",
        "summary": "Summary <tag>",
    }})
    assert "📎 <b>Attached DeepAlpha analysis</b>" in output
    assert "&lt;script&gt; A &amp; B" in output
    assert "A &amp; B" in output
    assert "&lt;b&gt;bad&lt;/b&gt;" in output
    assert "<script>" not in output
    assert "<b>bad</b>" not in output
    assert _html_tags_balanced(output)


def test_article_html_source_regressions():
    source = open("telegram_bot.py", encoding="utf-8").read()
    send_start = source.index('async def send_author_article')
    send_end = source.index('def _format_author_post', send_start)
    send_block = source[send_start:send_end]
    assert 'text[: text_limit' not in send_block
    assert 'text[: caption_limit' not in send_block
    assert 'caption = text[:' not in send_block
    assert '_escape_article_html' in source
    format_start = source.index('def _format_author_article_compact')
    format_end = source.index('async def send_author_article', format_start)
    assert '_escape_article_html' in source[format_start:format_end]
