# Financial analyst: editable ledger and arbitrary date reports

Financial requests run through the normal browser chat with the selected Flash,
Quantum or PRO model. The model extracts instructions; Python Decimal performs
all calculations. The module uses the existing database and service.

## User flow

- Record income or expense: `Запиши доход 500 USD сегодня по проекту Velia`.
  No bank balance is required. If a currency or account is ambiguous, ask first.
- Get a result: `Сегодня я в плюсе или минусе?`, `Покажи доходы с 1 января
  2020 по 30 октября 2026`, `А за прошлый месяц?`.
- Edit a specific entry: `Исправь доход за 3 октября с 500 на 450 USD`,
  `Перенеси этот расход на 4 октября`, or `Удали эту запись`.
- Edit categories, account/project assignment, observed balances or budgets.
  Date/project/category filters define the analysis. Calculated totals are
  recomputed from the ledger rather than manually edited.

The planner reads account metadata, then can retrieve at most 100 matching entries
before selecting a real transaction ID for editing. Ambiguous matches require
clarification. Each accounting mutation has a before/after preview and explicit
confirmation. It preserves unrelated entries. Account-scoped revision checks
reject stale writes, and stable request IDs prevent duplicate additions on replay.
Full-dataset save remains available only for an explicit complete replacement.
Deleting the dataset or entries does not remove source messages from chat history.

## Period and calculation semantics

Any past calendar date range is supported, including today and ranges longer than
one year. Presets: today, yesterday, current week/month/year to date, and all
recorded history. UTC instants are resolved to local dates with the supplied IANA
zone (browser zone by default). Operations carry user-specified calendar dates;
intraday timestamps and time-zone conversion of historic date-only entries are
not inferred. Range endpoints are inclusive. No transactions on a day means no
recorded data, not verified zero spending. Requests beyond recorded coverage are
marked incomplete. The report provides daily income, expenses and net results.

Income minus expenses is operating cash flow, not accounting profit. Transfers,
financing and investments remain separate. Currencies, including USD and USDT,
are not implicitly converted. Rows and daily totals retain decimal precision.

Account balances are optional dated observations. Closing assets, available cash,
runway and forecasts are produced only when every selected account in a group
has an observation on the selected period end. Category-filtered cash results
never imply a whole-account balance. Editing a historic receipt does not rewrite
an independently observed bank balance. Budgets remain attached to their named
projects; moving an account does not silently change another project's budget.

Reserve, runway, 3/6/12-month and stress scenarios use 30-day monthly cash-flow
normalization with constant revenue/expenses. Short observations are flagged.
These cash scenarios exclude future financing, investments, FX/asset-price changes
and seasonality. Crypto liquidity is user classified and flagged for market risk.

## Storage, API and bounds

`FinanceStore` stores the complete ledger as one encrypted account-bound document
in `velia_finance_snapshot`, using `VELIA_WEB_GUEST_DATABASE_URL` and a
domain-separated key derived from `VELIA_WEB_SESSION_KEY`. Existing snapshots
remain readable. Keep the session key stable or migrate ciphertext during rotation.
Owner IDs come only from authentication; origin checks cover all endpoints.
Payload values are omitted from application error logs. No-store headers apply.

`POST /web-api/v1/finance/plan` uses normal model authorization/quota and disables
web search. Other actions: `context` (account metadata/local date), `load`,
`analyze` (optional period/filter query), `preview`, `mutate`, `save`, `delete`.
Mutation preview and commit accept the same edit, revision, stable operation ID,
IANA zone and report query; commit additionally requires confirmed=true.
The saved revision is checked again when committing. Failed validation commits
nothing. Responses after mutation include the recalculated report.

Capacity limits concern data size, not period length: 50 accounts, 5,000 saved
transactions, 50 budgets; 64 KiB per incoming accounting request; 100 entries per
planner lookup; the visible daily table shows the latest 31 date/currency rows.
Small CSV/TXT statements (up to 12,000 bytes) can enter through chat. Statement
content is processed by the selected model and can remain in chat history.
Flash retains its existing 1,536-token output ceiling. Large imports or more than
100 matches need a narrower selection. Model extraction quality is fixture tested;
live inference acceptance still remains before release.

## Release boundary

