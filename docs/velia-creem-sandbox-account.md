# Account-bound purchase testing

Static `/test/payment/prod_...` links do not carry a VELIA account binding.
The owner can now initiate a separate test checkout from the signed-in Balance
section. Only the authenticated account equal to configured `ADMIN_ID` can use
the sandbox account endpoints; the test key remains on the backend.

## Flow

1. Open Balance and usage → **Тестовая оплата** in the owner's account.
2. The backend creates a test checkout with a durable server order UUID in
   `metadata.velia_sandbox_order_id`, the approved test product and a fixed
   success URL. It independently retrieves the checkout and verifies its test
   mode, exact product and ownership before returning a `/test/checkout/...` URL.
3. Complete that hosted test checkout. On return to VELIA, the balance section
   and its test panel reopen. The owner can also refresh the test purchase.
4. The server reconciles its saved checkouts using canonical checkout,
   subscription, customer and paid invoice reads from `test-api.creem.io`.
   Redirect/query parameters and browser-supplied IDs never grant credits.
5. Verified purchase, test plan and test credits appear in the owner's sandbox
   balance. Real `users`, subscriptions, billing charges and feature access are
   untouched. The panel explicitly says these credits grant no real paid access.

The isolated tables are `velia_creem_sandbox_orders` and
`velia_creem_sandbox_receipts`. PostgreSQL initialization and account changes
are serialized. Provider invoice IDs and one receipt per order make repeated
or concurrent refreshes idempotent; refunds monotonically revoke the original
test allowance. A sandbox product mismatch disables only that test plan.

## Boundaries and limitations

- Existing live checkout routes, URLs, webhook fulfillment and balances are
  unchanged. Owner capability is added per response, outside the live catalog
  cache, so other accounts cannot see it through cached owner data.
- The original test webhook inbox remains an audit sink. Account reconciliation
  on return/refresh is what confirms this test preview. This does **not** certify
  production webhook fulfillment, recurring renewal or paid plan switching.
- A checkout whose create response is lost before its ID can be saved is held
  pending rather than automatically issuing a second provider request. Such a
  case needs operator reconciliation; a new browser attempt cannot bypass it.
- Each approved test plan can be purchased once per test account. Testing a
  different plan as another test purchase is not an implementation of a paid
  upgrade or a proration quote. Existing sandbox subscription experiments are
  separate from this account-bound test flow.
- Both backend and web gateway commits must be manually deployed. No new
  Railway project/service, API key or copied webhook secret is needed.
