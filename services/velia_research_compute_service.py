"""Allowlisted numerical experiments for VELIA Research Center.

This service intentionally does not execute user code. It accepts a small,
versioned JSON schema and dispatches only to deterministic, in-process numeric
algorithms implemented below. No shell, dynamic imports, file paths, URLs,
expressions, callbacks, or arbitrary Python are accepted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


ENGINE_VERSION = 1
MAX_VECTOR = 2000
MAX_BOOTSTRAP_VECTOR = 500
MAX_RESAMPLES = 2000
MAX_MONTE_CARLO_TRIALS = 25000
MAX_VARIABLES = 16
ALLOWED_OPERATIONS = {
    "descriptive_stats",
    "pearson_correlation",
    "linear_regression",
    "bootstrap_mean_ci",
    "bootstrap_mean_difference_ci",
    "monte_carlo_sum",
}
STOCHASTIC_OPERATIONS = {
    "bootstrap_mean_ci",
    "bootstrap_mean_difference_ci",
    "monte_carlo_sum",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return center.experiment_execution_enabled() and _env_bool("VELIA_RESEARCH_COMPUTE_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "engine": "velia_safe_compute",
        "engine_version": ENGINE_VERSION,
        "allowlisted_operations": sorted(ALLOWED_OPERATIONS),
        "arbitrary_code": False,
        "arbitrary_shell": False,
        "arbitrary_file_access": False,
        "arbitrary_url_fetch": False,
        "dynamic_expression_eval": False,
        "limits": {
            "max_vector": MAX_VECTOR,
            "max_bootstrap_vector": MAX_BOOTSTRAP_VECTOR,
            "max_resamples": MAX_RESAMPLES,
            "max_monte_carlo_trials": MAX_MONTE_CARLO_TRIALS,
            "max_variables": MAX_VARIABLES,
        },
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _number(value: Any, code: str = "invalid_compute_parameters") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise projects.ProjectError(code)
    out = float(value)
    if not math.isfinite(out) or abs(out) > 1e15:
        raise projects.ProjectError(code)
    return out


def _integer(value: Any, low: int, high: int, code: str = "invalid_compute_parameters") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise projects.ProjectError(code)
    if value < low or value > high:
        raise projects.ProjectError(code)
    return int(value)


def _vector(value: Any, *, maximum: int = MAX_VECTOR, minimum: int = 2) -> List[float]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise projects.ProjectError("invalid_compute_parameters")
    return [_number(item) for item in value]


def _same_length(x: Sequence[float], y: Sequence[float]) -> None:
    if len(x) != len(y):
        raise projects.ProjectError("invalid_compute_parameters")


def _strict_keys(data: Dict[str, Any], required: Iterable[str], optional: Iterable[str] = ()) -> None:
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_compute_parameters")
    required_set, optional_set = set(required), set(optional)
    if set(data) - required_set - optional_set or not required_set.issubset(data):
        raise projects.ProjectError("invalid_compute_parameters")


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise projects.ProjectError("invalid_compute_parameters")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(sorted_values[low])
    frac = pos - low
    return float(sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac)


def _summary(values: Sequence[float]) -> Dict[str, Any]:
    ordered = sorted(values)
    n = len(values)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "se": sd / math.sqrt(n) if n else 0.0,
        "min": float(ordered[0]),
        "p25": _quantile(ordered, 0.25),
        "median": _quantile(ordered, 0.5),
        "p75": _quantile(ordered, 0.75),
        "max": float(ordered[-1]),
    }


def _descriptive(parameters: Dict[str, Any], _rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"values"})
    return _summary(_vector(parameters["values"]))


def _correlation(parameters: Dict[str, Any], _rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"x", "y"})
    x, y = _vector(parameters["x"]), _vector(parameters["y"])
    _same_length(x, y)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    sxx = sum(v * v for v in dx)
    syy = sum(v * v for v in dy)
    if sxx <= 0.0 or syy <= 0.0:
        raise projects.ProjectError("compute_zero_variance")
    r = sum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)
    return {"n": len(x), "pearson_r": max(-1.0, min(1.0, r))}


def _linear_regression(parameters: Dict[str, Any], _rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"x", "y"})
    x, y = _vector(parameters["x"]), _vector(parameters["y"])
    _same_length(x, y)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((value - mx) ** 2 for value in x)
    if sxx <= 0.0:
        raise projects.ProjectError("compute_zero_variance")
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    intercept = my - slope * mx
    predictions = [intercept + slope * value for value in x]
    sse = sum((actual - predicted) ** 2 for actual, predicted in zip(y, predictions))
    sst = sum((actual - my) ** 2 for actual in y)
    r2 = 1.0 - sse / sst if sst > 0.0 else 1.0
    return {
        "n": len(x),
        "slope": slope,
        "intercept": intercept,
        "r_squared": max(0.0, min(1.0, r2)),
        "rmse": math.sqrt(sse / len(x)),
    }


def _confidence(parameters: Dict[str, Any]) -> float:
    value = _number(parameters.get("confidence", 0.95))
    if value < 0.80 or value > 0.99:
        raise projects.ProjectError("invalid_compute_parameters")
    return value


def _resamples(parameters: Dict[str, Any]) -> int:
    return _integer(parameters.get("resamples", 1000), 100, MAX_RESAMPLES)


def _bootstrap_mean(parameters: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"values"}, {"resamples", "confidence"})
    values = _vector(parameters["values"], maximum=MAX_BOOTSTRAP_VECTOR)
    count, confidence = _resamples(parameters), _confidence(parameters)
    n = len(values)
    samples = []
    for _ in range(count):
        samples.append(statistics.fmean(values[rng.randrange(n)] for _ in range(n)))
    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    return {
        "n": n,
        "mean": statistics.fmean(values),
        "confidence": confidence,
        "resamples": count,
        "ci_low": _quantile(samples, alpha),
        "ci_high": _quantile(samples, 1.0 - alpha),
        "method": "percentile_bootstrap",
    }


def _bootstrap_mean_difference(parameters: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"a", "b"}, {"resamples", "confidence"})
    a = _vector(parameters["a"], maximum=MAX_BOOTSTRAP_VECTOR)
    b = _vector(parameters["b"], maximum=MAX_BOOTSTRAP_VECTOR)
    count, confidence = _resamples(parameters), _confidence(parameters)
    na, nb = len(a), len(b)
    samples = []
    for _ in range(count):
        ma = statistics.fmean(a[rng.randrange(na)] for _ in range(na))
        mb = statistics.fmean(b[rng.randrange(nb)] for _ in range(nb))
        samples.append(ma - mb)
    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    return {
        "n_a": na,
        "n_b": nb,
        "mean_a": statistics.fmean(a),
        "mean_b": statistics.fmean(b),
        "mean_difference_a_minus_b": statistics.fmean(a) - statistics.fmean(b),
        "confidence": confidence,
        "resamples": count,
        "ci_low": _quantile(samples, alpha),
        "ci_high": _quantile(samples, 1.0 - alpha),
        "method": "percentile_bootstrap",
    }


def _distribution(variable: Dict[str, Any], rng: random.Random) -> float:
    if not isinstance(variable, dict):
        raise projects.ProjectError("invalid_compute_parameters")
    kind = variable.get("distribution")
    weight = _number(variable.get("weight", 1.0))
    if abs(weight) > 1e6:
        raise projects.ProjectError("invalid_compute_parameters")
    if kind == "normal":
        _strict_keys(variable, {"distribution", "mean", "sd"}, {"weight"})
        mean, sd = _number(variable["mean"]), _number(variable["sd"])
        if sd < 0.0 or sd > 1e12:
            raise projects.ProjectError("invalid_compute_parameters")
        value = rng.gauss(mean, sd)
    elif kind == "uniform":
        _strict_keys(variable, {"distribution", "low", "high"}, {"weight"})
        low, high = _number(variable["low"]), _number(variable["high"])
        if low > high:
            raise projects.ProjectError("invalid_compute_parameters")
        value = rng.uniform(low, high)
    elif kind == "bernoulli":
        _strict_keys(variable, {"distribution", "p"}, {"weight"})
        p = _number(variable["p"])
        if p < 0.0 or p > 1.0:
            raise projects.ProjectError("invalid_compute_parameters")
        value = 1.0 if rng.random() < p else 0.0
    else:
        raise projects.ProjectError("compute_distribution_not_allowed")
    out = weight * value
    if not math.isfinite(out) or abs(out) > 1e18:
        raise projects.ProjectError("compute_numeric_overflow")
    return out


def _monte_carlo_sum(parameters: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    _strict_keys(parameters, {"variables"}, {"trials", "threshold"})
    variables = parameters["variables"]
    if not isinstance(variables, list) or not 1 <= len(variables) <= MAX_VARIABLES:
        raise projects.ProjectError("invalid_compute_parameters")
    trials = _integer(parameters.get("trials", 5000), 100, MAX_MONTE_CARLO_TRIALS)
    threshold = None if "threshold" not in parameters else _number(parameters["threshold"])
    results = []
    exceed = 0
    for _ in range(trials):
        value = sum(_distribution(variable, rng) for variable in variables)
        if not math.isfinite(value):
            raise projects.ProjectError("compute_numeric_overflow")
        results.append(value)
        if threshold is not None and value >= threshold:
            exceed += 1
    ordered = sorted(results)
    output = {
        "trials": trials,
        "variables": len(variables),
        "mean": statistics.fmean(results),
        "sd": statistics.stdev(results) if trials > 1 else 0.0,
        "p05": _quantile(ordered, 0.05),
        "p50": _quantile(ordered, 0.50),
        "p95": _quantile(ordered, 0.95),
        "model": "weighted_sum_of_independent_allowlisted_distributions",
    }
    if threshold is not None:
        output["threshold"] = threshold
        output["probability_gte_threshold"] = exceed / trials
    return output


_HANDLERS = {
    "descriptive_stats": _descriptive,
    "pearson_correlation": _correlation,
    "linear_regression": _linear_regression,
    "bootstrap_mean_ci": _bootstrap_mean,
    "bootstrap_mean_difference_ci": _bootstrap_mean_difference,
    "monte_carlo_sum": _monte_carlo_sum,
}


def _validate_request(data: Any) -> Tuple[str, Dict[str, Any], Optional[int]]:
    if not isinstance(data, dict) or set(data) - {"operation", "parameters", "seed"}:
        raise projects.ProjectError("invalid_compute_request")
    operation = data.get("operation")
    parameters = data.get("parameters")
    if operation not in ALLOWED_OPERATIONS or not isinstance(parameters, dict):
        raise projects.ProjectError("compute_operation_not_allowed")
    seed = data.get("seed")
    if seed is not None:
        seed = _integer(seed, 0, 2**63 - 1, "invalid_compute_seed")
    try:
        encoded = _json(data)
    except (TypeError, ValueError, OverflowError):
        raise projects.ProjectError("invalid_compute_parameters")
    if len(encoded) > 64000:
        raise projects.ProjectError("compute_request_too_large", 413)
    return str(operation), parameters, seed


def ensure_tables() -> None:
    if not projects.ready():
        center.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_compute_runs (
            run_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            operation TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            input_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            seed BIGINT NULL,
            input_hash TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(user_id,client_request_id),
            FOREIGN KEY(experiment_id) REFERENCES velia_research_experiments(experiment_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id) REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_compute_runs
            ON velia_research_compute_runs(mission_id,user_id,created_at DESC)""")


