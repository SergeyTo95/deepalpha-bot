"""Bounded experiment planning, review and replication for VELIA Research Center.

The pipeline is intentionally narrow: it only accepts structured numerical
plans that map to the existing Safe Compute allowlist. It never invents data,
executes user code, opens files, fetches URLs, or invokes a shell.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_compute_service as compute
from services import velia_research_dataset_service as datasets
from services import velia_research_protocol_service as protocol
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


PIPELINE_VERSION = 1
MAX_READY_PER_RUN = 2
ANALYSIS_KIND_TO_OPERATION = {
    "descriptive_stats": "descriptive_stats",
    "correlation": "pearson_correlation",
    "linear_regression": "linear_regression",
    "bootstrap_mean_ci": "bootstrap_mean_ci",
    "bootstrap_mean_difference_ci": "bootstrap_mean_difference_ci",
    "monte_carlo_sum": "monte_carlo_sum",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return compute.enabled() and _env_bool("VELIA_RESEARCH_EXPERIMENT_PIPELINE_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "pipeline_version": PIPELINE_VERSION,
        "planner": "deterministic_allowlist",
        "statistician": "deterministic",
        "skeptic": "deterministic",
        "replication_agent": "hash_verified_recompute",
        "dataset_registry_enabled": datasets.enabled(),
        "dataset_backed_plans": True,
        "protocol_officer_enabled": protocol.enabled(),
        "preregistration_required_for_test_split": protocol.enabled(),
        "allowed_analysis_kinds": sorted(ANALYSIS_KIND_TO_OPERATION),
        "max_ready_per_run": MAX_READY_PER_RUN,
        "invented_numeric_data_allowed": False,
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int, code: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    return re.sub(r"\s+", " ", value).strip()


def ensure_tables() -> None:
    compute.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_experiment_reviews (
            review_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            compute_run_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            operation TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            statistician_json TEXT NOT NULL,
            skeptic_json TEXT NOT NULL,
            replication_json TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(experiment_id,user_id),
            UNIQUE(compute_run_id,user_id),
            FOREIGN KEY(experiment_id) REFERENCES velia_research_experiments(experiment_id) ON DELETE CASCADE,
            FOREIGN KEY(compute_run_id) REFERENCES velia_research_compute_runs(run_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_experiment_reviews
            ON velia_research_experiment_reviews(mission_id,user_id,created_at DESC)""")


