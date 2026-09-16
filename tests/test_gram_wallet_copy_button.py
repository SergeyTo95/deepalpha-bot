from bot.admin_viewers import _health_html, _health_keyboard


def test_gram_wallet_address_is_isolated_and_copy_button_copies_only_address():
    address = "UQARaLE231LsLLaQtra3878G5DQAZBXm90_lQwHNuyqGpeo0"
    status = {
        "address": address,
        "network": "mainnet",
        "balance_nano": 49_578_000,
        "balance_error": "",
        "ready": True,
        "reason": "",
    }

    rendered = _health_html(status)
    assert f"<code>{address}</code>" in rendered
    assert rendered.count("<code>") == 1

    keyboard = _health_keyboard(status, ("🔄 Обновить", "admin_gram_payment_health"))
    payload = keyboard.to_python()
    copy_button = payload["inline_keyboard"][0][0]
    assert copy_button["text"] == "📋 Скопировать адрес"
    assert copy_button["copy_text"] == {"text": address}
    assert "callback_data" not in copy_button
    assert payload["inline_keyboard"][1][0]["callback_data"] == "admin_gram_payment_health"
