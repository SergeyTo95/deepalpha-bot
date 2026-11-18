import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import uuid

from aiohttp import web,ClientSession
from aiohttp.test_utils import TestServer
import pytest

from services.velia_creem_billing import BillingError,PLANS
from services.velia_creem_plan_change import SANDBOX_RETURN_URL
from services.velia_creem_sandbox_account import (SandboxAccountService,SandboxAccountStore,
    TEST_PLANS,test_checkout_url as destination)
from services.velia_creem_routes import setup_creem_routes

NOW=1794500000


class AccountAPI:
    def __init__(self):
        self.posts=[]
        self.upgrades=[]
        self.values={('products',s['product_id']):{'id':s['product_id'],'mode':'test','price':s['price'],
            'currency':'USD','billing_type':'recurring','billing_period':'every-month','status':'active',
            'tax_mode':'inclusive'} for s in TEST_PLANS.values()}

    def get(self,resource,field,value):return deepcopy(self.values[(resource,value)])

    def request(self,method,resource,*,data=None,params=None):
        if method=='POST' and resource.endswith('/upgrade'):
            self.upgrades.append((resource,deepcopy(data)))
            sub=self.values[('subscriptions',resource.split('/')[1])]
            sub.update(product=data['product_id'],items=[{'product_id':data['product_id'],'units':1}])
            return deepcopy(sub)
        assert method=='POST' and resource=='checkouts'
        self.posts.append(deepcopy(data))
        co={'id':'ch_'+str(len(self.posts)),'mode':'test','status':'pending','product':data['product_id'],
            'metadata':deepcopy(data['metadata'])}
        self.values[('checkouts',co['id'])]=deepcopy(co)
        return co

    def complete(self,index=1,*,plan='pro'):
        co=self.values[('checkouts','ch_'+str(index))]
        sid,tid='sub_'+str(index),'tran_'+str(index)
        product_id=TEST_PLANS[plan]['product_id']
        co.update(status='completed',subscription=sid,customer='cust_example',order={'transaction':tid})
        self.values[('subscriptions',sid)]={'id':sid,'mode':'test','product':product_id,
            'customer':'cust_example','status':'active','items':[{'product_id':product_id,'units':1}],
            'last_transaction_id':tid,'current_period_start_date':NOW,'current_period_end_date':NOW+30*86400}
        self.values[('transactions',tid)]={'id':tid,'mode':'test','subscription':sid,'customer':'cust_example',
            'currency':'USD','type':'invoice','status':'paid','amount':TEST_PLANS[plan]['price'],
            'amount_paid':TEST_PLANS[plan]['price'],'tax_amount':0,'period_start':NOW,'period_end':NOW+30*86400}


@pytest.fixture
def sandbox(tmp_path):
    path=str(tmp_path/'account.db')
    connect=lambda:sqlite3.connect(path,timeout=10)
    with connect() as conn:
        conn.execute('CREATE TABLE users(user_id BIGINT PRIMARY KEY,token_balance BIGINT,subscription_until TEXT)')
        conn.execute('INSERT INTO users VALUES (7,100,NULL)')
    api=AccountAPI()
    service=SandboxAccountService(SandboxAccountStore(connect,sqlite=True,clock=lambda:NOW),api)
    return service,connect


def test_account_checkout_uses_test_products_server_metadata_and_verified_session_url(sandbox):
    service,_=sandbox
    result=service.checkout(7,'pro',str(uuid.uuid4()))
    assert result['mode']=='test'
    assert result['checkout_url']=='https://www.creem.io/test/checkout/'+TEST_PLANS['pro']['product_id']+'/ch_1'
    body=service.api.posts[0]
    assert body['success_url']==SANDBOX_RETURN_URL and body['units']==1
    assert body['product_id']==TEST_PLANS['pro']['product_id']!=PLANS['pro']['product_id']
    assert body['metadata']=={'velia_sandbox_order_id':body['request_id']}
    assert set(body)=={'product_id','request_id','success_url','units','metadata'}


