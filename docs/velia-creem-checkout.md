# VELIA Creem subscriptions

## Release and activation

Deploy the account authority (`deepalpha-bot`) in **production** first, then
`velia-desktop-gateway` in **deepalpha-bot-pr-577**. The public website currently
runs on that gateway environment, but its canonical account authority is
`https://deepalpha-bot-production.up.railway.app`. Billing and all balance
changes must stay with that production authority. A billing deployment in the
gateway's preview environment cannot serve production account payments.

Preserve the authority's current production ancestry (Google Workspace/calendar
backend) and the gateway's independent ancestry. This release creates no
service, database or project. Do not switch account origins to the preview
backend or create a separate billing authority.

In **Creem live mode**, create a webhook with this exact endpoint:

`https://velyoncore.ai/web-api/v1/billing/creem/webhook`

Select `checkout.completed`, `subscription.active`, `subscription.paid`,
`subscription.canceled`, `subscription.scheduled_cancel`, `subscription.expired`,
`subscription.unpaid`, `subscription.update`, `subscription.past_due`,
`subscription.paused`, `subscription.trialing`, `refund.created`, `dispute.created`.
An empty event list means all events in Creem's API. Creem credit-meter events
are unrelated to VELIA credits and are not required.

Set these variables **directly in Railway on `deepalpha-bot`, environment `production`**, never in chat,
browser JavaScript, a repository or logs:

| Variable | Value |
| --- | --- |
| `VELIA_CREEM_API_KEY` | Live Creem API key |
| `VELIA_CREEM_WEBHOOK_SECRET` | Secret of the live webhook above |
| `VELIA_CREEM_ENABLED` | `true` after completing configuration |

The API key must permit checkout creation, product/subscription/transaction
reads, customer billing portal creation, and webhook/secret reads. No live Creem
key is needed in the gateway. Keys entered on `deepalpha-bot-pr-577` are not
inherited by production and must be entered in production separately. Its
existing `VELIA_DESKTOP_AUTH_ORIGIN` continues to point at the production account
authority; the existing `DATABASE_URL` there owns balances.

Purchasing fails closed until the server confirms all three live products,
prices, monthly recurrence, USD, active status, exclusive tax mode, and the
registered enabled HTTP webhook, event coverage and exact signing secret.
Provider-side settings have not been verified without a live key. Do not
announce live acceptance before this preflight and the deployed flow succeed.

## Approved products

| Plan | USD/month | Credits/paid month | Live product ID |
| --- | ---: | ---: | --- |
| Plus | 14.99 | 1200 | prod_3YUMifrIso3U0JEvAJEl3o |
| Pro | 29.99 | 3000 | prod_1E2KmHCJDzxC3nMraYc1iB |
| Max | 59.99 | 6500 | prod_QM6adegRzqFXCFL6lyO6a |

Free stays internal to VELIA. Existing one-time credit package prices remain
published, but their card purchase controls stay disabled until the owner
supplies approved live product IDs. Google Play catalog flags cannot enable
Creem. Do not add trials, usage charges, discounts, affiliates or extra checkout
fields. Keep self-service plan changes disabled in Creem: upgrades, downgrades
and prorated invoices require an explicit migration policy before support.
Cancel renewal and view billing through the owner-bound customer portal.

## Request and fulfillment boundaries

The browser uses the existing encrypted HttpOnly web session and origin/CSRF
checks. POST `economy/creem/checkout` accepts only `plan` and a UUID `attempt`.
The account authority chooses the authenticated user, product, cents, allowance,
return URL and opaque order reference. Static public product URLs are retained
as configuration references only, never a checkout fallback.

The gateway relays exact signed bytes as bounded base64 to the fixed account
origin. It never fulfills payments or logs customer payloads. The authority
verifies HMAC-SHA256, then independently reads provider checkout, subscription
and transaction data. It verifies production mode, owner/order binding,
customer, product, units, amount, currency, paid status and billing period.

PostgreSQL user row locks and a single transaction update the existing
`users.token_balance`, paid subscription access, charge ledger and delivery
inbox. Delivery IDs, transaction IDs and subscription periods prevent duplicate
grants, including dashboard resends with new event IDs. Monthly renewal grants
one allowance per paid period. Browser redirects cannot grant credits.

Lifecycle events read current provider state and do not resurrect an older
snapshot. Existing non-Creem access is preserved. Refunds reclaim the matching
credit grant proportionally; full refunds and chargebacks remove its access.
Spent credits are recorded as unrecovered rather than producing a negative
balance. Won disputes and unrecovered amounts need operator reconciliation.
A partial refund leaves the paid period active while a net grant remains.

An ambiguous checkout creation is held for reconciliation and is never silently
reissued. An existing open checkout is resumed; switching plans while another
checkout is pending is blocked. Expired or uncertain checkouts currently need
support to reconcile against Creem before releasing the pending order. Do not
clear them just to bypass a duplicate-subscription guard.

The portal always resolves the stored customer of the authenticated account;
the browser cannot submit a customer ID. Management remains available with a
configured API key even when new purchases are disabled.

## Validation and rollout

Run unit, HTTP authorization, signature, refund and duplicate-delivery tests.
The `VELIA Creem Billing` GitHub Actions job also runs real PostgreSQL 16
concurrency and rollback tests with an isolated schema. No production data is
used. Its PostgreSQL checks must pass before live activation. Locally they skip
only when `VELIA_CREEM_TEST_DATABASE_URL` is absent.

After both deployments and live configuration, confirm authenticated catalog
returns `enabled: true`. Then verify the owner-bound checkout opens the correct
product and amount, payment fulfillment updates the same account, repeat
deliveries do not add credits, and billing management opens that customer's
portal. Any real purchase must be performed by the owner. A successful test-mode
checkout is not proof of live product configuration or fulfillment.

The `/thanks` return page is public, English, no-store, noindex and no-referrer.
It ignores query claims and links back to VELIA; it is not a receipt or payment
confirmation. The actual balance is read from the authority after returning.

VELIA / Velyon Core is the customer-facing brand. Support is
`support@velyoncore.ai`. Legal identity remains necessary for provider KYC;
checkout, receipts, invoices, portal and statement disclosure depend on provider
configuration and must be inspected before promising identity privacy.

## Separate test webhook

The existing test-only inbox remains at
`https://velyoncore.ai/web-api/v1/billing/creem/test/webhook`.
Its `VELIA_CREEM_TEST_WEBHOOK_SECRET` stays in the gateway. It records only
receipt metadata/digest in the existing guest database and never grants credits.
Never use that secret or endpoint for live delivery. Live and test environments
are separate.

Official references:
- https://docs.creem.io/code/webhooks
- https://docs.creem.io/api-reference/endpoint/list-webhooks
- https://docs.creem.io/api-reference/endpoint/get-webhook-secret

## Availability and browser updates

The authenticated catalog returns a safe `unavailable_reason` when preflight
fails; it never returns keys, secret values, provider bodies or customer data.
Readiness failures are cached for 30 seconds and logged by reason code only.
The UI distinguishes transport failure from provider readiness and offers a
read-only retry. Disabled purchase controls say that payment is unavailable.
The HTML shell versions both app and CSS URLs to avoid new membership markup
being rendered with an old cached stylesheet.
