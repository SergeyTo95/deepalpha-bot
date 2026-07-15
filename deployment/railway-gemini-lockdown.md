# Railway Gemini cost lockdown rollout

1. Deploy the migration `migrations/20260715_add_gemini_call_attempts.sql` before enabling Gemini.
2. Keep `GEMINI_ENABLED=false`, `GEMINI_BACKGROUND_ENABLED=false`, and all worker flags false for rollout.
3. Enable foreground features one at a time with explicit daily/request limits.
4. Enable background workers only with `GEMINI_BACKGROUND_ENABLED=true`, the worker flag, and the feature flag.
5. Multiple replicas coordinate background cycles through `background_locks` with `BACKGROUND_LOCK_TTL_SECONDS`.
6. Roll back by setting `GEMINI_ENABLED=false`; this blocks admin, foreground, background, retries, fallback, text, and vision.
