"""Exercise real PostgreSQL row locks, unique constraints and transaction rollback."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from services.velia_creem_billing import BillingService, BillingStore
from test_velia_creem_billing import API, NOW, SECRET, purchase, deliver


@pytest.fixture
def postgres_billing(monkeypatch):
    dsn = os.getenv('VELIA_CREEM_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('PostgreSQL test DSN is supplied by the required billing CI job')
    import psycopg2
    from psycopg2 import sql
    schema = 'billing_test_' + uuid.uuid4().hex
    def connect():
        conn = psycopg2.connect(dsn, options='-c search_path=' + schema, connect_timeout=5)
        return conn
    admin = psycopg2.connect(dsn)
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        admin.commit()
        with connect() as conn, conn.cursor() as cur:
            cur.execute('CREATE TABLE users(user_id BIGINT PRIMARY KEY,token_balance BIGINT,subscription_until TIMESTAMPTZ,updated_at TIMESTAMPTZ)')
            cur.execute('INSERT INTO users(user_id,token_balance) VALUES (7,100),(8,50)')
        monkeypatch.setenv('VELIA_CREEM_WEBHOOK_SECRET', SECRET)
        service = BillingService(BillingStore(connect, clock=lambda: NOW), API(), ready=lambda: True)
        service.store.initialize()
        yield service, connect
    finally:
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        admin.commit()
        admin.close()


def read_balance(connect, user=7):
    with connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT token_balance FROM users WHERE user_id=%s', (user,))
        return cur.fetchone()[0]


def test_postgres_parallel_payments_refunds_and_account_isolation(postgres_billing):
    service, connect = postgres_billing
    purchase(service)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: deliver(service,event='evt_pg_'+str(i)),range(16)))
    assert sum(x.get('credits_granted',0) for x in results) == 1200
    assert read_balance(connect) == 1300
    assert read_balance(connect,8) == 50
    assert service.account(7)['plan_code'] == 'plus'
    service.api.values[('transactions','tran_example')].update(status='refunded')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: deliver(service,kind='refund.created',event='evt_refund_pg_'+str(i),
            obj={'subscription':'sub_example','transaction':'tran_example'}),range(16)))
    assert read_balance(connect) == 100
    assert service.account(7)['plan_code'] == 'free'


def test_postgres_failed_delivery_rolls_back_balance_ledger_and_access(postgres_billing, monkeypatch):
    service, connect = postgres_billing
    purchase(service)
    original = service.store.refresh_access
    def fail(*args):
        raise RuntimeError('simulate interrupted transaction after credit update')
    monkeypatch.setattr(service.store,'refresh_access',fail)
    with pytest.raises(RuntimeError):
        deliver(service,event='evt_atomic')
    assert read_balance(connect) == 100
    with connect() as conn, conn.cursor() as cur:
        for table in ('velia_creem_charges','velia_creem_deliveries','velia_creem_subscriptions'):
            cur.execute('SELECT COUNT(*) FROM '+table)
            assert cur.fetchone()[0] == 0
    monkeypatch.setattr(service.store,'refresh_access',original)
    deliver(service,event='evt_atomic')
    assert read_balance(connect) == 1300
