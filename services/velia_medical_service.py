"""VELIA Medical Intelligence control-plane state.

Raw medical images are never persisted in PostgreSQL. Railway stores only
owner-scoped job metadata and structured model output. The GPU worker is a
separate, explicitly configured data plane.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Dict, Optional

from services import velia_project_service as projects


RADAR_UPSTREAM_COMMIT = "9319f36642b6f3f4708c8e5c8844ab114d6e7b24"
RADAR_CODE_LICENSE = "Apache-2.0"
RADAR_WEIGHTS_LICENSE = "CC-BY-NC-SA-4.0"
ALLOWED_MODALITIES = {"ct"}
ALLOWED_STUDY_KINDS = {"contrast_abdomen"}
CASE_STATES = {"created", "uploading", "queued", "running", "completed", "failed", "cancelled"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean(value: Any, limit: int, code: str) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise projects.ProjectError(code)
    return re.sub(r"\s+", " ", value).strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def enabled() -> bool:
    return _env_bool("VELIA_MEDICAL_ENABLED", False)


def radar_noncommercial_acknowledged() -> bool:
    return _env_bool("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", False)


def provider() -> str:
    value = str(os.getenv("VELIA_MEDICAL_PROVIDER", "radar") or "radar").strip().lower()
    return value if value in {"radar"} else ""


def worker_configured() -> bool:
    return bool(
        str(os.getenv("VELIA_MEDICAL_WORKER_BASE_URL", "") or "").strip()
        and str(os.getenv("VELIA_MEDICAL_WORKER_AUTH_TOKEN", "") or "").strip()
    )


def radar_available() -> bool:
    return (
        enabled()
        and provider() == "radar"
        and radar_noncommercial_acknowledged()
        and worker_configured()
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "provider": provider(),
        "raw_medical_data_persisted_on_railway": False,
        "direct_android_to_gpu": False,
        "clinical_role": "decision_support_research",
        "definitive_diagnosis": False,
        "supported_modalities": [
            {
                "modality": "ct",
                "study_kind": "contrast_abdomen",
                "provider": "radar",
                "enabled": radar_available(),
                "findings": 146,
                "score_semantics": "model_score_not_calibrated_probability",
            }
        ],
        "unsupported_by_radar": ["xray", "mri", "ultrasound", "laboratory_results"],
        "radar": {
            "upstream_commit": RADAR_UPSTREAM_COMMIT,
            "code_license": RADAR_CODE_LICENSE,
            "weights_license": RADAR_WEIGHTS_LICENSE,
            "commercial_use_allowed_by_public_weights": False,
            "noncommercial_license_acknowledged": radar_noncommercial_acknowledged(),
            "worker_configured": worker_configured(),
            "available": radar_available(),
        },
        "safety": {
            "patient_data_logged": False,
            "raw_images_in_database": False,
            "arbitrary_code_execution": False,
            "arbitrary_model_url": False,
            "treatment_prescribing": False,
            "clinician_confirmation_required": True,
        },
    }


def ensure_tables() -> None:
    if not projects.ready():
        projects.ensure_tables()
    with projects.transaction() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS velia_medical_cases (
                case_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                project_id TEXT NULL,
                research_mission_id TEXT NULL,
                modality TEXT NOT NULL,
                study_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_job_id TEXT NULL,
                input_sha256 TEXT NULL,
                result_json TEXT NULL,
                error_code TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(case_id,user_id),
                CHECK(status IN ('created','uploading','queued','running','completed','failed','cancelled')),
                CHECK(modality IN ('ct')),
                CHECK(study_kind IN ('contrast_abdomen'))
            )"""
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_medical_owner_created "
            "ON velia_medical_cases(user_id,created_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_medical_provider_job "
            "ON velia_medical_cases(provider_job_id) WHERE provider_job_id IS NOT NULL"
        )


