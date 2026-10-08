"""Operator-only streaming diagnostic. Never imported by user chat routes.

Run with python -m research.deepseek.benchmark --help. Default is a self-hosted
endpoint. External requests additionally require --allow-paid-external and a
positive worst-case budget. The suite is fixed synthetic data, not owner chats.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from .evaluation import VERSION, cases, digest, evaluate, summarize

PRICES_AS_OF='2026-10-08'
# USD per million tokens; peak/cache-miss upper bounds, not a billing guarantee.
PRICES={'deepseek-flash':('0.30','1.20'),'deepseek-v4-pro':('1.32','3.96')}


def endpoint(value, external=False):
    p=urlsplit(value)
    if p.username or p.password or p.query or p.fragment or not p.hostname:
        raise ValueError('invalid_endpoint')
    if external:
        if value!='https://api.deepseek.com/chat/completions':raise ValueError('unapproved_external_endpoint')
    elif p.scheme!='https' and not (p.scheme=='http' and p.hostname in {'localhost','127.0.0.1','::1'}):
        raise ValueError('https_or_local_endpoint_required')
    elif p.hostname=='api.deepseek.com':
        raise ValueError('external_model_opt_in_required')
    if not p.path.endswith('/chat/completions'):raise ValueError('chat_completions_endpoint_required')
    return value


def reservation(model,messages,max_tokens):
    rates=PRICES[model]
    # UTF-8 bytes are a conservative tokenizer-independent bound for this fixed text suite.
    n=len(json.dumps(messages,ensure_ascii=False).encode())+256
    return (Decimal(n)*Decimal(rates[0])+Decimal(max_tokens)*Decimal(rates[1]))/Decimal(1000000)


def cost(model,usage):
    if not isinstance(usage,dict):return None
    values=[usage.get('prompt_tokens'),usage.get('completion_tokens')]
    if any(type(x) is not int or x<0 for x in values):return None
    hit=usage.get('prompt_cache_hit_tokens',0)
    if type(hit) is not int or not 0<=hit<=values[0]:return None
    # Conservative upper-bound accounting (not provider invoice, ignores discounts/cache hits).
    return str((Decimal(values[0])*Decimal(PRICES[model][0])+Decimal(values[1])*Decimal(PRICES[model][1]))/Decimal(1000000))


def process_sample(pid):
    if not pid:return None
    try:
        status=Path(f'/proc/{pid}/status').read_text()
        rss=next(float(l.split()[1])/1024 for l in status.splitlines() if l.startswith('VmRSS:'))
        # Parenthesized comm may contain spaces; split only after its closing bracket.
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        ticks=os.sysconf('SC_CLK_TCK')
        return {'rss_mib':rss,'cpu_seconds':(int(fields[11])+int(fields[12]))/ticks}
    except (OSError,ValueError,StopIteration):return None


async def generate(session,url,key,model,messages,max_tokens,external=False,pid=None):
    body={'model':model,'messages':messages,'stream':True,'stream_options':{'include_usage':True},'max_tokens':max_tokens}
    if external:body['thinking']={'type':'disabled'}
    else:body.update(temperature=0,seed=42)
    headers={'Authorization':'Bearer '+key} if key else {}
    start=time.perf_counter();first=None;answer='';finish=None;usage=None;timings={};done=False
    initial=process_sample(pid);peak=initial['rss_mib'] if initial else None;had_reasoning=False
    async with session.post(url,json=body,headers=headers,allow_redirects=False) as response:
        if response.status!=200:raise ValueError('provider_http_'+str(response.status))
        buffered=b'';received=0
        async for chunk in response.content.iter_any():
            received+=len(chunk)
            if received>1024*1024:raise ValueError('provider_output_too_large')
            buffered+=chunk
            while b'\n' in buffered:
                line,buffered=buffered.split(b'\n',1)
                if not line.startswith(b'data:'):continue
                raw=line[5:].strip()
                if raw==b'[DONE]':done=True;continue
                if not raw:continue
                event=json.loads(raw)
                if event.get('error'):raise ValueError('provider_stream_error')
                choices=event.get('choices') or []
                if choices:
                    choice=choices[0];delta=choice.get('delta') or {}
                    if delta.get('reasoning_content'):had_reasoning=True
                    content=delta.get('content')
                    if isinstance(content,str) and content:
                        if first is None:first=time.perf_counter()-start
                        answer+=content
                    if choice.get('finish_reason'):finish=choice['finish_reason']
                if isinstance(event.get('usage'),dict):usage=event['usage']
                if isinstance(event.get('timings'),dict):timings=event['timings']
            current=process_sample(pid)
            if current:peak=max(peak or 0,current['rss_mib'])
    end=process_sample(pid)
    reported=timings.get('predicted_per_second')
    return {'ok':bool(done and finish=='stop' and answer),'text':answer,'finish_reason':finish,
            'ttft_seconds':first,'total_seconds':time.perf_counter()-start,'usage':usage,
            'reported_decode_tps':reported if type(reported) in {int,float} and reported>0 else None,
            'server_rss_mib':peak,'server_cpu_seconds':max(0,end['cpu_seconds']-initial['cpu_seconds']) if initial and end else None,
            'resource_scope':'local_worker_pid_samples' if pid else 'unmeasured_remote_worker',
            'had_reasoning':had_reasoning}


async def run(args):
    external=args.model in PRICES
    if external and (not args.allow_paid_external or args.budget_usd<=0):raise ValueError('external_opt_in_and_budget_required')
    if not external and args.allow_paid_external:raise ValueError('unknown_external_model')
    url=endpoint(args.endpoint,external)
    key=os.environ.get(args.key_env,'')
    if external and not key:raise ValueError('provider_key_missing')
    suite=cases();spent=Decimal(0);rows=[]
    generation={'max_tokens':args.max_tokens,'thinking':False,'self_hosted_temperature':0,'self_hosted_seed':42,
                'external_temperature':'provider_default_nonthinking'}
    receipt={'suite_version':VERSION,'suite_digest':digest(suite),'generation_digest':digest(generation),
             'harness_digest':digest([Path(__file__).read_text(),Path(__file__).with_name('evaluation.py').read_text()]),
             'model':args.model,'checkpoint_revision':args.revision,'runtime_revision':args.runtime_revision,
             'endpoint_digest':digest(url),'generation':generation,'rows':rows,'prices_as_of':PRICES_AS_OF,
             'quality_improvement_proven':False}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180),trust_env=False) as session:
        for case in suite:
            reserved=reservation(args.model,case['messages'],args.max_tokens) if external else Decimal(0)
            if spent+reserved>args.budget_usd and external:raise ValueError('run_budget_exceeded')
            # Reserve before sending: failed/ambiguous requests consume reservation; no automatic retry.
            spent+=reserved
            try:
                result=await generate(session,url,key,args.model,case['messages'],args.max_tokens,external,args.worker_pid)
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,json.JSONDecodeError):
                result={'ok':False,'finish_reason':None,'text':'','error':'provider_request_failed'}
            row={k:case[k] for k in ['key','language','category','check']}
            row.update(result,outcome=evaluate(case,result),estimated_peak_cost_usd=cost(args.model,result.get('usage')) if external else None)
            rows.append(row)
            receipt.update(summary=summarize(rows),reserved_cost_usd=str(spent),completed=len(rows)==len(suite))
            path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n');temporary.replace(path)
    return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--endpoint',required=True);p.add_argument('--model',required=True)
    p.add_argument('--revision',required=True);p.add_argument('--runtime-revision',required=True)
    p.add_argument('--key-env',default='VELIA_LAB_BENCHMARK_KEY')
    p.add_argument('--max-tokens',type=int,default=128);p.add_argument('--worker-pid',type=int)
    p.add_argument('--allow-paid-external',action='store_true');p.add_argument('--budget-usd',type=Decimal,default=Decimal(0))
    p.add_argument('--report',required=True);args=p.parse_args()
    if not 16<=args.max_tokens<=2048 or not args.budget_usd.is_finite() or args.budget_usd<0:p.error('invalid token limit or budget')
    if args.worker_pid is not None and args.worker_pid<=0:p.error('invalid worker PID')
    receipt=asyncio.run(run(args));print(json.dumps({'completed':receipt['completed'],'summary':receipt['summary'],'reserved_cost_usd':receipt['reserved_cost_usd']},ensure_ascii=False))

if __name__=='__main__':main()
