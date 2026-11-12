"""Test-only Creem delivery inbox. Never grants balance or subscription access."""
import asyncio
from contextlib import closing
import hashlib
import hmac
import json
import logging
import os
import re

from aiohttp import web
from desktop.guest_store import GuestStore

PATH = '/web-api/v1/billing/creem/test/webhook'
MAX_BODY = 256 * 1024
EVENTS = frozenset({
    'checkout.completed', 'subscription.active', 'subscription.trialing',
    'subscription.canceled', 'subscription.scheduled_cancel', 'subscription.paid',
    'subscription.expired', 'subscription.unpaid', 'subscription.update',
    'subscription.past_due', 'subscription.paused', 'refund.created', 'dispute.created',
})
ID = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
logger = logging.getLogger(__name__)


class EventConflict(Exception):
    """A provider event identifier was reused with a different body."""


class TestEventStore(GuestStore):
    __test__ = False

    def initialize(self):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            # Deliberately separate from the account authority's money/credits tables.
            # Do not retain webhook bodies, customer objects, names or email addresses.
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_test_event (
                event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            conn.commit()

    def remember(self, event_id, event_type, body_sha256):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            try:
                self.execute(cur, '''INSERT INTO velia_creem_test_event
                    (event_id,event_type,body_sha256) VALUES (?,?,?)
                    ON CONFLICT(event_id) DO NOTHING''', (event_id, event_type, body_sha256))
                inserted = cur.rowcount == 1
                self.execute(cur, 'SELECT body_sha256 FROM velia_creem_test_event WHERE event_id=?', (event_id,))
                if cur.fetchone()[0] != body_sha256:
                    raise EventConflict()
                conn.commit()
                return inserted
            except BaseException:
                conn.rollback()
                raise


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('invalid number')


def _event(raw):
    event = json.loads(raw, object_pairs_hook=_unique_object,
                       parse_constant=_invalid_constant)
    if not isinstance(event, dict):
        raise ValueError('event required')
    event_id, kind, obj = event.get('id'), event.get('eventType'), event.get('object')
    if (not isinstance(event_id, str) or not event_id.startswith('evt_') or not ID.fullmatch(event_id)
            or not isinstance(kind, str) or len(kind) > 80 or not isinstance(obj, dict)):
        raise ValueError('invalid event')
    # Some subscription events have no mode field. The dedicated test signing
    # secret is the trust boundary; reject any explicit indication of live mode.
    for value in (event, obj):
        if 'mode' in value and value['mode'] != 'test':
            raise ValueError('test events only')
    return event_id, kind


def setup_creem_test_webhook(app, *, store=None, secret=None):
    if secret is None:
        secret = os.getenv('VELIA_CREEM_TEST_WEBHOOK_SECRET', '')
    if store is None:
        dsn = os.getenv('VELIA_WEB_GUEST_DATABASE_URL', '')
        store = TestEventStore(dsn) if dsn else None
    ready = False
    initialize_lock = asyncio.Lock()
    database_slots = asyncio.Semaphore(4)

    async def initialize():
        nonlocal ready
        async with initialize_lock:
            if not ready:
                await asyncio.to_thread(store.initialize)
                ready = True

    def response(data, status=200):
        return web.json_response(data, status=status, headers={'Cache-Control': 'no-store'})

    async def webhook(request):
        if not secret or store is None:
            return response({'ok': False, 'error': 'creem_test_not_configured'}, 503)
        signature = request.headers.get('creem-signature', '')
        if not re.fullmatch(r'[a-fA-F0-9]{64}', signature):
            return response({'ok': False, 'error': 'invalid_signature'}, 401)
        if request.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            return response({'ok': False, 'error': 'unsupported_encoding'}, 415)
        try:
            raw = await asyncio.wait_for(request.clone(client_max_size=MAX_BODY).read(), 10)
        except web.HTTPRequestEntityTooLarge:
            return response({'ok': False, 'error': 'body_too_large'}, 413)
        except asyncio.TimeoutError:
            return response({'ok': False, 'error': 'request_timeout'}, 408)
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            return response({'ok': False, 'error': 'invalid_signature'}, 401)
        try:
            event_id, kind = _event(raw)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            return response({'ok': False, 'error': 'invalid_test_event'}, 400)
        if kind not in EVENTS:
            return response({'ok': True, 'mode': 'test', 'ignored': True, 'credits_granted': False})
        try:
            async with database_slots:
                await initialize()
                inserted = await asyncio.to_thread(store.remember, event_id, kind, hashlib.sha256(raw).hexdigest())
        except EventConflict:
            return response({'ok': False, 'error': 'event_conflict'}, 409)
        except Exception:
            # Do not log exception text: a DB driver can include credentials or PII.
            logger.warning('Creem test delivery storage unavailable')
            return response({'ok': False, 'error': 'delivery_storage_unavailable'}, 503)
        return response({'ok': True, 'mode': 'test', 'received': True,
                         'duplicate': not inserted, 'credits_granted': False})

    # This is server-to-server authentication, not a browser cookie/CSRF route.
    # No public endpoint exposes the delivery inbox.
    app.router.add_post(PATH, webhook)
