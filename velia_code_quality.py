"""Bounded, non-executing checks for changed source and shared engineering guidance."""
import ast
import hashlib
import json
import warnings
from pathlib import Path

POLICY = json.loads((Path(__file__).parent / 'desktop/src/engineering-policy.json').read_text(encoding='utf-8'))
MAX_FILE_BYTES = 200000
MAX_TOTAL_BYTES = 800000
MAX_NODES = 25000


class QualityError(ValueError):
    def __init__(self, code, path='', line=None):
        self.code = code
        self.detail = path + (':'+str(line) if line else '')
        super().__init__(code)


def guidance(stage='implementation', *, compact=False):
    if compact:
        return POLICY['brief']
    return 'VELIA ENGINEERING:\n'+'\n'.join(POLICY['rules'])+'\n'+POLICY['stages'][stage]


def spec_guidance(stage='agent'):
    return POLICY['spec_workflow'][stage]


def _strict_json(source):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError('nonfinite_json_number')
    return json.loads(source, object_pairs_hook=unique, parse_constant=invalid)


def _parse(path, source):
    if path.endswith('.json'):
        _strict_json(source)
        return None
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        tree = ast.parse(source, filename='<velia-candidate>')
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise QualityError('developer_coding_quality_limit', path)
    # Compilation validates constructs such as module-level return without
    # importing or executing any project code.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        compile(tree, '<velia-candidate>', 'exec', dont_inherit=True)
    return tree


def _functions(node, prefix=''):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = prefix+child.name
            yield name, child
            yield from _functions(child, name+'.')
        elif isinstance(child, ast.ClassDef):
            yield from _functions(child, prefix+child.name+'.')
        else:
            yield from _functions(child, prefix)


def _body_nodes(function):
    pending = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def _metrics(tree):
    if tree is None:
        return {}
    result = {}
    for name, function in _functions(tree):
        nodes = list(_body_nodes(function))
        decisions = sum(isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp)) for n in nodes)
        decisions += sum(len(n.values)-1 for n in nodes if isinstance(n, ast.BoolOp))
        decisions += sum(1+len(n.ifs) for n in nodes if isinstance(n, ast.comprehension))
        decisions += sum(len(n.cases) for n in nodes if isinstance(n, ast.Match))
        body = ast.dump(ast.Module(body=function.body, type_ignores=[]), include_attributes=False)
        result[name] = {'complexity_estimate':1+decisions, 'line':function.lineno,
            'signature':hashlib.sha256(body.encode()).hexdigest() if len(nodes)>=20 else None}
    return result


def _warnings(path, before, after):
    previous, current = _metrics(before), _metrics(after)
    warnings, seen = [], {}
    for name, metric in sorted(current.items()):
        if metric['complexity_estimate'] > max(10, previous.get(name, {}).get('complexity_estimate', 0)):
            warnings.append({'path':path,'line':metric['line'],'kind':'complexity_increased',
                'complexity_estimate':metric['complexity_estimate']})
        signature = metric['signature']
        if signature:
            if signature in seen:
                other = seen[signature]
                other_metric = current[other]
                unchanged = previous.get(name,{}).get('signature') == signature and previous.get(other,{}).get('signature') == signature
                if not unchanged:
                    warnings.append({'path':path,'line':metric['line'],'other_line':other_metric['line'],'kind':'exact_body_duplication'})
            else:
                seen[signature] = name
        if len(warnings)>=20:return warnings[:20]
    return warnings[:20]


def inspect_changes(before, after):
    """Check full changed files in memory. Never execute repository code or tests."""
    paths = sorted(path for path in after if after[path] != before.get(path) and after[path] is not None)
    if len(paths)>16:
        raise QualityError('developer_coding_quality_limit')
    report = {'policy':POLICY['version'],'checks':[],'warnings':[], 'tests':'not_run',
        'coverage':'not_measured','crap':'not_measured','mutation':'not_measured'}
    used = 0
    for path in paths:
        source = after[path]
        try:size = len(source.encode('utf-8'))
        except UnicodeError:raise QualityError('developer_coding_syntax_invalid', path) from None
        used += size
        if size>MAX_FILE_BYTES or used>MAX_TOTAL_BYTES:
            raise QualityError('developer_coding_quality_limit', path)
        if not path.endswith(('.py','.json')):
            report['checks'].append({'path':path,'check':'syntax','status':'not_run','reason':'parser_unavailable'})
            continue
        old = None
        baseline_valid = True
        if before.get(path) is not None:
            try:
                if len(before[path].encode('utf-8'))>MAX_FILE_BYTES:raise ValueError('baseline_limit')
                old = _parse(path, before[path])
            except (SyntaxError, ValueError, RecursionError):baseline_valid = False
        try:
            tree = _parse(path, source)
        except QualityError:
            raise
        except (SyntaxError, ValueError) as exc:
            if baseline_valid:
                raise QualityError('developer_coding_syntax_invalid', path, getattr(exc,'lineno',None)) from None
            report['checks'].append({'path':path,'check':'syntax','status':'not_run','reason':'baseline_or_language_version_unsupported'})
            continue
        except RecursionError:
            raise QualityError('developer_coding_quality_limit', path) from None
        report['checks'].append({'path':path,'check':'syntax','status':'passed'})
        if path.endswith('.py'):
            report['warnings'].extend(_warnings(path, old, tree))
    report['warnings'] = report['warnings'][:20]
    return report
