import re
from typing import Dict, List, Optional


KNOWN_BASES = {
    "BTC", "ETH", "SOL", "TON", "BNB", "XRP", "ADA", "DOGE", "AVAX",
    "DOT", "MATIC", "LINK", "UNI", "ATOM", "LTC", "BCH", "NEAR", "APT",
    "OP", "ARB", "SUI", "INJ", "TIA", "SEI", "PEPE", "WIF", "JUP",
    "BONK", "FLOKI", "SHIB", "SAND", "MANA", "AXS", "ENJ", "GALA",
    "FTM", "ONE", "HBAR", "XLM", "VET", "ALGO", "THETA", "FIL", "ICP",
    "EOS", "XTZ", "AAVE", "COMP", "MKR", "SNX", "CRV", "LDO", "RPL",
    "GMX", "GRT", "BAT", "ZRX", "1INCH", "CAKE", "SUSHI", "YFI",
    "RUNE", "KSM", "ZIL", "ICX", "IOTA", "NEO", "WAVES", "DASH",
    "ZEC", "ETC", "STX", "CFX", "KLAY", "ROSE", "CELO", "FLOW",
    "EGLD", "AUDIO", "CHZ", "CELR", "SKL", "STORJ", "REN", "UMA",
    "BAND", "OXT", "NKN", "CTSI", "ANKR", "DENT", "WIN", "HOT",
    "ENS", "LRC", "JASMY", "API3", "RAD", "GLM", "MASK", "QNT",
    "ORN", "PAXG", "OGN", "POND", "IOTX", "MDT", "LOOM",
}

QUOTE_ASSETS = {"USDT", "USDC", "BUSD", "BTC", "ETH", "BNB", "EUR", "USD"}

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "TON": "the-open-network",
    "BNB": "binancecoin", "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "LINK": "chainlink", "UNI": "uniswap", "ATOM": "cosmos", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "NEAR": "near", "APT": "aptos", "OP": "optimism",
    "ARB": "arbitrum", "SUI": "sui", "INJ": "injective-protocol",
    "TIA": "celestia", "SEI": "sei-network", "PEPE": "pepe",
    "WIF": "dogwifcoin", "JUP": "jupiter-exchange-solana", "BONK": "bonk",
    "FLOKI": "floki", "SHIB": "shiba-inu", "SAND": "the-sandbox",
    "MANA": "decentraland", "AXS": "axie-infinity", "ENJ": "enjincoin",
    "GALA": "gala", "FTM": "fantom", "HBAR": "hedera-hashgraph",
    "XLM": "stellar", "VET": "vechain", "ALGO": "algorand",
    "THETA": "theta-token", "FIL": "filecoin", "ICP": "internet-computer",
    "AAVE": "aave", "COMP": "compound-governance-token", "MKR": "maker",
    "SNX": "havven", "CRV": "curve-dao-token", "LDO": "lido-dao",
    "GRT": "the-graph", "RUNE": "thorchain", "KSM": "kusama",
    "STX": "blockstack", "EGLD": "elrond-erd-2", "QNT": "quant-network",
    "ENS": "ethereum-name-service", "LRC": "loopring",
}


def normalize_crypto_symbol(
    user_input: str,
    default_quote: str = "USDT",
) -> Optional[Dict[str, str]]:
    """
    Нормализует ввод пользователя в структуру символа.

    Поддерживает: BTC, TON/USDT, BTC-USDT, TONUSDT, ton usdt
    """
    if not user_input:
        return None

    raw = user_input.strip().upper()
    raw = re.sub(r'\s+', '', raw)

    # Формат: BASE/QUOTE или BASE-QUOTE
    for sep in ("/", "-"):
        if sep in raw:
            parts = raw.split(sep, 1)
            if len(parts) == 2:
                base, quote = parts[0].strip(), parts[1].strip()
                if base and quote:
                    return {
                        "base": base,
                        "quote": quote,
                        "symbol": f"{base}{quote}",
                        "display": f"{base}/{quote}",
                    }

    # Формат: BASEUSDT, BASEUSDC, BASEBTC, BASEETH
    for q in sorted(QUOTE_ASSETS, key=len, reverse=True):
        if raw.endswith(q) and len(raw) > len(q):
            base = raw[:-len(q)]
            if base:
                return {
                    "base": base,
                    "quote": q,
                    "symbol": f"{base}{q}",
                    "display": f"{base}/{q}",
                }

    # Просто тикер: BTC, ETH, TON
    if re.match(r'^[A-Z0-9]{1,20}$', raw):
        return {
            "base": raw,
            "quote": default_quote,
            "symbol": f"{raw}{default_quote}",
            "display": f"{raw}/{default_quote}",
        }

    return None


def get_coingecko_id(base: str) -> Optional[str]:
    return COINGECKO_IDS.get(base.upper())


def format_price(price: float) -> str:
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    elif price >= 0.001:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


def format_change(change: float) -> str:
    icon = "📈" if change >= 0 else "📉"
    sign = "+" if change >= 0 else ""
    return f"{icon} {sign}{change:.2f}%"


def format_volume(volume: float) -> str:
    if volume >= 1_000_000_000:
        return f"${volume / 1_000_000_000:.2f}B"
    elif volume >= 1_000_000:
        return f"${volume / 1_000_000:.2f}M"
    elif volume >= 1_000:
        return f"${volume / 1_000:.2f}K"
    else:
        return f"${volume:.2f}"


def classify_rsi(rsi: float) -> str:
    if rsi >= 75:
        return "сильная перекупленность"
    elif rsi >= 65:
        return "перекупленность"
    elif rsi <= 25:
        return "сильная перепроданность"
    elif rsi <= 35:
        return "перепроданность"
    else:
        return "нейтральная зона"


def classify_rsi_en(rsi: float) -> str:
    if rsi >= 75:
        return "strongly overbought"
    elif rsi >= 65:
        return "overbought"
    elif rsi <= 25:
        return "strongly oversold"
    elif rsi <= 35:
        return "oversold"
    else:
        return "neutral zone"
