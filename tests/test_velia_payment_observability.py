from pathlib import Path

import services.velia_admin_payments_routes as payment_routes


class _AdminStub:
    @staticmethod
    def _e(value):
        return str(value)

    @staticmethod
    def _metric(value):
        return str(value if value is not None else "—")


def test_payments_ui_separates_public_checkout_from_worker_poll_evidence():
    body = payment_routes._payments_body(
        _AdminStub(),
        {
            "available": True,
            "public_checkout_enabled": False,
            "signing_capability": False,
            "successful_poll_networks": ["ton"],
            "summary": {},
            "channels": [],
            "networks": [
                {
                    "network": "ton",
                    "asset": "USDT",
                    "enabled": True,
                    "status": "running",
                    "chain_height": 123,
                    "lag_blocks": 0,
                    "last_poll_at": "2026-08-09 10:00:00+00",
                    "last_success_at": "2026-08-09 10:00:00+00",
                    "last_error_code": None,
                }
            ],
            "intents": [],
            "fulfillments": [],
            "legacy_ton": {"available": False},
            "scope_note": "Watch-only payment telemetry.",
        },
    )

    assert "WATCH-ONLY · PUBLIC CHECKOUT OFF" in body
    assert "Successful poll recorded for: Gram" in body
    assert "Gram <span class='hint'>(technical: ton)</span>" in body
    assert "Last poll" in body
    assert "Last success" in body
    assert "2026-08-09 10:00:00+00" in body
    assert "does not by itself prove an end-to-end payment" in body
    assert "No blockchain polling" not in body
    assert "LIVE MONEY DISABLED" not in body


def test_payment_snapshot_uses_persisted_poll_telemetry_without_worker_env_guessing():
    source = Path("services/velia_admin_payments_service.py").read_text(encoding="utf-8")

    assert "last_poll_at,last_success_at,last_error_code" in source
    assert '"successful_poll_networks"' in source
    assert "crypto_checkout_enabled()" in source
    assert '"foundation_mode"' not in source
    assert '"live_money_acceptance"' not in source
    assert "worker_enabled()" not in source
    assert "SELECT channel, COUNT(*) AS intents" in source


def test_payment_observability_remains_read_only_and_secret_free():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "services/velia_admin_payments_service.py",
            "services/velia_admin_payments_routes.py",
        )
    ).lower()

    assert "seed_encrypted" not in text
    assert "mnemonic" not in text
    assert "private_key" not in text
    assert "update velia_payment_worker_state" not in text
    assert "insert into velia_payment_worker_state" not in text
    assert "delete from velia_payment_worker_state" not in text