def _review_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["review_id"],
        "experiment_id": row["experiment_id"],
        "compute_run_id": row["compute_run_id"],
        "mission_id": row["mission_id"],
        "operation": row["operation"],
        "result_hash": row["result_hash"],
        "statistician": json.loads(row["statistician_json"]),
        "skeptic": json.loads(row["skeptic_json"]),
        "replication": json.loads(row["replication_json"]),
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def _experiment_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["experiment_id"],
        "hypothesis_id": row["hypothesis_id"],
        "mission_id": row["mission_id"],
        "status": row["status"],
        "method": json.loads(row["method_json"]),
        "safety": json.loads(row["safety_json"]),
        "result": json.loads(row["result_json"]) if row.get("result_json") else None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _load_hypothesis(user_id: int, mission_id: str, hypothesis_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_hypotheses
            WHERE hypothesis_id=%s AND mission_id=%s AND user_id=%s""",
            (str(hypothesis_id), str(mission_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_hypothesis_not_found", 404)
        if row["status"] == "blocked":
            raise projects.ProjectError("research_hypothesis_not_active", 409)
        return dict(row)


def _dataset_request(
    user_id: int,
    operation: str,
    dataset_snapshot: Dict[str, Any],
    analysis_options: Dict[str, Any],
    seed: Any,
) -> Dict[str, Any]:
    values = datasets.materialize(user_id, dataset_snapshot)
    columns = list(dataset_snapshot["columns"])
    if operation in {"descriptive_stats", "bootstrap_mean_ci"}:
        if len(columns) != 1:
            raise projects.ProjectError("invalid_research_dataset_columns")
        parameters: Dict[str, Any] = {"values": values[columns[0]]}
    elif operation in {"pearson_correlation", "linear_regression"}:
        if len(columns) != 2:
            raise projects.ProjectError("invalid_research_dataset_columns")
        parameters = {"x": values[columns[0]], "y": values[columns[1]]}
    elif operation == "bootstrap_mean_difference_ci":
        if len(columns) != 2:
            raise projects.ProjectError("invalid_research_dataset_columns")
        parameters = {"a": values[columns[0]], "b": values[columns[1]]}
    else:
        raise projects.ProjectError("research_dataset_analysis_not_supported")

    if operation in {"bootstrap_mean_ci", "bootstrap_mean_difference_ci"}:
        if not isinstance(analysis_options, dict) or set(analysis_options) - {"resamples", "confidence"}:
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        parameters.update(analysis_options)
    elif analysis_options:
        raise projects.ProjectError("invalid_experiment_pipeline_plan")

    request = {"operation": operation, "parameters": parameters, "seed": seed}
    validated_operation, validated_parameters, validated_seed = compute._validate_request(request)
    return {
        "operation": validated_operation,
        "parameters": validated_parameters,
        "seed": validated_seed,
    }


def plan(user_id: int, mission_id: str, data: Any) -> Dict[str, Any]:
    """Create one executable allowlisted experiment from explicit data or an immutable dataset snapshot."""
    if not enabled():
        raise projects.ProjectError("research_experiment_pipeline_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "hypothesis_id", "analysis_kind", "parameters", "seed", "question",
        "dataset_id", "split", "columns", "analysis_options",
    }:
        raise projects.ProjectError("invalid_experiment_pipeline_plan")

    hypothesis_id = data.get("hypothesis_id")
    analysis_kind = data.get("analysis_kind")
    parameters = data.get("parameters")
    seed = data.get("seed")
    question = _text(data.get("question"), 600, "invalid_experiment_pipeline_plan")
    dataset_id = data.get("dataset_id")
    split = data.get("split")
    selected_columns = data.get("columns")
    analysis_options = data.get("analysis_options") or {}

    if not isinstance(hypothesis_id, str) or not hypothesis_id or len(hypothesis_id) > 128:
        raise projects.ProjectError("invalid_experiment_pipeline_plan")
    operation = ANALYSIS_KIND_TO_OPERATION.get(str(analysis_kind))
    if not operation:
        raise projects.ProjectError("invalid_experiment_pipeline_plan")

    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_compute_read_only", 403)
    hypothesis = _load_hypothesis(user_id, mission_id, hypothesis_id)
    hypothesis_safety = json.loads(hypothesis["safety_json"])
    if hypothesis_safety.get("read_only_only"):
        raise projects.ProjectError("research_compute_read_only", 403)

    dataset_mode = dataset_id is not None
    if dataset_mode:
        if parameters is not None or not datasets.enabled():
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        if operation == "monte_carlo_sum":
            raise projects.ProjectError("research_dataset_analysis_not_supported")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        if split not in datasets.ALLOWED_SPLITS or not isinstance(selected_columns, list):
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        dataset_snapshot = datasets.snapshot(user_id, dataset_id, split, selected_columns)
        if dataset_snapshot["mission_id"] != str(mission_id):
            raise projects.ProjectError("research_dataset_not_found", 404)
        protocol_snapshot = None
        analysis_mode = "exploratory"
        if split == "test" and protocol.enabled():
            protocol_snapshot = protocol.authorize_test_plan(
                user_id,
                mission_id,
                hypothesis_id,
                dataset_snapshot,
                str(analysis_kind),
            )
            analysis_mode = "confirmatory"
        validated = _dataset_request(
            user_id, operation, dataset_snapshot, analysis_options, seed
        )
        method = {
            "type": "safe_compute",
            "pipeline_version": PIPELINE_VERSION,
            "analysis_kind": str(analysis_kind),
            "operation": validated["operation"],
            "seed": validated["seed"],
            "question": question,
            "data_origin": "dataset_registry",
            "dataset_snapshot": dataset_snapshot,
            "analysis_options": analysis_options,
            "analysis_mode": analysis_mode,
            "protocol_snapshot": protocol_snapshot,
            "invented_numeric_data": False,
        }
    else:
        if not isinstance(parameters, dict):
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        if split is not None or selected_columns is not None or analysis_options:
            raise projects.ProjectError("invalid_experiment_pipeline_plan")
        compute_request = {"operation": operation, "parameters": parameters, "seed": seed}
        validated_operation, validated_parameters, validated_seed = compute._validate_request(compute_request)
        method = {
            "type": "safe_compute",
            "pipeline_version": PIPELINE_VERSION,
            "analysis_kind": str(analysis_kind),
            "operation": validated_operation,
            "parameters": validated_parameters,
            "seed": validated_seed,
            "question": question,
            "data_origin": "explicit_structured_input",
            "invented_numeric_data": False,
        }

    plan_safety = safety.classify(_json({
        "hypothesis": {
            "title": hypothesis["title"],
            "rationale": hypothesis["rationale"],
        },
        "method": method,
    }), phase="experiment")
    if plan_safety["decision"] != "allowed" or plan_safety.get("read_only_only"):
        raise projects.ProjectError("research_compute_safety_blocked", 403)

    encoded = _json(method)
    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_experiments
            WHERE mission_id=%s AND user_id=%s AND hypothesis_id=%s AND method_json=%s
            ORDER BY created_at DESC LIMIT 1""",
            (str(mission_id), int(user_id), str(hypothesis_id), encoded))
        existing = cur.fetchone()
        if existing:
            return _experiment_row(existing)

    planned = center.plan_experiment(user_id, mission_id, method, hypothesis_id)
    return {
        "id": planned["id"],
        "hypothesis_id": hypothesis_id,
        "mission_id": str(mission_id),
        "status": planned["status"],
        "method": planned["method"],
        "safety": planned["safety"],
        "result": None,
        "created_at": None,
        "updated_at": None,
    }


