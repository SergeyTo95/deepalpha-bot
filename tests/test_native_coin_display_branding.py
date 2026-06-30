from pathlib import Path

from services.native_coin_display import (
    get_native_coin_name,
    get_native_coin_symbol,
    format_native_amount,
    native_wallet_label,
)

ROOT = Path(__file__).resolve().parents[1]


def test_native_coin_display_helper():
    assert get_native_coin_name() == "Gram"
    assert get_native_coin_symbol() == "GRAM"
    assert format_native_amount(1.25) == "1.25 Gram"
    assert native_wallet_label("ru") == "Gram кошелёк"
    assert native_wallet_label("en") == "Gram Wallet"


def test_main_keyboard_labels_are_gram():
    text = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    assert 'f"💎 {native_wallet_label(\'ru\')}"' in text
    assert 'f"💎 {native_wallet_label(\'en\')}"' in text


def test_wallet_display_copy_uses_gram():
    text = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    for expected in [
        "Ваш Gram кошелёк",
        "Your Gram Wallet",
        "Получить Gram",
        "Receive Gram",
        "Отправить Gram",
        "Send Gram",
        "Gram Транзакции",
        "Gram Transactions",
    ]:
        assert expected in text


def test_webapp_visible_labels_use_gram():
    combined = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [ROOT / "webapp" / "app.js", ROOT / "webapp" / "index.html"]
    )
    for expected in ["Gram Wallet", "Send Gram", "Buy tokens with Gram wallet", "Gram Transactions"]:
        assert expected in combined


def test_admin_visible_labels_use_gram():
    combined = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [ROOT / "bot" / "admin.py", ROOT / "admin_routes.py"]
    )
    assert "Token price, Gram" in combined
    assert "Subscription price, Gram" in combined
    assert "TON Wallet" not in combined
    assert "TON balance" not in combined
    assert "TON payment" not in combined


def test_old_and_new_wallet_buttons_route_to_wallet_handler():
    text = (ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    handler_line = '@dp.message_handler(lambda m: m.text in ["💎 Gram кошелёк", "💎 Gram Wallet", "💎 TON кошелёк", "💎 TON Wallet"])'
    assert handler_line in text


def test_user_facing_forbidden_ton_phrases_absent_except_compatibility():
    combined = "\n".join(
        p.read_text(encoding="utf-8")
        for p in [ROOT / "telegram_bot.py", ROOT / "webapp" / "app.js", ROOT / "webapp" / "index.html", ROOT / "admin_routes.py", ROOT / "bot" / "admin.py"]
    )
    compatibility = {"TON кошелёк", "TON Wallet"}
    forbidden = [
        "TON balance", "TON баланс", "Send TON", "Receive TON", "Pay with TON",
        "Buy with TON", "TON transaction", "TON транзакция",
    ]
    for phrase in forbidden:
        assert phrase not in combined
    for phrase in compatibility:
        assert phrase in combined
