"""Read-only cash analysis. Explicit observations in, decimal calculations out.

No network, trading, transfers, inferred exchange rates or accounting-profit claims.
Snapshots describe closing balances, so period flows must not be added again.
"""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from copy import deepcopy
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re


class FinanceError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def fail(code='finance_invalid_data'):
    raise FinanceError(code)


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        fail()
    return value


def text(value, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        fail()
    return value.strip()


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,36})?', value):
        fail('finance_invalid_amount')
    return Decimal(value)


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        fail('finance_invalid_period')
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail('finance_invalid_period')


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z][A-Z0-9._:-]{1,31}', value):
        fail('finance_invalid_currency')
    return value


def zone(value='UTC'):
    if not isinstance(value, str) or len(value) > 100:
        fail('finance_invalid_timezone')
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        fail('finance_invalid_timezone')


def today(time_zone='UTC', now=None):
    return (now or datetime.now(timezone.utc)).astimezone(zone(time_zone)).date()


def validate_snapshot(raw):
    fields(raw, ('period_start', 'period_end', 'source', 'accounts', 'transactions'), ('budgets', 'time_zone', 'assets', 'fx_rates'))
    first, last = day(raw['period_start']), day(raw['period_end'])
    if first > last or last > today(raw.get('time_zone', 'UTC')):
        fail('finance_invalid_period')
    source = text(raw['source'], 200)
    accounts, transactions, budgets = raw['accounts'], raw['transactions'], raw.get('budgets', [])
    if not isinstance(accounts, list) or len(accounts) > 50 or not isinstance(transactions, list) or len(transactions) > 5000 or not isinstance(budgets, list) or len(budgets) > 50:
        fail()
    ids, account_rows = set(), []
    for row in accounts:
        balance_fields = ('balance', 'restricted', 'due', 'liquid', 'as_of')
        fields(row, ('id', 'project', 'currency'), balance_fields)
        item = {**row, 'id': text(row['id']), 'project': text(row['project']), 'currency': currency(row['currency'])}
        if item['id'] in ids:
            fail('finance_duplicate_account')
        ids.add(item['id'])
        if any(key in row for key in balance_fields):
            if not all(key in row for key in balance_fields) or not first <= day(row['as_of']) <= last or type(row['liquid']) is not bool:
                fail('finance_balance_date_mismatch')
            for key in ('balance', 'restricted', 'due'):
                item[key] = format(amount(row[key]), 'f')
            if amount(item['restricted']) > amount(item['balance']):
                fail('finance_invalid_amount')
        account_rows.append(item)
    indexed = {row['id']: row for row in account_rows}
    ids, transaction_rows = set(), []
    for row in transactions:
        fields(row, ('id', 'account_id', 'date', 'kind', 'amount'), ('category',))
        item = {**row, 'id': text(row['id']), 'account_id': text(row['account_id'])}
        if item['id'] in ids:
            fail('finance_duplicate_transaction')
        ids.add(item['id'])
        if item['account_id'] not in indexed or not first <= day(row['date']) <= last:
            fail('finance_invalid_transaction')
        if row['kind'] not in ('revenue', 'expense', 'transfer_in', 'transfer_out', 'financing_in', 'financing_out', 'investment_in', 'investment_out'):
            fail('finance_invalid_transaction')
        item['amount'] = format(amount(row['amount']), 'f')
        if 'category' in row:
            item['category'] = text(row['category'])
        transaction_rows.append(item)
    keys, budget_rows = set(), []
    for row in budgets:
        fields(row, ('project', 'currency', 'monthly_limit'))
        key = (text(row['project']), currency(row['currency']))
        if key in keys:
            fail('finance_invalid_budget')
        keys.add(key)
        budget_rows.append({'project': key[0], 'currency': key[1], 'monthly_limit': format(amount(row['monthly_limit']), 'f')})
    result = {'period_start': raw['period_start'], 'period_end': raw['period_end'], 'source': source,
              'accounts': account_rows, 'transactions': transaction_rows, 'budgets': budget_rows}
    if 'time_zone' in raw:
        zone(raw['time_zone'])
        result['time_zone'] = raw['time_zone']
    if 'assets' in raw or 'fx_rates' in raw:
        from desktop.finance_assets import validate_assets
        result['assets'], result['fx_rates'] = validate_assets(raw.get('assets', []), raw.get('fx_rates', []), first, last)
    return result


