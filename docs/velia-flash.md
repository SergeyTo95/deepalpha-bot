# VELIA Flash / PRO

Flash is a free-to-user **text chat** backed by self-hosted PrismML Bonsai 2
27B PTQ1_0. Infrastructure is paid by the project. No paid provider fallback,
web search, file preprocessing, media generation or agent planner runs on this
route. PRO retains the established provider and tool stack. The request field
`chat_mode` is `pro` by default for old clients; accepted values are `pro` and
`flash`. Unknown values fail before generation.

The official card reports 98.2% of its Qwen3.8-27B FP16 average, not a guarantee
for individual tasks. Weights are 5,946,648,928 bytes; process memory also
includes attention caches and compute buffers. The advertised maximum context
is 262,144, while this CPU deployment defaults to 4,096 tokens, one generation
at a time, and 256 output tokens. Images require an additional vision projector
and separate acceptance; the initial Flash client advertises text only.

## Configuration

Build a separate private Railway service with root directory `bonsai_worker`
and its Dockerfile. It needs a dedicated CPU/RAM allocation; it must not run
inside the bot process. Use `/health` with a 300-second startup allowance.
No public domain is needed. Match the context variable on both services.

Worker:
- `VELIA_FLASH_API_KEY`: random service credential, at least 32 characters.
- `PORT=8080`
- `VELIA_FLASH_CPU_THREADS=4`
- `VELIA_FLASH_CONTEXT_TOKENS=4096`

Backend:
- `VELIA_FLASH_ENABLED=false` until real inference acceptance passes.
- `VELIA_FLASH_BASE_URL=http://<worker-private-domain>:8080`
- `VELIA_FLASH_API_KEY`: same credential, never sent to Android.
- `VELIA_FLASH_TIMEOUT_SECONDS=180`
- `VELIA_FLASH_MAX_OUTPUT_TOKENS=256`
- `VELIA_FLASH_CONTEXT_TOKENS=4096`
- `VELIA_FLASH_USER_DAILY_LIMIT=20`
- `VELIA_FLASH_GLOBAL_DAILY_LIMIT=200`

The `/me` response advertises `features.chat_flash`. The Android selector is
disabled while it is false. Backend requests independently enforce the flag.
Existing chat ownership, idempotency and pending-turn expiry are retained.
A shared PostgreSQL advisory lock bounds Flash concurrency across web replicas;
the existing per-user lock prevents overlapping PRO and Flash turns. Flash
attempts reserve quota in persisted assistant rows (`provider=bonsai`) before
generation, and are excluded from PRO's daily message/cost limits. Replays
cannot change mode. A failed Flash request never becomes a paid request.

Weights revision `6ed5e12bf84b7a63069882c91dd9e9218647d17b` and the official
runtime `prism-b10709-9a9394a` are pinned and SHA-256 checked during build.
The model is Apache-2.0; upstream license files remain in the runtime image.

Sources:
- https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf
- https://github.com/PrismML-Eng/Bonsai-demo
- https://docs.railway.com/pricing/plans
