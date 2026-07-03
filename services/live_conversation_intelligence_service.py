"""Deterministic Live Conversation Intelligence layer.

Keeps Live Analyst from treating short clarification replies as brand-new forms.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import re
from services.live_election_context_service import extract_election_candidate_context

DOMAINS = {"crypto","sports","esports","politics","polymarket","macro","weather","calculator","translation","explanation","casual","general","unknown"}
MARKET_DOMAINS = {"crypto", "sports", "esports", "politics", "polymarket", "macro"}


def _s(value: Any) -> str:
    return str(value or "").strip()


def _low(text: str) -> str:
    return _s(text).lower().replace("ё", "е")


def _ctx_domain(ctx: Optional[Dict[str, Any]]) -> str:
    if not ctx:
        return "unknown"
    domain = _s(ctx.get("domain") or ctx.get("mode") or ctx.get("market_domain")).lower()
    if domain == "prediction_market":
        domain = "polymarket"
    if domain == "event_betting":
        domain = "sports"
    return domain if domain in DOMAINS else "unknown"


def _ctx_subject(ctx: Optional[Dict[str, Any]]) -> Optional[str]:
    if not ctx:
        return None
    return _s(ctx.get("subject") or ctx.get("event") or ctx.get("teams_event") or ctx.get("asset_pair") or ctx.get("asset") or ctx.get("market") or "") or None


def _missing(ctx: Optional[Dict[str, Any]]) -> list[str]:
    if not ctx:
        return []
    raw = ctx.get("missing_data") or ctx.get("missing") or []
    if isinstance(raw, dict):
        raw = [k for k, v in raw.items() if v]
    return [str(x) for x in raw if str(x or "").strip()]


def _original(ctx: Optional[Dict[str, Any]]) -> str:
    return _s((ctx or {}).get("original_user_text") or (ctx or {}).get("normalized_query") or (ctx or {}).get("question") or "")


def _extract_year(text: str) -> Optional[int]:
    m = re.fullmatch(r"\s*(20\d{2})\s*\??\s*", text or "") or re.search(r"\b(20\d{2})\b", text or "")
    return int(m.group(1)) if m else None


def _extract_url(text: str) -> str:
    m = re.search(r"https?://\S+", text or "")
    return m.group(0).rstrip(".,)") if m else ""


def _extract_odds(text: str) -> Optional[float]:
    m = re.search(r"(?i)(?:к[эе]ф|коэфф(?:ициент)?|odds?)?\s*(\d+(?:[.,]\d+)?)", text or "")
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return val if 1.01 <= val <= 100 else None


def _extract_timeframe(text: str) -> str:
    m = re.fullmatch(r"\s*(\d+\s*(?:m|h|d|м|ч|д))\s*", text or "", flags=re.I) or re.search(r"(?i)\b(\d+\s*(?:m|h|d|м|ч|д))\b", text or "")
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1).lower()).replace("м", "m").replace("ч", "h").replace("д", "d")


def _extract_side(text: str) -> str:
    value = _low(text)
    if value in {"yes", "y", "да", "за", "up"}:
        return "Yes"
    if value in {"no", "n", "нет", "против", "down"}:
        return "No"
    return ""


def _detect_general_intent(text: str) -> tuple[str, str, dict, list[str], Optional[str]]:
    value = _low(text)
    if not value:
        return "unknown", "unknown", {}, [], None
    if re.search(r"\b(привет|здравствуй|hello|hi|hey)\b", value) and len(value.split()) <= 3:
        return "casual", "greeting", {}, [], None
    if "погод" in value or re.search(r"\bweather\b", value):
        city = None
        m = re.search(r"(?:в|in)\s+([A-Za-zА-Яа-яЁёİıÇçĞğÖöŞşÜü\- ]{2,40})\??$", text or "", flags=re.I)
        if m:
            city = m.group(1).strip()
        return "weather", "weather_lookup", {"city": city} if city else {}, ([] if city else ["city"]), city
    if re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(\d+(?:[.,]\d+)?)", text or ""):
        return "calculator", "percent", {}, [], None
    if value.startswith("переведи") or value.startswith("translate"):
        return "translation", "translate", {}, [], None
    if "что значит" in value or "what does" in value or "объясни" in value:
        return "explanation", "explain", {}, [], None
    return "unknown", "unknown", {}, [], None


def _detect_domain(text: str, router_result: Optional[Dict[str, Any]], understanding: Optional[Dict[str, Any]]) -> str:
    if extract_election_candidate_context(text).get("is_election_question"):
        return "politics"
    for src in (router_result, understanding):
        d = _ctx_domain(src)
        if d != "unknown":
            return d
    value = _low(text)
    if "выбор" in value or "election" in value or re.search(r"\b(?:president|senate|congress)\b|президент", value):
        return "politics"
    if "polymarket.com" in value:
        return "polymarket"
    if re.search(r"\b(?:btc|eth|sol|xrp|usdt|bitcoin|биткоин)\b", value):
        return "crypto"
    if any(k in value for k in ("real madrid", "реал", "матч", "кэф", "тотал", "фора")):
        return "sports"
    return "unknown"


def _targeted(domain: str, subject: Optional[str], missing: list[str], ui_language: str) -> str:
    if ui_language == "ru":
        if domain in {"politics", "polymarket"}:
            subj = f"{subject} и " if subject else ""
            return f"Понял: речь про {subj}выборы. Уточни сторону рынка: Yes или No, или пришли Polymarket-ссылку."
        if domain == "sports":
            return f"Понял: {subject or 'матч'}. Уточни рынок: победа, тотал или фора, и коэффициент."
        if domain == "crypto":
            return f"Понял: {subject or 'актив'}. Уточни таймфрейм: 5m, 15m, 1h, 4h или daily."
        if domain == "weather":
            return "В каком городе посмотреть погоду?"
    return "Please clarify one detail needed to continue."


def _reconstruct(original: str, current: str, domain: str, filled: Dict[str, Any], subject: Optional[str]) -> Optional[str]:
    if not original:
        return None
    if domain in {"politics", "polymarket"} and filled.get("election_year"):
        return original.rstrip().rstrip("?").rstrip() + f" {filled['election_year']}?"
    if domain == "crypto" and filled.get("timeframe"):
        return f"{original.rstrip()} Таймфрейм {filled['timeframe']}"
    if domain == "sports":
        cur = current.strip()
        if filled.get("market") == "winner" and subject and "побед" in _low(cur) and subject.lower() not in cur.lower():
            cur = re.sub(r"(?i)победа", f"Победа {subject}", cur, count=1)
        return f"{original.rstrip('?')}? {cur[:1].upper() + cur[1:]}".strip()
    if filled:
        return f"{original.rstrip()} {current.strip()}".strip()
    return None



def _is_explicit_new_domain(text: str) -> bool:
    value = _low(text)
    if re.search(r"\b(?:btc|eth|sol|xrp|usdt|bitcoin|биткоин|крипт|лонг|шорт|futures?|entry)\b", value):
        return True
    if "погод" in value or re.search(r"\bweather\b", value):
        return True
    if any(k in value for k in ("real madrid", "реал", "матч", "тотал", "фора")) and len(value.split()) > 1:
        return True
    return False


def _country_only(text: str) -> str:
    ec = extract_election_candidate_context(f"выборы {text}")
    return ec.get("country") or ""


def _office_only(text: str) -> str:
    ec = extract_election_candidate_context(f"{text} выборы")
    return ec.get("office") or ""


def _has_authoritative_election_context(ctx: Optional[Dict[str, Any]]) -> bool:
    if not ctx:
        return False
    ec = ctx.get("election_context") if isinstance(ctx.get("election_context"), dict) else {}
    return bool(
        _ctx_domain(ctx) in {"politics", "polymarket"}
        or ec
        or ctx.get("candidate")
        or ctx.get("country")
        or ctx.get("office")
        or ctx.get("election_year")
        or ("выбор" in _low(_original(ctx)) or "election" in _low(_original(ctx)))
    )


def _merge_election_context(base: dict, filled: dict) -> dict:
    merged = dict(base or {})
    for key in ("candidate", "country", "office", "election_year", "side", "market_url"):
        if filled.get(key) not in (None, "", [], {}):
            merged[key] = filled[key]
    if merged:
        merged["is_election_question"] = True
        if merged.get("office") == "president":
            merged.setdefault("election_type", "presidential")
    return merged


def _reconstruct_election_followup(original: str, current: str, filled: dict, election_ctx: dict) -> str:
    base = (original or "").strip() or (election_ctx.get("original_user_text") or "").strip()
    if not base:
        cand = election_ctx.get("candidate") or filled.get("candidate") or ""
        country = election_ctx.get("country") or filled.get("country") or ""
        year = election_ctx.get("election_year") or filled.get("election_year") or ""
        base = f"{cand} победит на выборах".strip() if cand else "Кто победит на выборах"
        if country:
            base += f" во {country}" if country == "France" else f" {country}"
        if year:
            base += f" {year}"
        base += "?"
    base = re.sub(r"\s+", " ", base).strip()
    if filled.get("candidate") and not re.search(re.escape(str(filled["candidate"])), base, re.I):
        country_part = ""
        country = election_ctx.get("country") or filled.get("country")
        if country == "France":
            country_part = " во Франции"
        return f"{filled['candidate']} победит на выборах{country_part}?"
    if filled.get("election_year") and not re.search(rf"\b{filled['election_year']}\b", base):
        return base.rstrip().rstrip("?").rstrip() + f" {filled['election_year']}?"
    if filled.get("country") and filled["country"] not in (election_ctx.get("country"),):
        return base.rstrip().rstrip("?").rstrip() + f" {current.strip()}?"
    if filled.get("office") and filled["office"] not in (election_ctx.get("office"),):
        return base.rstrip().rstrip("?").rstrip() + f" {current.strip()}?"
    if filled.get("side"):
        return base.rstrip() + f" {filled['side']}"
    if filled.get("market_url"):
        return base.rstrip() + f" {filled['market_url']}"
    return base


def resolve_short_live_followup(current_text: str, previous_context: dict | None, pending_clarification: dict | None, ui_language: str = "ru") -> dict:
    latest = _s(current_text)
    ctx = pending_clarification or previous_context or {}
    filled: Dict[str, Any] = {}
    notes: list[str] = []
    url = _extract_url(latest)
    side = _extract_side(latest)
    year = _extract_year(latest)
    country = _country_only(latest)
    office = _office_only(latest)
    candidate = extract_election_candidate_context(f"{latest} победит на выборах?").get("candidate") if latest else ""
    bot_prompt = _low(ctx.get("bot_clarification_message") or ctx.get("last_final_answer") or "")
    asked_to_continue = bool("хочешь продолжить" in bot_prompt or "continue" in bot_prompt)
    is_continue = _low(latest) in {"продолжай", "давай", "разбери", "continue"} or (asked_to_continue and side == "Yes")
    is_short = bool(url or side or year or country or office or candidate or is_continue or len(latest.split()) <= 2)
    if not is_short:
        return {"is_short_followup": False, "domain": None, "effective_text": None, "original_user_text": _original(ctx), "latest_user_text": latest, "filled": {}, "election_context": {}, "should_continue_previous_analysis": False, "should_ask_targeted_choice": False, "notes": []}
    if _is_explicit_new_domain(latest) and not (url and "polymarket" in url.lower()):
        return {"is_short_followup": True, "domain": _detect_domain(latest, None, None), "effective_text": None, "original_user_text": _original(ctx), "latest_user_text": latest, "filled": {}, "election_context": {}, "should_continue_previous_analysis": False, "should_ask_targeted_choice": False, "notes": ["explicit_new_domain_overrides_previous_context"]}
    if not _has_authoritative_election_context(ctx):
        return {"is_short_followup": is_short, "domain": None, "effective_text": None, "original_user_text": _original(ctx), "latest_user_text": latest, "filled": {}, "election_context": {}, "should_continue_previous_analysis": False, "should_ask_targeted_choice": False, "notes": ["no_authoritative_previous_election_context"]}
    ec = dict(ctx.get("election_context") or {})
    for key in ("candidate", "country", "office", "election_year", "side", "market_url"):
        if ctx.get(key) not in (None, "", [], {}) and not ec.get(key):
            ec[key] = ctx.get(key)
    if year:
        filled["election_year"] = year
    if side:
        if is_continue:
            notes.append("yes_confirms_continue_previous_analysis")
        elif not ec.get("side"):
            filled["side"] = side
        else:
            is_continue = True
            notes.append("side_already_present_yes_no_means_continue")
    if country:
        filled["country"] = country
    if candidate and not ec.get("candidate"):
        filled["candidate"] = candidate
    if office:
        filled["office"] = office
    if url and "polymarket" in url.lower():
        filled["market_url"] = url
    if is_continue and not filled:
        notes.append("continue_previous_analysis")
    merged_ec = _merge_election_context(ec, filled)
    effective = _reconstruct_election_followup(_original(ctx), latest, filled, merged_ec) if (filled or is_continue or side) else None
    return {"is_short_followup": True, "domain": "politics", "effective_text": effective, "original_user_text": _original(ctx), "latest_user_text": latest, "filled": filled, "election_context": merged_ec, "should_continue_previous_analysis": bool(is_continue), "should_ask_targeted_choice": False, "notes": notes}


def cleanup_final_politics_election_answer(answer: str, evidence_pack: Dict[str, Any], ui_language: str = "ru") -> str:
    ctx = (evidence_pack or {}).get("election_context") or ((evidence_pack or {}).get("conversation_intelligence") or {}).get("election_context") or {}
    domain = str(((evidence_pack or {}).get("conversation_intelligence") or {}).get("domain") or (evidence_pack or {}).get("mode") or "").lower()
    if domain not in {"politics", "polymarket"} and not ctx:
        return answer
    lines = []
    seen_decision = False
    banned = re.compile(r"(?i)(playable odds|fair price|value\s+под\s+твой\s+коэффициент|\bNO BET\b|\bставк|поставить)")
    for line in (answer or "").splitlines():
        if banned.search(line):
            continue
        if re.search(r"(?i)^\s*Decision:\s*DATA NEEDED", line):
            if seen_decision:
                continue
            seen_decision = True
        if "Решение:" in line:
            seen_decision = True
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    year = ctx.get("election_year") or "нужный год"
    safe = [
        f"Найти активный Polymarket-рынок на выборы {year}?",
        "Проверить eligibility, правила resolution и ликвидность?",
        "Разобрать сценарии: кандидат, номинация, партия, преемник?",
    ]
    cleaned = re.sub(r"(?is)(?:\n\s*)?(?:Хочешь|Могу|Дальше|Следующий шаг).{0,700}$", "", cleaned).strip()
    if not all(term.lower() in cleaned.lower() for term in ("polymarket", "eligibility", "resolution")) or "ликвид" not in cleaned.lower():
        cleaned = cleaned.rstrip() + "\n\n" + "\n".join(f"• {x}" for x in safe)
    return cleaned

def resolve_live_conversation_intent(current_text: str, *, previous_context: dict | None = None, pending_clarification: dict | None = None, router_result: dict | None = None, understanding: dict | None = None, ui_language: str = "ru") -> dict:
    current_text = _s(current_text)
    notes: list[str] = []
    ctx = pending_clarification or previous_context or {}
    short_followup = resolve_short_live_followup(current_text, previous_context, pending_clarification, ui_language=ui_language)
    if short_followup.get("effective_text") or short_followup.get("should_continue_previous_analysis"):
        filled = short_followup.get("filled") or {}
        election_ctx = short_followup.get("election_context") or {}
        return {"ok": True, "is_followup": True, "is_short_followup": True, "is_clarification_answer": bool(pending_clarification and filled), "should_reconstruct_question": bool(short_followup.get("effective_text")), "completed_text": short_followup.get("effective_text"), "domain": short_followup.get("domain") or "politics", "intent": "probability_check", "subject": election_ctx.get("candidate") or _ctx_subject(ctx), "filled": filled, "remaining_missing": [], "answer_strategy": "continue_previous_analysis" if short_followup.get("should_continue_previous_analysis") else "market_lookup", "clarification_message": None, "confidence": 0.94, "election_context": election_ctx, "short_followup": short_followup, "notes": short_followup.get("notes") or []}
    ctx_domain = _ctx_domain(ctx)
    if short_followup.get("notes") and "explicit_new_domain_overrides_previous_context" in short_followup.get("notes"):
        ctx_domain = "unknown"
    domain = ctx_domain if ctx_domain != "unknown" else _detect_domain(current_text, router_result, understanding)
    election_ctx = extract_election_candidate_context(current_text, previous_context=previous_context, pending_clarification=pending_clarification, ui_language=ui_language)
    if not election_ctx.get("is_election_question") and isinstance(ctx.get("election_context"), dict) and ctx.get("election_context"):
        election_ctx = dict(ctx.get("election_context") or {})
    if not election_ctx.get("is_election_question") and _original(ctx):
        election_ctx = extract_election_candidate_context(_original(ctx), previous_context=previous_context, pending_clarification=pending_clarification, ui_language=ui_language)
    subject = _ctx_subject(ctx) or _ctx_subject(understanding) or _ctx_subject(router_result) or election_ctx.get("candidate")
    missing = _missing(ctx)
    filled: Dict[str, Any] = {}
    is_followup = bool(ctx and current_text and (len(current_text.split()) <= 5 or _extract_url(current_text)))

    gen_domain, gen_intent, gen_filled, gen_missing, gen_subject = _detect_general_intent(current_text)
    if ctx_domain == "unknown" and gen_domain != "unknown":
        domain, subject, filled, missing = gen_domain, gen_subject, gen_filled, gen_missing
        intent = gen_intent
        strategy = {"weather": "weather_lookup" if not missing else "targeted_clarification", "calculator": "calculate", "translation": "translate", "explanation": "explain", "casual": "answer_now"}.get(domain, "answer_now")
        return {"ok": True, "is_followup": False, "is_clarification_answer": False, "should_reconstruct_question": False, "completed_text": None, "domain": domain, "intent": intent, "subject": subject, "filled": filled, "remaining_missing": missing, "answer_strategy": strategy, "clarification_message": _targeted(domain, subject, missing, ui_language) if strategy == "targeted_clarification" else None, "confidence": 0.9, "election_context": election_ctx if election_ctx.get("is_election_question") else {}, "notes": notes}

    url = _extract_url(current_text)
    if url and ("polymarket" in url.lower() or domain in {"politics", "polymarket"}):
        filled["market_url"] = url
    year = _extract_year(current_text)
    if year and domain in {"politics", "polymarket"}:
        filled["election_year"] = year
    side = _extract_side(current_text)
    if side and domain in {"politics", "polymarket"}:
        filled["side"] = side
    tf = _extract_timeframe(current_text)
    if tf and domain == "crypto":
        filled["timeframe"] = tf
    odds = _extract_odds(current_text)
    if odds and domain == "sports":
        filled["odds"] = odds
    if domain == "sports" and any(k in _low(current_text) for k in ("побед", "winner", "moneyline")):
        filled["market"] = "winner"

    remaining = [m for m in missing if m not in filled]
    # Normalize equivalent missing names.
    if "year" in remaining and "election_year" in filled:
        remaining.remove("year")
    if domain in MARKET_DOMAINS and ctx_domain != "unknown" and not filled and not remaining:
        remaining = missing
    completed = _reconstruct(_original(ctx), current_text, domain, filled, subject) if (ctx and filled) else None
    strategy = "generic_clarification"
    if domain in {"weather"}:
        strategy = "weather_lookup" if not remaining else "targeted_clarification"
    elif domain in {"calculator"}:
        strategy = "calculate"
    elif domain in {"translation"}:
        strategy = "translate"
    elif domain in {"explanation"}:
        strategy = "explain"
    elif domain == "casual":
        strategy = "answer_now"
    elif domain in {"politics", "polymarket"}:
        strategy = "market_lookup" if filled.get("election_year") or filled.get("market_url") else ("calculate" if filled.get("side") and any(x in filled for x in ("probability", "odds")) else "targeted_clarification")
    elif domain == "sports":
        strategy = "calculate" if filled.get("odds") else "targeted_clarification"
    elif domain == "crypto":
        strategy = "answer_now" if filled.get("timeframe") else "targeted_clarification"
    elif domain != "unknown":
        strategy = "answer_now"
    if ctx_domain != "unknown" and strategy == "generic_clarification":
        strategy = "targeted_clarification"
    intent = _s((ctx or {}).get("intent") or (understanding or {}).get("intent") or (router_result or {}).get("intent")) or ("probability_check" if domain in {"politics", "polymarket"} else "live_analysis")
    return {"ok": True, "is_followup": is_followup, "is_clarification_answer": bool(pending_clarification and filled), "should_reconstruct_question": bool(completed), "completed_text": completed, "domain": domain, "intent": intent, "subject": subject, "filled": filled, "remaining_missing": remaining, "answer_strategy": strategy, "clarification_message": _targeted(domain, subject, remaining, ui_language) if strategy == "targeted_clarification" else ("Что разбираем: крипту, спорт, киберспорт, политику или Polymarket-событие?" if strategy == "generic_clarification" else None), "confidence": 0.86 if filled or domain != "unknown" else 0.35, "election_context": election_ctx if election_ctx.get("is_election_question") else {}, "notes": notes}
