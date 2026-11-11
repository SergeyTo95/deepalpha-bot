"""Read-only cash analysis. Explicit observations in, decimal calculations out.

No network, trading, transfers, inferred exchange rates or accounting-profit claims.
Snapshots describe closing balances, so period flows must not be added again.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext, ROUND_HALF_UP
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
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?', value):
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
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z][A-Z0-9]{1,11}', value):
        fail('finance_invalid_currency')
    return value


def validate_snapshot(raw):
    fields(raw, ('period_start', 'period_end', 'source', 'accounts', 'transactions'), ('budgets',))
    first, last = day(raw['period_start']), day(raw['period_end'])
    if not 1 <= (last-first).days+1 <= 366 or last > datetime.now(timezone.utc).date():
        fail('finance_invalid_period')
    source = text(raw['source'], 200)
    accounts, transactions, budgets = raw['accounts'], raw['transactions'], raw.get('budgets', [])
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 50 or not isinstance(transactions, list) or len(transactions) > 100 or not isinstance(budgets, list) or len(budgets) > 50:
        fail()
    ids, account_rows = set(), []
    for row in accounts:
        fields(row, ('id', 'project', 'currency', 'balance', 'restricted', 'due', 'liquid', 'as_of'))
        item = {**row, 'id': text(row['id']), 'project': text(row['project']), 'currency': currency(row['currency'])}
        if item['id'] in ids:
            fail('finance_duplicate_account')
        ids.add(item['id'])
        # Closing balances and immediately payable liabilities must share the reporting date.
        if day(row['as_of']) != last or type(row['liquid']) is not bool:
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
        if key in keys or key not in {(a['project'], a['currency']) for a in account_rows}:
            fail('finance_invalid_budget')
        keys.add(key)
        budget_rows.append({'project': key[0], 'currency': key[1], 'monthly_limit': format(amount(row['monthly_limit']), 'f')})
    return {'period_start': raw['period_start'], 'period_end': raw['period_end'], 'source': source,
            'accounts': account_rows, 'transactions': transaction_rows, 'budgets': budget_rows}


def number(value):
    return format(value.quantize(Decimal('.00000001'), rounding=ROUND_HALF_UP).normalize(), 'f')


def analyze(raw, *, reserve_months=6):
    if type(reserve_months) is not int or not 1 <= reserve_months <= 24:
        fail('finance_invalid_reserve')
    snapshot = validate_snapshot(raw)
    days = (day(snapshot['period_end']) - day(snapshot['period_start'])).days + 1
    accounts = {a['id']: a for a in snapshot['accounts']}
    groups = {}
    def group(project, code):
        return groups.setdefault((project, code), {k: Decimal(0) for k in
            ('assets', 'liquid', 'restricted', 'due', 'revenue', 'expense', 'transfer_in', 'transfer_out', 'financing_in', 'financing_out', 'investment_in', 'investment_out')})
    for a in accounts.values():
        for project in (a['project'], None):
            g = group(project, a['currency'])
            g['assets'] += amount(a['balance'])
            g['restricted'] += amount(a['restricted'])
            g['due'] += amount(a['due'])
            if a['liquid']:
                g['liquid'] += amount(a['balance']) - amount(a['restricted'])
    for t in snapshot['transactions']:
        a = accounts[t['account_id']]
        for project in (a['project'], None):
            group(project, a['currency'])[t['kind']] += amount(t['amount'])
    budgets = {(b['project'], b['currency']): amount(b['monthly_limit']) for b in snapshot['budgets']}
    rows = []
    with localcontext() as ctx:
        ctx.prec = 50
        for (project, code), g in sorted(groups.items(), key=lambda item: (item[0][0] or '', item[0][1])):
            monthly_expense = g['expense'] * 30 / days
            monthly_revenue = g['revenue'] * 30 / days
            burn = max(Decimal(0), monthly_expense - monthly_revenue)
            available = g['liquid'] - g['due']
            reserve = monthly_expense * reserve_months
            stress_burn = max(Decimal(0), monthly_expense * Decimal('1.2') - monthly_revenue * Decimal('.8'))
            limit = budgets.get((project, code))
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
            rows.append(row)
    warnings = ['user_provided_unverified', 'no_fx_conversion', 'cash_flow_is_not_accounting_profit',
                'forecast_assumes_constant_30_day_cash_flow', 'external_accounts_not_connected',
                'snapshot_may_be_incomplete', 'financing_investments_transfers_excluded_from_operating_cash_flow']
    if days < 30:
        warnings.append('short_observation_period')
    if not snapshot['transactions']:
        warnings.append('no_transactions_zero_flow_is_not_verified')
    if any(a['liquid'] and a['currency'] not in {'USD','EUR','TRY','GBP','RUB','BYN','CNY','CHF','AED','JPY','CAD','AUD'} for a in accounts.values()):
        warnings.append('user_classified_crypto_liquidity_has_market_risk')
    return {'period_start': snapshot['period_start'], 'period_end': snapshot['period_end'], 'days': days,
            'source': snapshot['source'], 'reserve_months': reserve_months, 'accounts': snapshot['accounts'],
            'rows': rows, 'warnings': warnings}
