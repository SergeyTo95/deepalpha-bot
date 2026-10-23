# Project analysis workflow v1

In browser VELIA: Projects -> open a project -> enter the service's HTTPS URL
and the intended improvement -> Start analysis. The first Flash stage opens in
a linked account conversation. Reopen the resource in Projects to run another
stage or read the saved conversation. Normal chat export also exports the reports.

Four stages:

1. Recon: scoped journeys, screens, states and initial feature priorities.
2. Feedback: original review text with one `https://review-url | exact review`
   per line. RU/EN/TR lexical triage supports the model's analysis; unknown
   languages and unmatched wording remain in the evidence. URLs are not fetched
   to verify reviews. Duplicate quote text does not inflate counts.
3. Test: a test plan, including account isolation and degraded-network states.
4. Diff: reported test cases, one `Name | must/should/could |
   passed/partial/failed/not_run | https://evidence-url` per line. `-` is allowed
   instead of a URL and makes a declared pass untested. Critical issues use
   `Name | S1/S2 | open/fixed | https://evidence-url`.

The last stage calculates its score outside the model. Missing/untested required
cases, absence of a mandatory scope and open S1/S2 issues block readiness at any
score. The footer survives history reloads. Ready for review never authorizes a
release or claims the evidence was independently verified.

## Runtime and persistence

Only the existing `velia-flash` account stream may run a stage. Paid models,
voice and attachments are rejected for structured stages. Existing model
authorization, request quotas and generation cancellation apply. Prerequisite
stages require a completed saved assistant reply, not merely a pending request.

A versioned `seed_query` is persisted in an ordinary owner-scoped chat resource;
there is no new database or schema migration. Requests and results use existing
conversation storage and idempotency keys. Stage completion is rebuilt from
saved messages after restart. Up to 200 messages are inspected, matching the
existing Web history page; very old stage evidence outside that window must be
rerun. Resource lookup follows the existing 2,000-resource account limit.

Only the active stage instructions enter its turn. Internal guidance is removed
from the user's history view. A normal follow-up uses ordinary chat behavior;
choose a structured stage from Projects to run its checks again.

When the existing Web search is available, recon makes one cached search using
only the target host/path and fixed documentation terms. Project goals, query
parameters and pasted reviews are not search queries. Other stages bypass
automatic search. Without configured search, recon must mark unseen behavior
unknown and use supplied notes only. Existing search/provider charges still
apply; there is no new provider, GPU, shell access or background billing loop.

This first stage does not build software, execute tests, benchmark a competitor,
publish, buy services or change production. Build/backend/brand/launch/deploy
need a separately qualified project executor and release integration.

## Verification

Run `python -m pytest tests/test_velia_project_workflow.py
tests/test_velia_web_features.py tests/test_velia_web_chat.py
tests/test_velia_web_search.py -q`, plus `node --test desktop/web/tests/*.test.mjs`.
The fixture HTTP tests use synthetic account/model data; live Flash quality and
deployment require separate acceptance on the target service.

`desktop/scripts/smoke-project-workflow.mjs` exercises all four stages, restores
progress after reload, checks inert rendering of untrusted project titles and
checks 1440/390/360-pixel layouts. It reuses the local Web fixture and accepts
the existing `VELIA_PLAYWRIGHT_MODULE`, `VELIA_CHROMIUM_MODULE` and
`VELIA_CHROMIUM_EXECUTABLE` test settings. No live account or model is used.
