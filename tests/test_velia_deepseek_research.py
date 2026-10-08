import asyncio
import copy
import json
import os
from decimal import Decimal
from types import SimpleNamespace
import pytest
from aiohttp import web,ClientSession
from aiohttp.test_utils import TestServer
from research.deepseek.evaluation import cases,evaluate,comparison,digest
from research.deepseek.benchmark import endpoint,reservation,cost,generate,run,process_sample
from research.deepseek.engram import addresses,budget
from research.deepseek.speculation import admission,ngram_trial_args,FLASH_RUNTIME
from research.deepseek.ocr import route_pages,metrics,table_accuracy
from research.deepseek.datasets import add_reviewed_candidate


def test_fixed_suite_has_independent_targets_and_twenty_languages():
    suite=cases();assert len(suite)==36 and len({c['language'] for c in suite})==20
    assert len({c['key'] for c in suite})==36
    for c in suite:
        assert c['split']=='holdout'
        assert evaluate(c,{'ok':True,'text':c['target'],'finish_reason':'stop'})=='passed'

@pytest.mark.parametrize('finish',[None,'length','tool_calls','content_filter'])
def test_truncated_or_unexecuted_answers_never_pass(finish):
    assert evaluate(cases()[0],{'ok':True,'text':'136','finish_reason':finish})=='error'


def test_json_types_and_unsupported_oracles():
    c={'check':'json','target':'{"count":1}'}
    assert evaluate(c,{'ok':True,'text':'{"count":true}','finish_reason':'stop'})=='failed'
    assert evaluate(c,{'ok':True,'text':'{"count":1.0}','finish_reason':'stop'})=='failed'
    assert evaluate(c,{'ok':True,'text':'{"count":1,"x":2}','finish_reason':'stop'})=='failed'


def receipt():
    return dict(completed=True,suite_digest='suite',generation_digest='generation',harness_digest='harness',
                model='velia-flash',checkpoint_revision='weights',runtime_revision='runtime',
                rows=[dict(key=str(i),language='ru',category='math',check='exact',outcome='passed',
                      ttft_seconds=2,reported_decode_tps=10,server_rss_mib=1000,server_cpu_seconds=2)
                      for i in range(3)])


def test_comparison_rejects_mismatched_suite_and_reports_regressions():
    a=receipt();b=copy.deepcopy(a);b['suite_digest']='different'
    with pytest.raises(ValueError):comparison(a,b)
    b=copy.deepcopy(a);b['rows'][0]['outcome']='error'
    assert comparison(a,b)['regressions']==['0']
    assert not admission(a,b)['eligible_for_further_trials']


def test_speculation_requires_real_resources_and_same_weights():
    a=receipt();b=copy.deepcopy(a)
    for r in b['rows']:r['reported_decode_tps']=12
    assert admission(a,b)['eligible_for_further_trials']
    assert not admission(a,b)['automatic_release_allowed']
    b['rows'][0]['server_rss_mib']=None
    assert 'missing_server_rss_mib' in admission(a,b)['reasons']
    b=copy.deepcopy(a);b['checkpoint_revision']='new'
    assert 'changed_checkpoint_revision' in admission(a,b)['reasons']


def test_runtime_args_fail_closed():
    with pytest.raises(ValueError):ngram_trial_args(FLASH_RUNTIME,'unsupported help')
    help_text='--spec-type --spec-ngram-simple-size-n --spec-ngram-simple-size-m --spec-ngram-simple-min-hits'
    assert ngram_trial_args(FLASH_RUNTIME,help_text)[1]=='ngram-simple'
    with pytest.raises(ValueError):ngram_trial_args('changed',help_text)

@pytest.mark.parametrize('url',['https://user:secret@api.deepseek.com/chat/completions',
 'http://api.deepseek.com/chat/completions','https://example.com/chat/completions',
 'https://api.deepseek.com/chat/completions?key=secret'])
def test_external_endpoint_restricted(url):
    with pytest.raises(ValueError):endpoint(url,True)


def test_accounting_has_no_fake_zero_and_reserves_peak_prices():
    assert cost('deepseek-flash',None) is None
    assert cost('deepseek-flash',{'prompt_tokens':True,'completion_tokens':1}) is None
    assert Decimal(cost('deepseek-flash',{'prompt_tokens':1000000,'completion_tokens':1000000}))==Decimal('1.50')
    assert reservation('deepseek-flash',cases()[0]['messages'],128)>0


def test_no_external_request_without_opt_in_or_budget(tmp_path):
    args=SimpleNamespace(model='deepseek-flash',allow_paid_external=False,budget_usd=Decimal('1'))
    with pytest.raises(ValueError,match='external_opt_in'):asyncio.run(run(args))

