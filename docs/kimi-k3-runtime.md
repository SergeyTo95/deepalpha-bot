# Kimi K3 runtime settings

Recommended Railway values for text analysis:

```env
KIMI_ENABLED=true
KIMI_MODEL=kimi-k3
KIMI_REASONING_EFFORT=high
KIMI_MAX_COMPLETION_TOKENS=8192
KIMI_MAX_COMPLETION_TOKENS_CAP=32768
KIMI_TIMEOUT_SECONDS=120
KIMI_MAX_RETRIES=1
LLM_PRIMARY_PROVIDER=kimi
LLM_TEXT_PROVIDER=kimi
LLM_FALLBACK_PROVIDER=gemini
LLM_VISION_PROVIDER=gemini
```

`KIMI_MAX_OUTPUT_TOKENS` is a deprecated compatibility alias. New deployments should leave it empty or remove it.

Only the dedicated Telegram service may set `BOT_POLLING_ENABLED=true`. All WebApp, worker, preview, and legacy bot services must set it to `false`.

Expected runtime log sequence for a successful Kimi call:

```text
KIMI_REQUEST_START ...
KIMI_REQUEST_SUCCESS ...
```

If K3 reaches its completion budget, the gateway records `reason=completion_length`. It can retry once with a larger `max_completion_tokens` value and then fall back to Gemini if the response is still truncated.
