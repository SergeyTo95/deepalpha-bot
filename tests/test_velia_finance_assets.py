from copy import deepcopy
from decimal import Decimal
import pytest
from desktop.finance import analyze, amount, currency, edit_snapshot, FinanceError
from desktop.finance_store import FinanceStore


def position(id, type, quantity, price, basis=None, code='USD', unit='unit'):
    observation = {'date':'2026-10-30','quantity':quantity,'currency':code,'source':'Synthetic user estimate'}
    if price is not None:
        observation['unit_price'] = price
    if basis is not None:
        observation.update(cost_basis=basis,cost_currency=code)
    return {'id':id,'project':'Личное','asset_type':type,'name':id,'symbol':id.upper(),
            'unit':unit,'observations':[observation]}


def portfolio_data():
    return {'period_start':'2026-10-01','period_end':'2026-10-30','source':'Synthetic data',
            'accounts':[],'transactions':[],'assets':[
                position('btc','crypto','0.1','70000','5000',unit='BTC'),
                position('gold','metal','10','70','500',unit='g'),
                position('stock','stock','2','300','500',unit='share'),
                position('house','real_estate','1','100000','80000',code='EUR',unit='object')],
            'fx_rates':[{'base':'EUR','quote':'USD','rate':'1.1','date':'2026-10-30','source':'Manual FX'}]}


def test_mixed_asset_classes_native_profit_and_explicit_base_currency():
    result = analyze(portfolio_data(),query={'base_currency':'USD'})
    assets = {a['id']:a for a in result['portfolio']['positions']}
    assert assets['btc']['market_value'] == '7000' and assets['btc']['unrealized_gain'] == '2000'
    assert assets['gold']['market_value'] == '700' and assets['gold']['unrealized_gain'] == '200'
    assert assets['stock']['market_value'] == '600' and assets['stock']['unrealized_gain'] == '100'
    assert assets['house']['unrealized_gain'] == '20000'
    total = result['portfolio']['base_total']
    assert total['value'] == '118300' and total['complete'] is True
    assert result['rows'] == [] and result['transactions'] == []
    assert 'unrealized_gain_is_not_cash_income' in result['portfolio']['warnings']


def test_all_supported_asset_classes_and_any_currency_code():
    data = portfolio_data()
    types = ('cash','crypto','metal','stock','bond','fund','real_estate','business','collectible','commodity','receivable','other')
    data['assets'] = [position(name,name,'1','2',code='CUSTOM.CREDIT') for name in types]
    result = analyze(data)['portfolio']
    assert len(result['positions']) == len(types)
    assert result['totals'][0]['value'] == '24' and result['totals'][0]['currency'] == 'CUSTOM.CREDIT'
    for code in ('TRY','BYN','INR','RSD','KWD','USDT:TRON','XAU','CUSTOM.CREDIT'):
        assert currency(code) == code


def test_crypto_precision_and_small_products_are_not_zeroed():
    data = portfolio_data()
    data['assets'] = [position('wei','crypto','0.000000000000000001','0.000000000000000001')]
    value = analyze(data)['portfolio']['positions'][0]['market_value']
    assert Decimal(value) == Decimal('1e-36')
    assert amount('0.'+'0'*35+'1') == Decimal('1e-36')
    data['assets'][0]['observations'][0]['unit_price'] = '0.'+'0'*35+'1'
    value = analyze(data)['portfolio']['positions'][0]['market_value']
    assert Decimal(value) == Decimal('1e-54')


