# Paid subscription changes: implementation gate

Status: **sandbox preparation, not a released subscription-change feature**.
Existing live checkout and fulfillment are unchanged. No plan-change route,
background scheduler, live upgrade POST or credit mutation is introduced here.

The optional pre-deploy discovery now verifies `VELIA_CREEM_TEST_API_KEY` inside
the Railway container. It makes exactly two GET requests to the test origin
(`products/search` and `subscriptions/search`, first page of 25). The sanitized
`VELIA_CREEM_SANDBOX_DISCOVERY` log contains matching test product/subscription
IDs and statuses, without names, customer details, email, URLs or credentials.
It cannot execute a plan change. Missing keys skip the probe; authentication or
network errors are reported without blocking mandatory production checks.
This source must be deployed manually before adding the key alone can run it.

## Approved first experiment

The owner supplied sandbox Pro `prod_7e0Yag0LFPiyVAx4OUs4IG` and Max
`prod_1TxrSlOznctfgTo7mBNXMq`. Existing sandbox Plus is
`prod_fvrGJW4vX4KZfzJfXBZBO`, with discovered active test subscription
`sub_1zCm0jsSzizMUIagDO1oyp`. These references are separate from the live catalog.

After mandatory pre-deploy checks, `--predeploy-test-plan-change` can perform
ONE test-only Plus → Pro change with `proration-charge-immediately`. Preflight
requires the exact monthly USD Plus/Pro products at $14.99/$29.99 with inclusive
tax, and a canonical paid active Plus subscription. Max cannot block this
Plus → Pro experiment. A mismatch logs
`sandbox_plan_settings_mismatch` or another sanitized code and does not charge.

The observed sandbox Plus still used exclusive tax. After verifying its exact
test identity, price and paid subscription, the runner may set only its
`tax_mode` to `inclusive` via the documented product PATCH API and canonical GET
readback. It cannot change a price, edit Pro/Max, or address a live product.
This setting operation is idempotent; the separate durable subscription claim
still guards the charge attempt. The upgrade also rechecks its expected source
immediately before POST.

Before the POST, a unique run ID and intent digest are committed to a separate
`velia_creem_sandbox_runs` audit table. It never reads or modifies user balances,
live subscriptions or fulfillment tables. Concurrent invocations, timeouts,
lost readbacks and redeploys cannot repeat the POST: a claimed run only performs
read-only reconciliation. Even a crash before POST deliberately needs manual
inspection, rather than guessing that retry is safe. No automatic Pro → Max or
downgrade experiment is chained onto this first observation.

The sanitized `VELIA_CREEM_SANDBOX_EXPERIMENT` log records the invoice amounts,
tax and period timestamps. `state: observed` means readback was recorded, not
that live switching is ready; `new_invoice_observed` and invoice paid status
must be checked. An asynchronous invoice may need later read-only inspection.
Actual execution still requires the owner to deploy this source manually.

## Customer policy to implement after provider validation

| Action | Intended behavior | Credits |
| --- | --- | --- |
| Upgrade | Confirm a provider-verified payable amount before charging | Only the paid additional allowance, never a second full monthly pack |
| Downgrade | Lower price and allowance at next renewal, zero charge today | Preserve existing balance; next successful renewal grants new plan allowance |
| Separately purchased tokens | Preserve when changing plan | No reset or deletion |

`change_estimate()` is an integer-cent reference calculation assuming the current
billing cycle stays unchanged. It uses actual invoice timestamps, floors extra
credits and rounds illustrative money half up. It always returns
`estimate_only: true` and `can_confirm: false`. Example at exactly half a period:
Plus → Pro is an **estimate** of $7.50 and 900 additional credits. This must not
be presented as a Creem invoice or used to authorize a charge.

Creem's documented `POST /v1/subscriptions/{id}/upgrade` is a mutation, not a
quote. Supported values are `proration-charge-immediately`, `proration-charge`
and `proration-none`. An older official reference states immediate mode starts
a new cycle; current endpoint docs do not resolve that accounting detail.
Neither the current subscription SDK nor the endpoint inventory exposes an
invoice-preview operation. Customer-portal documentation describes cancellation
and invoices, without confirming a hosted plan-change confirmation screen.

Sources checked 2026-11-13:

- https://docs.creem.io/api-reference/endpoint/upgrade-subscription
- https://docs.creem.io/skills/creem-api/REFERENCE
- https://docs.creem.io/features/customer-portal
- https://github.com/armitage-labs/creem/blob/main/packages/sdk/src/sdk/subscriptions.ts

## Sandbox evidence collection

The probe accepts only `VELIA_CREEM_TEST_API_KEY` with the `creem_test_` prefix.
It cannot fall back to the live key. Its origin is fixed to `test-api.creem.io`.
It checks the subscription, product, customer and latest invoice from canonical
GETs before its one explicitly requested sandbox upgrade. It never touches the
VELIA database. Output excludes customer identity, email, credentials and URLs.

Use a **paid sandbox subscription**, sandbox monthly USD products matching the
intended plan prices, and its actual sandbox identifiers. Do not put keys in
command-line arguments, chat, GitHub files or reports. The existing sandbox
webhook secret is not an API key. Provide the API key in a secret environment.

Read-only inspection:

```bash
python -m scripts.velia_creem_plan_change_probe --discover
python -m scripts.velia_creem_plan_change_probe --subscription sub_TEST
```

One explicit test-only change (use separate disposable test subscriptions for
each scenario so invoice history is unambiguous):

```bash
python -m scripts.velia_creem_plan_change_probe --subscription sub_TEST \
  --target-product prod_TEST --behavior proration-charge-immediately \
  --apply-test-change
```

After a timeout or unexpected response, inspect read-only. **Do not repeat the
mutation automatically**. A successful response also requires readback and later
webhook/renewal verification; the report intentionally never certifies live
readiness. A missing new invoice during readback can mean asynchronous provider
processing, not a free upgrade.

Required sandbox cases:

1. Plus → Pro, Plus → Max, Pro → Max: record actual amount, tax representation,
   invoice and period dates; check whether a new billing cycle starts.
2. Max → Pro, Max → Plus, Pro → Plus with `proration-none`: verify zero charge
   now and exact time the product changes; observe the next paid renewal.
3. Failed payment, customer authentication/3DS and timeout after provider action.
4. Signed webhook ordering and retries, renewal, partial and full refunds.

## Required live integration before enabling any change button

The existing fulfillment deliberately rejects a different product for an
existing order/subscription and requires full-price monthly invoices. Do not
remove those checks to get an upgrade working. A correct follow-up must:

- Persist an immutable, account-bound, expiring offer with the **verified**
  amount/currency/tax, subscription version, target product and intended cycle.
  If exact provider preview/confirmation cannot be obtained, choose and explain
  a different customer billing policy before implementing immediate charges.
- Persist change intent before POST, serialize by account/subscription, and
  reconcile ambiguous outcomes without issuing another debit.
- Keep charge-specific plan, price and period snapshots. Do not reinterpret
  original charges or refunds using the subscription's newest plan.
- Distinguish a paid upgrade adjustment from a normal monthly renewal. Bind
  every grant to its canonical paid invoice and durable change intent. Preserve
  existing credits and revoke only the refunded invoice's own grant.
- Keep a scheduled downgrade separate from current paid access. Switch allowance
  only on the verified next-period invoice, never just a browser return.
- Test concurrency, forged amount/customer/product, expired offers, retries,
  out-of-order old-plan refunds, failed payment, downgrade renewal and cancel.

No feature flag can make the current preparation live: there is intentionally
no live mutation adapter or API route in this change.
