from services import velia_telegram_connect_page_patch
from services import velia_telegram_pairing_service


class FakeCursor:
    def __init__(self):
        self.executions = []
        self.rowcount = 0
        self.closed = False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executions.append((normalized, params))
        self.rowcount = 1 if normalized.startswith("INSERT INTO velia_mobile_pairing_codes") else 0

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_extracts_only_exact_velia_start_payload():
    assert velia_telegram_pairing_service.extract_start_payload("/start velia_connect") == "velia_connect"
    assert velia_telegram_pairing_service.extract_start_payload("/start@DeepAlphaAI_bot velia_connect") == "velia_connect"
    assert velia_telegram_pairing_service.is_velia_connect_start("/start velia_connect") is True
    assert velia_telegram_pairing_service.is_velia_connect_start("/start") is False
    assert velia_telegram_pairing_service.is_velia_connect_start("/start something_else") is False
    assert velia_telegram_pairing_service.is_velia_connect_start("hello velia_connect") is False


def test_telegram_pairing_code_is_single_use_compatible_and_five_minutes(monkeypatch):
    fake_connection = FakeConnection()
    ensured = []

    monkeypatch.setattr(
        velia_telegram_pairing_service,
        "ensure_user",
        lambda user_id, **kwargs: ensured.append((user_id, kwargs)),
    )
    monkeypatch.setattr(
        velia_telegram_pairing_service,
        "get_connection",
        lambda: fake_connection,
    )
    monkeypatch.setattr(
        velia_telegram_pairing_service,
        "_new_raw_code",
        lambda: "ABCDEFGHJKLMNPQR",
    )

    result = velia_telegram_pairing_service.create_telegram_pairing_code(
        5811340792,
        username="sergey",
        first_name="Sergey",
    )

    assert result["ok"] is True
    assert result["pairing_code"] == "ABCD-EFGH-JKLM-NPQR"
    assert result["expires_in"] == 300
    assert ensured[0][0] == 5811340792
    assert ensured[0][1]["source"] == "velia_telegram_pairing"
    assert fake_connection.commits == 1
    assert fake_connection.rollbacks == 0
    assert fake_connection.closed is True
    assert fake_connection.cursor_instance.closed is True

    queries = [query for query, _ in fake_connection.cursor_instance.executions]
    assert any(query.startswith("UPDATE velia_mobile_pairing_codes SET consumed_at") for query in queries)
    assert any(query.startswith("INSERT INTO velia_mobile_pairing_codes") for query in queries)


def test_pairing_message_does_not_claim_more_than_five_minutes():
    text = velia_telegram_pairing_service.build_pairing_message(
        "ABCD-EFGH-JKLM-NPQR",
        300,
    )
    assert "5 минут" in text
    assert "только один раз" in text
    assert "Новый код автоматически отменит предыдущий" in text
    assert "<code>ABCD-EFGH-JKLM-NPQR</code>" in text


def test_connect_page_points_to_sanitized_telegram_deep_link(monkeypatch):
    monkeypatch.setenv("BOT_USERNAME", "@DeepAlphaAI_bot<script>")

    url = velia_telegram_connect_page_patch.build_telegram_connect_url()
    page = velia_telegram_connect_page_patch.build_unauthenticated_connect_page()

    assert url == "https://t.me/DeepAlphaAI_botscript?start=velia_connect"
    assert "Получить код в Telegram" in page
    assert "действует 5 минут" in page
    assert "velia_connect" in page
    assert "<script>" not in page
