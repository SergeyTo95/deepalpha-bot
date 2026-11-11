"""User-observed positions and FX. No quotes, trades, implied pegs or cash flows."""
from copy import deepcopy
from decimal import Decimal, localcontext
from desktop.finance import amount, currency, day, fail, fields, number, text

ASSET_TYPES = ('cash', 'crypto', 'metal', 'stock', 'bond', 'fund', 'real_estate',
               'business', 'collectible', 'commodity', 'receivable', 'other')


def validate_assets(raw, rates, first, last):
    if not isinstance(raw, list) or len(raw) > 200 or not isinstance(rates, list) or len(rates) > 2000:
        fail('finance_asset_limit')
    assets, seen, observations = [], set(), 0
    for record in raw:
        fields(record, ('id', 'project', 'asset_type', 'name', 'symbol', 'unit', 'observations'), ('identity',))
        result = {key: text(record[key]) for key in ('id', 'project', 'name', 'symbol', 'unit')}
        if result['id'] in seen:
            fail('finance_duplicate_asset')
        seen.add(result['id'])
        if record['asset_type'] not in ASSET_TYPES:
            fail('finance_invalid_asset_type')
        result['asset_type'] = record['asset_type']
        if 'identity' in record:
            identity = fields(record['identity'], (), ('network', 'contract', 'venue', 'isin', 'reference'))
            result['identity'] = {key: text(value, 200) for key, value in identity.items()}
        rows = record['observations']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            fail('finance_asset_limit')
        observations += len(rows)
        if observations > 5000:
            fail('finance_asset_limit')
        dates, normalized = set(), []
        for row in rows:
            fields(row, ('date', 'quantity', 'source'), ('currency', 'unit_price', 'cost_basis', 'unit_cost', 'cost_currency'))
            when = day(row['date'])
            if when in dates or not first <= when <= last:
                fail('finance_invalid_asset_date')
            dates.add(when)
            entry = {'date': row['date'], 'quantity': format(amount(row['quantity']), 'f'), 'source': text(row['source'], 200)}
            if 'currency' in row:
                entry['currency'] = currency(row['currency'])
            if 'unit_price' in row:
                if 'currency' not in row:
                    fail('finance_invalid_currency')
                entry['unit_price'] = format(amount(row['unit_price']), 'f')
            if ('cost_basis' in row and 'unit_cost' in row) or (('cost_basis' in row or 'unit_cost' in row) != ('cost_currency' in row)):
                fail('finance_invalid_cost_basis')
            if 'cost_basis' in row:
                entry['cost_basis'] = format(amount(row['cost_basis']), 'f')
                entry['cost_currency'] = currency(row['cost_currency'])
                if amount(entry['quantity']) == 0 and amount(entry['cost_basis']) != 0:
                    fail('finance_invalid_cost_basis')
            if 'unit_cost' in row:
                entry['unit_cost'] = format(amount(row['unit_cost']), 'f')
                entry['cost_currency'] = currency(row['cost_currency'])
            normalized.append(entry)
        result['observations'] = sorted(normalized, key=lambda row: row['date'])
        assets.append(result)
    normalized_rates, seen = [], set()
    for row in rates:
        fields(row, ('base', 'quote', 'rate', 'date', 'source'))
        base, quote = currency(row['base']), currency(row['quote'])
        when = day(row['date'])
        key = (*sorted((base, quote)), row['date'])
        if base == quote or amount(row['rate']) <= 0 or not first <= when <= last or key in seen:
            fail('finance_invalid_fx_rate')
        seen.add(key)
        normalized_rates.append({**row, 'base': base, 'quote': quote,
                                 'rate': format(amount(row['rate']), 'f'), 'source': text(row['source'], 200)})
    return assets, sorted(normalized_rates, key=lambda row: (row['date'], row['base'], row['quote']))


def observed(asset, when, *, inclusive=True):
    return next((row for row in reversed(asset['observations'])
                 if day(row['date']) <= when and (inclusive or day(row['date']) < when)), None)


def rate_for(rates, base, quote, when):
    if base == quote:
        return Decimal(1), None
    candidates = [row for row in rates if {row['base'], row['quote']} == {base, quote} and day(row['date']) <= when]
    if not candidates:
        return None, None
    record = max(candidates, key=lambda row: row['date'])
    rate = amount(record['rate'])
    return (rate if record['base'] == base else Decimal(1)/rate), {**record, 'stale': day(record['date']) != when}