def test_upgrade_uses_the_owned_plus_subscription_and_preserves_purchases(sandbox):
    service,connect=sandbox
    for index,plan in enumerate(('pro','plus'),1):
        service.checkout(7,plan,str(uuid.uuid4()))
        service.api.complete(index,plan=plan)
    before=service.account(7)
    assert before['plan_code']=='plus' and before['plus_to_pro']=={'status':'ready'}
    assert before['test_token_balance']==4200
    assert not service.api.upgrades
    with pytest.raises(BillingError,match='test_plus_required'):
        service.upgrade_plus(8)
    result=service.upgrade_plus(7)
    assert result['plan_code']=='pro' and result['plus_to_pro']=={'status':'applied'}
    assert 'sync_error' not in result and result['test_token_balance']==4200
    assert result['purchases']==before['purchases'] and result['real_access_granted'] is False
    assert service.upgrade_plus(7)==result
    assert len(service.api.posts)==2
    assert service.api.upgrades==[('subscriptions/sub_2/upgrade',{
        'product_id':TEST_PLANS['pro']['product_id'],'update_behavior':'proration-charge-immediately'})]
    tx=deepcopy(service.api.values[('transactions','tran_2')])
    tx.update(id='tran_upgrade',amount=1200,amount_paid=1500,status='pending')
    service.api.values[('transactions',tx['id'])]=tx
    service.api.values[('subscriptions','sub_2')]['last_transaction_id']=tx['id']
    assert service.account(7)['plus_to_pro']=={'status':'applied'}
    tx['status']='paid'
    assert service.account(7)['plus_to_pro']=={'status':'applied','paid_cents':1500}
    with connect() as conn:
        assert conn.execute('SELECT token_balance,subscription_until FROM users WHERE user_id=7').fetchone()==(100,None)


def test_ambiguous_upgrade_is_never_retried(sandbox,monkeypatch):
    service,_=sandbox
    service.checkout(7,'plus',str(uuid.uuid4()))
    service.api.complete(1,plan='plus')
    service.account(7)
    attempts=[]
    def ambiguous(*args,**kwargs):
        attempts.append(args)
        raise BillingError('sandbox_request_failed',502)
    monkeypatch.setattr(service.api,'request',ambiguous)
    for _ in range(2):
        account=service.upgrade_plus(7)
        assert account['plan_code']=='plus' and account['plus_to_pro']=={'status':'pending'}
    assert len(attempts)==1


def test_verified_purchase_appears_in_own_test_balance_once_and_preserves_real_account(sandbox):
    service,connect=sandbox
    service.checkout(7,'pro',str(uuid.uuid4()))
    assert service.account(7)['test_token_balance']==0
    service.api.complete()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:service.account(7),range(12)))
    assert all(r['plan_code']=='pro' and r['test_token_balance']==3000 for r in results)
    assert service.account(8)['test_token_balance']==0
    assert service.account(7)['real_access_granted'] is False
    with connect() as conn:
        assert conn.execute('SELECT token_balance,subscription_until FROM users WHERE user_id=7').fetchone()==(100,None)
        assert conn.execute('SELECT COUNT(*) FROM velia_creem_sandbox_receipts').fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='velia_creem_subscriptions'").fetchone()[0]==0


@pytest.mark.parametrize('case',['metadata','mode','customer','underpaid','currency','product','pending','units'])
def test_invalid_or_unpaid_canonical_evidence_cannot_change_test_balance(sandbox,case):
    service,_=sandbox
    service.checkout(7,'pro',str(uuid.uuid4()))
    service.api.complete()
    co=service.api.values[('checkouts','ch_1')]
    tx=service.api.values[('transactions','tran_1')]
    sub=service.api.values[('subscriptions','sub_1')]
    if case=='metadata':co['metadata']['velia_sandbox_order_id']='foreign'
    if case=='mode':tx['mode']='prod'
    if case=='customer':tx['customer']='cust_foreign'
    if case=='underpaid':tx['amount_paid']=1
    if case=='currency':tx['currency']='EUR'
    if case=='product':sub['product']=PLANS['pro']['product_id']
    if case=='pending':tx['status']='pending'
    if case=='units':sub['items'][0]['units']=True
    account=service.account(7)
    assert account['plan_code']=='free' and account['test_token_balance']==0
    assert account['sync_error']=='test_payment_verification_pending'


def test_refund_and_late_paid_response_never_restore_refunded_test_tokens(sandbox):
    service,_=sandbox
    service.checkout(7,'pro',str(uuid.uuid4()))
    service.api.complete()
    assert service.account(7)['test_token_balance']==3000
    tx=service.api.values[('transactions','tran_1')]
    tx['status']='refunded'
    assert service.account(7)['test_token_balance']==0
    tx['status']='paid'
    assert service.account(7)['test_token_balance']==0


def test_refund_uses_original_receipt_allowance_after_catalog_credits_change(sandbox,monkeypatch):
    service,_=sandbox
    service.checkout(7,'pro',str(uuid.uuid4()))
    service.api.complete()
    assert service.account(7)['test_token_balance']==3000
    monkeypatch.setitem(TEST_PLANS['pro'],'credits',9999)
    service.api.values[('transactions','tran_1')]['status']='refunded'
    assert service.account(7)['test_token_balance']==0


