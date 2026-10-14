# VELIA connections — implementation status

The catalogue contains all 28 requested products. Catalogue membership is not implementation, provider approval, or a connected user account. The server returns `planned`, `requires_setup`, `available`, `connected`, or `unavailable`; the UI never offers an active connection button for an unimplemented adapter.

## Implemented adapters

- Google Calendar: existing service and guarded read/create tools; existing environment configuration remains unchanged. The browser catalogue requires an HTTPS `VELIA_GOOGLE_CALENDAR_APP_REDIRECT` before offering new authorization; the default mobile deep link is not advertised as a browser connection.
- Notion: session-bound public OAuth, single-use ten-minute state bound to the authenticated user, encrypted per-user access/refresh tokens, fixed provider host, redirect blocking, search and partial page-text reading. Read tools are registered in Agent Core. Creating/updating pages is not enabled. General chat orchestration remains a separate integration task.

Notion setup requires a provider-registered public connection, approval/distribution configuration and:

- `VELIA_NOTION_ENABLED=true`
- `VELIA_NOTION_CLIENT_ID`
- `VELIA_NOTION_CLIENT_SECRET`
- `VELIA_NOTION_OAUTH_CALLBACK_URL=https://<browser-domain>/web-api/v1/connectors/notion/callback`
- existing `VELIA_CONNECTOR_FERNET_KEY` encryption key

Grant only read-content permissions for this release. The user selects pages at Notion authorization. Do not paste secrets into chat or commit them. Configure credentials through the deployment secret store. No credentials or account authorization were supplied or activated by this change.

OAuth starts via authenticated same-origin POST. The public callback requires the current browser session and completes through its bearer token; the backend consumes state only for that same user. Provider errors never expose codes or tokens in application response bodies. Callback codes necessarily arrive in the OAuth redirect URL; reverse-proxy access logs must redact that route's query before enabling production OAuth. Successful callbacks redirect to `/` with no code in the final URL.

Disconnect removes local credentials and pending authorization states; it does not revoke provider-side consent. The user can also remove VELIA in Notion settings. In-flight reads may already have completed. Search covers titles only; page reading is top-level and marks nested/paginated results as partial. It is not whole-workspace semantic search.

## Remaining 26 adapters

Google Drive/Docs, Sheets, Tasks, Gmail, Classroom; Microsoft Outlook/calendar, OneDrive, To Do, OneNote; Todoist, Dropbox, Trello, Slack, HubSpot, Canva, Zotero, Zoom, YouTube, Figma, Asana, ClickUp, monday.com, Jira, Shopify, WordPress.com, Zendesk.

Each needs its own adapter, permission/capability tests, provider application registration, and user authorization. Some require administrator consent, distribution review, paid account capabilities, or a different authorization protocol. They remain `planned`; no automatic installing, browser-login scraping, blanket permissions, paid API calls, or account access is introduced.

YouTube is categorized under Content and targets creators' channels and playlists. Connecting an account is not a way to retrieve a transcript of every video.

## Release boundaries

No merge or deployment is performed by this change. Fixture tests do not prove live OAuth approval, valid production credentials, remote authorization revocation, or full chat tool routing. Complete these gates before enabling the relevant providers.
