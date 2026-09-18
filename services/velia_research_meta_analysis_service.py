"""Deterministic meta-analysis and evidence calibration for VELIA Research Center.

This layer accepts only source-backed, structured aggregate study statistics.
It never fetches arbitrary URLs, opens files, executes user code, or treats
publication-bias screening as proof of publication bias.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Tuple

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_claim_service as claims
from services import velia_research_literature_service as literature
from services import velia_research_safety_service as safety
from services import velia_research_systematic_review_service as systematic_review
from services.velia_chat_service import _iso


META_VERSION = 1
ALLOWED_EFFECT_TYPES = {"correlation", "standardized_mean_difference", "log_odds_ratio"}
CLAIM_EFFECT_COMPATIBILITY = {
    "correlation": {"correlation"},
    "bootstrap_mean_difference_ci": {"standardized_mean_difference"},
}
ALLOWED_DESIGNS = {"rct", "cohort", "case_control", "cross_sectional", "preclinical", "other"}
ROB_DOMAINS = ("selection", "measurement", "confounding", "missing_data", "reporting")
ROB_LEVELS = {"low", "some_concerns", "high"}
MAX_STUDIES_PER_CLAIM = 100
MIN_META_STUDIES = 2
MAX_NOTE = 500


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        center.enabled()
        and claims.enabled()
        and _env_bool("VELIA_RESEARCH_META_ANALYSIS_ENABLED", False)
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "meta_version": META_VERSION,
        "allowed_effect_types": sorted(ALLOWED_EFFECT_TYPES),
        "fixed_effect": "inverse_variance",
        "random_effects": "dersimonian_laird",
        "heterogeneity": ["cochran_q", "i_squared", "tau_squared"],
        "leave_one_out": True,
        "risk_of_bias_domains": list(ROB_DOMAINS),
        "publication_bias_screening": "egger_normal_approximation_when_k_gte_10",
        "publication_bias_diagnostic_is_proof": False,
        "claim_promotion_allowed": False,
        "claim_downgrade_or_contradiction_allowed": True,
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


def _num(value: Any, low: float, high: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise projects.ProjectError(code)
    out = float(value)
    if not math.isfinite(out) or not low <= out <= high:
        raise projects.ProjectError(code)
    return out


def _int(value: Any, low: int, high: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise projects.ProjectError(code)
    return int(value)


def _text(value: Any, limit: int, code: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    out = " ".join(value.split())
    if required and not out:
        raise projects.ProjectError(code)
    return out


def ensure_tables() -> None:
    claims.ensure_tables()
    literature.ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_meta_studies (
            study_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            source_id TEXT NOT NULL,
            publication_fingerprint TEXT NOT NULL,
            study_design TEXT NOT NULL,
            effect_type TEXT NOT NULL,
            input_json TEXT NOT NULL,
            normalized_json TEXT NOT NULL,
            risk_json TEXT NOT NULL,
            study_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(claim_id,user_id,source_id),
            UNIQUE(claim_id,user_id,publication_fingerprint),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_meta_studies
            ON velia_research_meta_studies(claim_id,user_id,created_at ASC)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_meta_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            effect_type TEXT NOT NULL,
            study_ids_json TEXT NOT NULL,
            fixed_json TEXT NOT NULL,
            random_json TEXT NOT NULL,
            heterogeneity_json TEXT NOT NULL,
            leave_one_out_json TEXT NOT NULL,
            risk_json TEXT NOT NULL,
            publication_bias_json TEXT NOT NULL,
            calibration_json TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            safety_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(claim_id,user_id,evidence_hash),
            FOREIGN KEY(claim_id) REFERENCES velia_research_claims(claim_id) ON DELETE CASCADE,
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_meta_snapshots
            ON velia_research_meta_snapshots(claim_id,user_id,created_at DESC)""")


