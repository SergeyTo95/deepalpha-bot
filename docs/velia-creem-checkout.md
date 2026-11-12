# VELIA token purchase preparation

The browser's Balance section now displays one-time token packages from the
existing authenticated `/mobile-api/v1/economy/catalog` snapshot. It does not
publish admin draft prices or introduce another credits ledger. Balance, usage
and catalog failures are independent. Both economy reads use the existing opaque
web session; no billing write endpoint is exposed.

Card payments are deliberately unavailable in this release. Selecting a package
only updates its displayed total. The catalog's `billing_enabled` flag refers to
Google Play and must never enable Creem. There are no provider keys, receipts,
external scripts or payment callbacks in the client. Owner-supplied public
product references are registered below but are not actionable checkout links.

## Subscription product references

The Balance section now lists the three monthly subscription products supplied
by the owner. They remain unavailable for payment until account-bound checkout
and authoritative credit fulfillment are implemented. The registration uses
the agreed prices/allowances; provider-side fields have not been independently
verified by this change. The public links do not bind a VELIA account.

| Plan | USD/month | Credits/paid month | Live product ID |
| --- | ---: | ---: | --- |
| Plus | 14.99 | 1200 | prod_3YUMifrIso3U0JEvAJEl3o |
| Pro | 29.99 | 3000 | prod_1E2KmHCJDzxC3nMraYc1iB |
| Max | 59.99 | 6500 | prod_QM6adegRzqFXCFL6lyO6a |

`desktop/web/subscription-plans.mjs` retains the exact public product URLs and
renders display-only cards. No href, URL data attribute or purchase event handler
is attached to them. Google Play flags, catalog URLs or injected plan values do
not enable these controls. Remote top-up catalog failure does not hide the
registered subscription cards. Max is a web subscription proposal here, not a
new Google Play entitlement or a change to the authority's account plan state.
Do not advertise feature unlocks until plan access checks are implemented.

## Follow-up for live acceptance

Configure approved one-time Creem products against the existing token catalog on
the account authority, which owns credits. Add server-created owner-bound orders
and signed webhook verification with atomic, idempotent credit fulfillment in
that authority before enabling any purchase button. Redirects and browser success
callbacks must not grant credits. Test duplicate events, amount/product mismatches,
account isolation and interrupted checkout before accepting money.

Customer-facing branding is Velyon Core / VELIA. Keep legal identity in provider
KYC; verify the configured checkout, invoice, receipt, customer portal and bank
descriptor before live activation. This UI release makes no guarantee about
which legal details the provider's account-specific documents disclose.

Official integration reference:
https://docs.creem.io/features/checkout/checkout-api

## Test webhook delivery setup (no purchases or entitlements)

The gateway now registers a **test-only delivery inbox** at:
`POST https://velyoncore.ai/web-api/v1/billing/creem/test/webhook`.
Deploy this revision before testing it. This is not a live billing integration:
HTTP 200 confirms durable test-event receipt, not payment fulfillment. It never
writes to the account authority's balance or subscription tables and introduces
no parallel credits ledger. The card purchase button remains disabled.

In the Creem **Test Mode** webhook form:

- Name: `VELIA Test`
- Endpoint: the URL above
- Select `checkout.completed`, all `subscription.*`, `refund.created`,
  and `dispute.created`. Leave `credits.*` and `customer_credits.exhausted` off:
  these are Creem's own credits features, not VELIA credits.
- Create the webhook, then place its signing secret in Railway service
  **velia-desktop-gateway**, variable `VELIA_CREEM_TEST_WEBHOOK_SECRET`.
  Enter it directly in Railway, never in chat, the repo, or browser JavaScript.
  Apply the pending changes manually in the existing environment.

The existing `VELIA_WEB_GUEST_DATABASE_URL` supplies the persistent PostgreSQL
inbox; no new service or database is required. Before the secret and database
are configured, delivery returns 503 and fails closed. Creem can retry or the
operator can resend after configuration. Register the endpoint first and set
the secret before triggering the first delivery.

The receiver verifies `creem-signature` using HMAC-SHA256 over the exact request
body, with constant-time comparison, a 256 KiB body limit and bounded DB
concurrency. It deduplicates by event ID durably, including across restarts;
reusing an ID with a different body is a conflict. Explicit live-mode payloads
are rejected, and unknown event types are acknowledged as ignored. Since some
subscription payloads omit `mode`, the dedicated **test webhook secret** is the
trust boundary: never configure a live webhook's secret here.

Only event ID, event type, body digest and receipt time are stored. Customer
names, email addresses, raw bodies, signatures and keys are not retained or
logged. There is no public inbox reader. A signed event from the public test
payment link can validate transport but cannot identify a VELIA account or
grant it tokens. Owner-bound checkout and authoritative fulfillment are still
required before end-to-end purchase testing or accepting real money.

After deployment and configuration, send a test event from Creem and confirm
the delivery gets HTTP 200, `mode: test`, `received: true`,
`credits_granted: false`. An automatic retry with the same ID produces
`duplicate: true`. A manual dashboard resend gets a new event ID and creation
time according to Creem, so it is a separate inbox receipt. Future financial
fulfillment must also deduplicate the underlying transaction/paid period,
not only the delivery ID.
If Cloudflare challenges requests, inspect the route-specific WAF configuration;
do not disable general site protection or trust source IP in place of signatures.

Official webhook reference: https://docs.creem.io/code/webhooks

## Shared English purchase-return page

After manually deploying this revision, all subscription and top-up products can
use `https://velyoncore.ai/thanks` as their optional Return URL. The page links
back to VELIA and to support. It is public, English, responsive, noindex and
no-store, with no scripts, trackers, forms or external assets. Its no-referrer
policy prevents checkout query parameters being forwarded in link referrers.

The page does not read or display checkout query parameters, announce verified
payment success, poll a nonexistent billing-status API, or mutate account state.
The same content is returned for direct visits and forged success/amount/plan
parameters. This return page is not a payment receipt and does not enable live
purchases: authoritative webhook fulfillment is still required.

Product setup for this release: keep usage-based charges, trial periods, extra
checkout fields, abandoned-cart emails and automatic affiliate enrollment off.
Use the existing plan and package prices; taxes are excluded as in the pricing
proposal. SaaS is the software category if the dashboard offers it.
