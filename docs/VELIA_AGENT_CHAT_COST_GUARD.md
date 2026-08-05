# VELIA Agent Chat Cost Guard

VELIA ordinary-chat planning uses the provider-neutral adapter in `services/kimi_gateway_service.py`.

For the internal feature `velia_agent_chat_plan` only:

- completion tokens are clamped to 400–1400;
- the default planner request remains 900 tokens;
- only one foreground attempt is allowed;
- low reasoning effort is used;
- the existing planner preflight and post-response USD budget checks remain active.

All other Kimi features keep their existing completion-limit behavior.

The guard must stay selective. Do not lower limits for Developer, vision, decision, signal, summary or other analytical features through this adapter.