def _case(row: Dict[str, Any]) -> Dict[str, Any]:
    result = json.loads(row["result_json"]) if row.get("result_json") else None
    return {
        "id": row["case_id"],
        "project_id": row.get("project_id"),
        "research_mission_id": row.get("research_mission_id"),
        "modality": row["modality"],
        "study_kind": row["study_kind"],
        "title": row["title"],
        "status": row["status"],
        "provider": row["provider"],
        "provider_job_id": row.get("provider_job_id"),
        "input_sha256": row.get("input_sha256"),
        "result": result,
        "error": row.get("error_code"),
        "created_at": projects._iso(row["created_at"]) if hasattr(projects, "_iso") else str(row["created_at"]),
        "updated_at": projects._iso(row["updated_at"]) if hasattr(projects, "_iso") else str(row["updated_at"]),
    }


def create_case(user_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("medical_disabled", 503)
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_request")
    modality = _clean(data.get("modality", ""), 32, "invalid_medical_modality").lower()
    study_kind = _clean(data.get("study_kind", ""), 64, "invalid_medical_study_kind").lower()
    title = _clean(data.get("title", ""), 160, "invalid_title") or "Abdominal CT"
    project_id = data.get("project_id") or None
    if modality not in ALLOWED_MODALITIES or study_kind not in ALLOWED_STUDY_KINDS:
        raise projects.ProjectError("medical_modality_not_supported", 422)
    if provider() != "radar":
        raise projects.ProjectError("medical_provider_not_configured", 503)
    if not radar_noncommercial_acknowledged():
        raise projects.ProjectError("radar_weights_license_not_acknowledged", 503)
    if not worker_configured():
        raise projects.ProjectError("medical_worker_not_configured", 503)
    case_id = str(uuid.uuid4())
    with projects.transaction(user_id) as cur:
        if project_id:
            projects._owned(cur, int(user_id), str(project_id))
        cur.execute(
            """INSERT INTO velia_medical_cases(
                case_id,user_id,project_id,modality,study_kind,title,status,provider
            ) VALUES(%s,%s,%s,%s,%s,%s,'created','radar') RETURNING *""",
            (case_id, int(user_id), project_id, modality, study_kind, title),
        )
        return _case(cur.fetchone())


def get_case(user_id: int, case_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute(
            "SELECT * FROM velia_medical_cases WHERE case_id=%s AND user_id=%s",
            (str(case_id), int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("medical_case_not_found", 404)
        return _case(row)


def list_cases(user_id: int, offset: int = 0) -> Dict[str, Any]:
    offset = int(offset)
    if offset < 0 or offset > 1000:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute(
            """SELECT * FROM velia_medical_cases WHERE user_id=%s
               ORDER BY created_at DESC,case_id DESC LIMIT 31 OFFSET %s""",
            (int(user_id), offset),
        )
        rows = list(cur.fetchall())
    return {
        "cases": [_case(row) for row in rows[:30]],
        "next_offset": offset + 30 if len(rows) > 30 else None,
    }


def mark_uploading(user_id: int, case_id: str) -> Dict[str, Any]:
    with projects.transaction(user_id) as cur:
        cur.execute(
            """UPDATE velia_medical_cases SET status='uploading',error_code=NULL,updated_at=NOW()
               WHERE case_id=%s AND user_id=%s AND status IN ('created','failed')
               RETURNING *""",
            (str(case_id), int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            current = get_case(user_id, case_id)
            raise projects.ProjectError(
                "medical_case_not_uploadable" if current["status"] != "uploading" else "medical_upload_in_progress",
                409,
            )
        return _case(row)


def mark_queued(
    user_id: int,
    case_id: str,
    *,
    provider_job_id: str,
    input_sha256: str,
) -> Dict[str, Any]:
    digest = str(input_sha256 or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise projects.ProjectError("invalid_medical_input_hash", 502)
    with projects.transaction(user_id) as cur:
        cur.execute(
            """UPDATE velia_medical_cases
               SET status='queued',provider_job_id=%s,input_sha256=%s,error_code=NULL,updated_at=NOW()
               WHERE case_id=%s AND user_id=%s AND status='uploading' RETURNING *""",
            (str(provider_job_id)[:160], digest, str(case_id), int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("medical_case_state_conflict", 409)
        return _case(row)


def reconcile_worker_result(user_id: int, case_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise projects.ProjectError("medical_worker_invalid_response", 502)
    state = str(payload.get("status") or "").strip().lower()
    if state not in {"queued", "running", "completed", "failed"}:
        raise projects.ProjectError("medical_worker_invalid_response", 502)
    result = payload.get("result")
    error = str(payload.get("error") or "")[:160] or None
    if state == "completed":
        validate_result(result)
    encoded = _json(result) if result is not None else None
    if encoded and len(encoded) > 250_000:
        raise projects.ProjectError("medical_result_too_large", 502)
    with projects.transaction(user_id) as cur:
        cur.execute(
            """UPDATE velia_medical_cases
               SET status=%s,result_json=%s,error_code=%s,updated_at=NOW()
               WHERE case_id=%s AND user_id=%s RETURNING *""",
            (state, encoded, error, str(case_id), int(user_id)),
        )
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("medical_case_not_found", 404)
        return _case(row)


def validate_result(result: Any) -> None:
    if not isinstance(result, dict):
        raise projects.ProjectError("medical_worker_invalid_result", 502)
    if result.get("provider") != "radar":
        raise projects.ProjectError("medical_worker_invalid_result", 502)
    if result.get("score_semantics") != "model_score_not_calibrated_probability":
        raise projects.ProjectError("medical_worker_invalid_result", 502)
    findings = result.get("findings")
    if not isinstance(findings, list) or len(findings) > 146:
        raise projects.ProjectError("medical_worker_invalid_result", 502)
    for item in findings:
        if not isinstance(item, dict):
            raise projects.ProjectError("medical_worker_invalid_result", 502)
        organ = str(item.get("organ") or "")
        label = str(item.get("finding") or "")
        score = item.get("score")
        if not organ or not label or len(organ) > 120 or len(label) > 220:
            raise projects.ProjectError("medical_worker_invalid_result", 502)
        if not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
            raise projects.ProjectError("medical_worker_invalid_result", 502)


def attach_research_mission(user_id: int, case_id: str) -> Dict[str, Any]:
    case = get_case(user_id, case_id)
    if case["status"] != "completed" or not case.get("result"):
        raise projects.ProjectError("medical_case_not_completed", 409)
    if case.get("research_mission_id"):
        return case
    findings = case["result"].get("findings") or []
    top = sorted(findings, key=lambda item: float(item.get("score") or 0), reverse=True)[:8]
    summary = "; ".join(
        f"{item.get('organ')}: {item.get('finding')} (RADAR model score {float(item.get('score') or 0):.3f})"
        for item in top
    )
    goal = (
        "Review the scientific and clinical evidence relevant to these abdominal CT model findings. "
        "Treat the RADAR values as uncalibrated model scores, not diagnoses or probabilities. "
        "Identify supporting and conflicting evidence, important differential considerations, "
        "limitations, and what a qualified radiologist/physician should verify. Findings: " + summary
    )
    from services import velia_research_center_service as research
    mission = research.create_mission(
        int(user_id),
        {
            "goal": goal,
            "title": "Medical evidence review: " + case["title"][:80],
            "project_id": case.get("project_id"),
        },
        "medical-case:" + str(case_id),
    )
    with projects.transaction(user_id) as cur:
        cur.execute(
            """UPDATE velia_medical_cases SET research_mission_id=%s,updated_at=NOW()
               WHERE case_id=%s AND user_id=%s RETURNING *""",
            (mission["id"], str(case_id), int(user_id)),
        )
        return _case(cur.fetchone())
