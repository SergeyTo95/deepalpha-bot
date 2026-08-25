from __future__ import annotations

import json

from services.velia_admin_security_service import configured_admin_id
from services import velia_agent_coding_autopilot_ci_service as ci
from services import velia_software_factory_stage3_hardening_patch as stage3
from services import velia_software_factory_admin_acceptance_runtime_patch as acceptance_runtime
from services import velia_software_factory_admin_acceptance_service as acceptance

FACTORY_RUN_ID = "c34348c7-2012-45bd-bfd5-860ae044c1bc"
REPOSITORY = "SergeyTo95/deepalpha-bot"
EXPECTED_PR = 527
EXPECTED_FINAL_HEAD = "9c8865dbb02c39c71c4d52d8897b92c61a232f11"


def main() -> int:
    # Install the same read/review runtime wiring as production bootstrap. This
    # probe is read-only: it only inspects the persisted acceptance certificate.
    stage3.install()
    ci.install_ci_repair_loop()
    acceptance_runtime.install()

    actor = int(configured_admin_id() or 0)
    if actor <= 0:
        raise RuntimeError("stage67_certificate_admin_missing")

    inspected = acceptance.inspect_acceptance(actor, FACTORY_RUN_ID, REPOSITORY)
    certificate = dict(inspected.get("certificate") or {})
    evidence = dict(inspected.get("evidence") or {})
    grant = dict(inspected.get("grant") or {})

    payload = {
        "factory_run_id": FACTORY_RUN_ID,
        "acceptance_id": str(certificate.get("acceptance_id") or ""),
        "certificate_id": str(certificate.get("certificate_id") or ""),
        "issued": bool(certificate.get("issued")),
        "outcome": str(certificate.get("outcome") or ""),
        "acceptance_passed": bool(certificate.get("acceptance_passed")),
        "read_only": bool(certificate.get("read_only")),
        "merge_authority": bool(certificate.get("merge_authority")),
        "deployment_authority": bool(certificate.get("deployment_authority")),
        "grant_status": str(grant.get("status") or ""),
        "run_status": str(evidence.get("run_status") or ""),
        "reviewer_status": str(evidence.get("reviewer_status") or ""),
        "reviewed_head_sha": str(evidence.get("reviewed_head_sha") or ""),
        "remediation_phase": str(evidence.get("remediation_phase") or ""),
        "remediation_attempt_count": int(evidence.get("remediation_attempt_count") or 0),
        "pull_request_number": int(evidence.get("pull_request_number") or 0),
    }
    print("STAGE67_CERTIFICATE " + json.dumps(payload, sort_keys=True), flush=True)

    required = {
        "issued": payload["issued"] is True,
        "outcome": payload["outcome"] == "passed",
        "acceptance_passed": payload["acceptance_passed"] is True,
        "read_only": payload["read_only"] is True,
        "merge_authority": payload["merge_authority"] is False,
        "deployment_authority": payload["deployment_authority"] is False,
        "grant_status": payload["grant_status"] == "consumed",
        "run_status": payload["run_status"] == "ready_for_review",
        "reviewer_status": payload["reviewer_status"] == "passed",
        "reviewed_head_sha": payload["reviewed_head_sha"] == EXPECTED_FINAL_HEAD,
        "remediation_phase": payload["remediation_phase"] == "completed",
        "remediation_attempt_count": payload["remediation_attempt_count"] >= 1,
        "pull_request_number": payload["pull_request_number"] == EXPECTED_PR,
        "certificate_id": bool(payload["certificate_id"]),
    }
    failed = [name for name, ok in required.items() if not ok]
    if failed:
        print("STAGE67_CERTIFICATE_FAILED " + json.dumps({"failed": failed}, sort_keys=True), flush=True)
        return 2
    print("STAGE67_CERTIFICATE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
