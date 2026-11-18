"""Owner-only account-bound test checkout and isolated purchase preview.

All payment evidence comes from canonical test API reads. Browser redirects and
static product links cannot grant anything. Writes are isolated to sandbox
tables and the Creem test API.
Account refresh reconciles saved orders, independently of webhook delivery.
"""
import math
import os
import re
import time
import uuid
from urllib.parse import urlparse

from services.velia_creem_billing import BillingError, BillingStore, PLANS, RANK, identifier, require, timestamp, iso
from services.velia_creem_plan_change import (APPROVED_SANDBOX_EXPERIMENT, SANDBOX_RETURN_URL, SandboxAPI, _product, snapshot)

TEST_PLANS = {code:{**spec,'product_id':APPROVED_SANDBOX_EXPERIMENT['products'][code]}
              for code,spec in PLANS.items()}


def test_owner(user):
    try:
        owner = int(os.getenv('ADMIN_ID','0'))
        return owner > 0 and int(user) == owner
    except (ValueError,TypeError):
        return False


def test_ready():
    key = os.getenv('VELIA_CREEM_TEST_API_KEY','')
    return key.startswith('creem_test_') and len(key) >= 20


def test_checkout_url(value):
    require(isinstance(value,str) and len(value)<2048 and not re.search(r'[\x00-\x20\\]',value),
            'invalid_test_checkout_url',502)
    try:
        url=urlparse(value)
        valid=(url.scheme=='https' and url.hostname in {'www.creem.io','creem.io'}
               and not url.username and not url.password and not url.fragment and url.port in (None,443)
               and not url.params and re.fullmatch(r'/test/(?:checkout|payment)/[A-Za-z0-9_/-]+',url.path))
    except ValueError:
        valid=False
    require(bool(valid),'invalid_test_checkout_url',502)
    return value


