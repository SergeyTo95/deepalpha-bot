from typing import Any, Dict


def _client_request_id(row: Any) -> str:
    if isinstance(row, dict):
        value = row.get("idempotency_key")
    else:
        try:
            value = row[6]
        except (IndexError, TypeError):
            value = None
    return str(value or "").strip()


def install_client_request_id_serialization(chat_module: Any) -> None:
    """Expose only the caller-owned idempotency identity on user messages."""
    if getattr(chat_module, "_velia_client_request_id_installed", False):
        return

    original_serialize = getattr(chat_module, "_serialize_message", None)
    if not callable(original_serialize):
        return

    def serialize_with_client_request_id(
        row: Any,
        *,
        debug_usage: bool = False,
    ) -> Dict[str, Any]:
        result = original_serialize(row, debug_usage=debug_usage)
        if str(result.get("role") or "") == "user":
            client_request_id = _client_request_id(row)
            if client_request_id:
                result["client_request_id"] = client_request_id
        return result

    chat_module._serialize_message = serialize_with_client_request_id
    chat_module._velia_client_request_id_installed = True
