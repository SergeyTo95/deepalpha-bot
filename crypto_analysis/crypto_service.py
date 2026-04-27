from typing import Any, Dict, Optional

from crypto_analysis.crypto_utils import normalize_crypto_symbol
from crypto_analysis.crypto_market_agent import CryptoMarketAgent
from crypto_analysis.crypto_ta_agent import CryptoTAAgent
from crypto_analysis.crypto_news_agent import CryptoNewsAgent
from crypto_analysis.crypto_decision_agent import CryptoDecisionAgent
from crypto_analysis.crypto_communication_agent import CryptoCommunicationAgent


def analyze_crypto(
    user_input: str,
    lang: str = "ru",
    timeframe: str = "4h",
    default_quote: str = "USDT",
    cryptopanic_api_key: Optional[str] = None,
) -> str:
    """
    Главная точка входа для крипто анализа.
    Возвращает готовый текст для Telegram.
    """
    symbol_info = normalize_crypto_symbol(user_input, default_quote=default_quote)

    if not symbol_info:
        if lang == "ru":
            return "❌ Не удалось распознать тикер. Попробуйте: BTC, ETH, TON/USDT"
        else:
            return "❌ Could not parse ticker. Try: BTC, ETH, TON/USDT"

    try:
        market_agent = CryptoMarketAgent()
        market_data = market_agent.run(symbol_info, timeframe=timeframe)

        if not market_data.get("found"):
            if lang == "ru":
                return (
                    f"❌ Актив {symbol_info['display']} не найден. "
                    "Проверьте тикер и попробуйте другой."
                )
            else:
                return (
                    f"❌ Asset {symbol_info['display']} not found. "
                    "Check the ticker and try another."
                )

        ta_agent = CryptoTAAgent()
        ta_data = ta_agent.run(market_data)

        news_agent = CryptoNewsAgent(cryptopanic_api_key=cryptopanic_api_key)
        news_data = news_agent.run(symbol_info["base"], lang=lang)

        decision_agent = CryptoDecisionAgent()
        decision_data = decision_agent.run(
            market_data, ta_data, news_data, lang=lang
        )

        comm_agent = CryptoCommunicationAgent()
        result_text = comm_agent.run(
            market_data, ta_data, news_data, decision_data, lang=lang
        )

        return result_text

    except Exception as e:
        print(f"analyze_crypto error for {user_input}: {e}")
        if lang == "ru":
            return (
                "❌ Ошибка при анализе. Попробуйте позже или другой тикер."
            )
        else:
            return "❌ Analysis error. Try again later or use a different ticker."
