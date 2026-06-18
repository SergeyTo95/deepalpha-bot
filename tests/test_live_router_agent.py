from services.live_router_agent import LiveRouterAgent


def route(**kwargs):
    return LiveRouterAgent().route(**kwargs)


def test_polymarket_event_screenshot():
    result = route(screenshot_payload={"market_title": "World Cup Winner", "outcomes": ["Spain", "France", "England"], "visible_prices": ["25%", "20%"]})
    assert result["mode"] == "polymarket"
    assert result["screen_type"] == "polymarket_event"
    assert result["next_agent"] == "PredictionMarketAgent"


def test_polymarket_url():
    result = route(source_url="https://polymarket.com/ru/event/world-cup-winner")
    assert result["mode"] == "polymarket"
    assert result["confidence"] >= 0.85


def test_crypto_chart_text():
    result = route(user_text="BTCUSDT 15m Binance RSI Volume")
    assert result["mode"] == "crypto"
    assert result["screen_type"] == "crypto_chart"
    assert result["next_agent"] == "CryptoLiveAgent"
    assert result["entities"]["asset"] == "BTC"


def test_dexscreener_token_page():
    result = route(user_text="DEX Screener TON liquidity FDV market cap")
    assert result["mode"] == "crypto"
    assert result["screen_type"] == "crypto_token_page"


def test_football_odds():
    result = route(user_text="France vs Spain Over 2.5 odds 1.92 62 minute")
    assert result["mode"] == "sports"
    assert result["screen_type"] == "sports_odds"
    assert result["next_agent"] == "SportsLiveAgent"
    assert "France" in result["entities"]["teams"]
    assert "Spain" in result["entities"]["teams"]


def test_live_score():
    result = route(user_text="Real Madrid 1-0 Barcelona 68' shots on target")
    assert result["mode"] == "sports"
    assert result["screen_type"] == "sports_live_score"


def test_ambiguous():
    result = route(user_text="What do you think?")
    assert result["mode"] == "unknown"
    assert result["next_agent"] == "ClarificationAgent"


def test_mixed_low_confidence_crypto_sports():
    result = route(user_text="BTC football")
    assert result["mode"] == "unknown"
    assert result["next_agent"] == "ClarificationAgent"