def number(value):
    with localcontext() as ctx:
        ctx.prec = 200
        rounded = value.quantize(Decimal('1e-36'), rounding=ROUND_HALF_UP)
        result = format(value if value and not rounded else rounded, 'f')
        return result.rstrip('0').rstrip('.') if '.' in result else result


def decimal_calculation(function):
    @wraps(function)
    def calculate(*args, **kwargs):
        with localcontext() as ctx:
            ctx.prec = 200
            return function(*args, **kwargs)
    return calculate


def period_window(query, snapshot, *, now=None):
    query = {} if query is None else query
    fields(query, (), ('period_start', 'period_end', 'preset', 'project', 'category', 'time_zone', 'base_currency', 'asset_type', 'asset_id', 'asset_name'))
    tz = query.get('time_zone', snapshot.get('time_zone', 'UTC'))
    current = today(tz, now)
    preset = query.get('preset', 'all')
    if preset not in ('all', 'today', 'yesterday', 'week', 'month', 'year', 'custom'):
        fail('finance_invalid_period')
    if 'period_start' in query or 'period_end' in query:
        if preset not in ('all', 'custom') or 'period_start' not in query or 'period_end' not in query:
            fail('finance_invalid_period')
        first, last = day(query['period_start']), day(query['period_end'])
    elif preset == 'custom':
        fail('finance_invalid_period')
    elif preset == 'all':
        first, last = day(snapshot['period_start']), day(snapshot['period_end'])
    elif preset == 'yesterday':
        first = last = current - timedelta(days=1)
    else:
        last = current
        first = {'today': current, 'week': current-timedelta(days=current.weekday()),
                 'month': current.replace(day=1), 'year': current.replace(month=1, day=1)}[preset]
    if first > last or last > current:
        fail('finance_invalid_period')
    for key in ('project', 'category', 'asset_id', 'asset_name'):
        if key in query:
            text(query[key])
    if 'base_currency' in query:
        currency(query['base_currency'])
    if 'asset_type' in query:
        from desktop.finance_assets import ASSET_TYPES
        if query['asset_type'] not in ASSET_TYPES:
            fail('finance_invalid_asset_type')
    return first, last, tz


