# Free API tiers — prepared rollout

Set `VELIA_FREE_API_TIERS_ENABLED=true` only with the existing Z.ai Standard API key and the existing Groq **Free** account key present. The flag is opt-in; disabled keeps the previous deployment behavior. Enable it on the account backend first and on the gateway after the backend is healthy.

| Public mode | Pinned upstream | Text | Voice |
| --- | --- | --- | --- |
| VELIA Flash | Z.ai `glm-4.5-flash` | Thinking disabled | Same model, short spoken answer |
| VELIA Quantum | Z.ai `glm-4.7-flash` | Thinking disabled | Same model, short spoken answer |
| VELIA PRO | Groq `openai/gpt-oss-120b` | Medium reasoning | Same model, low reasoning |

Z.ai lists both Flash models at zero token price. Groq is free within the account's Free plan limits; the model has paid pricing on Developer accounts, so no paid-plan upgrade is part of this rollout. This is not a promise of unlimited quota or zero hosting cost. No paid model ID, provider built-in search, Coding Plan endpoint, or automatic cross-model fallback is selected. Errors stop the selected route.

Normal chat uses one streaming completion, or two when the selected model calls one bounded local web-search function. Existing search keys/quotas remain; no new paid search integration is enabled. No separate Flash planner, Decision call, Bonsai prompt render/tokenize request, or answer-review model call precedes normal free-tier chat. Desktop function tools still use validated tool messages on the pinned selected model. No new Railway service or project is needed.

The browser speech input and device/Edge output pipeline are unchanged. Voice mode passes the selected mode through to its own model, uses account-language defaults, and preserves the existing call lifecycle and transcript corrections. Native inference is not a speed guarantee: measure physical voice latency and multilingual answer quality after deployment.

Existing account ownership, request idempotency, generation locks, guest 30-message trial, application quotas, and PRO token-access rules stay enforced. New saved-chat requests fail closed if the gateway is upgraded before a backend advertising the free tiers. PRO image uploads use local text/OCR extraction in this mode, rather than a paid vision model. General image understanding is not claimed.

The old Bonsai worker should be stopped only after the new chat and Browser Agent release checks succeed; this patch does not stop or delete it. PostgreSQL persistence tests are mandatory in the existing build CI. The existing live Flash predeploy probe now exercises the selected free API route; configuration checks ensure all three routes exist. Further live model/phone latency measurements remain necessary.