def exact(value):
    result = format(value, 'f')
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    return '0' if result in ('-0', '') else result


def market_value(observation):
    if 'currency' not in observation:
        return None
    if amount(observation['quantity']) == 0:
        return Decimal(0)
    return amount(observation['quantity']) * amount(observation['unit_price']) if 'unit_price' in observation else None


def portfolio(snapshot, first, last, query):
    positions, groups = [], {}
    base = query.get('base_currency')
    base_known, missing, used_rates = Decimal(0), [], []
    with localcontext() as ctx:
        ctx.prec = 200
        for asset in snapshot.get('assets', []):
            if query.get('project') and asset['project'] != query['project'] or query.get('asset_type') and asset['asset_type'] != query['asset_type']:
                continue
            if query.get('asset_id') and asset['id'] != query['asset_id'] or query.get('asset_name') and query['asset_name'].casefold() not in asset['name'].casefold():
                continue
            end = observed(asset, last)
            if end is None:
                continue
            start = observed(asset, first, inclusive=False)
            value = market_value(end)
            previous = market_value(start) if start else None
            basis = amount(end['cost_basis']) if 'cost_basis' in end else amount(end['quantity'])*amount(end['unit_cost']) if 'unit_cost' in end else None
            gain = value - basis if basis is not None and value is not None and end.get('cost_currency') == end.get('currency') else None
            change = value - previous if value is not None and previous is not None and end.get('currency') == start.get('currency') else None
            code = end.get('currency')
            group = groups.setdefault(code, {'known': Decimal(0), 'missing': 0, 'positions': 0})
            group['positions'] += 1
            if value is None:
                group['missing'] += 1
            else:
                group['known'] += value
            position = {key: deepcopy(asset[key]) for key in asset if key != 'observations'}
            position.update(quantity=end['quantity'], unit_price=end.get('unit_price'), currency=code,
                as_of=end['date'], source=end['source'], stale=day(end['date']) != last,
                market_value=exact(value) if value is not None else None,
                cost_basis=exact(basis) if basis is not None else None, unit_cost=end.get('unit_cost'), cost_currency=end.get('cost_currency'),
                unrealized_gain=exact(gain) if gain is not None else None,
                unrealized_percent=number(gain/basis*100) if gain is not None and basis else None,
                baseline_as_of=start['date'] if start else None,
                holding_value_change=exact(change) if change is not None else None,
                quantity_changed=start is not None and amount(start['quantity']) != amount(end['quantity']))
            if base:
                rate, record = rate_for(snapshot.get('fx_rates', []), code, base, last) if code else (None, None)
                converted = value * rate if value is not None and rate is not None else None
                position['base_value'] = exact(converted) if converted is not None else None
                position['base_currency'] = base
                if record and record not in used_rates:
                    used_rates.append(record)
                if converted is None:
                    missing.append(asset['id'])
                else:
                    base_known += converted
            positions.append(position)
        totals = [{'currency': code, 'known_value': exact(g['known']),
                   'value': exact(g['known']) if not g['missing'] else None,
                   'unpriced': g['missing'], 'position_count': g['positions']} for code, g in sorted(groups.items(), key=lambda item: item[0] or '')]
        return {'as_of': last.isoformat(), 'positions': positions, 'totals': totals,
                'base_total': {'currency': base, 'value': exact(base_known) if positions and not missing else None,
                               'known_value': exact(base_known), 'complete': bool(positions) and not missing,
                               'missing_assets': missing, 'rates': used_rates} if base else None,
                'warnings': ['manual_valuations_not_live_quotes', 'portfolio_excludes_cash_accounts_to_avoid_double_counting',
                             'holding_value_change_is_not_investment_return', 'unrealized_gain_is_not_cash_income']}


