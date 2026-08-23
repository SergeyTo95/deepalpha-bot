from __future__ import annotations

from typing import Any, Mapping

from services.velia_software_factory_core_service import SoftwareFactoryError

_INSTALLED = False


def install(completion_module: Any) -> None:
    global _INSTALLED
    if getattr(completion_module, "_release_completion_hardening_installed", False):
        return

    original_build = completion_module.build_completion_snapshot

    def evaluate_acceptance_contexts(
        profile: Mapping[str, Any], status_snapshot: Mapping[str, Any]
    ):
        expected = completion_module._normalize_contexts(
            profile.get("expected_contexts") or []
        )
        statuses = {
            str(item.get("context") or ""): dict(item)
            for item in status_snapshot.get("statuses") or []
            if isinstance(item, Mapping) and str(item.get("context") or "").strip()
        }
        matched = []
        missing = []
        failing = []
        waiting = []
        invalid_targets = []
        railway_targets = []
        railway_hosts = set(completion_module.status_github._RAILWAY_HOSTS)
        for context in expected:
            item = statuses.get(context)
            if item is None:
                missing.append(context)
                continue
            state = str(item.get("state") or "").strip().lower()
            target_url = str(item.get("target_url") or "")[:1000]
            target_host = completion_module.status_github._target_host(target_url)
            matched.append(
                {
                    "context": context,
                    "state": state,
                    "description": str(item.get("description") or "")[:500],
                    "target_url": target_url,
                    "target_host": target_host,
                    "updated_at": str(item.get("updated_at") or "")[:80],
                }
            )
            if not target_host:
                invalid_targets.append(context)
                continue
            if target_host in railway_hosts:
                railway_targets.append(context)
                continue
            if state in {"failure", "error"}:
                failing.append(context)
            elif state != "success":
                waiting.append(context)
        if invalid_targets or railway_targets or failing:
            status = "failed"
        elif missing or waiting:
            status = "pending"
        else:
            status = "success"
        return {
            "status": status,
            "expected_contexts": expected,
            "matched_contexts": matched,
            "missing_contexts": missing,
            "failing_contexts": failing,
            "waiting_contexts": waiting,
            "invalid_target_contexts": invalid_targets,
            "railway_target_contexts": railway_targets,
        }

    def validate_deployment_evidence(
        execution_module: Any,
        user_id: int,
        verification: Mapping[str, Any],
        observation: Mapping[str, Any],
    ) -> None:
        verified = [
            dict(item)
            for item in verification.get("verified_merges") or []
            if isinstance(item, Mapping)
        ]
        observed = [
            dict(item)
            for item in observation.get("repositories") or []
            if isinstance(item, Mapping)
        ]
        if not verified:
            raise SoftwareFactoryError(
                "velia_factory_release_completion_verified_merges_missing", status=409
            )
        if len(observed) != len(verified):
            raise SoftwareFactoryError(
                "velia_factory_release_completion_deployment_evidence_incomplete",
                detail=f"verified={len(verified)} observed={len(observed)}",
                status=409,
            )
        by_project = {}
        for item in observed:
            project_id = str(item.get("project_id") or "")
            if not project_id or project_id in by_project:
                raise SoftwareFactoryError(
                    "velia_factory_release_completion_deployment_evidence_ambiguous",
                    detail=project_id,
                    status=409,
                )
            by_project[project_id] = item
        for item in verified:
            project_id = str(item.get("project_id") or "")
            evidence = by_project.get(project_id)
            if evidence is None:
                raise SoftwareFactoryError(
                    "velia_factory_release_completion_deployment_evidence_missing",
                    detail=project_id,
                    status=409,
                )
            if str(evidence.get("status") or "") != "success":
                raise SoftwareFactoryError(
                    "velia_factory_release_completion_deployment_repository_not_success",
                    detail=project_id,
                    status=409,
                )
            expected_repo = str(item.get("repository_full_name") or "").casefold()
            actual_repo = str(evidence.get("repository_full_name") or "").casefold()
            expected_branch = str(item.get("base_branch") or "")
            actual_branch = str(evidence.get("branch") or "")
            expected_sha = str(item.get("merge_commit_sha") or "").lower()
            actual_sha = str(evidence.get("merge_commit_sha") or "").lower()
            if (
                not expected_repo
                or expected_repo != actual_repo
                or not expected_branch
                or expected_branch != actual_branch
                or not expected_sha
                or expected_sha != actual_sha
            ):
                raise SoftwareFactoryError(
                    "velia_factory_release_completion_deployment_identity_mismatch",
                    detail=project_id,
                    status=409,
                )
            current_profile = completion_module.deployment.get_profile(
                execution_module,
                int(user_id),
                project_id,
                expected_branch,
                require_enabled=True,
            )
            current_fp = str(current_profile.get("profile_fingerprint") or "")
            evidence_fp = str(evidence.get("profile_fingerprint") or "")
            if not current_fp or current_fp != evidence_fp:
                raise SoftwareFactoryError(
                    "velia_factory_release_completion_deployment_profile_stale",
                    detail=project_id,
                    status=409,
                )

    def build_completion_snapshot(
        execution_module: Any,
        user_id: int,
        verification_id: str,
        deployment_observation_id: str,
    ):
        completion_module._require_user(int(user_id))
        verification = completion_module.post_merge.get_verification(
            execution_module, int(user_id), str(verification_id)
        )
        if str(verification.get("verification_status") or "") != "verified":
            raise SoftwareFactoryError(
                "velia_factory_release_completion_requires_full_verified_release",
                detail=str(verification.get("verification_status") or ""),
                status=409,
            )
        observation = completion_module.deployment.get_observation(
            execution_module, int(user_id), str(deployment_observation_id)
        )
        if str(observation.get("verification_id") or "") != str(verification_id):
            raise SoftwareFactoryError(
                "velia_factory_release_completion_deployment_verification_mismatch",
                status=409,
            )
        if (
            str(observation.get("status") or "") != "success"
            or not bool(observation.get("deployment_complete"))
        ):
            raise SoftwareFactoryError(
                "velia_factory_release_completion_deployment_not_complete",
                detail=str(observation.get("status") or ""),
                status=409,
            )
        validate_deployment_evidence(
            execution_module, int(user_id), verification, observation
        )
        return original_build(
            execution_module,
            int(user_id),
            str(verification_id),
            str(deployment_observation_id),
        )

    completion_module._evaluate_acceptance_contexts = evaluate_acceptance_contexts
    completion_module._validate_deployment_evidence = validate_deployment_evidence
    completion_module.build_completion_snapshot = build_completion_snapshot
    completion_module._release_completion_hardening_installed = True
    _INSTALLED = True
