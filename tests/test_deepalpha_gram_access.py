import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from db import database as db
from services import deepalpha_admin_access as access
from services import gram_payment_health as health
from services import gram_purchase_service as purchase
from services import ton_service as scanner

SRC = '0:' + '1' * 64
DST = '0:' + '2' * 64
OWNER = 777000


def terms(count=10):
    return dict(ok=True, requested_tokens=count, bonus_tokens=2, total_tokens=count + 2,
                amount_nano=100000000, price_per_token_nano=10000000, project_wallet=DST, network='mainnet')


def intent():
    return dict(id=5, wallet_address=SRC, project_wallet=DST, expected_amount_nano='100000000',
                created_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())


def receipt(row=None):
    row = row or intent()
    return dict(hash='receiver-transaction', account=DST, emulated=False, finality='finalized',
                mc_block_seqno=100, now=int(datetime.now(timezone.utc).timestamp()) + 60,
                description=dict(aborted=False, compute_ph=dict(success=True, exit_code=0)),
                in_msg=dict(source=SRC, destination=DST, value=row['expected_amount_nano'], bounced=False,
                            message_content=dict(decoded=dict(comment=f"DeepAlpha token purchase:{row['id']}"))))


@pytest.mark.parametrize('path,value', [
    ('emulated', True), ('finality', 'pending'), ('mc_block_seqno', 0), ('now', 1), ('hash', ''),
    ('description.aborted', True), ('description.compute_ph.success', False),
    ('description.compute_ph.exit_code', 33), ('description.bounce', {'type': 'ok'}),
    ('description.action', {'success': False, 'result_code': 1}),
    ('in_msg.bounced', True), ('in_msg.source', DST), ('in_msg.destination', SRC),
    ('account', SRC), ('in_msg.value', '99999999'),
    ('in_msg.message_content.decoded.comment', 'DeepAlpha token purchase:50'),
    ('in_msg.message_content.decoded.comment', 'pay_5'),
])
def test_receipt_requires_actual_exact_finalized_incoming_funds(path, value):
    row, tx = intent(), receipt()
    assert purchase.matching_receipt(row, tx)
    target = tx
    keys = path.split('.')
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    assert not purchase.matching_receipt(row, tx)


def test_hash_alone_and_incomplete_receipt_are_not_confirmation():
    assert not purchase.matching_receipt(intent(), {'hash': 'sender-message-hash'})
    tx = receipt()
    del tx['in_msg']['bounced']
    assert not purchase.matching_receipt(intent(), tx)


def test_main_wallet_balance_is_independent_of_signing(monkeypatch):
    monkeypatch.setenv('TON_WALLET_ENABLED', 'false')
    monkeypatch.setenv('TON_NETWORK', 'mainnet')
    monkeypatch.setenv('TREASURY_INCOMING_ENABLED', 'true')
    monkeypatch.setattr(health.treasury, 'get_public_treasury_address', lambda: dict(ok=True, address=DST, network='mainnet'))
    monkeypatch.setattr(health, 'get_ton_balance', lambda address: 123)
    result = health.payment_health(with_balance=True)
    assert result['ready'] and result['balance_nano'] == 123
    monkeypatch.setattr(health, 'get_ton_balance', lambda address: (_ for _ in ()).throw(RuntimeError('unavailable')))
    result = health.payment_health(with_balance=True)
    assert result['balance_nano'] is None and result['balance_error'] == 'balance_unavailable'
    monkeypatch.setenv('TON_NETWORK', 'testnet')
    assert health.payment_health()['reason'] == 'treasury_network_mismatch'


def test_provider_requests_use_network_key_and_fail_closed(monkeypatch):
    monkeypatch.setenv('TON_NETWORK', 'mainnet')
    monkeypatch.setenv('TONCENTER_MAINNET_API_KEY', 'test-key')
    monkeypatch.delenv('TONCENTER_BASE_URL', raising=False)
    monkeypatch.setattr(scanner, 'get_public_treasury_address', lambda: dict(ok=True, address=DST, network='mainnet'))
    closed = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(True)
        def iter_content(self, size): yield b'{"ok":false,"result":[]}'
    def get(url, **kwargs):
        assert 'testnet' not in url and 'api_key' not in kwargs['params']
        assert kwargs['headers']['X-API-Key'] == 'test-key'
        assert kwargs['allow_redirects'] is False and kwargs['stream'] is True
        return Response()
    monkeypatch.setattr(scanner.requests, 'get', get)
    with pytest.raises(RuntimeError, match='treasury_scan_unavailable'):
        scanner._get_transactions_page()
    with pytest.raises(RuntimeError, match='receipt_response_invalid'):
        purchase.fetch_receipts(DST, 1)
    assert len(closed) == 2