No automatic bank, broker, exchange or wallet synchronization, reconciliation,
trading, transfers, payments, live quote feeds or scheduled notifications are added.
Asset valuation can convert currencies using explicitly supplied dated FX rates.
No new infrastructure or provider account is created. This branch is not deployed.

Synthetic acceptance: add 500 USD income on October 3 and 100 USD expense on
October 4, 2026. October 3 is +500; October 4 is -100; the combined period is +400.
Correct income to 450: the combined result becomes +350; delete that receipt:
the combined result becomes -100. Without an observed closing balance the result
must not claim account liquidity or runway. Repeat from another account and check
isolation, then check calendar and existing chat/connectors after deployment.


## Multi-asset portfolio

The same encrypted ledger now supports dated positions for cash, crypto, metals,
stocks, bonds, funds, real estate, business interests, collectibles, commodities,
receivables and custom assets (`other`). A custom asset can use quantity=1 and an
explicit whole-object estimate. Assets can exist without bank accounts or quotes.
Each instrument has an ID, project, type, name, symbol, unit and optional network,
contract, venue, ISIN or reference. Different IDs/venues/chains stay distinct;
no ticker-only merge occurs. Units and instrument identity cannot be relabelled
across existing history. Prices must refer to the declared unit; no automatic
ounce/gram, purity, split or token-redenomination conversion is performed.

`assets[].observations[]` contains date, quantity, source and optional quote currency /
optional unit price / total cost basis with its own currency. Alternatively,
unit_cost records acquisition cost per declared unit; the server multiplies it by
quantity. Providing total basis and unit cost simultaneously is rejected. Dates are independent
historical observations, not real buy/sell events. The latest observation at or
before the report end is used; older observations are explicitly marked stale.
No future quote is used. Missing prices and cost basis remain unknown. An unpriced asset needs no assumed
quote currency; adding a price requires its currency. Partial
price updates preserve prior quantity and basis only when quantity is unchanged;
a new quantity without a new total basis makes basis unknown. New-day updates do
not silently make an old price current. Quantity zero records a closed position;
a positive remaining basis on a zero position is rejected.

Market value = quantity × unit price. Unrealized gain is calculated only when a
known total basis is denominated in the same currency as that value. The period
change compares ending value to the last observation strictly before the start
date, and is labelled position-value change, never investment return. It can
include contributions, purchases, disposals and quantity changes. Adding a holding
or mark does not create cash income or payment. Dividends and sale proceeds must
be entered as explicit cash records; tax lots, FIFO and realized-return attribution
are not implemented. Cash balances are shown separately and are not automatically
added to portfolio value, preventing a default double count.

Currencies use extensible codes (2–32 uppercase letters/digits/._:-) without a
small whitelist. Monetary amounts/quantities accept up to 30 integer and 36
fractional digits as strings, with Decimal precision 200 for calculations. Tiny
crypto quantities are not coerced to float or rounded to zero. Values produced by
quantity×price preserve fractional products; division-based metrics use bounded
Decimal precision. User estimates remain unverified values rather than certified
market prices.

`fx_rates[]` stores base, quote, positive rate, date and source: 1 base = rate quote.
`query.base_currency` requests a portfolio total. Direct or inverse rates at or
before the valuation date are used, with dates/sources/staleness exposed. No FX
triangulation, assumed stablecoin peg or live quote lookup occurs. Missing rates
or asset prices yield a partial known subtotal and an unknown complete total.
USD and USDT are converted only with an explicit rate. Original cash-flow rows
stay in their recorded currencies.

Edits: upsert_asset, set_asset_observation, delete_asset_observation, delete_asset,
set_fx_rate, through the same reviewed preview/revision-controlled commit flow.
History remains intact when a new mark is recorded. Deleting a mark differs from
deleting a position and its history. `query.asset_type`, asset_id and asset_name
support scoped selection. Context/entry lookups expose at most 20 instruments to
the planner per step; ambiguous or larger matches need a narrower selection.
Portfolio capacity is 200 instruments / 5,000 observations / 2,000 FX observations;
request-size and per-instrument limits also apply. The source prompt is chosen for
cash versus asset tasks, keeping the existing cash path free of the larger asset
schema. Inference quality still needs live synthetic acceptance before release.
