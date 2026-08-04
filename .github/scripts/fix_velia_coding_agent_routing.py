from pathlib import Path


path = Path("services/velia_developer_chat_runtime_patch.py")
source = path.read_text(encoding="utf-8")
old = '''    active = coding_service.active_job(int(user_id), str(conversation_id))
    if not coding_service.should_handle(message, has_active_job=bool(active)):
        return None
'''
new = '''    coding_intent = (
        coding_service.is_coding_request(message)
        or coding_service.is_approval(message)
        or coding_service.is_cancel(message)
        or coding_service.is_status_request(message)
    )
    if not coding_intent:
        return None

    active = coding_service.active_job(int(user_id), str(conversation_id))
    if not coding_service.should_handle(message, has_active_job=bool(active)):
        return None
'''
if old not in source:
    raise SystemExit("coding gate marker missing")
source = source.replace(old, new, 1)
path.write_text(source, encoding="utf-8")
print("VELIA Coding Agent read-only routing gate applied")
