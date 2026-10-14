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

## Vendor adapters (remaining 16 implemented)

All 28 catalogue cards now have an adapter. This is code readiness, not live provider approval or user authorization. Vendor operations are READ in Agent Core. Dropbox list-folder, monday and Shopify use fixed read-only POST requests; arbitrary GraphQL, URLs, writes and pagination URLs are never accepted from a model.

Configuration (disabled by default):

- `VELIA_VENDOR_CONNECTIONS_ENABLED=true`
- shared `VELIA_CONNECTIONS_WEB_ORIGIN` and `VELIA_CONNECTOR_FERNET_KEY`
- per provider `VELIA_<ID_IN_UPPERCASE>_OAUTH_CLIENT_ID` and `VELIA_<ID_IN_UPPERCASE>_OAUTH_CLIENT_SECRET`, e.g. `VELIA_DROPBOX_OAUTH_CLIENT_ID`.
- Register `<origin>/web-api/v1/connectors/<id>/callback` for each provider.

| ID | Implemented reads | Requested permission / app configuration |
| --- | --- | --- |
| todoist | Projects, tasks | data:read; modern OAuth with refresh; legacy 10-year compatibility expiry accepted |
| dropbox | Root file metadata | files.metadata.read, offline access; select app folder/full Dropbox in provider app setup |
| trello | Open board metadata | read:member:trello, read:board:trello, offline_access; confidential OAuth2 + PKCE |
| slack | Public channels, 15 recent messages | channels:read, channels:history bot scopes; install bot and grant channel access; respect commercial API rate limits |
| hubspot | Contact names and emails | crm.objects.contacts.read |
| canva | Design metadata search | design:meta:read; Basic token exchange and mandatory PKCE |
| zotero | Personal reference metadata | OAuth1 HMAC-SHA1; suggest library_access=1, notes_access=0, write_access=0, all_groups=none; user can change suggested grants |
| zoom | Scheduled meetings | Configure General App with meeting:read:list_meetings; no recordings or transcripts |
| figma | Selected file metadata and comments | file_metadata:read, file_comments:read; publish/review app as appropriate |
| asana | Workspaces, projects, tasks | workspaces:read, projects:read, tasks:read; PKCE |
| clickup | Authorized workspaces, list tasks | OAuth has broader account capabilities; executor exposes reads only |
| monday | Board names and states | boards:read; enable New OAuth Flow, mandatory PKCE, JWT expiry and rotated refresh tokens |
| jira | Accessible sites, projects, issue search | read:jira-work, offline_access; cloud_id must match authorized site list on every read |
| shopify | Product titles and states | read_products; standalone app; validated merchant slug, callback HMAC and owner-bound state; no orders/customer access |
| wordpress | Selected site and bounded posts | sites, posts; WordPress.com selected-site authorization; provider grants may include writing; executor exposes reads only |
| zendesk | Accessible tickets | tickets:read, PKCE, one-day access and seven-day refresh requested; register a distributed/global client for multi-tenant use |

Shopify and Zendesk ask for the tenant name before redirecting, never an arbitrary URL. API hosts are derived from that validated name encrypted with the owner's credentials. Jira exposes `sites` so the agent can ask the user which site to use, then checks the chosen cloud ID against accessible resources. Zotero stores the OAuth request secret in encrypted state, validates the returned request token, then stores the API key secret and provider user ID. Callback queries must be redacted in proxy logs, including OAuth1 verifier/token and Shopify parameters.

Vendor state lasts ten minutes and is single use. Tokens are encrypted with owner and connector bindings. Expiring credentials refresh under a database row lock, preserving tenant context and saving rotated refresh tokens. Non-expiring credentials store a NULL expiry rather than an invented lifetime. Expired tokens without refresh require reconnection. Disconnect deletes local credentials and states; users remove remote consent in the provider settings. A stored credential does not prove a remote grant is still valid.

All results disclose bounded/possibly partial output, reject HTTP200 API errors, cap response bytes at 2 MB and returned characters at 60000. Metadata-only adapters do not imply downloading or indexing all files. OAuth scopes and executor capabilities are different: ClickUp, WordPress and user-edited Zotero grants may be broader than this release's read operations; the UI explicitly says so.

Live acceptance for every provider: approved/distributed app where required, secret-store credentials, registered callback, two separate users, consent/cancellation/replay, first read, refresh/reconnect, disconnect. Fixture tests cannot replace these checks. Enable only tested providers by supplying only their credentials. No provider apps, credentials, paid capabilities or user accounts were activated here.

YouTube is for creators' channels and playlists. It does not provide arbitrary video transcripts.

## Release boundaries

No merge or deployment is performed by this change. Fixture tests do not prove live OAuth approval, valid production credentials, remote authorization revocation, or full chat tool routing. Complete these gates before enabling the relevant providers.
