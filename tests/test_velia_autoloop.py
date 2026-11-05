import hashlib
import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from velia_autoloop import LoopStore, receipt, validate_rule
from velia_background_jobs import JobStore, JobError, public_job
from test_velia_flash import postgres_chat

CID = str(uuid.uuid4())
PROJECT = str(uuid.uuid4())


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    jobs = JobStore(lambda: sqlite3.connect(path, timeout=10), sqlite=True)
    jobs.initialize()
    loops = LoopStore(jobs)
    loops.initialize()
    return jobs, loops


def rule(loops, **kwargs):
    return loops.create(77, dict(conversation_id=CID, model='quantum', instruction='Check the project status',
        idempotency_key='rule-key-0001', **kwargs))


def emit(loops, rid, eid='event-key-0001', subject='issue#1', detail='A bug report'):
    return loops.emit(77, rid, dict(id=eid, type='manual', subject=subject, detail=detail))


def test_rules_recover_without_browser_and_event_admission_is_atomic(stores):
    jobs, loops = stores
    r = rule(loops)
    event = emit(loops, r['id'])
    restored = LoopStore(JobStore(jobs.connect, sqlite=True))
    restored.tick(lambda user, cfg: None)
    job = jobs.claim()
    assert job['model'] == 'quantum' and job['conversation_id'] == CID
    assert job['payload']['autoloop']['event_id'] == event['id']
    assert 'EVENT_DATA_UNTRUSTED' in job['payload']['content']
    restored.tick(lambda user, cfg: None)
    assert len(jobs.listing(77)) == 1
    assert emit(restored, r['id'])['job_id'] == job['id']


def test_event_burst_coalesces_pending_subject_but_never_changes_running_work(stores):
    jobs, loops = stores
    r = rule(loops, max_runs_per_day=3)
    emit(loops, r['id'])
    emit(loops, r['id'], 'event-key-0002', detail='Latest bug context')
    loops.tick(lambda *args: None)
    job = jobs.claim()
    assert 'Latest bug context' in job['payload']['content']
    emit(loops, r['id'], 'event-key-0003', detail='New information')
    loops.tick(lambda *args: None)
    assert jobs.get(77, job['id'])['payload'] == job['payload']
    jobs.finish(job, 'Fixed')
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 2


def test_changed_duplicate_payload_and_cross_account_controls_are_rejected(stores):
    _, loops = stores
    r = rule(loops)
    assert rule(loops)['id'] == r['id']
    with pytest.raises(JobError, match='idempotency_mode_mismatch'):
        rule(loops, interval_minutes=15)
    emit(loops, r['id'])
    with pytest.raises(JobError, match='idempotency_mode_mismatch'):
        emit(loops, r['id'], detail='Changed')
    assert loops.listing(78) == []
    with pytest.raises(JobError, match='autoloop_rule_not_found'):
        loops.toggle(78, r['id'], False)
    assert 'secret' not in json.dumps(loops.listing(77))


def test_queue_failure_rolls_back_event_to_pending(stores):
    jobs, loops = stores
    r = rule(loops)
    emit(loops, r['id'])
    def reject(cur, job):
        raise JobError('global_daily_limit_exceeded', 429)
    jobs.prepare = reject
    loops.tick(lambda *args: None)
    assert jobs.listing(77) == []
    jobs.prepare = None
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 1


