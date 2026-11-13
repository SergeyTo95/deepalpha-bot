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
        result={'id': 'ch_example', 'mode': 'prod', 'status':'pending', 'product':data['product_id'],
                'request_id':data['request_id'], 'metadata':deepcopy(data['metadata']),
                'checkout_url': 'https://www.creem.io/checkout/prod_example/ch_example'}
        self.values[('checkouts','ch_example')]=deepcopy(result)
        return result


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


def test_uncertain_checkout_reuses_exact_durable_order_and_request_id(billing):
    service,connect=billing
    original=service.api.request
    posted=[]
    def lost_once(method,resource,**kwargs):
        if method=='POST' and resource=='checkouts':
            posted.append(deepcopy(kwargs['data']))
            result=original(method,resource,**kwargs)
            if len(posted)==1:raise BillingError('billing_provider_timeout',502)
            return result
        return original(method,resource,**kwargs)
    service.api.request=lost_once
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    result=service.checkout(7,'plus',str(uuid.uuid4()))
    assert result['ok'] and posted[0]==posted[1]
    with connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM velia_creem_orders').fetchone()[0]==1
    assert balance(connect)==(100,None)


def test_uncertain_checkout_cannot_switch_product_or_user_binding(billing):
    service,_=billing
    original=service.api.request
    posted=[]
    def lost(method,resource,**kwargs):
        if method=='POST' and resource=='checkouts':
            posted.append(kwargs['data'])
            raise BillingError('billing_provider_timeout',502)
        return original(method,resource,**kwargs)
    service.api.request=lost
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    with pytest.raises(BillingError,match='checkout_already_pending'):service.checkout(7,'max',str(uuid.uuid4()))
    assert len(posted)==1


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


@pytest.mark.parametrize('url',['http://www.creem.io/checkout/ch_a','https://www.creem.io.evil.test/checkout/ch_a','https://evil.test','https://www.creem.io/test/payment/prod_example','https://user@www.creem.io/checkout/ch_a','https://www.creem.io:444/checkout/ch_a',None,'https://www.creem.io/','https://www.creem.io/checkout/ch_a#fragment','https://www.creem.io/checkout/ch_a;param','https://www.creem.io/checkout/ch_a\n'])
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


