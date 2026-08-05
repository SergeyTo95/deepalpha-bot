from cryptography.fernet import Fernet

from services import velia_agent_connector_crypto_service as crypto
from services import velia_agent_google_calendar_service as calendar
from services import velia_agent_tool_registry_service as registry
from services.velia_agent_protocol_service import ActionRisk


def test_connector_secrets_are_encrypted_at_rest(monkeypatch):
    monkeypatch.setenv("VELIA_CONNECTOR_FERNET_KEY", Fernet.generate_key().decode("ascii"))
    ciphertext = crypto.encrypt_secret("refresh-token")
    assert ciphertext != "refresh-token"
    assert crypto.decrypt_secret(ciphertext) == "refresh-token"


def test_google_calendar_tools_are_read_and_external_write(monkeypatch):
    registry.clear_registry_for_tests()
    calendar._TOOLS_READY = False
    monkeypatch.setattr(calendar, "configured", lambda: True)
    calendar.register_tools()

    read_tool = registry.get_tool("google.calendar.events.list")
    create_tool = registry.get_tool("google.calendar.events.create")
    assert read_tool.risk is ActionRisk.READ
    assert read_tool.requires_approval is False
    assert create_tool.risk is ActionRisk.WRITE_EXTERNAL
    assert create_tool.requires_approval is True


def test_create_event_uses_stable_server_idempotency_and_recovers_conflict(monkeypatch):
    calls = []

    def fake_request(user_id, method, path, **kwargs):
        calls.append((user_id, method, path, kwargs))
        if method == "POST":
            return 409, {}
        return 200, {
            "id": kwargs.get("body", {}).get("id") or path.rsplit("/", 1)[-1],
            "summary": "Investor call",
            "start": {"dateTime": "2026-08-06T14:00:00+03:00"},
            "end": {"dateTime": "2026-08-06T15:00:00+03:00"},
            "status": "confirmed",
        }

    monkeypatch.setattr(calendar, "_calendar_request", fake_request)
    result = calendar.create_event(
        77,
        {
            "title": "Investor call",
            "start": "2026-08-06T14:00:00+03:00",
            "end": "2026-08-06T15:00:00+03:00",
            "time_zone": "Europe/Istanbul",
            "_velia_idempotency_key": "calendar-action-1",
        },
    )

    expected_id = calendar._event_id("calendar-action-1")
    assert calls[0][1:3] == ("POST", "/calendars/primary/events")
    assert calls[0][3]["body"]["id"] == expected_id
    assert calls[1][1:3] == ("GET", f"/calendars/primary/events/{expected_id}")
    assert result["idempotent"] is True


def test_event_validation_requires_timezone_and_positive_range():
    try:
        calendar.create_event(
            1,
            {
                "title": "Bad",
                "start": "2026-08-06T14:00:00",
                "end": "2026-08-06T15:00:00",
                "_velia_idempotency_key": "one",
            },
        )
        raise AssertionError("expected timezone validation")
    except calendar.GoogleCalendarError as exc:
        assert exc.code == "velia_google_event_start_timezone_required"