def test_daily_limit_counts_dispatch_day_instead_of_event_creation_day(stores, monkeypatch):
    jobs, loops = stores
    import velia_autoloop as module
    clock = [864000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    r = rule(loops)
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    jobs.finish(jobs.claim(), 'Done')
    emit(loops, r['id'], 'event-key-0002')
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 1
    clock[0] += 86400
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 2
    jobs.finish(jobs.claim(), 'Done')
    emit(loops, r['id'], 'event-key-0003')
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 2


def test_timer_collapses_missed_intervals_and_disable_cancels_pending_events(stores, monkeypatch):
    jobs, loops = stores
    import velia_autoloop as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    r = rule(loops, interval_minutes=15)
    loops.tick(lambda *args: None)
    assert jobs.listing(77) == []
    clock[0] += 10 * 900
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 1
    assert loops.listing(77)[0]['next_at'] == clock[0] + 900
    jobs.finish(jobs.claim(), 'done')
    emit(loops, r['id'])
    loops.toggle(77, r['id'], False)
    loops.tick(lambda *args: None)
    assert len(jobs.listing(77)) == 1
    with pytest.raises(JobError, match='autoloop_rule_paused'):
        emit(loops, r['id'], 'event-key-0003')


def test_signature_and_repository_and_event_type_all_required(stores):
    _, loops = stores
    cfg = dict(conversation_id=CID, model='flash', instruction='Inspect this issue', project_id=PROJECT,
        event_types=['issues.opened'], idempotency_key='rule-key-github')
    r = loops.create(77, cfg, repository_full_name='owner/repo')
    secret = loops.webhook_secret(77, r['id'])
    raw = json.dumps(dict(repository={'full_name': 'owner/repo'}, action='opened', issue={'number': 1, 'body': 'fix'})).encode()
    sign = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    with pytest.raises(JobError, match='unauthorized'):
        loops.github(r['id'], raw, 'sha256=invalid', 'github-delivery-001', 'issues')
    with pytest.raises(JobError, match='autoloop_event_denied'):
        loops.github(r['id'], raw, sign, 'github-delivery-001', 'pull_request')
    assert loops.github(r['id'], raw, sign, 'github-delivery-001', 'issues')['status'] == 'pending'
    evil = raw.replace(b'owner/repo', b'other/repo')
    sign = 'sha256=' + hmac.new(secret.encode(), evil, hashlib.sha256).hexdigest()
    with pytest.raises(JobError, match='autoloop_event_denied'):
        loops.github(r['id'], evil, sign, 'github-delivery-002', 'issues')


def test_question_exits_worker_and_answer_resumes_same_job_model_and_history(stores):
    jobs, loops = stores
    r = rule(loops)
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    # Real prepare() snapshots chat history. This fixture supplies a minimal one.
    job = jobs.claim()
    jobs.checkpoint(job, {'messages': [{'role': 'user', 'content': 'Original request'}]})
    assert jobs.wait(job, 'Which branch?', options=['develop', 'main'])
    assert jobs.claim() is None
    assert public_job(jobs.get(77, job['id']))['question']['text'] == 'Which branch?'
    restored = JobStore(jobs.connect, sqlite=True)
    with pytest.raises(JobError, match='background_task_not_found'):
        restored.answer(78, job['id'], 'develop', 'answer-key-001')
    answered = restored.answer(77, job['id'], 'develop', 'answer-key-001')
    assert restored.answer(77, job['id'], 'develop', 'answer-key-001')['id'] == job['id']
    resumed = restored.claim()
    assert resumed['id'] == job['id'] and resumed['model'] == 'quantum'
    assert resumed['payload']['messages'][-2:] == [{'role': 'assistant', 'content': 'Which branch?'}, {'role': 'user', 'content': 'develop'}]
    assert resumed['payload']['autoloop']['hops'] == 1
    assert restored.finish(resumed, 'Done')
    assert not restored.finish(resumed, 'Duplicate')


def test_delayed_recheck_has_no_active_slot_and_keeps_hop_limit(stores, monkeypatch):
    jobs, loops = stores
    import velia_background_jobs as module
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    r = rule(loops)
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    job = jobs.claim()
    jobs.checkpoint(job, {'messages': []})
    assert jobs.wait(job, 'Check later', later_minutes=15)
    other = jobs.create(77, CID, 'pro', 'normal-request-001', {'content': 'Other task'})
    clock[0] += 901
    assert jobs.claim()['id'] == other['id']
    assert jobs.claim() is None
    jobs.finish(jobs.get(77, other['id']), 'done')
    next_round = jobs.claim()
    assert next_round['id'] == job['id']
    next_round['payload']['autoloop']['hops'] = 8
    jobs.checkpoint(next_round, {'autoloop': next_round['payload']['autoloop']})
    with pytest.raises(JobError, match='autoloop_round_limit'):
        jobs.wait(next_round, 'Again?', later_minutes=15)


def test_duplicate_dispatchers_do_not_admit_two_jobs(stores):
    jobs, loops = stores
    r = rule(loops)
    emit(loops, r['id'])
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: LoopStore(JobStore(jobs.connect, sqlite=True)).tick(lambda *args: None), range(2)))
    assert len(jobs.listing(77)) == 1


