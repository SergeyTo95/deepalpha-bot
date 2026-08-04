from services import velia_developer_project_service as projects


def test_install_state_round_trip_and_expiry(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "x" * 48)
    token = projects.create_install_state(77, now=1_800_000_000)

    payload = projects.verify_install_state(token, now=1_800_000_100)
    assert payload["user_id"] == 77
    assert payload["v"] == 1

    try:
        projects.verify_install_state(token, now=1_800_001_000)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "install_state_expired"


def test_install_state_rejects_tampering(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_STATE_SECRET", "y" * 48)
    token = projects.create_install_state(88, now=1_800_000_000)
    encoded, signature = token.split(".")
    tampered = ("A" if encoded[0] != "A" else "B") + encoded[1:] + "." + signature

    try:
        projects.verify_install_state(tampered, now=1_800_000_100)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "invalid_install_state"


def test_state_secret_is_required(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_STATE_SECRET", raising=False)
    try:
        projects.create_install_state(1, now=1_800_000_000)
        assert False
    except projects.DeveloperProjectError as exc:
        assert exc.code == "developer_state_secret_missing"
        assert exc.status == 503