def edit_assets(current, edit, *, time_zone, operation_id):
    kind = edit['type']
    allowed = ('holding', 'asset_id', 'observation', 'date', 'rate')
    fields(edit, ('type',), allowed)
    doc = deepcopy(current) if current else {'period_start': '', 'period_end': '', 'source': 'Данные пользователя',
           'time_zone': time_zone, 'accounts': [], 'transactions': [], 'budgets': []}
    assets, rates = doc.setdefault('assets', []), doc.setdefault('fx_rates', [])
    before = after = None
    def view(asset, observation=None, *, all_history=False):
        if asset is None or observation is None:
            return None
        value = {key: asset[key] for key in ('id', 'project', 'asset_type', 'name', 'symbol', 'unit')}
        value.update(asset.get('identity', {}))
        value.update(observation)
        if all_history:
            value['observation_count'] = len(asset['observations'])
        return value
    if kind in ('upsert_asset', 'set_asset_observation'):
        if kind == 'upsert_asset':
            holding = fields(edit.get('holding'), ('project', 'asset_type', 'name', 'symbol', 'unit'), ('id', 'identity'))
            asset_id = text(holding.get('id', operation_id))
        else:
            asset_id = text(edit.get('asset_id'))
            holding = None
        asset = next((a for a in assets if a['id'] == asset_id), None)
        old_asset = deepcopy(asset)
        if asset is None:
            if holding is None:
                fail('finance_asset_not_found')
            asset = {**holding, 'id': asset_id, 'observations': []}
            assets.append(asset)
        elif holding and any(holding.get(key, asset.get(key)) != asset.get(key) for key in ('symbol', 'unit', 'asset_type', 'identity')):
            # Keep the instrument and unit fixed so old observations cannot be relabelled.
            fail('finance_asset_identity_conflict')
        elif holding:
            asset.update({key: holding[key] for key in ('project', 'name')})
        observation = deepcopy(edit.get('observation'))
        fields(observation, ('date', 'source'), ('quantity', 'currency', 'unit_price', 'cost_basis', 'unit_cost', 'cost_currency'))
        previous = observed({'observations': sorted(asset['observations'], key=lambda row: row['date'])}, day(observation['date']))
        if previous:
            observation.setdefault('quantity', previous['quantity'])
            if 'currency' in previous:
                observation.setdefault('currency', previous['currency'])
            if amount(observation['quantity']) == amount(previous['quantity']):
                if 'cost_basis' not in observation and 'unit_cost' not in observation:
                    for key in ('cost_basis', 'unit_cost', 'cost_currency'):
                        if key in previous:
                            observation.setdefault(key, previous[key])
                elif 'cost_currency' in previous:
                    observation.setdefault('cost_currency', previous['cost_currency'])
            if observation['date'] == previous['date'] and observation.get('currency') == previous.get('currency') and 'unit_price' in previous:
                observation.setdefault('unit_price', previous['unit_price'])
        asset['observations'] = [row for row in asset['observations'] if row['date'] != observation['date']] + [observation]
        before = view(old_asset, next((r for r in old_asset['observations'] if r['date'] == observation['date']), None)) if old_asset else None
        after = view(asset, observation)
    elif kind == 'delete_asset_observation':
        asset = next((a for a in assets if a['id'] == edit.get('asset_id')), None)
        if asset is None:
            fail('finance_asset_not_found')
        date = day(edit.get('date')).isoformat()
        before = view(asset, next((r for r in asset['observations'] if r['date'] == date), None))
        filtered = [row for row in asset['observations'] if row['date'] != date]
        if len(filtered) == len(asset['observations']):
            fail('finance_asset_observation_not_found')
        if filtered:
            asset['observations'] = filtered
        else:
            assets.remove(asset)
    elif kind == 'delete_asset':
        asset = next((a for a in assets if a['id'] == edit.get('asset_id')), None)
        if asset is None:
            fail('finance_asset_not_found')
        before = view(asset, asset['observations'][-1], all_history=True)
        assets.remove(asset)
    elif kind == 'set_fx_rate':
        rate = deepcopy(edit.get('rate'))
        fields(rate, ('base', 'quote', 'rate', 'date', 'source'))
        before = next((r for r in rates if {r['base'], r['quote']} == {rate['base'], rate['quote']} and r['date'] == rate['date']), None)
        if before is not None:
            rates.remove(before)
        rates.append(rate)
        after = deepcopy(rate)
    else:
        fail('finance_invalid_edit')
    dates = [day(value) for value in (doc['period_start'], doc['period_end']) if value]
    dates += [day(row['date']) for a in assets for row in a['observations']]
    dates += [day(row['date']) for row in rates]
    if not dates:
        fail('finance_invalid_asset_date')
    doc['period_start'], doc['period_end'] = min(dates).isoformat(), max(dates).isoformat()
    doc['time_zone'] = time_zone
    return doc, {'type': kind, 'before': before, 'after': after}
