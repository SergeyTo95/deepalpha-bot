from __future__ import annotations

from typing import Any, Mapping

from services.velia_software_factory_core_service import SoftwareFactoryError

_INSTALLED = False


def install(observer_module: Any) -> None:
    global _INSTALLED
    if getattr(observer_module, "_deployment_observer_hardening_installed", False):
        return

    original_build = observer_module.build_observation_snapshot

    def normalize_contexts(values):
        result = []
        seen = set()
        for raw in values or []:
            text = str(raw or "").replace("\x00", "").strip()
            if not text:
                continue
            if len(text) > 240:
                raise SoftwareFactoryError(
                    "velia_factory_deployment_context_too_long", status=400
                )
            if any(token in text for token in ("*", "?", "[", "]")):
                raise SoftwareFactoryError(
                    "velia_factory_deployment_context_must_be_exact",
                    detail=text[:240],
                    status=400,
                )
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        if not result:
            raise SoftwareFactoryError(
                "velia_factory_deployment_contexts_required", status=400
            )
        if len(result) > int(getattr(observer_module, "_MAX_CONTEXTS", 24) or 24):
            raise SoftwareFactoryError(
                "velia_factory_deployment_context_limit_exceeded",
                detail=str(len(result)),
                status=400,
            )
        return sorted(result, key=str.casefold)

    def observation_row(row):
        loads = observer_module._loads
        value = observer_module._value
        snapshot = loads(value(row, "snapshot_json", 6, "{}"), {})
        result = dict(snapshot) if isinstance(snapshot, dict) else {}
        result.update(
            {
                "observation_id": str(value(row, "observation_id", 0, "")),
                "verification_id": str(value(row, "verification_id", 1, "")),
                "release_execution_id": str(value(row, "release_execution_id", 2, "")),
                "user_id": int(value(row, "user_id", 3, 0) or 0),
                "observation_fingerprint": str(
                    value(row, "observation_fingerprint", 4, "")
                ),
                "status": str(
                    value(row, "status", 5, result.get("status") or "blocked")
                ),
                "created_at": str(value(row, "created_at", 7, "") or ""),
            }
        )
        return result

    def evaluate_expected_contexts(
        profile: Mapping[str, Any], status_snapshot: Mapping[str, Any]
    ):
        expected = normalize_contexts(profile.get("expected_contexts") or [])
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
        allowed_hosts = set(
            getattr(observer_module.status_github, "_RAILWAY_HOSTS", set()) or set()
        )
        for context in expected:
            item = statuses.get(context)
            if item is None:
                missing.append(context)
                continue
            state = str(item.get("state") or "").strip().lower()
            target_url = str(item.get("target_url") or "")[:1000]
            target_host = observer_module.status_github._target_host(target_url)
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
            if target_host not in allowed_hosts:
                invalid_targets.append(context)
                continue
            if state in {"failure", "error"}:
                failing.append(context)
            elif state != "success":
                waiting.append(context)
        if invalid_targets:
            status = "failed"
        elif failing:
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
        }

    def build_observation_snapshot(execution_module, user_id, verification_id):
        observer_module._require_user(int(user_id))
        verification = observer_module.post_merge.get_verification(
            execution_module, int(user_id), str(verification_id)
        )
        verification_status = str(verification.get("verification_status") or "")
        if verification_status not in observer_module._VERIFICATION_STATES:
            raise SoftwareFactoryError(
                "velia_factory_deployment_requires_verified_release",
                detail=verification_status,
                status=409,
            )
        verified_merges = [
            item
            for item in verification.get("verified_merges") or []
            if isinstance(item, Mapping)
        ]
        if not verified_merges:
            raise SoftwareFactoryError(
                "velia_factory_deployment_verified_merges_missing", status=409
            )
        return original_build(execution_module, int(user_id), str(verification_id))

    observer_module._normalize_contexts = normalize_contexts
    observer_module._observation_row = observation_row
    observer_module._evaluate_expected_contexts = evaluate_expected_contexts
    observer_module.build_observation_snapshot = build_observation_snapshot
    observer_module._deployment_observer_hardening_installed = True
    _INSTALLED = True
