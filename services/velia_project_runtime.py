"""Connect passports and read-only DeepAlpha to the final hardened chat path."""
import logging
import re
from contextvars import ContextVar

from services import velia_project_service as projects

IN_DEEPALPHA = ContextVar("velia_deepalpha", default=False)
logger = logging.getLogger(__name__)


def install(chat):
    if getattr(chat, "_velia_projects_installed", False):
        return
    original_prompt = chat._build_prompt
    original_generate = chat.generate_velia_chat_result

    def build_prompt(user_id, conversation_id):
        resource = projects.resource_context(user_id, conversation_id)
        token = IN_DEEPALPHA.set(bool(resource and resource["kind"] == "deepalpha"))
        try:
            return original_prompt(user_id, conversation_id) + projects.passport_prompt(resource)
        finally:
            IN_DEEPALPHA.reset(token)

    def generate(prompt, *, user_id, conversation_id, request_id=None):
        resource = projects.resource_context(user_id, conversation_id)
        if not resource or resource["kind"] != "deepalpha":
            return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
        from services.velia_deepalpha_research import collect_evidence, evidence_prompt, evidence_footer
        from services.velia_live_plugins_patch import _latest_user_message, _env_bool
        from services.velia_llm_service import generate_velia_chat_result
        if not _env_bool("VELIA_LIVE_PLUGINS_ENABLED", True):
            return {"ok": False, "text": "", "reason": "research_disabled", "request_id": request_id}
        latest = _latest_user_message(user_id, conversation_id)
        russian = bool(re.search(r"[А-Яа-яЁё]", latest))
        # Include the explicit research seed for short follow-ups, never the
        # private project passport or another conversation's messages in search.
        query = latest if len(latest) > 160 or latest == resource["seed_query"] else resource["seed_query"] + " " + latest
        evidence = collect_evidence(user_id, query)
        evidence["project_revision"] = resource.get("revision")
        projects.save_evidence(user_id, conversation_id, request_id, evidence)
        if evidence["status"] == "unavailable":
            text = ("Не удалось получить проверяемые источники для этого исследования. "
                    "Попробуйте уточнить рынок, событие или период и повторить запрос позже."
                    if russian else "Verifiable sources are unavailable for this research. "
                    "Specify the market, event or time horizon and retry later.")
            return {"ok": True, "text": text, "request_id": request_id, "estimated_cost_usd": 0.0}
        # The ordinary sender has already applied access, budget and idempotency
        # gates. Bypass action/media planners for research; save its final answer
        # through the same sender. The SSE route delivers the completed result.
        result = generate_velia_chat_result(prompt + evidence_prompt(evidence), user_id=user_id,
                    conversation_id=conversation_id, request_id=request_id)
        if result.get("ok") and str(result.get("text") or "").strip():
            result["text"] += evidence_footer(evidence, russian)
        return result

    chat._build_prompt = build_prompt
    chat.generate_velia_chat_result = generate
    chat._velia_projects_installed = True
