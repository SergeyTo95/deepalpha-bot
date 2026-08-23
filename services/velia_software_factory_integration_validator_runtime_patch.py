from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Dict, Mapping, Optional

from db.database import get_connection
from services import velia_software_factory_integration_validator_service as validator
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_622
_INSTALLED = False


def _json(value: Any, limit: int = 120000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def _configure_llm_feature() -> None:
    features = {"software_factory_integration_validator": "GEMINI_ENABLED"}
    try:
        from services import gemini_budget_guard, llm_service
        gemini_budget_guard.FEATURE_FLAGS.update(features)
        llm_service._FEATURE_PROVIDER_ENV.update(features)
    except Exception:
        logger.exception("VELIA_SOFTWARE_FACTORY_INTEGRATION_LLM_FEATURE_PATCH_FAILED")


def ensure_integration_tables(execution_module: Any) -> None:
    global _SCHEMA_READY
    execution_module.ensure_workspace_execution_tables()
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_software_factory_workspace_integration_validations (
                    validation_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL REFERENCES velia_software_factory_workspace_executions(execution_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL DEFAULT '',
                    report_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('pending','passed','failed','blocked'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_workspace_integration_validation "
                "ON velia_software_factory_workspace_integration_validations(execution_id,user_id,created_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def latest_validation(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
    ensure_integration_tables(execution_module)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT validation_id,status,contract_fingerprint,report_json,created_at "
            "FROM velia_software_factory_workspace_integration_validations "
            "WHERE execution_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1",
            (str(execution_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            "validation_id": str(_value(row, "validation_id", 0, "")),
            "status": str(_value(row, "status", 1, "")),
            "contract_fingerprint": str(_value(row, "contract_fingerprint", 2, "")),
            "report": _loads(_value(row, "report_json", 3, "{}"), {}),
            "created_at": str(_value(row, "created_at", 4, "") or ""),
        }
    finally:
        cursor.close()
        conn.close()


def _store_validation(execution_module: Any, execution: Mapping[str, Any], report: Mapping[str, Any]) -> Dict[str, Any]:
    ensure_integration_tables(execution_module)
    validation_id = str(uuid.uuid4())
    status = str(report.get("status") or "blocked")
    if status not in {"pending", "passed", "failed", "blocked"}:
        status = "blocked"
    fingerprint = str(report.get("contract_fingerprint") or (execution.get("plan") or {}).get("integration_contract_fingerprint") or "")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO velia_software_factory_workspace_integration_validations "
            "(validation_id,execution_id,user_id,status,contract_fingerprint,report_json,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                validation_id,
                str(execution["execution_id"]),
                int(execution["user_id"]),
                status,
                fingerprint,
                _json(dict(report)),
                execution_module._utcnow(),
            ),
        )
        execution_module._append_event(
            cursor,
            str(execution["execution_id"]),
            int(execution["user_id"]),
            f"workspace_integration.{status}",
            {
                "validation_id": validation_id,
                "contract_fingerprint": fingerprint,
                "issues": list(report.get("issues") or [])[:20],
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return latest_validation(execution_module, int(execution["user_id"]), str(execution["execution_id"]))


def validate_and_store(execution_module: Any, execution: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        report = validator.validate_execution(execution)
    except SoftwareFactoryError as exc:
        report = {
            "status": "blocked",
            "execution_id": str(execution.get("execution_id") or ""),
            "contracts": [],
            "issues": [exc.code],
            "detail": exc.detail,
            "contract_fingerprint": str((execution.get("plan") or {}).get("integration_contract_fingerprint") or ""),
        }
    except Exception as exc:
        logger.exception("VELIA_WORKSPACE_INTEGRATION_VALIDATION_FAILED execution_id=%s", execution.get("execution_id"))
        report = {
            "status": "blocked",
            "execution_id": str(execution.get("execution_id") or ""),
            "contracts": [],
            "issues": ["velia_factory_integration_validator_internal_error"],
            "detail": exc.__class__.__name__,
            "contract_fingerprint": str((execution.get("plan") or {}).get("integration_contract_fingerprint") or ""),
        }
    return _store_validation(execution_module, execution, report)


def install(workspace_module: Any, execution_module: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_integration_validator_installed", False):
        return
    _configure_llm_feature()

    original_normalize_plan = workspace_module.normalize_workspace_plan
    original_create_execution = execution_module.create_execution
    original_get_execution = execution_module.get_execution
    original_set_execution_state = execution_module._set_execution_state
    original_resume_execution = getattr(execution_module, "resume_execution", None)

    def normalize_workspace_plan(raw_plan: Mapping[str, Any], workspace: Mapping[str, Any]) -> Dict[str, Any]:
        normalized = original_normalize_plan(raw_plan, workspace)
        return validator.normalize_plan_contracts(raw_plan, normalized)

    def create_execution(user_id: int, workspace_id: str, plan_payload: Mapping[str, Any]) -> Dict[str, Any]:
        if validator.integration_validator_enabled():
            workspace = workspace_module.get_workspace(int(user_id), str(workspace_id))
            plan = workspace_module.normalize_workspace_plan(plan_payload, workspace)
            if bool(plan.get("integration_required")) and not bool(plan.get("integration_ready")):
                first_issue = (plan.get("integration_contract_issues") or [{}])[0]
                raise SoftwareFactoryError(
                    "velia_factory_workspace_integration_contracts_required",
                    detail=str(first_issue.get("code") or "integration_contracts_incomplete"),
                    status=409,
                )
        return original_create_execution(int(user_id), str(workspace_id), plan_payload)

    def get_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
        result = original_get_execution(int(user_id), str(execution_id))
        result["integration_validator_enabled"] = validator.integration_validator_enabled()
        result["integration_validation"] = latest_validation(execution_module, int(user_id), str(execution_id))
        return result

    def set_execution_state(
        execution_id: str,
        user_id: int,
        status: str,
        blocker: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if str(status) != "review_ready" or not validator.integration_validator_enabled():
            original_set_execution_state(str(execution_id), int(user_id), str(status), blocker)
            return
        current = original_get_execution(int(user_id), str(execution_id))
        validation = validate_and_store(execution_module, current)
        report = validation.get("report") if isinstance(validation.get("report"), Mapping) else {}
        validation_status = str(report.get("status") or validation.get("status") or "blocked")
        if validation_status == "passed":
            original_set_execution_state(str(execution_id), int(user_id), "review_ready", {})
            return
        blocker_code = (
            "velia_factory_workspace_integration_validation_failed"
            if validation_status == "failed"
            else "velia_factory_workspace_integration_validation_blocked"
        )
        original_set_execution_state(
            str(execution_id),
            int(user_id),
            "blocked",
            {
                "code": blocker_code,
                "validation_id": str(validation.get("validation_id") or ""),
                "issues": list(report.get("issues") or [])[:20],
            },
        )

    def validate_integration(user_id: int, execution_id: str) -> Dict[str, Any]:
        if not validator.integration_validator_enabled():
            raise SoftwareFactoryError("velia_factory_integration_validator_disabled", status=503)
        current = original_get_execution(int(user_id), str(execution_id))
        return validate_and_store(execution_module, current)

    def resume_execution(user_id: int, execution_id: str) -> Dict[str, Any]:
        current = original_get_execution(int(user_id), str(execution_id))
        blocker = current.get("blocker") if isinstance(current.get("blocker"), Mapping) else {}
        if str(current.get("status") or "") == "blocked" and str(blocker.get("code") or "") in {
            "velia_factory_workspace_integration_validation_failed",
            "velia_factory_workspace_integration_validation_blocked",
        }:
            execution_module._require_live(int(user_id))
            original_set_execution_state(str(execution_id), int(user_id), "created", {})
            return execution_module.tick_execution(int(user_id), str(execution_id))
        if callable(original_resume_execution):
            return original_resume_execution(int(user_id), str(execution_id))
        raise SoftwareFactoryError("velia_factory_workspace_execution_not_resumable", status=409)

    workspace_module.normalize_workspace_plan = normalize_workspace_plan
    execution_module.create_execution = create_execution
    execution_module.get_execution = get_execution
    execution_module._set_execution_state = set_execution_state
    execution_module.validate_integration = validate_integration
    execution_module.latest_integration_validation = lambda user_id, execution_id: latest_validation(
        execution_module, int(user_id), str(execution_id)
    )
    execution_module.integration_validator_enabled = validator.integration_validator_enabled
    execution_module.resume_execution = resume_execution
    execution_module._workspace_integration_validator_installed = True
    _INSTALLED = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_INSTALLED enabled=%s",
        str(validator.integration_validator_enabled()).lower(),
    )
