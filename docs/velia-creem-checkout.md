# VELIA token purchase preparation

The browser's Balance section now displays one-time token packages from the
existing authenticated `/mobile-api/v1/economy/catalog` snapshot. It does not
publish admin draft prices or introduce another credits ledger. Balance, usage
and catalog failures are independent. Both economy reads use the existing opaque
web session; no billing write endpoint is exposed.

Card payments are deliberately unavailable in this release. Selecting a package
only updates its displayed total. The catalog's `billing_enabled` flag refers to
Google Play and must never enable Creem. There are no provider keys, checkout
links, receipts, external scripts or payment callbacks in the client.

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
