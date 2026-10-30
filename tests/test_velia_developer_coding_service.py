import json

import pytest

from services import velia_developer_coding_service as coding
from velia_code_quality import inspect_changes, QualityError


PROJECT = {
    "id": "project-1",
    "installation_id": 11,
    "repository_id": 22,
    "repository_full_name": "owner/repo",
    "selected_branch": "develop",
}


def test_coding_intent_and_control_messages():
    assert coding.is_coding_request("Реализуй новый endpoint в backend") is True
    assert coding.is_coding_request("Где находится endpoint в backend?") is False
    assert coding.is_approval("Выполняй план") is True
    assert coding.is_cancel("Отмени план") is True
    assert coding.is_status_request("Статус") is True


def test_normalize_plan_keeps_small_ordered_tasks():
    plan = coding._normalize_plan(
        {
            "title": "Add endpoint",
            "summary": "Add a guarded endpoint and tests.",
            "steps": [
                {
                    "title": "Implement route",
                    "objective": "Add the route",
                    "files": ["services/example.py", "services/example.py"],
                    "checks": ["compile"],
                },
                {
                    "title": "Test route",
                    "objective": "Add regression coverage",
                    "files": ["tests/test_example.py"],
                    "checks": ["pytest"],
                },
            ],
            "suggestions": ["Add metrics"],
        }
    )
    assert [step["index"] for step in plan["steps"]] == [1, 2]
    assert plan["steps"][0]["files"] == ["services/example.py"]
    assert plan["suggestions"] == ["Add metrics"]


def test_apply_patch_payload_uses_exact_unique_replacements():
    payload = {
        "summary": "Update greeting",
        "operations": [
            {
                "op": "replace",
                "path": "services/example.py",
                "old": "return 'old'",
                "new": "return 'new'",
            },
            {
                "op": "create",
                "path": "tests/test_example.py",
                "content": "def test_ok():\n    assert True\n",
            },
        ],
    }
    operations, states = coding._apply_patch_payload(
        payload,
        allowed_files=["services/example.py", "tests/test_example.py"],
        states={"services/example.py": "def value():\n    return 'old'\n", "tests/test_example.py": None},
    )
    assert states["services/example.py"].endswith("return 'new'\n")
    assert [item["op"] for item in operations] == ["upsert", "upsert"]


def test_apply_patch_rejects_paths_outside_plan():
    with pytest.raises(coding.DeveloperCodingError) as exc:
        coding._apply_patch_payload(
            {
                "operations": [
                    {"op": "create", "path": "unexpected.py", "content": "x = 1\n"}
                ]
            },
            allowed_files=["services/example.py"],
            states={"services/example.py": "x = 0\n"},
        )
    assert exc.value.code == "developer_coding_path_outside_plan"


@pytest.mark.parametrize('path,source', [('app.py','def broken(:\n'),('config.json','{"x":1,"x":2}'),('config.json','{"x":NaN}')])
def test_quality_gate_rejects_invalid_new_source_without_echoing_source(path,source):
    with pytest.raises(QualityError) as exc:
        inspect_changes({path:None},{path:source})
    assert exc.value.code=='developer_coding_syntax_invalid' and source not in exc.value.detail


def test_quality_checks_do_not_execute_source_or_claim_project_tests_passed():
    report=inspect_changes({'app.py':None,'ui.ts':None},
        {'app.py':'raise RuntimeError("source must not execute")\n','ui.ts':'export const x: number = 1;\n'})
    assert report['checks']==[{'path':'app.py','check':'syntax','status':'passed'},
        {'path':'ui.ts','check':'syntax','status':'not_run','reason':'parser_unavailable'}]
    assert report['tests']=='not_run' and report['crap']==report['mutation']==report['coverage']=='not_measured'


def test_legacy_syntax_is_not_misrepresented_as_a_pass_and_resource_limits_are_bounded(monkeypatch):
    import velia_code_quality as quality
    report=inspect_changes({'old.py':'print "old syntax"\n'},{'old.py':'print "updated legacy syntax"\n'})
    assert report['checks'][0]['status']=='not_run'
    monkeypatch.setattr(quality,'MAX_FILE_BYTES',8)
    with pytest.raises(QualityError,match='developer_coding_quality_limit'):
        inspect_changes({'app.py':None},{'app.py':'number = 123456789\n'})


