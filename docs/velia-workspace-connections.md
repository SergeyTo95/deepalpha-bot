# VELIA workspace connections

Users connect from the compact Connections list. Each service has one Connect/Disconnect button. Checks run before chat operations. Shopify additionally asks for the store's `*.myshopify.com` address; it never asks the merchant for an API key.

## Release order

Deploy the backend OAuth relay first, then the web gateway. Use the existing services and environments. No new compute services or GPU are required.

## Google operator setup

The relay reuses the existing `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`. Its Google callback remains the registered backend `/api/auth/google/callback`. Workspace-prefixed state returns to the gateway callback; ordinary Google login keeps the original flow. Dedicated overrides are `VELIA_GOOGLE_OAUTH_CLIENT_ID`, `VELIA_GOOGLE_OAUTH_CLIENT_SECRET`, `VELIA_WORKSPACE_GOOGLE_CALLBACK`.

The trusted return origin uses `VELIA_WORKSPACE_WEB_ORIGIN`, falling back to the origin from `VELIA_DEVELOPER_WEB_CALLBACK_REDIRECT`.

In the same Google Cloud project:

1. Enable Google Drive API, Google Docs API, Google Sheets API and Gmail API.
2. Configure the OAuth consent screen with the scopes listed in `services/velia_workspace_oauth_routes.py`. Include the owner as a test user during development.
3. Complete Google's applicable sensitive/restricted-scope verification before distributing Gmail and broad Drive access publicly. Existing Google login credentials alone do not prove these permissions are approved or APIs are enabled.
4. Test a real account: search a file, read a document, create a sheet, write and read cells, find a message and create a draft. Gmail sends are not implemented.

Google uses stable REST APIs; this release does not depend on Workspace MCP's developer preview or its agent-client registration requirements.

## Notion

The official remote MCP endpoint uses dynamic OAuth client registration. No operator secret or user API key is required. Discovery and authorization-link creation were tested; actual user consent and workspace operations require a connected account.

## Shopify operator setup

Register a standalone/API app in Shopify's Dev Dashboard. Configure its callback:

`https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app/web-api/v1/connections/shopify/callback`

Store `VELIA_SHOPIFY_CLIENT_ID` and `VELIA_SHOPIFY_CLIENT_SECRET` in the existing backend service. Use public distribution/approval appropriate to the intended merchant population. Request read_products, write_products, read_orders and read_inventory. Order access may require Shopify approval even though the selected fields omit customer details.

Until app credentials are configured, Shopify shows an unavailable connection state instead of a working-looking authorization button. Test with a development store before public rollout. Tools list products/inventory totals and recent orders, and update product titles/descriptions on request. There are no billing, refunds or payment tools.

## Credentials and execution

OAuth state is bound to a VELIA account session, expires in ten minutes and is single-use. The backend also signs provider state with the authenticated user and store, preventing cross-account token exchanges. Google uses PKCE. Shopify additionally verifies callback HMAC, store and timestamp on the backend. Tokens stay in gateway memory, are isolated by account/session, and are cleared on disconnect/logout/restart or after an hour idle; reconnect is required after token expiry. Refresh tokens are not retained in this release. Operations are serialized and keep replay receipts; interrupted mutations are not retried automatically.

## File outputs

Assistant answers offer Markdown/TXT, DOCX, PDF, XLSX and PPTX downloads. Binary formats are generated in memory on the authenticated gateway, with no server-side artifact storage. PDF embeds a Unicode font. Spreadsheet export preserves allowed local formulas but does not calculate/revalidate business totals. Unsupported/external formulas become text. Content is limited to 50,000 characters per export. The chat displays source content; it is not a pixel-exact Office document preview.

Service icons are embedded local SVG paths from Simple Icons (CC0): https://github.com/simple-icons/simple-icons. No third-party image fetches are made by users’ browsers.
