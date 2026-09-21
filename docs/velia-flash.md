# VELIA Flash / PRO

Flash is a free-to-user **text chat** backed by self-hosted PrismML Bonsai 2
27B PQ2_0. Infrastructure is paid by the project. No paid provider fallback,
web search, file preprocessing, media generation or agent planner runs on this
route. PRO retains the established provider and tool stack. The request field
`chat_mode` is `pro` by default for old clients; accepted values are `pro` and
`flash`. Unknown values fail before generation.

The official card reports 98.2% of its Qwen3.8-27B FP16 average, not a guarantee
for individual tasks. The advertised PTQ1_0 weights are 5,946,648,928 bytes;
this CPU service uses the faster PQ2_0 packing at 7,206,168,928 bytes. Memory also
includes attention caches and compute buffers. The advertised maximum context
is 262,144, while this CPU deployment allocates 2,048 tokens, one generation
at a time, and 128 output tokens. Input including system/history is capped at
384 tokens to bound CPU latency; oldest history is dropped first, and a current
question that exceeds the limit is rejected. Images require an additional vision projector
and separate acceptance; the initial Flash client advertises text only.

## Configuration

Build a separate private Railway service with root directory `bonsai_worker`
and its Dockerfile. It needs a dedicated CPU/RAM allocation; it must not run
inside the bot process. Use `/health` with a 300-second startup allowance.
No public domain is needed. Match the context variable on both services.

Worker:
- `VELIA_FLASH_API_KEY`: random service credential, at least 32 characters.
- `PORT=8080`
- `VELIA_FLASH_CPU_THREADS=8`
- `VELIA_FLASH_CONTEXT_TOKENS=2048`
- `VELIA_FLASH_REPACK=false`

The worker explicitly disables reasoning and the optional RAM prompt cache.
Weight repacking is disabled to fit the tested 8 GB service. Responses use the
`deepseek` parser to remove even empty thought wrappers from final content.
Before enabling Flash, run `python3 /opt/bonsai/probe.py` as a one-shot Railway
start command with restart policy `NEVER`. It checks three synthetic completions,
prints latency and token usage, and always terminates the model afterward. It
uses an ephemeral service key, accepts no user traffic and needs no public domain.
An HTTP health check alone does not prove that inference is usable.

The isolated PostgreSQL integration suite can also be run with
`docker build -f ci/Dockerfile.velia-flash-verify -t velia-flash-verify .`
and `docker run --rm velia-flash-verify`. It creates its own temporary database.

Backend:
- `VELIA_FLASH_ENABLED=false` until real inference acceptance passes.
- `VELIA_FLASH_BASE_URL=http://<worker-private-domain>:8080`
- `VELIA_FLASH_API_KEY`: same credential, never sent to Android.
- `VELIA_FLASH_TIMEOUT_SECONDS=180`
- `VELIA_FLASH_MAX_OUTPUT_TOKENS=128`
- `VELIA_FLASH_MAX_INPUT_TOKENS=384`
- `VELIA_FLASH_CONTEXT_TOKENS=2048`
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

## Railway acceptance, 2026-09-21

On an 8-vCPU / 8-GB service, PQ2_0 with repacking disabled, eight threads and a
2,048-token allocation passed arithmetic, Python and Russian probes. Total
latencies were 17.00, 11.42 and 17.37 seconds respectively; generation was about
3 tokens/second. The latter two requests reused 29 system-prefix tokens. Both
`/apply-template` and `/tokenize` were exercised. Deployment:
`6f3bdc14-f96a-4105-b6e3-b28fa5bccec1`.

PTQ1_0 with four threads needed 145.96 seconds for the same arithmetic prompt,
with 138.37 seconds to first content. PQ2_0 with repacking enabled exited during
loading; its exit reason was not captured. These are short synthetic checks,
not a reproduction of the vendor benchmark or a concurrency/load test.

For a deployment smoke test using the full backend prompt and private network,
the disposable PostgreSQL verifier can run `ci/probe-velia-flash.py` after its
tests. Set `VELIA_FLASH_RUN_REAL_PROBE=1` and the Flash endpoint/key/enabled
variables on that isolated verifier. It sends one synthetic coding request,
checks streamed final content, provider and zero user cost, and creates no chat.

Sources:
- https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf
- https://github.com/PrismML-Eng/Bonsai-demo
- https://docs.railway.com/pricing/plans
