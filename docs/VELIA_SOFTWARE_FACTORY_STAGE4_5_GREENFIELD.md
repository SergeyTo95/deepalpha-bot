# VELIA Software Factory Stage 4.5 — Greenfield Bootstrap

Stage 4.5 extends Software Factory to projects that do not yet have all required Developer projects connected.

## Boundary

VELIA does not create GitHub repositories in Stage 4.5.

The flow is:

1. infer the smallest greenfield topology;
2. select an already linked GitHub App installation;
3. emit exact repository names;
4. require the user to create/expose those repositories and initialize each with a first commit;
5. after an explicit continuation message, attach only those exact repositories as Developer projects;
6. register sandboxed greenfield roots;
7. ask for write scope separately;
8. delegate execution to the existing single-repository Factory or Stage 4.4 workspace pipeline.

## Safety invariants

- `VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED` defaults to false.
- The GitHub App installation must already belong to the same VELIA user.
- Repository owner must exactly match the selected installation account.
- Repository attach uses exact `owner/name`, never fuzzy matching.
- Repository creation is not implemented here.
- New repositories need an initial commit so Coding Autopilot has a real base branch.
- Greenfield write-scope recommendations are limited to `app`, `android`, `tests`, and `docs` according to profile.
- `.github`, auth, billing, secrets, migrations, infrastructure and Terraform are not greenfield scaffold roots.
- Write scope still requires explicit approval before Coding Autopilot can modify code.
- Greenfield code has no GitHub write primitive and does not enqueue Coding Autopilot directly.

## Delegation

A single full-stack repository delegates to the established single-repository Software Factory.

A multi-repository greenfield topology delegates to Stage 4.4, so per-repository scope approval, workspace scheduling, exact-head CI, integration validation, and bounded same-PR repair remain authoritative.
