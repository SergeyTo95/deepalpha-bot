# Browser server voice integration

The encrypted, authenticated Web session exposes `/web-api/v1/voice/voices` and `/web-api/v1/voice/speech`. No TTS provider call is made unless both settings are present:

- `VELIA_WEB_TTS_PROVIDER=speechkit`
- `VELIA_SPEECHKIT_API_KEY=<service-account API key with synthesis access>`

The key remains server-side. Use a service-account key; the adapter deliberately does not send `folderId`. Three documented v1 female Russian voices are offered: `jane`, `omazh`, `marina`. The catalog contains no aliases pretending to be different speakers. Other languages continue using the device's voices. The chosen voice persists across reloads. The browser synthesizes bounded phrases through the same-origin authenticated endpoint and plays MP3. Closing the conversation aborts downloads, pauses audio and releases object URLs. Provider failure stops speech and preserves the chat reply rather than silently changing the selected speaker.

The server uses a fixed HTTPS endpoint with redirects disabled, a 15-second deadline, bounded audio size, four concurrent requests and 40 requests per user per minute. Audio and text are not stored by this adapter. The UI tells users that online synthesis sends reply text to the speech service. Guest sessions cannot access these endpoints.

Official documentation checked 2026-10-07:
- https://yandex.cloud/ru/docs/speechkit/tts/request
- https://yandex.cloud/ru/docs/speechkit/tts/voices

Activation and a live provider/mobile microphone/audio acceptance check are still required. Synthetic tests do not establish voice quality, distinct timbres or real device latency. STT remains the browser's recognition service in this change.