@pytest.mark.parametrize('patch', [
    {'cwd': '/tmp'}, {'access': 'full'}, {'model': 'unknown'}, {'interval_minutes': 1},
    {'max_runs_per_day': 100}, {'enabled': 'yes'}, {'kind': 'shell'},
    {'event_types': ['issues.opened']}, {'kind': 'coding'},
])
def test_rule_input_cannot_expand_execution_authority(patch):
    with pytest.raises(JobError):
        validate_rule({**dict(conversation_id=CID, model='flash', instruction='Do work', idempotency_key='rule-key-0001'), **patch})


def test_receipt_accepts_only_questions_or_bounded_delays():
    assert receipt('Done') == ('Done', None)
    assert receipt('Result\n```velia-autoloop\n{"ask":{"text":"Which?","options":["A","B"]}}\n```')[1]['ask']['text'] == 'Which?'
    for value in ({'later_minutes': 0}, {'later_minutes': True}, {'events': []}, {'ask': {'text': 'x', 'cwd': '/'}}, {'later_minutes': 15, 'hops': 0}):
        with pytest.raises(JobError, match='autoloop_invalid_receipt'):
            receipt('Result\n```velia-autoloop\n' + json.dumps(value) + '\n```')


def test_worker_suspends_and_resumes_selected_model_without_inference_while_waiting(stores, monkeypatch):
    import threading
    from services import velia_background_chat_service as background
    jobs, loops = stores
    jobs.prepare = lambda cur, j: {**j['payload'], 'messages': [{'role': 'user', 'content': j['payload']['content']}], 'language': 'ru'}
    r = rule(loops)
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    class Cursor:
        def execute(self, *args): pass
        def fetchone(self): return (True,)
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass
    monkeypatch.setattr(background.chat, 'get_connection', Connection)
    monkeypatch.setattr(background.chat, 'get_conversation', lambda *args: {'id': CID})
    monkeypatch.setattr(background, 'allowed', lambda *args: None)
    calls = []
    def generate(*args, **kw):
        calls.append(kw)
        return {'ok': True, 'text': 'Question\n```velia-autoloop\n{"ask":{"text":"Which?","options":["A","B"]}}\n```' if len(calls) == 1 else 'Final result'}
    monkeypatch.setattr(background.quantum, 'generate', generate)
    assert background.run_one(jobs, threading.Event())
    job = jobs.listing(77)[0]
    assert job['status'] == 'waiting_user'
    assert not background.run_one(jobs, threading.Event())
    assert len(calls) == 1
    jobs.answer(77, job['id'], 'A', 'answer-key-001')
    assert background.run_one(jobs, threading.Event())
    assert len(calls) == 2 and all(c['chat_mode'] == 'quantum' for c in calls)
    assert calls[1]['messages'][-1] == {'role': 'user', 'content': 'A'}
    assert jobs.get(77, job['id'])['result'] == 'Final result'