def test_every_owner_handler_rejects_viewers_and_group_chats(monkeypatch):
    from tests.test_treasury_admin_command import _registered_admin, _FakeMessage
    _, dp = _registered_admin(monkeypatch)
    guarded = [r['handler'] for r in dp.message_handlers + dp.callback_handlers if hasattr(r['handler'], '__wrapped__')]
    assert len(guarded) > 100
    async def run():
        for user_id, chat_type in ((12345, 'private'), (OWNER, 'group')):
            event = _FakeMessage('/admin', user_id)
            event.chat.type = chat_type
            event.message = event
            event.answer = AsyncMock()
            for handler in guarded:
                await handler(event, state=object())
    asyncio.run(run())


def test_guard_preserves_fsm_arguments_and_denies_inline_callbacks():
    from bot.admin_guard import OwnerDispatcher
    registered = []
    class Dispatcher:
        def callback_query_handler(self, *args, **kwargs):
            return lambda fn: registered.append(fn) or fn
    calls = []
    @OwnerDispatcher(Dispatcher(), lambda uid: uid == OWNER).callback_query_handler()
    async def handler(callback, state):
        calls.append(state)
    async def run():
        event = SimpleNamespace(from_user=SimpleNamespace(id=OWNER), message=None, answer=AsyncMock())
        await registered[0](event, state='inline')
        event.message = SimpleNamespace(chat=SimpleNamespace(type='private', id=OWNER))
        await registered[0](event, state='owner-state')
    asyncio.run(run())
    assert calls == ['owner-state']


def test_moderation_only_allows_registered_viewers_to_open_readonly_panel(monkeypatch):
    from bot.admin_guard import can_open_view_during_moderation
    monkeypatch.setattr(access, 'can_view_project', lambda uid: uid == 11)
    event = SimpleNamespace(from_user=SimpleNamespace(id=11), chat=SimpleNamespace(type='private', id=11), text='/admin')
    event.message = event
    assert can_open_view_during_moderation(event)
    for action in ('deepalpha_view:overview', 'deepalpha_view:wallet'):
        event.data = action
        assert can_open_view_during_moderation(event, callback=True)
    for action in ('admin_back', 'deepalpha_team:add', 'ton_send', 'deepalpha_view:unknown'):
        event.data = action
        assert not can_open_view_during_moderation(event, callback=True)
    event.text = '/ton_send'
    assert not can_open_view_during_moderation(event)


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL integration required in CI')
    import psycopg2
    schema = 'gram_access_' + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg2.connect(url, options=f'-c search_path={schema}')
    monkeypatch.setattr(db, 'get_connection', connect)
    monkeypatch.setattr(access, 'get_connection', connect)
    monkeypatch.setenv('ADMIN_ID', str(OWNER))
    monkeypatch.setenv('TON_NETWORK', 'mainnet')
    try:
        db.init_db()
        db.init_db()  # The same migrations must tolerate multiple process starts.
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO users(user_id,username,token_balance) VALUES (%s,\'owner\',0),(11,\'one\',0),(12,\'two\',0),(13,\'three\',0)', (OWNER,))
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_postgres_two_viewers_immediate_revocation_owner_unchanged(postgres):
    def grant(uid):
        try:
            access.set_viewer(OWNER, uid, active=True)
            return uid
        except ValueError as exc:
            assert str(exc) == 'viewer_limit_reached'
    with ThreadPoolExecutor(max_workers=3) as pool:
        granted = [uid for uid in pool.map(grant, [11, 12, 13]) if uid]
    assert len(granted) == 2 and access.can_view_project(OWNER)
    viewer = granted[0]
    with pytest.raises(PermissionError):
        access.set_viewer(viewer, 13, active=True)
    with pytest.raises(ValueError, match='owner_role_is_fixed'):
        access.set_viewer(OWNER, OWNER, active=False)
    assert set(access.project_snapshot(viewer)) == {'users', 'tokens', 'analyses', 'payments', 'revenue_nano', 'pending'}
    access.set_viewer(OWNER, viewer, active=False)
    assert not access.can_view_project(viewer) and access.can_view_project(OWNER)
    with pytest.raises(PermissionError):
        access.project_snapshot(viewer)
    with postgres() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM deepalpha_viewer_audit')
            assert cur.fetchone()[0] == 3


