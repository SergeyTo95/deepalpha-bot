# Notes and geographic map

The existing gateway and backend serve this feature. No new service or GPU is
required. Manual notebook/map actions need no model call. Map tasks in chat use
the selected model and its normal token accounting. Deploy the backend notebook
endpoint before the gateway UI.

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

Explicit notebook commands work before model availability checks: “Сохрани
заметку: …”, “Создай список покупок: хлеб, молоко”, “Найди мои заметки про …”,
“Добавь это в избранное”, “Открой карту”. Geographic tasks such as “Найди на карте
Анталию” run the map tools through the selected model. Checklists are stored lists,
not scheduled reminders.

## Map actions in the main chat

Geographic action requests and concrete follow-ups use `map/plan`, authenticated
through the normal model authorization path for Flash, Quantum and PRO. The
bounded client tool loop can search addresses, read saved places, show selected
points in an inline map, save places, edit their comments and prepare navigation
links. Search results get opaque references; the model cannot invent a coordinate
or a saved-record ID to execute a write. Writes require an explicit request and
are guarded against account changes, stale revisions and repeated mutation
attempts. Other notebook entries and encryption keys are never passed to the
planner. Only relevant place results enter the conversation context.

At most four searches and ten planner steps run per turn. Routes support two to
five points in the requested order and a travel mode. They are navigation links,
not computed road paths, traffic estimates or route optimization. The UI labels
this distinction and generates the navigation URL from validated coordinates.

Map results and continuations use the encrypted browser chat cache, as other
client-side integration tasks do. When starting a map task from a server chat,
the visible context is retained in a browser continuation; map tool turns are
not yet synchronized as server conversation messages across devices. Saved
places themselves remain in the account notebook and are available on other
devices. The “Мои места” sidebar opens saved locations instead of displaying an
empty world map by default.

## Daily visit plans

The encrypted account notebook now stores `visit` records: a place snapshot with
coordinates and address, task, calendar date, optional local time, IANA timezone,
status (`planned`, `done`, `cancelled`) and result. Place addresses and comments
are separate fields; legacy places without an address use their existing text.
Deleting a saved place does not erase the location of past visits.

“Мои места” has “Места” and “Выезды” tabs. Add/edit visits, filter by day/status,
show their locations or discuss them in chat. The chat tools `schedule_visit`,
`list_visits`, `update_visit` require real references and explicit write intent.
They support “Запланируй визит к этому месту на завтра в 10:00”, “Собери мои
выезды на завтра” and “Отметь первый визит выполненным; результат: заказ передан”.
Identical place/date/time/timezone/task appointments do not create a duplicate.
The existing notebook revision checks protect changes from concurrent tabs.

Relative dates use the browser/device timezone, rather than server UTC. Each
visit retains its own timezone. Untimed visits appear after timed appointments.
Equal planned slots in the same timezone are flagged; travel time and conflicts
across different timezones are not calculated. The chat shows up to 20 visits
and 12 distinct map points with full counts; the “Выезды” tab shows the full day.
No road optimization or automatic reminders are added. Simple Russian/English
visit confirmations are generated from successful tool results without another
model call; composite requests and other languages use the normal planner loop.
Simple Russian/English day reads and explicit completion/cancellation of a
uniquely referenced visit also bypass planning requests. Ambiguous place or visit
references continue through the selected model instead of choosing a sole item.

Visits sync through the notebook; chat plan cards remain in the encrypted
browser cache. Open tabs should refresh after release to read the new entry
kind. No backend ciphertext schema or encryption key migration is required.

## Map providers and privacy

Leaflet 1.9.4 is vendored locally with its license. Map tiles use
`https://tile.openstreetmap.org` with visible attribution, a browser referrer and
normal browser caching. No offline tile download or bulk prefetch is implemented.
See https://operations.osmfoundation.org/policies/tiles/.

Search accepts optional ISO country and place-category constraints. The gateway
passes them to Photon and independently checks the returned country and OSM
object type. This excludes, for example, car rentals and restaurants when an
airport is requested. The planner uses English or local names and retries
unsuitable or empty searches without changing the requested object type.
Search candidates are not displayed automatically: the planner must explicitly
select relevant points with `show_places`.

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

## Place history and original materials

A saved place has a single card with its comment, linked notes/agreements,
visits/results, and original photo/document files. Notes link by `placeId`;
visits with an explicit place ID remain separate even if two places share
coordinates. Legacy visits without an ID match the exact name and coordinates.
Existing unlinked notes can be linked explicitly. Deleting a place preserves
its records in Notes; delete individual materials there to reclaim their space.

Main-chat tools `get_place_history`, `add_place_note`, `attach_place_materials`
use actual place references. Only explicitly selected-place history goes to the
selected model; file contents and unrelated notes do not. Public history is
limited to 12 clipped items and total counts. The full card has the complete
account data. This is saved work history, not an archive of every prior chat.
Attachment commands work with Flash, Quantum and PRO independently of the model's
file-analysis capability; analyzing a file remains a separate, authorized flow.

The browser encrypts each original with AES-GCM using its existing account key,
a fresh 96-bit nonce, and AAD bound to the user and material UUID. Filenames,
MIME type, size and captions are stored inside the encrypted notebook snapshot.
The backend stores only opaque immutable ciphertext in a separate table, keyed
by authenticated user and UUID. Reads and deletion require the owner's session;
mutations also check the web account fingerprint. There are no public file URLs.
The existing encryption-at-rest/key-backup caveat above still applies.

Supported originals: JPEG, PNG, WebP, PDF, DOCX, TXT/MD/CSV/JSON; at most 5 MiB per
file, 100 files and 50 MiB of ciphertext per account, up to four files per action.
The per-user PostgreSQL owner row is locked while checking quota and mutating.
The gateway reads bounded authenticated JSON and permits larger upstream
responses only for the exact material endpoint. Authentication body limits stay
unchanged. Previews use temporary object URLs cleared on dialog close/logout.

Upload metadata is saved as `pending` before transferring ciphertext and becomes
`ready` only after both storage and final notebook write succeed. Interrupted
uploads remain visible. Opening a pending material recovers it if the original
arrived; otherwise delete the pending record and attach again. Deletion removes
the ciphertext before deleting notebook metadata; retry is idempotent if a
revision conflict/network failure interrupted the second step. No unreferenced
file is intentionally uploaded before a persistent notebook record exists.

Release the existing backend with `services/velia_notebook_materials.py` before
the gateway. Its two tables initialize lazily through the same database helper
as the notebook. The stable encryption secret is unchanged; no new service,
bucket, model or GPU is needed. Refresh open browser tabs after the gateway
release. Calendar reminders and road optimization are outside this release.

Simple Russian/English commands to open history for one already selected saved
place read its actual notebook directly, without a model planning call. Ambiguous
selection, composite requests and history analysis use the selected model.