def test_max_settings_mismatch_disables_only_max(sandbox):
    service,_=sandbox
    service.api.values[('products',TEST_PLANS['max']['product_id'])]['price']=399
    entries={p['code']:p for p in service.catalog()['plans']}
    assert entries['plus']['enabled'] is True and entries['pro']['enabled'] is True
    assert entries['max']['enabled'] is False


def test_unknown_create_outcome_never_creates_a_second_checkout(sandbox,monkeypatch):
    service,_=sandbox
    original=service.api.request
    def lost(*args,**kwargs):
        original(*args,**kwargs)
        raise BillingError('sandbox_request_failed',502)
    monkeypatch.setattr(service.api,'request',lost)
    with pytest.raises(BillingError):service.checkout(7,'pro',str(uuid.uuid4()))
    with pytest.raises(BillingError,match='checkout_already_pending'):
        service.checkout(7,'pro',str(uuid.uuid4()))
    assert len(service.api.posts)==1


@pytest.mark.parametrize('url',['https://www.creem.io/checkout/prod_x/ch_x',
    'https://www.creem.io/payment/prod_x','https://evil.example/test/checkout/prod_x/ch_x',
    'https://www.creem.io/test/checkout/prod_x/ch_x#fake','https://user@www.creem.io/test/payment/prod_x'])
def test_test_redirect_cannot_fall_back_to_live_or_foreign_origin(url):
    with pytest.raises(BillingError,match='invalid_test_checkout_url'):destination(url)


def test_test_routes_require_owner_and_server_derives_purchase_account():
    async def scenario():
        calls=[]
        class Fake:
            def catalog(self):return {'ok':True,'mode':'test'}
            def account(self,user):calls.append(('account',user));return {'ok':True,'mode':'test'}
            def checkout(self,*args):calls.append(('checkout',args));return {'ok':True,'mode':'test'}
            def upgrade_plus(self,user):calls.append(('upgrade_plus',user));return {'ok':True,'mode':'test'}
        app=web.Application()
        setup_creem_routes(app,require_auth=lambda r:{'user_id':int(r.headers['X-Test-Owner'])} if r.headers.get('X-Test-Owner') else None,
            api_available=lambda:True,billing=Fake(),sandbox=Fake(),sandbox_allowed=lambda user:user==7)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/economy/creem-test/account')
            assert (await client.get(url)).status==401
            assert (await client.get(url,headers={'X-Test-Owner':'8'})).status==403
            assert calls==[]
            assert (await client.get(url,headers={'X-Test-Owner':'7'})).status==200
            assert calls.pop()==('account',7)
            checkout=server.make_url('/mobile-api/v1/economy/creem-test/checkout')
            assert (await client.post(checkout,headers={'X-Test-Owner':'7'},json={'plan':'pro','attempt':'a','user_id':8})).status==400
            assert calls==[]
            assert (await client.post(checkout,headers={'X-Test-Owner':'7'},json={'plan':'pro','attempt':'a'})).status==200
            assert calls.pop()==('checkout',(7,'pro','a'))
            upgrade=server.make_url('/mobile-api/v1/economy/creem-test/upgrade-plus')
            assert (await client.get(upgrade,headers={'X-Test-Owner':'7'})).status==404
            assert (await client.post(upgrade,headers={'X-Test-Owner':'8'},json={})).status==403
            assert (await client.post(upgrade,headers={'X-Test-Owner':'7'},json={'user_id':8})).status==400
            assert not calls
            assert (await client.post(upgrade,headers={'X-Test-Owner':'7'},json={})).status==200
            assert calls.pop()==('upgrade_plus',7)
    asyncio.run(scenario())


def test_owner_test_capability_is_not_cached_into_other_users_live_catalog():
    async def scenario():
        shared={'ok':True,'enabled':True,'mode':'prod'}
        class Fake:
            def catalog(self):return shared
        app=web.Application()
        setup_creem_routes(app,require_auth=lambda r:{'user_id':int(r.headers['X-Test-Owner'])},
            api_available=lambda:True,billing=Fake(),sandbox=Fake(),sandbox_allowed=lambda user:user==7)
        async with TestServer(app) as server,ClientSession() as client:
            url=server.make_url('/mobile-api/v1/economy/creem/catalog')
            async with client.get(url,headers={'X-Test-Owner':'7'}) as response:
                assert (await response.json())['can_test_billing'] is True
            async with client.get(url,headers={'X-Test-Owner':'8'}) as response:
                assert (await response.json())['can_test_billing'] is False
            assert 'can_test_billing' not in shared
    asyncio.run(scenario())
