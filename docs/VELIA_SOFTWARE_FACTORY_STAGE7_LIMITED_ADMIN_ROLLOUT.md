# VELIA Software Factory — Stage 7 Limited Admin Rollout

Stage 7 is the first persistent rollout envelope after the successful Stage 6.7 production acceptance. It deliberately does **not** enable general `live` rollout and does not add merge, release, deployment, repository-write, or environment-mutation primitives.

## Goal

Allow the configured VELIA administrator to run the already accepted one-shot Software Factory build/review chain:

`natural-language request -> Architect/Planner/team DAG -> Coding Autopilot -> CI -> Senior Reviewer -> bounded reviewer remediation -> CI -> Senior Reviewer -> draft PR`

The result remains a draft PR. Stage 7 does not authorize Stage 5 delivery/release.

## New rollout mode

`VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE=limited_admin`

This mode is distinct from unrestricted `live`:

- only the Stage 6 admin-pilot identity is eligible;
- explicit general Factory allowlists do not become eligible in `limited_admin`;
- the Stage 6.2 one-shot dispatch guard is still mandatory;
- the Stage 6.3 owner control is still mandatory;
- build/review readiness may become true;
- release readiness always remains false because Stage 5 requires the normal `live` mode.

## Stage 7 gate

Stage 7 additionally requires:

- `VELIA_SOFTWARE_FACTORY_STAGE7_LIMITED_ADMIN_ROLLOUT_ENABLED=true`;
- Stage 6.3 live-pilot control enabled;
- Stage 6.2 one-shot guard enabled;
- Senior Reviewer enabled and runtime-installed;
- bounded Reviewer remediation enabled and available;
- Stage 6.7 acceptance harness disabled after acceptance;
- all Stage 5 delivery/release flags disabled;
- greenfield bootstrap and integration repair disabled for this first persistent rollout;
- an exact, previously issued Stage 6.7 passed certificate revalidated from persisted DB evidence.

## Acceptance proof configuration

Stage 7 never accepts a plain boolean such as `acceptance=true`. The operator must bind it to the exact historical Stage 6.7 evidence:

- `VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_RUN_ID`
- `VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_REPOSITORY`
- `VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_CERTIFICATE_ID`

`verify_passed_certificate(...)` reloads the persisted Stage 6.7 one-shot grant and exact Autopilot evidence, requires:

- acceptance approval source;
- consumed one-shot grant;
- final Autopilot state `ready_for_review`;
- final Senior Reviewer status `passed`;
- reviewer remediation phase `completed`;
- at least one remediation attempt;
- non-empty exact reviewed head SHA;
- certificate fingerprint equality.

The verification does not reopen Stage 6.7 and creates no new grant or repository operation.

## Accepted production evidence before Stage 7 implementation

The Stage 6.7 production acceptance certificate observed on 2026-08-25 was:

- Factory run: `c34348c7-2012-45bd-bfd5-860ae044c1bc`
- Repository: `SergeyTo95/deepalpha-bot`
- Acceptance ID: `eb354287-7a87-4c0c-aa7c-8137474d14b4`
- Certificate ID: `e69cde5b8ce78ef1450ed749fb305d65330213280b18f31636f8d03a50817f3b`
- Draft PR: `#527`
- Reviewed final head: `9c8865dbb02c39c71c4d52d8897b92c61a232f11`
- Reviewer: `passed`
- Remediation phase: `completed`
- Remediation attempts: `2`

The canary PR was closed without merge.

## Capabilities intentionally blocked

While Stage 7 is active, these must remain false:

- `VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED`
- `VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED`
- `VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED`
- `VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED`
- `VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED`
- `VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED`
- `VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED`
- `VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED`
- `VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED`
- `VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED`

Stage 7 reports `merge_supported=false`, `release_supported=false`, and `deployment_supported=false`.

## Fail-closed defaults

Nothing becomes active by merging Stage 7. Production remains unchanged while:

- rollout mode is `off`;
- admin pilot is disabled;
- Stage 7 gate is disabled;
- write/release gates remain closed.

Activating persistent `limited_admin` production rollout is an operational rollout decision after the Stage 7 code itself passes exact-head CI and deployment verification.
