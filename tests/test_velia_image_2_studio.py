import services.velia_studio_generation_service as generation_service
import services.velia_studio_service as studio_service
from services.velia_image_2_service import (
    VELIA_IMAGE_2_PROVIDER,
    VELIA_IMAGE_PROVIDER,
    normalize_image_provider,
)


def test_velia_image_provider_ids_are_stable():
    assert normalize_image_provider(None) == VELIA_IMAGE_PROVIDER
    assert normalize_image_provider("velia_image_2") == VELIA_IMAGE_2_PROVIDER


def test_velia_image_provider_rejects_unknown_value():
    try:
        normalize_image_provider("qwen_image")
    except ValueError as exc:
        assert str(exc) == "studio_image_provider_not_supported"
    else:
        raise AssertionError("unknown provider must fail closed")


def test_velia_image_2_routing_does_not_call_legacy_image(monkeypatch):
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(studio_service, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        studio_service,
        "get_session",
        lambda *_args, **_kwargs: {"id": "session", "mode": "image"},
    )
    monkeypatch.setattr(studio_service, "_generation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(studio_service, "_reference_ids", lambda value: list(value or []))
    monkeypatch.setattr(studio_service, "_load_refs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(studio_service, "_insert_turn", lambda *_args, **_kwargs: "generation")
    monkeypatch.setattr(studio_service, "_finish", lambda *_args, **_kwargs: None)

    calls = []
    monkeypatch.setattr(
        generation_service,
        "generate_and_store_velia_image_2",
        lambda **kwargs: calls.append(kwargs) or {
            "image_created": False,
            "error_code": "velia_image_2_disabled",
        },
    )

    result = generation_service.generate_studio_turn(
        user_id=1,
        session_id="session",
        prompt="hello",
        client_request_id="idem",
        image_provider="velia_image_2",
    )

    assert len(calls) == 1
    assert result["duplicate"] is False
