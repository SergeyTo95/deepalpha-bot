# Decision-1 routing

The Web gateway optionally calls Microsoft's Decision-1 through the OpenRouter
Decisions API (`https://openrouter.ai/api/alpha/decisions`). This is not the chat
completions API. Flash continues to generate every conversational answer.

## Configuration

Set `VELIA_DECISION_API_KEY` in the gateway's Railway Variables, never in client
code or git. Set `VELIA_DECISION_MODE` to `shadow` for evaluation, then `active`
after multilingual acceptance. Default is `disabled`. Missing keys skip calls.

Shadow mode logs the proposed direct routing and latency, but always uses Flash's
existing interpreter. Active mode can skip that interpreter only for a single,
self-contained text turn, with route probability >= .98, route confidence >= .95,
and interpretation probability <= .02. These are initial conservative thresholds,
not demonstrated production calibration. Search, typo repair, personal conditions,
medical advice, tools and multi-turn references stay on the existing path.

The model does not execute tools, authorize transactions, generate search queries,
or make medical decisions. It receives only the latest eligible text message;
account tokens, system messages, history and attachments are not transmitted.
Logs contain no user content or secrets. Review provider data handling before
enabling external routing for sensitive workloads.

Calls have an end-to-end 800 ms budget, no retries, at most four concurrent calls
per process, a 64 KiB response limit, redirects disabled and a 60-second circuit
breaker after transport/HTTP/JSON errors. Failure retains the Flash path.
`/health` reports configuration readiness, not live provider availability.

## Acceptance before activation

1. Supply a funded provider key securely in Railway Variables.
2. Verify a real Decision-1 call and its response schema.
3. Run shadow comparisons on Russian, English and Turkish requests, including
   search, medical advice, typos, ambiguous follow-ups and prompt injection.
4. Check disagreement rate, false direct routes, p50/p95 total response latency
   and provider spend. Activate only if results improve the existing path.

No live provider acceptance has been performed without a configured key.
Rollback: `VELIA_DECISION_MODE=disabled`.

References:
- https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
- https://ai.azure.com/catalog/models/Microsoft-Decision-1