def test_new_duplicate_bodies_are_advisory_and_unchanged_legacy_duplicates_are_not_reflagged():
    body='    total = value + 1\n    if total > 2:\n        total += value\n    return total * 2\n'
    before='def first(value):\n'+body
    after=before+'\ndef second(value):\n'+body
    report=inspect_changes({'app.py':before},{'app.py':after})
    assert any(w['kind']=='exact_body_duplication' for w in report['warnings'])
    preserved=inspect_changes({'app.py':after},{'app.py':after+'\n# documentation\n'})
    assert not preserved['warnings']


def test_execute_step_repairs_invalid_syntax_before_a_single_commit(monkeypatch):
    monkeypatch.setattr(coding,'_step_context',lambda *a:('New file',{'app.py':None}))
    monkeypatch.setattr(coding.fast_path,'_estimate_cost',lambda *a:0.001)
    responses=[{'operations':[{'op':'create','path':'app.py','content':'def broken(:\n'}]},
        {'operations':[{'op':'create','path':'app.py','content':'def answer():\n    return 42\n'}]}]
    calls=[];commits=[]
    def model(**kwargs):calls.append(kwargs);return {'text':json.dumps(responses.pop(0)),'estimated_cost_usd':0.001,'usage':{}}
    def commit(*args,**kwargs):commits.append(kwargs);return {'files':['app.py'],'commit_sha':'fixture-sha'}
    monkeypatch.setattr(coding,'_model_call',model);monkeypatch.setattr(coding.write_service,'commit_operations',commit)
    job={'job_id':'fixture','goal':'Implement answer','base_branch':'main','work_branch':'velia/test','total_steps':1,'plan':{}}
    step={'index':1,'title':'Answer','objective':'Return 42','files':['app.py'],'checks':['pytest']}
    result=coding._execute_step(user_id=1,project=PROJECT,job=job,step=step,step_number=1)
    assert len(commits)==1 and len(calls)==2
    assert 'developer_coding_syntax_invalid' in calls[1]['prompt']
    assert commits[0]['operations'][0]['content']=='def answer():\n    return 42\n'
    assert result['quality']['tests']=='not_run' and result['quality']['checks'][0]['status']=='passed'
    assert result['estimated_cost_usd']==0.002


def test_exhausted_syntax_repair_never_commits_invalid_code(monkeypatch):
    monkeypatch.setattr(coding,'_step_context',lambda *a:('New file',{'app.py':None}))
    monkeypatch.setattr(coding.fast_path,'_estimate_cost',lambda *a:0.001)
    monkeypatch.setattr(coding,'_model_call',lambda **kw:{'text':json.dumps({'operations':[{'op':'create','path':'app.py','content':'return 42\n'}]}),'usage':{}})
    monkeypatch.setattr(coding.write_service,'commit_operations',lambda *a,**kw:pytest.fail('Invalid code reached GitHub'))
    job={'job_id':'fixture','goal':'Implement answer','base_branch':'main','work_branch':'velia/test','total_steps':1,'plan':{}}
    step={'index':1,'title':'Answer','objective':'Return 42','files':['app.py'],'checks':[]}
    with pytest.raises(coding.DeveloperCodingError,match='developer_coding_syntax_invalid'):
        coding._execute_step(user_id=1,project=PROJECT,job=job,step=step,step_number=1)


