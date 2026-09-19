"""Immutable scientific claim ledger and dataset-distinct replication network."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


LEDGER_VERSION = 1
ALLOWED_DIRECTIONS = {"positive", "negative", "nonzero"}
ALLOWED_ANALYSES = {
    "correlation",
    "linear_regression",
    "bootstrap_mean_ci",
    "bootstrap_mean_difference_ci",
}
STAGES = {"exploratory", "preregistered", "replicated", "contradicted"}
MAX_CLAIMS_PER_MISSION = 30
MAX_SOURCE_IDS = 12


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return center.enabled() and _env_bool("VELIA_RESEARCH_CLAIM_LEDGER_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "ledger_version": LEDGER_VERSION,
        "immutable_claim_definitions": True,
        "immutable_stage_snapshots": True,
        "replication_requires_distinct_dataset_hash": True,
        "replication_requires_distinct_provenance_fingerprint": True,
        "allowed_stages": sorted(STAGES),
        "claim_language_is_stage_bounded": True,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
    }


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int, code: str) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        raise projects.ProjectError(code)
    return value


def ensure_tables() -> None:
    center.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_claims (
            claim_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            expected_direction TEXT NOT NULL,
            analysis_kind TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            claim_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id,claim_hash),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(hypothesis_id)
                REFERENCES velia_research_hypotheses(hypothesis_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_claims_mission
            ON velia_research_claims(mission_id,user_id,created_at DESC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_claim_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            stage TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            wording TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(claim_id,user_id,evidence_hash),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_claim_snapshots
            ON velia_research_claim_snapshots(claim_id,user_id,created_at DESC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_replication_edges (
            edge_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            left_review_id TEXT NOT NULL,
            right_review_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            left_dataset_hash TEXT NOT NULL,
            right_dataset_hash TEXT NOT NULL,
            left_provenance_hash TEXT NOT NULL,
            right_provenance_hash TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(claim_id,user_id,left_review_id,right_review_id),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_claim_calibrations (
            calibration_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            source_type TEXT NOT NULL,
            source_snapshot_id TEXT NOT NULL,
            calibration_hash TEXT NOT NULL,
            calibration_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(claim_id,user_id,source_type,source_snapshot_id),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_claim_calibrations
            ON velia_research_claim_calibrations(claim_id,user_id,created_at DESC)""")


def _claim_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["claim_id"],
        "mission_id": row["mission_id"],
        "hypothesis_id": row["hypothesis_id"],
        "statement": row["statement"],
        "expected_direction": row["expected_direction"],
        "analysis_kind": row["analysis_kind"],
        "source_ids": json.loads(row["source_ids_json"]),
        "claim_hash": row["claim_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def _snapshot_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["snapshot_id"],
        "claim_id": row["claim_id"],
        "mission_id": row["mission_id"],
        "stage": row["stage"],
        "evidence_hash": row["evidence_hash"],
        "evidence": json.loads(row["evidence_json"]),
        "wording": row["wording"],
        "created_at": _iso(row["created_at"]),
    }


