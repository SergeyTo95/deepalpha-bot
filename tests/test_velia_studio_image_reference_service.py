import base64

import pytest

from services import velia_studio_generation_service as generation_service
from services import velia_studio_image_reference_service as reference_service


def _reference(raw: bytes = b"reference-bytes", mime_type: str = "image/jpeg"):
    return {
        "id": "reference-1",
        "mime_type": mime_type,
        "content_bytes": raw,
        "width": 100,
        "height": 100,
    }


def test_single_reference_uses_edit_with_private_data_uri():
    endpoint, body, mode = reference_service._request_contract(
        "Keep the face and change the background",
        [_reference()],
    )

    assert endpoint.endswith("/reve/2.1/edit")
    assert mode == "edit"
    assert body["prompt"] == "Keep the face and change the background"
    assert body["aspect_ratio"] == "auto"
    assert body["num_images"] == 1
    assert body["output_format"] == "jpeg"
    expected = base64.b64encode(b"reference-bytes").decode("ascii")
    assert body["image_url"] == f"data:image/jpeg;base64,{expected}"
    assert "http" not in body["image_url"]
    assert "image_urls" not in body


def test_multiple_references_use_remix_without_public_urls():
    references = [
        _reference(b"first", "image/png"),
        _reference(b"second", "image/webp"),
    ]
    endpoint, body, mode = reference_service._request_contract(
        "Combine both references into one scene",
        references,
    )

    assert endpoint.endswith("/reve/2.1/remix")
    assert mode == "remix"
    assert len(body["image_urls"]) == 2
    assert body["image_urls"][0].startswith("data:image/png;base64,")
    assert body["image_urls"][1].startswith("data:image/webp;base64,")
    assert all("http" not in value for value in body["image_urls"])
    assert "image_url" not in body


def test_provider_reference_size_is_rejected_before_submission():
    oversized = b"x" * (reference_service._MAX_REFERENCE_BYTES + 1)
    with pytest.raises(reference_service.StudioImageReferenceError) as exc:
        reference_service._request_contract("edit", [_reference(oversized)])
    assert exc.value.code == "studio_image_reference_provider_size_limit"


def test_image_reference_generation_uses_studio_only_path(monkeypatch):
    studio = generation_service.studio_service
    monkeypatch.setattr(studio, "_ensure_schema", lambda: None)
    monkeypatch.setattr(studio, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        studio,
        "get_session",
        lambda user_id, session_id: {"id": session_id, "mode": "image"},
    )
    monkeypatch.setattr(studio, "_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        studio,
        "_reference_ids",
        lambda values: ["11111111-1111-4111-8111-111111111111"],
    )
    refs = [_reference()]
    monkeypatch.setattr(studio, "_load_refs", lambda *args, **kwargs: refs)
    monkeypatch.setattr(studio, "_insert_turn", lambda *args, **kwargs: "generation-1")
    finished = {}
    monkeypatch.setattr(
        studio,
        "_finish",
        lambda *args, **kwargs: finished.update(kwargs),
    )
    monkeypatch.setattr(
        generation_service,
        "generate_and_store_reference_image",
        lambda **kwargs: {
            "image_created": True,
            "estimated_cost_usd": 0.25,
            "error_code": None,
        },
    )
    monkeypatch.setattr(
        studio,
        "generate_turn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("legacy path called")),
    )
    final_generation = {
        "id": "generation-1",
        "session_id": "session-1",
        "type": "image",
        "status": "completed",
    }
    calls = {"count": 0}

    def generation_lookup(*args, **kwargs):
        calls["count"] += 1
        return None if calls["count"] == 1 else final_generation

    monkeypatch.setattr(studio, "_generation", generation_lookup)

    result = generation_service.generate_studio_turn(
        user_id=7,
        session_id="session-1",
        prompt="Edit this reference",
        client_request_id="client-1",
        reference_asset_ids=["11111111-1111-4111-8111-111111111111"],
    )

    assert result == {"duplicate": False, "generation": final_generation}
    assert finished["created"] is True
    assert finished["cost"] == 0.25
    assert finished["error_code"] is None