@decimal_calculation
def analyze(raw, *, reserve_months=6, query=None, now=None):
    if type(reserve_months) is not int or not 1 <= reserve_months <= 24:
        fail('finance_invalid_reserve')
    snapshot = validate_snapshot(raw)
    first, last, tz = period_window(query, snapshot, now=now)
    query = query or {}
    days = (last-first).days + 1
    asset_only = any(query.get(key) for key in ('asset_type', 'asset_id', 'asset_name'))
    accounts = {a['id']: a for a in snapshot['accounts'] if not asset_only and (not query.get('project') or a['project'] == query['project'])}
    transactions = [t for t in snapshot['transactions'] if t['account_id'] in accounts and first <= day(t['date']) <= last
                    and (not query.get('category') or t.get('category') == query['category'])]
    groups = {}
    balance_known = {}
    operation_counts = {}
    def group(project, code):
        return groups.setdefault((project, code), {k: Decimal(0) for k in
            ('assets', 'liquid', 'restricted', 'due', 'revenue', 'expense', 'transfer_in', 'transfer_out', 'financing_in', 'financing_out', 'investment_in', 'investment_out')})
    for a in accounts.values():
        for project in (a['project'], None):
            g = group(project, a['currency'])
            key = (project, a['currency'])
            known = 'balance' in a and day(a['as_of']) == last and not query.get('category')
            balance_known[key] = balance_known.get(key, True) and known
            if known:
                g['assets'] += amount(a['balance'])
                g['restricted'] += amount(a['restricted'])
                g['due'] += amount(a['due'])
                if a['liquid']:
                    g['liquid'] += amount(a['balance']) - amount(a['restricted'])
    daily = {}
    for t in transactions:
        a = accounts[t['account_id']]
        for project in (a['project'], None):
            group(project, a['currency'])[t['kind']] += amount(t['amount'])
            key = (project, a['currency'])
            operation_counts[key] = operation_counts.get(key, 0) + 1
        d = daily.setdefault((t['date'], a['currency']), {'revenue': Decimal(0), 'expense': Decimal(0)})
        if t['kind'] in d:
            d[t['kind']] += amount(t['amount'])
    budgets = {(b['project'], b['currency']): amount(b['monthly_limit']) for b in snapshot['budgets']}
    rows = []
    with localcontext() as ctx:
        ctx.prec = 200
        for (project, code), g in sorted(groups.items(), key=lambda item: (item[0][0] or '', item[0][1])):
            monthly_expense = g['expense'] * 30 / days
            monthly_revenue = g['revenue'] * 30 / days
            burn = max(Decimal(0), monthly_expense - monthly_revenue)
            available = g['liquid'] - g['due']
            reserve = monthly_expense * reserve_months
            stress_burn = max(Decimal(0), monthly_expense * Decimal('1.2') - monthly_revenue * Decimal('.8'))
            limit = None if query.get('category') else budgets.get((project, code))
            row = {'project': project, 'currency': code, **{k: number(v) for k, v in g.items()},
                   'available': number(available), 'operating_cash_flow': number(g['revenue'] - g['expense']),
                   'monthly_expense': number(monthly_expense), 'monthly_revenue': number(monthly_revenue),
                   'monthly_burn': number(burn), 'reserve_required': number(reserve),
                   'reserve_gap': number(max(Decimal(0), reserve-available)),
                   'runway_months': number(max(Decimal(0), available)/burn) if burn else None,
                   'stress_runway_months': number(max(Decimal(0), available)/stress_burn) if stress_burn else None,
                   'forecast': {str(m): number(available + (monthly_revenue-monthly_expense)*m) for m in (3, 6, 12)},
                   'monthly_budget': number(limit) if limit is not None else None,
                   'budget_excess': number(max(Decimal(0), monthly_expense-limit)) if limit is not None else None}
            net = g['revenue'] - g['expense']
            row.update(operation_count=operation_counts.get((project, code), 0),
                       result='no_data' if not operation_counts.get((project, code)) else 'plus' if net > 0 else 'minus' if net < 0 else 'zero',
                       balance_as_of=last.isoformat() if balance_known[(project, code)] else None)
            if not balance_known[(project, code)]:
                for key in ('assets', 'liquid', 'restricted', 'due', 'available', 'reserve_gap', 'runway_months', 'stress_runway_months'):
                    row[key] = None
                row['forecast'] = None
            rows.append(row)
    warnings = ['user_provided_unverified', 'no_fx_conversion', 'cash_flow_is_not_accounting_profit',
                'forecast_assumes_constant_30_day_cash_flow', 'external_accounts_not_connected',
                'snapshot_may_be_incomplete', 'financing_investments_transfers_excluded_from_operating_cash_flow']
    if days < 30:
        warnings.append('short_observation_period')
    if not transactions:
        warnings.append('no_transactions_zero_flow_is_not_verified')
    if first < day(snapshot['period_start']) or last > day(snapshot['period_end']):
        warnings.append('requested_period_exceeds_recorded_coverage')
    if any(not value for value in balance_known.values()):
        warnings.append('closing_balance_missing_for_selected_period')
    if any(a.get('liquid') and a['currency'].split(':')[0] in {'BTC','ETH','TON','USDT','USDC','SOL','BNB','XRP','ADA','DOGE','LTC','TRX','DAI','XMR','DOT','AVAX'} for a in accounts.values()):
        warnings.append('user_classified_crypto_liquidity_has_market_risk')
    from desktop.finance_assets import portfolio
    return {'period_start': first.isoformat(), 'period_end': last.isoformat(), 'days': days, 'time_zone': tz,
            'source': snapshot['source'], 'reserve_months': reserve_months, 'accounts': list(accounts.values()),
            'filters': {k: query[k] for k in ('project', 'category', 'base_currency', 'asset_type', 'asset_id', 'asset_name') if k in query},
            'portfolio': portfolio(snapshot, first, last, query) if not query.get('category') else None,
            'transactions': sorted(transactions, key=lambda t: (t['date'], t['id']), reverse=True)[:100],
            'transaction_count': len(transactions), 'rows': rows, 'warnings': warnings,
            'daily': [{'date': d, 'currency': c, **{k: number(v) for k, v in values.items()},
                       'net': number(values['revenue']-values['expense'])} for (d, c), values in sorted(daily.items())]}


