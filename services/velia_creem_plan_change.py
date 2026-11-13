"""Non-binding plan-change calculation and an isolated Creem sandbox probe.

Not wired to live checkout, fulfillment or HTTP routes. A computed estimate is
never an authorization to debit a card or grant tokens. Provider cycle/proration
semantics must first be measured with sandbox invoices.
"""
from fractions import Fraction
import hashlib
import json
import re

from services.velia_creem_billing import BillingError, BillingStore, PLANS, RANK, identifier, require, timestamp


BEHAVIORS = frozenset({'proration-charge-immediately', 'proration-charge', 'proration-none'})

# One explicitly authorized experiment on the already discovered sandbox
# subscription. These IDs are NOT live products or the live fulfillment map.
APPROVED_SANDBOX_EXPERIMENT = {
    'run_id':'velia-creem-plus-pro-immediate-2026-11-13-1',
    'subscription_id':'sub_1zCm0jsSzizMUIagDO1oyp',
    'products':{'plus':'prod_fvrGJW4vX4KZfzJfXBZBO',
                'pro':'prod_7e0Yag0LFPiyVAx4OUs4IG',
                'max':'prod_1TxrSlOznctfgTo7mBNXMq'},
    'source':'plus', 'target':'pro', 'behavior':'proration-charge-immediately'}


def change_estimate(current, target, *, period_start, period_end, now):
    """Reference policy for an unchanged billing cycle, in integer cents.

Only the difference in monthly allowances is prorated; remaining balance is
untouched. Calendar months use the real invoice timestamps, never a fixed 30d.
Creem may reset the cycle: in that case this reference policy is NOT a quote.
"""
    require(current in PLANS and target in PLANS and current != target, 'invalid_plan_change')
    require(all(type(v) is int for v in (period_start, period_end, now)), 'invalid_billing_period')
    require(0 < period_start <= now < period_end and period_end-period_start <= 35*86400,
            'invalid_billing_period')
    upgrade = RANK[target] > RANK[current]
    remaining = Fraction(period_end-now, period_end-period_start)
    difference = (PLANS[target]['price']-PLANS[current]['price']) * remaining
    # Half up for the illustrative monetary estimate; floor for extra credits.
    cents = ((difference.numerator*2+difference.denominator)//(2*difference.denominator)
             if upgrade else 0)
    credits = int((PLANS[target]['credits']-PLANS[current]['credits'])*remaining) if upgrade else 0
    return {'current_plan': current, 'target_plan': target,
            'direction': 'upgrade' if upgrade else 'downgrade',
            'currency': 'USD', 'estimate_only': True, 'can_confirm': False,
            'estimated_due_cents': cents, 'estimated_extra_credits': credits,
            'effective_at': now if upgrade else period_end,
            'next_monthly_price_cents': PLANS[target]['price'],
            'next_monthly_credits': PLANS[target]['credits'],
            'balance_action': 'preserve', 'cycle_assumption': 'unchanged',
            'blocked_reason': 'provider_proration_not_verified'}


