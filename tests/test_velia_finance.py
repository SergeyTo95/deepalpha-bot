import asyncio
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from desktop.finance import analyze, FinanceError
from desktop.finance_store import FinanceStore
from desktop.finance_routes import setup_finance_routes


def snapshot():
    return {'period_start': '2026-10-01', 'period_end': '2026-10-30', 'source': 'Test statement',
            'accounts': [{'id': 'bank', 'project': 'Velia', 'currency': 'USD', 'balance': '1000',
                          'restricted': '100', 'due': '50', 'liquid': True, 'as_of': '2026-10-30'}],
            'transactions': [{'id': 'sale', 'account_id': 'bank', 'date': '2026-10-03', 'kind': 'revenue', 'amount': '200'},
                             {'id': 'hosting', 'account_id': 'bank', 'date': '2026-10-04', 'kind': 'expense', 'amount': '300'}],
            'budgets': [{'project': 'Velia', 'currency': 'USD', 'monthly_limit': '250'}]}


def test_balances_are_closing_not_added_again_and_reserve_is_gross_expense():
    report = analyze(snapshot())
    total, project = report['rows']
    assert total['project'] is None and project['project'] == 'Velia'
    for row in report['rows']:
        assert row['available'] == '850'
        assert row['monthly_expense'] == '300'
        assert row['monthly_burn'] == '100'
        assert row['reserve_required'] == '1800' and row['reserve_gap'] == '950'
        assert row['runway_months'] == '8.5'
        assert row['stress_runway_months'] == '4.25'
        assert row['forecast'] == {'3': '550', '6': '250', '12': '-350'}
    assert project['budget_excess'] == '50'


def test_no_implicit_fx_or_profit_from_loans_transfers_or_asset_sales():
    data = snapshot()
    for code in ('USDT', 'TRY'):
        data['accounts'].append({**data['accounts'][0], 'id': code, 'currency': code})
    data['accounts'].append({**data['accounts'][0], 'id': 'shares', 'balance': '50000', 'restricted': '0', 'due': '0', 'liquid': False})
    for kind in ('financing_in', 'transfer_in', 'investment_in'):
        data['transactions'].append({'id': kind, 'account_id': 'bank', 'date': '2026-10-05', 'kind': kind, 'amount': '50000'})
    report = analyze(data)
    totals = {r['currency']: r for r in report['rows'] if r['project'] is None}
    assert set(totals) == {'USD', 'USDT', 'TRY'}
    assert totals['USD']['available'] == '850' and totals['USD']['assets'] == '51000'
    assert totals['USD']['operating_cash_flow'] == '-100'
    assert 'user_classified_crypto_liquidity_has_market_risk' in report['warnings']


def test_decimal_precision_and_negative_liquidity():
    data = snapshot()
    data['accounts'][0].update(balance='0.3', restricted='0.1', due='0.3')
    data['transactions'][0]['amount'] = '0.1'
    data['transactions'][1]['amount'] = '0.2'
    row = analyze(data)['rows'][0]
    assert row['available'] == '-0.1' and row['monthly_burn'] == '0.1'
    assert row['runway_months'] == '0' and row['reserve_gap'] == '1.3'


def test_no_flow_and_profitable_period_are_not_infinite_runway():
    data = snapshot()
    data['transactions'] = []
    report = analyze(data)
    assert 'no_transactions_zero_flow_is_not_verified' in report['warnings']
    assert report['rows'][0]['runway_months'] is None
    data['transactions'] = snapshot()['transactions'][:1]
    assert analyze(data)['rows'][0]['runway_months'] is None


@pytest.mark.parametrize('invalid', [1, 1.2, True, '-1', 'NaN', 'Infinity', '1e2', '1,2', '0.000000001', '9'*16, '', None])
def test_invalid_amounts_rejected(invalid):
    data = snapshot()
    data['accounts'][0]['balance'] = invalid
    with pytest.raises(FinanceError):
        analyze(data)


@pytest.mark.parametrize('mutation', [
    lambda d: d['accounts'].append(deepcopy(d['accounts'][0])),
    lambda d: d['transactions'].append(deepcopy(d['transactions'][0])),
    lambda d: d['accounts'][0].update(as_of='2026-10-29'),
    lambda d: d['transactions'][0].update(account_id='other-user-account'),
    lambda d: d['transactions'][0].update(date='2026-09-30'),
    lambda d: d.update(period_end='2026-02-30'),
    lambda d: d.update(period_start='2024-01-01'),
    lambda d: d['accounts'][0].update(currency='USD;DROP'),
    lambda d: d['accounts'][0].update(restricted='1001'),
    lambda d: d.update(user_id='other-user'),
    lambda d: d['accounts'][0].update(api_key='secret'),
])
def test_duplicates_unknown_accounts_wrong_dates_and_extra_fields_fail(mutation):
    data = snapshot()
    mutation(data)
    with pytest.raises(FinanceError):
        analyze(data)