def _statistician(run: Dict[str, Any]) -> Dict[str, Any]:
    operation = run["operation"]
    output = run["result"]["output"]
    notes: List[str] = []
    metrics: Dict[str, Any] = {}

    if operation == "descriptive_stats":
        metrics = {key: output.get(key) for key in ("n", "mean", "sd", "se", "median", "min", "max")}
        notes.append("Descriptive statistics summarize this dataset and do not establish causality.")
        if int(output.get("n") or 0) < 10:
            notes.append("Sample size is small; estimates may be unstable.")
    elif operation == "pearson_correlation":
        r = float(output.get("pearson_r") or 0.0)
        metrics = {"n": output.get("n"), "pearson_r": r, "absolute_r": abs(r)}
        notes.append("Correlation does not establish causation.")
        if int(output.get("n") or 0) < 20:
            notes.append("Correlation is based on a small sample.")
    elif operation == "linear_regression":
        metrics = {key: output.get(key) for key in ("n", "slope", "intercept", "r_squared", "rmse")}
        notes.append("Linear regression is descriptive unless its assumptions and study design justify stronger inference.")
        notes.append("Do not extrapolate beyond the observed input range without separate validation.")
    elif operation == "bootstrap_mean_ci":
        metrics = {key: output.get(key) for key in ("n", "mean", "confidence", "ci_low", "ci_high", "resamples")}
        metrics["ci_width"] = float(output["ci_high"]) - float(output["ci_low"])
        notes.append("Percentile bootstrap uncertainty is conditional on the observed sample.")
    elif operation == "bootstrap_mean_difference_ci":
        low, high = float(output["ci_low"]), float(output["ci_high"])
        metrics = {
            key: output.get(key) for key in (
                "n_a", "n_b", "mean_a", "mean_b", "mean_difference_a_minus_b",
                "confidence", "ci_low", "ci_high", "resamples"
            )
        }
        metrics["ci_includes_zero"] = low <= 0.0 <= high
        metrics["ci_width"] = high - low
        notes.append("A bootstrap interval that excludes zero is not by itself proof of a causal effect.")
    elif operation == "monte_carlo_sum":
        metrics = {key: output.get(key) for key in (
            "trials", "variables", "mean", "sd", "p05", "p50", "p95",
            "threshold", "probability_gte_threshold"
        ) if key in output}
        notes.append("Monte Carlo output reflects the supplied model assumptions, not empirical truth.")
        notes.append("The current model assumes independent allowlisted input distributions.")

    return {
        "role": "statistician",
        "operation": operation,
        "metrics": metrics,
        "notes": notes,
        "claims_boundary": "numeric_summary_only",
    }


