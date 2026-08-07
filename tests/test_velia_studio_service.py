import io

import pytest
from PIL import Image

from services import velia_studio_service as service


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_studio_mode_and_prompt_validation():
    assert service._mode(" IMAGE ") == "image"
    assert service._mode("video") == "video"
    assert service._prompt("  raccoon   dinner ") == "raccoon dinner"
    with pytest.raises(service.StudioError) as exc:
        service._mode("audio")
    assert exc.value.code == "studio_invalid_mode"


def test_reference_image_validation_accepts_real_png_and_rejects_text():
    assert service._verify_image(_png_bytes(), "image/png") == (32, 24)
    with pytest.raises(service.StudioError) as exc:
        service._verify_image(b"not-an-image", "image/png")
    assert exc.value.code == "studio_reference_invalid"


def test_reference_ids_are_deduplicated_and_bounded():
    values = [
        "11111111-1111-4111-8111-111111111111",
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]
    assert service._reference_ids(values) == [values[0], values[2]]


def test_image_reference_is_rejected_before_paid_generation(monkeypatch):
    monkeypatch.setattr(service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(service, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "get_session",
        lambda user_id, session_id: {"id": session_id, "mode": "image", "title": ""},
    )
    monkeypatch.setattr(service, "_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "_load_refs",
        lambda *args, **kwargs: [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "mime_type": "image/png",
                "content_bytes": _png_bytes(),
                "width": 32,
                "height": 24,
            }
        ],
    )
    called = {"paid": False}
    monkeypatch.setattr(
        service,
        "generate_and_store_image",
        lambda **kwargs: called.update(paid=True),
    )

    with pytest.raises(service.StudioError) as exc:
        service.generate_turn(
            user_id=1,
            session_id="session-1",
            prompt="Create an image",
            client_request_id="request-1",
            reference_asset_ids=["11111111-1111-4111-8111-111111111111"],
        )
    assert exc.value.code == "studio_image_references_not_supported"
    assert called["paid"] is False


def test_duplicate_generation_never_calls_provider(monkeypatch):
    monkeypatch.setattr(service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(service, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        service,
        "get_session",
        lambda user_id, session_id: {"id": session_id, "mode": "video", "title": ""},
    )
    existing = {
        "id": "generation-1",
        "session_id": "session-1",
        "type": "video",
        "status": "pending",
    }
    monkeypatch.setattr(service, "_generation", lambda *args, **kwargs: existing)
    monkeypatch.setattr(
        service,
        "_studio_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider called")),
    )

    result = service.generate_turn(
        user_id=1,
        session_id="session-1",
        prompt="Create a video",
        client_request_id="request-1",
    )
    assert result == {"duplicate": True, "generation": existing}


def test_disabled_studio_fails_before_session_lookup(monkeypatch):
    monkeypatch.setattr(service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(service, "studio_enabled", lambda: False)
    monkeypatch.setattr(
        service,
        "get_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("session lookup")),
    )
    with pytest.raises(service.StudioError) as exc:
        service.generate_turn(
            user_id=1,
            session_id="session-1",
            prompt="Create a video",
            client_request_id="request-1",
        )
    assert exc.value.code == "studio_disabled"
