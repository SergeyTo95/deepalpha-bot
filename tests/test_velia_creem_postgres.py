"""Exercise real PostgreSQL row locks, unique constraints and transaction rollback."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from services.velia_creem_billing import BillingService, BillingStore
from test_velia_creem_billing import API, NOW, SECRET, purchase, deliver, plan_checkout_api


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


def test_postgres_concurrent_retry_claims_reuse_one_order_without_granting(postgres_billing):
    from copy import deepcopy
    from services.velia_creem_billing import BillingError
    service,connect=postgres_billing
    original=service.api.request
    posted=[]
    def lost_once(method,resource,**kwargs):
        if method=='POST' and resource=='checkouts':
            posted.append(deepcopy(kwargs['data']))
            if len(posted)==1:raise BillingError('billing_provider_timeout',502)
        return original(method,resource,**kwargs)
    service.api.request=lost_once
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    def retry(_):
        try:return service.checkout(7,'plus',str(uuid.uuid4()))
        except BillingError as error:
            assert error.code=='checkout_already_pending'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(retry,range(16)))
    assert len(posted)==2 and posted[0]==posted[1]
    assert any(result and result['ok'] for result in results)
    assert read_balance(connect)==100
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM velia_creem_orders');assert cur.fetchone()[0]==1
        cur.execute('SELECT COUNT(*) FROM velia_creem_charges');assert cur.fetchone()[0]==0


def test_postgres_plan_switching_keeps_one_session_per_plan_during_parallel_clicks(postgres_billing):
    from services.velia_creem_billing import BillingError,PLANS
    service,connect=postgres_billing
    plan_checkout_api(service)
    plans=list(PLANS)
    def choose(i):
        try:return service.checkout(7,plans[i%3],str(uuid.uuid4()))
        except BillingError as error:
            assert error.code=='checkout_already_pending'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(choose,range(30)))
    assert len(service.api.posts)==3
    assert len({r['checkout_url'] for r in results if r})==3
    assert len({service.checkout(7,p,str(uuid.uuid4()))['checkout_url'] for p in plans})==3
    assert len(service.api.posts)==3 and read_balance(connect)==100
    with connect() as conn,conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM velia_creem_orders');assert cur.fetchone()[0]==3
        cur.execute('SELECT COUNT(*) FROM velia_creem_subscriptions');assert cur.fetchone()[0]==0
