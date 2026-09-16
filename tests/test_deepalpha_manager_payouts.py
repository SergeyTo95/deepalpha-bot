import os
import uuid
from datetime import datetime, timezone

import pytest

from db import database as db
from services import deepalpha_admin_access as access
from services import deepalpha_manager_payout_service as payouts
from services import ton_wallet_service as wallets
from services import treasury_service as treasury

OWNER = 777000


@pytest.fixture
def postgres(monkeypatch):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL integration required in CI')
    import psycopg2
    schema = 'manager_payout_' + uuid.uuid4().hex
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg2.connect(url, options=f'-c search_path={schema}')

    monkeypatch.setattr(db, 'get_connection', connect)
    monkeypatch.setattr(access, 'get_connection', connect)
    monkeypatch.setattr(payouts, 'get_connection', connect)
    monkeypatch.setenv('ADMIN_ID', str(OWNER))
    monkeypatch.setenv('TON_NETWORK', 'mainnet')
    monkeypatch.delenv('DEEPALPHA_MANAGER_BOOTSTRAP', raising=False)
    db.init_db()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users(user_id,username,token_balance) VALUES (%s,'owner',0),(11,'manager20',0),(12,'manager10',0)", (OWNER,))
    try:
        yield connect
    finally:
        with psycopg2.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _add_august_revenue(connect, gram=100):
    nano = int(gram * payouts.NANO)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO ton_purchase_intents(
                user_id,product_type,wallet_address,project_wallet,expected_amount_nano,status,
                requested_tokens,total_tokens,created_at,submitted_at,fulfilled_at,updated_at)
                VALUES (11,'token_purchase','src','dst',%s,'fulfilled',1,1,%s,%s,%s,%s)""",
                (str(nano), '2026-08-15T10:00:00+00:00', '2026-08-15T10:01:00+00:00',
                 '2026-08-15T10:02:00+00:00', '2026-08-15T10:02:00+00:00'))


def _backdate_shares(connect, *user_ids, timestamp='2026-08-01T00:00:00+00:00'):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE deepalpha_project_viewers SET share_effective_at=%s WHERE user_id=ANY(%s)",
                        (timestamp, list(user_ids)))


def test_bootstrap_applies_requested_roles_once(postgres, monkeypatch):
    monkeypatch.setenv('DEEPALPHA_MANAGER_BOOTSTRAP', '11:2000,12:1000')
    assert set(payouts.bootstrap_configured_managers()) == {11, 12}
    viewers = {v['user_id']: v for v in access.list_viewers(OWNER)}
    assert viewers[11]['share_bps'] == 2000
    assert viewers[12]['share_bps'] == 1000
    access.set_share_bps(OWNER, 11, 1500)
    assert payouts.bootstrap_configured_managers() == []
    viewers = {v['user_id']: v for v in access.list_viewers(OWNER)}
    assert viewers[11]['share_bps'] == 1500


def test_new_share_does_not_create_retroactive_closed_month_payout(postgres):
    connect = postgres
    access.set_viewer(OWNER, 11, active=True)
    access.set_share_bps(OWNER, 11, 2000)
    _add_august_revenue(connect, 100)
    assert payouts.ensure_previous_month_proposals(datetime(2026, 9, 16, tzinfo=timezone.utc)) == []
    assert payouts.list_pending(OWNER) == []


def test_retroactive_pending_created_before_migration_is_cancelled(postgres):
    connect = postgres
    access.set_viewer(OWNER, 11, active=True)
    access.set_share_bps(OWNER, 11, 2000)
    _add_august_revenue(connect, 100)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO deepalpha_manager_payouts(
                period_start,period_end,manager_user_id,share_bps,gross_revenue_nano,suggested_amount_nano,amount_nano,status)
                VALUES ('2026-08-01','2026-09-01',11,2000,%s,%s,%s,'pending')""",
                (100 * payouts.NANO, 20 * payouts.NANO, 20 * payouts.NANO))
    payouts.ensure_previous_month_proposals(datetime(2026, 9, 16, tzinfo=timezone.utc))
    assert payouts.list_pending(OWNER) == []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM deepalpha_manager_payouts WHERE manager_user_id=11")
            assert cur.fetchone()[0] == 'cancelled'


