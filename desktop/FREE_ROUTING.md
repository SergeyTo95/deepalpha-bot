# Free road routing

The map agent now supports explicit user-triggered road calculations through
openrouteservice's hosted Standard API. No routing engine is deployed on Railway,
and no paid provider or public demo-server fallback is used. This branch is stacked
on `velia/intelligent-meetings`; financial, CRM and meeting changes are retained.

## Enable

1. Create/sign in to a free HeiGIT account at https://account.heigit.org/ and
   generate an openrouteservice Standard API key. Review the current plan/terms
   in the dashboard; this feature is intended for the free Standard plan.
2. In the existing `velia-desktop-gateway` service/environment, add the key as
   `VELIA_MAP_ORS_API_KEY`. Keep it in service variables, never in chat or source.
   No new project, service, payment details or paid-plan upgrade is required by
   this implementation. Registration/key generation requires the account owner.
3. Deploy this branch and request, for example, `Построй автомобильный маршрут
   между этими местами` after selecting actual map points. A small live route
   acceptance test is required; mocked responses do not verify API permissions.

If the key is absent, the route result clearly says so and preserves the existing
navigator link with the user's original stop order. Keys never reach browser
responses, model messages or logs. The gateway sends only coordinate arrays and
travel mode/settings, without contact names, addresses, chat text or user IDs.
Do not include personal information in provider payloads; review HeiGIT's terms
for the deployment's use case.

## Supported behavior

Simple named `from → to` requests use a bounded route-specific workflow rather
than the general ten-step tool planner: one selected-model extraction, provider
lookups for each endpoint, and selected-model verification using short candidate
IDs. Each endpoint gets at most one alternate-language lookup; ambiguous matches
get at most one additional selection pass. No unverified first-result choice,
invented coordinates or automatic landmark alias is used. Category filters belong
to each endpoint separately: an airport destination does not filter out a historic
starting point. Generic repeated identical searches also stop before another
provider request, and terminal failure wording does not claim results were saved.

This addresses a live failure where four successful geocoder HTTP responses were
followed by repeated planner calls without ever invoking `/map/route`. HTTP 200
from geocoding alone does not establish a correct landmark selection. Tests cover
the reported Hadrian's Gate → Antalya Airport request and same-name hotel/other
city ambiguity. Live provider acceptance still requires an authenticated account.

- 2–5 distinct points, driving, walking or cycling. Public transport retains the
  navigator handoff; no timetables or transit estimates are fabricated.
- Actual provider road geometry, total distance and estimated duration, visible
  on the existing map. Times exclude live traffic, weather, dwell times and
  meeting durations. No arrival guarantee is implied.
- On explicit optimization requests, directed duration matrices choose among
  permutations of intermediate stops with fixed first/last points. At most five
  points make exhaustive selection small; this is travel-time optimization for
  this static matrix, not meeting scheduling or traffic-aware planning. For two
  or three points the fixed endpoints leave only one possible intermediate order.
- Provider output is validated against the original coordinates, mode, endpoint
  order, numeric ranges, leg count and geometry. Invalid/partial responses never
  supply approximate straight-line values masquerading as road distances.
- A route failure returns the original-order navigator link and a clear reason.
  No extra model call can invent a successful calculation after a routing error.
- `Напомнить о выезде` opens an existing Pulse draft tied to the chat. The user
  chooses date/time and saves it there. No automatic calculation of a departure
  time, calendar read/rescheduling or automatic notification creation is added.
- Results and exports include provider, OSM and CC BY-SA attribution. The full
  road line is retained in encrypted device chat history but excluded from model
  context; only summary/provenance fields enter follow-up prompts.

## Limits and failures

Only `https://api.heigit.org/openrouteservice/v2` is contacted, with authorization
in a header and redirects disabled. This is the current official API hostname;
the retired `api.openrouteservice.org` URL is not used. No user-supplied provider
URL, token, callback or arbitrary HTTP request is accepted.

Limits: 4 KiB API body, five points, 1 MiB provider response, 10,000 geometry points,
one routing task per model turn, one concurrent gateway calculation, at least two
seconds between provider calls, twelve route requests per owner per hour, 800
directions and 100 matrix attempts per UTC day per gateway process. These are
conservative application limits, not a promise of upstream quota availability.
Provider 401/403/429 failures trigger a sixty-second cooldown. There are no retry
loops, extra account/key rotation or paid fallback on exhaustion.

The documented Standard quotas have been 2,000 Directions/day and 40/min, with
500 Matrix/day; check the account's current published plan before enabling.
Application counters and cache are process-local, so restarts/multiple replicas
do not provide a durable shared quota counter; upstream free-plan enforcement
still applies and excess requests fail rather than upgrading a plan.

Results are cached for the same owner/coordinate/mode/order request for up to ten
minutes, bounded to 64 entries, in memory only. Account separation prevents route
cache reuse across owners. Cache hits retain their original calculation timestamp.
The provider account's retention and usage terms apply to requests sent upstream.

## Sources checked 2026-11-11

- Plans: https://account.heigit.org/info/plans
- Standard plan and commercial use confirmation:
  https://ask.openrouteservice.org/t/free-plan-in-an-app-with-a-paid-tier-license-cc-by-sa-4-0-personal-data-and-attribution-questions/8079
- API migration:
  https://ask.openrouteservice.org/t/deprecating-api-openrouteservice-org-in-favour-of-api-heigit-org/7912
- Directions and matrix API docs:
  https://giscience.github.io/openrouteservice/api-reference/

Route output includes the provider's stated CC BY-SA-4.0 license. Keep its
attribution when exporting or redistributing route artifacts.