def _risk(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"domains", "note"}:
        raise projects.ProjectError("invalid_research_meta_risk")
    domains = value.get("domains")
    if not isinstance(domains, dict) or set(domains) != set(ROB_DOMAINS):
        raise projects.ProjectError("invalid_research_meta_risk")
    clean: Dict[str, str] = {}
    for domain in ROB_DOMAINS:
        level = str(domains.get(domain) or "")
        if level not in ROB_LEVELS:
            raise projects.ProjectError("invalid_research_meta_risk")
        clean[domain] = level
    note = _text(value.get("note"), MAX_NOTE, "invalid_research_meta_risk")
    rank = {"low": 0, "some_concerns": 1, "high": 2}
    summary = max(clean.values(), key=lambda level: rank[level])
    return {"domains": clean, "summary": summary, "note": note}


def _source(user_id: int, mission_id: str, source_id: str) -> Dict[str, Any]:
    if not isinstance(source_id, str) or not source_id or len(source_id) > 128:
        raise projects.ProjectError("invalid_research_meta_source")
    with projects.transaction() as cur:
        cur.execute("""SELECT source_id,provider,external_id,doi,title,published_year,evidence_hint
            FROM velia_research_sources
            WHERE source_id=%s AND mission_id=%s AND user_id=%s""",
            (source_id, str(mission_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_meta_source_not_found", 404)
    doi = str(row.get("doi") or "").strip().casefold()
    external_id = str(row.get("external_id") or "").strip().casefold()
    fingerprint = _sha({
        "doi": doi or None,
        "external_id": None if doi else external_id,
        "provider": None if doi else str(row.get("provider") or "").casefold(),
    })
    return {
        "source_id": row["source_id"],
        "provider": row["provider"],
        "external_id": row["external_id"],
        "doi": row["doi"],
        "title": row["title"],
        "published_year": row["published_year"],
        "evidence_hint": row["evidence_hint"],
        "publication_fingerprint": fingerprint,
    }


def _normalize(effect_type: str, stats: Any) -> Dict[str, Any]:
    if not isinstance(stats, dict):
        raise projects.ProjectError("invalid_research_meta_effect")

    if effect_type == "correlation":
        if set(stats) != {"r", "n"}:
            raise projects.ProjectError("invalid_research_meta_effect")
        r = _num(stats["r"], -0.999999, 0.999999, "invalid_research_meta_effect")
        n = _int(stats["n"], 4, 1_000_000, "invalid_research_meta_effect")
        z = math.atanh(r)
        variance = 1.0 / (n - 3.0)
        return {
            "effect": z,
            "variance": variance,
            "standard_error": math.sqrt(variance),
            "normalized_metric": "fisher_z",
            "display_metric": "correlation_r",
            "display_effect": r,
            "sample_size": n,
        }

    if effect_type == "standardized_mean_difference":
        required = {
            "n_treatment", "mean_treatment", "sd_treatment",
            "n_control", "mean_control", "sd_control",
        }
        if set(stats) != required:
            raise projects.ProjectError("invalid_research_meta_effect")
        n1 = _int(stats["n_treatment"], 2, 1_000_000, "invalid_research_meta_effect")
        n0 = _int(stats["n_control"], 2, 1_000_000, "invalid_research_meta_effect")
        m1 = _num(stats["mean_treatment"], -1e12, 1e12, "invalid_research_meta_effect")
        m0 = _num(stats["mean_control"], -1e12, 1e12, "invalid_research_meta_effect")
        sd1 = _num(stats["sd_treatment"], 1e-12, 1e12, "invalid_research_meta_effect")
        sd0 = _num(stats["sd_control"], 1e-12, 1e12, "invalid_research_meta_effect")
        df = n1 + n0 - 2
        pooled_var = (((n1 - 1) * sd1 * sd1) + ((n0 - 1) * sd0 * sd0)) / df
        if pooled_var <= 0.0 or not math.isfinite(pooled_var):
            raise projects.ProjectError("invalid_research_meta_effect")
        d = (m1 - m0) / math.sqrt(pooled_var)
        correction = 1.0 - 3.0 / (4.0 * df - 1.0)
        g = correction * d
        if not math.isfinite(g) or abs(g) > 100.0:
            raise projects.ProjectError("invalid_research_meta_effect")
        variance = ((n1 + n0) / (n1 * n0)) + (g * g) / (2.0 * df)
        return {
            "effect": g,
            "variance": variance,
            "standard_error": math.sqrt(variance),
            "normalized_metric": "hedges_g",
            "display_metric": "hedges_g",
            "display_effect": g,
            "sample_size": n1 + n0,
        }

    if effect_type == "log_odds_ratio":
        required = {
            "event_treatment", "non_event_treatment",
            "event_control", "non_event_control",
        }
        if set(stats) != required:
            raise projects.ProjectError("invalid_research_meta_effect")
        cells = [
            _int(stats[key], 0, 10_000_000, "invalid_research_meta_effect")
            for key in (
                "event_treatment", "non_event_treatment",
                "event_control", "non_event_control",
            )
        ]
        if cells[0] + cells[1] < 1 or cells[2] + cells[3] < 1:
            raise projects.ProjectError("invalid_research_meta_effect")
        corrected = [float(value) for value in cells]
        continuity = any(value == 0 for value in cells)
        if continuity:
            corrected = [value + 0.5 for value in corrected]
        a, b, c, d = corrected
        effect = math.log((a * d) / (b * c))
        variance = (1.0 / a) + (1.0 / b) + (1.0 / c) + (1.0 / d)
        return {
            "effect": effect,
            "variance": variance,
            "standard_error": math.sqrt(variance),
            "normalized_metric": "log_odds_ratio",
            "display_metric": "odds_ratio",
            "display_effect": math.exp(effect),
            "sample_size": sum(cells),
            "continuity_correction_0_5": continuity,
        }

    raise projects.ProjectError("invalid_research_meta_effect")


def _study_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["study_id"],
        "claim_id": row["claim_id"],
        "mission_id": row["mission_id"],
        "source_id": row["source_id"],
        "publication_fingerprint": row["publication_fingerprint"],
        "study_design": row["study_design"],
        "effect_type": row["effect_type"],
        "input": json.loads(row["input_json"]),
        "normalized": json.loads(row["normalized_json"]),
        "risk_of_bias": json.loads(row["risk_json"]),
        "study_hash": row["study_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def register_study(user_id: int, claim_id: str, data: Any) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_meta_analysis_disabled", 503)
    if not isinstance(data, dict) or set(data) - {
        "source_id", "study_design", "effect_type", "statistics", "risk_of_bias"
    }:
        raise projects.ProjectError("invalid_research_meta_study")
    claim = claims.get_claim(user_id, claim_id)
    mission = center.get_mission(user_id, claim["mission_id"])
    if mission["status"] in {"blocked", "cancelled"} or mission["safety"].get("read_only_only"):
        raise projects.ProjectError("research_meta_analysis_read_only", 403)

    design = str(data.get("study_design") or "")
    effect_type = str(data.get("effect_type") or "")
    if design not in ALLOWED_DESIGNS or effect_type not in ALLOWED_EFFECT_TYPES:
        raise projects.ProjectError("invalid_research_meta_study")
    compatible = CLAIM_EFFECT_COMPATIBILITY.get(claim["analysis_kind"], set())
    if effect_type not in compatible:
        raise projects.ProjectError("research_meta_effect_incompatible_with_claim", 409)
    source = _source(user_id, claim["mission_id"], data.get("source_id"))
    systematic_review.assert_source_eligible(
        user_id, claim["mission_id"], source["source_id"]
    )
    risk = _risk(data.get("risk_of_bias"))
    normalized = _normalize(effect_type, data.get("statistics"))

    with projects.transaction() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM velia_research_meta_studies
            WHERE claim_id=%s AND user_id=%s""", (claim["id"], int(user_id)))
        if int(cur.fetchone()["n"]) >= MAX_STUDIES_PER_CLAIM:
            raise projects.ProjectError("research_meta_study_limit_reached", 409)

    frozen = {
        "meta_version": META_VERSION,
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "source": source,
        "study_design": design,
        "effect_type": effect_type,
        "statistics": data.get("statistics"),
        "normalized": normalized,
        "risk_of_bias": risk,
    }
    decision = safety.classify(_json(frozen), phase="experiment")
    if decision["decision"] != "allowed" or decision.get("read_only_only"):
        raise projects.ProjectError("research_meta_analysis_safety_blocked", 403)

    study_hash = _sha(frozen)
    study_id = _sha({
        "claim_id": claim["id"],
        "publication_fingerprint": source["publication_fingerprint"],
        "study_hash": study_hash,
    })
    with projects.transaction(user_id) as cur:
        try:
            cur.execute("""INSERT INTO velia_research_meta_studies(
                study_id,claim_id,mission_id,user_id,source_id,publication_fingerprint,
                study_design,effect_type,input_json,normalized_json,risk_json,study_hash,
                safety_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    study_id, claim["id"], claim["mission_id"], int(user_id), source["source_id"],
                    source["publication_fingerprint"], design, effect_type,
                    _json(data.get("statistics")), _json(normalized), _json(risk),
                    study_hash, _json(decision),
                ))
        except Exception as exc:
            # A claim may not double-count the same source/publication fingerprint.
            if "unique" in str(exc).casefold() or "duplicate" in str(exc).casefold():
                raise projects.ProjectError("research_meta_duplicate_publication", 409) from exc
            raise
        cur.execute("""SELECT * FROM velia_research_meta_studies
            WHERE study_id=%s AND user_id=%s""", (study_id, int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_meta_study_state_missing", 500)
        center._event(cur, claim["mission_id"], user_id, "research_meta_study_registered", {
            "claim_id": claim["id"],
            "study_id": study_id,
            "source_id": source["source_id"],
            "effect_type": effect_type,
            "risk_summary": risk["summary"],
            "study_hash": study_hash,
        })
        return _study_row(row)


def list_studies(user_id: int, claim_id: str, offset: int = 0) -> Dict[str, Any]:
    claims.get_claim(user_id, claim_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_meta_studies
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at ASC,study_id ASC LIMIT 51 OFFSET %s""",
            (str(claim_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "studies": [_study_row(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


def get_study(user_id: int, study_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_meta_studies
            WHERE study_id=%s AND user_id=%s""", (str(study_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_meta_study_not_found", 404)
    return _study_row(row)


def _pool(items: List[Dict[str, Any]], tau2: float = 0.0) -> Dict[str, Any]:
    weights = [1.0 / (float(item["normalized"]["variance"]) + tau2) for item in items]
    total = sum(weights)
    if total <= 0.0 or not math.isfinite(total):
        raise projects.ProjectError("research_meta_invalid_weights", 409)
    estimate = sum(
        weight * float(item["normalized"]["effect"])
        for weight, item in zip(weights, items)
    ) / total
    se = math.sqrt(1.0 / total)
    z = NormalDist().inv_cdf(0.975)
    return {
        "estimate": estimate,
        "standard_error": se,
        "ci_low": estimate - z * se,
        "ci_high": estimate + z * se,
        "weights": weights,
    }


def _display(effect_type: str, value: float) -> float:
    if effect_type == "correlation":
        return math.tanh(value)
    if effect_type == "log_odds_ratio":
        return math.exp(value)
    return value


def _heterogeneity(items: List[Dict[str, Any]], fixed: Dict[str, Any]) -> Dict[str, Any]:
    weights = fixed["weights"]
    estimate = float(fixed["estimate"])
    q = sum(
        weight * (float(item["normalized"]["effect"]) - estimate) ** 2
        for weight, item in zip(weights, items)
    )
    df = len(items) - 1
    sum_w = sum(weights)
    sum_w2 = sum(weight * weight for weight in weights)
    c = sum_w - (sum_w2 / sum_w) if sum_w > 0 else 0.0
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    i2 = max(0.0, ((q - df) / q) * 100.0) if q > 0 else 0.0
    return {
        "cochran_q": q,
        "degrees_of_freedom": df,
        "i_squared_percent": min(100.0, i2),
        "tau_squared": tau2,
        "method": "dersimonian_laird",
    }


def _leave_one_out(items: List[Dict[str, Any]], effect_type: str) -> Dict[str, Any]:
    if len(items) <= 2:
        return {
            "available": False,
            "reason": "requires_at_least_3_studies",
            "runs": [],
            "sign_stable": None,
        }
    runs: List[Dict[str, Any]] = []
    signs = set()
    for omitted in items:
        subset = [item for item in items if item["id"] != omitted["id"]]
        fixed = _pool(subset)
        heterogeneity = _heterogeneity(subset, fixed)
        random = _pool(subset, heterogeneity["tau_squared"])
        estimate = float(random["estimate"])
        sign = 1 if estimate > 0 else (-1 if estimate < 0 else 0)
        signs.add(sign)
        runs.append({
            "omitted_study_id": omitted["id"],
            "estimate": estimate,
            "display_estimate": _display(effect_type, estimate),
            "ci_low": float(random["ci_low"]),
            "ci_high": float(random["ci_high"]),
            "i_squared_percent": heterogeneity["i_squared_percent"],
        })
    nonzero = {value for value in signs if value != 0}
    return {
        "available": True,
        "runs": runs,
        "sign_stable": len(nonzero) <= 1 and 0 not in signs,
    }


def _risk_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {level: 0 for level in sorted(ROB_LEVELS)}
    domain_counts = {
        domain: {level: 0 for level in sorted(ROB_LEVELS)}
        for domain in ROB_DOMAINS
    }
    for item in items:
        risk = item["risk_of_bias"]
        counts[risk["summary"]] += 1
        for domain in ROB_DOMAINS:
            domain_counts[domain][risk["domains"][domain]] += 1
    high_count = counts["high"]
    return {
        "summary_counts": counts,
        "domain_counts": domain_counts,
        "high_risk_count": high_count,
        "high_risk_fraction": high_count / len(items),
        "high_risk_majority": high_count * 2 >= len(items),
        "interpretation": "Risk-of-bias ratings constrain certainty; they do not numerically correct effects.",
    }


def _egger(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    k = len(items)
    if k < 10:
        return {
            "available": False,
            "reason": "requires_at_least_10_studies",
            "signal": None,
            "proof_of_publication_bias": False,
        }
    x = [1.0 / float(item["normalized"]["standard_error"]) for item in items]
    y = [
        float(item["normalized"]["effect"]) / float(item["normalized"]["standard_error"])
        for item in items
    ]
    mean_x = sum(x) / k
    mean_y = sum(y) / k
    sxx = sum((value - mean_x) ** 2 for value in x)
    if sxx <= 0.0:
        return {
            "available": False,
            "reason": "insufficient_precision_variation",
            "signal": None,
            "proof_of_publication_bias": False,
        }
    slope = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / sxx
    intercept = mean_y - slope * mean_x
    residuals = [b - (intercept + slope * a) for a, b in zip(x, y)]
    df = k - 2
    residual_var = sum(value * value for value in residuals) / df if df > 0 else 0.0
    se_intercept = math.sqrt(residual_var * ((1.0 / k) + (mean_x * mean_x / sxx)))
    if se_intercept <= 0.0:
        z_score = 0.0
        p_value = 1.0
    else:
        z_score = intercept / se_intercept
        p_value = 2.0 * (1.0 - NormalDist().cdf(abs(z_score)))
    return {
        "available": True,
        "method": "egger_regression_normal_approximation",
        "intercept": intercept,
        "standard_error": se_intercept,
        "z_score": z_score,
        "approx_two_sided_p": p_value,
        "signal": p_value < 0.10,
        "threshold": 0.10,
        "proof_of_publication_bias": False,
        "interpretation": "A signal indicates funnel asymmetry requiring investigation, not proof of publication bias.",
    }


def _direction(expected: str, pooled: Dict[str, Any]) -> str:
    low = float(pooled["ci_low"])
    high = float(pooled["ci_high"])
    if low <= 0.0 <= high:
        return "inconclusive"
    positive = low > 0.0
    if expected == "nonzero":
        return "supports"
    if expected == "positive":
        return "supports" if positive else "contradicts"
    return "contradicts" if positive else "supports"


def _calibration(
    claim: Dict[str, Any],
    items: List[Dict[str, Any]],
    random: Dict[str, Any],
    heterogeneity: Dict[str, Any],
    loo: Dict[str, Any],
    risk: Dict[str, Any],
    publication_bias: Dict[str, Any],
) -> Dict[str, Any]:
    signal = _direction(claim["expected_direction"], random)
    reasons: List[str] = []
    i2 = float(heterogeneity["i_squared_percent"])
    high_heterogeneity = i2 >= 75.0
    if high_heterogeneity:
        reasons.append("high_heterogeneity_i2_gte_75")
    if risk["high_risk_majority"]:
        reasons.append("majority_high_risk_of_bias")
    if publication_bias.get("signal") is True:
        reasons.append("egger_funnel_asymmetry_signal")
    if loo.get("available") and loo.get("sign_stable") is False:
        reasons.append("leave_one_out_direction_unstable")

    non_high = sum(1 for item in items if item["risk_of_bias"]["summary"] != "high")
    robust_contradiction = (
        signal == "contradicts"
        and len(items) >= 3
        and non_high >= 2
        and not high_heterogeneity
        and (loo.get("sign_stable") is True)
    )
    if robust_contradiction:
        action = "contradict"
        reasons.append("robust_random_effects_direction_opposes_preregistered_claim")
    elif reasons or signal == "contradicts":
        action = "caution"
        if signal == "contradicts":
            reasons.append("pooled_direction_opposes_claim_but_contradiction_threshold_not_met")
    else:
        action = "no_change"

    return {
        "claim_action": action,
        "pooled_signal": signal,
        "reasons": list(dict.fromkeys(reasons)),
        "claim_promotion_allowed": False,
        "claim_stage_ceiling_changed": False,
        "policy": (
            "Meta-analysis may add caution or robust contradiction, but supportive external evidence "
            "cannot promote a Claim Ledger stage."
        ),
    }


def _snapshot_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["snapshot_id"],
        "claim_id": row["claim_id"],
        "mission_id": row["mission_id"],
        "effect_type": row["effect_type"],
        "study_ids": json.loads(row["study_ids_json"]),
        "fixed_effect": json.loads(row["fixed_json"]),
        "random_effects": json.loads(row["random_json"]),
        "heterogeneity": json.loads(row["heterogeneity_json"]),
        "leave_one_out": json.loads(row["leave_one_out_json"]),
        "risk_of_bias": json.loads(row["risk_json"]),
        "publication_bias": json.loads(row["publication_bias_json"]),
        "calibration": json.loads(row["calibration_json"]),
        "evidence_hash": row["evidence_hash"],
        "safety": json.loads(row["safety_json"]),
        "created_at": _iso(row["created_at"]),
    }


def analyze_claim(user_id: int, claim_id: str) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_meta_analysis_disabled", 503)
    claim = claims.get_claim(user_id, claim_id)
    studies = list_studies(user_id, claim_id)["studies"]
    if len(studies) < MIN_META_STUDIES:
        raise projects.ProjectError("research_meta_insufficient_studies", 409)
    effect_types = {item["effect_type"] for item in studies}
    if len(effect_types) != 1:
        raise projects.ProjectError("research_meta_mixed_effect_types", 409)
    effect_type = next(iter(effect_types))

    fixed_raw = _pool(studies)
    heterogeneity = _heterogeneity(studies, fixed_raw)
    random_raw = _pool(studies, heterogeneity["tau_squared"])
    fixed = {
        key: value for key, value in fixed_raw.items() if key != "weights"
    }
    random = {
        key: value for key, value in random_raw.items() if key != "weights"
    }
    fixed.update({
        "model": "inverse_variance_fixed_effect",
        "display_estimate": _display(effect_type, fixed["estimate"]),
        "display_ci_low": _display(effect_type, fixed["ci_low"]),
        "display_ci_high": _display(effect_type, fixed["ci_high"]),
    })
    random.update({
        "model": "dersimonian_laird_random_effects",
        "display_estimate": _display(effect_type, random["estimate"]),
        "display_ci_low": _display(effect_type, random["ci_low"]),
        "display_ci_high": _display(effect_type, random["ci_high"]),
        "primary_for_calibration": True,
    })
    loo = _leave_one_out(studies, effect_type)
    risk = _risk_summary(studies)
    publication_bias = _egger(studies)
    calibration = _calibration(
        claim, studies, random, heterogeneity, loo, risk, publication_bias
    )

    study_ids = [item["id"] for item in studies]
    evidence = {
        "meta_version": META_VERSION,
        "claim_id": claim["id"],
        "claim_hash": claim["claim_hash"],
        "effect_type": effect_type,
        "study_ids": study_ids,
        "study_hashes": [item["study_hash"] for item in studies],
        "publication_fingerprints": [item["publication_fingerprint"] for item in studies],
        "fixed_effect": fixed,
        "random_effects": random,
        "heterogeneity": heterogeneity,
        "leave_one_out": loo,
        "risk_of_bias": risk,
        "publication_bias": publication_bias,
        "calibration": calibration,
    }
    decision = safety.classify(_json(evidence), phase="final_output")
    if decision["decision"] == "blocked":
        raise projects.ProjectError("research_meta_analysis_safety_blocked", 403)
    evidence_hash = _sha(evidence)
    snapshot_id = _sha({
        "claim_id": claim["id"],
        "evidence_hash": evidence_hash,
    })

    with projects.transaction(user_id) as cur:
        cur.execute("""INSERT INTO velia_research_meta_snapshots(
            snapshot_id,claim_id,mission_id,user_id,effect_type,study_ids_json,
            fixed_json,random_json,heterogeneity_json,leave_one_out_json,risk_json,
            publication_bias_json,calibration_json,evidence_hash,safety_json)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(claim_id,user_id,evidence_hash) DO NOTHING""",
            (
                snapshot_id, claim["id"], claim["mission_id"], int(user_id), effect_type,
                _json(study_ids), _json(fixed), _json(random), _json(heterogeneity),
                _json(loo), _json(risk), _json(publication_bias), _json(calibration),
                evidence_hash, _json(decision),
            ))
        cur.execute("""SELECT * FROM velia_research_meta_snapshots
            WHERE claim_id=%s AND user_id=%s AND evidence_hash=%s""",
            (claim["id"], int(user_id), evidence_hash))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_meta_snapshot_state_missing", 500)
        center._event(cur, claim["mission_id"], user_id, "research_meta_analysis_completed", {
            "claim_id": claim["id"],
            "snapshot_id": row["snapshot_id"],
            "study_count": len(studies),
            "effect_type": effect_type,
            "i_squared_percent": heterogeneity["i_squared_percent"],
            "claim_action": calibration["claim_action"],
            "evidence_hash": evidence_hash,
        })
        snapshot = _snapshot_row(row)

    calibration_record = claims.record_calibration(
        user_id,
        claim["id"],
        "meta_analysis",
        snapshot["id"],
        snapshot["calibration"],
    )
    snapshot["claim_calibration"] = calibration_record
    snapshot["claim_snapshot"] = claims.refresh_claim(user_id, claim["id"])
    return snapshot


def get_snapshot(user_id: int, snapshot_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_meta_snapshots
            WHERE snapshot_id=%s AND user_id=%s""", (str(snapshot_id), int(user_id)))
        row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_meta_snapshot_not_found", 404)
    return _snapshot_row(row)


def list_snapshots(user_id: int, claim_id: str, offset: int = 0) -> Dict[str, Any]:
    claims.get_claim(user_id, claim_id)
    offset = int(offset)
    if offset < 0 or offset > 500:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_meta_snapshots
            WHERE claim_id=%s AND user_id=%s
            ORDER BY created_at DESC,snapshot_id DESC LIMIT 21 OFFSET %s""",
            (str(claim_id), int(user_id), offset))
        rows = list(cur.fetchall())
    return {
        "meta_analyses": [_snapshot_row(row) for row in rows[:20]],
        "next_offset": offset + 20 if len(rows) > 20 else None,
    }


def mission_meta_evidence(user_id: int, mission_id: str) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    with projects.transaction() as cur:
        cur.execute("""SELECT DISTINCT ON (claim_id) * FROM velia_research_meta_snapshots
            WHERE mission_id=%s AND user_id=%s
            ORDER BY claim_id,created_at DESC,snapshot_id DESC""",
            (str(mission_id), int(user_id)))
        snapshots = [_snapshot_row(row) for row in cur.fetchall()]
    return {
        "snapshots": snapshots,
        "snapshot_ids": [item["id"] for item in snapshots],
        "evidence_hashes": [item["evidence_hash"] for item in snapshots],
        "boundary": (
            "Meta-analysis uses source-backed aggregate statistics. It may add caution or contradiction "
            "to the Claim Ledger but cannot promote a claim stage."
        ),
    }