def test_monthly_proposals_are_gross_revenue_snapshot_and_idempotent(postgres):
    connect = postgres
    access.set_viewer(OWNER, 11, active=True)
    access.set_viewer(OWNER, 12, active=True)
    access.set_share_bps(OWNER, 11, 2000)
    access.set_share_bps(OWNER, 12, 1000)
    _backdate_shares(connect, 11, 12)
    _add_august_revenue(connect, 100)

    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    created = payouts.ensure_previous_month_proposals(now)
    assert len(created) == 2
    by_user = {r['manager_user_id']: r for r in created}
    assert by_user[11]['gross_revenue_nano'] == 100 * payouts.NANO
    assert by_user[11]['amount_nano'] == 20 * payouts.NANO
    assert by_user[12]['amount_nano'] == 10 * payouts.NANO
    assert payouts.ensure_previous_month_proposals(now) == []

    access.set_share_bps(OWNER, 11, 500)
    assert payouts.ensure_previous_month_proposals(now) == []
    pending = {r['manager_user_id']: r for r in payouts.list_pending(OWNER)}
    assert pending[11]['share_bps'] == 2000
    assert pending[11]['amount_nano'] == 20 * payouts.NANO


def test_owner_can_edit_before_confirmation_and_viewer_cannot(postgres):
    access.set_viewer(OWNER, 11, active=True)
    access.set_share_bps(OWNER, 11, 2000)
    _backdate_shares(postgres, 11)
    _add_august_revenue(postgres, 100)
    row = payouts.ensure_previous_month_proposals(datetime(2026, 9, 16, tzinfo=timezone.utc))[0]
    updated = payouts.set_amount(OWNER, row['id'], '15.5')
    assert updated['amount_nano'] == 15_500_000_000
    with pytest.raises(PermissionError):
        payouts.set_amount(11, row['id'], '12')
    with pytest.raises(PermissionError):
        access.set_share_bps(11, 11, 1000)
    with pytest.raises(ValueError, match='amount_exceeds_period_revenue'):
        payouts.set_amount(OWNER, row['id'], '101')


def test_approval_is_required_and_submission_is_idempotent(postgres, monkeypatch):
    access.set_viewer(OWNER, 11, active=True)
    access.set_share_bps(OWNER, 11, 2000)
    _backdate_shares(postgres, 11)
    _add_august_revenue(postgres, 100)
    row = payouts.ensure_previous_month_proposals(datetime(2026, 9, 16, tzinfo=timezone.utc))[0]

    monkeypatch.setattr(treasury, 'outgoing_enabled', lambda: False)
    assert payouts.approve_and_send(OWNER, row['id'])['error'] == 'treasury_outgoing_disabled'
    assert payouts.list_pending(OWNER)[0]['status'] == 'pending'

    calls = []
    monkeypatch.setattr(treasury, 'outgoing_enabled', lambda: True)
    monkeypatch.setattr(wallets, 'get_or_create_user_ton_wallet', lambda uid: {'ok': True, 'wallet_address': 'wallet11'})
    monkeypatch.setattr(treasury, 'resolve_internal_payout_wallet', lambda uid, conn=None, for_update=False: {'ok': True, 'wallet_id': 7, 'wallet_address': 'wallet11'})
    monkeypatch.setattr(treasury, 'get_public_treasury_address', lambda: {'ok': True, 'wallet_id': 8, 'address': 'treasury'})
    def send(address, amount, comment):
        calls.append((address, amount, comment))
        return {'ok': True, 'tx_hash': 'tx-manager-1'}
    monkeypatch.setattr(treasury, 'send_from_treasury', send)

    result = payouts.approve_and_send(OWNER, row['id'])
    assert result['ok'] and result['status'] == 'submitted'
    assert len(calls) == 1 and calls[0][2].startswith('payout:')
    second = payouts.approve_and_send(OWNER, row['id'])
    assert second['ok'] and second['already_submitted']
    assert len(calls) == 1


def test_total_active_share_cannot_exceed_100_percent(postgres):
    access.set_viewer(OWNER, 11, active=True)
    access.set_viewer(OWNER, 12, active=True)
    access.set_share_bps(OWNER, 11, 9000)
    with pytest.raises(ValueError, match='total_share_exceeds_100'):
        access.set_share_bps(OWNER, 12, 1100)
    assert access.set_share_bps(OWNER, 12, 1000) == 1000
