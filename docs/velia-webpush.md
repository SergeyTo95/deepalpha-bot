# VELIA Web Push

Web notifications are delivered by the existing backend Pulse worker, not by the
page timer. No new Railway service or external notification account is needed.

## Release order

1. Deploy the backend release (subscription routes, encrypted outbox, sender).
2. Deploy the web gateway release (root-scoped worker, manifest, device toggle).
3. Open Pulse and enable notifications on each browser that should receive them.
   On iPhone/iPad use Safari → Share → Add to Home Screen, launch that icon, then
   enable notifications inside Pulse. Android and desktop can use the website.
4. Create a reminder a few minutes ahead, close its tab, and check the native
   system notification. Clicking opens Pulse; reading is separate from completing.
5. Disable notifications and verify a later reminder stays in Pulse but does not
   notify this browser. Repeat after logout/account switching.

## Operations and privacy

- VAPID is derived with domain separation from the existing stable notebook/master
  encryption secret. The private key never leaves the backend. Rotation of that
  secret rotates VAPID; the next web visit detects the changed public key and
  requires enabling this browser again.
- Subscriptions are encrypted with the existing per-account key. A push endpoint
  cannot be transferred between accounts. Up to 20 active subscriptions per user.
- Logout clears the local worker binding, closes existing notifications and
  unsubscribes before clearing auth cookies. Delayed packets with another account,
  device or binding are ignored. No private responses are cached by the worker.
- Notification content is hidden by default. A private preview requires both the
  current preference and a successful check of the live account cookie. A failed
  network check uses generic text; a changed/unauthorized account clears the binding.
- Silent hours are rechecked before sending. No old inbox notifications are replayed
  when opting in. 404/410 deletes expired subscriptions; 429/5xx retries up to three
  attempts. Ambiguous network results are not automatically resent. The durable
  in-app inbox remains available. Provider acceptance is separate from browser display.
- Push hosts are restricted to standard browser push services, HTTPS/443 only.
  No redirects or environment proxies; requests have a 15-second bound.
- System Focus modes, denied OS/browser permissions, force-stopped browsers, offline
  devices and platform restrictions may delay or suppress notifications. Native
  delivery must be tested with permission on a physical device after deployment.

## Validation

Backend tests cover encryption, VAPID signature and wire decryption, ownership,
opt-out/rebinding, quiet hours, receipt ownership, retries, expiry and HTTP routes.
Web tests execute the worker and permission lifecycle, including late permission
resolution, stale messages, account switching and background subscription rotation.
Gateway HTTP tests verify root scope, MIME, installation icons and trusted origin.
