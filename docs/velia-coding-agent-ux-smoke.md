# VELIA Coding Agent — UX Smoke Test Guide

This document describes how to smoke-test the user-facing behavior of the VELIA Coding Agent ("coding autopilot") feature. It is a documentation-only reference; it does not change any code.

Every behavioral claim below is mapped to a concrete code path in the current source. Items that could not be verified from code are listed under [Open verification items](#open-verification-items) and must be confirmed before being documented as fact.

## Prerequisites and feature gating

The coding agent is opt-in and disabled by default. All of the following must be true for any autopilot route to respond successfully:

1. The mobile API surface must be available (`_mobile_api_available()`), otherwise routes return the disabled response.
2. The developer feature must be enabled (`project_service.developer_enabled()`), otherwise routes return `503` with error `velia_developer_disabled`.
3. The coding feature must be enabled (`coding_service.coding_enabled()`), otherwise routes return `503` with error `velia_developer_coding_disabled`.
4. The autopilot feature must be enabled (`autopilot.autopilot_enabled()`), otherwise routes return `503` with error `velia_coding_autopilot_disabled`.

Feature flags (environment variables):

| Flag | Default | Effect |
| --- | --- | --- |
| `VELIA_DEVELOPER_AUTOPILOT_ENABLED` | off | Enables the autopilot API surface. |
| `VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED` | off | Enables the background worker; requires the main flag. |

Source: `services/velia_agent_coding_autopilot_service.py` (`autopilot_enabled`, `worker_enabled`, `_env_bool`) and `services/velia_agent_coding_autopilot_routes.py` (`_require_available`).

Smoke check: with the flags unset, every autopilot endpoint must return a clean `503` JSON error (not a 500, not a hang). With the flags set, the same endpoints must pass the availability gate.

## API surface and error contract

All autopilot routes live under the prefix:

```
/mobile-api/v1/developer/autopilot
```

Requests require mobile authentication (`_require_mobile_auth`).

Error responses follow a consistent typed contract:

```json
{
  "ok": false,
  "error": "<machine-readable error code>",
  "detail": "<optional short detail, truncated to 500 chars>"
}
```

Known domain exceptions mapped to this contract:

- `CodingAutopilotError` (autopilot service)
- `CodingAutopilotPolicyError` (policy service)
- `DeveloperCodingError` (coding service)
- `DeveloperProjectError` (project service)
- `DeveloperGithubError` (GitHub service)
- `DeveloperWriteError` (write service)

Each error carries its own HTTP status (default `400`). Unexpected exceptions return `500` with error `velia_coding_autopilot_internal_error` and are logged as `VELIA_CODING_AUTOPILOT_ROUTE_FAILED`.

Source: `services/velia_agent_coding_autopilot_routes.py` (`_PREFIX`, `_auth`, `_json_error`).

Smoke check: trigger each typed error and confirm the response shape is exactly `{ok: false, error, detail}` with the expected status, and that unexpected failures never leak internals beyond the generic `500` code.

## Mission policy: allowed and protected paths

Every mission must declare `allowed_paths`; a missing or empty list is rejected with `velia_coding_autopilot_allowed_paths_required`. At most 20 allowed prefixes are accepted (`velia_coding_autopilot_allowed_paths_too_many`).

Path prefixes are normalized (backslashes converted, slashes trimmed) and validated through the GitHub path validator. Prefixes that are empty, longer than 300 characters, contain NUL bytes, or contain `*` are rejected with `velia_coding_autopilot_path_invalid`.

The following path prefixes are always protected. Any allowed path that overlaps a protected prefix (in either direction) is rejected with `velia_coding_autopilot_protected_path` and HTTP status `403`:

- `.github`
- `.env`
- `secrets`
- `credentials`
- `private_keys`
- `auth`
- `billing`
- `migrations`
- `infrastructure`
- `terraform`

Additionally, callers may pass `blocked_paths` (at most 30 extra prefixes, otherwise `velia_coding_autopilot_blocked_paths_too_many`); these are merged with the protected set. Duplicate prefixes are de-duplicated.

Source: `services/velia_agent_coding_autopilot_policy_service.py` (`_PROTECTED_PREFIXES`, `_path_prefix`, `_matches`, `normalize_policy`).

Smoke check: attempt missions whose allowed paths include `.env`, `auth`, `billing`, and `terraform`; each must fail with `403` / `velia_coding_autopilot_protected_path`. Attempt a mission with no allowed paths and with 21 allowed paths; both must fail with the respective policy errors.

## Mission policy: step and file limits

`max_steps` defaults to 4 and must be an integer in the range 1–5. `max_files` defaults to 8. Non-integer values are rejected with `velia_coding_autopilot_limits_invalid`.

> Note: the upper bound for `max_files` is defined just past the reviewed source excerpt and must be confirmed against the full file before being stated here. Do not quote an exact `max_files` range in user-facing copy until verified.

Source: `services/velia_agent_coding_autopilot_policy_service.py` (`normalize_policy`).

Smoke check: submit missions with `max_steps` of 0, 1, 5, and 6; 0 and 6 must be rejected, 1 and 5 accepted. Submit a non-numeric `max_steps` and confirm `velia_coding_autopilot_limits_invalid`.

## Run lifecycle and concurrency

Active run and task statuses are:

```
claimed, planning, executing
```

The service uses PostgreSQL advisory locks to coordinate schema setup and run claiming (`_SCHEMA_ADVISORY_KEY`, `_CLAIM_ADVISORY_KEY`), supporting safe concurrent access and single-claim semantics for active runs.

Source: `services/velia_agent_coding_autopilot_service.py` (`_ACTIVE_RUN_STATUSES`, `_TASK_ACTIVE_STATUSES`, `_SCHEMA_ADVISORY_KEY`, `_CLAIM_ADVISORY_KEY`).

Smoke check: while one run is active for a repository, starting another must not silently double-claim work; observe the claim path under concurrent requests and confirm advisory-lock behavior.

## UX checklist for any UI surface built on this API

The autopilot routes are API-only, but any client UI consuming them must be smoke-tested against this checklist:

1. **Disabled state:** with feature flags off, the UI must surface the `503` disabled errors as a clear, non-destructive message — never as a generic crash.
2. **Loading state:** planning and executing runs can be long-lived; the UI must show a persistent loading/progress state for `claimed`/`planning`/`executing` statuses.
3. **Error state:** every typed error code above must map to a readable message; unknown codes must fall back to the `velia_coding_autopilot_internal_error` copy.
4. **Empty state:** a repository with no missions or runs must render an intentional empty state, not a blank panel.
5. **Policy feedback:** when a mission is rejected for protected paths or limit violations, the UI must display the `detail` field so the user can fix the input.
6. **Responsive behavior, focus states, contrast, and reduced motion:** verify per the standard design-execution checklist for any new UI; this document itself introduces no UI changes.

## Open verification items

The following claims appear in planning material but are **not** confirmed by the reviewed source excerpts. Confirm them in code before documenting them as behavior:

- Exact `max_files` upper bound (expected small integer range).
- Full mission/run status enum beyond the active statuses (e.g., `queued`, `ready_for_review`, `failed`, `blocked`, `cancelled`).
- Enforcement of one active run per repository end-to-end.
- Whether missions start in a paused state.
- Mode restrictions such as draft-PR-only, and explicit absence of auto-merge, deployment, and CI-repair behavior.
- The complete route list under `/mobile-api/v1/developer/autopilot`.