class SandboxAPI:
    """Fixed test origin, test-key required, narrow read/write allowlist.

    Never accepts a base URL or uses VELIA_CREEM_API_KEY. No retries, redirects,
    refund/cancel operations or access to the production ledger. The only
    product edit allowed is inclusive tax on the approved sandbox Plus.
    """
    def __init__(self, key, *, requester=None):
        require(isinstance(key, str) and key.startswith('creem_test_') and len(key) >= 20
                and not any(c.isspace() for c in key), 'test_api_key_required')
        self._key, self._requester = key, requester

    def request(self, method, resource, *, params=None, data=None):
        read = method == 'GET' and resource in {
            'products', 'subscriptions', 'transactions', 'products/search', 'subscriptions/search'}
        write = (method == 'POST' and re.fullmatch(r'subscriptions/sub_[A-Za-z0-9_-]+/upgrade', resource)
                 and isinstance(data, dict) and set(data) == {'product_id', 'update_behavior'}
                 and data['update_behavior'] in BEHAVIORS)
        tax_fix = (method == 'PATCH' and resource == 'products/'+APPROVED_SANDBOX_EXPERIMENT['products']['plus']
                   and params is None and data == {'tax_mode':'inclusive'})
        require(bool(read or write or tax_fix), 'sandbox_operation_not_allowed')
        if write:
            identifier(data['product_id'], 'prod_')
        import requests
        requester = self._requester or requests.request
        try:
            with requester(method, 'https://test-api.creem.io/v1/'+resource,
                           params=params, json=data, headers={'x-api-key': self._key},
                           timeout=(3, 15), allow_redirects=False, stream=True) as response:
                require(response.status_code in (200, 201), 'sandbox_provider_error', 502)
                raw = bytearray()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    require(len(raw) <= 512*1024, 'sandbox_response_too_large', 502)
                import json
                result = json.loads(raw)
                require(isinstance(result, dict), 'sandbox_invalid_response', 502)
                return result
        except BillingError:
            raise
        except Exception:
            # Never surface URLs, headers, response bodies, card or customer data.
            raise BillingError('sandbox_request_failed', 502) from None

    def get(self, resource, field, value):
        return self.request('GET', resource, params={field: value})


def discover_sandbox(api):
    """Two bounded, read-only requests; return allowlisted test IDs only.

    Listing is evidence that the configured key works, not proof an invoice is
    paid. A mutation still needs the canonical ownership checks in probe_change.
    The first-page limit is explicit so discovery never delays a release with an
    unbounded store scan. Product names, emails, customers and URLs are omitted.
    """
    require(isinstance(api, SandboxAPI), 'sandbox_client_required')
    products = api.request('GET', 'products/search', params={'page_number':1, 'page_size':25})
    subscriptions = api.request('GET', 'subscriptions/search', params={'page_number':1, 'page_size':25})
    for result in (products, subscriptions):
        require(isinstance(result.get('items'), list) and len(result['items']) <= 25,
                'sandbox_invalid_response', 502)
        require(all(isinstance(item, dict) and item.get('mode') == 'test' for item in result['items']),
                'sandbox_wrong_environment', 502)
    candidates, product_plans = {plan:[] for plan in PLANS}, {}
    for product in products['items']:
        if (product.get('currency') != 'USD' or product.get('billing_type') != 'recurring'
                or product.get('billing_period') != 'every-month' or product.get('status') != 'active'
                or product.get('tax_mode') not in {'inclusive', 'exclusive'}
                or type(product.get('price')) is not int):
            continue
        plan = next((key for key, value in PLANS.items() if value['price'] == product['price']), None)
        if plan is None:
            continue
        product_id = identifier(product, 'prod_')
        candidates[plan].append({'product_id':product_id, 'tax_mode':product['tax_mode']})
        product_plans[product_id] = plan
    summaries = []
    for sub in subscriptions['items']:
        product_id = identifier(sub.get('product'), 'prod_')
        if product_id not in product_plans:
            continue
        status = sub.get('status')
        require(status in {'active', 'scheduled_cancel', 'canceled', 'expired', 'unpaid',
                           'past_due', 'paused', 'trialing'}, 'sandbox_subscription_mismatch')
        summaries.append({'subscription_id':identifier(sub, 'sub_'),
                          'plan':product_plans[product_id], 'status':status})
    return {'ok':True, 'mode':'test', 'read_only':True, 'key_authenticated':True,
            'live_changes_enabled':False, 'candidate_products':candidates,
            'candidate_subscriptions':summaries,
            'first_page_only':True,
            'more_products':bool((products.get('pagination') or {}).get('next_page')),
            'more_subscriptions':bool((subscriptions.get('pagination') or {}).get('next_page')),
            'requires_canonical_paid_invoice_validation':True}


def _product(api, product_id):
    product_id = identifier(product_id, 'prod_')
    product = api.get('products', 'product_id', product_id)
    require(product.get('id') == product_id and product.get('mode') == 'test'
            and product.get('currency') == 'USD' and product.get('status') == 'active'
            and product.get('billing_type') == 'recurring'
            and product.get('billing_period') == 'every-month'
            and product.get('tax_mode') in {'inclusive', 'exclusive'}
            and type(product.get('price')) is int and product['price'] > 0,
            'sandbox_product_mismatch')
    return product


def snapshot(api, subscription_id):
    """Return canonical sandbox state with no customer PII or portal URLs."""
    subscription_id = identifier(subscription_id, 'sub_')
    sub = api.get('subscriptions', 'subscription_id', subscription_id)
    require(sub.get('id') == subscription_id and sub.get('mode') == 'test', 'sandbox_subscription_mismatch')
    require(sub.get('status') in {'active', 'scheduled_cancel', 'canceled', 'expired',
                                'unpaid', 'past_due', 'paused', 'trialing'}, 'sandbox_subscription_mismatch')
    product_id = identifier(sub.get('product'), 'prod_')
    product = _product(api, product_id)
    items = sub.get('items')
    require(isinstance(items, list) and len(items) == 1 and items[0].get('product_id') == product_id
            and type(items[0].get('units')) is int and items[0]['units'] == 1,
            'sandbox_subscription_mismatch')
    customer_id = identifier(sub.get('customer'), 'cust_')
    start, end = timestamp(sub.get('current_period_start_date')), timestamp(sub.get('current_period_end_date'))
    require(0 < end-start <= 35*86400, 'invalid_billing_period')
    result = {'status': sub.get('status'), 'period_start': start, 'period_end': end,
              'product_id': product_id, 'price_cents': product['price'], 'tax_mode': product['tax_mode']}
    transaction_id = sub.get('last_transaction_id')
    if transaction_id:
        transaction_id = identifier(transaction_id, 'tran_')
        tx = api.get('transactions', 'transaction_id', transaction_id)
        require(tx.get('mode') == 'test' and identifier(tx, 'tran_') == transaction_id
                and identifier(tx.get('subscription'), 'sub_') == subscription_id
                and identifier(tx.get('customer'), 'cust_') == customer_id
                and tx.get('currency') == 'USD' and tx.get('type') in {'invoice','payment'},
                'sandbox_invoice_mismatch')
        require(all(type(tx.get(k)) is int and tx[k] >= 0 for k in ('amount', 'amount_paid')),
                'sandbox_invoice_mismatch')
        require(tx.get('tax_amount') is None or (type(tx['tax_amount']) is int and tx['tax_amount'] >= 0),
                'sandbox_invoice_mismatch')
        require(tx.get('status') in {'paid', 'pending', 'processing', 'failed', 'refunded',
                                    'partially_refunded', 'chargeback', 'canceled'}, 'sandbox_invoice_mismatch')
        result['last_invoice'] = {'transaction_id': transaction_id, 'status': tx.get('status'), 'type':tx['type'],
            'amount_cents': tx['amount'], 'paid_cents': tx['amount_paid'],
            'tax_cents': tx.get('tax_amount'),
            # A checkout payment can omit invoice periods. Keep absence explicit
            # rather than fabricate dates from the subscription or grant access.
            'period_start': timestamp(tx['period_start']) if tx.get('period_start') is not None else None,
            'period_end': timestamp(tx['period_end']) if tx.get('period_end') is not None else None}
    return result


def probe_change(api, subscription_id, target_product_id, behavior, *, expected_source=None):
    """Perform ONE explicit sandbox mutation after canonical test-mode checks.

    A timeout or unexpected response is ambiguous: callers must inspect with
    snapshot(), never automatically repeat POST. This is evidence collection,
    not a certification of live readiness or a payment confirmation screen.
    """
    require(isinstance(api, SandboxAPI), 'sandbox_client_required')
    require(behavior in BEHAVIORS, 'invalid_proration_behavior')
    before = snapshot(api, subscription_id)
    require(expected_source is None or before['product_id'] == expected_source, 'sandbox_source_changed')
    require(before['status'] == 'active' and before.get('last_invoice', {}).get('status') == 'paid',
            'sandbox_paid_subscription_required')
    target = _product(api, target_product_id)
    require(target['id'] != before['product_id'], 'invalid_plan_change')
    # This is the ONLY write. No automatic retry, including after an ambiguous
    # response or failed readback. The CLI labels such outcomes as uncertain.
    result = api.request('POST', 'subscriptions/'+subscription_id+'/upgrade',
                        data={'product_id': target['id'], 'update_behavior': behavior})
    require(result.get('mode') == 'test' and identifier(result, 'sub_') == subscription_id,
            'sandbox_subscription_mismatch')
    after = snapshot(api, subscription_id)
    previous = before.get('last_invoice', {}).get('transaction_id')
    current = after.get('last_invoice', {}).get('transaction_id')
    return {'mode': 'test', 'live_changes_enabled': False, 'behavior': behavior,
            'before': before, 'after': after,
            'cycle_changed': (before['period_start'], before['period_end']) !=
                             (after['period_start'], after['period_end']),
            'new_invoice_observed': bool(current and current != previous),
            'target_applied': after['product_id'] == target['id'],
            'requires_webhook_and_renewal_validation': True}


class SandboxRunStore:
    """A separate audit table; no reads/writes to users or billing ledgers.

    The durable INSERT commits before the provider POST. A process crash, timeout
    or failed report persistence can never turn a later deploy into a retry.
    """
    def __init__(self, connect=None, *, sqlite=False):
        self.db = BillingStore(connect, sqlite=sqlite)

    def initialize(self):
        with self.db.transaction() as cur:
            if not self.db.sqlite:
                cur.execute('SELECT pg_advisory_xact_lock(1450731742)')
            cur.execute('''CREATE TABLE IF NOT EXISTS velia_creem_sandbox_runs (
                id TEXT PRIMARY KEY, intent_digest TEXT NOT NULL,
                state TEXT NOT NULL, report TEXT NOT NULL)''')

    def get(self, run_id):
        with self.db.transaction() as cur:
            self.db.execute(cur, 'SELECT * FROM velia_creem_sandbox_runs WHERE id=?', (run_id,))
            return self.db.row(cur)

    def claim(self, run_id, digest, before):
        report = json.dumps({'mode':'test', 'live_changes_enabled':False, 'before':before}, sort_keys=True)
        with self.db.transaction() as cur:
            self.db.execute(cur, '''INSERT INTO velia_creem_sandbox_runs(id,intent_digest,state,report)
                VALUES (?,?,'started',?) ON CONFLICT DO NOTHING''', (run_id,digest,report))
            claimed = cur.rowcount == 1
            self.db.execute(cur, 'SELECT * FROM velia_creem_sandbox_runs WHERE id=?', (run_id,))
            row = self.db.row(cur)
            require(row is not None and row['intent_digest'] == digest, 'sandbox_intent_conflict', 409)
            return claimed, row

    def record(self, run_id, state, report):
        require(state in {'observed','uncertain'}, 'sandbox_invalid_state')
        with self.db.transaction() as cur:
            self.db.execute(cur, '''UPDATE velia_creem_sandbox_runs SET state=?,report=?
                WHERE id=? AND state='started' ''', (state,json.dumps(report,sort_keys=True),run_id))


