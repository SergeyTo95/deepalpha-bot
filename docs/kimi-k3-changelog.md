# Kimi K3 completion-budget hotfix

- Replaced deprecated `max_tokens` with `max_completion_tokens`.
- Raised the default completion budget for reasoning-heavy DeepAlpha features.
- Prevented the legacy `KIMI_MAX_OUTPUT_TOKENS=1200` value from shrinking K3 below the safe feature default.
- Added explicit `finish_reason=length` detection.
- Added bounded retry with a doubled completion budget.
- Preserved Gemini fallback when Kimi remains truncated.
- Added structured request start, success, and failure logs without prompts or API keys.
