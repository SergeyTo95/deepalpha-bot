"""
DeepAlpha Turbo Signal — ShortTermPriceAgent
Handles ultra-short BTC Up/Down markets (5m / 15m / 1h).
"""
import re
from typing import Any, Dict, List, Optional, Tuple


# ── Detection helpers ─────────────────────────────────────────────────────────

_SHORT_TITLE_PATTERNS = [
    r'\bbtc\b', r'\bbitcoin\b', r'биткоин',
    r'up or down', r'up/down', r'вверх или вниз',
    r'above or below', r'выше или ниже',
    r'\b5\s*m\b', r'\b5\s*min', r'\b5\s*minutes?\b',
    r'\b15\s*m\b', r'\b15\s*min', r'\b15\s*minutes?\b',
    r'\b1\s*h\b', r'\b1\s*hour\b',
]

_SHORT_RULES_PATTERNS = [
    r'price at the end of the time range',
    r'greater than or equal to the price at the beginning',
    r'chainlink btc',
    r'will resolve to.{0,10}up',
    r'will resolve to.{0,10}down',
    r'resolve.*up.*if.*price',
    r'resolve.*down.*if.*price',
]

_LONG_TERM_SIGNALS = [
    r'by june', r'by december', r'by january', r'by february',
    r'by march', r'by april', r'by may', r'by july', r'by august',
    r'by september', r'by october', r'by november',
    r'\b202[5-9]\b', r'by end of year', r'by q[1-4]',
    r'\$\d{2,3},\d{3}', r'hit \$', r'reach \$', r'exceed \$',
]


def is_short_term_price_market(market_data: dict) -> bool:
    """
    Returns True only if market is clearly an ultra-short BTC Up/Down price market.

    Requires at least ONE of:
      A) Explicit Up/Down outcomes
      B) Rules containing "price at end" / "price at beginning" / Chainlink BTC

    Plus BTC context.
    Excludes long-term threshold markets ("hit $100k by June").
    """
    question    = (market_data.get("question") or "").lower()
    description = (market_data.get("description") or "").lower()
    rules       = (market_data.get("rules") or "").lower()
    outcomes    = [str(o).lower() for o in (market_data.get("outcomes") or [])]

    text = f"{question} {description} {rules}"

    # ── Hard exclude: long-term markets ──────────────────────────────────────
    long_term_signals = [
        r'by june', r'by december', r'by january', r'by february',
        r'by march', r'by april', r'by may', r'by july', r'by august',
        r'by september', r'by october', r'by november',
        r'\b202[5-9]\b', r'by end of year', r'by q[1-4]',
        r'hit \$', r'reach \$', r'exceed \$', r'surpass \$',
        r'above \$\d', r'below \$\d',
    ]
    for pat in long_term_signals:
        if re.search(pat, text, re.IGNORECASE):
            return False

    # ── Must have BTC context ─────────────────────────────────────────────────
    has_btc = bool(re.search(r'\bbtc\b|\bbitcoin\b|биткоин', text, re.IGNORECASE))
    if not has_btc:
        return False

    # ── Signal A: explicit Up/Down outcomes ───────────────────────────────────
    has_up_outcome   = any(re.search(r'^up$|^вверх$|^выше$', o) for o in outcomes)
    has_down_outcome = any(re.search(r'^down$|^вниз$|^ниже$', o) for o in outcomes)
    has_updown_outcomes = has_up_outcome and has_down_outcome

    # ── Signal B: rules describe price-at-end vs price-at-beginning ──────────
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
        re.search(pat, text, re.IGNORECASE) for pat in rules_patterns
    )

    # ── Must satisfy A or B ───────────────────────────────────────────────────
    if not has_updown_outcomes and not has_price_rules:
        return False

    # ── Optional: time horizon confirmation ───────────────────────────────────
    has_short_horizon = bool(re.search(
        r'\b(5|15|30)\s*(m|min|minutes?|м|мин)\b|\b1\s*(h|hour|ч|час)\b',
        text, re.IGNORECASE
    ))

    # Satisfy A+B → certain, A or B alone → need short horizon hint
    if has_updown_outcomes and has_price_rules:
        return True
    if (has_updown_outcomes or has_price_rules) and has_short_horizon:
        return True
    # B alone without time horizon still valid (rules are very specific)
    if has_price_rules and has_btc:
        return True

    return False

