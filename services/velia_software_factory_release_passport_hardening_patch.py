from __future__ import annotations

import re
from typing import Any, Mapping

from services.velia_software_factory_core_service import SoftwareFactoryError

_INSTALLED = False
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def install(passport_module: Any) -> None:
    global _INSTALLED
    if getattr(passport_module, "_release_passport_hardening_installed", False):
        return

    original_build = passport_module.build_passport_snapshot

    def _hash(value: Any, code: str) -> str:
        text = str(value or "").strip().lower()
        if not _HASH_RE.fullmatch(text):
            raise SoftwareFactoryError(code, detail=text[:80], status=409)
        return text

    def build_passport_snapshot(execution_module: Any, user_id: int, certificate_id: str):
        snapshot = original_build(execution_module, int(user_id), str(certificate_id))
        chain = snapshot.get("evidence_chain")
        if not isinstance(chain, Mapping):
            raise SoftwareFactoryError("velia_factory_release_passport_chain_missing", status=409)
        chain = dict(chain)
        candidate_evidence = chain.get("candidate") if isinstance(chain.get("candidate"), Mapping) else {}
        candidate_id = str(candidate_evidence.get("candidate_id") or "")
        source_id = str(candidate_evidence.get("source_id") or "")
        candidate = passport_module.delivery.get_candidate(
            execution_module, int(user_id), candidate_id
        )
        if str(candidate.get("source_id") or "") != source_id:
            raise SoftwareFactoryError(
                "velia_factory_release_passport_source_execution_mismatch", status=409
            )
        source_execution = execution_module.get_execution(int(user_id), source_id)
        if str(source_execution.get("execution_id") or "") != source_id:
            raise SoftwareFactoryError(
                "velia_factory_release_passport_source_execution_mismatch", status=409
            )
        if str(source_execution.get("status") or "") != "review_ready":
            raise SoftwareFactoryError(
                "velia_factory_release_passport_source_not_review_ready",
                detail=str(source_execution.get("status") or ""),
                status=409,
            )
        source_plan_fp = str(source_execution.get("plan_fingerprint") or "")
        candidate_plan_fp = str(candidate.get("plan_fingerprint") or "")
        if not source_plan_fp or source_plan_fp != candidate_plan_fp:
            raise SoftwareFactoryError(
                "velia_factory_release_passport_source_plan_fingerprint_mismatch",
                status=409,
            )
        _hash(candidate.get("source_fingerprint"), "velia_factory_release_passport_source_fingerprint_invalid")
        release_evidence = chain.get("release_execution") if isinstance(chain.get("release_execution"), Mapping) else {}
        repositories = chain.get("repositories") if isinstance(chain.get("repositories"), list) else []
        if int(release_evidence.get("merged_count") or 0) != len(repositories):
            raise SoftwareFactoryError(
                "velia_factory_release_passport_merged_count_mismatch",
                detail=f"merged={int(release_evidence.get('merged_count') or 0)} repos={len(repositories)}",
                status=409,
            )
        for section, field, code in (
            ("preflight", "plan_fingerprint", "velia_factory_release_passport_plan_fingerprint_invalid"),
            ("post_merge_verification", "verification_fingerprint", "velia_factory_release_passport_verification_fingerprint_invalid"),
            ("deployment_observation", "observation_fingerprint", "velia_factory_release_passport_observation_fingerprint_invalid"),
            ("completion_certificate", "certificate_fingerprint", "velia_factory_release_passport_certificate_fingerprint_invalid"),
        ):
            evidence = chain.get(section) if isinstance(chain.get(section), Mapping) else {}
            _hash(evidence.get(field), code)
        source_plan = source_execution.get("plan") if isinstance(source_execution.get("plan"), Mapping) else {}
        source_summary = {
            "execution_id": source_id,
            "workspace_id": str(source_execution.get("workspace_id") or ""),
            "status": "review_ready",
            "plan_fingerprint": source_plan_fp,
            "objective": str(source_plan.get("objective") or "")[:4000],
            "acceptance_criteria": list(source_plan.get("acceptance_criteria") or [])[:50],
            "task_count": len(source_plan.get("tasks") or []),
            "created_at": str(source_execution.get("created_at") or ""),
            "updated_at": str(source_execution.get("updated_at") or ""),
        }
        hardened_chain = {
            "workspace_execution": source_summary,
            **chain,
        }
        evidence_chain_hash = passport_module._fingerprint(hardened_chain)
        result = dict(snapshot)
        result["evidence_chain"] = hardened_chain
        result["evidence_chain_hash"] = evidence_chain_hash
        result["passport_fingerprint"] = passport_module._fingerprint(
            {
                "certificate_id": str(result.get("certificate_id") or ""),
                "release_execution_id": str(result.get("release_execution_id") or ""),
                "workspace_execution_id": source_id,
                "evidence_chain_hash": evidence_chain_hash,
                "repository_count": len(repositories),
            }
        )
        result["workspace_execution_id"] = source_id
        result["workspace_id"] = str(source_execution.get("workspace_id") or "")
        return result

    passport_module.build_passport_snapshot = build_passport_snapshot
    passport_module._release_passport_hardening_installed = True
    _INSTALLED = True
