"""Deterministic election-question context extraction for Live Analyst.

This module intentionally avoids candidate-specific eligibility conclusions.  It
extracts what the user actually supplied (or what can be inferred with high
confidence from a tiny set of public-person hints) and marks eligibility as
unknown/unclear unless evidence is provided by another layer.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

_HINTS = {
    "trump": ("Trump", "United States"), "трамп": ("Трамп", "United States"),
    "macron": ("Macron", "France"), "макрон": ("Макрон", "France"),
    "biden": ("Biden", "United States"), "байден": ("Байден", "United States"),
    "putin": ("Putin", "Russia"), "путин": ("Путин", "Russia"),
    "zelensky": ("Zelensky", "Ukraine"), "zelenskyy": ("Zelensky", "Ukraine"), "зеленский": ("Зеленский", "Ukraine"),
    "erdogan": ("Erdogan", "Turkey"), "эрдоган": ("Эрдоган", "Turkey"),
    "le pen": ("Le Pen", "France"), "лепен": ("Ле Пен", "France"), "ле пен": ("Ле Пен", "France"),
}
_COUNTRIES = {
    "france": "France", "франции": "France", "франция": "France", "french": "France",
    "united states": "United States", "usa": "United States", "us": "United States", "сша": "United States", "америке": "United States",
    "russia": "Russia", "россии": "Russia", "россия": "Russia",
    "ukraine": "Ukraine", "украине": "Ukraine", "украина": "Ukraine",
    "turkey": "Turkey", "турции": "Turkey", "турция": "Turkey",
}
_OFFICES = {
    r"presidential|president|президент(?:ск|ом|а|ы|ских|ские|ских)?": "president",
    r"parliament|парламент": "parliament",
    r"senate|сенат": "senate",
    r"congress|конгресс": "congress",
}
_ELECTION_RE = re.compile(r"election|elections|vote|ballot|president|senate|congress|выбор|голосован|президент|парламент", re.I)
_WIN_RE = re.compile(r"\bwin(?:s|ning)?\b|побед|выигра", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)
_CAP_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}|[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2})\b")
_STOP = {"will", "who", "what", "when", "where", "кто", "что", "будет", "выборы", "выборах", "следующих", "next", "yes", "no"}


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _year(text: str) -> Optional[int]:
    m = re.search(r"\b(19\d{2}|20\d{2})\b", text or "")
    return int(m.group(1)) if m else None


def _side(text: str) -> Optional[str]:
    low = _norm(text)
    if re.search(r"\b(yes|y)\b|\bда\b", low):
        return "Yes"
    if re.search(r"\b(no|n)\b|\bнет\b", low):
        return "No"
    return None


def _country(text: str, candidate: Optional[str]) -> Optional[str]:
    low = _norm(text)
    for key, val in _COUNTRIES.items():
        if re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", low):
            return val
    if candidate:
        clow = _norm(candidate)
        for key, (_, val) in _HINTS.items():
            if key in low or key in clow:
                return val
    return None


def _office(text: str) -> Optional[str]:
    for pat, val in _OFFICES.items():
        if re.search(pat, text or "", re.I):
            return val
    return None


def _hint_candidate(text: str) -> Optional[str]:
    low = _norm(text)
    for key, (name, _) in sorted(_HINTS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", low):
            return name if re.search(r"[A-Za-z]", text) else (re.search(rf"(?i){re.escape(key)}", text) or [name])[0]
    return None


def _candidate_before_verb(text: str) -> Optional[str]:
    patterns = [
        r"^\s*(?:will\s+)?([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’-]*(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’-]*){0,2})\s+win\b",
        r"^\s*([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’-]*(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’-]*){0,2})\s+победит\b",
    ]
    for pat in patterns:
        m = re.search(pat, text or "", re.I)
        if m:
            cand = m.group(1).strip()
            if _norm(cand) not in _STOP:
                return cand
    return None


def _capitalized_near_election(text: str) -> Optional[str]:
    if not (_ELECTION_RE.search(text or "") or _WIN_RE.search(text or "")):
        return None
    for m in _CAP_NAME_RE.finditer(text or ""):
        cand = m.group(1).strip()
        low_cand = _norm(cand)
        if low_cand in _COUNTRIES or low_cand.split()[0] in _STOP:
            continue
        if len(cand) > 1:
            return cand
    return None


def _candidate(text: str) -> Optional[str]:
    return _candidate_before_verb(text) or _hint_candidate(text) or _capitalized_near_election(text)


def extract_election_candidate_context(text: str, *, previous_context: dict | None = None, pending_clarification: dict | None = None, ui_language: str = "ru") -> Dict[str, Any]:
    raw = text or ""
    low = _norm(raw)
    candidate = _candidate(raw)
    election_year = _year(raw)
    is_election_question = bool(_ELECTION_RE.search(raw) or (_WIN_RE.search(raw) and (candidate or election_year or "next" in low or "следующ" in low)))
    country = _country(raw, candidate)
    office = _office(raw)
    market_url_m = _URL_RE.search(raw)
    election_type = "presidential" if office == "president" else ("general" if is_election_question and re.search(r"election|выбор", low) else None)
    missing: list[str] = []
    if is_election_question:
        if not candidate and not re.search(r"\bwho\b|\bкто\b", low):
            missing.append("candidate")
        if not country:
            missing.append("country")
        if not office:
            missing.append("office")
        if not election_year and not re.search(r"\bnext\b|следующ", low):
            missing.append("election_year")
    notes: list[str] = []
    if candidate and candidate.lower() in {"trump", "трамп"}:
        notes.append("candidate_extracted_without_trump_specific_logic")
    return {
        "is_election_question": is_election_question,
        "candidate": candidate,
        "country": country,
        "office": office,
        "election_year": election_year,
        "election_type": election_type,
        "side": _side(raw),
        "market_url": market_url_m.group(0).rstrip(".,)") if market_url_m else None,
        "needs_eligibility_check": bool(is_election_question and candidate),
        "eligibility_status": "unknown" if candidate else "unclear",
        "eligibility_reason": None,
        "missing_data": missing,
        "notes": notes,
    }