def setup_purchase(monkeypatch):
    monkeypatch.setattr(purchase, 'quote', lambda uid, amount: terms(int(amount)))
    monkeypatch.setattr(purchase.wallets, 'get_or_create_user_ton_wallet', lambda uid: dict(ok=True, wallet_address=SRC))
    calls = []
    def send(*args, **kwargs):
        assert kwargs['allow_seqno_retry'] is False
        calls.append(args)
        return dict(ok=True, tx_hash='sender-hash')
    monkeypatch.setattr(purchase.wallets, 'send_ton_from_user_wallet', send)
    return calls


def test_postgres_concurrent_retry_sends_once_and_receipt_credits_once(postgres, monkeypatch):
    calls = setup_purchase(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: purchase.purchase(11, 10, 'same-request-key'), range(4)))
    assert len(calls) == 1 and len({r['intent_id'] for r in results}) == 1
    assert all(r['tokens_credited'] == 0 for r in results)
    ident = results[0]['intent_id']
    assert db.fulfill_ton_purchase_intent(ident) is None
    assert not purchase.verify_purchase(ident, receipts=[]).get('ok')
    assert purchase.purchase(11, 12, 'same-request-key')['error'] == 'idempotency_conflict'
    assert purchase.purchase(11, 10, 'different-request-key')['error'] == 'purchase_pending'
    tx = receipt(dict(id=ident, expected_amount_nano='100000000'))
    assert purchase.verify_purchase(ident, receipts=[tx])['ok']
    with ThreadPoolExecutor(max_workers=3) as pool:
        fulfilled = list(pool.map(lambda _: db.fulfill_ton_purchase_intent(ident), range(3)))
    assert sum(not r.get('already_fulfilled') for r in fulfilled) == 1
    assert purchase.purchase(11, 10, 'same-request-key')['tokens_credited'] == 12
    with postgres() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT token_balance FROM users WHERE user_id=11')
            assert cur.fetchone()[0] == 12


def test_postgres_uncertain_submission_recovers_in_background_without_resend(postgres, monkeypatch):
    setup_purchase(monkeypatch)
    calls = []
    def send(*args, **kwargs):
        calls.append(True)
        return dict(ok=False, error='send_failed')
    monkeypatch.setattr(purchase.wallets, 'send_ton_from_user_wallet', send)
    first = purchase.purchase(11, 10, 'uncertain-request-key')
    assert first['status'] == 'submitted'
    assert purchase.purchase(11, 10, 'uncertain-request-key')['intent_id'] == first['intent_id']
    assert len(calls) == 1
    row = dict(id=first['intent_id'], expected_amount_nano='100000000')
    monkeypatch.setattr(purchase, 'fetch_receipts', lambda *a, **k: [receipt(row)])
    from services import referral_rewards_service as rewards
    monkeypatch.setattr(rewards, 'process_token_purchase_referral_reward', lambda **kw: None)
    completed = purchase.reconcile_pending_purchases()
    assert len(completed) == 1 and completed[0]['status'] == 'fulfilled'
    assert purchase.reconcile_pending_purchases() == []


def test_postgres_changed_quote_cannot_send_and_known_failure_is_replayable(postgres, monkeypatch):
    calls = setup_purchase(monkeypatch)
    changed = {**terms(), 'amount_nano': 1}
    assert purchase.purchase(11, 10, 'changed-request-key', expected_quote=changed)['error'] == 'quote_changed'
    assert calls == []
    monkeypatch.setattr(purchase.wallets, 'send_ton_from_user_wallet', lambda *a, **k: dict(ok=False, error='insufficient_balance'))
    first = purchase.purchase(11, 10, 'no-funds-request-key')
    assert first['error'] == 'insufficient_balance'
    assert purchase.purchase(11, 10, 'no-funds-request-key')['error'] == 'insufficient_balance'


def test_postgres_verified_payment_resumes_after_credit_interruption(postgres, monkeypatch):
    setup_purchase(monkeypatch)
    created = purchase.purchase(11, 10, 'resume-credit-key')
    row = dict(id=created['intent_id'], expected_amount_nano='100000000')
    assert purchase.verify_purchase(row['id'], receipts=[receipt(row)])['ok']
    monkeypatch.setattr(purchase, 'fetch_receipts', lambda *a, **k: pytest.fail('Confirmed receipt must not need another RPC'))
    from services import referral_rewards_service as rewards
    monkeypatch.setattr(rewards, 'process_token_purchase_referral_reward', lambda **kw: None)
    completed = purchase.reconcile_pending_purchases()
    assert len(completed) == 1 and completed[0]['status'] == 'fulfilled'
