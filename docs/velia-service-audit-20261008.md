# VELIA service audit — 2026-10-08 UTC

## Observed infrastructure

Railway reads confirm terminal SUCCESS for:

| Service | Environment | Latest observed deployment |
| --- | --- | --- |
| Desktop gateway | deepalpha-bot-pr-577 | 6a445f93-5a03-4e02-9120-ccc8c3ef33e5; pinned a8651bc |
| Browser Agent | deepalpha-bot-pr-577 | d273a307-ddc2-4b57-81df-d940ed264d1a |
| Backend | production | e5b87ad8-865c-4011-a501-0b7c705dcd16 |
| Memory | production | 72321771-f316-436a-bc9d-7677664adba3 |
| Research worker | production | bc541ea6-fb2e-44fb-8046-b52fb7c526e9 |

Public gateway /health returned HTTP 200 and ok=true. Browser sessions have a
1 GiB persistent /data volume. Current gateway error-filtered deployment logs
returned no entries in the queried interval. Backend log matches were INFO
EDGE_WATCH_DONE with errors=0, not confirmed runtime failures. Historical failed
qualification logs were not treated as current failures.

These are liveness/deployment observations, not authenticated task acceptance.
No user session, user documents, worker secrets or external account credentials
were acquired for this audit. No live paid model calls were made.

## Confirmed code problems and changes

* Software status can be enabled while worker_ready=false. The overview now
  exposes partial readiness and fixed public prerequisites, instead of merely
  saying the endpoint responds.
* Plugins may be only partly available. Aggregate readiness now lists unavailable
  fixed plugin names without disclosing provider payloads.
* Research/image-generation chat plugin toggles are hardcoded unavailable in
  the current backend source. They do not represent Research/Studio availability.
  The UI now explains that distinction and offers the existing section links.
  It does not claim those sections are operational without qualification.

Validation after changes: 446 gateway Python tests, 19 existing JavaScript
unit tests, and features.mjs syntax check passed. The new section-link UI was
not exercised through an authenticated browser.

## Remaining release gates

1. Authenticated end-to-end chat, search and file upload/analysis, including
   failed upload, oversized input, truncation and owner isolation.
2. Flash current TTFT, token rate, RSS/CPU and queue behavior under concurrent
   users. Deployment SUCCESS is not a performance benchmark.
3. Research mission with actual sources, persisted reports and restart recovery.
   Worker deployment does not demonstrate a successfully completed mission.
4. Studio image/video acceptance per mode and reference count with measured
   worker availability and cost. Do not enable paid fallback implicitly.
5. Native PDF page coverage fix (#585) and separate OCR acceptance; scans are
   not automatically supported by text extraction.
6. Model Lab #584 backend integration, DB/authenticated UI qualification and
   actual Flash baseline. Not included in gateway deployment.
7. Browser logged-in session, disk retention, cancellation and verified tool
   outcome. Do not infer arbitrary website integration from Harness presence.
8. Upwork approved API configuration, OAuth and verified connector actions.
   Draft applications are not submitted applications or accepted jobs.
9. USDT wallets: connected signer, tested ledger/limits and reconciliation;
   no direct language-model secret-key access. Current Work balance is unknown.
10. Voice/STT/TTS acceptance on devices/languages; browser voice inventory is
    device-dependent. No new claim of identical voices or latency improvement.

The remaining gates are unfinished work. No claim of ideal or universally
working VELIA is justified by this audit.
