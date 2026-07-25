# DeepAlpha Edge Watch Alerts

Edge Watch extends the existing user Watchlist with decision-state monitoring.

## Decision policy

For the forecasted side:

- `NO_TRADE`: independent edge is below 5 percentage points, or the stored analysis is only a market-aligned fallback;
- `WATCH`: edge is at least 5 percentage points, but BUY conditions are not satisfied;
- `BUY`: edge is strictly above 8 percentage points and confidence is medium or high.

The monitor sends an alert only when the decision state or forecasted side changes. Initial state is stored silently so adding a market does not immediately duplicate the analysis card.

## Tracking proposal in analysis cards

When an analysis returns `WAIT`, `WATCH`, or `NO_TRADE`, the decision block explicitly proposes adding the market to Watchlist with the button below it.

- `WAIT` / `NO_TRADE`: DeepAlpha will notify the user when the decision becomes `WATCH` or `BUY`;
- `WATCH`: DeepAlpha will notify the user when `BUY` appears or the signal weakens;
- `BUY`: no tracking proposal is inserted because the card already presents an entry decision.

The proposal is localized in Russian and English.

## Price source

The worker resolves the exact Polymarket contract. Older Watchlist rows may contain an event slug instead of the submarket slug, so the resolver loads the event and matches the stored analysis question to the correct submarket.

## Forecast source

The fair probability, side and confidence come from the latest saved user analysis. A market-aligned fallback where the saved model probability equals the saved market probability is treated as non-independent and cannot produce WATCH or BUY.

## Runtime

- enabled by default in the production bot process;
- startup delay: `EDGE_WATCH_STARTUP_DELAY_SECONDS`, default `75`;
- interval: `EDGE_WATCH_INTERVAL_SECONDS`, default `300`, minimum `60`;
- disable with `EDGE_WATCH_WORKER_ENABLED=false`;
- protected by the distributed lock `edge_watch_worker`.

## Billing

Edge-transition alerts are included with Watchlist by default to avoid charging twice for one price movement. Optional billing can be enabled with `EDGE_WATCH_BILLING_ENABLED=true`; it then uses the existing probability-alert billing policy.
