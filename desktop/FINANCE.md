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
These scenarios exclude future financing, investments, FX/asset-price changes
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
trading, transfers, payments, FX conversion or scheduled notifications are added.
No new infrastructure or provider account is created. This branch is not deployed.

Synthetic acceptance: add 500 USD income on October 3 and 100 USD expense on
October 4, 2026. October 3 is +500; October 4 is -100; the combined period is +400.
Correct income to 450: the combined result becomes +350; delete that receipt:
the combined result becomes -100. Without an observed closing balance the result
must not claim account liquidity or runway. Repeat from another account and check
isolation, then check calendar and existing chat/connectors after deployment.
