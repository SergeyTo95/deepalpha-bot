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

No Railway variables or other infrastructure changes are needed for this release.