class SandboxAccountStore:
    def __init__(self, connect=None, *, sqlite=False, clock=time.time):
        self.db=BillingStore(connect,sqlite=sqlite)
        self.clock=clock

    def initialize(self):
        with self.db.transaction() as cur:
            if not self.db.sqlite:cur.execute('SELECT pg_advisory_xact_lock(1450731744)')
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_sandbox_orders (
                id TEXT PRIMARY KEY,user_id BIGINT NOT NULL,plan TEXT NOT NULL,
                attempt TEXT NOT NULL,state TEXT NOT NULL,created_at BIGINT NOT NULL,
                checkout_id TEXT UNIQUE,checkout_url TEXT,UNIQUE(user_id,attempt))''')
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_sandbox_receipts (
                transaction_id TEXT PRIMARY KEY,order_id TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL,plan TEXT NOT NULL,credits BIGINT NOT NULL,
                revoked BIGINT NOT NULL,period_start BIGINT NOT NULL,period_end BIGINT NOT NULL)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_sandbox_upgrades (
                user_id BIGINT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE,
                subscription_id TEXT NOT NULL UNIQUE, previous_transaction TEXT NOT NULL,
                source_period_end BIGINT NOT NULL)''')

    def lock(self,cur,user):
        if not self.db.sqlite:
            self.db.execute(cur,'SELECT pg_advisory_xact_lock(hashtextextended(?,0))',('velia:sandbox:account:'+str(user),))

    def reserve(self,user,plan,attempt):
        with self.db.transaction() as cur:
            self.lock(cur,user)
            self.db.execute(cur,'SELECT * FROM velia_creem_sandbox_orders WHERE user_id=? AND attempt=?',(user,attempt))
            row=self.db.row(cur)
            if row:
                require(row['plan']==plan,'billing_attempt_conflict',409)
                return row,False
            self.db.execute(cur,'SELECT * FROM velia_creem_sandbox_orders WHERE user_id=? AND plan=? ORDER BY created_at DESC',(user,plan))
            row=self.db.row(cur)
            if row:return row,False
            row={'id':str(uuid.uuid4()),'user_id':user,'plan':plan,'attempt':attempt,'state':'creating',
                 'created_at':int(self.clock()),'checkout_id':None,'checkout_url':None}
            self.db.execute(cur,'''INSERT INTO velia_creem_sandbox_orders(id,user_id,plan,attempt,state,created_at)
                VALUES (?,?,?,?,?,?)''',tuple(row[k] for k in ('id','user_id','plan','attempt','state','created_at')))
            return row,True

    def remember(self,order,checkout_id,url=None):
        with self.db.transaction() as cur:
            self.lock(cur,order['user_id'])
            self.db.execute(cur,'''UPDATE velia_creem_sandbox_orders SET checkout_id=?,
                checkout_url=COALESCE(?,checkout_url),state=CASE WHEN state='paid' THEN 'paid'
                WHEN ? IS NOT NULL THEN 'open' ELSE state END WHERE id=? AND user_id=?
                AND (checkout_id IS NULL OR checkout_id=?)''',
                (checkout_id,url,url,order['id'],order['user_id'],checkout_id))
            require(cur.rowcount==1,'billing_checkout_conflict',409)

    def uncertain(self,order):
        with self.db.transaction() as cur:
            self.db.execute(cur,"UPDATE velia_creem_sandbox_orders SET state='uncertain' WHERE id=? AND state='creating'",(order['id'],))

    def orders(self,user):
        with self.db.transaction() as cur:
            self.db.execute(cur,'''SELECT o.*,r.transaction_id FROM velia_creem_sandbox_orders o
                LEFT JOIN velia_creem_sandbox_receipts r ON r.order_id=o.id
                WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT 3''',(user,))
            return [dict(zip([d[0] for d in cur.description],row)) for row in cur.fetchall()]

    def settle(self,order,tx,start,end):
        user=order['user_id']
        credits=TEST_PLANS[order['plan']]['credits']
        def refunded(allowance):
            return (allowance if tx['status'] in {'refunded','chargeback'} else
                    math.ceil(allowance*tx['refunded_amount']/tx['amount_paid']) if tx['status']=='partially_refunded' else 0)
        revoked=refunded(credits)
        with self.db.transaction() as cur:
            self.lock(cur,user)
            self.db.execute(cur,'SELECT * FROM velia_creem_sandbox_receipts WHERE transaction_id=? OR order_id=?',(tx['id'],order['id']))
            previous=self.db.row(cur)
            if previous:
                require(previous['user_id']==user and previous['order_id']==order['id']
                        and previous['transaction_id']==tx['id'] and previous['plan']==order['plan']
                        and previous['period_start']==start and previous['period_end']==end,'billing_charge_conflict',409)
                self.db.execute(cur,'UPDATE velia_creem_sandbox_receipts SET revoked=? WHERE transaction_id=?',
                                (max(previous['revoked'],refunded(previous['credits'])),tx['id']))
            else:
                self.db.execute(cur,'''INSERT INTO velia_creem_sandbox_receipts
                    (transaction_id,order_id,user_id,plan,credits,revoked,period_start,period_end)
                    VALUES (?,?,?,?,?,?,?,?)''',(tx['id'],order['id'],user,order['plan'],credits,revoked,start,end))
            self.db.execute(cur,"UPDATE velia_creem_sandbox_orders SET state='paid' WHERE id=? AND user_id=?",(order['id'],user))

    def upgrade_for(self,user):
        with self.db.transaction() as cur:
            self.db.execute(cur,'SELECT * FROM velia_creem_sandbox_upgrades WHERE user_id=?',(user,))
            return self.db.row(cur)

    def reserve_upgrade(self,user,order_id,subscription_id,previous_transaction,source_period_end):
        with self.db.transaction() as cur:
            self.lock(cur,user)
            self.db.execute(cur,'''INSERT INTO velia_creem_sandbox_upgrades
                (user_id,order_id,subscription_id,previous_transaction,source_period_end) VALUES (?,?,?,?,?)
                ON CONFLICT DO NOTHING''',(user,order_id,subscription_id,previous_transaction,source_period_end))
            return cur.rowcount==1

    def active_plus(self,user):
        with self.db.transaction() as cur:
            self.db.execute(cur,'''SELECT order_id FROM velia_creem_sandbox_receipts WHERE user_id=?
                AND plan='plus' AND revoked=0 AND period_end>? ORDER BY period_end DESC LIMIT 1''',
                (user,int(self.clock())))
            row=self.db.row(cur)
            return row['order_id'] if row else None

    def account(self,user,*,preferred=None):
        with self.db.transaction() as cur:
            self.db.execute(cur,'SELECT plan,credits,revoked,period_end FROM velia_creem_sandbox_receipts WHERE user_id=?',(user,))
            rows=[dict(zip([d[0] for d in cur.description],row)) for row in cur.fetchall()]
        active=[r for r in rows if r['revoked']<r['credits'] and r['period_end']>self.clock()]
        selected=next((r for r in active if r['plan']==preferred),None)
        if selected is None and active:
            selected=max(active,key=lambda r:RANK[r['plan']])
        return {'ok':True,'mode':'test','preview_only':True,'real_access_granted':False,
                'plan_code':selected['plan'] if selected else 'free',
                'subscription_until':iso(selected['period_end']) if selected else None,
                'test_token_balance':sum(r['credits']-r['revoked'] for r in rows),
                'purchases':[{'plan':r['plan'],'credits':r['credits']-r['revoked'],
                              'subscription_until':iso(r['period_end'])} for r in rows]}


