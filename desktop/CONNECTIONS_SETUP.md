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

## Google and Microsoft read adapters

Ten additional implemented connection cards: Drive/Docs, Sheets, Google Tasks, Gmail, Classroom, YouTube, Outlook/calendar, OneDrive, Microsoft To Do and OneNote. These add 17 fixed GET read operations, not sending mail, modifying files, publishing videos or scheduling tasks. General chat orchestration is not implemented by this adapter layer.

Shared configuration:

- `VELIA_CLOUD_CONNECTIONS_ENABLED=true` (disabled by default)
- `VELIA_CONNECTIONS_WEB_ORIGIN=https://<browser-domain>` (origin only, no path)
- existing `VELIA_CONNECTOR_FERNET_KEY`
- Google: `VELIA_GOOGLE_OAUTH_CLIENT_ID` / `VELIA_GOOGLE_OAUTH_CLIENT_SECRET`
- Microsoft: `VELIA_MICROSOFT_OAUTH_CLIENT_ID` / `VELIA_MICROSOFT_OAUTH_CLIENT_SECRET`

Register each product callback: `<origin>/web-api/v1/connectors/<id>/callback`, with IDs `google-drive`, `google-sheets`, `google-tasks`, `gmail`, `google-classroom`, `youtube`, `outlook`, `onedrive`, `microsoft-todo`, `onenote`. Each connection gets only its product scopes, its own ten-minute owner/product-bound state and PKCE verifier, and its own encrypted owner/product-bound tokens. Google uses offline access with consent; Microsoft uses offline_access. Refresh is serialized and rotating refresh tokens are persisted before returning the new access token. Disconnect removes local tokens; provider-side grants may remain.

Scopes are read-only: Drive read access (not a selected-file Picker flow), Sheets read access, Tasks read access, Gmail read access, Classroom courses and the student's coursework read access, YouTube read access, Mail.ReadBasic + Calendars.Read, Files.Read, Tasks.Read, Notes.Read. Google restricted/sensitive scope verification and Microsoft account/tenant consent must be completed as applicable. Do not enable providers before live OAuth checks.

Reading limits: Drive file metadata and bounded Docs structure; a Sheets rectangle of at most 5000 cells; Gmail IDs/snippets/selected headers, not full bodies; Outlook subjects/senders/timestamps and at most 31 days of events; OneDrive root metadata, not downloads; OneNote page titles, not content; up to 20 items for list operations. API responses are capped at 2 MB and returned data at 60000 characters; all read results explicitly mark possible partial output. Pagination URLs are not followed. Returned text is external evidence, not agent instructions.

## Remaining 16 adapters

Todoist, Dropbox, Trello, Slack, HubSpot, Canva, Zotero, Zoom, Figma, Asana, ClickUp, monday.com, Jira, Shopify, WordPress.com, Zendesk.

Each needs its own adapter, permission/capability tests, provider application registration, and user authorization. Some require administrator consent, distribution review, paid account capabilities, or a different authorization protocol. They remain `planned`; no automatic installing, browser-login scraping, blanket permissions, paid API calls, or account access is introduced.

YouTube is categorized under Content and targets creators' channels and playlists. Connecting an account is not a way to retrieve a transcript of every video.

## Release boundaries

No merge or deployment is performed by this change. Fixture tests do not prove live OAuth approval, valid production credentials, remote authorization revocation, or full chat tool routing. Complete these gates before enabling the relevant providers.
