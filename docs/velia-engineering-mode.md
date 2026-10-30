# VELIA engineering guidance

Adaptation reviewed on 2026-10-29 from [ub-mode](https://github.com/unclebob/ub-mode/tree/57a8bdd28e45ed41900ace0bafa2ff76dd9ed70f). The shared policy is original VELIA wording based on the engineering principles; no upstream installer, automatic update process or third-party analyzer is bundled.

## Where it applies

`desktop/src/engineering-policy.json` is the single policy source. Native Flash, Quantum and PRO turns receive a compact conditional coding instruction. Desktop Agent Core profiles use the fuller software-only guidance from the same file. Coding Agent planning/execution, Software Factory workspace instructions and independent review also receive the policy. Existing model choice, cost limits, write permissions and feature flags remain authoritative. This change does not activate the legacy Coding Agent or change its provider routing.

The principles cover reproducing defects, preserving contracts, small cohesive changes, input boundaries, idempotent writes, behavioral regression tests when practical, scoped refactoring and evidence-based completion reports. Ordinary conversation does not invoke source analysis, tests or an additional model call. A code answer in chat receives guidance; it is not represented as a tested repository patch.

## Checks before Coding Agent commits

`velia_code_quality.inspect_changes` examines complete changed files in memory after the existing exact-patch/path checks and before `commit_operations`. It compiles Python without importing or executing it and parses JSON with duplicate-key/nonfinite-number rejection. A new syntax failure on a previously valid file, or on a new file, enters the existing bounded repair loop. If repair fails, that step is not committed. Previous completed steps remain in the work branch.

Checks are limited to 16 changed non-deleted files, 200 KB per file, 800 KB total and 25,000 AST nodes per Python file. No project scripts, shell commands, dependencies or tests run in the serving process. Files requiring another parser and previously incompatible Python/JSON baselines are explicitly reported as not checked, not as passing. The current server interpreter defines supported Python syntax.

Newly increased Python complexity and exact duplicate function bodies are bounded advisory findings, compared with the baseline. They are not coverage, CRAP, mutation or semantic correctness measurements. Existing unchanged duplicate bodies are not reflagged. The report carries observed syntax results and explicitly labels project tests as `not_run` and coverage/CRAP/mutation as `not_measured`. Draft-PR descriptions distinguish this evidence from proposed project checks.

For real CRAP, structural similarity and mutation testing, the agent may use already configured project tools in an authorized execution environment. The policy names upstream review targets, but does not claim they were measured or met. Unavailable tools do not trigger automatic installs or permission expansion.

## Verification

Behavioral tests cover rejection of invalid Python/JSON, no source execution, parser/resource limits, legacy syntax, incremental duplication findings, repair before exactly one commit and refusal to commit after failed repair. Existing native chat, developer, design, workspace, independent review and Desktop configuration checks remain relevant. No live user repository was edited by the tests and no paid inference request was made for this adaptation.


## Structured specification workflow (2026-10-30)

VELIA adapts the requirements → plan → tasks → implementation → evidence workflow from
[GitHub Spec Kit](https://github.com/github/spec-kit) and its public specification/task templates.
These are original VELIA instructions. No upstream CLI, shell installer, extensions, catalogs,
or copied template files are bundled or executed. The MIT-licensed upstream toolkit itself
is free; using it does not make inference, hosting or external tools free.

For native software questions and Desktop software work the shared engineering policy adds
concise scope and acceptance guidance. Simple chat remains direct. Coding Agent persists a
bounded `plan.specification` with goal, constraints, acceptance, assumptions and `verification:
not_run`; each step receives it and the ordered plan. The existing current-step path allowlist,
permission/budget gates and static syntax checks still enforce writes. Draft PR acceptance
checkboxes remain unchecked until a reviewer has actual evidence. This task does not enable
legacy paid coding routes or launch additional calls/services.

The existing action planner and selected-model Browser Agent planner/reviewer also receive
stage-specific guidance without changing their JSON contracts or tools. The browser brief is
saved with the pre-dispatch checkpoint and included as untrusted evidence context for review.
The existing gateway also prepends a bounded workflow envelope to direct Flash browser tasks; oversized final payloads fail before dispatch. The original request controls authorization, never the model's plan. This is not a proof that
a model follows every semantic requirement.

### Custom agents and API contract

The existing authenticated `/mobile-api/v1/agents` API now accepts optional strict boolean
`spec_mode_enabled` on POST and PATCH `/{agent_id}`. GET and list return the stored boolean
and `workflow_version` (`velia-spec-v1` when enabled, otherwise null). Omission on create is
false; omission on patch preserves the old value. Strings, numbers and null are rejected as
`velia_agent_builder_spec_mode_invalid`. Existing rows receive an additive FALSE database
default; no existing agent is opted in. All profile/session lookups retain owner filtering,
and global/owner rollout, memory boundaries and external-action permissions are unchanged.

The browser already has “Мои агенты”. Its create and edit forms now expose “Работать по плану”
as a keyboard-accessible switch, with a brief explanation. The proxy adds only authenticated
GET/PATCH profile paths; it does not add arbitrary execute URLs. A new agent conversation uses
the same stored profile. Foreground prompting uses the existing builder hook. Background
admission snapshots the owner's profile guidance in the durable payload so a queued task
keeps its original workflow even if the switch is later changed. Disabling applies to future
turns/tasks. Agent history can still describe an earlier plan, as expected.

This prepares the setting for API integration. It does not introduce public machine API keys,
externally hosted agent registration, arbitrary webhook execution or a bridge that can enforce
rules inside a third-party agent. A future external adapter must pass the profile/workflow
contract to its executor and return task progress and verification evidence. An existing
external service cannot become spec-driven merely because a VELIA checkbox is checked.

Validation covers boolean validation, bounded prompt inclusion and unchanged ordinary chat,
API relay authorization, persisted plan/unchecked criteria, and synthetic browser create →
edit → reload → same-agent conversation. The dedicated PostgreSQL acceptance test also covers
migration of a pre-existing table, default false, cross-owner rejection and frozen background
profile context; it runs only with `VELIA_FLASH_TEST_DATABASE_URL` in isolated test schemas.