def edit_snapshot(current, edit, *, time_zone='UTC', operation_id):
    """Apply one reviewed local accounting edit. Never adjust observed bank balances."""
    fields(edit, ('type',), ('transaction', 'transaction_id', 'changes', 'account', 'account_id', 'budget',
                            'holding', 'asset_id', 'observation', 'date', 'rate'))
    operation_id = text(operation_id, 100)
    zone(time_zone)
    if edit['type'] in ('upsert_asset', 'set_asset_observation', 'delete_asset', 'delete_asset_observation', 'set_fx_rate'):
        from desktop.finance_assets import edit_assets
        snapshot, change = edit_assets(current, edit, time_zone=time_zone, operation_id=operation_id)
        return validate_snapshot(snapshot), change
    doc = deepcopy(current) if current else None
    kind = edit['type']
    before = None
    after = None
    if kind == 'add_transaction':
        transaction = deepcopy(edit.get('transaction'))
        fields(transaction, ('account_id', 'date', 'kind', 'amount'), ('category',))
        transaction['id'] = operation_id
        when = day(transaction['date'])
        if doc is None:
            doc = {'period_start': when.isoformat(), 'period_end': when.isoformat(), 'source': 'Данные пользователя',
                   'time_zone': time_zone, 'accounts': [], 'transactions': [], 'budgets': []}
        if any(t['id'] == operation_id for t in doc['transactions']):
            fail('finance_duplicate_transaction')
        if not any(a['id'] == transaction['account_id'] for a in doc['accounts']):
            account = edit.get('account')
            fields(account, ('id', 'project', 'currency'))
            if account['id'] != transaction['account_id']:
                fail('finance_invalid_transaction')
            doc['accounts'].append(deepcopy(account))
        doc['transactions'].append(transaction)
        after = transaction
    elif kind in ('update_transaction', 'delete_transaction'):
        if doc is None:
            fail('finance_snapshot_required')
        transaction_id = text(edit.get('transaction_id'))
        found = next((t for t in doc['transactions'] if t['id'] == transaction_id), None)
        if found is None:
            fail('finance_transaction_not_found')
        before = deepcopy(found)
        if kind == 'delete_transaction':
            doc['transactions'].remove(found)
        else:
            changes = fields(edit.get('changes'), (), ('date', 'kind', 'amount', 'category', 'account_id'))
            if not changes:
                fail()
            if 'account_id' in changes and not any(a['id'] == changes['account_id'] for a in doc['accounts']):
                account = fields(edit.get('account'), ('id', 'project', 'currency'))
                if account['id'] != changes['account_id']:
                    fail('finance_invalid_transaction')
                doc['accounts'].append(deepcopy(account))
            found.update(changes)
            after = found
    elif kind == 'update_account':
        if doc is None:
            fail('finance_snapshot_required')
        found = next((a for a in doc['accounts'] if a['id'] == edit.get('account_id')), None)
        if found is None:
            fail('finance_account_not_found')
        changes = fields(edit.get('changes'), (), ('project', 'balance', 'restricted', 'due', 'liquid', 'as_of'))
        if not changes:
            fail()
        before = deepcopy(found)
        found.update(changes)
        after = found
    elif kind == 'set_budget':
        if doc is None:
            fail('finance_snapshot_required')
        budget = fields(edit.get('budget'), ('project', 'currency', 'monthly_limit'))
        budgets = doc.setdefault('budgets', [])
        before = next((b for b in budgets if (b['project'], b['currency']) == (budget['project'], budget['currency'])), None)
        if before is not None:
            budgets.remove(before)
        budgets.append(deepcopy(budget))
        after = budget
    else:
        fail('finance_invalid_edit')
    # Preserve coverage and all unrelated history; extend it for newly dated observations.
    dates = [day(doc['period_start']), day(doc['period_end'])]
    dates += [day(t['date']) for t in doc['transactions']]
    dates += [day(a['as_of']) for a in doc['accounts'] if 'as_of' in a]
    doc['period_start'], doc['period_end'] = min(dates).isoformat(), max(dates).isoformat()
    # Dates are calendar dates in the user's selected zone, including local midnight.
    doc['time_zone'] = time_zone
    validated = validate_snapshot(doc)
    def describe(record, source):
        if record is None or 'account_id' not in record:
            return record
        account = next(a for a in source['accounts'] if a['id'] == record['account_id'])
        return {**record, 'project': account['project'], 'currency': account['currency']}
    return validated, {'type': kind, 'before': describe(before, current), 'after': describe(after, validated)}