# ── Parser helpers ────────────────────────────────────────────────────────────

def _extract_time_horizon(text: str) -> str:
    m = re.search(
        r'\b(5|15|30)\s*(m|min|minutes?|м|мин)\b|\b(1)\s*(h|hour|ч|час)\b',
        text, re.IGNORECASE
    )
    if not m:
        return "unknown"
    if m.group(3):
        return "1h"
    num = m.group(1)
    return f"{num}m"


def _parse_money(text: str) -> Optional[float]:
    """Parse $77,360.50 or 77360 or 77,360 into float."""
    if not text:
        return None
    cleaned = re.sub(r'[$,]', '', str(text))
    m = re.search(r'([\d]+(?:\.\d+)?)', cleaned)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _extract_prices(market_data: dict) -> dict:
    """
    Attempts to extract: up_price, down_price, current_price, target_price.
    Returns a dict with available values (None if unavailable).
    """
    result = {
        "up_price": None,
        "down_price": None,
        "current_price": None,
        "target_price": None,
        "reference_price": None,
    }

    # Prices array (Polymarket CLOB format: [yes_price, no_price])
    prices = market_data.get("prices") or []
    if len(prices) >= 2:
        try:
            result["up_price"] = float(prices[0])
            result["down_price"] = float(prices[1])
        except (ValueError, TypeError):
            pass

    # market_probability string "Up: 59% | Down: 42%"
    mp = market_data.get("market_probability") or ""
    up_m = re.search(r'(?:up|yes|вверх)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    dn_m = re.search(r'(?:down|no|вниз)[:\s]+([\d.]+)%', mp, re.IGNORECASE)
    if up_m:
        result["up_price"] = float(up_m.group(1)) / 100
    if dn_m:
        result["down_price"] = float(dn_m.group(1)) / 100

    # current / target from market_data fields
    for field in ("current_price", "btc_price", "live_price"):
        val = market_data.get(field)
        if val is not None:
            result["current_price"] = _parse_money(str(val))
            break

    for field in ("target_price", "start_price", "reference_price", "open_price"):
        val = market_data.get(field)
        if val is not None:
            parsed = _parse_money(str(val))
            if parsed:
                result["target_price"] = parsed
                result["reference_price"] = parsed
                break

    # Try to extract from description/rules text
    text = " ".join([
        market_data.get("description") or "",
        market_data.get("rules") or "",
        market_data.get("question") or "",
    ])
    money_matches = re.findall(r'\$[\d,]+(?:\.\d+)?', text)
    parsed_amounts = [_parse_money(m) for m in money_matches if _parse_money(m)]

    if parsed_amounts and result["target_price"] is None:
        # Largest suspicious amount likely to be BTC price
        btc_range = [v for v in parsed_amounts if 10_000 < v < 500_000]
        if btc_range:
            result["reference_price"] = max(btc_range)
            result["target_price"] = result["reference_price"]

    return result


def _get_time_left_seconds(market_data: dict) -> Optional[int]:
    """Returns seconds until expiration, or None if unavailable."""
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
                    ts /= 1000  # ms → s
                now = _time.time()
                diff = int(ts - now)
                if diff > 0:
                    return diff
            elif isinstance(val, str):
                # Try ISO format
                val_clean = val.replace("Z", "+00:00")
                dt = datetime.fromisoformat(val_clean)
                now = datetime.now(timezone.utc)
                diff = int((dt - now).total_seconds())
                if diff > 0:
                    return diff
        except Exception:
            continue
    return None


def _detect_direction_labels(market_data: dict) -> Tuple[str, str]:
    """Returns (up_label, down_label)."""
    outcomes = [str(o) for o in (market_data.get("outcomes") or [])]
    for o in outcomes:
        o_lower = o.lower()
        if "up" in o_lower or "вверх" in o_lower:
            up = o
        if "down" in o_lower or "вниз" in o_lower:
            down = o
    try:
        return up, down
    except NameError:
        return "Up", "Down"


def _extract_resolution_source(market_data: dict) -> str:
    text = " ".join([
        market_data.get("description") or "",
        market_data.get("rules") or "",
    ])
    if re.search(r'chainlink', text, re.IGNORECASE):
        m = re.search(r'chainlink\s+([\w/]+)', text, re.IGNORECASE)
        if m:
            return f"Chainlink {m.group(1)}"
        return "Chainlink BTC/USD"
    return "Polymarket resolution"


# ── Scoring ───────────────────────────────────────────────────────────────────

def _calculate_scores(
    up_price: Optional[float],
    down_price: Optional[float],
    current_price: Optional[float],
    target_price: Optional[float],
    time_left_sec: Optional[int],
    horizon: str,
    lang: str,
) -> Tuple[int, int, str, str]:
    """
    Returns (edge_score, risk_score, confidence, decision).
    """
    edge_score = 0
    risk_score = 30  # baseline risk for any ultra-short market

    has_live_data = current_price is not None and target_price is not None

    # ── No live data → NO TRADE ──
    if not has_live_data:
        risk_score += 40
        return 0, min(risk_score, 100), "Low", "NO TRADE"

    # Distance to target
    distance = current_price - target_price
    distance_pct = abs(distance) / target_price * 100 if target_price else 0
    up_winning = distance >= 0

    # Buffer scoring
    if distance_pct > 1.0:
        edge_score += 25  # strong buffer
    elif distance_pct > 0.3:
        edge_score += 12  # moderate buffer
    elif distance_pct > 0.05:
        edge_score += 4   # thin buffer
        risk_score += 20  # near threshold
    else:
        risk_score += 35  # at threshold, high flip risk

    # Time left
    if time_left_sec is not None:
        if time_left_sec < 60:
            edge_score += 20  # very little time — buffer matters more
            risk_score += 15  # but latency risk increases
        elif time_left_sec < 180:
            edge_score += 12
            risk_score += 10
        elif time_left_sec < 600:
            edge_score += 5
            risk_score += 5
        else:
            risk_score += 20  # lots of time → flip likely
    else:
        risk_score += 15  # unknown time → conservative

    # Market pricing signal
    if up_price is not None and down_price is not None:
        market_up_prob = up_price
        # If UP is winning and market underprices UP → edge
        if up_winning and market_up_prob < 0.55:
            edge_score += 15  # market underpricing current state
        elif up_winning and market_up_prob > 0.75:
            edge_score -= 5   # already expensive
        elif not up_winning and (1 - market_up_prob) < 0.55:
            edge_score += 15
        # Spread check
        spread = abs((up_price + down_price) - 1.0)
        if spread > 0.15:
            risk_score += 20  # wide spread
        elif spread < 0.05:
            edge_score += 5   # tight spread

    # Latency / oracle risk (always present for Chainlink markets)
    risk_score += 15

    # Cap
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


# ── Formatter ─────────────────────────────────────────────────────────────────

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
    lang: str,
) -> str:
    sep = "──────────────────────────────"
    question = market_data.get("question") or market_data.get("title") or "BTC Up/Down"
    mp = market_data.get("market_probability") or ""

    up_price_raw = prices.get("up_price")
    dn_price_raw = prices.get("down_price")
    current = prices.get("current_price")
    target = prices.get("target_price") or prices.get("reference_price")

    # Price display
    up_str  = f"{int(up_price_raw * 100)}¢" if up_price_raw is not None else "—"
    dn_str  = f"{int(dn_price_raw * 100)}¢" if dn_price_raw is not None else "—"
    up_pct  = f"{int(up_price_raw * 100)}%" if up_price_raw is not None else "—"
    dn_pct  = f"{int(dn_price_raw * 100)}%" if dn_price_raw is not None else "—"

    # Distance
    if current is not None and target is not None:
        diff = current - target
        sign = "+" if diff >= 0 else ""
        pos = "above" if lang != "ru" else "выше"
        pos_word = pos if diff >= 0 else ("below" if lang != "ru" else "ниже")
        dist_str = f"${current:,.2f}\nTarget: ${target:,.2f}\nDistance: {sign}${abs(diff):.2f} {pos_word} target"
    else:
        dist_str = "Live price unavailable" if lang != "ru" else "Текущая цена недоступна"

    # Time left
    if time_left_sec is not None:
        if time_left_sec < 60:
            t_str = f"{time_left_sec}s"
        elif time_left_sec < 3600:
            t_str = f"{time_left_sec // 60}m {time_left_sec % 60}s"
        else:
            t_str = f"{time_left_sec // 3600}h {(time_left_sec % 3600) // 60}m"
    else:
        t_str = "unknown" if lang != "ru" else "неизвестно"

    # Decision icon
    d_icon = {
        "MICRO LONG UP":   "🟢",
        "MICRO LONG DOWN": "🔴",
        "WAIT":            "🟡",
        "NO TRADE":        "⚫",
    }.get(decision, "⚫")

    if lang == "ru":
        no_data_note = ""
        if current is None:
            no_data_note = (
                "\n⚠️ Нет live данных по цене. "
                "Для ultra-short рынков нужны реальные котировки. "
                "Без них нельзя оценить буфер — решение консервативное.\n"
            )

        logic_ru = {
            "MICRO LONG UP":   (
                f"Цена выше target с буфером, мало времени до экспирации. "
                f"Рынок недооценивает UP. Осторожный вход при строгом стоп-лоссе."
            ),
            "MICRO LONG DOWN": (
                f"Цена ниже target с буфером, мало времени. "
                f"Рынок недооценивает DOWN. Осторожный вход."
            ),
            "WAIT":            (
                f"Есть потенциальная позиция, но буфер или время недостаточны для уверенного входа. "
                f"Следить за движением цены."
            ),
            "NO TRADE":        (
                f"Нет достаточного edge. Отсутствуют live данные, буфер слишком мал, "
                f"или рынок уже корректно оценён. Оставаться вне позиции."
            ),
        }.get(decision, "")

        conclusion_ru = {
            "MICRO LONG UP":   "Осторожный вход в UP при подтверждении буфера. Риск-менеджмент обязателен.",
            "MICRO LONG DOWN": "Осторожный вход в DOWN при подтверждении буфера. Риск-менеджмент обязателен.",
            "WAIT":            "Ждать движения цены перед входом. Буфер или время ещё не достаточны.",
            "NO TRADE":        "Оставаться вне позиции. Нет подтверждённого edge для ultra-short рынка.",
        }.get(decision, "")

        return (
            f"⚡ DeepAlpha Turbo Signal\n"
            f"{sep}\n\n"
            f"📌 Рынок: {question}\n"
            f"⏱ Горизонт: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n\n"
            f"🎯 Условие:\n"
            f"{up_label} побеждает, если BTC в конце периода >= стартовой цены.\n"
            f"{down_label} побеждает, если BTC ниже стартовой цены.\n\n"
            f"📍 Цена:\n"
            f"{dist_str}\n"
            f"{no_data_note}"
            f"⏰ До экспирации: {t_str}\n\n"
            f"🧠 Оценка:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Логика:\n{logic_ru}\n\n"
            f"⚠️ Риски:\n"
            f"— last-second wick может изменить исход\n"
            f"— Chainlink resolution может отличаться от видимой цены на бирже\n"
            f"— высокий latency risk при экспирации\n"
            f"— ultra-short горизонт: фундаментал не имеет значения\n\n"
            f"📝 Вывод:\n{conclusion_ru}"
        )

    else:
        no_data_note = ""
        if current is None:
            no_data_note = (
                "\n⚠️ Live price data unavailable. "
                "Ultra-short markets require real-time quotes. "
                "Without them, buffer cannot be assessed — decision is conservative.\n"
            )

        logic_en = {
            "MICRO LONG UP":   (
                "Price is above target with buffer, time is short. "
                "Market underpricing UP side. Cautious entry with strict risk management."
            ),
            "MICRO LONG DOWN": (
                "Price is below target with buffer, time is short. "
                "Market underpricing DOWN side. Cautious entry."
            ),
            "WAIT":            (
                "Potential position exists but buffer or time insufficient for confident entry. "
                "Monitor price movement."
            ),
            "NO TRADE":        (
                "Insufficient edge. Missing live data, buffer too thin, "
                "or market fairly priced. Stay out."
            ),
        }.get(decision, "")

        conclusion_en = {
            "MICRO LONG UP":   "Cautious entry in UP on buffer confirmation. Risk management required.",
            "MICRO LONG DOWN": "Cautious entry in DOWN on buffer confirmation. Risk management required.",
            "WAIT":            "Wait for price movement before entry. Buffer or time not yet sufficient.",
            "NO TRADE":        "Stay out of position. No confirmed edge for this ultra-short market.",
        }.get(decision, "")

        return (
            f"⚡ DeepAlpha Turbo Signal\n"
            f"{sep}\n\n"
            f"📌 Market: {question}\n"
            f"⏱ Horizon: {horizon}\n"
            f"🔗 Resolution: {resolution_source}\n\n"
            f"📊 Polymarket:\n"
            f"{up_label}: {up_str} | {down_label}: {dn_str}\n"
            f"Implied: {up_label} {up_pct} / {down_label} {dn_pct}\n\n"
            f"🎯 Resolution Logic:\n"
            f"{up_label} wins if BTC at end of period >= start price.\n"
            f"{down_label} wins if BTC < start price.\n\n"
            f"📍 Price:\n"
            f"{dist_str}\n"
            f"{no_data_note}"
            f"⏰ Time left: {t_str}\n\n"
            f"🧠 Assessment:\n"
            f"Edge Score: {edge_score}/100\n"
            f"Risk Score: {risk_score}/100\n"
            f"Confidence: {confidence}\n\n"
            f"{d_icon} Decision: {decision}\n\n"
            f"{sep}\n\n"
            f"💬 Reasoning:\n{logic_en}\n\n"
            f"⚠️ Risks:\n"
            f"— last-second wick can flip outcome\n"
            f"— Chainlink resolution may differ from visible exchange price\n"
            f"— high latency risk near expiration\n"
            f"— ultra-short horizon: fundamentals irrelevant\n\n"
            f"📝 Conclusion:\n{conclusion_en}"
        )


