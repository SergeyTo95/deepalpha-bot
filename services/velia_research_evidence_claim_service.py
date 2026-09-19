"""Read-only Evidence Claims derived from the latest Research Center synthesis.

Evidence Claims are intentionally separate from the immutable experimental Claim
Ledger. They summarize literature-backed synthesis output and never promote a
literature conclusion into a preregistered experimental claim.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_reasoning_service as reasoning

MAX_EVIDENCE_CLAIMS = 24
VERDICTS = {"supported", "contradicted", "conflicting", "insufficient"}
CONFIDENCE = {"high", "moderate", "low", "uncertain"}
STANCE_TO_VERDICT = {
    "supports": "supported",
    "challenges": "contradicted",
    "mixed": "conflicting",
    "context": "insufficient",
    "uncertain": "insufficient",
}


def enabled() -> bool:
    return center.enabled() and reasoning.enabled()


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "feature": "research_evidence_claims",
        "read_only": True,
        "derived_from_latest_synthesis": True,
        "experimental_claim_ledger_separate": True,
        "legacy_synthesis_fallback": True,
    }


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _claim_id(synthesis_id: str, statement: str, source_ids: List[str]) -> str:
    frozen = synthesis_id + "|" + statement + "|" + "|".join(sorted(source_ids))
    return hashlib.sha256(frozen.encode("utf-8")).hexdigest()


def _latest_synthesis(user_id: int, mission_id: str) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT synthesis_id,evidence_hash,source_ids_json,result_json,provider,created_at
            FROM velia_research_syntheses
            WHERE mission_id=%s AND user_id=%s AND status='completed'
            ORDER BY created_at DESC,synthesis_id DESC LIMIT 1""",
            (str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row or not row["result_json"]:
        return {}
    return row


def _source_map(user_id: int, mission_id: str, source_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not source_ids:
        return {}
    with projects.transaction() as cur:
        cur.execute("""SELECT source_id,provider,doi,title,source_url,published_year,venue
            FROM velia_research_sources
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        rows = cur.fetchall()
    return {
        row["source_id"]: {
            "id": row["source_id"],
            "provider": row["provider"] or "",
            "doi": row["doi"] or "",
            "title": row["title"] or "",
            "url": row["source_url"] or "",
            "published_year": row["published_year"],
            "venue": row["venue"] or "",
        }
        for row in rows
        if row["source_id"] in set(source_ids)
    }


def _explicit_claims(
    result: Dict[str, Any],
    synthesis_id: str,
    source_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    values = result.get("evidence_claims")
    if not isinstance(values, list):
        return []
    output: List[Dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        statement = _text(item.get("statement"), 1200)
        rationale = _text(item.get("rationale"), 1800)
        verdict = _text(item.get("verdict"), 24).lower()
        confidence = _text(item.get("confidence"), 24).lower()
        raw_ids = item.get("source_ids")
        if not statement or verdict not in VERDICTS:
            continue
        source_ids = []
        if isinstance(raw_ids, list):
            for value in raw_ids:
                source_id = _text(value, 128)
                if source_id in source_map and source_id not in source_ids:
                    source_ids.append(source_id)
                if len(source_ids) >= 12:
                    break
        if not source_ids:
            continue
        output.append({
            "id": _claim_id(synthesis_id, statement, source_ids),
            "statement": statement,
            "verdict": verdict,
            "confidence": confidence if confidence in CONFIDENCE else "uncertain",
            "rationale": rationale,
            "source_ids": source_ids,
            "sources": [source_map[source_id] for source_id in source_ids],
            "origin": "synthesis",
        })
        if len(output) >= MAX_EVIDENCE_CLAIMS:
            break
    return output


def _fallback_claims(
    result: Dict[str, Any],
    synthesis_id: str,
    source_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    assessments = result.get("evidence_assessment")
    if not isinstance(assessments, list):
        return []
    output: List[Dict[str, Any]] = []
    for item in assessments:
        if not isinstance(item, dict):
            continue
        source_id = _text(item.get("source_id"), 128)
        source = source_map.get(source_id)
        if not source:
            continue
        notes = _text(item.get("notes"), 1800)
        statement = notes or _text(source.get("title"), 1200)
        if not statement:
            continue
        stance = _text(item.get("stance"), 24).lower()
        strength = _text(item.get("strength"), 24).lower()
        source_ids = [source_id]
        output.append({
            "id": _claim_id(synthesis_id, statement, source_ids),
            "statement": statement,
            "verdict": STANCE_TO_VERDICT.get(stance, "insufficient"),
            "confidence": strength if strength in CONFIDENCE else "uncertain",
            "rationale": notes,
            "source_ids": source_ids,
            "sources": [source],
            "origin": "legacy_assessment_fallback",
        })
        if len(output) >= MAX_EVIDENCE_CLAIMS:
            break
    return output


def list_evidence_claims(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_evidence_claims_disabled", 503)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")

    row = _latest_synthesis(user_id, mission_id)
    if not row:
        return {
            "evidence_claims": [],
            "next_offset": None,
            "synthesis_id": "",
            "evidence_hash": "",
            "experimental_claim_ledger_separate": True,
        }

    result = json.loads(row["result_json"])
    source_ids = json.loads(row["source_ids_json"])
    if not isinstance(source_ids, list):
        source_ids = []
    normalized_ids = [str(value) for value in source_ids[:48]]
    sources = _source_map(user_id, mission_id, normalized_ids)
    claims = _explicit_claims(result, row["synthesis_id"], sources)
    if not claims:
        claims = _fallback_claims(result, row["synthesis_id"], sources)

    page = claims[offset : offset + 20]
    return {
        "evidence_claims": page,
        "next_offset": offset + 20 if offset + 20 < len(claims) else None,
        "synthesis_id": row["synthesis_id"],
        "evidence_hash": row["evidence_hash"],
        "provider": row["provider"],
        "experimental_claim_ledger_separate": True,
    }
