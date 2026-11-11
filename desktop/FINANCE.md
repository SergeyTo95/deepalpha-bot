# Financial analyst: first implementation

The normal browser chat routes explicit financial requests to a bounded planner
using the user's selected Flash, Quantum or PRO model. It converts user-provided
observations to a strict schema; Python Decimal performs all financial arithmetic.
No new service, subscription, provider account or paid fallback is introduced.

## Available behavior

- Analyze closing account balances and dated receipts/payments from messages or
  one CSV/TXT attachment up to 12,000 bytes. The statement content enters the
  selected inference model and the existing chat history.
- Separate projects and currencies, including USD and USDT. No FX assumptions.
- Exclude restricted balances and non-liquid assets; deduct obligations due now.
- Separate operating receipts/expenses from transfers, financing and investments.
- Calculate monthly expense/burn, reserve shortfall (default six months), runway,
  3/6/12-month scenarios, expense budgets and an expense +20%/revenue -20% scenario.
- Save or replace one complete snapshot per account after a confirmation showing
  the source, reporting period, account balances and calculated report. Revision
  checks prevent a stale tab overwriting a newer snapshot. Deletion also requires
  confirmation. Deleting the snapshot does not delete the source chat.

## Scope and limitations

This is a manual-data MVP. Bank/broker/exchange/wallet synchronization and external
financial credentials are not implemented. There are no trade, transfer, payment
or withdrawal endpoints. The existing Google/Shopify connections are unchanged;
this module does not automatically combine those integrations with the snapshot.

Balances are dated at the closing reporting date; flows are inclusive of both
period endpoints and are not added to closing balances again. Amounts must be
non-negative decimal strings with at most eight fractional places; operation
kinds define direction. Schema limits: 50 accounts, 100 operations, 50 budgets,
1–366 past reporting days. Duplicate IDs fail validation. Repeated actual
operations under different IDs still require statement review; there is no bank
reconciliation. Unknown observations must be clarified rather than filled as zero.

Forecasts use a 30-day month and constant historical operating cash flow. They
exclude future financing, investments, transfers, price/FX changes and seasonality.
They are scenarios, not predictions or accounting profit. Short samples and empty
transaction sets carry warnings. An empty set cannot establish zero spending.
Crypto liquidity is explicitly user classified and carries a market-risk warning.

Planner extraction is model-dependent: incomplete/truncated JSON is rejected.
Flash retains its existing 1,536-token ceiling; large statements may need smaller
periods or another selected model. Automated tests use inference fixtures; real
model extraction quality still needs deployment acceptance with synthetic data.

## Storage and operation

`FinanceStore` uses the existing `VELIA_WEB_GUEST_DATABASE_URL` and
`VELIA_WEB_SESSION_KEY`. It creates only `velia_finance_snapshot` in that database.
Payloads are Fernet encrypted with a domain-separated derived key, bound inside
the ciphertext to an HMAC account index. The session key must remain stable;
key rotation needs an explicit migration of existing ciphertexts.

The API accepts only the authenticated session's owner; client-provided owner IDs
are rejected. Origin/header checks protect all routes, including reads. Responses
use the gateway's no-store headers. Application error logs omit financial values.
One snapshot request is limited to 64 KiB. Persistence is unavailable without the
existing database/key; explicitly supplied data can still be calculated.

`POST /web-api/v1/finance/plan` uses the normal model authorization, quota and
streaming path while disabling internet search. `/finance/analyze` accepts a new
snapshot or reads the saved one. `/finance/load`, `/finance/save` and
`/finance/delete` are account scoped; save/delete require confirmation and revision.

## Acceptance

Synthetic chat example (no real account data):

> Рассчитай финансовый резерв Velia за 1–30 октября 2026. На 30 октября на
> банковском счёте USD остаток 1000, из них заблокировано 100, к немедленной
> оплате 50. Деньги ликвидны. Поступление от продаж 3 октября — 200 USD,
> хостинг 4 октября — 300 USD. Других операций нет. Бюджет расходов 250 USD
> в месяц. Источник — мои тестовые данные.

Expected: available 850 USD; monthly expense 300; burn 100; six-month reserve
1,800; reserve gap 950; runway 8.5 months; stress runway 4.25 months;
forecast balances 550/250/-350 at 3/6/12 months. No USD/USDT aggregation.

Then ask `Покажи сохранённую финансовую сводку`: before an explicit save there
must be no saved snapshot. To save, send the complete observations with
`Сохрани эти финансовые данные` and review the confirmation. Repeat from a second
account to verify isolation. Check calendar, ordinary chat and existing
connections after deployment. This branch does not perform a deployment.
