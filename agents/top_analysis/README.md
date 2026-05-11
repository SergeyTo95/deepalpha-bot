# DeepAlpha Top Analysis (Internal Skeleton)

This package defines the internal architecture skeleton for future **Top Analysis** / **DeepAlpha Top Analysis** extended forecast mode.

## Scope

- Skeleton-only internal orchestration package.
- No external API calls.
- No provider SDK dependencies.
- No Telegram, admin/settings, billing, callback, or normal analysis flow integration.

## Design Principles

- DeepAlpha remains a standalone product in all user-facing behavior.
- Provider/model details are internal implementation concerns.
- Specialist roles are isolated by responsibility to support future extension.

## Specialists

- `ResearchSpecialist`: future evidence planning and source-quality assessment.
- `SocialSignalSpecialist`: future social narrative/sentiment signal analysis.
- `RiskAuditor`: future red-team challenge and forecast-risk auditing.
- `ChiefForecaster`: future synthesis and final Top Analysis structure.

## Status

Current implementation returns safe placeholders and is designed not to crash orchestration.

Future pull requests can connect this package to:

- provider routing and execution,
- settings/admin gates,
- billing controls,
- UI and delivery.
