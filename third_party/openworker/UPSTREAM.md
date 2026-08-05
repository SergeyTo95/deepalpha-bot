# OpenWorker upstream reference

VELIA Agent Core is a compact, provider-neutral adaptation of selected architectural ideas from:

- Repository: `andrewyng/openworker`
- Reviewed commit: `01b6f83b3927e02912dda84bb392942c13ca70d1`
- License: MIT

VELIA does not vendor the OpenWorker desktop application, GUI, Tauri shell, updater, OAuth broker, local secret store, terminal runtime, or connector implementations.

The VELIA adaptation uses its own production contracts and security boundaries while drawing on the upstream concepts of:

- a typed tool registry;
- explicit action risk classes;
- plan/read-only versus interactive execution;
- approval-gated consequential actions;
- persistent jobs and audit events;
- future scheduled and MCP-compatible tools.

All public product surfaces remain branded as VELIA / Velia and Velyon Core. External implementation names must not appear in user-facing Android or Mobile API responses.
