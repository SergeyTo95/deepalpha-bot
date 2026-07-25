# Kimi K3 smoke test

Use a normal active Polymarket market, not a short-term BTC up/down market.

After sending the market link, the production logs should show:

```text
KIMI_REQUEST_START feature=signal_generation ...
KIMI_REQUEST_SUCCESS feature=signal_generation ...
```

A successful DeepAlpha result must include an independent probability instead of `model not built` / `модель не построена`.

If logs show `reason=completion_length`, verify that the next attempt uses a larger `max_completion_tokens` value. If Kimi still cannot complete, Gemini fallback should produce the final answer.
