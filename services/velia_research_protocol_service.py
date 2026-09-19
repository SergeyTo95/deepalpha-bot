"""Scientific protocol and dataset quality boundary for VELIA Research Center.

This layer preregisters confirmatory analyses before held-out test materialization.
It is deterministic and allowlisted: no shell, user code, files, URLs, or dynamic
expressions. Quality checks inspect immutable numeric snapshots and never modify
the registered dataset.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_dataset_service as datasets
from services import velia_research_claim_service as claims
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


PROTOCOL_VERSION = 1
ALLOWED_ANALYSES = {
    "descriptive_stats",
    "correlation",
    "linear_regression",
    "bootstrap_mean_ci",
    "bootstrap_mean_difference_ci",
}
ALLOWED_ROLES = {"feature", "outcome", "group", "covariate", "index", "unspecified"}
ALLOWED_OUTLIER_POLICIES = {"none", "report_only"}
ALLOWED_CORRECTIONS = {"none", "bonferroni", "holm"}
MAX_FAMILY_SIZE = 20
MAX_PROTOCOLS_PER_MISSION = 20


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        center.enabled()
        and datasets.enabled()
        and _env_bool("VELIA_RESEARCH_PROTOCOL_OFFICER_ENABLED", False)
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "protocol_version": PROTOCOL_VERSION,
        "preregistration_required_for_test_split": True,
        "test_rows_exposed_by_api": False,
        "missing_data_policy": "reject_missing",
        "outlier_policies": sorted(ALLOWED_OUTLIER_POLICIES),
        "multiple_comparison_corrections": sorted(ALLOWED_CORRECTIONS),
        "power_planning": True,
        "leakage_checks": True,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
        "dynamic_expression_eval": False,
    }


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _number(value: Any, low: float, high: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise projects.ProjectError(code)
    out = float(value)
    if not math.isfinite(out) or not low <= out <= high:
        raise projects.ProjectError(code)
    return out


def _integer(value: Any, low: int, high: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise projects.ProjectError(code)
    return int(value)


def _dictionary(value: Any, selected: List[str]) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(selected):
        raise projects.ProjectError("invalid_research_protocol_dictionary")
    result: Dict[str, Any] = {}
    for column in selected:
        item = value[column]
        if not isinstance(item, dict) or set(item) - {"unit", "role", "description"}:
            raise projects.ProjectError("invalid_research_protocol_dictionary")
        role = str(item.get("role") or "unspecified")
        if role not in ALLOWED_ROLES:
            raise projects.ProjectError("invalid_research_protocol_dictionary")
        unit = item.get("unit", "")
        description = item.get("description", "")
        if not isinstance(unit, str) or len(unit) > 80 or "\x00" in unit:
            raise projects.ProjectError("invalid_research_protocol_dictionary")
        if not isinstance(description, str) or len(description) > 400 or "\x00" in description:
            raise projects.ProjectError("invalid_research_protocol_dictionary")
        result[column] = {
            "unit": " ".join(unit.split()),
            "role": role,
            "description": " ".join(description.split()),
        }
    return result


def _power_plan(
    analysis_kind: str,
    alpha: float,
    power: float,
    effect_size: Optional[float],
    family_size: int,
    correction: str,
) -> Dict[str, Any]:
    effective_alpha = alpha
    if family_size > 1 and correction in {"bonferroni", "holm"}:
        effective_alpha = alpha / family_size
    z_alpha = NormalDist().inv_cdf(1.0 - effective_alpha / 2.0)
    z_power = NormalDist().inv_cdf(power)
    result: Dict[str, Any] = {
        "requested_alpha": alpha,
        "effective_planning_alpha": effective_alpha,
        "target_power": power,
        "family_size": family_size,
        "correction": correction,
    }
    if analysis_kind in {"correlation", "linear_regression"}:
        if effect_size is None or not 0.05 <= effect_size < 0.99:
            raise projects.ProjectError("invalid_research_protocol_effect_size")
        fisher = math.atanh(effect_size)
        required = math.ceil(3.0 + ((z_alpha + z_power) / fisher) ** 2)
        result.update({
            "effect_size_metric": "absolute_correlation",
            "minimum_effect_size": effect_size,
            "minimum_total_n": max(4, required),
            "method": "fisher_z_normal_approximation",
        })
    elif analysis_kind == "bootstrap_mean_difference_ci":
        if effect_size is None or not 0.1 <= effect_size <= 5.0:
            raise projects.ProjectError("invalid_research_protocol_effect_size")
        per_group = math.ceil(2.0 * ((z_alpha + z_power) / effect_size) ** 2)
        result.update({
            "effect_size_metric": "standardized_mean_difference",
            "minimum_effect_size": effect_size,
            "minimum_per_group_n": max(2, per_group),
            "minimum_total_n": max(4, 2 * per_group),
            "method": "two_sample_normal_approximation",
        })
    else:
        if effect_size is not None:
            raise projects.ProjectError("invalid_research_protocol_effect_size")
        result.update({
            "effect_size_metric": None,
            "minimum_effect_size": None,
            "minimum_total_n": None,
            "method": "not_applicable_for_descriptive_or_single_mean_summary",
        })
    return result


def _row_hash(row: List[float]) -> str:
    return hashlib.sha256(_json(row).encode("utf-8")).hexdigest()


def _iqr_outliers(values: List[float]) -> int:
    if len(values) < 4:
        return 0
    ordered = sorted(values)
    q = statistics.quantiles(ordered, n=4, method="inclusive")
    q1, q3 = q[0], q[2]
    iqr = q3 - q1
    if iqr <= 0.0:
        return 0
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return sum(1 for value in values if value < low or value > high)


def _quality_audit(
    dataset: Dict[str, Any],
    selected: List[str],
    dictionary: Dict[str, Any],
    outcome_column: str,
    outlier_policy: str,
) -> Dict[str, Any]:
    rows = dataset["rows"]
    columns = dataset["columns"]
    split = dataset["split"]
    train_indices = list(split["train_indices"])
    test_indices = list(split["test_indices"])
    positions = {name: columns.index(name) for name in selected}

    train_hashes = {_row_hash(rows[idx]) for idx in train_indices}
    test_hashes = {_row_hash(rows[idx]) for idx in test_indices}
    duplicate_overlap = len(train_hashes & test_hashes)

    train_outliers: Dict[str, int] = {}
    if outlier_policy == "report_only":
        for name, pos in positions.items():
            train_outliers[name] = _iqr_outliers([float(rows[idx][pos]) for idx in train_indices])

    role_map = {name: dictionary[name]["role"] for name in selected}
    leakage_findings: List[str] = []
    if duplicate_overlap:
        leakage_findings.append("identical_full_rows_cross_train_test_boundary")
    if outcome_column and role_map.get(outcome_column) != "outcome":
        leakage_findings.append("declared_outcome_role_mismatch")
    feature_columns = [name for name in selected if role_map.get(name) == "feature"]
    if outcome_column in feature_columns:
        leakage_findings.append("outcome_also_declared_feature")

    return {
        "passed": not leakage_findings,
        "missing_values": 0,
        "missing_policy": "reject_missing",
        "outlier_policy": outlier_policy,
        "train_outlier_counts_iqr": train_outliers,
        "train_count": len(train_indices),
        "test_count": len(test_indices),
        "train_test_duplicate_full_rows": duplicate_overlap,
        "leakage_findings": leakage_findings,
        "test_values_used_to_choose_protocol": False,
        "test_values_exposed": False,
    }


def ensure_tables() -> None:
    datasets.ensure_tables()
    if claims.enabled():
        claims.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_protocols (
            protocol_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            claim_id TEXT,
            claim_hash TEXT,
            dataset_id TEXT NOT NULL,
            dataset_hash TEXT NOT NULL,
            split_hash TEXT NOT NULL,
            analysis_kind TEXT NOT NULL,
            selected_columns_json TEXT NOT NULL,
            outcome_column TEXT NOT NULL,
            dictionary_json TEXT NOT NULL,
            alpha DOUBLE PRECISION NOT NULL,
            target_power DOUBLE PRECISION NOT NULL,
            effect_size DOUBLE PRECISION,
            family_size INTEGER NOT NULL,
            correction TEXT NOT NULL,
            outlier_policy TEXT NOT NULL,
            power_json TEXT NOT NULL,
            quality_json TEXT NOT NULL,
            protocol_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'locked',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            locked_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(mission_id,user_id,protocol_hash),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE,
            FOREIGN KEY(dataset_id)
                REFERENCES velia_research_datasets(dataset_id) ON DELETE CASCADE,
            FOREIGN KEY(hypothesis_id)
                REFERENCES velia_research_hypotheses(hypothesis_id) ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE velia_research_protocols ADD COLUMN IF NOT EXISTS claim_id TEXT")
        cur.execute("ALTER TABLE velia_research_protocols ADD COLUMN IF NOT EXISTS claim_hash TEXT")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_protocols_mission
            ON velia_research_protocols(mission_id,user_id,created_at DESC)""")


