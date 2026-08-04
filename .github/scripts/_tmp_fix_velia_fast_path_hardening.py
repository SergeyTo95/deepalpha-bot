from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


fast_path = Path("services/velia_developer_fast_path_service.py")
replace_once(
    fast_path,
    '        header_reserve = len(path) + 80\n',
    '        header_reserve = len(f"FILE {path} [L{start_line}-L999999]\\n") + len("\\nEND FILE\\n")\n',
)
replace_once(
    fast_path,
    '        if remaining <= 64:\n',
    '        if remaining <= 8:\n',
)

gateway = Path("services/kimi_gateway.py")
replace_once(
    gateway,
    '        fast_cap = max(512, env_int("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", 2048) or 2048)\n        return min(fast_cap, max(512, requested))\n',
    '        fast_cap = max(2048, env_int("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", 2048) or 2048)\n        return min(fast_cap, max(2048, requested))\n',
)
