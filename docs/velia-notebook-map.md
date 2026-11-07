# Notes and geographic map

The existing gateway and backend serve this feature. No new service, GPU or model
call is required. Deploy the backend notebook endpoint before the gateway UI.

## Account notebook

Notes, checklist items, favorite answers and saved places share one notebook.
An authenticated browser reads its account key and encrypts the complete snapshot
with AES-256-GCM and a random 96-bit nonce. Associated data binds it to the backend
user ID. Only ciphertext and the optimistic revision are stored in
`velia_notebooks`. Account changes wipe the browser key and plaintext; gateway
fingerprint checks and backend ownership checks reject stale-account writes.

This is encryption at rest, not end-to-end encryption against the service
operator. The backend derives independent keys from `MASTER_ENCRYPTION_KEY`, or
the optional `VELIA_NOTEBOOK_SECRET`. Keep the chosen secret stable across
restarts, environments sharing the database and releases. Changing it requires a
key migration, otherwise existing notebooks cannot be decrypted. Back up the
database and the secret separately using the existing operator backup process.

The browser keeps an unlocked notebook in memory. There is no plaintext browser
cache and no offline-write queue. A failed request never reports a successful
save. Revision conflicts require reloading before retrying, preserving another
device's changes. A snapshot holds at most 500 records and 512 KiB of plaintext.
No notebook content is automatically supplied to an LLM. “Обсудить с Велией”
prepares a chat draft for the user to send.

Explicit Russian commands work before model availability checks: “Сохрани
заметку: …”, “Создай список покупок: хлеб, молоко”, “Найди мои заметки про …”,
“Добавь это в избранное”, “Открой карту”, “Найди на карте Анталию”. Other prompts
continue through the normal selected-model flow. Checklists are stored lists,
not scheduled reminders.

## Map providers and privacy

Leaflet 1.9.4 is vendored locally with its license. Map tiles use
`https://tile.openstreetmap.org` with visible attribution, a browser referrer and
normal browser caching. No offline tile download or bulk prefetch is implemented.
See https://operations.osmfoundation.org/policies/tiles/.

Only an explicit submitted address search contacts the Photon demo API. There is
no autocomplete. The gateway serializes upstream lookup at one request per
second, caps it at 1,000 requests/day per process and 30 submissions/user/hour,
and caches up to 500 results for 24 hours. Queries are sent to the map provider;
they are not added to the saved notebook unless the user saves a place. The
provider has no availability guarantee. See https://github.com/komoot/photon.
An operator may set `VELIA_MAP_GEOCODER_URL` to an alternative Photon-compatible
HTTPS endpoint. Before adding gateway replicas or expanding traffic, use a
shared limiter and a suitable provider or self-hosted geocoder. Public Nominatim
is deliberately not used.

Geolocation is requested only after pressing “Моё местоположение”; the browser
asks permission and the coordinate remains in browser memory until explicitly
saved. Navigation opens Google Maps in a new tab with the selected coordinates,
using Maps URLs without an API key. It does not provide in-app traffic or road
optimization. The displayed distance between saved points is explicitly a
straight-line distance. See https://developers.google.com/maps/documentation/urls/guide.

## Verification

Run `node --test desktop/web/tests/*.test.mjs` and the exact checked-stage Python
batches in `desktop/Dockerfile.gateway`. Backend ownership, revision and
ciphertext validation are covered by `tests/test_velia_notebook.py` on the backend
branch. The whole-app synthetic smoke is
`VELIA_JSDOM_MODULE=/path/to/jsdom/lib/api.js node desktop/scripts/smoke-web-notebook.mjs`.
This exercises the app with a stubbed map SDK and geocoder. Run
`desktop/scripts/smoke-web-map.mjs` with the same environment variable for the
real vendored Leaflet SDK in JSDOM. Neither replaces live mobile browser checks
of tiles, geolocation permission and navigation.