def _protocol_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["protocol_id"],
        "mission_id": row["mission_id"],
        "hypothesis_id": row["hypothesis_id"],
        "claim_id": row.get("claim_id"),
        "claim_hash": row.get("claim_hash"),
        "dataset_id": row["dataset_id"],
        "dataset_hash": row["dataset_hash"],
        "split_hash": row["split_hash"],
        "analysis_kind": row["analysis_kind"],
        "selected_columns": json.loads(row["selected_columns_json"]),
        "outcome_column": row["outcome_column"],
        "data_dictionary": json.loads(row["dictionary_json"]),
        "alpha": float(row["alpha"]),
        "target_power": float(row["target_power"]),
        "effect_size": None if row["effect_size"] is None else float(row["effect_size"]),
        "family_size": int(row["family_size"]),
        "correction": row["correction"],
        "outlier_policy": row["outlier_policy"],
        "power_plan": json.loads(row["power_json"]),
        "quality": json.loads(row["quality_json"]),
        "protocol_hash": row["protocol_hash"],
        "safety": json.loads(row["safety_json"]),
        "status": row["status"],
        "created_at": _iso(row["created_at"]),
        "locked_at": _iso(row["locked_at"]),
    }


def create_locked(user_id: int, mission_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_protocol_officer_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "hypothesis_id", "dataset_id", "analysis_kind", "columns", "outcome_column",
        "data_dictionary", "alpha", "target_power", "effect_size", "family_size",
        "correction", "outlier_policy", "claim_id",
    }:
        raise projects.ProjectError("invalid_research_protocol")

    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_protocol_read_only", 403)

    hypothesis_id = data.get("hypothesis_id")
    claim_id = data.get("claim_id")
    dataset_id = data.get("dataset_id")
    analysis_kind = str(data.get("analysis_kind") or "")
    selected = data.get("columns")
    outcome = str(data.get("outcome_column") or "")
    if not isinstance(hypothesis_id, str) or not hypothesis_id:
        raise projects.ProjectError("invalid_research_protocol")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise projects.ProjectError("invalid_research_protocol")
    if analysis_kind not in ALLOWED_ANALYSES:
        raise projects.ProjectError("invalid_research_protocol")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 2:
        raise projects.ProjectError("invalid_research_protocol")
    if len(set(selected)) != len(selected) or any(not isinstance(v, str) for v in selected):
        raise projects.ProjectError("invalid_research_protocol")
    if outcome not in selected:
        raise projects.ProjectError("invalid_research_protocol_outcome")

    with projects.transaction() as cur:
        cur.execute("""SELECT hypothesis_id,status,safety_json FROM velia_research_hypotheses
            WHERE hypothesis_id=%s AND mission_id=%s AND user_id=%s""",
            (hypothesis_id, str(mission_id), int(user_id)))
        hypothesis = cur.fetchone()
        if not hypothesis:
            raise projects.ProjectError("research_hypothesis_not_found", 404)
        if hypothesis["status"] == "blocked":
            raise projects.ProjectError("research_hypothesis_not_active", 409)
        if json.loads(hypothesis["safety_json"]).get("read_only_only"):
            raise projects.ProjectError("research_protocol_read_only", 403)
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_protocols
            WHERE mission_id=%s AND user_id=%s""", (str(mission_id), int(user_id)))
        if int(cur.fetchone()["n"]) >= MAX_PROTOCOLS_PER_MISSION:
            raise projects.ProjectError("research_protocol_limit_reached", 409)

    dataset_meta = datasets.get(user_id, dataset_id, include_rows=False)
    if dataset_meta["mission_id"] != str(mission_id):
        raise projects.ProjectError("research_dataset_not_found", 404)
    if any(column not in dataset_meta["columns"] for column in selected):
        raise projects.ProjectError("research_dataset_column_not_found", 404)

    dictionary = _dictionary(data.get("data_dictionary"), selected)
    alpha = _number(data.get("alpha", 0.05), 0.001, 0.2, "invalid_research_protocol_alpha")
    target_power = _number(
        data.get("target_power", 0.8), 0.5, 0.99, "invalid_research_protocol_power"
    )
    effect_raw = data.get("effect_size")
    effect_size = None if effect_raw is None else _number(
        effect_raw, 0.0001, 5.0, "invalid_research_protocol_effect_size"
    )
    family_size = _integer(
        data.get("family_size", 1), 1, MAX_FAMILY_SIZE, "invalid_research_protocol_family"
    )
    correction = str(data.get("correction") or "none")
    if correction not in ALLOWED_CORRECTIONS:
        raise projects.ProjectError("invalid_research_protocol_correction")
    if family_size > 1 and correction == "none":
        raise projects.ProjectError("research_protocol_correction_required")
    outlier_policy = str(data.get("outlier_policy") or "report_only")
    if outlier_policy not in ALLOWED_OUTLIER_POLICIES:
        raise projects.ProjectError("invalid_research_protocol_outlier_policy")

    power_plan = _power_plan(
        analysis_kind, alpha, target_power, effect_size, family_size, correction
    )

    claim_binding = None
    if claims.enabled():
        if not isinstance(claim_id, str) or not claim_id:
            raise projects.ProjectError("research_claim_required", 409)
        claim_binding = claims.authorize_protocol(
            user_id, claim_id, mission_id, hypothesis_id, analysis_kind
        )
    elif claim_id is not None:
        raise projects.ProjectError("research_claim_ledger_disabled", 503)

    # Protocol choices are fully validated and frozen before held-out rows are read.
    frozen = {
        "protocol_version": PROTOCOL_VERSION,
        "mission_id": str(mission_id),
        "hypothesis_id": hypothesis_id,
        "claim_binding": claim_binding,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_meta["dataset_hash"],
        "split_hash": dataset_meta["split_hash"],
        "analysis_kind": analysis_kind,
        "selected_columns": selected,
        "outcome_column": outcome,
        "data_dictionary": dictionary,
        "alpha": alpha,
        "target_power": target_power,
        "effect_size": effect_size,
        "family_size": family_size,
        "correction": correction,
        "outlier_policy": outlier_policy,
        "power_plan": power_plan,
    }
    dataset_internal = datasets.get(user_id, dataset_id, include_rows=True)
    quality = _quality_audit(
        dataset_internal, selected, dictionary, outcome, outlier_policy
    )
    if not quality["passed"]:
        raise projects.ProjectError("research_dataset_quality_failed", 409)
    frozen["quality"] = quality

    if power_plan.get("minimum_total_n") is not None:
        actual_n = int(dataset_meta["split"]["test_count"])
        if analysis_kind == "bootstrap_mean_difference_ci":
            actual_n = int(dataset_meta["split"]["test_count"]) * 2
        power_plan["available_confirmatory_n"] = actual_n
        power_plan["planned_sample_size_sufficient"] = actual_n >= int(
            power_plan["minimum_total_n"]
        )
    else:
        power_plan["available_confirmatory_n"] = int(dataset_meta["split"]["test_count"])
        power_plan["planned_sample_size_sufficient"] = None

    # The immutable hash covers the final frozen power plan, including available
    # held-out sample size and the deterministic sufficiency assessment.
    protocol_hash = _sha(frozen)
    decision = safety.classify(_json(frozen), phase="experiment")
    if decision["decision"] != "allowed" or decision.get("read_only_only"):
        raise projects.ProjectError("research_protocol_safety_blocked", 403)

    protocol_id = hashlib.sha256(
        (str(mission_id) + "|" + protocol_hash).encode("utf-8")
    ).hexdigest()
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_protocols(
            protocol_id,mission_id,user_id,hypothesis_id,claim_id,claim_hash,dataset_id,dataset_hash,split_hash,
            analysis_kind,selected_columns_json,outcome_column,dictionary_json,alpha,target_power,
            effect_size,family_size,correction,outlier_policy,power_json,quality_json,protocol_hash,
            safety_json,status)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'locked')
            ON CONFLICT(mission_id,user_id,protocol_hash) DO NOTHING""",
            (
                protocol_id, str(mission_id), int(user_id), hypothesis_id,
                claim_binding["claim_id"] if claim_binding else None,
                claim_binding["claim_hash"] if claim_binding else None,
                dataset_id, dataset_meta["dataset_hash"], dataset_meta["split_hash"], analysis_kind,
                _json(selected), outcome, _json(dictionary), alpha, target_power, effect_size,
                family_size, correction, outlier_policy, _json(power_plan), _json(quality),
                protocol_hash, _json(decision),
            ))
        cur.execute("""SELECT * FROM velia_research_protocols
            WHERE mission_id=%s AND user_id=%s AND protocol_hash=%s""",
            (str(mission_id), int(user_id), protocol_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_protocol_state_missing", 500)
        center._event(cur, str(mission_id), user_id, "research_protocol_locked", {
            "protocol_id": row["protocol_id"],
            "protocol_hash": row["protocol_hash"],
            "claim_id": row.get("claim_id"),
            "claim_hash": row.get("claim_hash"),
            "dataset_id": dataset_id,
            "dataset_hash": dataset_meta["dataset_hash"],
            "split_hash": dataset_meta["split_hash"],
            "analysis_kind": analysis_kind,
            "family_size": family_size,
            "correction": correction,
        })
        return _protocol_row(row)


def get(user_id: int, protocol_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_protocols
            WHERE protocol_id=%s AND user_id=%s""", (str(protocol_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_protocol_not_found", 404)
        return _protocol_row(row)


def list_protocols(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_protocols
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,protocol_id DESC LIMIT 51 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "protocols": [_protocol_row(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def authorize_test_plan(
    user_id: int,
    mission_id: str,
    hypothesis_id: str,
    dataset_snapshot: Dict[str, Any],
    analysis_kind: str,
    claim_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_protocol_officer_disabled", 503)
    if dataset_snapshot.get("split") != "test":
        raise projects.ProjectError("research_protocol_test_split_required")
    if claims.enabled() and (not isinstance(claim_id, str) or not claim_id):
        raise projects.ProjectError("research_claim_required", 409)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_protocols
            WHERE mission_id=%s AND user_id=%s AND hypothesis_id=%s
              AND dataset_id=%s AND dataset_hash=%s AND split_hash=%s
              AND analysis_kind=%s AND selected_columns_json=%s AND status='locked'
              AND (%s IS NULL OR claim_id=%s)
            ORDER BY locked_at DESC,protocol_id DESC LIMIT 1""",
            (
                str(mission_id), int(user_id), str(hypothesis_id),
                str(dataset_snapshot["dataset_id"]), str(dataset_snapshot["dataset_hash"]),
                str(dataset_snapshot["split_hash"]), str(analysis_kind),
                _json(dataset_snapshot["columns"]), claim_id, claim_id,
            ))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_preregistration_required", 409)
        protocol = _protocol_row(row)
    if not protocol["quality"].get("passed"):
        raise projects.ProjectError("research_dataset_quality_failed", 409)
    return {
        "protocol_id": protocol["id"],
        "protocol_hash": protocol["protocol_hash"],
        "claim_id": protocol.get("claim_id"),
        "claim_hash": protocol.get("claim_hash"),
        "dataset_id": protocol["dataset_id"],
        "dataset_hash": protocol["dataset_hash"],
        "split_hash": protocol["split_hash"],
        "analysis_kind": protocol["analysis_kind"],
        "selected_columns": protocol["selected_columns"],
        "alpha": protocol["alpha"],
        "family_size": protocol["family_size"],
        "correction": protocol["correction"],
        "power_plan": protocol["power_plan"],
        "quality_passed": True,
        "locked_at": protocol["locked_at"],
    }


def verify_authorization(user_id: int, expected: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(expected, dict) or "protocol_id" not in expected:
        raise projects.ProjectError("research_protocol_snapshot_invalid", 409)
    protocol = get(user_id, str(expected["protocol_id"]))
    current = {
        "protocol_id": protocol["id"],
        "protocol_hash": protocol["protocol_hash"],
        "claim_id": protocol.get("claim_id"),
        "claim_hash": protocol.get("claim_hash"),
        "dataset_id": protocol["dataset_id"],
        "dataset_hash": protocol["dataset_hash"],
        "split_hash": protocol["split_hash"],
        "analysis_kind": protocol["analysis_kind"],
        "selected_columns": protocol["selected_columns"],
        "alpha": protocol["alpha"],
        "family_size": protocol["family_size"],
        "correction": protocol["correction"],
        "power_plan": protocol["power_plan"],
        "quality_passed": bool(protocol["quality"].get("passed")),
        "locked_at": protocol["locked_at"],
    }
    if current != expected or protocol["status"] != "locked":
        raise projects.ProjectError("research_protocol_snapshot_mismatch", 409)
    return current


def correct_p_values(user_id: int, protocol_id: str, p_values: Any) -> Dict[str, Any]:
    protocol = get(user_id, protocol_id)
    if not isinstance(p_values, list) or not 1 <= len(p_values) <= protocol["family_size"]:
        raise projects.ProjectError("invalid_research_protocol_pvalues")
    values = [
        _number(value, 0.0, 1.0, "invalid_research_protocol_pvalues")
        for value in p_values
    ]
    method = protocol["correction"]
    m = protocol["family_size"]
    if method == "none":
        adjusted = list(values)
    elif method == "bonferroni":
        adjusted = [min(1.0, value * m) for value in values]
    else:
        order = sorted(range(len(values)), key=lambda idx: values[idx])
        adjusted = [0.0] * len(values)
        running = 0.0
        for rank, idx in enumerate(order):
            raw = min(1.0, values[idx] * (m - rank))
            running = max(running, raw)
            adjusted[idx] = running
    return {
        "protocol_id": protocol["id"],
        "protocol_hash": protocol["protocol_hash"],
        "method": method,
        "family_size": m,
        "alpha": protocol["alpha"],
        "raw_p_values": values,
        "adjusted_p_values": adjusted,
        "reject": [value <= protocol["alpha"] for value in adjusted],
    }
