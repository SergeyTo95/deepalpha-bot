from services.velia_telegram_pairing_service import build_pairing_message


def test_pairing_message_explains_how_to_return_to_velia():
    text = build_pairing_message("ABCD-EFGH-JKLM-NPQR", 300)

    assert "скопировать" in text.lower()
    assert "вернись в VELIA" in text
    assert "список последних приложений" in text
