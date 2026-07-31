# DeepAlpha canonical public domain

The canonical production origin is:

```text
https://deepalpha-ai.com
```

The Railway-generated `*.up.railway.app` address remains an infrastructure endpoint and must not be published to users, configured in BotFather, or used as the production Developer API base URL.

## Railway production variables

Recommended explicit values:

```env
WEBAPP_URL=https://deepalpha-ai.com
WEB_APP_BASE_URL=https://deepalpha-ai.com
PUBLIC_BASE_URL=https://deepalpha-ai.com
CORS_ALLOWED_ORIGINS=https://deepalpha-ai.com
```

`services.public_domain_service.configure_public_urls()` is installed before bot and WebApp imports. In production it replaces missing or stale Railway public URLs with the canonical origin while preserving explicitly configured non-Railway CORS partners.

## Telegram BotFather

Configure both Telegram entry points for `@DeepAlphaAI_bot`:

1. `/mybots` → `@DeepAlphaAI_bot` → **Bot Settings** → **Configure Mini App** → **Main Mini App**;
2. set the URL to `https://deepalpha-ai.com`;
3. return to **Bot Settings** → **Menu Button** / **Configure Menu Button**;
4. set the same URL: `https://deepalpha-ai.com`;
5. use button text `🚀 Open App` or the current approved product label.

The Main Mini App profile button and the chat menu button are separate BotFather settings and must both be updated.

## Verification

After production deployment:

- open `https://deepalpha-ai.com` in a normal browser;
- open the profile **Open App** button in Telegram;
- open the bot chat menu button;
- trigger a bot message containing a WebApp button;
- verify that Telegram opens `deepalpha-ai.com`, not a Railway hostname;
- check `/api/openapi.json` and confirm its `servers[0].url` is `https://deepalpha-ai.com`;
- download `/api/postman.json` and confirm `base_url` is `https://deepalpha-ai.com`.