@pytest.mark.parametrize('tracking_echo',[True,False])
def test_known_checkout_is_recovered_by_read_only_lookup_after_validation_failure(billing,tracking_echo):
    service,connect=billing
    original=service.api.request
    def bad_url(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':result['checkout_url']='https://untrusted.test/checkout/ch_example'
        return result
    service.api.request=bad_url
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    with connect() as conn:
        order=conn.execute('SELECT id,checkout_id FROM velia_creem_orders').fetchone()
    assert order[1]=='ch_example'
    service.api.values[('checkouts','ch_example')]={'id':'ch_example','mode':'prod','status':'pending',
        'product':PLANS['plus']['product_id'],'request_id':order[0] if tracking_echo else None,
        'metadata':{'velia_order_id':order[0]},
        'checkout_url':'https://www.creem.io/checkout/prod_example/ch_example'}
    assert service.checkout(7,'plus',str(uuid.uuid4()))['ok'] is True
    assert len(service.api.posts)==1


def test_provider_redirect_token_is_opaque_and_saved_retry_reuses_it(billing):
    service,connect=billing
    original=service.api.request
    url='https://www.creem.io/checkout/'+PLANS['plus']['product_id']+'/opaqueHostedToken'
    def opaque_url(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':result['checkout_url']=url
        return result
    service.api.request=opaque_url
    assert service.checkout(7,'plus',str(uuid.uuid4()))=={'ok':True,'checkout_url':url}
    assert service.checkout(7,'plus',str(uuid.uuid4()))=={'ok':True,'checkout_url':url}
    assert len(service.api.posts)==1
    with connect() as conn:
        assert conn.execute('SELECT checkout_id,state FROM velia_creem_orders').fetchone()==('ch_example','open')
    assert balance(connect)==(100,None)


@pytest.mark.parametrize('field,value,phase',[
    ('product',PLANS['max']['product_id'],'validate_product'),
    ('request_id','another-owner-order','validate_metadata'),
])
def test_opaque_redirect_does_not_bypass_order_binding(billing,caplog,field,value,phase):
    service,connect=billing
    original=service.api.request
    def mismatched(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':
            result[field]=value
            result['checkout_url']='https://www.creem.io/checkout/prod_other/opaqueToken'
            service.api.values[('checkouts','ch_example')].update(
                request_id='another-owner-order',metadata={'velia_order_id':'foreign-order'})
        return result
    service.api.request=mismatched
    with pytest.raises(BillingError,match='billing_checkout_conflict'):
        service.checkout(7,'plus',str(uuid.uuid4()))
    assert 'phase='+phase in caplog.text
    assert value not in caplog.text and 'opaqueToken' not in caplog.text
    assert balance(connect)==(100,None)
    with connect() as conn:
        assert conn.execute('SELECT checkout_id,state FROM velia_creem_orders').fetchone()==(None,'uncertain')


@pytest.mark.parametrize('tracking',[None,'provider-tracking-reference'])
def test_checkout_without_echoed_tracking_uses_independent_metadata_lookup(billing,tracking):
    service,connect=billing
    original=service.api.request
    gets=[]
    original_get=service.api.get
    def get(resource,key,value):
        gets.append((resource,key,value))
        return original_get(resource,key,value)
    def without_tracking(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':
            result['request_id']=tracking
            result.pop('metadata')  # A partial create response cannot prove ownership.
            service.api.values[('checkouts','ch_example')]['request_id']=tracking
        return result
    service.api.request=without_tracking
    service.api.get=get
    assert service.checkout(7,'plus',str(uuid.uuid4()))['ok']
    assert ('checkouts','checkout_id','ch_example') in gets
    assert balance(connect)==(100,None)
    assert service.checkout(7,'plus',str(uuid.uuid4()))['ok']
    assert len(service.api.posts)==1


@pytest.mark.parametrize('field,value',[
    ('id','ch_foreign'),('mode','test'),('product',PLANS['max']['product_id']),
    ('metadata',None),('metadata',{}),('metadata',{'velia_order_id':'foreign-order'}),
    ('status','completed'),('checkout_url','https://evil.test/checkout/ch_example'),
])
def test_metadata_lookup_must_match_the_exact_checkout_and_owner(billing,field,value):
    service,connect=billing
    original=service.api.request
    def mismatched_lookup(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':
            result.pop('request_id')
            canonical=service.api.values[('checkouts','ch_example')]
            canonical.pop('request_id')
            canonical[field]=value
        return result
    service.api.request=mismatched_lookup
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    assert balance(connect)==(100,None)


@pytest.mark.parametrize('tracking',[None,'provider-tracking-reference'])
def test_signed_completed_checkout_uses_canonical_metadata_when_tracking_is_not_echoed(billing,tracking):
    service,connect=billing
    purchase(service)
    service.api.values[('checkouts','ch_example')]['request_id']=tracking
    deliver(service,kind='checkout.completed',obj={'id':'ch_example'})
    deliver(service,kind='checkout.completed',event='evt_repeated',obj={'id':'ch_example'})
    assert balance(connect)[0]==1300
    assert balance(connect,8)==(50,None)


def test_canonical_metadata_cannot_reassign_a_checkout_to_another_accounts_order(billing):
    service,connect=billing
    purchase(service)
    other,_=service.store.reserve(8,'plus',str(uuid.uuid4()))
    service.store.checkout_created(other,'ch_other','https://www.creem.io/checkout/prod_example/ch_other')
    service.api.values[('checkouts','ch_example')].update(
        request_id=None,metadata={'velia_order_id':other['id']})
    with pytest.raises(BillingError,match='billing_owner_conflict'):
        deliver(service,kind='checkout.completed',obj={'id':'ch_example'})
    assert balance(connect)==(100,None)
    assert balance(connect,8)==(50,None)


@pytest.mark.parametrize('family',['checkout','payment'])
@pytest.mark.parametrize('lookup_has_url',[True,False])
def test_verified_metadata_lookup_preserves_provider_redirect_contract(billing,family,lookup_has_url):
    service,connect=billing
    original=service.api.request
    url='https://www.creem.io/'+family+'/'+PLANS['plus']['product_id']+'?checkout_id=ch_example'
    def create(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':
            result.pop('request_id')
            result['checkout_url']=url
            canonical=service.api.values[('checkouts','ch_example')]
            canonical.pop('request_id')
            canonical['checkout_url']=url if lookup_has_url else None
        return result
    service.api.request=create
    assert service.checkout(7,'plus',str(uuid.uuid4()))=={'ok':True,'checkout_url':url}
    assert service.checkout(7,'plus',str(uuid.uuid4()))=={'ok':True,'checkout_url':url}
    assert len(service.api.posts)==1 and balance(connect)==(100,None)


def test_url_diagnostics_do_not_log_provider_url_or_metadata(billing,caplog):
    service,_=billing
    original=service.api.request
    def invalid(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':result['checkout_url']='https://foreign.test/private?token=must-not-log'
        return result
    service.api.request=invalid
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    assert 'validation_reason=host' in caplog.text
    assert 'must-not-log' not in caplog.text and 'foreign.test' not in caplog.text


@pytest.mark.parametrize('plan',list(PLANS))
@pytest.mark.parametrize('missing_url',[None,''])
def test_old_uncertain_checkout_without_saved_or_api_url_recovers_existing_session(billing,plan,missing_url):
    service,connect=billing
    order,_=service.store.reserve(7,plan,str(uuid.uuid4()))
    service.store.remember_checkout(order,'ch_existing')
    service.store.uncertain(order)
    service.api.values[('checkouts','ch_existing')]={
        'id':'ch_existing','mode':'prod','status':'pending','product':PLANS[plan]['product_id'],
        'metadata':{'velia_order_id':order['id']},'checkout_url':missing_url}
    expected='https://www.creem.io/checkout/'+PLANS[plan]['product_id']+'/ch_existing'
    assert service.checkout(7,plan,str(uuid.uuid4()))=={'ok':True,'checkout_url':expected}
    assert service.checkout(7,plan,str(uuid.uuid4()))=={'ok':True,'checkout_url':expected}
    assert service.api.posts==[]  # Both clicks reuse the same existing session.
    with connect() as conn:
        assert conn.execute('SELECT id,checkout_id,state,checkout_url FROM velia_creem_orders').fetchone()==(
            order['id'],'ch_existing','open',expected)
    assert balance(connect)==(100,None)


@pytest.mark.parametrize('field,value',[
    ('id','ch_foreign'),('mode','test'),('product',PLANS['max']['product_id']),
    ('metadata',{}),('metadata',{'velia_order_id':'foreign-order'}),('status','expired'),
])
def test_missing_redirect_recovery_cannot_open_an_unverified_session(billing,field,value):
    service,connect=billing
    order,_=service.store.reserve(7,'plus',str(uuid.uuid4()))
    service.store.remember_checkout(order,'ch_existing')
    service.store.uncertain(order)
    canonical={'id':'ch_existing','mode':'prod','status':'pending','product':PLANS['plus']['product_id'],
        'metadata':{'velia_order_id':order['id']},'request_id':None}
    canonical[field]=value
    service.api.values[('checkouts','ch_existing')]=canonical
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    assert service.api.posts==[] and balance(connect)==(100,None)


def test_new_checkout_without_redirect_requires_independent_metadata_proof(billing):
    service,_=billing
    original=service.api.request
    def create(method,resource,**kwargs):
        result=original(method,resource,**kwargs)
        if resource=='checkouts':
            result.pop('checkout_url')
            service.api.values[('checkouts','ch_example')].pop('checkout_url')
        return result
    service.api.request=create
    result=service.checkout(7,'plus',str(uuid.uuid4()))
    assert result['checkout_url']=='https://www.creem.io/checkout/'+PLANS['plus']['product_id']+'/ch_example'
    assert len(service.api.posts)==1


@pytest.mark.parametrize('included',['plus','pro','max','all'])
def test_tax_included_products_enable_catalog_and_preserve_each_plans_tax_label(billing,included):
    service,connect=billing
    for plan,spec in PLANS.items():
        if included in {plan,'all'}:service.api.values[('products',spec['product_id'])]['tax_mode']='inclusive'
    catalog=service.catalog()
    assert catalog['enabled'] is True
    for item in catalog['plans']:
        expected='inclusive' if included in {item['code'],'all'} else 'exclusive'
        assert item['tax_mode']==expected
        assert item['price']==PLANS[item['code']]['price']/100
    assert service.checkout(7,'plus',str(uuid.uuid4()))['ok']
    assert balance(connect)==(100,None)


@pytest.mark.parametrize('field,value',[
    ('price',1),('currency','EUR'),('billing_period','every-year'),
    ('status','archived'),('mode','test'),('tax_mode','unknown'),('tax_mode',None),
])
def test_supporting_inclusive_tax_does_not_disable_product_validation(billing,field,value):
    service,_=billing
    product=service.api.values[('products',PLANS['plus']['product_id'])]
    product['tax_mode']='inclusive'
    product[field]=value
    catalog=service.catalog()
    assert catalog['enabled'] is False and catalog['unavailable_reason']=='billing_product_mismatch'
    assert all('tax_mode' not in item for item in catalog['plans'])
    with pytest.raises(BillingError):service.checkout(7,'plus',str(uuid.uuid4()))
    assert not service.api.posts


@pytest.mark.parametrize('net_amount',[False,True])
def test_tax_inclusive_paid_invoice_and_refund_grant_and_reverse_once(billing,net_amount):
    service,connect=billing
    service.api.values[('products',PLANS['plus']['product_id'])]['tax_mode']='inclusive'
    purchase(service)
    tx=service.api.values[('transactions','tran_example')]
    tx.update(amount=1249 if net_amount else 1499,tax_amount=250,amount_paid=1499)
    deliver(service,kind='checkout.completed',obj={'id':'ch_example'})
    deliver(service,event='evt_duplicate_paid')
    assert balance(connect)[0]==1300
    tx.update(status='partially_refunded',refunded_amount=750)
    deliver(service,kind='refund.created',event='evt_tax_refund',obj={'subscription':'sub_example','transaction':'tran_example'})
    after=balance(connect)[0]
    assert 100<after<1300
    deliver(service,kind='refund.created',event='evt_tax_refund_repeat',obj={'subscription':'sub_example','transaction':'tran_example'})
    assert balance(connect)[0]==after
    tx.update(status='refunded',refunded_amount=1499)
    deliver(service,kind='refund.created',event='evt_tax_full_refund',obj={'subscription':'sub_example','transaction':'tran_example'})
    assert balance(connect)[0]==100


@pytest.mark.parametrize('amount,tax,paid',[
    (1249,250,1249),(1249,249,1499),(1249,None,1499),(1249,-1,1499),
    (1499,250,1498),(1249,True,1499),(1249,'250',1499),(True,1498,1499),
])
def test_inclusive_tax_never_accepts_underpaid_or_unproven_amounts(billing,amount,tax,paid):
    service,connect=billing
    service.api.values[('products',PLANS['plus']['product_id'])]['tax_mode']='inclusive'
    purchase(service)
    service.api.values[('transactions','tran_example')].update(amount=amount,tax_amount=tax,amount_paid=paid)
    with pytest.raises(BillingError,match='billing_amount_mismatch'):deliver(service)
    assert balance(connect)==(100,None)
