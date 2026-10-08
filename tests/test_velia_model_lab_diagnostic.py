"""Exercise the production adapter without importing Telegram/DB integrations."""
import ast
from pathlib import Path
import pytest
from research.deepseek.evaluation import cases

SOURCE = Path(__file__).resolve().parents[1] / 'services/velia_model_lab_service.py'

def functions(*names, **namespace):
    tree = ast.parse(SOURCE.read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(tree, str(SOURCE), 'exec'), namespace)
    return namespace

@pytest.mark.parametrize('case', cases(), ids=lambda c: c['key'])
def test_adapter_checks_every_reference(case):
    evaluate = functions('evaluate')['evaluate']
    assert evaluate(case, dict(ok=True, text=case['target'], finish_reason='stop')) == 'passed'
    assert evaluate(case, dict(ok=True, text=case['target'], finish_reason='length')) == 'error'

@pytest.mark.parametrize('suite,kind', [('unknown','benchmark'), ('velia-deepseek-diagnostic-v1','research')])
def test_suite_rejected_before_queue_or_provider(suite, kind):
    enqueue = functions('enqueue')['enqueue']
    with pytest.raises(ValueError, match='Неизвестный набор'):
        enqueue(1, kind, 'goal', 'label', 'id', suite)

@pytest.mark.parametrize('case', cases(), ids=lambda c: c['key'])
def test_diagnostic_holdout_cannot_enter_training(case):
    ns = functions('add_example', 'diagnostic_holdouts', _owner=lambda n:n, builtin_cases=lambda:[])
    with pytest.raises(ValueError, match='контрольные задачи'):
        ns['add_example'](1, case['messages'][-1]['content'], case['target'], 'train', True)
