"""
DeepAlpha Turbo Signal — ShortTermPriceAgent
Handles ultra-short BTC Up/Down markets (5m / 15m / 1h).
"""
import re
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════
# DETECTION
# ═══════════════════════════════════════════

def _parse_intraday_time_range_minutes(text: str) -> Optional[int]:
    """
    Parses intraday time ranges and returns duration in minutes.
    Supports:
      1:30AM-1:35AM
      1:30 AM - 1:35 AM
      7:25AM-7:30AM ET
      1:30PM-2:30PM
      13:30-13:35
      13:30 - 14:30
    Returns None if not found.
    """
    # 12-hour AM/PM format
    pattern_12 = (
        r'(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)'
    )
    m = re.search(pattern_12, text, re.IGNORECASE)
    if m:
        h1, min1, ap1, h2, min2, ap2 = (
            int(m.group(1)), int(m.group(2)), m.group(3).upper(),
            int(m.group(4)), int(m.group(5)), m.group(6).upper(),
        )
        # Convert to 24h
        def to_minutes(h, mn, ap):
            if ap == "AM":
                h = 0 if h == 12 else h
            else:
                h = 12 if h == 12 else h + 12
            return h * 60 + mn

        start = to_minutes(h1, min1, ap1)
        end   = to_minutes(h2, min2, ap2)
        if end < start:
            end += 24 * 60  # crosses midnight
        return end - start

    # 24-hour format  13:30-13:35 or 13:30 - 14:30
    pattern_24 = r'(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})(?!\s*[AP]M)'
    m2 = re.search(pattern_24, text, re.IGNORECASE)
    if m2:
        h1, min1, h2, min2 = (
            int(m2.group(1)), int(m2.group(2)),
            int(m2.group(3)), int(m2.group(4)),
        )
        start = h1 * 60 + min1
        end   = h2 * 60 + min2
        if end < start:
            end += 24 * 60
        diff = end - start
        if 0 < diff <= 180:  # sanity: up to 3h
            return diff

    return None