def test_plan_job_uses_one_low_cost_model_call(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true")
    monkeypatch.setattr(coding, "_candidate_files", lambda project, goal: ({"entries": []}, ["endpoint"], []))
    monkeypatch.setattr(coding, "_planning_evidence", lambda project, queries, candidates: "No files")
    captured = {}

    def fake_model_call(**kwargs):
        captured.update(kwargs)
        return {
            "text": json.dumps(
                {
                    "title": "Add endpoint",
                    "summary": "Add endpoint and tests",
                    "steps": [
                        {
                            "title": "Implement",
                            "objective": "Create the endpoint",
                            "files": ["services/example.py"],
                            "checks": ["pytest"],
                        }
                    ],
                    "suggestions": ["Add metrics"],
                }
            ),
            "estimated_cost_usd": 0.01,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

    monkeypatch.setattr(coding, "_model_call", fake_model_call)
    monkeypatch.setattr(
        coding,
        "_insert_job",
        lambda **kwargs: {
            "job_id": "job-1",
            "status": "planned",
            "plan": kwargs["plan"],
            "total_steps": len(kwargs["plan"]["steps"]),
        },
    )
    job = coding.plan_job(
        user_id=1,
        conversation_id="conversation-1",
        project=PROJECT,
        goal="Реализуй endpoint в backend",
    )
    assert job["total_steps"] == 1
    assert captured["feature"] == "velia_developer_coding_plan"
    assert captured["max_tokens"] <= 1800


def test_execute_job_runs_steps_in_order_and_opens_draft_pr(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true")
    job = {
        "job_id": "job-1",
        "project_id": "project-1",
        "status": "planned",
        "goal": "Implement feature",
        "base_branch": "develop",
        "work_branch": "",
        "estimated_cost_usd": 0.01,
        "total_steps": 2,
        "plan": {
            "title": "Implement feature",
            "summary": "Two small tasks",
            "steps": [
                {"index": 1, "title": "First", "objective": "First task", "files": ["a.py"], "checks": []},
                {"index": 2, "title": "Second", "objective": "Second task", "files": ["b.py"], "checks": []},
            ],
            "suggestions": ["Add docs"],
        },
    }
    monkeypatch.setattr(coding, "active_job", lambda user_id, conversation_id: dict(job))
    monkeypatch.setattr(coding.write_service, "require_write_permissions", lambda project: {})
    monkeypatch.setattr(coding, "_work_branch", lambda value: "velia/task-1")
    monkeypatch.setattr(coding.write_service, "create_work_branch", lambda project, branch: {"branch": branch})
    updates = []
    monkeypatch.setattr(coding, "_update_job", lambda job_id, **fields: updates.append(fields))
    seen = []

    def fake_execute_step(**kwargs):
        index = kwargs["step_number"]
        seen.append(index)
        return {
            "index": index,
            "title": kwargs["step"]["title"],
            "summary": "done",
            "files": kwargs["step"]["files"],
            "commit_sha": f"sha-{index}",
            "checks": [],
            "suggestions": [],
            "estimated_cost_usd": 0.02,
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    monkeypatch.setattr(coding, "_execute_step", fake_execute_step)
    monkeypatch.setattr(
        coding.write_service,
        "create_draft_pull_request",
        lambda *args, **kwargs: {"number": 7, "url": "https://github.com/owner/repo/pull/7", "draft": True},
    )
    monkeypatch.setattr(coding.write_service, "commit_status", lambda project, sha: {"total": 0, "checks": []})
    progress = []
    result = coding.execute_job(
        user_id=1,
        conversation_id="conversation-1",
        project=PROJECT,
        on_progress=lambda phase, details: progress.append((phase, details)),
    )
    assert seen == [1, 2]
    assert result["pull_request"]["draft"] is True
    assert result["pull_request"]["number"] == 7
    assert [phase for phase, _ in progress].count("step_start") == 2
    assert [phase for phase, _ in progress].count("step_complete") == 2
    assert updates[-1]["status"] == "completed"


def test_format_plan_requires_one_explicit_approval():
    text = coding.format_plan(
        {
            "plan": {
                "summary": "Change code",
                "steps": [
                    {"index": 1, "title": "Task", "objective": "Do it", "files": ["a.py"], "checks": []}
                ],
                "suggestions": [],
            }
        },
        "Реализуй изменение",
    )
    assert "Выполняй план" in text
    assert "a.py" in text


def test_specification_is_persistable_and_acceptance_is_not_a_claim_of_success():
    raw={'summary':'Build export','specification':{'goal':'Owner downloads their data','constraints':['Owner scoped'],'acceptance':['Other owners receive 404'],'assumptions':['Reuse auth']},'steps':[{'objective':'Owner scoped export','files':['app.py'],'checks':['Check other owner gets 404']}]}
    plan=coding._normalize_plan(raw)
    assert plan['specification']['acceptance']==['Other owners receive 404']
    assert plan['specification']['verification']=='not_run'
    job={'plan':plan,'goal':'Build export','total_steps':1}
    prompt=coding._step_prompt(PROJECT,job,plan['steps'][0],'source')
    assert 'Other owners receive 404' in prompt and 'Owner scoped export' in prompt
    body=coding._pr_body(job,[])
    assert '- [ ] Other owners receive 404' in body
    assert 'verification pending' in body
