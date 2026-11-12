
"""Regression contracts for the screenshot request and safe document compilation."""
import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
import uuid
import pytest
from desktop.mission_delivery import document_plan
from desktop.mission_runtime import MissionTools,run_mission
from desktop.mission_store import MissionStore
from desktop.mission_schema import validate_plan

GOAL='Подготовь подробный план запуска цветочного интернет-магазина на 30 дней и создай PDF'
SESSION=SimpleNamespace(user_id=7,id='document-session',access='fixture-access')
BODY='# План запуска\n'+'\n'.join(f'День {day}: выполнить этап запуска магазина. Результат: проверяемый этап готов.' for day in range(1,31))
async def allowed(*_):return None
async def identity(*_):return {'user_id':7}
def tools():
    return MissionTools(upstream=None,upstream_stream=None,authorize_model=allowed,
        handlers={'reserve':lambda _:None,'release':lambda _:None})

@pytest.mark.parametrize('goal,formats',[
    (GOAL,['pdf']),
    ('Составь план изучения турецкого языка на 14 дней и создай PDF и DOCX',['pdf','docx']),
    ('Напиши статью о робототехнике и создай пдф',['pdf']),
    ('Create a detailed launch plan for a flower store as PDF',['pdf']),
])
def test_standalone_text_uses_validated_fixed_document_steps(goal,formats):
    p=document_plan(goal)
    assert validate_plan(p,goal) is p
    assert [s['tool'] for s in p['steps']]==['text.draft']+['document.export']*len(formats)
    assert [s['arguments']['format'] for s in p['steps'][1:]]==formats
    assert all(s['arguments']['source_step']=='s1' for s in p['steps'][1:])

@pytest.mark.parametrize('goal',[
    'Что такое PDF?',
    'Создай PDF',
    'Подготовь подробный план запуска магазина',
    GOAL+' и отправь инвестору',
    GOAL+' и сохрани в заметки',
    GOAL+' и согласуй со мной',
    GOAL+' с актуальными ценами из интернета',
    'Найди источники, составь план запуска и создай PDF',
    'Подготовь план на основе моих финансовых данных и создай PDF',
    'Подготовь план по приложенному файлу и создай PDF',
    'Подготовь план по загруженному документу и создай PDF',
    'Подготовь план по документам и создай PDF',
    'Подготовь план по данным CRM и создай PDF',
    'Подготовь план по предыдущему диалогу и создай PDF',
    'Подготовь план и создай PDF, PPTX',
    'Create a launch plan as PDF and send it to the team',
    'Prepare a plan using our data as PDF',
    'Подготовь план по https://example.com и создай PDF',
])
def test_source_reads_extra_actions_and_unspecified_work_use_full_planner(goal):
    assert document_plan(goal) is None

@pytest.mark.parametrize('model',['velia-flash','velia-quantum','velia-pro'])
def test_document_plan_does_not_ask_the_model_for_json(model):
    async def scenario():
        t=tools()
        async def unexpected(*_,**__):raise AssertionError('document planning must not call model')
        t.draft=unexpected
        p=await t.plan(SESSION,{'goal':GOAL,'model':model},unexpected)
        assert p['steps'][1]['tool']=='document.export'
    asyncio.run(scenario())

def test_source_dependent_work_still_uses_typed_model_planner():
    async def scenario():
        t=tools();calls=[]
        p={'title':'Сводка','steps':[
            {'id':'s1','title':'Получить финансовые данные','tool':'finance.report','arguments':{}},
            {'id':'s2','title':'Подготовить выводы','tool':'text.draft','arguments':{'instruction':'Выводы на основе s1'}}]}
        async def draft(s,m,step,save,**kw):
            calls.append(kw);return json.dumps(p,ensure_ascii=False)
        t.draft=draft
        assert await t.plan(SESSION,{'goal':'Подготовь план на основе моих доходов и создай PDF','model':'velia-flash'},allowed)==p
        assert calls==[{'planning':True}]
    asyncio.run(scenario())

def test_failed_zero_step_task_replans_and_resumes_without_rewriting_goal(tmp_path):
    async def scenario():
        store=MissionStore(sqlite_path=tmp_path/'documents.db',session_key='x'*48);store.initialize()
        m=store.create(7,GOAL,None,'velia-flash','Europe/Istanbul','document-test')
        queued=store.enqueue(7,m['id'],m['revision']);active=store.activate(7,m['id'],queued['lease'])
        store.checkpoint(7,m['id'],active['lease'],lambda v:v.update(status='failed',planning_error='mission_invalid_plan',lease=None,lease_until=0))
        failed=store.detail(7,m['id']);assert failed['steps']==[] and failed['needs_plan']
        queued=store.enqueue(7,m['id'],failed['revision']);active=store.activate(7,m['id'],queued['lease'])
        t=tools()
        p=await t.plan(SESSION,active,allowed)
        active=store.planned(7,m['id'],active['lease'],p)
        # A completed stream containing only a promise is not a document.
        calls=[]
        async def short(s,m,step,save,**kw):
            calls.append(step['id']);return 'Я подготовлю подробный план. Давайте сначала соберём информацию.'
        t.draft=short
        await run_mission(store,7,active,SESSION,t,identity)
        incomplete=store.detail(7,m['id'])
        assert incomplete['status']=='failed' and incomplete['steps'][0]['error']=='mission_draft_incomplete'
        assert incomplete['steps'][1]['status']=='pending' and incomplete['steps'][1]['result'] is None
        async def finished(s,m,step,save,**kw):
            calls.append(step['id']);return BODY
        t.draft=finished
        queued=store.enqueue(7,m['id'],incomplete['revision']);active=store.activate(7,m['id'],queued['lease'])
        await run_mission(store,7,active,SESSION,t,identity)
        done=store.detail(7,m['id'])
        assert done['status']=='completed' and done['goal']==GOAL
        assert done['steps'][0]['attempt']==1 and calls==['s1','s1']
        result=done['steps'][1]['result'];body=base64.b64decode(result['file'],validate=True)
        assert body.startswith(b'%PDF') and result['data']['bytes']==len(body)
        assert result['data']['sha256']==hashlib.sha256(body).hexdigest()
        assert 'file' not in store.public(done)['steps'][1]['result']
    asyncio.run(scenario())
