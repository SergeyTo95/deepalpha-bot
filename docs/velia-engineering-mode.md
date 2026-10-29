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