@pytest.mark.asyncio
async def test_rule_http_requires_account_and_never_lists_webhook_secret(stores, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from services import velia_autoloop_service as service, velia_background_chat_service as background
    jobs, loops = stores
    monkeypatch.setattr(background, 'enabled', lambda: True)
    monkeypatch.setattr(background.chat, 'get_conversation', lambda u, cid: {'id': cid} if u == 77 else None)
    monkeypatch.setattr(service, 'authorize', lambda *args: None)
    monkeypatch.setattr(service, 'question', lambda *args: None)
    routes = SimpleNamespace(_mobile_api_available=lambda: True,
        _require_mobile_auth=lambda req: {'user_id': 77} if req.headers.get('Authorization') == 'Bearer owner' else None,
        _read_json=lambda req: req.json(), _json_response=lambda data, status=200: web.json_response(data, status=status))
    app = web.Application()
    service.setup_routes(app, routes, jobs, loops, asyncio.Event())
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/mobile-api/v1/autoloop/rules')).status == 401
        data = dict(conversation_id=CID, model='flash', instruction='Summarize', idempotency_key='rule-key-0001', interval_minutes=15)
        headers = {'Authorization': 'Bearer owner'}
        response = await client.post('/mobile-api/v1/autoloop/rules', headers=headers, json=data)
        assert response.status == 201
        value = await response.json()
        rid = value['rule']['id']
        response = await client.get('/mobile-api/v1/autoloop/rules', headers=headers)
        assert 'secret' not in await response.text()
        response = await client.post(f'/mobile-api/v1/autoloop/rules/{rid}/events', headers=headers,
            json=dict(id='event-key-001', type='manual', subject='test', detail='Evidence'))
        assert response.status == 202
        response = await client.post(f'/mobile-api/v1/autoloop/rules/{rid}/disable', headers=headers, json={})
        assert (await response.json())['rule']['enabled'] is False


def test_postgres_event_question_answer_and_final_publication_are_durable(monkeypatch, postgres_chat):
    from services import velia_background_chat_service as background, velia_attachment_service as files
    _, connect = postgres_chat
    monkeypatch.setattr(files, 'get_connection', connect)
    files.ensure_velia_attachment_tables()
    monkeypatch.setattr(background.quantum, 'account_language', lambda *args: 'ru')
    monkeypatch.setattr(background.quantum, 'free_budget_error', lambda *args: None)
    from services import velia_agent_builder_service as builder
    monkeypatch.setattr(builder, 'prompt_context_for_conversation', lambda *args: '')
    cid = background.chat.create_conversation(77)['id']
    jobs = background.store()
    jobs.initialize()
    loops = LoopStore(jobs)
    loops.initialize()
    r = loops.create(77, dict(conversation_id=cid, model='flash', instruction='Check project', idempotency_key='rule-key-pg'))
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    job = jobs.claim()
    assert jobs.wait(job, 'Choose a branch', options=['develop'])
    assert jobs.claim() is None
    jobs.answer(77, job['id'], 'develop', 'answer-key-pg')
    assert jobs.get(77, job['id'])['status'] == 'queued'
    with pytest.raises(JobError, match='background_task_not_found'):
        jobs.answer(78, job['id'], 'main', 'cross-owner-key')
    assert jobs.finish(jobs.claim(), 'Verified result')
    messages = background.chat.list_messages(77, cid)
    assert [m['content'] for m in messages if m['content'] in {'Choose a branch','develop','Verified result'}] == ['Choose a branch','develop','Verified result']
    with connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT job_id FROM velia_autoloop_events WHERE id=%s', (job['payload']['autoloop']['event_id'],))
        assert cur.fetchone()[0] == job['id']


def test_coding_loop_uses_existing_plan_approval_and_never_replays_uncertain_write(monkeypatch, stores):
    from services import velia_autoloop_service as service, velia_superagent_chat_service as superagent
    from services import velia_developer_coding_service as coding, velia_developer_chat_runtime_patch as developer
    jobs, loops = stores
    project = {'id': PROJECT}
    monkeypatch.setattr(service, 'authorize', lambda *args: project)
    monkeypatch.setattr(developer, '_bind_project', lambda *args: None)
    active = [None]
    monkeypatch.setattr(coding, 'active_job', lambda *args: active[0])
    calls = []
    def execute(*args, **kw):
        calls.append(kw)
        active[0] = {'status': 'planned', 'project_id': PROJECT}
        return {'ok': True, 'text': 'Plan and token quote. Reply Execute the plan.'}
    monkeypatch.setattr(superagent, 'route_free', execute)
    r = rule(loops, kind='coding', project_id=PROJECT)
    emit(loops, r['id'])
    loops.tick(lambda *args: None)
    job = jobs.claim()
    jobs.checkpoint(job, {'messages': []})
    assert service.execute_coding(job, jobs) is None
    assert jobs.get(77, job['id'])['status'] == 'waiting_user' and len(calls) == 1
    jobs.answer(77, job['id'], 'Execute the plan', 'answer-key-code')
    resumed = jobs.claim()
    jobs.checkpoint(resumed, {'coding_inflight': True})
    with pytest.raises(JobError, match='autoloop_execution_uncertain'):
        service.execute_coding(resumed, jobs)
    assert len(calls) == 1 and calls[0]['chat_mode'] == 'quantum'
