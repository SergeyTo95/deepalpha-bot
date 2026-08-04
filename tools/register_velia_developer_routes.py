from __future__ import annotations

import ast
from pathlib import Path


TARGET = "setup_velia_mobile_streaming_route"
IMPORT_LINE = "from services.velia_developer_routes import setup_velia_developer_routes"


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def main() -> None:
    matches: list[tuple[Path, ast.Call, str]] = []
    for path in Path(".").rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "tests", "tools"} for part in path.parts):
            continue
        if path.as_posix().endswith("services/velia_mobile_streaming_service.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if TARGET + "(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == TARGET and len(node.args) >= 3:
                matches.append((path, node, text))

    if len(matches) != 1:
        found = [str(item[0]) for item in matches]
        raise SystemExit(f"Expected exactly one streaming route setup call, found {found}")

    path, call, text = matches[0]
    if "setup_velia_developer_routes(" in text:
        print(path)
        Path(".developer_registration_path").write_text(str(path), encoding="utf-8")
        return

    app_expr = ast.get_source_segment(text, call.args[0])
    routes_expr = ast.get_source_segment(text, call.args[2])
    if not app_expr or not routes_expr:
        raise SystemExit("Unable to resolve setup call arguments")

    lines = text.splitlines(keepends=True)
    insert_line = int(call.end_lineno or call.lineno)
    source_line = lines[int(call.lineno) - 1]
    indent = source_line[: len(source_line) - len(source_line.lstrip())]
    lines.insert(insert_line, f"{indent}setup_velia_developer_routes({app_expr}, {routes_expr})\n")
    patched = "".join(lines)

    if IMPORT_LINE not in patched:
        patched_lines = patched.splitlines(keepends=True)
        import_at = 0
        if patched_lines and patched_lines[0].startswith("#!"):
            import_at = 1
        if import_at < len(patched_lines) and "coding:" in patched_lines[import_at]:
            import_at += 1
        try:
            patched_tree = ast.parse(patched)
            if (
                patched_tree.body
                and isinstance(patched_tree.body[0], ast.Expr)
                and isinstance(getattr(patched_tree.body[0], "value", None), ast.Constant)
                and isinstance(patched_tree.body[0].value.value, str)
            ):
                import_at = max(import_at, int(patched_tree.body[0].end_lineno or 0))
            for node in patched_tree.body:
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    import_at = max(import_at, int(node.end_lineno or node.lineno))
        except SyntaxError as exc:
            raise SystemExit(f"Patched source failed before import insertion: {exc}") from exc
        patched_lines.insert(import_at, IMPORT_LINE + "\n")
        patched = "".join(patched_lines)

    ast.parse(patched)
    path.write_text(patched, encoding="utf-8")
    Path(".developer_registration_path").write_text(str(path), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
