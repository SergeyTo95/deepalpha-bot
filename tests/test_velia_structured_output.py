import pytest
from services.velia_structured_output import strict_json_loads
from desktop.request_intent import parse_decision
from desktop.work_runtime import structured
from desktop.work_store import WorkError

@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"a":{"b":1,"b":2}}', 'NaN', 'Infinity', '-Infinity', '1e999', '{"a":NaN}'])
def test_ambiguous_or_nonfinite_json_rejected(text):
    with pytest.raises(ValueError):strict_json_loads(text)

@pytest.mark.parametrize('text', ['{"язык":"Türkçe 中文 العربية","values":[true,null,1]}', '[1,2,3]', '"hello"'])
def test_valid_multilingual_json_preserved(text):
    import json
    assert strict_json_loads(text)==json.loads(text)

def test_size_and_depth_limits():
    with pytest.raises(ValueError):strict_json_loads('"яя"', max_bytes=5)
    with pytest.raises(ValueError):strict_json_loads('[[[0]]]', max_depth=2)
    assert strict_json_loads('[[0]]', max_depth=2)==[[0]]

def test_work_cannot_override_decline_with_duplicate_decision():
    with pytest.raises(WorkError) as exc:
        structured('{"decision":"decline","decision":"proceed","plan":"go"}', 'manager')
    assert exc.value.code=='invalid_role_output'

def test_chat_cannot_override_search_decision():
    result={'choices':[{'message':{'tool_calls':[{'function':{'name':'understand_request','arguments':'{"action":"clarify","action":"search","quote":"","query":"anything"}'}}]}}]}
    with pytest.raises(ValueError):parse_decision(result,'hello')
