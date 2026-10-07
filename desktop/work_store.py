"""Durable owner-scoped workspaces. Amounts are decimal strings, never floats."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re
import time
import uuid
from desktop.guest_store import GuestStore

ROLES = [{'id':k,'name':v} for k,v in [('manager','Управляющий'),('proposal','Переговорщик'),('executor','Исполнитель'),('reviewer','Контролёр качества'),('treasurer','Казначей')]]
DEFAULT_AUTONOMY={'enabled':False,'query':'','interval_minutes':60,'max_jobs_per_day':1}
DEFAULT_MANDATE = {'reserve_usdt':'30','max_expense_usdt':'5','owner_address':'','network':'','agent_share_percent':10}
class WorkError(Exception):
    def __init__(self, code, status=400):
        self.code,self.status=code,status
        super().__init__(code)

def amount(value, *, positive=False):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?',value):
        raise WorkError('invalid_amount')
    number=Decimal(value)
    if number>Decimal('1000000') or (positive and number<=0):
        raise WorkError('invalid_amount')
    return format(number,'f')

def mandate(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_MANDATE):
        raise WorkError('invalid_mandate')
    result={**value,'reserve_usdt':amount(value['reserve_usdt']),'max_expense_usdt':amount(value['max_expense_usdt'])}
    if type(value['agent_share_percent']) is not int or not 0<=value['agent_share_percent']<=100:
        raise WorkError('invalid_mandate')
    address,network=value['owner_address'],value['network']
    if not isinstance(address,str) or not isinstance(network,str):
        raise WorkError('invalid_destination')
    if not address and not network:
        return result
    patterns={'tron':r'T[1-9A-HJ-NP-Za-km-z]{33}', 'ethereum':r'0x[0-9a-fA-F]{40}',
              'polygon':r'0x[0-9a-fA-F]{40}', 'bnb':r'0x[0-9a-fA-F]{40}', 'arbitrum':r'0x[0-9a-fA-F]{40}'}
    if network not in patterns or not re.fullmatch(patterns[network],address):
        raise WorkError('invalid_destination')
    return result

def identity_key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9:_-]{8,128}',value):
        raise WorkError('invalid_request_id')
    return value

def fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def initial():
    return {'mandate':deepcopy(DEFAULT_MANDATE),'mandate_revision':1,'jobs':[],'payouts':[],
            'balance_usdt':None,'available_usdt':None,'earnings_confirmed_usdt':None}

class WorkStore(GuestStore):
    def initialize(self):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute('CREATE TABLE IF NOT EXISTS velia_work_workspace (user_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            conn.commit()

    def transaction(self,user,change=None):
        with closing(self.connect()) as conn, closing(conn.cursor()) as cur:
            try:
                if self.sqlite_path is not None:
                    cur.execute('BEGIN IMMEDIATE')
                self.execute(cur,'INSERT INTO velia_work_workspace(user_id,document) VALUES (?,?) ON CONFLICT(user_id) DO NOTHING',(str(user),json.dumps(initial())))
                self.execute(cur,'SELECT document FROM velia_work_workspace WHERE user_id=?'+(' FOR UPDATE' if self.sqlite_path is None else ''),(str(user),))
                doc=json.loads(cur.fetchone()[0])
                now=time.time()
                for job in doc['jobs']:
                    if job['status']=='running' and job.get('lease_until',0)<now:
                        job.update(status='interrupted',error='worker_interrupted',lease=None)
                result=change(doc) if change else deepcopy(doc)
                self.execute(cur,'UPDATE velia_work_workspace SET document=? WHERE user_id=?',(json.dumps(doc,ensure_ascii=False),str(user)))
                conn.commit()
                return deepcopy(result)
            except BaseException:
                conn.rollback()
                raise

    def workspace(self,user):
        doc=self.transaction(user)
        for job in doc['jobs']:
            for key in ['lease','lease_until','conversations','request_hash','client_request_id']:
                job.pop(key,None)
        for payout in doc['payouts']:
            for key in ['request_hash','client_request_id']:
                payout.pop(key,None)
        return doc

    @staticmethod
    def find(doc,id):
        job=next((j for j in doc['jobs'] if j['id']==id),None)
        if not job:
            raise WorkError('job_not_found',404)
        return job

    def set_mandate(self,user,value):
        value=mandate(value)
        def update(doc):
            doc['mandate']=value;doc['mandate_revision']+=1
            return {'mandate':value,'mandate_revision':doc['mandate_revision']}
        return self.transaction(user,update)

    def set_autonomy(self,user,value):
        if (not isinstance(value,dict) or set(value)!=set(DEFAULT_AUTONOMY)
                or type(value['enabled']) is not bool or not isinstance(value['query'],str)
                or len(value['query'])>300 or (value['enabled'] and len(value['query'].strip())<2)
                or type(value['interval_minutes']) is not int or not 15<=value['interval_minutes']<=1440
                or type(value['max_jobs_per_day']) is not int or not 1<=value['max_jobs_per_day']<=3):
            raise WorkError('invalid_autonomy')
        def update(doc):
            doc['autonomy']={**value,'query':value['query'].strip()}
            doc['next_scan_at']=0
            doc.update(scan_token=None,scan_until=0)
            return doc['autonomy']
        return self.transaction(user,update)

    def claim_scan(self,user):
        token=str(uuid.uuid4());now=time.time()
        def claim(doc):
            policy=doc.get('autonomy',DEFAULT_AUTONOMY)
            if not policy['enabled'] or doc.get('next_scan_at',0)>now or doc.get('scan_until',0)>now:
                return None
            if any(j['status']=='running' for j in doc['jobs']):return None
            doc.update(scan_token=token,scan_until=now+120,next_scan_at=now+policy['interval_minutes']*60)
            return {**policy,'token':token}
        return self.transaction(user,claim)

    def finish_scan(self,user,token,results,error=None):
        from desktop.web_search import public_url
        def finish(doc):
            if doc.get('scan_token')!=token or doc.get('scan_until',0)<time.time():raise WorkError('scan_expired',409)
            doc.update(scan_token=None,scan_until=0,last_scan_at=time.time(),last_scan_error=error)
            policy=doc.get('autonomy',DEFAULT_AUTONOMY)
            if not policy['enabled'] or error:return None
            today=int(time.time()//86400)
            used=sum(j.get('autonomous',False) and int(j['created_at']//86400)==today for j in doc['jobs'])
            if used>=policy['max_jobs_per_day'] or len(doc['jobs'])>=100:return None
            known={j['source_url'] for j in doc['jobs']}
            for item in results[:10]:
                url=item.get('url');title=item.get('title');snippet=item.get('snippet')
                if not isinstance(url,str) or not public_url(url) or url in known:continue
                if not isinstance(title,str) or not title.strip() or not isinstance(snippet,str) or len(snippet.strip())<40:continue
                id=str(uuid.uuid4())
                doc['jobs'].insert(0,{'id':id,'title':title[:120],
                    'brief':('Кандидат из поиска. Это не принятый заказ. Условия и оплата не подтверждены. '
                        'Оцени, достаточно ли данных для конкретного текстового результата. При недостатке данных откажись. '
                        'Не придумывай требования, бюджет, квалификацию владельца или договор с заказчиком.\n'
                        'Направление владельца: '+policy['query']+'\nОписание источника: '+snippet)[:6000],
                    'source_url':url,'expected_usdt':'0','autonomous':True,'status':'queued','outputs':{},'conversations':{},
                    'client_request_id':'auto:'+id,'request_hash':'auto:'+id,'attempt':1,'created_at':time.time(),'error':None})
                return id
            return None
        return self.transaction(user,finish)

    def create_job(self,user,data):
        if not isinstance(data,dict) or set(data)!={'title','brief','source_url','expected_usdt','client_request_id'}:
            raise WorkError('invalid_job')
        for key,limit in [('title',120),('brief',6000)]:
            if not isinstance(data[key],str) or not data[key].strip() or len(data[key])>limit:
                raise WorkError('invalid_job')
        from desktop.web_search import public_url
        if not isinstance(data['source_url'],str) or (data['source_url'] and not public_url(data['source_url'])):
            raise WorkError('invalid_source')
        price=amount(data['expected_usdt']);key=identity_key(data['client_request_id']);digest=fingerprint(data)
        def create(doc):
            old=next((j for j in doc['jobs'] if j['client_request_id']==key),None)
            if old:
                if old['request_hash']!=digest:raise WorkError('idempotency_mismatch',409)
                return old['id']
            if len(doc['jobs'])>=100:raise WorkError('workspace_job_limit',409)
            job={**data,'expected_usdt':price,'id':str(uuid.uuid4()),'status':'queued','outputs':{},'conversations':{},
                 'request_hash':digest,'attempt':1,'created_at':time.time(),'error':None}
            doc['jobs'].insert(0,job)
            return job['id']
        return self.transaction(user,create)

    def claim(self,user,id):
        token=str(uuid.uuid4())
        def claim(doc):
            job=self.find(doc,id)
            if any(j['status']=='running' for j in doc['jobs']):raise WorkError('work_busy',409)
            if job['status'] not in {'queued','failed','interrupted'}:raise WorkError('invalid_job_state',409)
            if job['status']=='failed' and job.get('error')=='invalid_role_output':job['attempt']+=1
            job.update(status='running',lease=token,lease_until=time.time()+600,error=None)
            return job
        return self.transaction(user,claim)

    def update_job(self,user,id,lease,**values):
        def update(doc):
            job=self.find(doc,id)
            if job['status']!='running' or job.get('lease')!=lease:raise WorkError('job_cancelled',409)
            job.update(values,lease_until=time.time()+600)
            return job
        return self.transaction(user,update)

    def cancel(self,user,id):
        def cancel(doc):
            job=self.find(doc,id)
            if job['status'] in {'ready','cancelled'}:raise WorkError('invalid_job_state',409)
            job.update(status='cancelled',lease=None)
            return {'id':id,'status':'cancelled'}
        return self.transaction(user,cancel)

    def payout(self,user,data,*,initiated_by="owner",job_id=None,lease=None):
        if not isinstance(data,dict) or set(data)!={'amount_usdt','reason','client_request_id'}:
            raise WorkError('invalid_payout')
        value=amount(data['amount_usdt'],positive=True);key=identity_key(data['client_request_id'])
        if not isinstance(data['reason'],str) or not 1<=len(data['reason'].strip())<=500:
            raise WorkError('invalid_payout')
        digest=fingerprint(data)
        def payout(doc):
            if initiated_by=='agent':
                job=self.find(doc,job_id)
                if job['status']!='running' or job.get('lease')!=lease:raise WorkError('job_cancelled',409)
            old=next((p for p in doc['payouts'] if p['client_request_id']==key),None)
            if old:
                if old['request_hash']!=digest:raise WorkError('idempotency_mismatch',409)
                return old['id']
            if len(doc['payouts'])>=100:raise WorkError('workspace_payout_limit',409)
            p={**data,'amount_usdt':value,'id':str(uuid.uuid4()),'status':'blocked_wallet_unconnected',
               'destination':doc['mandate']['owner_address'],'network':doc['mandate']['network'],
               'mandate_revision':doc['mandate_revision'],'initiated_by':initiated_by,'request_hash':digest,'created_at':time.time()}
            doc['payouts'].insert(0,p)
            return p['id']
        return self.transaction(user,payout)
