# VELIA Autopilot controlled acceptance fixture

This repository contains a dormant test fixture for the final end-to-end Coding Autopilot acceptance campaign.

The fixture is inert unless this file exists in a pull-request branch:

```text
docs/velia-autopilot-controlled-repair-smoke.txt
```

The first line must be exactly:

```text
VELIA_AUTOPILOT_REPAIR_OK
```

A controlled Autopilot mission may intentionally create the file with an incorrect first line. Exact-head CI will then fail with a bounded, deterministic repair instruction. VELIA must repair only that approved file, create a new commit in the same branch, and wait for the new exact-head CI result.

An optional second line may be used for the Review Loop smoke, for example:

```text
review-note: initial
```

A reviewer can explicitly request changing only the second line after CI is green. The first marker must remain unchanged.

Safety boundaries:

- the fixture does nothing when the smoke file is absent;
- it does not change workflows, secrets, authentication, billing, migrations or infrastructure;
- the autonomous PR remains draft-only;
- the fixture never authorizes merge or deployment;
- the smoke PR should be closed without merging after acceptance.