class SandboxAccountService:
    def __init__(self,store=None,api=None):
        self.store=store or SandboxAccountStore()
        self.api=api or SandboxAPI(os.getenv('VELIA_CREEM_TEST_API_KEY',''))

    def product(self,plan):
        spec=TEST_PLANS[plan]
        product=_product(self.api,spec['product_id'])
        require(product['price']==spec['price'],'billing_product_mismatch',503)
        return product

    def catalog(self):
        plans=[]
        for code,spec in TEST_PLANS.items():
            entry={'code':code,'name':spec['name'],'price':spec['price']/100,
                   'monthly_credits':spec['credits'],'enabled':False}
            try:
                entry['tax_mode']=self.product(code)['tax_mode']
                entry['enabled']=True
            except BillingError:
                entry['unavailable_reason']='test_product_not_ready'
            plans.append(entry)
        return {'ok':True,'mode':'test','preview_only':True,'plans':plans}

    def bound_checkout(self,order,checkout_id):
        co=self.api.get('checkouts','checkout_id',checkout_id)
        require(co.get('mode')=='test' and identifier(co,'ch_')==checkout_id
                and identifier(co.get('product'),'prod_')==TEST_PLANS[order['plan']]['product_id']
                and isinstance(co.get('metadata'),dict)
                and co['metadata'].get('velia_sandbox_order_id')==order['id'],'billing_owner_conflict',409)
        return co

    def checkout(self,user,plan,attempt):
        require(plan in TEST_PLANS,'invalid_plan')
        try:require(str(uuid.UUID(attempt))==attempt,'invalid_billing_attempt')
        except (ValueError,TypeError,AttributeError):raise BillingError('invalid_billing_attempt') from None
        self.product(plan)
        self.store.initialize()
        order,fresh=self.store.reserve(user,plan,attempt)
        require(order['state']!='paid','test_purchase_already_completed',409)
        if not fresh and order['checkout_url']:
            return {'ok':True,'mode':'test','checkout_url':test_checkout_url(order['checkout_url'])}
        require(fresh or bool(order['checkout_id']),'checkout_already_pending',409)
        try:
            created=None
            if fresh:
                created=self.api.request('POST','checkouts',data={'product_id':TEST_PLANS[plan]['product_id'],
                    'request_id':order['id'],'success_url':SANDBOX_RETURN_URL,'units':1,
                    'metadata':{'velia_sandbox_order_id':order['id']}})
                require(created.get('mode')=='test'
                        and identifier(created.get('product'),'prod_')==TEST_PLANS[plan]['product_id'],'billing_wrong_environment',502)
                checkout_id=identifier(created,'ch_')
                self.store.remember(order,checkout_id)
            else:checkout_id=order['checkout_id']
            co=self.bound_checkout(order,checkout_id)
            require(co.get('status') in {'pending','processing'},'billing_checkout_completed',409)
            # Open the exact independently retrieved account-bound session.
            # A static /test/payment/product link would create another checkout
            # without this order's metadata and cannot be used here.
            url='https://www.creem.io/test/checkout/'+TEST_PLANS[plan]['product_id']+'/'+checkout_id
            url=test_checkout_url(url)
            self.store.remember(order,checkout_id,url)
            return {'ok':True,'mode':'test','checkout_url':url}
        except BaseException:
            self.store.uncertain(order)
            raise

    def reconcile(self,order,upgrade=None):
        if not order['checkout_id']:return
        co=self.bound_checkout(order,order['checkout_id'])
        if co.get('status')!='completed':return
        sub_id=identifier(co.get('subscription'),'sub_')
        sub=self.api.get('subscriptions','subscription_id',sub_id)
        product_id=TEST_PLANS[order['plan']]['product_id']
        current_product=identifier(sub.get('product'),'prod_')
        upgraded=bool(upgrade and order['plan']=='plus' and upgrade['order_id']==order['id']
                      and upgrade['subscription_id']==sub_id and current_product==TEST_PLANS['pro']['product_id'])
        require(sub.get('mode')=='test' and identifier(sub,'sub_')==sub_id
                and (current_product==product_id or upgraded),'billing_owner_conflict',409)
        items=sub.get('items')
        require(isinstance(items,list) and len(items)==1 and items[0].get('product_id')==current_product
                and type(items[0].get('units')) is int and items[0]['units']==1,'billing_product_mismatch',409)
        customer=identifier(sub.get('customer'),'cust_')
        require(identifier(co.get('customer'),'cust_')==customer,'billing_owner_conflict',409)
        tx_id=order.get('transaction_id') or (co.get('order') or {}).get('transaction') or sub.get('last_transaction_id')
        tx_id=identifier(tx_id,'tran_')
        tx=self.api.get('transactions','transaction_id',tx_id)
        require(tx.get('mode')=='test' and identifier(tx,'tran_')==tx_id
                and identifier(tx.get('subscription'),'sub_')==sub_id
                and identifier(tx.get('customer'),'cust_')==customer,'billing_owner_conflict',409)
        price=TEST_PLANS[order['plan']]['price']
        amount,tax=tx.get('amount'),tx.get('tax_amount')
        require((tax is None or (type(tax) is int and tax>=0))
                and type(amount) is int and amount>0 and (amount==price or
                (type(tax) is int and tax>=0 and amount+tax==price))
                and type(tx.get('amount_paid')) is int and tx['amount_paid']>=price
                and tx.get('currency')=='USD' and tx.get('type')=='invoice','billing_amount_mismatch',409)
        require(tx.get('status') in {'paid','partially_refunded','refunded','chargeback'},'billing_payment_not_paid',409)
        if tx['status']=='partially_refunded':
            require(type(tx.get('refunded_amount')) is int and 0<tx['refunded_amount']<=tx['amount_paid'],'billing_amount_mismatch',409)
        start,end=timestamp(tx.get('period_start')),timestamp(tx.get('period_end'))
        require(0<end-start<=35*86400,'invalid_billing_period')
        self.store.settle(order,tx,start,end)
        return sub_id if sub.get('status')=='active' and tx['status']=='paid' else None

    def upgrade_plus(self,user):
        self.store.initialize()
        if self.store.upgrade_for(user):
            return self.account(user)
        orders=self.store.orders(user)
        source=self.store.active_plus(user)
        order=next((o for o in orders if o['id']==source and o['plan']=='plus'),None)
        require(order is not None,'test_plus_required',409)
        sub_id=self.reconcile(order)
        require(sub_id and self.store.active_plus(user)==source,'test_plus_required',409)
        before=snapshot(self.api,sub_id)
        invoice=before.get('last_invoice',{})
        require(before['status']=='active' and before['product_id']==TEST_PLANS['plus']['product_id']
                and before['price_cents']==TEST_PLANS['plus']['price']
                and before['period_end']>self.store.clock()
                and invoice.get('status')=='paid','test_plus_required',409)
        product=self.product('pro')
        if self.store.reserve_upgrade(user,source,sub_id,invoice['transaction_id'],before['period_end']):
            try:
                self.api.request('POST','subscriptions/'+sub_id+'/upgrade',
                                 data={'product_id':product['id'],'update_behavior':'proration-charge-immediately'})
            except BillingError:
                # The request may have been applied. Account reads reconcile it.
                pass
        return self.account(user)

    def account(self,user):
        self.store.initialize()
        upgrade=self.store.upgrade_for(user)
        errors=[]
        verified={}
        for order in self.store.orders(user):
            try:verified[order['id']]=self.reconcile(order,upgrade)
            except BillingError as error:errors.append(error.code)
        source=self.store.active_plus(user)
        current='plus' if source and verified.get(source) else None
        change={'status':'ready'} if current=='plus' else None
        if upgrade:
            change={'status':'pending'}
            if verified.get(upgrade['order_id'])==upgrade['subscription_id']:
                try:
                    latest=snapshot(self.api,upgrade['subscription_id'])
                    if latest['product_id']==TEST_PLANS['pro']['product_id'] and latest['status']=='active':
                        current='pro'
                        change={'status':'applied'}
                        invoice=latest.get('last_invoice',{})
                        if (invoice.get('status')=='paid' and invoice['type']=='invoice'
                                and invoice['transaction_id']!=upgrade['previous_transaction']
                                and invoice['period_end']==latest['period_end']==upgrade['source_period_end']
                                and invoice['period_start'] is not None
                                and latest['period_start']<=invoice['period_start']<latest['period_end']):
                            change['paid_cents']=invoice['paid_cents']
                except BillingError as error:errors.append(error.code)
        result=self.store.account(user,preferred=current)
        if change:
            result['plus_to_pro']=change
            if current=='pro':
                result['plan_code']='pro'
                result['subscription_until']=iso(latest['period_end'])
        if errors:result['sync_error']='test_payment_verification_pending'
        return result
