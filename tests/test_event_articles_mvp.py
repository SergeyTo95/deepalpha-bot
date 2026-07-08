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
