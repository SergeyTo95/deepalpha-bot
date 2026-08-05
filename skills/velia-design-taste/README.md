# VELIA Design Taste

Context-aware design-quality layer for **VELIA Coding Agent**.

VELIA Design Taste improves frontend, UI, UX, mobile-interface, layout, styling, typography, animation, dashboard, and redesign tasks without creating a second design agent and without adding another model call.

It is a compact VELIA-specific adaptation of selected ideas from the MIT-licensed [`Leonxlnx/taste-skill`](https://github.com/Leonxlnx/taste-skill) project.

---

## Status

- Runtime integration: production backend
- Coding Agent integration: enabled when VELIA Coding Agent is enabled
- Default Taste Layer state: enabled
- Additional model calls: none
- Write model: isolated `velia/...` branch only
- Pull request model: draft PR only
- Merge/deploy capability: intentionally absent

The Taste Layer is not a standalone endpoint and is not a replacement for VELIA Coding Agent. It enriches the existing planning and execution prompts only when the task is actually related to an interface.

---

## Why VELIA uses an adaptation instead of vendoring the full repository

The upstream project contains multiple skills, examples, screenshots, research material, image-generation instructions, and strong opinionated variants.

Copying the entire repository into VELIA would:

- increase repository size;
- increase prompt size and token cost;
- mix image-generation instructions with code-generation work;
- force rules that are not appropriate for every product;
- risk changing existing frameworks and design systems unnecessarily;
- apply frontend guidance to backend-only tasks.

VELIA therefore uses a curated runtime layer that keeps the strongest reusable ideas:

- infer the brief before selecting an aesthetic;
- audit existing interfaces before redesigning them;
- avoid common generic AI layout patterns;
- preserve the existing stack and design system;
- use platform-native mobile conventions;
- verify dependencies, responsiveness, accessibility, states, and motion;
- expose a concise design direction and bounded design dials;
- keep prompt guidance bounded and cost-aware.

The adaptation intentionally removes or makes contextual:

- mandatory GSAP;
- simulated random layout selection;
- absolute bans on specific libraries;
- image-generation-only workflow rules;
- universal requirements for cinematic motion;
- instructions that would override an existing product design system.

---

## Upstream and license

Upstream repository:

```text
Leonxlnx/taste-skill
```

Reviewed upstream commit:

```text
e988add20dab0fa97d7a76781c48961c8184288e
```

License:

```text
MIT
```

VELIA keeps the original MIT license and attribution in:

```text
third_party/taste-skill/LICENSE
third_party/taste-skill/UPSTREAM.md
```

Runtime source constants also record the exact upstream repository and commit.

---

## Main behavior

### UI task

A request such as:

```text
Redesign the Android chat screen and improve the empty, loading, and error states.
```

activates VELIA Design Taste.

The Coding Agent plan receives:

- a design mode;
- a platform classification;
- a concise design read;
- a design-system foundation;
- variance, motion, and density dials;
- audit-first guidance when appropriate;
- platform-specific and accessibility checks.

### Backend-only task

A request such as:

```text
Fix the PostgreSQL retry logic in the billing service.
```

does not activate the Taste Layer.

The backend task receives no design guidance and pays no design-context overhead.

---

## Supported modes

The runtime classifier can select the following modes.

| Mode | Use case |
| --- | --- |
| `web-new-ui` | New website or web interface work |
| `web-redesign` | Existing web interface redesign |
| `product-dashboard` | New product/dashboard UI |
| `product-dashboard-redesign` | Existing product/dashboard redesign |
| `mobile-android` | New Android interface work |
| `mobile-android-redesign` | Existing Android interface redesign |
| `mobile-ios` | New iOS interface work |
| `mobile-ios-redesign` | Existing iOS interface redesign |
| `mobile-cross-platform` | New React Native, Flutter, or cross-platform UI |
| `mobile-cross-platform-redesign` | Existing cross-platform app redesign |

Classification uses both the user request and candidate repository paths.

This means a task can activate the layer from explicit language such as `Android`, `screen`, `layout`, or `redesign`, or from relevant paths such as:

```text
ui/
screens/
components/
pages/
layouts/
theme/
styles/
*.tsx
*.jsx
*.css
*.scss
*Screen.kt
*Activity.kt
*Composable.kt
*.swift
```

---

## Design profile

For an active UI task, the plan stores a normalized `design` object.

Example:

```json
{
  "active": true,
  "version": "velia-design-taste-v1",
  "source": "Leonxlnx/taste-skill@e988add20dab0fa97d7a76781c48961c8184288e",
  "mode": "mobile-android-redesign",
  "platform": "android",
  "audit_first": true,
  "read": "redesign for android, with a premium and deliberate visual language; use the existing Android UI stack",
  "system": "existing Android UI stack; prefer Material 3/Jetpack Compose conventions when already present",
  "variance": 7,
  "motion": 5,
  "density": 4
}
```

### Dials

The three dials are bounded from `1` to `10`.

#### Variance

Controls how conventional or visually exploratory the layout may be.

- low: restrained, symmetric, system-first;
- medium: intentional variation while preserving product consistency;
- high: more expressive composition when the brief explicitly supports it.

#### Motion

Controls animation intensity.

- low: state transitions and functional feedback;
- medium: purposeful transitions and section movement;
- high: richer motion only when the product and brief justify it.

Motion must remain performant and reduced-motion safe.

#### Density

Controls information per viewport.

- low: spacious marketing or premium consumer UI;
- medium: balanced product UI;
- high: dashboards, admin, analytics, and dense operational surfaces.

The dials guide decisions. They do not replace repository conventions or user requirements.

---

## Brief inference

Before planning a UI change, the layer identifies:

- interface type;
- target platform;
- new UI versus redesign;
- existing framework and styling approach;
- brand or product signals;
- explicit vibe words such as minimal, premium, playful, or accessibility-first;
- candidate files and existing design assets;
- density requirements such as dashboard or admin use.

The result is a concise design read, for example:

```text
Existing Android chat redesign with a premium, deliberate visual language, preserving the current Material 3 and Jetpack Compose system.
```

The design read appears in the user-visible Coding Agent plan and in the generated draft PR body.

---

## Redesign workflow

Redesign mode is audit-first.

The plan must begin by understanding the current product rather than replacing it with a generic template.

Expected sequence:

1. inspect the current framework, styling system, tokens, navigation, components, assets, dependencies, and states;
2. identify hierarchy, typography, spacing, responsive, accessibility, state, and consistency issues;
3. select targeted improvements;
4. implement small reviewable changes;
5. preserve existing behavior and public contracts;
6. validate the affected states and layouts.

The agent must not rewrite an application solely to obtain a different visual style.

---

## Platform rules

### Android

VELIA must preserve the Android architecture already used by the repository.

Typical checks:

- Jetpack Compose or View conventions;
- Material 3 patterns when already present;
- system bars and window insets;
- back navigation;
- bottom sheets and dialogs;
- navigation state;
- minimum touch targets;
- pressed, selected, disabled, loading, empty, and error states;
- readable typography at normal device scale;
- no desktop-web layout compressed into a phone screen.

### iOS

Typical checks:

- SwiftUI or UIKit conventions;
- safe areas;
- native navigation hierarchy;
- sheets and tabs;
- standard controls and interaction feedback;
- Dynamic Type and readable content hierarchy.

### Cross-platform mobile

Typical checks:

- one coherent navigation model;
- consistent component behavior;
- platform-safe spacing;
- no careless mixing of Android and iOS conventions;
- readable, buildable layouts rather than design-only mockups.

### Web and dashboard

Typical checks:

- semantic structure;
- stable dynamic viewport units;
- no horizontal overflow;
- responsive containers and breakpoints;
- keyboard focus;
- readable contrast;
- loading, empty, error, disabled, pressed, and selected states;
- dashboard density and data readability.

---

## Anti-template rules

The layer actively guards against common generic AI output, but all rules remain contextual.

It discourages automatic use of:

- purple/blue startup glow as a default identity;
- centered hero followed by three identical cards;
- excessive pills, badges, glass panels, and floating cards;
- random gradients or animation without product purpose;
- tiny mobile typography;
- phone-sized desktop websites;
- placeholder copy and dead links;
- missing loading, empty, error, disabled, pressed, or selected states;
- fake imports or dependencies that are not installed;
- mixing unrelated design systems;
- framework migration without an explicit requirement.

Existing brand rules, accessibility requirements, and product conventions take priority.

---

## Dependency discipline

Before importing a UI library, the agent must verify the repository dependency manifest.

Examples:

```text
package.json
build.gradle.kts
libs.versions.toml
Podfile
Package.swift
pubspec.yaml
```

Rules:

- reuse current dependencies and components first;
- do not hallucinate packages;
- do not add a library for a small effect that the existing stack can implement safely;
- do not mix unrelated design systems;
- preserve the project's framework and styling architecture unless migration is explicitly requested.

---

## Mandatory preflight checks

For active UI tasks, the layer adds bounded checks to the plan and execution prompt.

Core checks:

- imported UI dependencies exist;
- responsive behavior works at small and large sizes;
- no unintended overflow;
- safe areas and touch targets are correct;
- visible focus states are present where applicable;
- semantic labels and meaningful image descriptions exist;
- contrast is readable;
- loading, empty, error, disabled, pressed, and selected states are handled when affected;
- animation uses performant properties where possible;
- reduced-motion preferences are respected;
- redesigns preserve existing behavior;
- the implementation follows the declared design read and dials.

These checks are instructions for implementation and CI planning. VELIA must not claim they passed until tests or real validation confirm them.

---

## Runtime architecture

### Files

```text
services/velia_developer_taste_skill_service.py
services/velia_developer_coding_service.py
skills/velia-design-taste/SKILL.md
skills/velia-design-taste/README.md
third_party/taste-skill/LICENSE
third_party/taste-skill/UPSTREAM.md
tests/test_velia_developer_taste_skill_service.py
tests/test_velia_developer_taste_integration.py
```

### Planning flow

```text
User coding request
  -> VELIA Coding Agent intent classifier
  -> candidate repository files
  -> taste_skill.classify(goal, paths)
  -> bounded repository evidence
  -> existing Coding Agent planning model call
  -> normalized plan with optional design profile
  -> user-visible ordered plan
```

The Taste Layer does not start a second planner.

### Execution flow

```text
User: "Выполняй план"
  -> create isolated velia/... branch
  -> load current task files
  -> read design profile from stored plan
  -> add bounded execution guidance and preflight checks
  -> existing Coding Agent task model call
  -> validate exact patch operations
  -> atomic commit for the task
  -> continue to the next task
  -> open draft PR
```

The design profile is stored in the plan so all tasks use one coherent direction.

### Pull request output

For active UI tasks, the draft PR includes:

- plan summary;
- design direction;
- selected mode;
- variance, motion, and density values;
- completed task commits;
- changed files;
- safety notice.

VELIA does not merge or deploy the PR.

---

## Cost model

VELIA Design Taste adds **zero model calls**.

It only adds bounded instructions to model calls that already exist in VELIA Coding Agent:

- one planning call;
- one call per implementation task;
- optional repair call only when an implementation patch is invalid.

To prevent richer design guidance from increasing input cost uncontrollably, active UI tasks use bounded evidence and context sizes.

Defaults:

```env
VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS=10000
VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS=17000
```

Allowed runtime ranges:

```text
Plan evidence: 4,000 to 16,000 characters
Step context: 8,000 to 24,000 characters
```

Coding Agent cost caps continue to apply:

```env
VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD=0.04
VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD=0.06
VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD=0.24
```

The exact production cost must be measured from real runs. The documentation does not claim a task will always use the maximum budget or always finish below a specific amount beyond enforced server caps.

---

## Configuration

### Required Coding Agent flags

```env
VELIA_DEVELOPER_CODING_ENABLED=true
VELIA_DEVELOPER_WRITE_ENABLED=true
```

### Taste Layer flag

```env
VELIA_DEVELOPER_TASTE_SKILL_ENABLED=true
```

The Taste Layer defaults to enabled when the variable is absent. It still activates only for classified UI tasks.

Disable it without disabling Coding Agent:

```env
VELIA_DEVELOPER_TASTE_SKILL_ENABLED=false
```

### Optional prompt bounds

```env
VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS=10000
VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS=17000
```

### Recommended Coding Agent limits

```env
VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD=0.04
VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD=0.06
VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD=0.24
VELIA_DEVELOPER_CODING_MAX_STEPS=5
VELIA_DEVELOPER_CODING_PATCH_ATTEMPTS=2
```

---

## GitHub App requirements

VELIA Coding Agent requires these repository permissions:

```text
Contents: Read and write
Pull requests: Read and write
```

Recommended:

```text
Workflows: No access
```

Safety behavior remains unchanged when the Taste Layer is active:

- no direct write to the selected/base branch;
- work only on `velia/...` branches;
- no force push;
- one atomic commit per task;
- protected secrets and environment files blocked;
- GitHub workflow files blocked by default;
- draft PR only;
- no merge function;
- no deployment function.

---

## User workflow

### 1. Request a change

Example:

```text
In deepalpha-android, redesign the ordinary chat empty and error states. Preserve the existing Material 3 theme, improve hierarchy and accessibility, and first show me the plan.
```

### 2. Review the plan

Expected response includes:

- design direction;
- mode and platform;
- variance, motion, and density;
- ordered small tasks;
- files;
- validation checks;
- additional suggestions.

No repository write occurs during planning.

### 3. Approve once

```text
Выполняй план
```

### 4. Observe progress

VELIA reports each stage:

```text
Creating work branch...
Task 1/3: auditing current files...
Task 1/3 completed, commit abc12345...
Task 2/3: implementing state components...
Opening draft pull request and checking CI...
```

### 5. Review the draft PR

The user reviews:

- diff;
- commits;
- CI status;
- screenshots or device validation when relevant;
- VELIA suggestions.

Merge and deployment remain separate explicit actions.

---

## Example tasks

### Android redesign

```text
Redesign the VELIA Android chat composer to improve visual hierarchy, touch targets, disabled state, sending state, and error recovery. Keep the existing Compose and Material 3 architecture. Build a plan first.
```

Expected mode:

```text
mobile-android-redesign
```

### Web landing page

```text
Create a premium but restrained landing page for the Velyon Core developer API using the existing React and Tailwind stack. Avoid generic AI gradients and include loading/error states for the API demo.
```

Expected mode:

```text
web-new-ui
```

### Existing dashboard

```text
Audit and improve the DeepAlpha admin dashboard. Preserve all current functionality and routes, improve data hierarchy, table readability, responsive behavior, empty states, and keyboard focus.
```

Expected mode:

```text
product-dashboard-redesign
```

### Backend bypass

```text
Fix duplicate PostgreSQL connection retries in the usage accounting service.
```

Expected Taste Layer state:

```text
inactive
```

---

## Tests

### Classification tests

Verify:

- UI text activates the layer;
- UI file paths activate the layer;
- backend-only work bypasses it;
- Android, iOS, cross-platform, dashboard, web, and redesign modes classify correctly;
- minimal, premium, playful, and accessibility signals adjust bounded dials;
- disabling the environment flag bypasses the layer.

### Normalization tests

Verify:

- model-provided dials are clamped to `1..10`;
- server classification remains authoritative for mode and platform;
- malformed or absent design data falls back safely;
- inactive tasks do not store a design profile.

### Prompt integration tests

Verify:

- UI plans receive planning guidance;
- backend plans do not receive design guidance;
- UI execution receives the stored profile and preflight checks;
- redesign plans receive audit-first instructions;
- prompt context remains bounded;
- the number of Coding Agent model-call sites does not increase.

### Security regression tests

The normal Coding Agent suite must continue to verify:

- branch isolation;
- draft PR only;
- protected paths;
- no merge/deploy capability;
- cost caps;
- read-only Developer isolation;
- ordinary chat routing;
- streaming behavior.

### Commands

```bash
python -m py_compile \
  services/velia_developer_taste_skill_service.py \
  services/velia_developer_coding_service.py
```

```bash
PYTHONPATH=. pytest -q \
  tests/test_velia_developer_taste_skill_service.py \
  tests/test_velia_developer_taste_integration.py \
  tests/test_velia_developer_coding_service.py \
  tests/test_velia_developer_coding_chat_gate.py \
  tests/test_velia_developer_coding_intent_classifier.py \
  tests/test_velia_developer_github_write_service.py \
  tests/test_velia_developer_chat_runtime_patch.py \
  tests/test_velia_mobile_streaming_service.py
```

Do not claim the feature works solely because these focused tests pass. Production acceptance also requires deployment and a real repository/device test.

---

## Production acceptance checklist

### Backend

- [ ] Coding Agent flags enabled in Railway
- [ ] Taste Layer enabled or default-enabled
- [ ] cost caps configured
- [ ] backend deployment succeeded
- [ ] database tables available

### GitHub

- [ ] Contents permission is read/write
- [ ] Pull requests permission is read/write
- [ ] updated app permissions accepted for the installation
- [ ] target repository is accessible
- [ ] Workflows permission remains disabled unless explicitly required later

### Plan test

- [ ] UI request classified correctly
- [ ] design direction displayed
- [ ] correct platform/mode displayed
- [ ] ordered tasks shown
- [ ] no branch or commit created before approval

### Execution test

- [ ] `Выполняй план` creates a `velia/...` branch
- [ ] progress appears per task
- [ ] each task creates one commit
- [ ] protected files remain blocked
- [ ] draft PR is created
- [ ] no merge occurs
- [ ] no deploy occurs

### Quality test

- [ ] diff follows existing architecture
- [ ] dependencies are real
- [ ] affected UI states are complete
- [ ] accessibility and responsive checks are included
- [ ] CI is reviewed
- [ ] Android/iOS work is validated on a real device or emulator when applicable

---

## Troubleshooting

### Taste Layer does not activate

Check:

```env
VELIA_DEVELOPER_TASTE_SKILL_ENABLED=true
```

Also verify the request or candidate paths clearly indicate UI work.

Use explicit wording when necessary:

```text
Redesign the Android chat screen UI...
```

instead of:

```text
Improve this.
```

### Backend task receives design guidance

This is a classifier regression.

Do not work around it by disabling Coding Agent globally. Add a focused test for the exact request phrase and adjust the UI classifier.

### Plan exceeds the planning cost cap

Reduce:

```env
VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS
```

or improve candidate-file ranking. Do not add another model call merely to summarize the design context.

### Step exceeds the per-step cost cap

Reduce:

```env
VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS
```

or split the task into smaller planned steps.

### Design direction is inconsistent across tasks

The executor must read the normalized `plan.design` object stored with the job. It must not independently reclassify each task into a different visual direction.

### Agent tries to replace the whole framework

For redesign tasks, verify that:

- `audit_first` is true;
- the planning guidance contains the preserve-stack rule;
- the plan's first step is an audit;
- the execution prompt includes the Design Execution Guard.

### Draft PR has no CI results yet

Check runs may still be pending immediately after the last commit. The final response must report them as pending rather than claiming success.

---

## Maintenance and upstream updates

VELIA does not automatically pull new upstream versions.

To review a new upstream release:

1. record the current upstream commit;
2. inspect the upstream changelog and relevant `SKILL.md` files;
3. identify reusable concepts, not entire prompt blocks;
4. reject rules that conflict with VELIA safety, cost, platform, or existing-stack requirements;
5. update `UPSTREAM_COMMIT` and `third_party/taste-skill/UPSTREAM.md` only after review;
6. preserve the MIT license and attribution;
7. update classification and integration tests;
8. verify model-call count remains unchanged;
9. run the full Developer/Coding Agent/streaming regression suite;
10. validate with a real UI request before calling the update production-ready.

Do not silently track `main`. Pinning the reviewed commit makes behavior auditable and reproducible.

---

## Known limitations

- The layer improves code-generation guidance; it is not a visual renderer.
- It cannot verify final pixel quality without screenshots, emulator/device testing, or browser inspection.
- It does not automatically generate images or brand assets.
- It does not replace product requirements or user research.
- It does not guarantee that every model-generated design decision is correct.
- It cannot safely infer an entire product design system from one small source fragment.
- It intentionally does not force maximum motion or novelty.
- It does not merge or deploy generated changes.

---

## Principles

1. Read the product before choosing the aesthetic.
2. Preserve the existing system unless change is explicitly required.
3. Audit before redesign.
4. Make UI complete, not merely attractive.
5. Prefer real dependencies and existing components.
6. Keep mobile app-native.
7. Treat accessibility and states as design work.
8. Avoid generic AI defaults without replacing them with random novelty.
9. Keep prompts and cost bounded.
10. Never claim CI, deployment, or device validation without evidence.

---

## Attribution

VELIA Design Taste is a curated adaptation of concepts from:

```text
Taste Skill
Copyright (c) 2026 Leonxlnx
https://github.com/Leonxlnx/taste-skill
```

Used and modified under the MIT License.

See:

```text
third_party/taste-skill/LICENSE
third_party/taste-skill/UPSTREAM.md
```