def test_missing_quotes_fx_and_cost_basis_remain_unknown_not_zero_or_pegged():
    data = portfolio_data()
    data['fx_rates'] = []
    data['assets'].append(position('collectible','collectible','1',None))
    result = analyze(data,query={'base_currency':'USD'})['portfolio']
    assert result['base_total']['value'] is None and result['base_total']['complete'] is False
    assert result['base_total']['known_value'] == '8300'
    assert set(result['base_total']['missing_assets']) == {'house','collectible'}
    usd = next(row for row in result['totals'] if row['currency']=='USD')
    assert usd['value'] is None and usd['known_value'] == '8300'
    data['assets'] = [position('cash-usdt','cash','100','1',code='USDT')]
    result = analyze(data,query={'base_currency':'USD'})['portfolio']
    assert result['base_total']['value'] is None
    data['fx_rates'] = [{'base':'USDT','quote':'USD','rate':'0.99','date':'2026-10-30','source':'Explicit estimate'}]
    assert analyze(data,query={'base_currency':'USD'})['portfolio']['base_total']['value'] == '99'
    assert analyze(data)['portfolio']['positions'][0]['unrealized_gain'] is None
    unpriced = position('rare-book','collectible','1',None)
    unpriced['observations'][0].pop('currency')
    data['assets'].append(unpriced)
    result = analyze(data,query={'base_currency':'USD'})['portfolio']
    assert next(p for p in result['positions'] if p['id']=='rare-book')['currency'] is None
    assert result['base_total']['complete'] is False


def test_date_selection_does_not_use_future_quotes_or_rates():
    data = portfolio_data()
    data['assets'] = [position('gold','metal','10','70','500',unit='g')]
    data['assets'][0]['observations'].insert(0,{'date':'2026-10-01','quantity':'10','currency':'EUR',
        'unit_price':'60','cost_basis':'500','cost_currency':'EUR','source':'Earlier estimate'})
    data['assets'][0]['observations'][1]['currency'] = 'EUR'
    data['assets'][0]['observations'][1]['cost_currency'] = 'EUR'
    data['fx_rates'].insert(0,{'base':'USD','quote':'EUR','rate':'0.8','date':'2026-10-01','source':'Earlier inverse quote'})
    earlier = analyze(data,query={'period_start':'2026-10-02','period_end':'2026-10-15','base_currency':'USD'})['portfolio']
    assert earlier['positions'][0]['market_value'] == '600' and earlier['positions'][0]['stale'] is True
    assert earlier['base_total']['value'] == '750' and earlier['base_total']['rates'][0]['date'] == '2026-10-01'
    end = analyze(data,query={'period_start':'2026-10-02','period_end':'2026-10-30'})['portfolio']['positions'][0]
    assert end['holding_value_change'] == '100' and end['baseline_as_of'] == '2026-10-01'
    before = analyze(data,query={'period_start':'2026-09-01','period_end':'2026-09-30'})['portfolio']
    assert before['positions'] == []


def test_same_ticker_different_identity_stays_separate_and_units_are_immutable():
    data = portfolio_data()
    a = position('exchange-a','stock','1','100',unit='share')
    b = position('exchange-b','stock','1','200',unit='share')
    a.update(symbol='ABC',identity={'venue':'A','isin':'INSTRUMENT-A'})
    b.update(symbol='ABC',identity={'venue':'B','isin':'INSTRUMENT-B'})
    data['assets'] = [a,b]
    assert len(analyze(data)['portfolio']['positions']) == 2
    with pytest.raises(FinanceError,match='identity_conflict'):
        edit_snapshot(data, {'type':'upsert_asset','holding':{k:a[k] for k in ('id','project','asset_type','name','symbol')}|{'unit':'g'},
            'observation':{'date':'2026-10-30','quantity':'1','currency':'USD','unit_price':'100','source':'User'}},time_zone='UTC',operation_id='bad-unit')


