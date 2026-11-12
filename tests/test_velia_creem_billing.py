import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import hmac
import json
import math
import sqlite3
import uuid
import pytest
from services.velia_creem_billing import BillingError, BillingService, BillingStore, PLANS, WEBHOOK_URL, checkout_url, iso

NOW = 1794500000
SECRET = 'unit-test-webhook-secret-not-a-real-key'


class API:
    def __init__(self):
        self.posts = []
        self.values = {}
        for spec in PLANS.values():
            self.values[('products', spec['product_id'])] = {
                'id': spec['product_id'], 'mode': 'prod', 'price': spec['price'],
                'currency': 'USD', 'billing_type': 'recurring', 'billing_period': 'every-month', 'status': 'active', 'tax_mode':'exclusive'}

    def get(self, resource, _, value):
        return deepcopy(self.values[(resource, value)])

    def request(self, method, resource, *, data=None, params=None):
        if method == 'GET' and resource == 'webhooks':
            return {'items':[{'id':'wh_example','url':WEBHOOK_URL,'mode':'prod','status':'enabled','events':[],'delivery_mode':'http'}]}
        if method == 'GET' and resource == 'webhooks/wh_example/secret':
            return {'secret':SECRET}
        self.posts.append((resource, deepcopy(data)))
        if resource == 'customers/billing':
            return {'customer_portal_link': 'https://www.creem.io/portal/cust_example?token=private'}
        return {'id': 'ch_example', 'mode': 'prod', 'product':data['product_id'], 'request_id':data['request_id'], 'checkout_url': 'https://www.creem.io/checkout/prod_example/ch_example'}


@pytest.fixture
def billing(tmp_path,monkeypatch):
    monkeypatch.setenv('VELIA_CREEM_WEBHOOK_SECRET',SECRET)
    path = str(tmp_path / 'billing.db')
    connect = lambda: sqlite3.connect(path, timeout=10)
    with connect() as conn:
        conn.execute('CREATE TABLE users(user_id BIGINT PRIMARY KEY,token_balance BIGINT,subscription_until TEXT,updated_at TEXT)')
        conn.executemany('INSERT INTO users(user_id,token_balance) VALUES (?,?)', [(7,100),(8,50)])
    store = BillingStore(connect, sqlite=True, clock=lambda: NOW)
    store.initialize()
    service = BillingService(store, API(), ready=lambda: True)
    return service, connect


def balance(connect, user=7):
    with connect() as conn:
        return conn.execute('SELECT token_balance,subscription_until FROM users WHERE user_id=?', (user,)).fetchone()


def purchase(service, *, plan='plus'):
    service.checkout(7, plan, str(uuid.uuid4()))
    order_id = service.api.posts[-1][1]['metadata']['velia_order_id']
    spec = PLANS[plan]
    service.api.values[('subscriptions','sub_example')] = {
        'id':'sub_example','mode':'prod','product':spec['product_id'],'customer':{'id':'cust_example','email':'private@example.com'},
        'items':[{'product_id':spec['product_id'],'units':1}], 'status':'active', 'updated_at':iso(NOW),
        'current_period_end_date':iso(NOW+30*86400),'last_transaction_id':'tran_example',
        'metadata':{'velia_order_id':order_id}}
    service.api.values[('transactions','tran_example')] = {
        'id':'tran_example','mode':'prod','subscription':'sub_example','customer':'cust_example',
        'amount':spec['price'],'amount_paid':spec['price'],'currency':'USD','type':'invoice','status':'paid',
        'period_start':NOW*1000,'period_end':(NOW+30*86400)*1000,'refunded_amount':None}
    service.api.values[('checkouts','ch_example')] = {
        'id':'ch_example','mode':'prod','status':'completed','request_id':order_id,
        'metadata':{'velia_order_id':order_id},'product':spec['product_id'],
        'subscription':'sub_example','order':{'transaction':'tran_example'}}
    return order_id


def deliver(service, *, kind='subscription.paid', event='evt_example', obj=None):
    if obj is None:
        obj = {'id':'sub_example','last_transaction_id':'tran_example','mode':'prod'}
    raw = json.dumps({'id':event,'eventType':kind,'object':obj}).encode()
    signature = hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    return service.webhook(raw, signature, SECRET)


@pytest.mark.parametrize('plan', list(PLANS))
def test_first_payment_checkout_and_manual_resends_grant_once_and_set_plan(billing,plan):
    service, connect = billing
    purchase(service,plan=plan)
    deliver(service,kind='checkout.completed',obj={'id':'ch_example'})
    deliver(service,event='evt_payment')
    deliver(service,event='evt_manually_resent')
    assert deliver(service,event='evt_payment')['duplicate'] is True
    assert balance(connect)[0] == 100+PLANS[plan]['credits']
    assert service.account(7)['plan_code'] == plan
    assert service.account(8)['plan_code'] == 'free'
    assert balance(connect,8) == (50,None)
    with connect() as conn:
        assert 'private@example.com' not in '\n'.join(conn.iterdump())


