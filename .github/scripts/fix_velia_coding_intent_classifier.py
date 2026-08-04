from pathlib import Path


path = Path("services/velia_developer_coding_service.py")
source = path.read_text(encoding="utf-8")
old = '''_WRITE_INTENT_RE = re.compile(
    r"(?:\\b(?:implement|fix|add|create|write|change|update|delete|remove|refactor|build)\\b|"
    r"(?:реализу|исправ|добав|созда|напиш|измени|обнови|удали|отрефактор|сделай))",
    re.IGNORECASE,
)
'''
new = '''_WRITE_INTENT_RE = re.compile(
    r"(?:\\b(?:implement|fix|add|create|write|change|update|delete|remove|refactor|build|modify)\\b|"
    r"\\b(?:реализуй(?:те)?|исправь(?:те)?|добавь(?:те)?|создай(?:те)?|напиши(?:те)?|"
    r"измени(?:те)?|обнови(?:те)?|удали(?:те)?|сделай(?:те)?|внедри(?:те)?|"
    r"перепиши(?:те)?|отрефакторируй(?:те)?)\\b|"
    r"\\b(?:нужно|надо|хочу|требуется|можно)\\s+(?:реализовать|исправить|добавить|"
    r"создать|написать|изменить|обновить|удалить|сделать|внедрить|переписать|"
    r"отрефакторить)\\b)",
    re.IGNORECASE,
)
'''
if old not in source:
    raise SystemExit("write intent regex marker missing")
source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8")
print("VELIA Coding Agent intent classifier narrowed")