def _row_to_run(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["run_id"],
        "experiment_id": row["experiment_id"],
        "mission_id": row["mission_id"],
        "operation": row["operation"],
        "input": json.loads(row["input_json"]),
        "result": json.loads(row["result_json"]),
        "seed": row["seed"],
        "input_hash": row["input_hash"],
        "result_hash": row["result_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def _experiment_snapshot(user_id: int, experiment_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT e.*,m.status AS mission_status,m.safety_json AS mission_safety
            FROM velia_research_experiments e
            JOIN velia_research_missions m
              ON m.mission_id=e.mission_id AND m.user_id=e.user_id
            WHERE e.experiment_id=%s AND e.user_id=%s""",
            (str(experiment_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_experiment_not_found", 404)
        return dict(row)


def execute(user_id: int, experiment_id: str, data: Any, client_request_id: Optional[str]) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_compute_disabled", 503)
    operation, parameters, explicit_seed = _validate_request(data)
    key = projects.request_key(client_request_id)
    experiment = _experiment_snapshot(user_id, experiment_id)
    if experiment["status"] == "blocked" or experiment["mission_status"] in {"blocked", "cancelled"}:
        raise projects.ProjectError("research_experiment_not_active", 409)

    mission_safety = json.loads(experiment["mission_safety"])
    experiment_safety = json.loads(experiment["safety_json"])
    if mission_safety.get("read_only_only") or experiment_safety.get("read_only_only"):
        raise projects.ProjectError("research_compute_read_only", 403)

    method = json.loads(experiment["method_json"])
    decision = safety.classify(_json({"stored_method": method, "operation": operation}), phase="tool_call")
    if decision["decision"] != "allowed" or not decision.get("execution_allowed") or decision.get("read_only_only"):
        raise projects.ProjectError("research_compute_safety_blocked", 403)

    normalized = {"operation": operation, "parameters": parameters, "seed": explicit_seed}
    request_hash = _sha([str(experiment_id), normalized])
    input_hash = _sha(normalized)
    seed = explicit_seed
    if operation in STOCHASTIC_OPERATIONS and seed is None:
        seed = int(input_hash[:16], 16) & ((1 << 63) - 1)
    rng = random.Random(seed if seed is not None else 0)
    output = _HANDLERS[operation](parameters, rng)
    result_hash = _sha(output)
    result = {
        "output": output,
        "reproducibility": {
            "engine": "velia_safe_compute",
            "engine_version": ENGINE_VERSION,
            "algorithm": operation,
            "seed": seed,
            "input_sha256": input_hash,
            "output_sha256": result_hash,
            "deterministic_given_input_and_seed": True,
            "user_code_executed": False,
        },
    }

    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_compute_runs
            WHERE user_id=%s AND client_request_id=%s""", (int(user_id), key))
        existing = cur.fetchone()
        if existing:
            if existing["request_hash"] != request_hash or existing["experiment_id"] != str(experiment_id):
                raise projects.ProjectError("idempotency_conflict", 409)
            return _row_to_run(existing)

        run_id = str(uuid.uuid4())
        cur.execute("""INSERT INTO velia_research_compute_runs(
            run_id,experiment_id,mission_id,user_id,operation,client_request_id,request_hash,
            input_json,result_json,seed,input_hash,result_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (run_id, str(experiment_id), experiment["mission_id"], int(user_id), operation, key,
             request_hash, _json(normalized), _json(result), seed, input_hash, result_hash, _json(decision)))
        row = cur.fetchone()
        center._event(cur, experiment["mission_id"], user_id, "compute_run_completed", {
            "run_id": run_id,
            "experiment_id": str(experiment_id),
            "operation": operation,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "seed": seed,
        })
        return _row_to_run(row)


def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("SELECT * FROM velia_research_compute_runs WHERE run_id=%s AND user_id=%s",
                    (str(run_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_compute_run_not_found", 404)
        return _row_to_run(row)


def verify_reproducibility(user_id: int, run_id: str) -> Dict[str, Any]:
    """Recompute one persisted allowlisted run without creating a second record."""
    run = get_run(user_id, run_id)
    stored_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    operation = str(run.get("operation") or "")
    parameters = stored_input.get("parameters")
    replay_request = {
        "operation": operation,
        "parameters": parameters,
        "seed": run.get("seed"),
    }
    validated_operation, validated_parameters, seed = _validate_request(replay_request)
    rng = random.Random(seed if seed is not None else 0)
    replay_output = _HANDLERS[validated_operation](validated_parameters, rng)
    observed_hash = _sha(replay_output)
    expected_hash = str(run.get("result_hash") or "")
    return {
        "run_id": str(run_id),
        "operation": validated_operation,
        "seed": seed,
        "expected_output_sha256": expected_hash,
        "observed_output_sha256": observed_hash,
        "match": bool(expected_hash) and observed_hash == expected_hash,
        "recomputed_without_user_code": True,
    }


def list_runs(user_id: int, experiment_id: str, offset: int = 0) -> Dict[str, Any]:
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    experiment = _experiment_snapshot(user_id, experiment_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_compute_runs
            WHERE experiment_id=%s AND user_id=%s
            ORDER BY created_at DESC,run_id DESC LIMIT 51 OFFSET %s""",
            (str(experiment_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "experiment_id": experiment["experiment_id"],
        "runs": [_row_to_run(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }
