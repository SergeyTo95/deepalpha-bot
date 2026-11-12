"""Account-authority Creem subscriptions. No browser/redirect can grant credits."""
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

PLANS = {
    'plus': {'name': 'Plus', 'price': 1499, 'credits': 1200, 'product_id': 'prod_3YUMifrIso3U0JEvAJEl3o'},
    'pro': {'name': 'Pro', 'price': 2999, 'credits': 3000, 'product_id': 'prod_1E2KmHCJDzxC3nMraYc1iB'},
    'max': {'name': 'Max', 'price': 5999, 'credits': 6500, 'product_id': 'prod_QM6adegRzqFXCFL6lyO6a'},
}
RANK = {'free': 0, 'plus': 1, 'pro': 2, 'max': 3}
MAX_BODY = 256 * 1024
RETURN_URL = 'https://velyoncore.ai/thanks'
WEBHOOK_URL = 'https://velyoncore.ai/web-api/v1/billing/creem/webhook'
REQUIRED_EVENTS = {'checkout.completed','subscription.active','subscription.paid','subscription.canceled',
    'subscription.scheduled_cancel','subscription.expired','subscription.unpaid','subscription.update',
    'subscription.past_due','subscription.paused','subscription.trialing','refund.created','dispute.created'}
ID = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')


class BillingError(Exception):
    def __init__(self, code, status=400, *, provider_status=None):
        self.code, self.status, self.provider_status = code, status, provider_status
        super().__init__(code)


def require(condition, code='billing_verification_failed', status=400):
    if not condition:
        raise BillingError(code, status)


def identifier(value, prefix):
    value = value.get('id') if isinstance(value, dict) else value
    require(isinstance(value, str) and value.startswith(prefix) and ID.fullmatch(value), 'invalid_provider_reference')
    return value


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        require(math.isfinite(value) and value > 0, 'invalid_billing_period')
        return int(value / 1000 if value > 10**11 else value)
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return int(date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp())
    except (ValueError, TypeError, OverflowError):
        raise BillingError('invalid_billing_period') from None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def epoch_or_zero(value):
    return timestamp(value) if value else 0


def checkout_url(value):
    require(isinstance(value, str) and len(value) < 2048, 'invalid_checkout_url', 502)
    url = urlparse(value)
    require(url.scheme == 'https' and not url.username and not url.password and not url.fragment
            and url.port in (None, 443) and
            ((url.hostname in {'www.creem.io', 'creem.io'} and url.path.startswith('/checkout/'))
             or (url.hostname == 'checkout.creem.io' and url.path.startswith('/ch_'))), 'invalid_checkout_url', 502)
    return value


class CreemAPI:
    def __init__(self, key):
        self.key = key

    def request(self, method, resource, *, params=None, data=None):
        import requests
        try:
            with requests.request(method, 'https://api.creem.io/v1/' + resource,
                    params=params, json=data, headers={'x-api-key': self.key},
                    timeout=(3, 5), allow_redirects=False, stream=True) as response:
                if response.status_code not in (200, 201):
                    raise BillingError('billing_provider_unavailable', 502, provider_status=response.status_code)
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    require(len(raw) <= 512 * 1024, 'billing_provider_unavailable', 502)
                result = json.loads(raw)
                require(isinstance(result, dict), 'billing_provider_unavailable', 502)
                return result
        except BillingError:
            raise
        except Exception:
            raise BillingError('billing_provider_unavailable', 502) from None

    def get(self, resource, key, value):
        return self.request('GET', resource, params={key: value})


class BillingStore:
    def __init__(self, connect=None, *, sqlite=False, clock=time.time):
        if connect is None:
            from db.database import get_connection
            connect = get_connection
        self.connect, self.sqlite, self.clock = connect, sqlite, clock
        self.ready, self.schema_lock = False, threading.Lock()

    def execute(self, cursor, sql, args=()):
        cursor.execute(sql.replace('?', '%s') if not self.sqlite else sql, args)

    @staticmethod
    def row(cur):
        value = cur.fetchone()
        return dict(zip([item[0] for item in cur.description], value)) if value is not None else None

    @contextmanager
    def transaction(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            if self.sqlite:
                cur.execute('BEGIN IMMEDIATE')
            yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def initialize(self):
        with self.schema_lock:
            if self.ready:
                return
            with self.transaction() as cur:
                if not self.sqlite:
                    cur.execute('SELECT pg_advisory_xact_lock(1450731741)')
                cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_orders (
                    id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, attempt TEXT NOT NULL,
                    plan TEXT NOT NULL, state TEXT NOT NULL, created_at BIGINT NOT NULL,
                    checkout_id TEXT UNIQUE, checkout_url TEXT,
                    UNIQUE(user_id,attempt))''')
                cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_subscriptions (
                    id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, order_id TEXT NOT NULL,
                    plan TEXT NOT NULL, customer_id TEXT NOT NULL, status TEXT NOT NULL,
                    period_end BIGINT NOT NULL, version BIGINT NOT NULL)''')
                cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_charges (
                    id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, user_id BIGINT NOT NULL,
                    period_start BIGINT NOT NULL, period_end BIGINT NOT NULL,
                    credits BIGINT NOT NULL, revoked BIGINT NOT NULL DEFAULT 0,
                    unrecovered BIGINT NOT NULL DEFAULT 0,
                    UNIQUE(subscription_id,period_start,period_end))''')
                cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_access (
                    user_id BIGINT PRIMARY KEY, baseline_until BIGINT NOT NULL,
                    managed_until BIGINT NOT NULL)''')
                cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_deliveries (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, received_at BIGINT NOT NULL)''')
            self.ready = True

    def lock_user(self, cur, user):
        self.execute(cur, 'SELECT user_id,token_balance,subscription_until FROM users WHERE user_id=?' +
                     ('' if self.sqlite else ' FOR UPDATE'), (user,))
        row = self.row(cur)
        require(row is not None, 'user_not_found', 404)
        return row

    def order(self, order_id):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_creem_orders WHERE id=?', (order_id,))
            return self.row(cur)

    def reserve(self, user, plan, attempt):
        with self.transaction() as cur:
            self.lock_user(cur, user)
            self.execute(cur, 'SELECT * FROM velia_creem_orders WHERE user_id=? AND attempt=?', (user, attempt))
            previous = self.row(cur)
            if previous:
                require(previous['plan'] == plan, 'billing_attempt_conflict', 409)
                return previous, False
            self.execute(cur, "SELECT id FROM velia_creem_subscriptions WHERE user_id=? AND status<>'canceled'", (user,))
            require(cur.fetchone() is None, 'subscription_exists', 409)
            self.execute(cur, "SELECT * FROM velia_creem_orders WHERE user_id=? AND state IN ('creating','open','uncertain') ORDER BY created_at DESC", (user,))
            pending = self.row(cur)
            if pending:
                require(pending['plan'] == plan and pending['state'] == 'open', 'checkout_already_pending', 409)
                return pending, False
            doc = {'id': str(uuid.uuid4()), 'user_id': user, 'attempt': attempt, 'plan': plan,
                   'state': 'creating', 'created_at': int(self.clock()), 'checkout_id': None, 'checkout_url': None}
            self.execute(cur, 'INSERT INTO velia_creem_orders(id,user_id,attempt,plan,state,created_at) VALUES (?,?,?,?,?,?)',
                         tuple(doc[k] for k in ('id','user_id','attempt','plan','state','created_at')))
            return doc, True

    def checkout_created(self, order, provider_id, url):
        with self.transaction() as cur:
            self.lock_user(cur, order['user_id'])
            self.execute(cur, "UPDATE velia_creem_orders SET checkout_id=?,checkout_url=?,state=CASE WHEN state='paid' THEN 'paid' ELSE 'open' END WHERE id=? AND (checkout_id IS NULL OR checkout_id=?)", (provider_id, url, order['id'], provider_id))
            require(cur.rowcount == 1, 'billing_checkout_conflict', 409)

    def uncertain(self, order):
        with self.transaction() as cur:
            self.execute(cur, "UPDATE velia_creem_orders SET state='uncertain' WHERE id=? AND state='creating'", (order['id'],))

    def subscription(self, sub_id):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT * FROM velia_creem_subscriptions WHERE id=?', (sub_id,))
            return self.row(cur)

    def effective(self, cur, user):
        self.execute(cur, '''SELECT s.id,s.plan,s.status,s.period_end,MAX(c.period_end) AS until
            FROM velia_creem_subscriptions s JOIN velia_creem_charges c ON c.subscription_id=s.id
            WHERE s.user_id=? AND s.status NOT IN ('canceled','paused','trialing')
            AND c.revoked<c.credits AND c.period_end>? AND s.period_end>?
            GROUP BY s.id,s.plan,s.status,s.period_end''', (user, int(self.clock()), int(self.clock())))
        rows = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
        for row in rows:
            row['until'] = min(row['until'], row['period_end'])
        return max(rows, key=lambda r: (RANK.get(r['plan'], 0), r['until'])) if rows else None

    def state(self, user):
        with self.transaction() as cur:
            active = self.effective(cur, user)
            self.execute(cur, 'SELECT id,status FROM velia_creem_subscriptions WHERE user_id=? ORDER BY version DESC', (user,))
            latest = self.row(cur)
            return {'plan_code': active['plan'] if active else 'free',
                    'subscription_until': iso(active['until']) if active else None,
                    'status': active['status'] if active else (latest['status'] if latest else None),
                    'has_subscription': bool(latest),
                    'has_active_subscription': bool(latest and latest['status'] != 'canceled')}

    def portal_customer(self, user):
        with self.transaction() as cur:
            self.execute(cur, 'SELECT customer_id FROM velia_creem_subscriptions WHERE user_id=? ORDER BY version DESC', (user,))
            row = cur.fetchone()
            require(row is not None, 'subscription_not_found', 404)
            return row[0]

    def refresh_access(self, cur, user, account):
        self.execute(cur, 'SELECT * FROM velia_creem_access WHERE user_id=?', (user,))
        context = self.row(cur)
        current = epoch_or_zero(account['subscription_until'])
        baseline = context['baseline_until'] if context else current
        if context and current != context['managed_until']:
            baseline = current  # Another authority changed its entitlement.
        active = self.effective(cur, user)
        desired = max(baseline, active['until'] if active else 0)
        self.execute(cur, 'UPDATE users SET subscription_until=?,updated_at=? WHERE user_id=?', (iso(desired), iso(int(self.clock())), user))
        self.execute(cur, '''INSERT INTO velia_creem_access(user_id,baseline_until,managed_until) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET baseline_until=excluded.baseline_until,managed_until=excluded.managed_until''', (user, baseline, desired))

    def apply(self, *, order, sub, transaction=None, refund=False, event_id, digest):
        user, sub_id = order['user_id'], identifier(sub, 'sub_')
        with self.transaction() as cur:
            account = self.lock_user(cur, user)
            self.execute(cur, 'SELECT digest FROM velia_creem_deliveries WHERE id=?', (event_id,))
            delivered = cur.fetchone()
            if delivered:
                require(delivered[0] == digest, 'billing_event_conflict', 409)
                return {'ok': True, 'duplicate': True}
            self.execute(cur, 'SELECT * FROM velia_creem_subscriptions WHERE id=?', (sub_id,))
            previous = self.row(cur)
            require(not previous or (previous['user_id'] == user and previous['order_id'] == order['id'] and previous['plan'] == order['plan']), 'billing_owner_conflict', 409)
            customer = identifier(sub.get('customer'), 'cust_')
            require(not previous or previous['customer_id'] == customer, 'billing_owner_conflict', 409)
            version, end = timestamp(sub.get('updated_at')), timestamp(sub.get('current_period_end_date'))
            if not previous or version >= previous['version']:
                self.execute(cur, '''INSERT INTO velia_creem_subscriptions(id,user_id,order_id,plan,customer_id,status,period_end,version)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,period_end=excluded.period_end,version=excluded.version''',
                    (sub_id, user, order['id'], order['plan'], customer, sub['status'], end, version))
            granted = 0
            if transaction is not None:
                tx_id, start, end = transaction['id'], transaction['_start'], transaction['_end']
                self.execute(cur, 'SELECT * FROM velia_creem_charges WHERE id=?', (tx_id,))
                charge = self.row(cur)
                require(not charge or (charge['user_id'] == user and charge['subscription_id'] == sub_id and charge['period_start'] == start and charge['period_end'] == end), 'billing_charge_conflict', 409)
                if charge is None:
                    # A refund can arrive before the paid event. Keep a tombstone;
                    # never issue fresh credits for already refunded transactions.
                    credits = PLANS[order['plan']]['credits']
                    revoked = (credits if transaction['status'] in {'refunded','chargeback'} else
                               math.ceil(credits * transaction['refunded_amount'] / transaction['amount_paid'])
                               if transaction['status'] == 'partially_refunded' else 0)
                    self.execute(cur, '''INSERT INTO velia_creem_charges(id,subscription_id,user_id,period_start,period_end,credits,revoked)
                        VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING''', (tx_id, sub_id, user, start, end, credits, revoked))
                    if cur.rowcount == 1 and revoked < credits:
                        granted = credits - revoked
                        self.execute(cur, 'UPDATE users SET token_balance=COALESCE(token_balance,0)+?,updated_at=? WHERE user_id=?', (granted, iso(int(self.clock())), user))
                elif refund:
                    target = charge['credits'] if transaction['status'] in {'refunded','chargeback'} else math.ceil(charge['credits'] * transaction['refunded_amount'] / transaction['amount_paid'])
                    target = min(charge['credits'], max(charge['revoked'], target))
                    delta = target - charge['revoked']
                    recovered = min(max(0, int(account['token_balance'] or 0)), delta)
                    self.execute(cur, 'UPDATE users SET token_balance=COALESCE(token_balance,0)-?,updated_at=? WHERE user_id=?', (recovered, iso(int(self.clock())), user))
                    self.execute(cur, 'UPDATE velia_creem_charges SET revoked=?,unrecovered=unrecovered+? WHERE id=?', (target, delta-recovered, tx_id))
                if transaction['status'] == 'paid':
                    self.execute(cur, "UPDATE velia_creem_orders SET state='paid' WHERE id=?", (order['id'],))
            self.refresh_access(cur, user, account)
            self.execute(cur, 'INSERT INTO velia_creem_deliveries(id,digest,received_at) VALUES (?,?,?)', (event_id, digest, int(self.clock())))
            return {'ok': True, 'credits_granted': granted}


def config_ready():
    return (os.getenv('VELIA_CREEM_ENABLED', '').lower() == 'true'
            and len(os.getenv('VELIA_CREEM_API_KEY', '')) >= 16
            and not os.getenv('VELIA_CREEM_API_KEY', '').startswith('creem_test_')
            and len(os.getenv('VELIA_CREEM_WEBHOOK_SECRET', '')) >= 16)


class BillingService:
    def __init__(self, store, api, *, ready=config_ready):
        self.store, self.api, self.ready = store, api, ready
        self.catalog_cache = None

    def verify_hook(self):
        # Read-only preflight: do not enable purchasing against a missing,
        # disabled or incorrectly signed delivery endpoint.
        expected = os.getenv('VELIA_CREEM_WEBHOOK_SECRET', '')
        for page in range(1, 6):
            result = self.api.request('GET', 'webhooks', params={'page_number':page,'page_size':50})
            require(isinstance(result.get('items'), list), 'billing_webhook_not_ready', 503)
            for hook in result['items']:
                if hook.get('url') != WEBHOOK_URL:
                    continue
                events = hook.get('events')
                require(hook.get('mode') == 'prod' and hook.get('status') == 'enabled'
                    and hook.get('delivery_mode') == 'http' and isinstance(events, list)
                    and (not events or REQUIRED_EVENTS <= set(events)), 'billing_webhook_not_ready', 503)
                hook_id = identifier(hook, 'wh_')
                actual = self.api.request('GET', 'webhooks/' + hook_id + '/secret').get('secret')
                require(isinstance(actual, str) and bool(expected) and hmac.compare_digest(actual, expected), 'billing_webhook_not_ready', 503)
                return
            if not (result.get('pagination') or {}).get('next_page'):
                break
        raise BillingError('billing_webhook_not_ready', 503)

    def product(self, plan):
        spec = PLANS[plan]
        product = self.api.get('products', 'product_id', spec['product_id'])
        require(product.get('mode') == 'prod' and product.get('id') == spec['product_id']
            and type(product.get('price')) is int and product['price'] == spec['price']
            and product.get('currency') == 'USD' and product.get('billing_type') == 'recurring'
            and product.get('billing_period') == 'every-month' and product.get('status') == 'active'
            and product.get('tax_mode') == 'exclusive', 'billing_product_mismatch', 503)
        return product

    def catalog(self):
        ready = bool(self.ready())
        if ready and self.catalog_cache and self.catalog_cache[0] > time.monotonic():
            return self.catalog_cache[1]
        reason = None if ready else 'billing_not_configured'
        if ready:
            try:
                self.store.initialize()
                self.verify_hook()
                for plan in PLANS:
                    self.product(plan)
            except BillingError as error:
                ready = False
                reason = ('billing_provider_credentials' if error.provider_status in (401,403) else error.code)
            except Exception:
                ready = False
                reason = 'billing_temporarily_unavailable'
        if reason:
            logging.getLogger(__name__).warning('Creem readiness unavailable code=%s', reason)
        result = {'ok': True, 'enabled': ready, 'mode': 'prod', 'plans': [
            {'code': key, 'name': p['name'], 'price': p['price']/100,
             'monthly_credits': p['credits'], 'currency': 'USD'} for key, p in PLANS.items()]}
        if reason:
            result['unavailable_reason'] = reason
        self.catalog_cache = (time.monotonic()+30, result)
        return result

    def account(self, user):
        self.store.initialize()
        state = self.store.state(user)
        return {'ok': True, **state, 'can_manage': bool(state['has_subscription'] and (self.ready() or getattr(self.api, 'key', '')))}

    def portal(self, user):
        require(self.ready() or bool(getattr(self.api, 'key', '')), 'billing_not_configured', 503)
        self.store.initialize()
        customer = self.store.portal_customer(user)
        result = self.api.request('POST', 'customers/billing', data={'customer_id': customer})
        value = result.get('customer_portal_link')
        require(isinstance(value, str) and len(value) < 4096, 'invalid_portal_url', 502)
        url = urlparse(value)
        require(url.scheme == 'https' and url.hostname in {'creem.io','www.creem.io'}
                and not url.username and not url.password and url.port in (None,443)
                and url.path.startswith('/portal/'), 'invalid_portal_url', 502)
        return {'ok': True, 'portal_url': value}

    def checkout(self, user, plan, attempt):
        require(self.ready(), 'billing_not_configured', 503)
        require(plan in PLANS, 'invalid_plan')
        try:
            require(str(uuid.UUID(attempt)) == attempt, 'invalid_billing_attempt')
        except (ValueError, TypeError, AttributeError):
            raise BillingError('invalid_billing_attempt') from None
        self.store.initialize()
        self.verify_hook()
        self.product(plan)
        order, fresh = self.store.reserve(user, plan, attempt)
        if not fresh:
            require(order['state'] == 'open' and order['checkout_url'], 'checkout_already_pending', 409)
            return {'ok': True, 'checkout_url': checkout_url(order['checkout_url'])}
        try:
            result = self.api.request('POST', 'checkouts', data={
                'product_id': PLANS[plan]['product_id'], 'request_id': order['id'],
                'success_url': RETURN_URL, 'units': 1, 'metadata': {'velia_order_id': order['id']}})
            require(result.get('mode') == 'prod', 'billing_wrong_environment', 502)
            provider_id = identifier(result, 'ch_')
            url = checkout_url(result.get('checkout_url'))
            require(identifier(result.get('product'), 'prod_') == PLANS[plan]['product_id']
                    and result.get('request_id') == order['id']
                    and urlparse(url).path.rstrip('/').split('/')[-1] == provider_id, 'billing_checkout_conflict', 502)
            self.store.checkout_created(order, provider_id, url)
            return {'ok': True, 'checkout_url': url}
        except BaseException:
            self.store.uncertain(order)
            raise

    def verified_subscription(self, sub_id, order=None):
        sub = self.api.get('subscriptions', 'subscription_id', sub_id)
        require(sub.get('mode') == 'prod' and identifier(sub, 'sub_') == sub_id)
        product_id = identifier(sub.get('product'), 'prod_')
        spec_plan = next((key for key, p in PLANS.items() if p['product_id'] == product_id), None)
        require(spec_plan is not None, 'unrecognized_subscription')
        known = self.store.subscription(sub_id)
        order_id = (sub.get('metadata') or {}).get('velia_order_id')
        if order is None:
            order = self.store.order(known['order_id'] if known else order_id) if known or order_id else None
        require(order is not None, 'billing_order_not_bound', 503)
        require(order['plan'] == spec_plan and (not order_id or order_id == order['id']), 'billing_owner_conflict', 409)
        items = sub.get('items')
        require(isinstance(items, list) and len(items) == 1 and items[0].get('units') == 1
                and items[0].get('product_id') == product_id, 'billing_product_mismatch', 409)
        require(sub.get('status') in {'active','scheduled_cancel','canceled','expired','unpaid','past_due','paused','trialing'}, 'invalid_subscription_state')
        return sub, order

    def verified_transaction(self, tx_id, sub, order, *, refund=False):
        tx = self.api.get('transactions', 'transaction_id', tx_id)
        require(tx.get('mode') == 'prod' and identifier(tx, 'tran_') == tx_id)
        require(identifier(tx.get('subscription'), 'sub_') == sub['id']
                and identifier(tx.get('customer'), 'cust_') == identifier(sub.get('customer'), 'cust_'), 'billing_owner_conflict', 409)
        require(type(tx.get('amount')) is int and tx['amount'] == PLANS[order['plan']]['price']
                and tx.get('currency') == 'USD' and tx.get('type') == 'invoice', 'billing_amount_mismatch', 409)
        require(tx.get('status') in ({'refunded','partially_refunded','chargeback'} if refund else {'paid'}), 'billing_payment_not_paid', 503)
        require(type(tx.get('amount_paid')) is int and tx['amount_paid'] >= tx['amount'], 'billing_amount_mismatch', 409)
        if refund and tx['status'] == 'partially_refunded':
            require(type(tx.get('refunded_amount')) is int and 0 < tx['refunded_amount'] <= tx['amount_paid'], 'billing_amount_mismatch')
        start, end = timestamp(tx.get('period_start')), timestamp(tx.get('period_end'))
        require(0 < end - start <= 35 * 86400, 'invalid_billing_period')
        return {**tx, '_start': start, '_end': end}

    def webhook(self, raw, signature, secret):
        require(bool(secret), 'billing_not_configured', 503)
        require(len(raw) <= MAX_BODY, 'billing_body_too_large', 413)
        require(isinstance(signature, str) and re.fullmatch('[a-fA-F0-9]{64}', signature), 'invalid_signature', 401)
        require(hmac.compare_digest(hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), signature.lower()), 'invalid_signature', 401)
        require(self.ready(), 'billing_not_configured', 503)
        try:
            event = json.loads(raw)
            event_id, kind, obj = identifier(event, 'evt_'), event['eventType'], event['object']
            require(isinstance(obj, dict) and obj.get('mode', 'prod') == 'prod', 'billing_wrong_environment')
        except (ValueError, TypeError, KeyError, RecursionError):
            raise BillingError('invalid_billing_event') from None
        self.store.initialize()
        order = None
        tx_id = None
        if kind == 'checkout.completed':
            co = self.api.get('checkouts', 'checkout_id', identifier(obj, 'ch_'))
            require(co.get('mode') == 'prod' and co.get('status') == 'completed' and co.get('id') == obj['id'])
            product = identifier(co.get('product'), 'prod_')
            if product not in {p['product_id'] for p in PLANS.values()}:
                return {'ok': True, 'ignored': True}
            order_id = (co.get('metadata') or {}).get('velia_order_id')
            require(bool(order_id), 'billing_order_not_bound', 503)
            order = self.store.order(order_id)
            require(order is not None and co.get('request_id') == order['id'], 'billing_owner_conflict', 409)
            require(order['checkout_id'] in (None, co['id']) and identifier(co.get('product'), 'prod_') == PLANS[order['plan']]['product_id'], 'billing_owner_conflict', 409)
            sub_id = identifier(co.get('subscription'), 'sub_')
            tx_id = (co.get('order') or {}).get('transaction')
        elif isinstance(kind, str) and kind.startswith('subscription.'):
            sub_id = identifier(obj, 'sub_')
            if kind == 'subscription.paid':
                tx_id = obj.get('last_transaction_id')
        elif kind in {'refund.created','dispute.created'}:
            sub_id = identifier(obj.get('subscription'), 'sub_')
            tx_id = identifier(obj.get('transaction'), 'tran_')
        else:
            return {'ok': True, 'ignored': True}
        sub, order = self.verified_subscription(sub_id, order)
        if kind == 'checkout.completed' and tx_id is None:
            tx_id = sub.get('last_transaction_id')
        if kind in {'checkout.completed','subscription.paid'}:
            require(bool(tx_id), 'billing_transaction_not_ready', 503)
        refund = kind in {'refund.created','dispute.created'}
        tx = self.verified_transaction(identifier(tx_id, 'tran_'), sub, order, refund=refund) if tx_id else None
        return self.store.apply(order=order, sub=sub, transaction=tx, refund=refund,
                                event_id=event_id, digest=hashlib.sha256(raw).hexdigest())