def test_asset_mutations_preserve_history_cash_and_other_positions(tmp_path):
    add = {'type':'upsert_asset','holding':{'project':'Личное','asset_type':'crypto','name':'Bitcoin','symbol':'BTC','unit':'BTC'},
           'observation':{'date':'2026-10-01','quantity':'0.1','currency':'USD','unit_price':'60000','cost_basis':'5000','cost_currency':'USD','source':'User'}}
    first, change = edit_snapshot(None,add,time_zone='UTC',operation_id='btc-position')
    assert change['after']['quantity'] == '0.1' and first['transactions'] == []
    updated, _ = edit_snapshot(first, {'type':'set_asset_observation','asset_id':'btc-position',
        'observation':{'date':'2026-10-30','unit_price':'70000','source':'User'}},time_zone='UTC',operation_id='price-update')
    report = analyze(updated,query={'period_start':'2026-10-02','period_end':'2026-10-30'})['portfolio']['positions'][0]
    assert report['unrealized_gain'] == '2000' and report['holding_value_change'] == '1000'
    assert len(updated['assets'][0]['observations']) == 2 and updated['transactions'] == []
    changed, _ = edit_snapshot(updated,{'type':'set_asset_observation','asset_id':'btc-position',
        'observation':{'date':'2026-10-30','quantity':'0.2','source':'Corrected quantity'}},time_zone='UTC',operation_id='qty')
    # Changed quantity makes old total cost unknown; a same-date quote still describes the same unit.
    assert analyze(changed)['portfolio']['positions'][0]['unrealized_gain'] is None
    assert analyze(changed)['portfolio']['positions'][0]['quantity_changed'] is False  # all-time has no opening observation
    deleted, _ = edit_snapshot(updated,{'type':'delete_asset_observation','asset_id':'btc-position','date':'2026-10-30'},time_zone='UTC',operation_id='remove-price')
    assert len(deleted['assets'][0]['observations']) == 1
    store = FinanceStore(sqlite_path=tmp_path/'portfolio.db',session_key='p'*44)
    store.initialize(); store.save(7,updated,'')
    assert store.load(8)['snapshot'] is None
    assert analyze(store.load(7)['snapshot'])['portfolio']['positions'][0]['market_value'] == '7000'
    removed, _ = edit_snapshot(updated,{'type':'delete_asset','asset_id':'btc-position'},time_zone='UTC',operation_id='remove-asset')
    assert analyze(removed)['portfolio']['positions'] == []


@pytest.mark.parametrize('bad', ['-1', 'NaN', 'Infinity', 1.5])
def test_invalid_asset_quantities_are_rejected(bad):
    data = portfolio_data()
    data['assets'][0]['observations'][0]['quantity'] = bad
    with pytest.raises(FinanceError):analyze(data)


def test_invalid_rates_dates_duplicate_ids_and_future_observations_fail():
    for kind in ('duplicate','rate-zero','future','basis-currency'):
        data = portfolio_data()
        if kind=='duplicate':data['assets'].append(deepcopy(data['assets'][0]))
        elif kind=='rate-zero':data['fx_rates'][0]['rate']='0'
        elif kind=='future':data['assets'][0]['observations'][0]['date']='2030-01-01'
        else:data['assets'][0]['observations'][0].pop('cost_currency')
        with pytest.raises(FinanceError):analyze(data)


def test_asset_and_project_filters_only_select_matching_positions():
    data = portfolio_data()
    assert len(analyze(data,query={'asset_type':'crypto'})['portfolio']['positions']) == 1
    assert analyze(data,query={'asset_id':'house'})['portfolio']['positions'][0]['currency'] == 'EUR'
    assert len(analyze(data,query={'asset_name':'GOLD'})['portfolio']['positions']) == 1
    assert analyze(data,query={'project':'missing'})['portfolio']['positions'] == []
    data['accounts'] = [{'id':'bank','project':'Личное','currency':'USD'}]
    data['transactions'] = [{'id':'salary','account_id':'bank','date':'2026-10-30','kind':'revenue','amount':'1000'}]
    assert analyze(data)['rows'][0]['operating_cash_flow'] == '1000'
    assert analyze(data,query={'asset_type':'stock'})['rows'] == []


def test_purchase_unit_price_is_multiplied_by_quantity_on_server():
    data = portfolio_data()
    data['assets'] = [position('shares','stock','2','300',unit='share')]
    data['assets'][0]['observations'][0].update(unit_cost='250',cost_currency='USD')
    end = analyze(data)['portfolio']['positions'][0]
    assert end['cost_basis'] == '500' and end['unrealized_gain'] == '100'
    data['assets'][0]['observations'][0]['cost_basis'] = '500'
    with pytest.raises(FinanceError,match='finance_invalid_cost_basis'):
        analyze(data)