def test_concurrent_duplicate_payments_are_atomic(billing):
    service, connect = billing
    purchase(service)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: deliver(service,event='evt_'+str(i)), range(12)))
    assert sum(item.get('credits_granted',0) for item in results) == 1200
    assert balance(connect)[0] == 1300


def test_different_delivery_id_and_transaction_cannot_double_grant_a_paid_period(billing):
    service, connect = billing
    purchase(service)
    deliver(service)
    duplicate = deepcopy(service.api.values[('transactions','tran_example')])
    duplicate['id'] = 'tran_duplicate'
    service.api.values[('transactions','tran_duplicate')] = duplicate
    deliver(service,event='evt_duplicate_period',obj={'id':'sub_example','last_transaction_id':'tran_duplicate'})
    assert balance(connect)[0] == 1300


def test_renewal_grants_new_period_once(billing):
    service, connect = billing
    purchase(service)
    deliver(service)
    tx = deepcopy(service.api.values[('transactions','tran_example')])
    tx.update(id='tran_renewal',period_start=(NOW+30*86400)*1000,period_end=(NOW+60*86400)*1000)
    service.api.values[('transactions','tran_renewal')] = tx
    sub = service.api.values[('subscriptions','sub_example')]
    sub.update(updated_at=iso(NOW+1),current_period_end_date=iso(NOW+60*86400),last_transaction_id='tran_renewal')
    deliver(service,event='evt_renewal',obj={'id':'sub_example','last_transaction_id':'tran_renewal'})
    deliver(service,event='evt_renewal_again',obj={'id':'sub_example','last_transaction_id':'tran_renewal'})
    assert balance(connect) == (2500,iso(NOW+60*86400))


@pytest.mark.parametrize('field,value', [('mode','test'),('amount',1),('currency','EUR'),('customer','cust_other'),('subscription','sub_other'),('status','pending')])
def test_provider_mismatch_never_grants(billing,field,value):
    service, connect = billing
    purchase(service)
    service.api.values[('transactions','tran_example')][field] = value
    with pytest.raises(BillingError):
        deliver(service)
    assert balance(connect) == (100,None)


def test_signature_and_environment_fail_closed_before_processing(billing):
    service, connect = billing
    for raw,signature,secret in [(b'{}','0'*64,SECRET),(b'{}','bad',SECRET),(b'{}','0'*64,'')]:
        with pytest.raises(BillingError):
            service.webhook(raw,signature,secret)
    service.ready = lambda: False
    assert service.catalog()['enabled'] is False
    with pytest.raises(BillingError):
        service.checkout(7,'plus',str(uuid.uuid4()))
    assert balance(connect) == (100,None)


def test_owner_binding_and_event_id_reuse_are_rejected(billing):
    service, connect = billing
    purchase(service)
    deliver(service)
    with pytest.raises(BillingError,match='billing_event_conflict'):
        deliver(service,kind='subscription.active')
    service.api.values[('subscriptions','sub_example')]['metadata']['velia_order_id'] = 'another-account'
    with pytest.raises(BillingError):
        deliver(service,event='evt_owner')
    assert balance(connect)[0] == 1300


def test_order_attempt_resumes_existing_checkout_but_cannot_create_two_subscriptions(billing):
    service, _ = billing
    attempt = str(uuid.uuid4())
    first = service.checkout(7,'plus',attempt)
    assert service.checkout(7,'plus',attempt) == first
    assert service.checkout(7,'plus',str(uuid.uuid4())) == first
    assert len(service.api.posts) == 1
    with pytest.raises(BillingError):
        service.checkout(7,'max',attempt)


def test_uncertain_checkout_is_not_retried_or_charged_again(billing):
    service, _ = billing
    count = []
    original = service.api.request
    def lost(method, resource, **kwargs):
        if resource != 'checkouts' or method != 'POST':
            return original(method, resource, **kwargs)
        count.append(1)
        raise BillingError('billing_provider_unavailable',502)
    service.api.request = lost
    with pytest.raises(BillingError):
        service.checkout(7,'plus',str(uuid.uuid4()))
    with pytest.raises(BillingError):
        service.checkout(7,'plus',str(uuid.uuid4()))
    assert len(count) == 1  # An ambiguous create is held for reconciliation, never reissued.


