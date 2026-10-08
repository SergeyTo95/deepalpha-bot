# VELIA specialist development plan

Scope: the whole product, including ordinary chat, voice, documents, research,
software, media and ongoing work. A specialist is an execution policy using
existing capabilities, not another model server or a duplicate Harness.

| Domain | Existing implementation to extend | Next qualification |
| --- | --- | --- |
| Request understanding and research | desktop/request_intent.py, desktop/web_search.py | multilingual intent, source provenance, unsupported claims |
| Documents | services/velia_attachment_service.py | page coverage, tables, bounded document context, OCR acceptance |
| Software | services/velia_developer_agent_service.py, existing coding autopilot | isolated workspace, executable tests, verified artifact |
| Tasks and calendar | services/velia_agent_runtime_service.py, scheduler/calendar services | typed arguments, timezone, idempotency, permissions |
| Memory and context | existing agent memory recall/namespace services | owner isolation, evidence source, retention and correction |
| Browser and desktop | existing Agent Core | persistent session, tool receipts, cancellation and recovery |
| Work | desktop/work_runtime.py | saved decision gates, bounded repair, result acceptance |
| Media | existing Studio and media worker | worker acceptance and cost/availability; no default GPU provisioning |

These domains are an audit map, not a claim that new autonomous specialists
have already been implemented or that all their integrations work end to end.

Execution principles:

* Keep Flash/Quantum/PRO routing. Use deterministic validation without a model
  call where possible; do not run multiple Flash specialists for every message.
* Select capabilities from explicit task requirements and available connectors.
  Do not route only by Russian keywords; test all supported language surfaces.
* Separate proposed actions, attempted actions and verified outcomes. A plan
  alone is not execution evidence; generated JSON is not a tool receipt.
* Reuse existing persisted job queues and owner namespaces. Each checkpoint
  records inputs, artifact references, tool outcome and remaining work.
* Bound retry counts, wall time and resource usage; preserve cancellation and
  idempotency. Additional reviewers consume the same CPU budget.
* Never interpret provider/search/document output as permission to send,
  publish, spend or change an owner's settings. Use existing authorization.
* Validate structured decisions before routing. The shared strict JSON decoder
  now rejects duplicate keys, nonfinite numbers, excessive size and depth in
  request understanding and Work. Existing domain validators remain required.
* Qualify changes in preview using successful, missing-tool, cancellation,
  restart and malformed-output scenarios before production rollout.

Next implementation order: inventory runtime availability and argument contracts;
connect specialists to existing tools; integrate verifiable result checks;
qualify multilingual multi-step handoffs. No extra always-on worker is required
by this plan. Paid teacher requests and financial signing stay separate.