@pytest.mark.parametrize('done',[True,False])
def test_sse_partial_chunks_usage_and_no_false_success(done):
    async def scenario():
        async def handler(request):
            body=await request.json();assert 'target' not in json.dumps(body)
            stream=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await stream.prepare(request)
            events=[{'choices':[{'delta':{'reasoning_content':'private thought'}}]},
                    {'choices':[{'delta':{'content':'136'}}]},
                    {'choices':[{'delta':{},'finish_reason':'stop'}]},
                    {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':3}}]
            for event in events:
                value=('data: '+json.dumps(event)+'\n\n').encode();await stream.write(value[:7]);await stream.write(value[7:])
            if done:await stream.write(b'data: [DONE]\n\n')
            await stream.write_eof();return stream
        app=web.Application();app.router.add_post('/chat/completions',handler)
        async with TestServer(app) as server,ClientSession() as session:
            result=await generate(session,str(server.make_url('/chat/completions')),'','velia-flash',cases()[0]['messages'],128)
            assert result['ok']==done and result['text']=='136'
            assert result['usage']['completion_tokens']==3 and result['ttft_seconds']>=0
            assert result['server_rss_mib'] is None and result['reported_decode_tps'] is None
            assert 'private thought' not in json.dumps(result)
    asyncio.run(scenario())


def test_process_resource_scope(monkeypatch):
    from pathlib import Path
    stat='1 (worker with spaces) S 0 0 0 0 0 0 0 0 0 0 100 50 0 0'
    monkeypatch.setattr(Path,'read_text',lambda p:'VmRSS: 2048 kB\n' if str(p).endswith('status') else stat)
    monkeypatch.setattr(os,'sysconf',lambda _:100)
    s=process_sample(1);assert s['rss_mib']==2 and s['cpu_seconds']==1.5
    monkeypatch.setattr(Path,'read_text',lambda _:(_ for _ in ()).throw(OSError()))
    assert process_sample(1) is None


def test_lookup_is_causal_bounded_and_deterministic():
    short=addresses([1,2,3]);long=addresses([1,2,3,999])
    assert short==long[:3] and len(short[0])==4
    assert all(0<=x<4096 for row in long for x in row)
    with pytest.raises(ValueError):addresses([-1])
    with pytest.raises(ValueError):addresses([True])
    assert budget()['weight_bytes']<16*1024*1024
    assert budget()['quality_gain'] is None


def test_ocr_existing_native_path_and_gpu_blocked():
    assert route_pages(['Русский текст','English'])['route']=='existing_native_extractor'
    assert route_pages(['Text',''])['ocr_pages']==[2]
    assert route_pages([''],enabled=True)['reason']=='document_consent_required'
    assert route_pages([''],enabled=True,consent=True,estimated_usd='1',budget_usd='0')['reason']=='ocr_budget_exceeded'
    assert route_pages([''],enabled=True,consent=True)['reason']=='worker_acceptance_required'
    assert metrics('Привет','Привет')['cer']==0
    assert metrics('abc','adc')['cer']==pytest.approx(1/3)
    assert table_accuracy([['a','2']],[['a','3']])['cell_accuracy']==.5
    assert not table_accuracy([['a']],[['a','b']])['shape_match']


def test_teacher_examples_require_reference_human_rights_and_train():
    lab=SimpleNamespace(builtin_cases=lambda:[],add_example=lambda *args:'saved')
    c={'prompt':'Independent training question','target':'reference','split':'train','reference_verified':True,'reference_source':'owned-verified-ref'}
    with pytest.raises(ValueError):add_reviewed_candidate(lab,7,c)
    flags=dict(human_approved=True,rights_confirmed=True,no_personal_data=True)
    assert add_reviewed_candidate(lab,7,c,**flags)['id']=='saved'
    with pytest.raises(ValueError,match='exam_leakage'):add_reviewed_candidate(lab,7,{**c,'prompt':cases()[0]['messages'][-1]['content']},**flags)
    with pytest.raises(ValueError,match='holdout'):add_reviewed_candidate(lab,7,{**c,'split':'holdout'},**flags)


def test_optional_trainable_lookup_initially_preserves_backbone():
    torch=pytest.importorskip('torch',reason='Separate PyTorch research environment is not installed')
    from research.deepseek.engram_candidate import ConditionalLookup
    module=ConditionalLookup(hidden=8,buckets=16,width=4,heads=1)
    hidden=torch.randn(1,3,8);tokens=torch.tensor([[1,2,3]])
    assert torch.equal(module(hidden,tokens),hidden)
    loss=module(hidden,tokens).sum();loss.backward()
    assert module.value.weight.grad is not None


def test_unknown_deepseek_alias_cannot_bypass_external_gate():
    with pytest.raises(ValueError,match='external_model_opt_in'):endpoint('https://api.deepseek.com/chat/completions',False)


def test_full_operator_run_saves_only_real_fixture_measurements(tmp_path):
    async def scenario():
        seen=[]
        async def handler(request):
            body=await request.json();seen.append(body)
            stream=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await stream.prepare(request)
            event={'choices':[{'delta':{'content':'136'},'finish_reason':'stop'}],'usage':{'prompt_tokens':5,'completion_tokens':2}}
            await stream.write(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode());await stream.write_eof();return stream
        app=web.Application();app.router.add_post('/v1/chat/completions',handler)
        async with TestServer(app) as server:
            args=SimpleNamespace(model='fixture-only',allow_paid_external=False,budget_usd=Decimal(0),
                endpoint=str(server.make_url('/v1/chat/completions')),key_env='VELIA_TEST_NO_KEY',
                max_tokens=128,worker_pid=None,revision='fixture',runtime_revision='fixture',report=str(tmp_path/'receipt.json'))
            result=await run(args)
            assert result['completed'] and len(seen)==36
            assert result['summary']['closed_score']==pytest.approx(20/36)
            assert result['summary']['metrics']['server_rss_mib']['median'] is None
            assert result['summary']['metrics']['reported_decode_tps']['median'] is None
            assert not result['quality_improvement_proven']
            assert all('target' not in body and 'tools' not in body for body in seen)
            assert json.loads((tmp_path/'receipt.json').read_text())['completed']
    asyncio.run(scenario())
