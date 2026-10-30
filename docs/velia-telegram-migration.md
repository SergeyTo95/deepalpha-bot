# Dedicated Velyon Core Telegram bot

The existing production web process can run an opt-in VELIA bot without creating
a Railway service. The DeepAlpha bot keeps its original `BOT_TOKEN` and handlers.
The new bot uses the same Telegram user IDs, login-code tables, admin allowlist,
account balances and final patched VELIA chat pipeline. No accounts are recreated.

## Operator configuration

In the existing backend production service, set:

```
VELIA_TELEGRAM_ENABLED=true
VELIA_TELEGRAM_BOT_USERNAME=VelyonCoreAI
VELIA_TELEGRAM_BOT_TOKEN=<the new bot's BotFather token>
VELIA_TELEGRAM_WEB_URL=https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app
```

Keep the token in Railway Variables. Never substitute the DeepAlpha `BOT_TOKEN`.
The provided Telegram address must belong to an actual bot. At startup `getMe`
must identify a bot with the expected username and token ID. An existing webhook
is rejected rather than deleted. Invalid/missing credentials keep legacy login
links operational. Only after identity validation and acquiring the polling lock
do mobile/browser and admin code links move to the dedicated bot.

The browser gateway already redirects to the backend `/mobile-connect` page;
it needs no new bot secret or separate deployment for this migration. Existing
WebApp `initData` signature validation still uses the legacy token. The new bot
opens ordinary VELIA Web and issues its existing one-time pairing codes; this is
not a migration of the DeepAlpha Mini App or its payments.

## Supported interactions

- `/start`, `/help`: welcome and links to VELIA and login codes.
- `/login` and `/start velia_connect`: existing five-minute one-time login code.
- `/start velia_admin_login`: existing owner-only admin login flow.
- Text messages: shared VELIA history with the same per-user admission/budgets.
- `/flash`, `/quantum`, `/pro`: persist the selected mode; Flash is the default.
  PRO keeps its existing balance gate; there is no extra automatic paid route.
- `/new`: create a new Telegram conversation; previous history stays in VELIA.

Only fresh private user messages are accepted. The sender ID must match the
private chat ID; bots, channels, groups, edited messages and messages older than
five minutes are ignored. Banned accounts are denied. Telegram voice notes and
files are not transcribed or uploaded by this adapter; it links to the existing
web voice/file interface instead. It does not send task-completion broadcasts.

## Restart and rollout behavior

Two model replies can run concurrently; each user has at most one active reply.
Code issuance is processed independently of these model slots. A bounded busy
response asks the user to retry; it is not silently queued. Each text generation
uses `tg:<bot_id>:<update_id>` as its shared chat idempotency key. A PostgreSQL
session advisory lock scopes polling to the dedicated bot during replica overlap.
An additive per-bot offset table persists consumed updates. The offset is committed
before dispatch, so a crash does not automatically repeat code issuance, model
tasks, tool actions or Telegram sends. This favors at-most-once processing: an
interrupted reply may not arrive in Telegram; any saved result remains in VELIA.
Manual retry is a new request. There is no automatic replay of uncertain work.

The polling worker runs only in production/prod when explicitly enabled, in the
same event loop as the web service. Credentials, raw update content and transport
exception URLs are not logged. API calls use a fixed Telegram origin and reject
redirects. Disabling the flag restores legacy code links on the next restart.

## Verification

`tests/test_velia_telegram_runtime.py`, `tests/test_velia_telegram_pairing.py` and
`tests/test_velia_control_center.py` cover identity checks, source isolation,
login/admission boundaries, literal replies, shared modes and PostgreSQL restart
state. The dedicated workflow is part of mandatory backend Railway CI; the
PostgreSQL test uses its own isolated schema, never the production database.
Live acceptance still requires the operator's new bot token: observe
`VELIA_TELEGRAM_READY`, obtain a code in the bot, exchange it in VELIA, send a text
and check the same account's chat history. Never print login codes or tokens to logs.