def _skeptic(run: Dict[str, Any], statistician: Dict[str, Any]) -> Dict[str, Any]:
    operation = run["operation"]
    output = run["result"]["output"]
    limitations: List[str] = [
        "The computation cannot repair biased, unrepresentative, or incorrectly measured input data.",
        "The result should be interpreted together with study design and source provenance.",
    ]
    checks: Dict[str, Any] = {
        "user_code_executed": bool(run["result"]["reproducibility"].get("user_code_executed")),
        "deterministic_given_input_and_seed": bool(
            run["result"]["reproducibility"].get("deterministic_given_input_and_seed")
        ),
    }
    if operation in {"descriptive_stats", "pearson_correlation", "linear_regression"}:
        n = int(output.get("n") or 0)
        checks["small_sample"] = n < 20
        if n < 20:
            limitations.append("Small sample size limits stability and generalizability.")
    elif operation == "bootstrap_mean_ci":
        n = int(output.get("n") or 0)
        checks["small_sample"] = n < 20
        if n < 20:
            limitations.append("Bootstrap resampling from a small sample may underrepresent uncertainty.")
    elif operation == "bootstrap_mean_difference_ci":
        small = min(int(output.get("n_a") or 0), int(output.get("n_b") or 0)) < 20
        checks["small_group"] = small
        if small:
            limitations.append("At least one comparison group is small.")
    elif operation == "monte_carlo_sum":
        limitations.append("Distribution choice and independence assumptions can dominate Monte Carlo conclusions.")
        checks["simulation_only"] = True

    return {
        "role": "skeptic",
        "operation": operation,
        "checks": checks,
        "limitations": limitations,
        "statistician_metrics_hash": _hash(statistician.get("metrics", {})),
        "claim": "computational_result_is_supporting_evidence_not_standalone_scientific_proof",
    }