def test_short_period_is_normalized_and_marked():
    data = snapshot()
    data['period_start'] = '2026-10-16'
    for row in data['transactions']:
        row['date'] = '2026-10-16'
    report = analyze(data, reserve_months=3)
    assert report['rows'][0]['monthly_expense'] == '600'
    assert report['rows'][0]['reserve_required'] == '1800'
    assert 'short_observation_period' in report['warnings']
    for invalid in (0, 25, True, '6'):
        with pytest.raises(FinanceError):
            analyze(data, reserve_months=invalid)


def test_encrypted_durable_owner_scoped_snapshots_and_revision_conflicts(tmp_path):
    path = tmp_path/'finance.db'
    store = FinanceStore(sqlite_path=path, session_key='a'*44)
    store.initialize()
    revision = store.save(7, snapshot(), '')['revision']
    assert store.load(8) == {'revision': '', 'snapshot': None}
    with pytest.raises(FinanceError, match='finance_revision_conflict'):
        store.save(7, snapshot(), '')
    restarted = FinanceStore(sqlite_path=path, session_key='a'*44)
    assert restarted.load(7)['snapshot'] == snapshot()
    with sqlite3.connect(path) as conn:
        encrypted = conn.execute('SELECT encrypted FROM velia_finance_snapshot').fetchone()[0]
        assert 'Velia' not in encrypted and 'Test statement' not in encrypted
    for user, rev in ((8, revision), (7, 'stale')):
        with pytest.raises(FinanceError, match='finance_revision_conflict'):
            store.delete(user, rev)
    store.delete(7, revision)
    assert store.load(7)['snapshot'] is None


def test_routes_auth_origin_confirmation_conflicts_and_tenant_isolation(tmp_path):
    async def scenario():
        store = FinanceStore(sqlite_path=tmp_path/'routes.db', session_key='k'*44)
        async def session(request):
            user = request.headers.get('X-User')
            return SimpleNamespace(user_id=int(user)) if user else None
        app = web.Application()
        setup_finance_routes(app, session_for=session, same_origin=lambda r:r.headers.get('Origin')=='https://velia.example',
                             json_response=lambda data,status=200:web.json_response(data,status=status), store=store)
        async with TestServer(app) as server, ClientSession() as client:
            async def post(action, data, user='7', origin='https://velia.example'):
                headers={'Origin':origin}
                if user:headers['X-User']=user
                response = await client.post(server.make_url('/web-api/v1/finance/'+action), headers=headers, json=data)
                return response.status, await response.json()
            assert (await post('analyze',{},user=None))[0] == 401
            assert (await post('analyze',{},origin='https://evil.example'))[0] == 403
            assert (await post('analyze',{}))[1]['error'] == 'finance_snapshot_required'
            assert (await post('analyze',{'snapshot':snapshot()}))[0] == 200
            assert (await post('load',{}))[1]['snapshot'] is None
            assert (await post('save',{'snapshot':snapshot(),'revision':''}))[0] == 409
            status, saved = await post('save',{'snapshot':snapshot(),'revision':'','confirmed':True})
            assert status == 200
            assert (await post('load',{},user='8'))[1]['snapshot'] is None
            assert (await post('analyze',{},user='8'))[0] == 409
            assert (await post('analyze',{'user_id':7},user='8'))[0] == 400
            assert (await post('analyze',{}))[1]['saved'] is True
            assert (await post('delete',{'revision':saved['revision'],'confirmed':True},user='8'))[0] == 409
            assert (await post('delete',{'revision':saved['revision'],'confirmed':True}))[0] == 200
    asyncio.run(scenario())


def test_finance_planner_uses_selected_model_without_web_search(monkeypatch,tmp_path):
    from test_velia_web_chat import fixture, headers, login
    from desktop.guest_store import GuestStore
    async def scenario():
        async with fixture(monkeypatch,quantum_api=True,features={'chat_quantum':True},with_search=True,
                           guest_store=GuestStore(sqlite_path=tmp_path/'quota.db')) as (server,client,state):
            url=server.make_url('/web-api/v1/finance/plan')
            body={'model':'velia-flash','messages':[{'role':'user','content':'Верни JSON финансового анализа'}],'stream':True,'web_search':False}
            assert (await client.post(url,headers=headers(),json=body)).status==401
            cookie,_,_=await login(server,client)
            assert (await client.post(url,headers=headers(cookie,Origin='https://evil.example'),json=body)).status==403
            for model in ('velia-flash','velia-quantum','velia-pro'):
                response=await client.post(url,headers=headers(cookie),json={**body,'model':model})
                assert response.status==200 and '[DONE]' in await response.text()
            assert len(state['payloads'])==3 and not state['search_queries'] and not state['intent_payloads']
            for payload in state['payloads']:
                assert 1024 <= payload.get('max_tokens', payload.get('max_completion_tokens', 0)) <= 8192
    asyncio.run(scenario())
