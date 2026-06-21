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


def test_russian_sports_total_entities():
    result = route(user_text="Реал - Барса тотал 2.5 коэффициент 1.87")
    assert result["mode"] == "sports"
    assert result["entities"]["teams"] == ["Реал", "Барса"]
    assert result["entities"]["market"] == "тотал 2.5"
    assert result["entities"]["odds"] == 1.87


def test_crypto_pair_timeframe_russian_entry():
    result = route(user_text="BTCUSDT 15m есть вход?")
    assert result["mode"] == "crypto"
    assert result["entities"]["pair"] == "BTCUSDT"
    assert result["entities"]["timeframe"] == "15m"


def test_crypto_slash_pair_timeframe():
    result = route(user_text="ETH/USDT 1h")
    assert result["mode"] == "crypto"
    assert result["entities"]["pair"] == "ETHUSDT"
    assert result["entities"]["asset"] == "ETH"
    assert result["entities"]["timeframe"] == "1h"


def test_crypto_dash_pair_timeframe():
    result = route(user_text="SOL-USDT 4h")
    assert result["mode"] == "crypto"
    assert result["entities"]["pair"] == "SOLUSDT"
    assert result["entities"]["asset"] == "SOL"
    assert result["entities"]["timeframe"] == "4h"


def test_crypto_russian_pair_request_routes_to_crypto_with_missing_pair():
    result = route(user_text="крипто пару")
    assert result["mode"] == "crypto"
    assert "asset_or_pair" in result["missing_data"]


def test_crypto_english_russian_pair_request_routes_to_crypto_with_missing_pair():
    result = route(user_text="Crypto-пару")
    assert result["mode"] == "crypto"
    assert "asset_or_pair" in result["missing_data"]


def test_natural_bitcoin_buy_question_routes_crypto_asset():
    result = route(user_text="биткоин сейчас покупать или не нужно?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "BTC"
    assert "timeframe" in result["missing_data"]


def test_natural_btc_buy_question_routes_crypto_asset():
    result = route(user_text="стоит брать BTC?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "BTC"


def test_natural_bitok_question_routes_crypto_asset():
    result = route(user_text="что по битку?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "BTC"


def test_natural_eth_hold_exit_question_routes_crypto_asset():
    result = route(user_text="ETH держать или выходить?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "ETH"


def test_natural_efir_buy_question_routes_crypto_asset():
    result = route(user_text="эфир сейчас покупать?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "ETH"


def test_natural_sol_entry_question_routes_crypto_asset():
    result = route(user_text="SOL норм для входа?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "SOL"


def test_natural_ton_potential_question_routes_crypto_asset():
    result = route(user_text="TON есть потенциал?")
    assert result["mode"] == "crypto"
    assert result["entities"]["asset"] == "TON"


def test_generic_crypto_watch_request_routes_crypto_missing_asset_or_pair():
    result = route(user_text="какую крипту посмотреть?")
    assert result["mode"] == "crypto"
    assert "asset_or_pair" in result["missing_data"]