def _request_from_method(user_id: int, method: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(method, dict) or method.get("type") != "safe_compute":
        raise projects.ProjectError("research_experiment_not_compute_ready", 409)
    if method.get("pipeline_version") != PIPELINE_VERSION:
        raise projects.ProjectError("research_experiment_not_compute_ready", 409)
    if method.get("invented_numeric_data") is not False:
        raise projects.ProjectError("research_experiment_not_compute_ready", 409)

    operation = ANALYSIS_KIND_TO_OPERATION.get(str(method.get("analysis_kind")))
    if operation != method.get("operation"):
        raise projects.ProjectError("research_experiment_not_compute_ready", 409)

    origin = method.get("data_origin")
    if origin == "explicit_structured_input":
        allowed_keys = {
            "type", "pipeline_version", "analysis_kind", "operation", "parameters",
            "seed", "question", "data_origin", "invented_numeric_data",
        }
        if set(method) - allowed_keys:
            raise projects.ProjectError("research_experiment_not_compute_ready", 409)
        request = {
            "operation": operation,
            "parameters": method.get("parameters"),
            "seed": method.get("seed"),
        }
        compute._validate_request(request)
        return request

    if origin == "dataset_registry":
        allowed_keys = {
            "type", "pipeline_version", "analysis_kind", "operation", "seed",
            "question", "data_origin", "dataset_snapshot", "analysis_options",
            "analysis_mode", "protocol_snapshot", "invented_numeric_data",
        }
        if set(method) - allowed_keys:
            raise projects.ProjectError("research_experiment_not_compute_ready", 409)
        snapshot = method.get("dataset_snapshot")
        if not isinstance(snapshot, dict):
            raise projects.ProjectError("research_experiment_not_compute_ready", 409)
        datasets.verify_snapshot(user_id, snapshot)
        analysis_mode = method.get("analysis_mode")
        protocol_snapshot = method.get("protocol_snapshot")
        if snapshot.get("split") == "test" and protocol.enabled():
            if analysis_mode != "confirmatory" or not isinstance(protocol_snapshot, dict):
                raise projects.ProjectError("research_preregistration_required", 409)
            verified_protocol = protocol.verify_authorization(user_id, protocol_snapshot)
            if (
                verified_protocol["dataset_id"] != snapshot["dataset_id"]
                or verified_protocol["dataset_hash"] != snapshot["dataset_hash"]
                or verified_protocol["split_hash"] != snapshot["split_hash"]
                or verified_protocol["analysis_kind"] != method.get("analysis_kind")
                or verified_protocol["selected_columns"] != snapshot["columns"]
            ):
                raise projects.ProjectError("research_protocol_snapshot_mismatch", 409)
        elif snapshot.get("split") == "train":
            if analysis_mode != "exploratory" or protocol_snapshot is not None:
                raise projects.ProjectError("research_experiment_not_compute_ready", 409)
        return _dataset_request(
            user_id,
            operation,
            snapshot,
            method.get("analysis_options") or {},
            method.get("seed"),
        )

    raise projects.ProjectError("research_experiment_not_compute_ready", 409)


def _execute_one(user_id: int, experiment: Dict[str, Any]) -> Dict[str, Any]:
    method = experiment["method"]
    request = _request_from_method(user_id, method)
    request_id = "pipeline-" + hashlib.sha256(
        (str(experiment["id"]) + "|" + _json(request)).encode("utf-8")
    ).hexdigest()[:48]
    run = compute.execute(user_id, experiment["id"], request, request_id)
    replication = compute.verify_reproducibility(user_id, run["id"])
    if not replication["match"]:
        raise projects.ProjectError("research_compute_replication_mismatch", 500)

    statistician = _statistician(run)
    skeptic = _skeptic(run, statistician)
    data_provenance = (
        {
            **(method.get("dataset_snapshot") or {}),
            "analysis_mode": method.get("analysis_mode"),
            "protocol_snapshot": method.get("protocol_snapshot"),
        }
        if method.get("data_origin") == "dataset_registry"
        else {"data_origin": "explicit_structured_input"}
    )
    review_safety = safety.classify(_json({
        "statistician": statistician,
        "skeptic": skeptic,
        "replication": replication,
        "data_provenance": data_provenance,
    }), phase="final_output")
    if review_safety["decision"] == "blocked":
        raise projects.ProjectError("research_compute_safety_blocked", 403)

    review_id = hashlib.sha256(
        (str(experiment["id"]) + "|" + str(run["id"]) + "|" + str(run["result_hash"])).encode("utf-8")
    ).hexdigest()
    result_snapshot = {
        "compute_run_id": run["id"],
        "operation": run["operation"],
        "result_hash": run["result_hash"],
        "data_origin": method.get("data_origin"),
        "data_provenance": data_provenance,
        "statistician": statistician,
        "skeptic": skeptic,
        "replication": replication,
    }
    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_experiment_reviews(
            review_id,experiment_id,compute_run_id,mission_id,user_id,operation,result_hash,
            statistician_json,skeptic_json,replication_json,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(experiment_id,user_id) DO NOTHING""",
            (
                review_id, str(experiment["id"]), str(run["id"]), str(experiment["mission_id"]),
                int(user_id), run["operation"], run["result_hash"], _json(statistician),
                _json(skeptic), _json(replication), _json(review_safety),
            ))
        cur.execute("""SELECT * FROM velia_research_experiment_reviews
            WHERE experiment_id=%s AND user_id=%s""",
            (str(experiment["id"]), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_experiment_review_state_missing", 500)
        cur.execute("""UPDATE velia_research_experiments
            SET status='completed',result_json=%s,updated_at=NOW()
            WHERE experiment_id=%s AND user_id=%s AND status='planned'""",
            (_json(result_snapshot), str(experiment["id"]), int(user_id)))
        event = {
            "review_id": row["review_id"],
            "experiment_id": str(experiment["id"]),
            "compute_run_id": str(run["id"]),
            "operation": run["operation"],
            "result_hash": run["result_hash"],
            "replication_match": True,
            "data_origin": method.get("data_origin"),
        }
        if method.get("data_origin") == "dataset_registry":
            event.update({
                "dataset_id": data_provenance.get("dataset_id"),
                "dataset_hash": data_provenance.get("dataset_hash"),
                "split_hash": data_provenance.get("split_hash"),
                "split": data_provenance.get("split"),
            })
        center._event(
            cur,
            str(experiment["mission_id"]),
            user_id,
            "experiment_pipeline_completed",
            event,
        )
        return _review_row(row)


def run_ready(user_id: int, mission_id: str, max_experiments: int = 1) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_experiment_pipeline_disabled", 503)
    if type(max_experiments) is not int or not 1 <= max_experiments <= MAX_READY_PER_RUN:
        raise projects.ProjectError("invalid_experiment_pipeline_limit")

    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_compute_read_only", 403)

    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_experiments e
            WHERE e.mission_id=%s AND e.user_id=%s AND e.status='planned'
              AND NOT EXISTS (
                SELECT 1 FROM velia_research_experiment_reviews r
                WHERE r.experiment_id=e.experiment_id AND r.user_id=e.user_id
              )
            ORDER BY e.created_at ASC,e.experiment_id ASC LIMIT 20""",
            (str(mission_id), int(user_id)))
        rows = [dict(row) for row in cur.fetchall()]

    reviews: List[Dict[str, Any]] = []
    skipped = 0
    for row in rows:
        method = json.loads(row["method_json"])
        if not isinstance(method, dict) or method.get("type") != "safe_compute":
            skipped += 1
            continue
        experiment = _experiment_row(row)
        reviews.append(_execute_one(user_id, experiment))
        if len(reviews) >= max_experiments:
            break
    return {
        "mission_id": str(mission_id),
        "executed": len(reviews),
        "skipped_non_compute": skipped,
        "reviews": reviews,
    }


def list_reviews(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_experiment_reviews
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,review_id DESC LIMIT 51 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "reviews": [_review_row(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }
