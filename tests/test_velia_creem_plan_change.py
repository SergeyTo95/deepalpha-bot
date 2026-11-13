from copy import deepcopy
import json
import pytest

from services.velia_creem_billing import BillingError, PLANS
from services.velia_creem_plan_change import SandboxAPI, change_estimate, discover_sandbox, probe_change, snapshot
from scripts.velia_creem_plan_change_probe import main


START = 1794500000
TEST_KEY = 'creem_test_fixture_not_a_real_key'


@pytest.mark.parametrize('days', [28, 29, 30, 31])
def test_upgrade_uses_actual_month_and_only_remaining_difference(days):
    end = START+days*86400
    preview = change_estimate('plus', 'pro', period_start=START, period_end=end, now=(START+end)//2)
    assert preview['estimated_due_cents'] == 750
    assert preview['estimated_extra_credits'] == 900
    assert preview['balance_action'] == 'preserve'
    assert preview['can_confirm'] is False and preview['estimate_only'] is True
    assert preview['cycle_assumption'] == 'unchanged'


def test_rounding_last_second_does_not_grant_monthly_pack():
    preview = change_estimate('plus', 'max', period_start=START, period_end=START+31*86400, now=START+31*86400-1)
    assert preview['estimated_extra_credits'] == 0
    assert preview['estimated_due_cents'] == 0
    assert preview['can_confirm'] is False


@pytest.mark.parametrize('current,target', [('plus','pro'), ('plus','max'), ('pro','max')])
def test_start_of_cycle_is_difference_not_second_full_allowance(current, target):
    preview = change_estimate(current, target, period_start=START, period_end=START+30*86400, now=START)
    assert preview['estimated_due_cents'] == PLANS[target]['price']-PLANS[current]['price']
    assert preview['estimated_extra_credits'] == PLANS[target]['credits']-PLANS[current]['credits']


@pytest.mark.parametrize('current,target', [('max','pro'), ('max','plus'), ('pro','plus')])
def test_downgrade_next_period_never_refunds_or_removes_balance(current, target):
    preview = change_estimate(current, target, period_start=START, period_end=START+30*86400, now=START+15*86400)
    assert preview['effective_at'] == START+30*86400
    assert preview['estimated_due_cents'] == preview['estimated_extra_credits'] == 0
    assert preview['next_monthly_price_cents'] == PLANS[target]['price']
    assert preview['next_monthly_credits'] == PLANS[target]['credits']
    assert preview['balance_action'] == 'preserve'


@pytest.mark.parametrize('kwargs', [
    {'current':'free'}, {'current':'plus','target':'plus'}, {'target':'starter'},
    {'now':START-1}, {'now':START+30*86400}, {'period_end':START},
    {'period_end':START+36*86400}, {'now':True}, {'period_start':float(START)}])
def test_invalid_or_expired_reference_is_rejected(kwargs):
    params = dict(current='plus', target='pro', period_start=START, period_end=START+30*86400, now=START)
    params.update(kwargs)
    with pytest.raises(BillingError):
        change_estimate(**params)


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield json.dumps(self.data).encode()


class Transport:
    def __init__(self):
        self.calls = []
        self.product = lambda key, price: {'id':key, 'mode':'test', 'price':price, 'currency':'USD',
            'status':'active', 'tax_mode':'inclusive', 'billing_type':'recurring', 'billing_period':'every-month'}
        self.values = {
            ('products','prod_testplus'):self.product('prod_testplus',1499),
            ('products','prod_testpro'):self.product('prod_testpro',2999),
            ('subscriptions','sub_test'): {'id':'sub_test','mode':'test','product':'prod_testplus',
                'customer':{'id':'cust_test','email':'private@example.com'}, 'status':'active',
                'items':[{'product_id':'prod_testplus','units':1}], 'last_transaction_id':'tran_before',
                'current_period_start_date':START, 'current_period_end_date':START+30*86400},
            ('transactions','tran_before'): {'id':'tran_before','mode':'test','subscription':'sub_test',
                'customer':'cust_test','type':'invoice','currency':'USD','status':'paid',
                'amount':1499,'amount_paid':1499,'tax_amount':250,
                'period_start':START,'period_end':START+30*86400}}
        self.timeout = False
        self.error_status = 200

    def __call__(self, method, url, **kwargs):
        assert url.startswith('https://test-api.creem.io/v1/')
        assert kwargs['allow_redirects'] is False
        self.calls.append((method,url,deepcopy(kwargs.get('json'))))
        resource = url.removeprefix('https://test-api.creem.io/v1/')
        if method == 'GET':
            return Response(deepcopy(self.values[(resource,next(iter(kwargs['params'].values())))]))
        # Simulate Creem having acted before a dropped HTTP response.
        sub = self.values[('subscriptions','sub_test')]
        sub['product'] = kwargs['json']['product_id']
        sub['items'][0]['product_id'] = sub['product']
        if self.timeout:
            raise TimeoutError('sensitive provider details must not escape')
        return Response(deepcopy(sub), self.error_status)


@pytest.mark.parametrize('key', ['', 'creem_live_not_a_real_key', TEST_KEY+'\n', None])
def test_sandbox_never_accepts_live_or_missing_key(key):
    with pytest.raises(BillingError, match='test_api_key_required'):
        SandboxAPI(key)


@pytest.mark.parametrize('method,resource', [('POST','checkouts'), ('POST','customers/billing'),
    ('POST','subscriptions/sub_test/cancel'), ('GET','https://api.creem.io/v1/subscriptions'),
    ('GET','webhooks'), ('DELETE','products')])
def test_sandbox_allowlist_rejects_unrelated_operations_before_network(method, resource):
    transport = Transport()
    with pytest.raises(BillingError, match='sandbox_operation_not_allowed'):
        SandboxAPI(TEST_KEY,requester=transport).request(method,resource)
    assert transport.calls == []


def test_probe_records_one_mutation_and_explicitly_not_live_ready():
    transport = Transport()
    result = probe_change(SandboxAPI(TEST_KEY,requester=transport), 'sub_test', 'prod_testpro', 'proration-none')
    assert len([call for call in transport.calls if call[0]=='POST']) == 1
    assert result['target_applied'] is True
    assert result['new_invoice_observed'] is False
    assert result['live_changes_enabled'] is False
    assert result['requires_webhook_and_renewal_validation'] is True
    assert 'private@example.com' not in json.dumps(result)
    assert 'cust_test' not in json.dumps(result)
    assert TEST_KEY not in json.dumps(result)


def test_probe_observes_new_invoice_and_cycle_reset_without_claiming_expected_proration():
    transport = Transport()
    def reset_cycle(method, url, **kwargs):
        result = transport(method, url, **kwargs)
        if method == 'POST':
            sub = transport.values[('subscriptions','sub_test')]
            sub.update(current_period_start_date=START+15*86400,
                       current_period_end_date=START+45*86400, last_transaction_id='tran_after')
            tx = deepcopy(transport.values[('transactions','tran_before')])
            tx.update(id='tran_after', amount=2250, amount_paid=2250, tax_amount=375,
                      period_start=START+15*86400, period_end=START+45*86400)
            transport.values[('transactions','tran_after')] = tx
        return result
    report = probe_change(SandboxAPI(TEST_KEY,requester=reset_cycle), 'sub_test', 'prod_testpro',
                          'proration-charge-immediately')
    assert report['cycle_changed'] is True
    assert report['new_invoice_observed'] is True
    assert report['after']['last_invoice']['paid_cents'] == 2250
    assert report['live_changes_enabled'] is False


def test_read_only_inspection_does_not_create_invoice_or_change_product():
    transport = Transport()
    report = snapshot(SandboxAPI(TEST_KEY,requester=transport), 'sub_test')
    assert report['product_id'] == 'prod_testplus'
    assert all(call[0]=='GET' for call in transport.calls)


@pytest.mark.parametrize('case', ['subscription_mode','product_mode','customer','unpaid','units','amount'])
def test_bad_canonical_binding_prevents_any_mutation(case):
    transport = Transport()
    if case=='subscription_mode': transport.values[('subscriptions','sub_test')]['mode']='prod'
    if case=='product_mode': transport.values[('products','prod_testpro')]['mode']='prod'
    if case=='customer': transport.values[('transactions','tran_before')]['customer']='cust_foreign'
    if case=='unpaid': transport.values[('transactions','tran_before')]['status']='pending'
    if case=='units': transport.values[('subscriptions','sub_test')]['items'][0]['units']=True
    if case=='amount': transport.values[('transactions','tran_before')]['amount_paid']=True
    with pytest.raises(BillingError):
        probe_change(SandboxAPI(TEST_KEY,requester=transport), 'sub_test', 'prod_testpro', 'proration-charge-immediately')
    assert not any(call[0]=='POST' for call in transport.calls)


def test_ambiguous_test_mutation_is_never_retried_and_read_only_recovery_is_possible():
    transport = Transport()
    transport.timeout = True
    api = SandboxAPI(TEST_KEY,requester=transport)
    with pytest.raises(BillingError, match='sandbox_request_failed') as error:
        probe_change(api, 'sub_test', 'prod_testpro', 'proration-charge-immediately')
    assert 'sensitive' not in str(error.value)
    assert len([call for call in transport.calls if call[0]=='POST']) == 1
    assert snapshot(api, 'sub_test')['product_id'] == 'prod_testpro'
    assert len([call for call in transport.calls if call[0]=='POST']) == 1


def test_cli_does_not_fall_back_to_live_key(monkeypatch, capsys):
    monkeypatch.delenv('VELIA_CREEM_TEST_API_KEY', raising=False)
    monkeypatch.setenv('VELIA_CREEM_API_KEY','creem_live_not_a_real_key')
    assert main(['--subscription','sub_test']) == 1
    assert json.loads(capsys.readouterr().out)['error']=='test_api_key_required'


def test_cli_requires_explicit_test_write_acknowledgment():
    with pytest.raises(SystemExit) as error:
        main(['--subscription','sub_test','--target-product','prod_testpro','--behavior','proration-none'])
    assert error.value.code==2


class SearchTransport:
    def __init__(self):
        fixture = Transport()
        self.products = {'items':[fixture.values[('products','prod_testplus')],
                                  fixture.values[('products','prod_testpro')]], 'pagination':{'next_page':None}}
        self.subscriptions = {'items':[fixture.values[('subscriptions','sub_test')]], 'pagination':{'next_page':2}}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        assert method=='GET' and kwargs['json'] is None
        assert kwargs['params']=={'page_number':1,'page_size':25}
        assert kwargs['allow_redirects'] is False
        return Response(deepcopy(self.products if url.endswith('/products/search') else self.subscriptions))


def test_discovery_authenticates_test_key_with_two_reads_and_no_pii():
    transport = SearchTransport()
    report = discover_sandbox(SandboxAPI(TEST_KEY,requester=transport))
    assert len(transport.calls)==2
    assert all(call[1].startswith('https://test-api.creem.io/v1/') for call in transport.calls)
    assert report['key_authenticated'] is True and report['read_only'] is True
    assert report['candidate_products']['plus']==[{'product_id':'prod_testplus','tax_mode':'inclusive'}]
    assert report['candidate_products']['max']==[]
    assert report['candidate_subscriptions']==[{'subscription_id':'sub_test','plan':'plus','status':'active'}]
    assert report['more_subscriptions'] is True and report['first_page_only'] is True
    assert report['requires_canonical_paid_invoice_validation'] is True
    encoded = json.dumps(report)
    assert 'private@example.com' not in encoded and 'cust_test' not in encoded and TEST_KEY not in encoded


@pytest.mark.parametrize('family', ['products','subscriptions'])
def test_discovery_rejects_any_production_record(family):
    transport = SearchTransport()
    getattr(transport,family)['items'][0]['mode']='prod'
    with pytest.raises(BillingError,match='sandbox_wrong_environment'):
        discover_sandbox(SandboxAPI(TEST_KEY,requester=transport))


def test_discovery_does_not_treat_unrelated_products_or_prices_as_velia_plans():
    transport = SearchTransport()
    transport.products['items'][0]['price']=399
    report = discover_sandbox(SandboxAPI(TEST_KEY,requester=transport))
    assert report['candidate_products']['plus']==[]
    assert report['candidate_subscriptions']==[]


def test_discovery_limits_scan_and_rejects_oversized_page():
    transport = SearchTransport()
    transport.products['items']*=13
    with pytest.raises(BillingError,match='sandbox_invalid_response'):
        discover_sandbox(SandboxAPI(TEST_KEY,requester=transport))
    assert len(transport.calls)==2


def test_predeploy_discovery_skips_missing_key_without_live_fallback(monkeypatch,capsys):
    monkeypatch.delenv('VELIA_CREEM_TEST_API_KEY',raising=False)
    monkeypatch.setenv('VELIA_CREEM_API_KEY','creem_live_not_a_real_key')
    assert main(['--predeploy-discover'])==0
    line = capsys.readouterr().out.removeprefix('VELIA_CREEM_SANDBOX_DISCOVERY ')
    assert json.loads(line)=={'ok':True,'skipped':True,'reason':'test_api_key_not_configured'}


def test_predeploy_invalid_key_is_reported_without_breaking_live_release(monkeypatch,capsys):
    monkeypatch.setenv('VELIA_CREEM_TEST_API_KEY','creem_live_not_a_real_key')
    assert main(['--predeploy-discover'])==0
    line = capsys.readouterr().out.removeprefix('VELIA_CREEM_SANDBOX_DISCOVERY ')
    assert json.loads(line)['error']=='test_api_key_required'
    assert 'creem_live' not in line


def test_predeploy_network_errors_are_sanitized_and_do_not_block_release(monkeypatch,capsys):
    import scripts.velia_creem_plan_change_probe as probe
    monkeypatch.setenv('VELIA_CREEM_TEST_API_KEY',TEST_KEY)
    def fail(*args):
        raise RuntimeError('sensitive response and credentials')
    monkeypatch.setattr(probe,'discover_sandbox',fail)
    assert probe.main(['--predeploy-discover'])==0
    output = capsys.readouterr().out
    assert 'sensitive' not in output and TEST_KEY not in output
    assert json.loads(output.removeprefix('VELIA_CREEM_SANDBOX_DISCOVERY '))['error']=='sandbox_probe_unavailable'


@pytest.mark.parametrize('flag',['--discover','--predeploy-discover'])
def test_discovery_cli_cannot_be_combined_with_a_plan_mutation(flag):
    with pytest.raises(SystemExit) as error:
        main([flag,'--apply-test-change','--target-product','prod_testpro','--behavior','proration-none'])
    assert error.value.code==2