def _extract_time_horizon(text: str) -> str:
    """
    Returns one of: 5m / 15m / 30m / 1h / unknown.
    Checks both explicit patterns and intraday time ranges.
    """
    # Explicit short patterns
    if re.search(r'\b5\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "5m"
    if re.search(r'\b15\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "15m"
    if re.search(r'\b30\s*(m|min|minutes?|м|мин)\b', text, re.IGNORECASE):
        return "30m"
    if re.search(r'\b1\s*(h|hour|ч|час)\b', text, re.IGNORECASE):
        return "1h"

    # Intraday time range
    minutes = _parse_intraday_time_range_minutes(text)
    if minutes is not None:
        if minutes <= 7:
            return "5m"
        elif minutes <= 20:
            return "15m"
        elif minutes <= 40:
            return "30m"
        elif minutes <= 90:
            return "1h"

    return "unknown"


def _has_intraday_time_range(text: str) -> bool:
    """True if text contains an intraday clock range like 1:30AM-1:35AM."""
    return bool(re.search(
        r'\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM|ET|UTC)?',
        text, re.IGNORECASE
    ))


def _is_long_term_market(text: str) -> bool:
    """
    Returns True if question is clearly a long-term threshold market.
    Uses price targets + long date horizons — NOT just any month name.
    """
    # Price target words
    price_target = bool(re.search(
        r'\b(hit|reach|surpass|exceed|cross|above|below)\s+\$[\d,]+',
        text, re.IGNORECASE
    ))
    # Long date horizon words
    long_date = bool(re.search(
        r'\bby\s+(june|january|february|march|april|may|july|august|september|october|november|december)\b'
        r'|\bby\s+end\s+of\s+year\b'
        r'|\bby\s+q[1-4]\b'
        r'|\bin\s+20(2[5-9]|3\d)\b'
        r'|\bbefore\s+december\b'
        r'|\bon\s+december\s+31\b'
        r'|\bby\s+year.?end\b',
        text, re.IGNORECASE
    ))
    return price_target or long_date


def is_short_term_price_market(market_data: dict) -> bool:
    """
    Returns True only if market is an ultra-short BTC Up/Down price market.

    TRUE for:
      - "BTC up or down in 5m"
      - "Bitcoin Up or Down - May 1, 1:30AM-1:35AM ET"
      - Markets with Chainlink BTC/USD + Up/Down resolution rules

    FALSE for:
      - "Will Bitcoin hit $100k by June 30?"
      - Long-term threshold markets
    """
    question    = (market_data.get("question") or "").strip()
    description = (market_data.get("description") or "")
    rules       = (market_data.get("rules") or "")
    outcomes    = [str(o).lower().strip() for o in (market_data.get("outcomes") or [])]

    text_all   = f"{question} {description} {rules}"
    text_lower = text_all.lower()
    q_lower    = question.lower()

    # ── Hard exclude long-term threshold ──────────────────────────────────────
    if _is_long_term_market(text_lower):
        return False

    # ── Must have BTC context ─────────────────────────────────────────────────
    has_btc = bool(re.search(r'\bbtc\b|\bbitcoin\b|биткоин', text_lower))
    if not has_btc:
        return False

    # ── Signal A: explicit Up/Down in title ───────────────────────────────────
    has_updown_title = bool(re.search(
        r'\bup\s+or\s+down\b'
        r'|\bup/down\b'
        r'|вверх\s+или\s+вниз',
        text_lower,
    ))

    # ── Signal B: Up/Down outcomes ────────────────────────────────────────────
    has_updown_outcomes = (
        any(re.fullmatch(r'up|вверх|выше', o) for o in outcomes) and
        any(re.fullmatch(r'down|вниз|ниже', o) for o in outcomes)
    )

    # ── Signal C: rules say price-at-end vs price-at-beginning / Chainlink ───
    rules_patterns = [
        r'price at the end of the time range',
        r'price at the beginning',
        r'greater than or equal to the price at the',
        r'resolve.*to.{0,15}up.{0,30}price',
        r'resolve.*to.{0,15}down.{0,30}price',
        r'chainlink\s+btc',
        r'chainlink.*btc/usd',
    ]
    has_price_rules = any(
        re.search(p, text_lower, re.IGNORECASE) for p in rules_patterns
    )

    # ── Signal D: intraday time range in title ────────────────────────────────
    has_intraday = _has_intraday_time_range(question)

    # ── Signal E: explicit short horizon ─────────────────────────────────────
    has_short_horizon = _extract_time_horizon(text_all) != "unknown"

    # ── Decision logic ────────────────────────────────────────────────────────
    # Case 1: BTC + Up/Down title + intraday range → certain
    if has_btc and has_updown_title and has_intraday:
        return True

    # Case 2: BTC + Up/Down title + short horizon explicitly
    if has_btc and has_updown_title and has_short_horizon:
        return True

    # Case 3: BTC + Up/Down outcomes + rules
    if has_btc and has_updown_outcomes and has_price_rules:
        return True

    # Case 4: BTC + explicit price rules (very specific — enough on their own)
    if has_btc and has_price_rules:
        return True

    # Case 5: BTC + Up/Down outcomes + intraday or short horizon
    if has_btc and has_updown_outcomes and (has_intraday or has_short_horizon):
        return True

    return False


# ═══════════════════════════════════════════
# PRICE PARSERS
# ═══════════════════════════════════════════

def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = re.sub(r'[$,]', '', str(text))
    m = re.search(r'([\d]+(?:\.\d+)?)', cleaned)
    if m:
        try:
            val = float(m.group(1))
            return val if val > 0 else None
        except ValueError:
            pass
    return None


def _extract_prices(market_data: dict) -> dict:
    result = {
        "up_price": None,
        "down_price": None,
        "current_price": None,
        "target_price": None,
        "reference_price": None,
    }

    # prices array [up, down]
    prices = market_data.get("prices") or []
    if len(prices) >= 2:
        try:
            result["up_price"]   = float(prices[0])
            result["down_price"] = float(prices[1])
        except (ValueError, TypeError):
            pass

    # market_probability string
    mp = market_data.get("market_probability") or ""
    up_m = re.search(r'(?:up|yes|вверх)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    dn_m = re.search(r'(?:down|no|вниз)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    if up_m:
        result["up_price"]   = float(up_m.group(1)) / 100
    if dn_m:
        result["down_price"] = float(dn_m.group(1)) / 100

    # live price fields
    for field in ("current_price", "btc_price", "live_price"):
        val = market_data.get(field)
        if val is not None:
            parsed = _parse_money(str(val))
            if parsed:
                result["current_price"] = parsed
                break

    # target / reference price fields
    for field in ("target_price", "start_price", "reference_price", "open_price"):
        val = market_data.get(field)
        if val is not None:
            parsed = _parse_money(str(val))
            if parsed:
                result["target_price"]    = parsed
                result["reference_price"] = parsed
                break

    # extract from description/rules text
    if result["target_price"] is None:
        text = " ".join([
            market_data.get("description") or "",
            market_data.get("rules") or "",
        ])
        money_matches = re.findall(r'\$[\d,]+(?:\.\d+)?', text)
        btc_range = [
            v for v in (_parse_money(m) for m in money_matches)
            if v and 10_000 < v < 500_000
        ]
        if btc_range:
            result["target_price"]    = max(btc_range)
            result["reference_price"] = result["target_price"]

    return result


def _get_time_left_seconds(market_data: dict) -> Optional[int]:
    import time as _time
    from datetime import datetime, timezone

    for field in ("end_time", "close_time", "expiration", "deadline", "end_date"):
        val = market_data.get(field)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                ts = float(val)
                if ts > 1e12:
                    ts /= 1000
                diff = int(ts - _time.time())
                if diff > 0:
                    return diff
            elif isinstance(val, str):
                val_clean = val.replace("Z", "+00:00")
                dt = datetime.fromisoformat(val_clean)
                diff = int((dt - datetime.now(timezone.utc)).total_seconds())
                if diff > 0:
                    return diff
        except Exception:
            continue
    return None


def _detect_direction_labels(market_data: dict) -> Tuple[str, str]:
    outcomes = [str(o) for o in (market_data.get("outcomes") or [])]
    up_label, dn_label = "Up", "Down"
    for o in outcomes:
        ol = o.lower()
        if ol in ("up", "вверх", "выше"):
            up_label = o
        if ol in ("down", "вниз", "ниже"):
            dn_label = o
    return up_label, dn_label


def _extract_resolution_source(market_data: dict) -> str:
    text = " ".join([
        market_data.get("description") or "",
        market_data.get("rules") or "",
    ])
    if re.search(r'chainlink', text, re.IGNORECASE):
        m = re.search(r'chainlink\s+([\w/]+)', text, re.IGNORECASE)
        return f"Chainlink {m.group(1)}" if m else "Chainlink BTC/USD"
    return "Polymarket resolution"


# ═══════════════════════════════════════════
# CLOSED MARKET DETECTION
# ═══════════════════════════════════════════

def _is_market_resolved(up_price: Optional[float], down_price: Optional[float]) -> bool:
    """Returns True if market looks fully resolved (100/0 or 0/100)."""
    if up_price is None or down_price is None:
        return False
    # resolved if one side is >= 0.98 and other <= 0.02
    return (up_price >= 0.98 and down_price <= 0.02) or \
           (down_price >= 0.98 and up_price <= 0.02)


# ═══════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════

def _calculate_scores(
    up_price: Optional[float],
    down_price: Optional[float],
    current_price: Optional[float],
    target_price: Optional[float],
    time_left_sec: Optional[int],
    horizon: str,
    lang: str,
) -> Tuple[int, int, str, str]:
    """Returns (edge_score, risk_score, confidence, decision)."""

    # Resolved market — always NO TRADE
    if _is_market_resolved(up_price, down_price):
        return 0, 100, "Low", "NO TRADE"

    edge_score = 0
    risk_score = 30  # baseline for ultra-short

    has_live_data = current_price is not None and target_price is not None

    if not has_live_data:
        risk_score += 40
        return 0, min(risk_score, 100), "Low", "NO TRADE"

    distance     = current_price - target_price
    distance_pct = abs(distance) / target_price * 100 if target_price else 0
    up_winning   = distance >= 0

    # Buffer scoring
    if distance_pct > 1.0:
        edge_score += 25
    elif distance_pct > 0.3:
        edge_score += 12
    elif distance_pct > 0.05:
        edge_score += 4
        risk_score += 20
    else:
        risk_score += 35

    # Time left
    if time_left_sec is not None:
        if time_left_sec < 60:
            edge_score += 20
            risk_score += 15
        elif time_left_sec < 180:
            edge_score += 12
            risk_score += 10
        elif time_left_sec < 600:
            edge_score += 5
            risk_score += 5
        else:
            risk_score += 20
    else:
        risk_score += 15

    # Market mispricing
    if up_price is not None and down_price is not None:
        market_up_prob = up_price
        if up_winning and market_up_prob < 0.55:
            edge_score += 15
        elif up_winning and market_up_prob > 0.75:
            edge_score -= 5
        elif not up_winning and (1 - market_up_prob) < 0.55:
            edge_score += 15
        spread = abs((up_price + down_price) - 1.0)
        if spread > 0.15:
            risk_score += 20
        elif spread < 0.05:
            edge_score += 5

    # Latency / oracle risk — always present
    risk_score += 15

    edge_score = max(0, min(edge_score, 100))
    risk_score = max(0, min(risk_score, 100))

    # Confidence
    if not has_live_data or time_left_sec is None:
        confidence = "Low"
    elif edge_score >= 50 and risk_score <= 55:
        confidence = "Medium"
    elif edge_score >= 35 and risk_score <= 65:
        confidence = "Medium-Low"
    else:
        confidence = "Low"

    # Decision
    net = edge_score - risk_score
    if net >= 20 and distance_pct > 0.3 and time_left_sec is not None and time_left_sec < 300:
        decision = "MICRO LONG UP" if up_winning else "MICRO LONG DOWN"
    elif net >= 5 and distance_pct > 0.1:
        decision = "WAIT"
    else:
        decision = "NO TRADE"

    return edge_score, risk_score, confidence, decision


# ═══════════════════════════════════════════
# FORMATTER
# ═══════════════════════════════════════════

def _format_turbo_signal(
    market_data: dict,
    prices: dict,
    horizon: str,
    resolution_source: str,
    up_label: str,
    down_label: str,
    time_left_sec: Optional[int],
    edge_score: int,
    risk_score: int,
    confidence: str,
    decision: str,
    is_resolved: bool,
    lang: str,
) -> str:
    sep      = "──────────────────────────────"
    question = market_data.get("question") or market_data.get("title") or "BTC Up/Down"

    up_price_raw = prices.get("up_price")
    dn_price_raw = prices.get("down_price")
    current      = prices.get("current_price")
    target       = prices.get("target_price") or prices.get("reference_price")

    up_str  = f"{int(round(up_price_raw * 100))}¢" if up_price_raw is not None else "—"
    dn_str  = f"{int(round(dn_price_raw * 100))}¢" if dn_price_raw is not None else "—"
    up_pct  = f"{int(round(up_price_raw * 100))}%" if up_price_raw is not None else "—"
    dn_pct  = f"{int(round(dn_price_raw * 100))}%" if dn_price_raw is not None else "—"

    # Price block
    if current is not None and target is not None:
        diff     = current - target
        sign     = "+" if diff >= 0 else ""
        pos_word = ("выше" if lang == "ru" else "above") if diff >= 0 else \
                   ("ниже"  if lang == "ru" else "below")
        price_block = (
            f"Target: ${target:,.2f}\n"
            f"Current: ${current:,.2f}\n"
            f"Distance: {sign}${abs(diff):.2f} {pos_word} target"
        )
    else:
        price_block = (
            "Текущая цена недоступна" if lang == "ru"
            else "Live price unavailable"
        )

    # Time left
    if time_left_sec is not None:
        if time_left_sec < 60:
            t_str = f"{time_left_sec}s"
        elif time_left_sec < 3600:
            t_str = f"{time_left_sec // 60}m {time_left_sec % 60}s"
        else:
            t_str = f"{time_left_sec // 3600}h {(time_left_sec % 3600) // 60}m"
    else:
        t_str = "неизвестно" if lang == "ru" else "unknown"

    d_icon = {
        "MICRO LONG UP":   "🟢",
        "MICRO LONG DOWN": "🔴",
        "WAIT":            "🟡",
        "NO TRADE":        "⚫",
    }.get(decision, "⚫")

    resolved_note = ""
    if is_resolved:
        resolved_note = (
            "\n⚠️ Рынок уже разрешён или полностью оценён (100/0). Торговля невозможна.\n"
            if lang == "ru" else
            "\n⚠️ Market appears resolved or fully priced (100/0). No trade possible.\n"
        )

    no_data_note = ""
    if current is None:
        no_data_note = (
            "\n⚠️ Нет live данных по цене. Без них нельзя оценить буфер — решение консервативное.\n"
            if lang == "ru" else
            "\n⚠️ Live price data unavailable. Buffer cannot be assessed — decision is conservative.\n"
        )

    if lang == "ru":
        logic_map = {
            "MICRO LONG UP":
                f"Цена выше target с буфером, времени до экспирации мало. "
                f"Рынок недооценивает UP. Осторожный вход при строгом стоп-лоссе.",
            "MICRO LONG DOWN":
                f"Цена ниже target с буфером, времени мало. "
                f"Рынок недооценивает DOWN. Осторожный вход.",
            "WAIT":
                f"Потенциальная позиция есть, но буфер или время недостаточны. "
                f"Следить за движением цены.",
            "NO TRADE":
                f"Нет достаточного edge. Нет live данных, буфер слишком мал, "
                f"рынок уже разрешён или корректно оценён. Оставаться вне позиции.",
        }
        conclusion_map = {
            "MICRO LONG UP":   "Осторожный вход в UP при подтверждении буфера. Риск-менеджмент обязателен.",
            "MICRO LONG DOWN": "Осторожный вход в DOWN при подтверждении буфера. Риск-менеджмент обязателен.",
            "WAIT":            "Ждать движения цены. Буфер или время ещё недостаточны.",
            "NO TRADE":        "Оставаться вне позиции. Нет подтверждённого edge.",
        }
        return (
            f"⚡ DeepAlpha Turbo Signal\n"
            f"{sep}\n\n"
            f"📌 Рынок: {question}\n"
            f"⏱ Горизонт: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n"
            f"{resolved_note}"
            f"\n🎯 Условие:\n"
            f"{up_label} побеждает, если BTC в конце периода >= стартовой цены.\n"
            f"{down_label} побеждает, если BTC ниже стартовой цены.\n\n"
            f"📍 Цена:\n{price_block}\n"
            f"{no_data_note}"
            f"\n⏰ До экспирации: {t_str}\n\n"
            f"🧠 Оценка:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Логика:\n{logic_map.get(decision, '')}\n\n"
            f"⚠️ Риски:\n"
            f"— last-second wick может изменить исход\n"
            f"— Chainlink resolution может отличаться от видимой цены на бирже\n"
            f"— высокий latency risk при экспирации\n"
            f"— ultra-short горизонт: фундаментал не имеет значения\n\n"
            f"📝 Вывод:\n{conclusion_map.get(decision, '')}"
        )
    else:
        logic_map = {
            "MICRO LONG UP":
                "Price above target with buffer, time is short. "
                "Market underpricing UP. Cautious entry with strict risk management.",
            "MICRO LONG DOWN":
                "Price below target with buffer, time is short. "
                "Market underpricing DOWN. Cautious entry.",
            "WAIT":
                "Potential position exists but buffer or time insufficient. "
                "Monitor price movement.",
            "NO TRADE":
                "Insufficient edge. Missing live data, buffer too thin, "
                "market resolved or fairly priced. Stay out.",
        }
        conclusion_map = {
            "MICRO LONG UP":   "Cautious entry in UP on buffer confirmation. Risk management required.",
            "MICRO LONG DOWN": "Cautious entry in DOWN on buffer confirmation. Risk management required.",
            "WAIT":            "Wait for price movement. Buffer or time not yet sufficient.",
            "NO TRADE":        "Stay out. No confirmed edge for this ultra-short market.",
        }
        return (
            f"⚡ DeepAlpha Turbo Signal\n"
            f"{sep}\n\n"
            f"📌 Market: {question}\n"
            f"⏱ Horizon: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n"
            f"{resolved_note}"
            f"\n🎯 Resolution Logic:\n"
            f"{up_label} wins if BTC at end of period >= start price.\n"
            f"{down_label} wins if BTC < start price.\n\n"
            f"📍 Price:\n{price_block}\n"
            f"{no_data_note}"
            f"\n⏰ Time left: {t_str}\n\n"
            f"🧠 Assessment:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Reasoning:\n{logic_map.get(decision, '')}\n\n"
            f"⚠️ Risks:\n"
            f"— last-second wick can flip outcome\n"
            f"— Chainlink resolution may differ from visible exchange price\n"
            f"— high latency risk near expiration\n"
            f"— ultra-short horizon: fundamentals irrelevant\n\n"
            f"📝 Conclusion:\n{conclusion_map.get(decision, '')}"
        )


# ═══════════════════════════════════════════
# MAIN AGENT
# ═══════════════════════════════════════════

class ShortTermPriceAgent:

    def is_short_term_price_market(self, market_data: dict) -> bool:
        return is_short_term_price_market(market_data)

    def run(self, market_data: dict, lang: str = "ru") -> dict:
        question  = market_data.get("question") or market_data.get("title") or ""
        category  = market_data.get("category") or "Crypto"
        mp        = market_data.get("market_probability") or ""

        text_all          = f"{question} {market_data.get('description','')} {market_data.get('rules','')}"
        horizon           = _extract_time_horizon(text_all)
        prices            = _extract_prices(market_data)
        resolution_source = _extract_resolution_source(market_data)
        up_label, dn_label = _detect_direction_labels(market_data)
        time_left_sec     = _get_time_left_seconds(market_data)
        is_resolved       = _is_market_resolved(prices.get("up_price"), prices.get("down_price"))

        edge_score, risk_score, confidence, decision = _calculate_scores(
            up_price      = prices.get("up_price"),
            down_price    = prices.get("down_price"),
            current_price = prices.get("current_price"),
            target_price  = prices.get("target_price"),
            time_left_sec = time_left_sec,
            horizon       = horizon,
            lang          = lang,
        )

        full_analysis = _format_turbo_signal(
            market_data       = market_data,
            prices            = prices,
            horizon           = horizon,
            resolution_source = resolution_source,
            up_label          = up_label,
            down_label        = dn_label,
            time_left_sec     = time_left_sec,
            edge_score        = edge_score,
            risk_score        = risk_score,
            confidence        = confidence,
            decision          = decision,
            is_resolved       = is_resolved,
            lang              = lang,
        )

        up_p = prices.get("up_price")
        probability = (
            f"{up_label} — {int(round(up_p * 100))}%"
            if up_p is not None else f"{up_label} / {dn_label}"
        )

        return {
            "analysis_mode":      "turbo_short_term",
            "category":           category,
            "question":           question,
            "market_probability": mp,
            "probability":        probability,
            "confidence":         confidence,
            "decision":           decision,
            "edge_score":         edge_score,
            "risk_score":         risk_score,
            "full_analysis":      full_analysis,
            "display_prediction": decision,
            "reasoning":          full_analysis,
            "main_scenario":      "",
            "alt_scenario":       "",
            "conclusion":         decision,
            "sources":            [],
            "news_sources":       [],
            "key_signals":        [],
            "market_structure": {
                "market_format": "binary",
                "domain":        "Crypto",
                "subtype":       "btc_short_term_updown",
                "resolution_logic": {
                    "yes_means":      f"{up_label} wins if BTC >= start price at expiration.",
                    "no_means":       f"{dn_label} wins if BTC < start price at expiration.",
                    "draw_handling":  "Not applicable.",
                    "ambiguity_risk": "medium",
                },
            },
            "user_context_used": False,
            "source_summary":    {},
            "evidence_matrix":   "",
        }
