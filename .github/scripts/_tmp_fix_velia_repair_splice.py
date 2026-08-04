from pathlib import Path

path = Path('.github/scripts/_tmp_apply_velia_repair_budget.py')
text = path.read_text(encoding='utf-8')
old = "        '    if not answer:\\n',\n        new_loop,"
new = (
    "        '\\n    if not answer:\\n"
    "        raise DeveloperAgentError(\"developer_generation_failed\")\\n',\n"
    "        new_loop,"
)
if old not in text:
    raise RuntimeError('temporary splice marker not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
