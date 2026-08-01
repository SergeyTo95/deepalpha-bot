from typing import Any, Dict

from services.velia_images_service import (
    image_intent_from_chat_prompt,
    image_metadata_for_request,
    generate_and_store_image,
)


def install(velia_chat_service_module: Any) -> None:
    if getattr(velia_chat_service_module, "_velia_images_patch_installed", False):
        return

    original_generate = velia_chat_service_module.generate_velia_chat_result
    original_serialize = velia_chat_service_module._serialize_message

    def generate_with_velia_images(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: str = None,
    ) -> Dict[str, Any]:
        intent = image_intent_from_chat_prompt(prompt)
        if not intent.requested:
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        original_message = velia_chat_service_module.re.findall(
            r"(?:^|\n\n)USER:\s*(.*?)(?=\n\n(?:USER|ASSISTANT):|\Z)",
            str(prompt or ""),
            flags=velia_chat_service_module.re.DOTALL,
        )
        latest_message = str(original_message[-1] if original_message else "").strip()
        result = generate_and_store_image(
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_id=str(request_id or ""),
            original_message=latest_message,
            prompt=intent.prompt,
        )
        return {
            "ok": True,
            "text": str(result.get("text") or ""),
            "request_id": str(request_id or ""),
            "provider": "velyon_images",
            "model": "quality",
            "finish_reason": "image_created" if result.get("image_created") else "image_not_created",
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
            "usage": {},
        }

    def serialize_with_velia_image(row: Any, *, debug_usage: bool = False) -> Dict[str, Any]:
        serialized = original_serialize(row, debug_usage=debug_usage)
        if serialized.get("role") != "assistant" or serialized.get("status") != "completed":
            return serialized
        request_id = str(serialized.get("request_id") or "")
        user_id = velia_chat_service_module._row_value(row, "user_id", 2, 0)
        try:
            image = image_metadata_for_request(request_id, int(user_id or 0))
        except Exception:
            image = None
        if image:
            serialized["type"] = "image"
            serialized["image"] = image
        else:
            serialized["type"] = "text"
        return serialized

    velia_chat_service_module.generate_velia_chat_result = generate_with_velia_images
    velia_chat_service_module._serialize_message = serialize_with_velia_image
    velia_chat_service_module._velia_images_patch_installed = True
