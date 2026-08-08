from types import SimpleNamespace

from aiohttp import web

from services.velia_admin_user_display_service import apply_admin_users_display_fallback


def test_admin_users_falls_back_to_saved_telegram_name_when_username_missing():
    request = SimpleNamespace(method="GET", path="/admin/users")
    response = web.Response(
        text=(
            "<table><thead><tr><th>Telegram ID</th><th>Username</th><th>Name</th></tr></thead>"
            "<tbody><tr><td>123</td><td>@—</td><td>Иван &amp; Co</td></tr></tbody></table>"
        ),
        content_type="text/html",
    )

    result = apply_admin_users_display_fallback(request, response)

    assert result is response
    assert "<th>User</th><th>Name</th>" in response.text
    assert "<td>Иван &amp; Co</td><td>Иван &amp; Co</td>" in response.text
    assert "@—" not in response.text


def test_admin_users_keeps_real_username_unchanged():
    request = SimpleNamespace(method="GET", path="/admin/users")
    response = web.Response(
        text=(
            "<table><thead><tr><th>Telegram ID</th><th>Username</th><th>Name</th></tr></thead>"
            "<tbody><tr><td>123</td><td>@real_user</td><td>Ivan</td></tr></tbody></table>"
        ),
        content_type="text/html",
    )

    apply_admin_users_display_fallback(request, response)

    assert "<td>@real_user</td><td>Ivan</td>" in response.text


def test_display_fallback_does_not_touch_non_users_pages():
    request = SimpleNamespace(method="GET", path="/admin")
    original = "<th>Username</th><td>@—</td><td>Ivan</td>"
    response = web.Response(text=original, content_type="text/html")

    apply_admin_users_display_fallback(request, response)

    assert response.text == original
