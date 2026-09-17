"""Deterministic safety boundary for VELIA Research Center.

The Research Center may study sensitive subjects for medicine, safety, history or
protection, but it must never turn those subjects into operational harmful
capability. This module sits below research agents and is intentionally model-
independent: prompts, agent roles and admin wording cannot disable it.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List


MAX_TEXT = 12000


def _norm(value: object) -> str:
    text = str(value or "")[:MAX_TEXT].lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


_BYPASS = (
    r"\b(ignore|disable|bypass|remove|override)\b.{0,45}\b(safety|guard|policy|restriction|filter)\b",
    r"\b(jailbreak|developer mode|unfiltered mode)\b",
    r"(отключ|обой|игнор|сними|убери).{0,45}(безопас|огранич|фильтр|политик)",
    r"(режим без ограничений|джейлбрейк)",
)

_OPERATIONAL = (
    r"\b(synthesi[sz]e|manufactur|produce|extract|purif|concentrat|formulat|optimi[sz]e|weaponize)\w*\b",
    r"\b(step[- ]by[- ]step|recipe|precursor|yield|dosage|delivery mechanism|payload)\b",
    r"(синтез|изготов|производ|получени|экстрак|очист|концентр|формуляц|оптимиз|рецепт|прекурсор|выход продукта|пошагов|способ доставк)",
)

_ILLICIT_DRUG = (
    r"\b(fentanyl|heroin|methamphetamine|cocaine|mdma|lsd|carfentanil)\b",
    r"(фентанил|карфентанил|героин|метамфетамин|кокаин|мдма|лсд|наркотик|рекреационн.{0,12}(веществ|препарат))",
)

_TOXIN = (
    r"\b(ricin|cyanide|botulinum toxin|poison|toxin|toxic agent)\b",
    r"(рицин|цианид|ботулин.{0,12}токсин|яд\b|ядовит|токсин|отравляющ.{0,12}веществ)",
)

_CYBER = (
    r"\b(ransomware|malware|keylogger|botnet|shellcode|rootkit|credential stealer|phishing kit|exploit)\b",
    r"(вымогател|вредонос|малвар|кейлоггер|ботнет|шеллкод|рутки|стилер|фишинг|эксплойт|взлом)",
)

_CYBER_HARM = (
    r"\b(steal|exfiltrat|deploy|infect|persist|evade detection|bypass auth|gain access|take over|hack|compromise)\w*\b",
    r"\b(write|create|build|develop|code|generate)\w*\b.{0,60}\b(ransomware|malware|keylogger|botnet|shellcode|rootkit|credential stealer|phishing kit|exploit)\b",
    r"\b(ransomware|malware|keylogger|botnet|shellcode|rootkit|credential stealer|phishing kit|exploit)\b.{0,60}\b(write|create|build|develop|code|generate|deploy)\w*\b",
    r"(украст|эксфильтр|зараз|закреп|обойти.{0,20}(auth|аутентиф|защит)|получить доступ|захватить|взлом)",
    r"(напиш|созда|разработ|сгенер|собер).{0,60}(вымогател|вредонос|малвар|кейлоггер|ботнет|шеллкод|рутки|стилер|фишинг|эксплойт)",
)

_WEAPON = (
    r"\b(explosive|bomb|grenade|detonator|weapon|firearm|silencer)\b",
    r"(взрывчат|бомб|гранат|детонатор|оружи|огнестрел|глушител)",
)

_PATHOGEN = (
    r"\b(pathogen|virus|bacteri(?:a|um)|fungus|toxin-producing organism)\b",
    r"(патоген|вирус|бактери|грибков.{0,12}(патоген|штамм)|микроорганизм)",
)

_PATHOGEN_HARM = (
    r"\b(increase|enhance|improve|optimi[sz]e)\b.{0,45}\b(virulence|transmissibility|immune evasion|lethality)\b",
    r"\b(aerosoli[sz]e|weaponize)\w*\b",
    r"(повыс|усил|увелич).{0,45}(вирулент|заразност|передач|обход иммун|летальн)",
    r"(аэрозолиз|оружейное примен|биооруж)",
)

_SELF_HARM = (
    r"\b(suicide method|kill myself|self[- ]harm method)\b",
    r"(способ самоубийств|как покончить с собой|метод самоповрежден)",
)

_DEFENSIVE = (
    r"\b(detect|detection|defen[cs]e|mitigat|prevent|forensic|incident response|patch|hardening|treatment|antidote|overdose|harm reduction|epidemiolog|diagnos|therapy|vaccine)\w*\b",
    r"(обнаруж|защит|предотврат|смягч|форензик|реагирован|патч|усилен.{0,12}безопас|лечен|антидот|передоз|снижени.{0,12}вред|эпидемиолог|диагност|терап|вакцин)",
)

_MEDICAL = (
    r"\b(medicine|medical|clinical|disease|patient|therapy|treatment|diagnos|pharmacolog|toxicolog|oncolog|neurolog|cardiolog|biomarker)\w*\b",
    r"(медицин|клинич|заболев|пациент|терап|лечен|диагност|фармаколог|токсиколог|онколог|невролог|кардиолог|биомаркер)",
)


def classify(text: object, *, phase: str = "intake") -> Dict[str, object]:
    """Classify a research request/branch before any external action.

    Returns one of ``allowed``, ``restricted_defensive`` or ``blocked``.
    ``blocked`` must never be executable. ``restricted_defensive`` remains
    researchable but downstream tools must stay read-only/non-operational.
    """
    value = _norm(text)
    categories: List[str] = []
    reasons: List[str] = []

    bypass = _matches(value, _BYPASS)
    operational = _matches(value, _OPERATIONAL)
    defensive = _matches(value, _DEFENSIVE)

    def add(name: str, reason: str) -> None:
        if name not in categories:
            categories.append(name)
            reasons.append(reason)

    if _matches(value, _SELF_HARM):
        add("self_harm", "operational_self_harm")

    drug = _matches(value, _ILLICIT_DRUG)
    toxin = _matches(value, _TOXIN)
    cyber = _matches(value, _CYBER)
    weapon = _matches(value, _WEAPON)
    pathogen = _matches(value, _PATHOGEN)

    if drug and operational:
        add("illicit_drugs", "drug_creation_or_optimization")
    if toxin and operational:
        add("toxins", "toxin_creation_or_optimization")
    if weapon and operational:
        add("weapons", "weapon_or_explosive_creation")
    if pathogen and (_matches(value, _PATHOGEN_HARM) or operational and not defensive):
        add("pathogens", "harmful_pathogen_engineering")
    if cyber and (_matches(value, _CYBER_HARM) or operational and not defensive):
        add("offensive_cyber", "offensive_cyber_capability")

    dangerous_subject = drug or toxin or cyber or weapon or pathogen
    if bypass and (dangerous_subject or phase != "intake"):
        add("safety_evasion", "attempt_to_bypass_research_safety")

    if categories:
        return {
            "decision": "blocked",
            "phase": str(phase)[:40],
            "categories": categories,
            "reasons": reasons,
            "execution_allowed": False,
            "read_only_only": True,
        }

    sensitive = dangerous_subject or defensive
    if sensitive:
        return {
            "decision": "restricted_defensive",
            "phase": str(phase)[:40],
            "categories": [
                name for name, present in (
                    ("controlled_substances", drug), ("toxicology", toxin),
                    ("cybersecurity", cyber), ("weapons_safety", weapon),
                    ("biosafety", pathogen),
                ) if present
            ],
            "reasons": ["sensitive_subject_read_only_or_protective_research"],
            "execution_allowed": True,
            "read_only_only": True,
        }

    return {
        "decision": "allowed",
        "phase": str(phase)[:40],
        "categories": ["medical_research"] if _matches(value, _MEDICAL) else [],
        "reasons": [],
        "execution_allowed": True,
        "read_only_only": False,
    }


def require_executable(text: object, *, phase: str) -> Dict[str, object]:
    decision = classify(text, phase=phase)
    if not decision["execution_allowed"]:
        raise ValueError("velia_research_safety_blocked")
    return decision