@pytest.mark.parametrize('before_paid',[False,True])
@pytest.mark.parametrize('partial',[False,True])
def test_refunds_are_proportional_idempotent_and_out_of_order_safe(billing,before_paid,partial):
    service, connect = billing
    purchase(service)
    if not before_paid:
        deliver(service)
    tx = service.api.values[('transactions','tran_example')]
    tx.update(status='partially_refunded' if partial else 'refunded',refunded_amount=500 if partial else 1499)
    obj = {'subscription':'sub_example','transaction':'tran_example'}
    deliver(service,kind='refund.created',event='evt_refund',obj=obj)
    expected_revoked = math.ceil(1200*500/1499) if partial else 1200
    assert balance(connect)[0] == 100+1200-expected_revoked
    deliver(service,kind='refund.created',event='evt_refund_again',obj=obj)
    assert balance(connect)[0] == 100+1200-expected_revoked
    assert service.account(7)['plan_code'] == ('plus' if partial else 'free')


def test_spent_credit_refund_records_unrecovered_amount_without_negative_balance(billing):
    service, connect = billing
    purchase(service)
    deliver(service)
    with connect() as conn:
        conn.execute('UPDATE users SET token_balance=10 WHERE user_id=7')
    service.api.values[('transactions','tran_example')].update(status='chargeback')
    deliver(service,kind='dispute.created',event='evt_dispute',obj={'subscription':'sub_example','transaction':'tran_example'})
    assert balance(connect)[0] == 0
    with connect() as conn:
        assert conn.execute('SELECT unrecovered FROM velia_creem_charges').fetchone()[0] == 1190


def test_cancel_preserves_other_provider_entitlement_and_stale_events_cannot_resurrect(billing):
    service, connect = billing
    baseline = NOW+40*86400
    with connect() as conn:
        conn.execute('UPDATE users SET subscription_until=? WHERE user_id=7',(iso(baseline),))
    purchase(service)
    deliver(service)
    sub = service.api.values[('subscriptions','sub_example')]
    sub.update(status='canceled',updated_at=iso(NOW+2))
    deliver(service,kind='subscription.canceled',event='evt_cancel')
    assert service.account(7)['plan_code'] == 'free'
    assert balance(connect)[1] == iso(baseline)
    sub.update(status='active',updated_at=iso(NOW+1))
    deliver(service,kind='subscription.active',event='evt_stale')
    assert service.account(7)['plan_code'] == 'free'


@pytest.mark.parametrize('url',['http://www.creem.io/checkout/ch_a','https://www.creem.io.evil.test/checkout/ch_a','https://evil.test','https://www.creem.io/payment/prod_example','https://user@www.creem.io/checkout/ch_a','https://www.creem.io:444/checkout/ch_a'])
def test_checkout_redirect_allowlist(url):
    with pytest.raises(BillingError):
        checkout_url(url)


@pytest.mark.parametrize('fault', ['secret','url','disabled','events','mode'])
def test_live_checkout_requires_verified_webhook_configuration(billing, fault):
    service, _ = billing
    original = service.api.request
    def altered(method, resource, **kwargs):
        value = original(method, resource, **kwargs)
        if resource == 'webhooks' and fault != 'secret':
            field, replacement = {'url':('url','https://foreign.test/hook'),
                'disabled':('status','disabled'),'events':('events',['checkout.completed']),
                'mode':('mode','test')}[fault]
            value['items'][0][field] = replacement
        elif resource.endswith('/secret') and fault == 'secret':
            value['secret'] = 'wrong-secret'
        return value
    service.api.request = altered
    assert service.catalog()['enabled'] is False
    with pytest.raises(BillingError):
        service.checkout(7,'plus',str(uuid.uuid4()))
    assert not service.api.posts


def test_portal_uses_only_authenticated_owner_customer(billing):
    service, _ = billing
    purchase(service)
    deliver(service)
    assert service.portal(7)['portal_url'].startswith('https://www.creem.io/portal/')
    assert service.api.posts[-1] == ('customers/billing', {'customer_id':'cust_example'})
    before = len(service.api.posts)
    with pytest.raises(BillingError):
        service.portal(8)
    assert len(service.api.posts) == before


def test_readiness_returns_only_safe_reason_and_caches_provider_failures(billing):
    service,_=billing
    calls=[]
    def unavailable(*args,**kwargs):
        calls.append(1)
        raise BillingError('billing_provider_unavailable',502,provider_status=403)
    service.api.request=unavailable
    value=service.catalog()
    assert value['enabled'] is False and value['unavailable_reason']=='billing_provider_credentials'
    assert service.catalog()==value and len(calls)==1
    assert set(value)=={'ok','enabled','mode','plans','unavailable_reason'}
