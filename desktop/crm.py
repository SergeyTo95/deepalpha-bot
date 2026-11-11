"""Owner-scoped CRM records and deterministic follow-up queries. No message sending."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROLES = ('client', 'partner', 'investor', 'supplier', 'colleague', 'other')
STAGES = ('lead', 'discovery', 'proposal', 'negotiation', 'won', 'lost')


class CrmError(ValueError):
    def __init__(self, code='crm_invalid_data', status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(value, code='crm_invalid_data', status=400):
    if not value:
        raise CrmError(code, status)


def fields(value, required=(), optional=()):
    require(isinstance(value, dict) and not (set(value)-set(required)-set(optional)) and not (set(required)-set(value)))
    return value


def string(value, limit=200, empty=False):
    require(isinstance(value, str) and len(value) <= limit and (empty or value.strip())
            and not any(ord(c) < 32 and c not in '\n\t' for c in value))
    return value.strip()


def strings(value, limit=20):
    require(isinstance(value, list) and len(value) <= limit)
    result = [string(v, 100) for v in value]
    require(len(result) == len(set(result)))
    return result


def local_today(time_zone='UTC', now=None):
    try:
        require(isinstance(time_zone, str) and len(time_zone) <= 100, 'crm_invalid_timezone')
        return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(time_zone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        raise CrmError('crm_invalid_timezone') from None


def day(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'crm_invalid_date')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CrmError('crm_invalid_date') from None


def initial():
    return {'version': 1, 'contacts': [], 'interactions': [], 'tasks': [], 'deals': []}


def contact(value):
    fields(value, ('id', 'name'), ('company', 'roles', 'email', 'phone', 'telegram', 'notes', 'tags', 'projects', 'status', 'links'))
    result = {'id':string(value['id'],100),'name':string(value['name']),
        **{k:string(value.get(k,''),2000 if k=='notes' else 200,empty=True) for k in ('company','email','phone','telegram','notes')},
        **{k:strings(value.get(k,[])) for k in ('roles','tags','projects')},'status':value.get('status','active')}
    require(all(role in ROLES for role in result['roles']) and result['status'] in ('active','archived'))
    require(not result['email'] or re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',result['email']), 'crm_invalid_email')
    require(not result['phone'] or re.fullmatch(r'[+0-9(). -]{5,40}',result['phone']), 'crm_invalid_phone')
    require(not result['telegram'] or re.fullmatch(r'@?[A-Za-z0-9_]{5,32}',result['telegram']), 'crm_invalid_telegram')
    links = value.get('links', [])
    require(isinstance(links,list) and len(links)<=50)
    normalized = []
    for link in links:
        fields(link,('kind','id','title'),('url',))
        require(link['kind'] in ('note','calendar','pulse','project'))
        item = {'kind':link['kind'],'id':string(link['id'],200),'title':string(link['title'])}
        if 'url' in link:
            url = urlsplit(string(link['url'],2000))
            require(url.scheme=='https' and url.hostname and not url.username and not url.password, 'crm_invalid_link')
            if link['kind']=='calendar':
                require(url.hostname in ('calendar.google.com','www.google.com') and url.path.startswith('/calendar'), 'crm_invalid_link')
            item['url']=link['url']
        require(not any((old['kind'],old['id'])==(item['kind'],item['id']) for old in normalized))
        normalized.append(item)
    result['links']=normalized
    return result


def validate(doc, *, time_zone='UTC', now=None):
    fields(doc,('version','contacts','interactions','tasks','deals'))
    require(type(doc['version']) is int and doc['version']==1)
    current=local_today(time_zone,now)
    limits={'contacts':500,'interactions':3000,'tasks':1000,'deals':500}
    for name,limit in limits.items():
        require(isinstance(doc[name],list) and len(doc[name])<=limit,'crm_capacity')
    result={'version':1,'contacts':[contact(c) for c in doc['contacts']], 'interactions':[], 'tasks':[], 'deals':[]}
    known={c['id'] for c in result['contacts']}
    require(len(known)==len(result['contacts']),'crm_duplicate_id')
    for key in ('email','phone','telegram'):
        seen=set()
        for c in result['contacts']:
            value=c[key].casefold().lstrip('@')
            if key=='phone':value=re.sub(r'\D','',value)
            if value:
                require(value not in seen,'crm_duplicate_contact',409)
                seen.add(value)
    for row in doc['deals']:
        fields(row,('id','title','contact_ids','stage'),('project','value','currency','notes'))
        item={'id':string(row['id'],100),'title':string(row['title']),'contact_ids':strings(row['contact_ids']),
              'stage':row['stage'],'project':string(row.get('project',''),empty=True),'notes':string(row.get('notes',''),2000,empty=True)}
        require(item['contact_ids'] and all(id in known for id in item['contact_ids']) and item['stage'] in STAGES)
        require(('value' in row)==('currency' in row))
        if 'value' in row:
            from desktop.finance import amount, currency, FinanceError
            try:item.update(value=format(amount(row['value']),'f'),currency=currency(row['currency']))
            except FinanceError:raise CrmError('crm_invalid_value') from None
        result['deals'].append(item)
    deals={d['id']:d for d in result['deals']}
    require(len(deals)==len(result['deals']),'crm_duplicate_id')
    for row in doc['interactions']:
        fields(row,('id','contact_id','date','direction','channel','summary'),('requires_reply','reply_to','resolved','time_zone'))
        item={k:string(row[k],2000 if k=='summary' else 100) for k in ('id','contact_id','date','direction','channel','summary')}
        require(item['contact_id'] in known,'crm_contact_not_found',404)
        item['time_zone']=row.get('time_zone',time_zone)
        require(day(item['date'])<=local_today(item['time_zone'],now),'crm_future_interaction')
        require(item['direction'] in ('inbound','outbound','meeting','note') and item['channel'] in ('email','telegram','phone','meeting','other'))
        item.update(requires_reply=row.get('requires_reply',False),resolved=row.get('resolved',False))
        require(type(item['requires_reply']) is bool and type(item['resolved']) is bool)
        require(not item['requires_reply'] or item['direction']=='inbound')
        if 'reply_to' in row:item['reply_to']=string(row['reply_to'],100)
        result['interactions'].append(item)
    interactions={i['id']:i for i in result['interactions']}
    require(len(interactions)==len(result['interactions']),'crm_duplicate_id')
    for item in interactions.values():
        if item.get('reply_to'):
            original=interactions.get(item['reply_to'])
            require(original and original['contact_id']==item['contact_id'] and original['direction']=='inbound'
                    and item['direction']=='outbound' and day(original['date'])<=day(item['date']), 'crm_invalid_reply')
    for row in doc['tasks']:
        fields(row,('id','contact_id','title'),('due_date','status','deal_id','notes'))
        item={'id':string(row['id'],100),'contact_id':string(row['contact_id'],100),'title':string(row['title']),
              'status':row.get('status','open'),'notes':string(row.get('notes',''),2000,empty=True)}
        require(item['contact_id'] in known and item['status'] in ('open','done','cancelled'))
        if 'due_date' in row:
            day(row['due_date']);item['due_date']=row['due_date']
        if 'deal_id' in row:
            require(row['deal_id'] in deals and item['contact_id'] in deals[row['deal_id']]['contact_ids'],'crm_invalid_deal')
            item['deal_id']=row['deal_id']
        result['tasks'].append(item)
    require(len({t['id'] for t in result['tasks']})==len(result['tasks']),'crm_duplicate_id')
    require(len(json.dumps(result,ensure_ascii=False).encode())<=2*1024*1024,'crm_capacity')
    return result


def query(doc, options=None, *, time_zone='UTC', now=None):
    options={} if options is None else options
    fields(options,(),('view','search','role','contact_id','stale_days','project','stage','status','limit','offset'))
    view=options.get('view','contacts')
    require(view in ('contacts','card','waiting_reply','stale','tasks','pipeline'))
    limit,offset,days=options.get('limit',20),options.get('offset',0),options.get('stale_days',14)
    require(type(limit) is int and 1<=limit<=50 and type(offset) is int and 0<=offset<=500)
    require(type(days) is int and 1<=days<=3650)
    for field in ('search','contact_id','project'):
        if field in options:string(options[field])
    if 'role' in options:require(options['role'] in ROLES)
    if 'stage' in options:require(options['stage'] in STAGES)
    if 'status' in options:require(options['status'] in ('active','archived','all'))
    current=local_today(time_zone,now)
    contacts=doc['contacts']
    if options.get('contact_id'):
        contacts=[c for c in contacts if c['id']==options['contact_id']]
        require(contacts,'crm_contact_not_found',404)
    if options.get('search'):
        terms=options['search'].casefold().split()
        contacts=[c for c in contacts if all(term in ' '.join([c['name'],c['company'],c['email'],c['phone'],c['telegram'],*c['tags']]).casefold() for term in terms)]
    if options.get('role'):contacts=[c for c in contacts if options['role'] in c['roles']]
    if options.get('project'):contacts=[c for c in contacts if options['project'] in c['projects'] or any(
        d['project']==options['project'] and c['id'] in d['contact_ids'] for d in doc['deals'])]
    answered={i.get('reply_to') for i in doc['interactions'] if i['direction']=='outbound'}
    cards=[];unknown_history=0
    for c in contacts:
        status=options.get('status','all' if view=='card' else 'active')
        if status!='all' and c['status']!=status:continue
        interactions=[i for i in doc['interactions'] if i['contact_id']==c['id']]
        actual=[day(i['date']) for i in interactions if i['direction']!='note']
        last=max(actual) if actual else None
        waiting=[i for i in interactions if i['direction']=='inbound' and i['requires_reply'] and not i['resolved'] and i['id'] not in answered]
        if view=='waiting_reply' and 'stale_days' in options:waiting=[i for i in waiting if (current-day(i['date'])).days>=days]
        waiting.sort(key=lambda i:(i['date'],i['id']))
        tasks=[t for t in doc['tasks'] if t['contact_id']==c['id']]
        open_tasks=sorted([t for t in tasks if t['status']=='open'],key=lambda t:(t.get('due_date','9999-12-31'),t['id']))
        overdue=[t for t in open_tasks if t.get('due_date') and day(t['due_date'])<current]
        age=max(0,(current-last).days) if last else None
        if last is None:unknown_history+=1
        if view=='waiting_reply' and not waiting:continue
        if view=='stale' and (age is None or age<days):continue
        if view=='tasks' and not open_tasks:continue
        actions=[{'kind':'reply','title':'Подготовить ответ на входящее','date':waiting[0]['date']}] if waiting else []
        actions += [{'kind':'task','title':t['title'],'date':t.get('due_date')} for t in open_tasks[:3]]
        if not actions and age is not None and age>=days:actions.append({'kind':'reconnect','title':'Предложить следующий контакт','date':last.isoformat()})
        card={**deepcopy(c),'last_contact_date':last.isoformat() if last else None,'days_since_contact':age,
              'waiting_reply':deepcopy(waiting[:10]),'waiting_count':len(waiting),'overdue_count':len(overdue),'next_actions':actions,
              'tasks':deepcopy(tasks if view=='card' else open_tasks[:10])}
        if view=='card':
            card['interactions']=sorted(deepcopy(interactions),key=lambda i:(i['date'],i['id']),reverse=True)[:100]
            card['interaction_count']=len(interactions)
        cards.append(card)
    cards.sort(key=lambda c:(c['waiting_reply'][0]['date'] if c['waiting_reply'] else '9999-12-31',-c['overdue_count'],c['name'].casefold(),c['id']))
    if view=='stale':cards.sort(key=lambda c:(-c['days_since_contact'],c['name'].casefold(),c['id']))
    ids={c['id'] for c in cards}
    deals=[d for d in doc['deals'] if ids.intersection(d['contact_ids']) and (not options.get('stage') or d['stage']==options['stage'])
           and (not options.get('project') or d['project']==options['project'])]
    return {'view':view,'today':current.isoformat(),'time_zone':time_zone,'contacts':cards[offset:offset+limit],
            'filters':deepcopy(options),
            'total':len(cards),'more':len(cards)>offset+limit,'offset':offset,
            'deals':deepcopy(deals[offset:offset+limit]) if view in ('pipeline','card') else [], 'deal_count':len(deals),
            'unknown_history_count':unknown_history,
            'scope':'user_recorded_only','automatically_synced':False}


def apply_edit(doc, edit, operation_id, *, time_zone='UTC', now=None):
    """Plan/commit share this deterministic function. A batch is all-or-nothing."""
    string(operation_id,80)
    work=deepcopy(doc)
    fields(edit,('type',),('contact','contact_id','changes','interaction','interaction_id','task','task_id','deal','deal_id','link','edits'))
    if edit['type']=='batch':
        require(isinstance(edit.get('edits'),list) and 1<=len(edit['edits'])<=8)
        changes=[];ids={}
        def references(value,key=''):
            if isinstance(value,dict):return {k:references(v,k) for k,v in value.items()}
            if isinstance(value,list):return [references(v,key) for v in value]
            if key in ('contact_id','contact_ids','deal_id','reply_to') and isinstance(value,str) and re.fullmatch(r'\$\d',value):
                require(value in ids,'crm_invalid_reference');return ids[value]
            return value
        for index,child in enumerate(edit['edits']):
            require(isinstance(child,dict) and child.get('type')!='batch')
            work,change=apply_edit(work,references(child),operation_id+':'+str(index),time_zone=time_zone,now=now)
            changes.append(change)
            if change['after'] is not None:ids['$'+str(index)]=change['after'].get('id')
        return work,{'type':'batch','changes':changes,'before':None,'after':None}
    kind=edit['type'];before=after=None
    tables={'contact':'contacts','interaction':'interactions','task':'tasks','deal':'deals'}
    for entity,table in tables.items():
        if kind=='add_'+entity:
            raw=deepcopy(edit.get(entity));require(isinstance(raw,dict) and 'id' not in raw)
            require(not any(row['id']==operation_id for row in work[table]),'crm_already_applied',409)
            after={**raw,'id':operation_id};work[table].append(after);break
        if kind in ('update_'+entity,'delete_'+entity):
            id=string(edit.get(entity+'_id'),100)
            row=next((row for row in work[table] if row['id']==id),None)
            require(row is not None,'crm_record_not_found',404)
            before=deepcopy(row)
            if kind.startswith('update_'):
                changes=edit.get('changes');require(isinstance(changes,dict) and changes and 'id' not in changes)
                row.update(changes);after=row
            else:
                work[table].remove(row)
                if entity=='contact':
                    counts={key:sum(1 for r in work[key] if r.get('contact_id')==id) for key in ('interactions','tasks')}
                    before['related_removed']=counts
                    for key in ('interactions','tasks'):work[key]=[r for r in work[key] if r['contact_id']!=id]
                    for deal in work['deals']:deal['contact_ids']=[c for c in deal['contact_ids'] if c!=id]
                    removed={d['id'] for d in work['deals'] if not d['contact_ids']}
                    before['related_removed']['deals']=len(removed)
                    work['deals']=[d for d in work['deals'] if d['id'] not in removed]
                    work['tasks']=[t for t in work['tasks'] if t.get('deal_id') not in removed]
                if entity=='interaction':
                    for i in work['interactions']:
                        if i.get('reply_to')==id:i.pop('reply_to')
                if entity=='deal':
                    for task in work['tasks']:
                        if task.get('deal_id')==id:task.pop('deal_id')
            break
    else:
        require(kind=='link_contact','crm_invalid_edit')
        row=next((c for c in work['contacts'] if c['id']==edit.get('contact_id')),None)
        require(row is not None,'crm_contact_not_found',404)
        before=deepcopy(row);link=deepcopy(edit.get('link'));require(isinstance(link,dict))
        row['links']=[old for old in row.get('links',[]) if (old['kind'],old['id'])!=(link.get('kind'),link.get('id'))]+[link]
        after=row
    work=validate(work,time_zone=time_zone,now=now)
    return work,{'type':kind,'before':before,'after':deepcopy(after)}
