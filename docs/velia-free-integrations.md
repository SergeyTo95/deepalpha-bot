# Free utility APIs for Velia

This change adds server APIs and Agent Core tools. Deploy the commit to the
existing backend to expose them; it does not create infrastructure, activate
provider accounts, replace Quantum, or modify existing model billing. Mobile
voice UI wiring is separate: the new speech endpoints do not automatically
replace the client's current voice implementation.

## Available without a provider key after deployment

All endpoints use the existing mobile Bearer authentication and start with
`/mobile-api/v1/utilities`. Identity comes from the token; payload user IDs are
ignored. Responses have `Cache-Control: no-store`.

| Endpoint | Request | Result |
| --- | --- | --- |
| `GET /status` | — | Configuration readiness; not a live provider health check or remaining quota |
| `POST /calculate` | `{"expression":"0.1+0.2"}` | Exact rational and decimal answer, without model/API billing |
| `POST /knowledge/search` | `{"query":"Quantenphysik","language":"de"}` | Up to five Wikipedia excerpts and source URLs |
| `POST /papers/search` | `{"query":"multilingual speech recognition"}` | Up to five Crossref publication records and DOIs, not full papers |

Agent Core registers `velia.calculate`, `velia.knowledge.search`,
`velia.papers.search`, and `velia.web.search`. They are callable through the
existing Agent jobs API and appear in its planner tool catalog when Agent Core
is enabled. This does not make every ordinary chat message run a tool or bypass
the established planner/approval flow.

Numeric computation uses Python's standard-library `Fraction` with a bounded
AST interpreter. It does not evaluate Python, download a model, or depend on
SymPy. Only numbers, parentheses, +, -, *, /, and integer powers -12..12 are
accepted. Expressions, AST size, and intermediate rational sizes are bounded.

Wikipedia supports edition codes such as `ru`, `en`, `tr`, `de`, `es`, `ar`,
`zh`, `hi`, `pt`, `fr`, `ja`, and others; there is no three-language allowlist.
Unsupported editions return an error; there is no silent language substitution.
Excerpts must retain source attribution and are untrusted evidence, not model
instructions. Wikipedia content reuse remains subject to its content licence.
Crossref supplies bibliographic metadata; no abstracts or full-text copies are
retrieved. Search queries are sent to the selected provider. Both APIs have a
small in-process cache, one request/second/provider/process and at most two
concurrent outbound knowledge requests/process. Their own limits still apply.

## Tavily free plan

The existing Tavily search now shares a durable monthly budget across normal
web search and DeepAlpha research. Default `VELIA_FREE_APIS_ONLY=true` restricts
these search paths to Tavily. Existing Serper/Bing and Brave paths require an
explicit operator opt-out (`false`), which permits their existing billing.
This flag does not control existing Quantum/Kimi/Gemini generation charges.

Configure in the existing backend's secret settings:

```dotenv
WEB_SEARCH_PROVIDER=tavily
WEB_SEARCH_API_KEY=<dedicated Tavily free-account key>
VELIA_TAVILY_FREE_PLAN_CONFIRMED=true
VELIA_TAVILY_MONTHLY_CREDITS=900
```

Verify the provider account is on its free plan with pay-as-you-go disabled
before confirming it. Use a dedicated account/key. Account usage from other
apps or keys is not visible to this ledger. If this month's account credits
have already been consumed, lower the cap to the available free balance before
activation. Do not overwrite a sealed existing key merely because it cannot
be read back.

`POST /search` accepts `{"query":"..."}` and keeps the query's language.
The request always uses basic search and disables auto parameters, answer
generation, and raw-content extraction. The cap can be lowered to zero but
cannot be raised above 900 through the free-mode setting. Authenticated utility
and Agent calls additionally have a 20-search/day/user cap. Legacy internal
search callers retain the shared monthly cap. There is no paid-provider
fallback on failure/exhaustion. Existing best-effort chat callers receive an
empty evidence list; the strict utility endpoint returns a safe error code.

## Azure Speech F0: prepared, disabled until configured

```dotenv
VELIA_AZURE_SPEECH_ENABLED=true
AZURE_SPEECH_TIER=F0
AZURE_SPEECH_REGION=<your resource region>
AZURE_SPEECH_KEY=<dedicated F0 Speech resource key>
VELIA_AZURE_TTS_MONTHLY_CHARACTERS=450000
VELIA_AZURE_STT_MONTHLY_SECONDS=14400
```

Create/select an actual **F0** resource in Azure; the adapter checks declared
configuration, not the subscription's billing tier through Azure management.
An S0 key mislabelled F0 can still incur charges. Use a dedicated F0 resource;
usage from other applications is not observable. The conservative app caps are
450,000 UTF-16 character units for TTS and four audio hours for STT/month,
plus 10,000 characters and 300 seconds/day/user. F0 has low concurrency and rate
limits and is suitable for a small launch, not unlimited simultaneous users.
No automatic retries or paid fallback are implemented.

| Endpoint | Request | Result |
| --- | --- | --- |
| `GET /speech/voices` | — | Region's standard neural voices with locale and gender; cached for one hour |
| `POST /speech/synthesize` | `{"text":"Bonjour","voice":"fr-FR-DeniseNeural"}` | MP3, up to 2,000 input characters |
| `POST /speech/transcribe?language=fr-FR` | Raw WAV bytes | Recognized text |

STT accepts validated PCM16 mono 16 kHz WAV, at most 60 seconds and 2 MiB.
The client must provide the locale and convert microphone audio to this format.
There is no automatic language detection in this short-audio adapter. TTS
voices/locales come from Azure, not a hardcoded three-language list. Voice
availability and STT language coverage differ by provider/region. User text is
XML-escaped; arbitrary SSML, custom voices and user-supplied endpoints are not
accepted. Text/audio are sent to Azure; the ledger stores neither.

## Budget behavior and rollout

On the first budgeted request the existing PostgreSQL database gets a small
`velia_free_api_usage` table. No extension or new database is required.
Reservations are atomic and committed **before** HTTP. They survive restarts
and replicas; UTC calendar months/days are used. Failed/ambiguous provider
requests still count conservatively. Missing DB, exhausted cap, missing
credentials or unconfirmed free plan stop external requests. Dedicated
provider resource configuration remains necessary: app limits cannot guarantee
an account-wide free balance if other consumers share the resource.

Run the free integration tests and isolated PostgreSQL concurrency tests before
rollout. Preserve the existing deployment checks and projects. Confirm the
status endpoint after deployment; make one bounded request per configured
provider, and wire client controls to these endpoints as needed.

Large local ASR/LLMs, Docling/Crawl4AI browser workers, paid embeddings/reranking,
and ElevenLabs are not installed or enabled by this change. Existing local
PDF/DOCX/text extraction and Tesseract OCR continue to operate. “Free API” does
not eliminate the existing server, network, model-generation or support costs.

Sources checked 2026-10-27:

- https://docs.tavily.com/documentation/api-credits
- https://docs.tavily.com/documentation/api-reference/endpoint/search
- https://azure.microsoft.com/en-us/pricing/details/speech/
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short
- https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support
- https://www.mediawiki.org/wiki/API:Search
- https://www.crossref.org/documentation/retrieve-metadata/rest-api/
