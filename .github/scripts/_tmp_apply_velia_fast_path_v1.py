from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}: {old!r}; found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = Path("services/velia_developer_chat_runtime_patch.py")
replace_once(
    runtime,
    "from services import velia_developer_agent_service as agent_service",
    "from services import velia_developer_fast_path_service as agent_service",
)

routes = Path("services/velia_developer_routes.py")
replace_once(
    routes,
    "from services import velia_developer_agent_service as agent_service",
    "from services import velia_developer_fast_path_service as agent_service",
)

gateway = Path("services/kimi_gateway.py")
replace_once(
    gateway,
    '''def _feature_default_completion_tokens(feature: str) -> int:\n    if feature == "velia_file_vision":\n        return 2048\n    return 8192 if feature in _HIGH_REASONING_FEATURES else 4096\n''',
    '''def _feature_default_completion_tokens(feature: str) -> int:\n    if feature in {"velia_file_vision", "velia_developer_fast"}:\n        return 2048\n    return 8192 if feature in _HIGH_REASONING_FEATURES else 4096\n''',
)

workflow = Path(".github/workflows/velia-developer-readonly.yml")
text = workflow.read_text(encoding="utf-8")
text = text.replace(
    "            services/velia_developer_agent_service.py \\\n",
    "            services/velia_developer_agent_service.py \\\n            services/velia_developer_fast_path_service.py \\\n",
    1,
)
text = text.replace(
    "            tests/test_velia_developer_agent_service.py \\\n",
    "            tests/test_velia_developer_agent_service.py \\\n            tests/test_velia_developer_fast_path_service.py \\\n",
    1,
)
needle = '''          grep -q 'PROVIDER_REPAIR:' services/velia_developer_agent_service.py\n          ! grep -q 'max_attempts=1' services/velia_developer_agent_service.py\n'''
replacement = '''          grep -q 'PROVIDER_REPAIR:' services/velia_developer_agent_service.py\n          ! grep -q 'max_attempts=1' services/velia_developer_agent_service.py\n          grep -q 'VELIA_DEVELOPER_MAX_MODEL_CALLS' services/velia_developer_fast_path_service.py\n          grep -q 'VELIA_DEVELOPER_MAX_COST_USD' services/velia_developer_fast_path_service.py\n          grep -q 'feature="velia_developer_fast"' services/velia_developer_fast_path_service.py\n          grep -q 'max_attempts=1' services/velia_developer_fast_path_service.py\n          grep -q 'VELIA_DEVELOPER_RESULT_CACHE_TTL_SECONDS' services/velia_developer_fast_path_service.py\n          grep -q 'velia_developer_fast_path_service as agent_service' services/velia_developer_chat_runtime_patch.py\n          grep -q 'velia_developer_fast_path_service as agent_service' services/velia_developer_routes.py\n'''
if needle not in text:
    raise RuntimeError("Developer resilience contract block changed")
text = text.replace(needle, replacement, 1)
workflow.write_text(text, encoding="utf-8")

bootstrap_test = Path("tests/test_velia_developer_fast_path_bootstrap.py")
bootstrap_test.write_text(
    '''from pathlib import Path\n\nfrom services import kimi_gateway\n\n\ndef test_ordinary_chat_and_developer_route_use_fast_path():\n    runtime = Path("services/velia_developer_chat_runtime_patch.py").read_text(encoding="utf-8")\n    routes = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")\n    assert "velia_developer_fast_path_service as agent_service" in runtime\n    assert "velia_developer_fast_path_service as agent_service" in routes\n    assert "velia_developer_agent_service as agent_service" not in runtime\n    assert "velia_developer_agent_service as agent_service" not in routes\n\n\ndef test_fast_path_completion_default_is_bounded():\n    assert kimi_gateway._feature_default_completion_tokens("velia_developer_fast") == 2048\n    assert kimi_gateway._initial_completion_limit("velia_developer_fast", 512) == 2048\n''',
    encoding="utf-8",
)
