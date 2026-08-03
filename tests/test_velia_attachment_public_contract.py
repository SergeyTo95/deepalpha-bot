from services.velia_attachment_public_contract import public_attachment


def test_public_attachment_excludes_private_storage_and_analysis_fields():
    attachment = public_attachment(
        {
            "id": "attachment-1",
            "conversation_id": "conversation-1",
            "name": "contract.pdf",
            "mime_type": "application/pdf",
            "kind": "document",
            "byte_size": 2048,
            "width": None,
            "height": None,
            "status": "ready",
            "created_at": "2026-08-03T00:00:00Z",
            "sha256": "private-digest",
            "content_bytes": b"private",
            "extracted_text": "private extracted text",
            "user_id": 42,
            "deleted_at": None,
        }
    )

    assert attachment == {
        "id": "attachment-1",
        "conversation_id": "conversation-1",
        "name": "contract.pdf",
        "mime_type": "application/pdf",
        "kind": "document",
        "byte_size": 2048,
        "width": None,
        "height": None,
        "status": "ready",
        "created_at": "2026-08-03T00:00:00Z",
    }
    assert "sha256" not in attachment
    assert "content_bytes" not in attachment
    assert "extracted_text" not in attachment
