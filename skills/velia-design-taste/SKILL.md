---
name: velia-design-taste
description: Context-aware design quality layer for VELIA Coding Agent. Applies brief inference, audit-first redesign, platform-native mobile rules, anti-template checks, and bounded pre-flight validation without adding model calls.
license: MIT-derived; see third_party/taste-skill/UPSTREAM.md
---

# VELIA Design Taste

This is a compact VELIA-specific adaptation of selected concepts from `Leonxlnx/taste-skill`.
It is activated only for frontend, UI, UX, screen, layout, styling, animation, and mobile-interface coding tasks.
Backend-only work must not receive this context.

## 1. Read the task before choosing an aesthetic

Infer:

- interface type: landing, product screen, dashboard, Android, iOS, or cross-platform;
- whether the task is new UI or a redesign;
- existing framework, styling system, design tokens, components, navigation, assets, and dependencies;
- audience, brand signals, accessibility constraints, and platform conventions.

Declare one concise design read and three bounded dials:

- `VARIANCE` — visual/layout experimentation;
- `MOTION` — animation intensity;
- `DENSITY` — information per viewport.

## 2. Redesign means audit first

For an existing interface:

1. inspect the current implementation and dependencies;
2. list weak hierarchy, spacing, typography, generic patterns, missing states, accessibility gaps, and responsive issues;
3. make targeted changes in the current stack;
4. preserve behavior and public contracts;
5. do not rewrite the application merely to obtain a new visual style.

## 3. Avoid common AI design defaults

Do not automatically produce:

- purple/blue glow gradients;
- a centered hero followed by three identical cards;
- excessive pills, badges, glass panels, or floating cards;
- random gradients and animation without product purpose;
- tiny mobile text or phone-sized desktop pages;
- placeholder copy, dead links, fake imports, or missing loading/error/empty states.

Rules remain contextual. Existing brand and product conventions take priority.

## 4. Use one coherent system

- Reuse project tokens and components first.
- Verify dependency manifests before importing a package.
- Do not mix unrelated design systems.
- Do not migrate frameworks or styling libraries unless the user explicitly requests it.
- New dependencies require a concrete need that cannot be met safely with the current stack.

## 5. Mobile must stay app-native

For Android:

- preserve Jetpack Compose/View and Material conventions already used;
- handle window insets, back behavior, sheets, navigation, touch targets, and state feedback;
- keep hierarchy readable and avoid desktop layout patterns.

For iOS:

- preserve SwiftUI/UIKit conventions, safe areas, native navigation, sheets, tabs, and controls.

For cross-platform:

- keep one coherent navigation and component model;
- do not mix platform-specific patterns carelessly.

## 6. Mandatory implementation checks

Before completing a UI task, verify:

- imports and packages exist;
- responsive layouts, overflow, safe areas, and touch targets;
- semantic labels, focus states, contrast, and meaningful alt text;
- loading, empty, error, disabled, pressed, and selected states affected by the task;
- motion uses performant properties and respects reduced-motion preferences;
- redesigns preserve existing functionality;
- the result follows the declared design read and dials.

## 7. Cost discipline

This skill adds prompt guidance only. It must not add another model call or launch a separate design agent.
The runtime Python layer selects and compresses the relevant rules for each plan and task.