def _validate_sources(user_id: int, mission_id: str, source_ids: Any) -> List[str]:
    if source_ids is None:
        return []
    if (
        not isinstance(source_ids, list)
        or len(source_ids) > MAX_SOURCE_IDS
        or any(not isinstance(value, str) or not value or len(value) > 128 for value in source_ids)
    ):
        raise projects.ProjectError("invalid_research_claim_sources")
    values = list(dict.fromkeys(source_ids))
    if not values:
        return []
    with projects.transaction() as cur:
        cur.execute("""SELECT source_id FROM velia_research_sources
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        known = {row["source_id"] for row in cur.fetchall()}
    if any(value not in known for value in values):
        raise projects.ProjectError("research_claim_source_not_found", 404)
    return values


def create_claim(user_id: int, mission_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_claim_ledger_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "hypothesis_id", "statement", "expected_direction", "analysis_kind", "source_ids"
    }:
        raise projects.ProjectError("invalid_research_claim")

    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_claim_read_only", 403)

    hypothesis_id = data.get("hypothesis_id")
    if not isinstance(hypothesis_id, str) or not hypothesis_id or len(hypothesis_id) > 128:
        raise projects.ProjectError("invalid_research_claim")
    statement = _text(data.get("statement"), 800, "invalid_research_claim")
    direction = str(data.get("expected_direction") or "")
    analysis_kind = str(data.get("analysis_kind") or "")
    if direction not in ALLOWED_DIRECTIONS or analysis_kind not in ALLOWED_ANALYSES:
        raise projects.ProjectError("invalid_research_claim")
    source_ids = _validate_sources(user_id, mission_id, data.get("source_ids"))

    with projects.transaction() as cur:
        cur.execute("""SELECT status,safety_json FROM velia_research_hypotheses
            WHERE hypothesis_id=%s AND mission_id=%s AND user_id=%s""",
            (hypothesis_id, str(mission_id), int(user_id)))
        hypothesis = cur.fetchone()
        if not hypothesis:
            raise projects.ProjectError("research_hypothesis_not_found", 404)
        if hypothesis["status"] == "blocked" or json.loads(hypothesis["safety_json"]).get("read_only_only"):
            raise projects.ProjectError("research_claim_read_only", 403)
        cur.execute("""SELECT method_json FROM velia_research_experiments
            WHERE hypothesis_id=%s AND mission_id=%s AND user_id=%s""",
            (hypothesis_id, str(mission_id), int(user_id)))
        for row in cur.fetchall():
            method = json.loads(row["method_json"])
            snap = method.get("dataset_snapshot") if isinstance(method, dict) else None
            if isinstance(snap, dict) and snap.get("split") == "test":
                raise projects.ProjectError("research_claim_must_precede_confirmatory_test", 409)
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_claims
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        if int(cur.fetchone()["n"]) >= MAX_CLAIMS_PER_MISSION:
            raise projects.ProjectError("research_claim_limit_reached", 409)

    frozen = {
        "ledger_version": LEDGER_VERSION,
        "mission_id": str(mission_id),
        "hypothesis_id": hypothesis_id,
        "statement": statement,
        "expected_direction": direction,
        "analysis_kind": analysis_kind,
        "source_ids": source_ids,
    }
    decision = safety.classify(_json(frozen), phase="experiment")
    if decision["decision"] != "allowed" or decision.get("read_only_only"):
        raise projects.ProjectError("research_claim_safety_blocked", 403)
    claim_hash = _sha(frozen)
    claim_id = hashlib.sha256((str(mission_id) + "|" + claim_hash).encode()).hexdigest()

    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_claims(
            claim_id,mission_id,user_id,hypothesis_id,statement,expected_direction,
            analysis_kind,source_ids_json,claim_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(mission_id,user_id,claim_hash) DO NOTHING""",
            (
                claim_id, str(mission_id), int(user_id), hypothesis_id, statement, direction,
                analysis_kind, _json(source_ids), claim_hash, _json(decision),
            ))
        cur.execute("""SELECT * FROM velia_research_claims
            WHERE mission_id=%s AND user_id=%s AND claim_hash=%s""",
            (str(mission_id), int(user_id), claim_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_claim_state_missing", 500)
        center._event(cur, str(mission_id), user_id, "research_claim_registered", {
            "claim_id": row["claim_id"],
            "claim_hash": row["claim_hash"],
            "hypothesis_id": hypothesis_id,
            "analysis_kind": analysis_kind,
            "expected_direction": direction,
        })
        return _claim_row(row)


def get_claim(user_id: int, claim_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claims
            WHERE claim_id=%s AND user_id=%s""", (str(claim_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_claim_not_found", 404)
        return _claim_row(row)


def list_claims(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claims
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,claim_id DESC LIMIT 51 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "claims": [_claim_row(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def authorize_protocol(
    user_id: int,
    claim_id: str,
    mission_id: str,
    hypothesis_id: str,
    analysis_kind: str,
) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_claim_ledger_disabled", 503)
    claim = get_claim(user_id, claim_id)
    if (
        claim["mission_id"] != str(mission_id)
        or claim["hypothesis_id"] != str(hypothesis_id)
        or claim["analysis_kind"] != str(analysis_kind)
    ):
        raise projects.ProjectError("research_claim_protocol_mismatch", 409)
    return {
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "expected_direction": claim["expected_direction"],
        "analysis_kind": claim["analysis_kind"],
    }


def _signal(analysis_kind: str, expected_direction: str, metrics: Dict[str, Any],
            power_plan: Dict[str, Any]) -> str:
    if power_plan.get("planned_sample_size_sufficient") is False:
        return "underpowered"

    value: Optional[float] = None
    decisive = True
    if analysis_kind == "correlation":
        value = float(metrics.get("pearson_r") or 0.0)
        minimum = power_plan.get("minimum_effect_size")
        if minimum is not None and abs(value) < float(minimum):
            return "inconclusive"
    elif analysis_kind == "linear_regression":
        value = float(metrics.get("slope") or 0.0)
        minimum = power_plan.get("minimum_effect_size")
        strength = math.sqrt(max(0.0, float(metrics.get("r_squared") or 0.0)))
        if minimum is not None and strength < float(minimum):
            return "inconclusive"
    elif analysis_kind in {"bootstrap_mean_ci", "bootstrap_mean_difference_ci"}:
        low = float(metrics.get("ci_low") or 0.0)
        high = float(metrics.get("ci_high") or 0.0)
        if low > 0.0:
            value = 1.0
        elif high < 0.0:
            value = -1.0
        else:
            decisive = False
    else:
        return "inconclusive"

    if not decisive or value is None or value == 0.0:
        return "inconclusive"
    if expected_direction == "nonzero":
        return "supports"
    if expected_direction == "positive":
        return "supports" if value > 0.0 else "contradicts"
    return "supports" if value < 0.0 else "contradicts"


def _provenance_fingerprint(cur, dataset_id: str, user_id: int) -> Tuple[str, Dict[str, Any]]:
    cur.execute("""SELECT provenance_json,dataset_hash FROM velia_research_datasets
        WHERE dataset_id=%s AND user_id=%s""", (str(dataset_id), int(user_id)))
    row = cur.fetchone()
    if not row:
        return "", {}
    provenance = json.loads(row["provenance_json"])
    fingerprint = _sha({
        "kind": provenance.get("kind"),
        "source_label": provenance.get("source_label"),
        "collected_at": provenance.get("collected_at"),
    })
    return fingerprint, provenance


def _evidence(user_id: int, claim: Dict[str, Any]) -> List[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("SELECT to_regclass('velia_research_experiment_reviews') AS table_name")
        table = cur.fetchone()
        if not table or not table.get("table_name"):
            return []
        cur.execute("""SELECT r.review_id,r.result_hash,r.statistician_json,r.replication_json,
                   e.experiment_id,e.method_json,e.result_json
            FROM velia_research_experiment_reviews r
            JOIN velia_research_experiments e
              ON e.experiment_id=r.experiment_id AND e.user_id=r.user_id
            WHERE e.mission_id=%s AND e.user_id=%s AND e.hypothesis_id=%s
            ORDER BY r.created_at ASC,r.review_id ASC""",
            (claim["mission_id"], int(user_id), claim["hypothesis_id"]))
        rows = list(cur.fetchall())
        evidence: List[Dict[str, Any]] = []
        for row in rows:
            method = json.loads(row["method_json"])
            if method.get("analysis_kind") != claim["analysis_kind"]:
                continue
            statistician = json.loads(row["statistician_json"])
            mode = method.get("analysis_mode") or "exploratory"
            protocol_snapshot = method.get("protocol_snapshot")
            dataset_snapshot = method.get("dataset_snapshot")
            item: Dict[str, Any] = {
                "review_id": row["review_id"],
                "experiment_id": row["experiment_id"],
                "result_hash": row["result_hash"],
                "analysis_mode": mode,
                "signal": "exploratory",
                "dataset_id": None,
                "dataset_hash": None,
                "split_hash": None,
                "provenance_fingerprint": None,
                "protocol_id": None,
                "protocol_hash": None,
                "replication_verified": bool(json.loads(row["replication_json"]).get("match")),
            }
            if isinstance(dataset_snapshot, dict):
                item.update({
                    "dataset_id": dataset_snapshot.get("dataset_id"),
                    "dataset_hash": dataset_snapshot.get("dataset_hash"),
                    "split_hash": dataset_snapshot.get("split_hash"),
                })
                fingerprint, provenance = _provenance_fingerprint(
                    cur, str(dataset_snapshot.get("dataset_id") or ""), user_id
                )
                item["provenance_fingerprint"] = fingerprint or None
                item["provenance_kind"] = provenance.get("kind")
                item["provenance_source_label"] = provenance.get("source_label")
            if mode == "confirmatory" and isinstance(protocol_snapshot, dict):
                if protocol_snapshot.get("claim_id") != claim["id"]:
                    continue
                power_plan = protocol_snapshot.get("power_plan") or {}
                item.update({
                    "protocol_id": protocol_snapshot.get("protocol_id"),
                    "protocol_hash": protocol_snapshot.get("protocol_hash"),
                    "signal": _signal(
                        claim["analysis_kind"],
                        claim["expected_direction"],
                        statistician.get("metrics") or {},
                        power_plan,
                    ),
                })
            evidence.append(item)
        return evidence


def _stage_and_wording(claim: Dict[str, Any], evidence: List[Dict[str, Any]]) -> Tuple[str, str]:
    confirmatory = [item for item in evidence if item["analysis_mode"] == "confirmatory"]
    supports = [item for item in confirmatory if item["signal"] == "supports"]
    contradicts = [item for item in confirmatory if item["signal"] == "contradicts"]

    if contradicts:
        return (
            "contradicted",
            "Preregistered evidence contradicts the claim; no affirmative conclusion is permitted: "
            + claim["statement"],
        )

    independent_support = {
        (item.get("dataset_hash"), item.get("provenance_fingerprint"))
        for item in supports
        if item.get("dataset_hash") and item.get("provenance_fingerprint")
    }
    dataset_hashes = {pair[0] for pair in independent_support}
    provenance_hashes = {pair[1] for pair in independent_support}
    if len(dataset_hashes) >= 2 and len(provenance_hashes) >= 2:
        return (
            "replicated",
            "Dataset-distinct preregistered replication supports the claim: " + claim["statement"],
        )
    if confirmatory:
        if supports:
            wording = "One preregistered held-out analysis supports the claim: "
        else:
            wording = "Preregistered held-out analysis is inconclusive for the claim: "
        return "preregistered", wording + claim["statement"]
    if evidence:
        wording = "Only exploratory evidence is available; the claim is not confirmed: "
    else:
        wording = "No completed confirmatory evidence is available; the claim is not confirmed: "
    return ("exploratory", wording + claim["statement"])


def _upsert_edges(user_id: int, claim: Dict[str, Any], evidence: List[Dict[str, Any]]) -> None:
    confirmatory = [
        item for item in evidence
        if item["analysis_mode"] == "confirmatory"
        and item.get("dataset_hash")
        and item.get("provenance_fingerprint")
        and item["signal"] in {"supports", "contradicts"}
    ]
    with projects.transaction(user_id) as cur:
        for i, left in enumerate(confirmatory):
            for right in confirmatory[i + 1:]:
                if (
                    left["dataset_hash"] == right["dataset_hash"]
                    or left["provenance_fingerprint"] == right["provenance_fingerprint"]
                ):
                    continue
                relation = (
                    "dataset_distinct_replication"
                    if left["signal"] == right["signal"]
                    else "dataset_distinct_conflict"
                )
                ordered = sorted([left, right], key=lambda item: item["review_id"])
                first, second = ordered[0], ordered[1]
                a, b = first["review_id"], second["review_id"]
                edge_id = _sha({
                    "claim_id": claim["id"],
                    "left_review_id": a,
                    "right_review_id": b,
                    "relation": relation,
                })
                cur.execute("""INSERT INTO velia_research_replication_edges(
                    edge_id,claim_id,mission_id,user_id,left_review_id,right_review_id,
                    relation,left_dataset_hash,right_dataset_hash,left_provenance_hash,
                    right_provenance_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(claim_id,user_id,left_review_id,right_review_id) DO NOTHING""",
                    (
                        edge_id, claim["id"], claim["mission_id"], int(user_id), a, b, relation,
                        first["dataset_hash"], second["dataset_hash"],
                        first["provenance_fingerprint"], second["provenance_fingerprint"],
                    ))


def record_calibration(
    user_id: int,
    claim_id: str,
    source_type: str,
    source_snapshot_id: str,
    calibration: Dict[str, Any],
) -> Dict[str, Any]:
    claim = get_claim(user_id, claim_id)
    if source_type not in {"meta_analysis", "living_reassessment"}:
        raise projects.ProjectError("invalid_research_claim_calibration")
    if (
        not isinstance(source_snapshot_id, str)
        or not source_snapshot_id
        or len(source_snapshot_id) > 128
        or not isinstance(calibration, dict)
    ):
        raise projects.ProjectError("invalid_research_claim_calibration")
    action = calibration.get("claim_action")
    if action not in {"no_change", "caution", "contradict"}:
        raise projects.ProjectError("invalid_research_claim_calibration")
    calibration_hash = _sha({
        "claim_id": claim["id"],
        "source_type": source_type,
        "source_snapshot_id": source_snapshot_id,
        "calibration": calibration,
    })
    calibration_id = _sha({
        "claim_id": claim["id"],
        "source_snapshot_id": source_snapshot_id,
        "calibration_hash": calibration_hash,
    })
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_claim_calibrations(
            calibration_id,claim_id,mission_id,user_id,source_type,source_snapshot_id,
            calibration_hash,calibration_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(claim_id,user_id,source_type,source_snapshot_id) DO NOTHING""",
            (
                calibration_id, claim["id"], claim["mission_id"], int(user_id),
                source_type, source_snapshot_id, calibration_hash, _json(calibration),
            ))
        cur.execute("""SELECT * FROM velia_research_claim_calibrations
            WHERE claim_id=%s AND user_id=%s AND source_type=%s AND source_snapshot_id=%s""",
            (claim["id"], int(user_id), source_type, source_snapshot_id))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_claim_calibration_state_missing", 500)
        return {
            "id": row["calibration_id"],
            "claim_id": row["claim_id"],
            "source_type": row["source_type"],
            "source_snapshot_id": row["source_snapshot_id"],
            "calibration_hash": row["calibration_hash"],
            "calibration": json.loads(row["calibration_json"]),
            "created_at": _iso(row["created_at"]),
        }


def _latest_calibration(user_id: int, claim_id: str) -> Optional[Dict[str, Any]]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claim_calibrations
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at DESC,calibration_id DESC LIMIT 1""",
            (str(claim_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row["calibration_id"],
        "source_type": row["source_type"],
        "source_snapshot_id": row["source_snapshot_id"],
        "calibration_hash": row["calibration_hash"],
        "calibration": json.loads(row["calibration_json"]),
        "created_at": _iso(row["created_at"]),
    }


def refresh_claim(user_id: int, claim_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_claim_ledger_disabled", 503)
    claim = get_claim(user_id, claim_id)
    evidence = _evidence(user_id, claim)
    stage, wording = _stage_and_wording(claim, evidence)
    _upsert_edges(user_id, claim, evidence)
    calibration = _latest_calibration(user_id, claim["id"])
    if calibration is not None:
        action = calibration["calibration"].get("claim_action")
        reasons = calibration["calibration"].get("reasons") or []
        reason_text = "; ".join(str(value) for value in reasons[:6] if value)
        source_label = (
            "Living reassessment"
            if calibration["source_type"] == "living_reassessment"
            else "Meta-analysis"
        )
        if action == "contradict":
            stage = "contradicted"
            wording = (
                source_label
                + " contradicts the claim; no affirmative conclusion is permitted: "
                + claim["statement"]
            )
        elif action == "caution":
            wording += (
                " " + source_label + " calibration requires caution"
                + (": " + reason_text if reason_text else ".")
            )
        else:
            wording += (
                " External evidence calibration does not promote the Claim Ledger stage; "
                "promotion still requires preregistered dataset-distinct replication."
            )

    evidence_snapshot = {
        "ledger_version": LEDGER_VERSION,
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "stage": stage,
        "source_ids": claim["source_ids"],
        "experiment_evidence": evidence,
        "evidence_calibration": calibration,
        "wording_policy": {
            "exploratory": "not_confirmed",
            "preregistered": "single_preregistered_or_inconclusive",
            "replicated": "dataset_and_provenance_distinct_support",
            "contradicted": "affirmative_claim_prohibited",
            "meta_analysis_policy": "may_caution_or_contradict_but_never_promote_stage",
            "living_reassessment_policy": "may_caution_or_contradict_but_never_promote_stage",
        },
    }
    evidence_hash = _sha(evidence_snapshot)
    snapshot_id = _sha({
        "claim_id": claim["id"],
        "evidence_hash": evidence_hash,
    })
    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s AND evidence_hash=%s""",
            (claim["id"], int(user_id), evidence_hash))
        existing = cur.fetchone()
        if existing:
            return _snapshot_row(existing)
        cur.execute("""INSERT INTO velia_research_claim_snapshots(
            snapshot_id,claim_id,mission_id,user_id,stage,evidence_hash,evidence_json,wording)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(claim_id,user_id,evidence_hash) DO NOTHING""",
            (
                snapshot_id, claim["id"], claim["mission_id"], int(user_id), stage,
                evidence_hash, _json(evidence_snapshot), wording,
            ))
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s AND evidence_hash=%s""",
            (claim["id"], int(user_id), evidence_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_claim_snapshot_state_missing", 500)
        center._event(cur, claim["mission_id"], user_id, "research_claim_evaluated", {
            "claim_id": claim["id"],
            "snapshot_id": row["snapshot_id"],
            "stage": stage,
            "evidence_hash": evidence_hash,
        })
        return _snapshot_row(row)


def latest_snapshot(user_id: int, claim_id: str) -> Dict[str, Any]:
    get_claim(user_id, claim_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_claim_snapshots
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at DESC,snapshot_id DESC LIMIT 1""",
            (str(claim_id), int(user_id)))
        row = cur.fetchone()
    if row:
        return _snapshot_row(row)
    return refresh_claim(user_id, claim_id)


def mission_ledger(user_id: int, mission_id: str) -> Dict[str, Any]:
    claims = list_claims(user_id, mission_id)["claims"]
    snapshots = [refresh_claim(user_id, claim["id"]) for claim in claims]
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_replication_edges
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at ASC,edge_id ASC LIMIT 200""",
            (str(mission_id), int(user_id)))
        edges = [{
            "id": row["edge_id"],
            "claim_id": row["claim_id"],
            "left_review_id": row["left_review_id"],
            "right_review_id": row["right_review_id"],
            "relation": row["relation"],
            "left_dataset_hash": row["left_dataset_hash"],
            "right_dataset_hash": row["right_dataset_hash"],
            "left_provenance_hash": row["left_provenance_hash"],
            "right_provenance_hash": row["right_provenance_hash"],
            "created_at": _iso(row["created_at"]),
        } for row in cur.fetchall()]
    return {
        "claims": claims,
        "snapshots": snapshots,
        "replication_edges": edges,
        "stage_counts": {
            stage: sum(1 for item in snapshots if item["stage"] == stage)
            for stage in sorted(STAGES)
        },
    }
