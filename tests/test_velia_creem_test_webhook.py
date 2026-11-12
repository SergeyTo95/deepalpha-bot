import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import hmac
import json
import sqlite3

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from desktop.creem_test_webhook import (
    EVENTS, MAX_BODY, PATH, EventConflict, TestEventStore, setup_creem_test_webhook,
)

SECRET = 'test-signing-secret-used-only-in-tests'


def event(kind='checkout.completed', identifier='evt_delivery1'):
    return {'id': identifier, 'eventType': kind, 'created_at': 1739963911073,
            'object': {'id': 'ch_test1', 'mode': 'test', 'status': 'completed',
                       'customer': {'email': 'private@example.com', 'name': 'Private customer'}}}


def encoded(data):
    return json.dumps(data).encode()


def signature(raw, secret=SECRET):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_durable_inbox_deduplicates_concurrent_redeliveries_without_storing_pii(tmp_path):
    path = tmp_path / 'inbox.sqlite'
    store = TestEventStore(sqlite_path=str(path))
    store.initialize()
    raw = encoded(event())
    digest = hashlib.sha256(raw).hexdigest()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.remember('evt_delivery1', 'checkout.completed', digest), range(16)))
    assert sum(results) == 1
    restarted = TestEventStore(sqlite_path=str(path))
    restarted.initialize()
    assert restarted.remember('evt_delivery1', 'checkout.completed', digest) is False
    with pytest.raises(EventConflict):
        restarted.remember('evt_delivery1', 'subscription.paid', 'a' * 64)
    with sqlite3.connect(path) as conn:
        rows = conn.execute('SELECT event_id,event_type,body_sha256 FROM velia_creem_test_event').fetchall()
        assert rows == [('evt_delivery1', 'checkout.completed', digest)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('velia_creem_test_event',)]
        dump = '\n'.join(conn.iterdump())
    assert 'private@example.com' not in dump and 'Private customer' not in dump


def run_requests(tmp_path, scenario, *, secret=SECRET, store_factory=None):
    async def run():
        store = (store_factory() if store_factory else
                 TestEventStore(sqlite_path=str(tmp_path / 'inbox.sqlite')))
        app = web.Application()
        setup_creem_test_webhook(app, store=store, secret=secret)
        async with TestServer(app) as server, ClientSession() as client:
            async def post(data, *, signed=True, extra=None):
                raw = data if isinstance(data, bytes) else encoded(data)
                headers = {'Content-Type': 'application/json'}
                if signed:
                    headers['creem-signature'] = signature(raw)
                headers.update(extra or {})
                response = await client.post(server.make_url(PATH), data=raw, headers=headers)
                assert response.headers['Cache-Control'] == 'no-store'
                return response.status, await response.json()
            await scenario(post)
    asyncio.run(run())


def test_signed_deliveries_are_acknowledged_once_but_never_grant_tokens(tmp_path):
    async def scenario(post):
        for i, kind in enumerate(sorted(EVENTS)):
            data = event(kind, 'evt_' + str(i))
            status, result = await post(data)
            assert status == 200 and result == {
                'ok': True, 'mode': 'test', 'received': True, 'duplicate': False, 'credits_granted': False}
            assert (await post(data))[1]['duplicate'] is True
        data['object']['status'] = 'changed'
        assert (await post(data))[0] == 409
    run_requests(tmp_path, scenario)


def test_signature_is_over_exact_raw_bytes_and_has_no_cookie_or_redirect_fallback(tmp_path):
    async def scenario(post):
        data = event()
        assert (await post(data, signed=False))[0] == 401
        assert (await post(data, extra={'creem-signature': 'bad'}))[0] == 401
        assert (await post(data, extra={'creem-signature': signature(encoded(data), 'different-test-secret')}))[0] == 401
        assert (await post(encoded(data) + b' ', extra={'creem-signature': signature(encoded(data))}))[0] == 401
        assert (await post(data, extra={'Cookie': '__Host-velia-web=forged',
                                       'Origin': 'https://attacker.example',
                                       'creem-signature': '0' * 64}))[0] == 401
        assert (await post(data))[0] == 200
    run_requests(tmp_path, scenario)


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(mode='live'),
    lambda d: d['object'].update(mode='production'),
    lambda d: d.update(id='not-an-event'),
    lambda d: d.update(id='evt_' + 'x' * 160),
    lambda d: d.update(object=[]),
    lambda d: d.update(eventType=None),
])
def test_invalid_or_explicit_live_event_rejected(tmp_path, mutation):
    async def scenario(post):
        data = deepcopy(event())
        mutation(data)
        assert (await post(data))[0] == 400
    run_requests(tmp_path, scenario)


def test_schema_failures_ignored_credit_events_and_missing_mode_in_subscription(tmp_path):
    async def scenario(post):
        for raw in (b'not-json', b'[]', b'{"id":"evt_a","id":"evt_b"}',
                    b'{"id":"evt_a","eventType":"subscription.paid","object":{"x":NaN}}'):
            assert (await post(raw))[0] == 400
        for kind in ('credits.granted', 'credits.consumed', 'credits.auto-recharged', 'customer_credits.exhausted'):
            status, result = await post(event(kind))
            assert status == 200 and result['ignored'] and result['credits_granted'] is False
        data = event('subscription.paid')
        del data['object']['mode']
        assert (await post(data))[0] == 200
        assert (await post(b'x' * (MAX_BODY + 1)))[0] == 413
    run_requests(tmp_path, scenario)


def test_missing_secret_fails_closed(tmp_path):
    async def scenario(post):
        assert (await post(event())) == (503, {'ok': False, 'error': 'creem_test_not_configured'})
    run_requests(tmp_path, scenario, secret='')
    assert not (tmp_path / 'inbox.sqlite').exists()


def test_storage_failure_returns_retryable_error_without_leaking_exception(tmp_path, caplog):
    class FlakyStore:
        def __init__(self):
            self.calls = 0
        def initialize(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('password=secret-and-private@example.com')
        def remember(self, *_):
            return True
    async def scenario(post):
        assert (await post(event())) == (503, {'ok': False, 'error': 'delivery_storage_unavailable'})
        assert (await post(event()))[0] == 200
    run_requests(tmp_path, scenario, store_factory=FlakyStore)
    assert 'password=' not in caplog.text and 'private@example.com' not in caplog.text
