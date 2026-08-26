from __future__ import annotations

from typing import Any, Dict


def install(runtime_module: Any) -> None:
    if getattr(runtime_module, "_stage8_release_hardening_installed", False):
        return

    def progress_release(execution_module: Any, user_id: int, execution_id: str) -> Dict[str, Any]:
        from services import velia_software_factory_rollout_service as rollout
        from services import velia_software_factory_stage8_full_autonomy_service as stage8

        if rollout.rollout_mode() != rollout.ROLLOUT_FULL_AUTONOMY:
            return {"status": "inactive", "execution_id": str(execution_id)}
        if not rollout.user_allowed(int(user_id)):
            return {"status": "forbidden", "execution_id": str(execution_id)}
        if not stage8.execution_allowed(int(user_id), user_eligible=True):
            return {"status": "not_ready", "execution_id": str(execution_id)}

        current = execution_module.get_execution(int(user_id), str(execution_id))
        if str(current.get("status") or "") != "review_ready":
            return {"status": "not_review_ready", "execution_id": str(execution_id)}
        runtime_module._assert_integration_passed(current)
        state = runtime_module._state(execution_module, int(user_id), str(execution_id))
        if state.get("status") == "complete" and state.get("passport_id"):
            return {**state, "execution_id": str(execution_id)}

        candidate = (
            execution_module.get_delivery_candidate(int(user_id), state["candidate_id"])
            if state.get("candidate_id")
            else execution_module.evaluate_delivery_candidate(int(user_id), str(execution_id))
        )
        if str(candidate.get("status") or "") != "eligible" or not bool(candidate.get("release_eligible")):
            return runtime_module._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                candidate_id=str(candidate.get("candidate_id") or ""),
                status="blocked",
                blocker_code="velia_factory_stage8_candidate_not_eligible",
            )
        runtime_module._assert_repository_scope(int(user_id), candidate)
        candidate_id = str(candidate.get("candidate_id") or "")
        state = runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            candidate_id=candidate_id,
            blocker_code="",
            blocker_detail="",
        )

        # Approval and preflight are immutable evidence. Once a release execution
        # exists, never re-evaluate them after the PR has been merged; resume from
        # the persisted release execution instead.
        if state.get("release_execution_id"):
            release = execution_module.get_release_execution(
                int(user_id), state["release_execution_id"]
            )
        else:
            if state.get("plan_id"):
                plan = execution_module.get_release_preflight(int(user_id), state["plan_id"])
            else:
                execution_module.record_delivery_decision(
                    int(user_id),
                    candidate_id,
                    "approved",
                    note="stage8_full_autonomy_delegated_auto_approval_after_reviewer",
                )
                plan = execution_module.prepare_release_preflight(int(user_id), candidate_id)
                state = runtime_module._save_state(
                    execution_module,
                    int(user_id),
                    str(execution_id),
                    plan_id=str(plan.get("plan_id") or ""),
                    status="preflight_prepared",
                )
            plan_id = str(plan.get("plan_id") or state.get("plan_id") or "")
            execution_module.validate_release_preflight(int(user_id), plan_id)
            release = execution_module.create_release_execution(int(user_id), plan_id)
            state = runtime_module._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                release_execution_id=str(release.get("execution_id") or ""),
                status="merging",
            )

        release_id = str(release.get("execution_id") or state.get("release_execution_id") or "")
        release = execution_module.execute_release(int(user_id), release_id)
        release_status = str(release.get("status") or "")
        if release_status != "completed":
            return runtime_module._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                release_execution_id=release_id,
                status="release_" + (release_status or "unknown"),
                blocker_code=str(release.get("error_code") or ""),
                blocker_detail=str(release.get("error_detail") or ""),
            )

        state = runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            release_execution_id=release_id,
            status="merged",
            blocker_code="",
            blocker_detail="",
        )
        verification = (
            execution_module.get_release_verification(int(user_id), state["verification_id"])
            if state.get("verification_id")
            else execution_module.verify_release_execution(int(user_id), release_id)
        )
        verification_id = str(verification.get("verification_id") or "")
        if str(verification.get("verification_status") or "") != "verified":
            return runtime_module._save_state(
                execution_module,
                int(user_id),
                str(execution_id),
                verification_id=verification_id,
                status="verification_failed",
                blocker_code="velia_factory_stage8_release_verification_failed",
            )

        state = runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            verification_id=verification_id,
            status="deployment_observing",
        )
        observation = execution_module.observe_release_deployment(int(user_id), verification_id)
        observation_id = str(observation.get("observation_id") or "")
        observation_status = str(observation.get("status") or "")
        state = runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            observation_id=observation_id,
            status="deployment_" + (observation_status or "unknown"),
        )
        if observation_status != "success" or not bool(observation.get("deployment_complete")):
            return state

        certificate = execution_module.evaluate_release_completion(
            int(user_id), verification_id, observation_id
        )
        certificate_id = str(certificate.get("certificate_id") or "")
        certificate_status = str(certificate.get("status") or "")
        state = runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            certificate_id=certificate_id,
            status="acceptance_" + (certificate_status or "unknown"),
        )
        if certificate_status != "complete" or not bool(certificate.get("release_complete")):
            return state

        passport = execution_module.create_release_passport(int(user_id), certificate_id)
        return runtime_module._save_state(
            execution_module,
            int(user_id),
            str(execution_id),
            passport_id=str(passport.get("passport_id") or ""),
            status="complete",
            blocker_code="",
            blocker_detail="",
        )

    runtime_module._progress_release = progress_release
    runtime_module._stage8_release_hardening_installed = True