# ── Main Agent ────────────────────────────────────────────────────────────────

class ShortTermPriceAgent:
    """
    Analyzes ultra-short BTC Up/Down Polymarket markets.
    Returns a compact Turbo Signal instead of the full DeepAlpha analysis.
    """

    def is_short_term_price_market(self, market_data: dict) -> bool:
        return is_short_term_price_market(market_data)

    def run(self, market_data: dict, lang: str = "ru") -> dict:
        question  = market_data.get("question") or market_data.get("title") or ""
        category  = market_data.get("category") or "Crypto"
        mp        = market_data.get("market_probability") or ""

        text_full = " ".join([
            question,
            market_data.get("description") or "",
            market_data.get("rules") or "",
        ])

        horizon           = _extract_time_horizon(text_full)
        prices            = _extract_prices(market_data)
        resolution_source = _extract_resolution_source(market_data)
        up_label, dn_label = _detect_direction_labels(market_data)
        time_left_sec     = _get_time_left_seconds(market_data)

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
            lang              = lang,
        )

        # Probability string for compatibility
        up_p = prices.get("up_price")
        probability = (
            f"{up_label} — {int(up_p * 100)}%"
            if up_p is not None else f"{up_label} / {dn_label}"
        )

        return {
            "analysis_mode":     "turbo_short_term",
            "category":          category,
            "question":          question,
            "market_probability": mp,
            "probability":       probability,
            "confidence":        confidence,
            "decision":          decision,
            "edge_score":        edge_score,
            "risk_score":        risk_score,
            "full_analysis":     full_analysis,
            "display_prediction": decision,
            "reasoning":         full_analysis,
            "main_scenario":     "",
            "alt_scenario":      "",
            "conclusion":        decision,
            "sources":           [],
            "news_sources":      [],
            "key_signals":       [],
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
