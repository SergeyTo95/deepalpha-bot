# Free Opportunity Pre-Scanner

DeepAlpha's Find Opportunity flow runs a deterministic Polymarket pre-scan by default.

## Cost boundary

The free mode:

- calls only public Polymarket market/event endpoints;
- does not import or instantiate NewsAgent or DecisionAgent;
- does not call Kimi, Gemini, `llm_service`, or any paid AI provider;
- does not calculate a fair probability, edge, WATCH, or BUY;
- returns candidates for a later user-triggered normal analysis.

Paid deep analysis remains fail-closed behind:

```env
OPPORTUNITY_PAID_ANALYSIS_ENABLED=false
```

Only setting this variable explicitly to `true` restores the legacy NewsAgent + DecisionAgent pass.

## Free ranking inputs

Each active binary market is scored from public metadata:

- liquidity;
- 24-hour volume;
- total volume;
- whether the line still has meaningful price discovery;
- time until resolution;
- 24-hour price movement;
- number of contracts in the same event;
- whether the question appears measurable using objective data.

The score is a triage score, not an expected-value estimate.

## Safety filters

The scanner rejects:

- inactive or closed markets;
- malformed/non-binary prices;
- nearly resolved 99/1 markets;
- markets below both liquidity and 24-hour volume minimums;
- markets closing too soon;
- markets whose deadline is too far away;
- test/demo/noise markets.

At most two contracts from one event can occupy the returned shortlist. This prevents a single multi-range event from filling the entire result.

## Runtime settings

```env
FREE_OPPORTUNITY_SCAN_LIMIT=100
FREE_OPPORTUNITY_RESULT_LIMIT=10
FREE_OPPORTUNITY_CACHE_SECONDS=120
FREE_OPPORTUNITY_MIN_LIQUIDITY=500
FREE_OPPORTUNITY_MIN_VOLUME_24H=100
FREE_OPPORTUNITY_MIN_HOURS_TO_CLOSE=6
FREE_OPPORTUNITY_MAX_DAYS_TO_CLOSE=365
OPPORTUNITY_PAID_ANALYSIS_ENABLED=false
```

The in-memory cache avoids repeated public API calls when several users request the scanner within the cache window.

## Future paid layer

A later minimal-cost phase can take only the top 3–5 candidates and run a controlled deep analysis. That phase must keep its own daily request and cost caps and remains disabled in this version.