def run_approved_sandbox_experiment(api, runs):
    """At most one attempted Plus→Pro POST across processes and redeploys.

    Preflight requires the exact source/target prices and paid test Plus. It can
    fix only that sandbox Plus's tax mode. Max does not block a Plus→Pro test.
    A resumed run only reads
    the provider state, even if the previous process died before making a POST.
    """
    require(isinstance(api, SandboxAPI), 'sandbox_client_required')
    intent = APPROVED_SANDBOX_EXPERIMENT
    digest = hashlib.sha256(json.dumps(intent,sort_keys=True).encode()).hexdigest()
    runs.initialize()

    def resume(row):
        require(row['intent_digest'] == digest, 'sandbox_intent_conflict', 409)
        saved = json.loads(row['report'])
        try:
            current = snapshot(api, intent['subscription_id'])
            # Read-only reconciliation is evidence, never another charge or an
            # assertion that a target product without a new paid invoice is paid.
            observed = {'current':current,
                'new_invoice_observed':bool(current.get('last_invoice',{}).get('transaction_id') and
                    current['last_invoice']['transaction_id'] != saved['before'].get('last_invoice',{}).get('transaction_id'))}
        except BillingError as error:
            observed = {'readback_error':error.code}
        return {'mode':'test', 'live_changes_enabled':False, 'run_id':intent['run_id'],
                'state':row['state'], 'reused':True, 'write_retried':False,
                'saved_report':saved, **observed}

    previous = runs.get(intent['run_id'])
    if previous:
        return resume(previous)
    products = {}
    for plan in (intent['source'],intent['target']):
        product_id = intent['products'][plan]
        product = _product(api, product_id)
        require(product['price'] == PLANS[plan]['price']
                and (plan == intent['source'] or product['tax_mode'] == 'inclusive'),
                'sandbox_plan_settings_mismatch')
        products[plan] = product
    before = snapshot(api, intent['subscription_id'])
    require(before['product_id'] == intent['products'][intent['source']]
            and before['status'] == 'active' and before.get('last_invoice',{}).get('status') == 'paid'
            and before['last_invoice']['paid_cents'] >= PLANS[intent['source']]['price'],
            'sandbox_paid_source_plan_required')
    if products[intent['source']]['tax_mode'] == 'exclusive':
        # Setting one known tax field is idempotent and never changes price or
        # charges a card. Verify canonical paid ownership before this edit, then
        # read back the product. A lost reply can safely be resolved by GET on
        # the next run; the subscription POST still has its durable claim below.
        product_id = intent['products'][intent['source']]
        result = api.request('PATCH','products/'+product_id,data={'tax_mode':'inclusive'})
        require(result.get('mode') == 'test' and identifier(result,'prod_') == product_id,
                'sandbox_product_mismatch')
        corrected = _product(api, product_id)
        require(corrected['price'] == PLANS[intent['source']]['price'] and corrected['tax_mode'] == 'inclusive',
                'sandbox_plan_settings_mismatch')
        before = snapshot(api,intent['subscription_id'])
        require(before['product_id'] == product_id and before['status'] == 'active'
                and before.get('last_invoice',{}).get('status') == 'paid'
                and before['last_invoice']['paid_cents'] >= PLANS[intent['source']]['price'],
                'sandbox_paid_source_plan_required')
    claimed, row = runs.claim(intent['run_id'], digest, before)
    if not claimed:
        return resume(row)
    try:
        report = probe_change(api, intent['subscription_id'], intent['products'][intent['target']], intent['behavior'],
                              expected_source=intent['products'][intent['source']])
    except Exception as error:
        report = {'mode':'test', 'live_changes_enabled':False, 'before':before,
                  'error':error.code if isinstance(error,BillingError) else 'sandbox_probe_unavailable',
                  'outcome':'inspect_subscription_before_retry'}
        runs.record(intent['run_id'], 'uncertain', report)
        return {'run_id':intent['run_id'], 'state':'uncertain', **report}
    runs.record(intent['run_id'], 'observed', report)
    return {'run_id':intent['run_id'], 'state':'observed', **report}
