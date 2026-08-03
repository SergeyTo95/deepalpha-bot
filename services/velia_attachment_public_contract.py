from typing import Any, Dict


_PUBLIC_ATTACHMENT_FIELDS = (
    "id",
    "conversation_id",
    "name",
    "mime_type",
    "kind",
    "byte_size",
    "width",
    "height",
    "status",
    "created_at",
)


def public_attachment(value: Dict[str, Any]) -> Dict[str, Any]:
    """Return only metadata that is safe for authenticated mobile clients."""
    source = value if isinstance(value, dict) else {}
    return {
        field: source[field]
        for field in _PUBLIC_ATTACHMENT_FIELDS
        if field in source
    }
