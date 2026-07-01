"""Autonomous resolver for broad Live Analyst market questions."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def get_crypto_market_context(*args, **kwargs):
    from services.crypto_market_context_service import get_crypto_market_context as _impl
    return _impl(*args, **kwargs)


def find_related_markets(*args, **kwargs):
    from services.polymarket_service import find_related_markets as _impl
    return _impl(*args, **kwargs)


def normalize_market_data(*args, **kwargs):
    from services.polymarket_service import normalize_market_data as _impl
    return _impl(*args, **kwargs)


def search_web(*args, **kwargs):
    from services.web_search_service import search_web as _impl
    return _impl(*args, **kwargs)

_DOMAINS = ("crypto", "sports", "esports", "politics", "polymarket", "macro", "event", "unknown")


def _base() -> Dict[str, Any]:
    return {"ok": True, "resolved": False, "domain": "unknown", "intent": "unknown", "subject": None, "market_title": None, "market_url": None, "market_probability": None, "odds": None, "implied_probability": None, "line": None, "event_time": None, "source": None, "freshness": "unknown", "missing_data": [], "search_attempted": False, "queries": [], "notes": []}


def _add_missing(out: Dict[str, Any], *items: str) -> None:
    for item in items:
        if item and item not in out["missing_data"]:
            out["missing_data"].append(item)


def implied_probability_from_decimal_odds(odds: Any) -> Optional[float]:
    try:
        value = float(odds)
    except (TypeError, ValueError):
        return None
    if value <= 1.0 or value > 1000:
        return None
    return round(100.0 / value, 2)


def _detect_domain(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"\b(btc|eth|sol|xrp|bitcoin|ethereum|crypto|long|short|entry|futures)\b|лонг|шорт|крипт|биткоин|эфир", low):
        return "crypto"
    if re.search(r"polymarket|prediction market|\bmarket\b|probability|odds|шанс|вероятност|рынок", low):
        if re.search(r"trump|biden|harris|election|president|senate|congress|трамп|байден|камала|выбор|президент", low):
            return "politics"
        return "polymarket"
    if re.search(r"trump|biden|harris|election|elections|president|senate|congress|трамп|байден|камала|выбор|президент|политик", low):
        return "politics"
    if re.search(r"real madrid|barcelona|lakers|celtics|ufc|match|football|basketball|tennis|матч|коэффициент|кэф|фора|тотал|победа", low):
        return "sports"
    if re.search(r"esports|кибер|cs2|dota|valorant|lol\b|league of legends", low):
        return "esports"
    if re.search(r"fed|cpi|inflation|rate cut|ставк|инфляц|макро", low):
        return "macro"
    return "unknown"


def _detect_intent(text: str, domain: str) -> str:
    low = (text or "").lower()
    if low.strip() in {"политика", "politics", "спорт", "sports", "крипта", "crypto", "polymarket"}:
        return "domain_entry"
    if any(x in low for x in ("value", "edge", "кэф", "коэффициент", "odds")):
        return "odds_value"
    if domain == "sports" and any(x in low for x in ("match", "матч", "real madrid", "lakers")):
        return "match_analysis"
    if any(x in low for x in ("probability", "шанс", "вероят", "победит", "win")):
        return "probability_check"
    if domain in {"polymarket", "politics"}:
        return "market_lookup"
    if domain == "crypto":
        return "forecast"
    return "unknown"


def _subject(text: str, domain: str) -> Optional[str]:
    low = text.lower()
    known = [("Real Madrid", "real madrid"), ("Barcelona", "barcelona"), ("Lakers", "lakers"), ("Celtics", "celtics"), ("UFC", "ufc"), ("Trump", "trump|трамп"), ("Biden", "biden|байден"), ("Harris", "harris|камала"), ("BTC", r"\bbtc\b|bitcoin|биткоин"), ("ETH", r"\beth\b|ethereum|эфир"), ("SOL", r"\bsol\b"), ("XRP", r"\bxrp\b")]
    hits = [name for name, pat in known if re.search(pat, low)]
    if hits:
        return " / ".join(dict.fromkeys(hits))
    cleaned = re.sub(r"[?!.]+", "", text).strip()
    return cleaned or None


def _extract_election_year(text: str) -> Optional[int]:
    match = re.search(r"\b(20\d{2}|19\d{2})\b", text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _has_yes_no_side(text: str) -> bool:
    low = (text or "").lower()
    return bool(re.search(r"\b(yes|no)\b|\bда\b|\bнет\b", low))


def _is_ambiguous_election_reference(text: str, domain: str) -> bool:
    if domain != "politics":
        return False
    low = (text or "").lower()
    electionish = bool(re.search(r"election|elections|выбор|president|президент", low))
    broad_win = bool(re.search(r"\bwin\b|побед", low)) and bool(re.search(r"trump|трамп|кто|who", low))
    if not (electionish or broad_win):
        return False
    if _extract_election_year(text):
        return False
    if re.search(r"polymarket\.com|slug=|condition_id=|/event/|/market/", low):
        return False
    return True


def _targeted_queries(text: str, domain: str, subject: Optional[str]) -> List[str]:
    s = subject or text.strip()
    if domain == "politics":
        return [f"Polymarket {s} election market", f"{s} probability Polymarket", f"{s} election odds prediction market"]
    if domain == "sports":
        return [f"{s} next match odds", f"{s} moneyline odds today", f"{s} vs odds"]
    if domain == "crypto":
        return [f"{s} current crypto market price news", f"{s} price forecast today"]
    if domain == "polymarket":
        return [f"site:polymarket.com {s}", f"Polymarket {s}"]
    return [f"{s} odds probability market"]


def _float_probability(value: Any) -> Optional[float]:
    if value in (None, "", "Unknown"):
        return None
    txt = str(value).strip().replace("%", "")
    try:
        num = float(txt)
    except ValueError:
        return None
    if 0 <= num <= 1:
        num *= 100
    return round(max(0.0, min(100.0, num)), 2)


def _try_polymarket(out: Dict[str, Any], text: str, domain: str) -> None:
    if domain not in {"politics", "polymarket", "event"}:
        return
    try:
        items = find_related_markets(text, category_hint="politics" if domain == "politics" else "", limit=3)
    except Exception as exc:
        out["notes"].append(f"polymarket_lookup_failed: {exc}")
        return
    if not items:
        return
    normalized = normalize_market_data(items[0])
    if not normalized:
        return
    out.update({"resolved": True, "market_title": normalized.get("question"), "market_url": normalized.get("url"), "market_probability": _float_probability(normalized.get("market_probability")), "source": "Polymarket", "freshness": "fresh"})
    if len(items) > 1:
        out["notes"].append("multiple_possible_markets")


def resolve_live_market_context(user_text: str, *, ui_language: str = "ru", router_result: dict | None = None, understanding: dict | None = None, recent_messages: list | None = None) -> dict:
    out = _base(); text = user_text or ""
    domain = _detect_domain(text)
    intent = _detect_intent(text, domain)
    subject = _subject(text, domain)
    out.update({"domain": domain, "intent": intent, "subject": subject})
    election_year = _extract_election_year(text) if domain == "politics" else None
    if election_year:
        out["election_year"] = election_year
    ambiguous_election = _is_ambiguous_election_reference(text, domain)
    odds_match = re.search(r"(?i)(?:odds|кэф|коэффициент)\s*[:=]?\s*(\d+(?:[.,]\d+)?)", text)
    if odds_match:
        try:
            out["odds"] = float(odds_match.group(1).replace(",", "."))
            out["implied_probability"] = implied_probability_from_decimal_odds(out["odds"])
            out["resolved"] = True
        except Exception:
            pass
    if domain == "unknown":
        _add_missing(out, "domain", "event_or_market")
        return out
    if intent == "domain_entry":
        out["resolved"] = False
        _add_missing(out, "event")
        return out
    if ambiguous_election:
        out["resolved"] = False
        out["search_attempted"] = True
        out["intent"] = "probability_check" if intent in {"unknown", "market_lookup"} else intent
        _add_missing(out, "election_year", "market", "side", "probability")
        if "ambiguous_election_reference" not in out["notes"]:
            out["notes"].append("ambiguous_election_reference")
        return out
    out["queries"] = _targeted_queries(text, domain, subject)[:3]
    if domain in {"politics", "polymarket", "sports", "esports", "macro", "event"}:
        out["search_attempted"] = True
    if domain == "crypto":
        asset = (subject or "BTC").split("/")[0].strip().upper()
        pair = asset if asset.endswith("USDT") else f"{asset}USDT"
        try:
            ctx = get_crypto_market_context(pair, (understanding or {}).get("timeframe") or "", (understanding or {}).get("horizon") or "")
            if ctx.get("ok"):
                out.update({"resolved": True, "source": ctx.get("price_source") or "crypto_market_context", "freshness": "fresh"})
        except Exception as exc:
            out["notes"].append(f"crypto_context_failed: {exc}")
        return out
    _try_polymarket(out, text, domain)
    if not out["resolved"]:
        for q in out["queries"][:2]:
            rows = search_web(q, limit=3)
            if rows:
                out.update({"resolved": True, "source": rows[0].get("source") or "web_search", "market_title": rows[0].get("title"), "market_url": rows[0].get("url"), "freshness": "partial"})
                break
    if out["odds"]:
        out["implied_probability"] = implied_probability_from_decimal_odds(out["odds"])
    if domain in {"politics", "polymarket"} and out.get("market_probability") is None:
        _add_missing(out, "market", "probability")
    if domain == "politics" and _extract_election_year(text) and not _has_yes_no_side(text):
        _add_missing(out, "side")
    if domain == "sports" and out.get("odds") is None:
        _add_missing(out, "odds", "market")
    return out


def domain_aware_clarification(domain: str, ui_language: str = "ru") -> str:
    domain = str(domain or "unknown").lower()
    if ui_language == "ru":
        messages = {
            "politics": "Понял: политика. Какое событие разбираем? Например: ‘Трамп победит на выборах?’ или пришли Polymarket-ссылку.",
            "sports": "Понял: спорт. Напиши матч/команды и, если есть, коэффициент или рынок.",
            "crypto": "Понял: крипта. Напиши актив/пару и таймфрейм.",
            "polymarket": "Пришли ссылку на рынок или название события.",
        }
        return messages.get(domain, "Что разбираем: крипту, спорт, киберспорт, политику или Polymarket-событие? Напиши событие обычным текстом — я сам попробую найти рынок и данные.")
    messages = {
        "politics": "Got it: politics. Which event should we analyze? For example: ‘Trump win election?’ or send a Polymarket link.",
        "sports": "Got it: sports. Send the match/teams and, if available, odds or market.",
        "crypto": "Got it: crypto. Send the asset/pair and timeframe.",
        "polymarket": "Send the market link or event name.",
    }
    return messages.get(domain, "What should we analyze: crypto, sports, esports, politics, or a Polymarket event? Write the event in plain text — I will try to find market data myself.")


def merge_market_resolution_into_pack(evidence_pack: Dict[str, Any], resolver_result: Dict[str, Any]) -> None:
    if not evidence_pack or not resolver_result:
        return
    evidence_pack["market_resolution"] = resolver_result
    domain = resolver_result.get("domain")
    if domain and (evidence_pack.get("mode") in (None, "", "unknown", "general")):
        evidence_pack["mode"] = "polymarket" if domain == "politics" else domain
    facts = evidence_pack.setdefault("derived_facts", {})
    for src, dst in (("market_probability", "polymarket_probability"), ("implied_probability", "implied_probability"), ("odds", "odds"), ("market_url", "market_url"), ("market_title", "market_title"), ("domain", "domain")):
        val = resolver_result.get(src)
        if val not in (None, "", []):
            facts[dst] = val
    missing = evidence_pack.setdefault("missing_data", [])
    for item in resolver_result.get("missing_data") or []:
        if item not in missing:
            missing.append(item)
    if resolver_result.get("source"):
        evidence_pack.setdefault("evidence_items", []).append({"type": "market_resolution", "title": resolver_result.get("market_title") or resolver_result.get("subject") or "Resolved market context", "summary": "Autonomous resolver context.", "source": resolver_result.get("source"), "url": resolver_result.get("market_url") or "", "freshness": resolver_result.get("freshness") or "unknown", "relevance": 0.8, "reliability": 0.65})
    if resolver_result.get("search_attempted") and not (resolver_result.get("market_probability") or resolver_result.get("implied_probability") or resolver_result.get("odds")):
        evidence_pack["recommended_decision_labels"] = ["DATA NEEDED", "WATCH"]
