from services.live_understanding_service import understand_live_request
from services import crypto_market_context_service as market


def test_understanding_bitcoin_buy_now_ru():
    result = understand_live_request("биткоин сейчас покупать или не нужно?", {"mode": "crypto", "entities": {"asset": "BTC"}}, {}, "ru")
    assert result["mode"] == "crypto"
    assert result["intent"] == "entry_now"
    assert result["asset"] == "BTC"
    assert result["pair"] == "BTCUSDT"
    assert result["needs"]["market_data"] is True
    assert result["needs"]["ohlcv"] is True
    assert result["needs"]["web_research"] is True
    assert "timeframe" in result["missing"]


def test_understanding_btcusdt_15m_entry():
    result = understand_live_request("BTCUSDT 15m есть вход?", {"mode": "crypto", "entities": {}}, {}, "ru")
    assert result["mode"] == "crypto"
    assert result["intent"] == "entry_now"
    assert result["pair"] == "BTCUSDT"
    assert result["timeframe"] == "15m"
    assert result["needs"]["market_data"] is True
    assert result["needs"]["ohlcv"] is True
    assert result["missing"] == []


def test_understanding_what_about_bitcoin():
    result = understand_live_request("что по битку?", {"mode": "crypto", "entities": {"asset": "BTC"}}, {}, "ru")
    assert result["mode"] == "crypto"
    assert result["asset"] == "BTC"
    assert result["intent"] in ("risk_check", "price_check")


def test_market_context_mocked_ohlcv_levels(monkeypatch):
    data = []
    for i in range(60):
        low = 63500 + i * 2
        high = 64500 + i * 2
        close = 64000 + i
        data.append([i, close - 10, high, low, close, 100 + i])
    monkeypatch.setattr(market, "_fetch_binance_ohlcv", lambda pair, timeframe: data)
    result = market.get_crypto_market_context("BTCUSDT", "1h")
    assert result["ok"] is True
    assert result["support_levels"]
    assert result["resistance_levels"]
    assert result["entry_context"]["better_zone"] in result["support_levels"]


def test_market_context_no_provider_safe_fallback(monkeypatch):
    monkeypatch.setattr(market, "_fetch_binance_ohlcv", lambda pair, timeframe: [])
    result = market.get_crypto_market_context("BTCUSDT", "1h")
    assert result["ok"] is False
    assert result["support_levels"] == []
    assert result["resistance_levels"] == []


def test_market_context_does_not_invent_levels_on_error(monkeypatch):
    def boom(pair, timeframe):
        raise RuntimeError("no provider")
    monkeypatch.setattr(market, "_fetch_binance_ohlcv", boom)
    result = market.get_crypto_market_context("BTCUSDT", "1h")
    assert result["ok"] is False
    assert result["support_levels"] == []
    assert result["entry_context"] == {}


def test_sports_schedule_france_iraq():
    result = understand_live_request("Когда матч Франция — Ирак?", {"mode": "sports", "entities": {}}, {}, "ru")
    assert result["mode"] == "sports"
    assert result["intent"] == "schedule_check"
    assert result["teams"] == ["France", "Iraq"]
    assert result["sport"] == "football"
    assert result["needs"]["sports_schedule"] is True
    assert result["needs"]["web_research"] is True


def test_sports_total_value_real_barca():
    result = understand_live_request("Реал — Барса тотал 2.5, есть value?", {"mode": "sports", "entities": {}}, {}, "ru")
    assert result["mode"] == "sports"
    assert result["intent"] == "odds_value"
    assert result["teams"] == ["Real", "Barcelona"]
    assert result["market"] == "total"
    assert result["line"] == "2.5"
    assert result["needs"]["odds"] is True
    assert result["needs"]["sports_stats"] is True
    assert result["needs"]["sports_news"] is True


def test_sports_betting_angle_england_ghana():
    result = understand_live_request("На кого ставить Англия — Гана?", {"mode": "sports", "entities": {}}, {}, "ru")
    assert result["intent"] == "betting_angle"
    assert result["teams"] == ["England", "Ghana"]
    assert result["needs"]["odds"] is True
    assert result["needs"]["sports_news"] is True
    assert result["needs"]["sports_stats"] is True


def test_sports_participants_missing_tournament():
    result = understand_live_request("Какие игроки в турнире?", {"mode": "sports", "entities": {}}, {}, "ru")
    assert result["intent"] == "participants_check"
    assert "tournament" in result["missing"]


def test_sports_polymarket_market_need():
    result = understand_live_request("Есть рынок Polymarket на этот матч?", {"mode": "sports", "entities": {}}, {}, "ru")
    assert result["intent"] == "polymarket_sports_market"
    assert result["needs"]["polymarket"] is True
