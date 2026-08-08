from types import SimpleNamespace

import services.velia_admin_mobile_ui_patch as mobile


def _layout_html() -> str:
    return """<!doctype html><html><head><style>.x{}</style></head><body>
    <div class='shell'><aside class='side'><div class='brand'>VELIA</div>
    <nav class='navs'><a class='nav'>Overview</a><a class='nav active'>Users</a></nav>
    <div class='logout'><form method='post'><button>Sign out</button></form></div></aside>
    <main class='main'><div class='topline'><h1>Users</h1><span class='pill'>Owner session</span></div>
    <div class='table-wrap'><table><thead><tr><th>User</th><th>Balance</th><th>Action</th></tr></thead>
    <tbody><tr><td>@sergey</td><td>120</td><td><button>Open</button></td></tr>
    <tr><td colspan='3'>No more users</td></tr></tbody></table></div></main></div></body></html>"""


def _login_html() -> str:
    return "<!doctype html><html><head><style>.box{}</style></head><body><div class='box'><input><button>Sign in</button></div></body></html>"


def test_mobile_patch_is_idempotent_and_presentation_only():
    module = SimpleNamespace(_layout=lambda *a, **k: _layout_html(), _login_page=lambda *a, **k: _login_html())
    mobile.install_admin_mobile_ui_patch(module)
    first_layout = module._layout
    mobile.install_admin_mobile_ui_patch(module)
    assert module._layout is first_layout
    assert module._velia_admin_mobile_ui_installed is True

    rendered = module._layout()
    assert "id='velia-mobile-first-ui'" in rendered
    assert "position:fixed!important" in rendered
    assert "env(safe-area-inset-bottom)" in rendered
    assert ".nav.active{order:-1" in rendered
    assert "class='nav active' aria-current='page'" in rendered
    assert ".logout button{display:inline-flex!important" in rendered
    assert "min-height:48px" in rendered
    assert "font-size:16px!important" in rendered
    assert "max-width:100vw" in rendered
    assert "mobile-card-table" in rendered
    assert "mobile-kv-table" in rendered
    assert "data-label='User'" in rendered
    assert "data-label='Balance'" in rendered
    assert "data-label='Action'" in rendered
    assert "class='mobile-colspan' data-label=''" in rendered

    # The patch must not introduce data/billing/auth mutations.
    source = open("services/velia_admin_mobile_ui_patch.py", encoding="utf-8").read()
    for forbidden in (
        "UPDATE users",
        "UPDATE settings",
        "UPDATE token_packages",
        "DELETE FROM",
        "INSERT INTO",
        "get_connection",
        "ADMIN_SECRET_KEY",
        "BOT_TOKEN",
    ):
        assert forbidden not in source


def test_login_gets_touch_and_safe_area_layout_without_table_processing():
    module = SimpleNamespace(_layout=lambda: _layout_html(), _login_page=lambda: _login_html())
    mobile.install_admin_mobile_ui_patch(module)
    rendered = module._login_page()
    assert "id='velia-mobile-login-ui'" in rendered
    assert "min-height:100dvh" in rendered
    assert "min-height:50px" in rendered
    assert "min-height:52px" in rendered
    assert "mobile-card-table" not in rendered


def test_table_enhancement_preserves_existing_classes_and_escapes_header_labels():
    document = """<html><head></head><body><div class='table-wrap'><table class='existing'>
    <thead><tr><th><b>Name &amp; ID</b></th><th>State</th></tr></thead>
    <tbody><tr><td class='strong'>A</td><td>OK</td></tr></tbody></table></div></body></html>"""
    rendered = mobile._enhance_tables(document)
    assert "class='existing mobile-card-table'" in rendered
    assert "class='strong' data-label='Name &amp; ID'" in rendered
    assert "data-label='State'" in rendered


def test_two_column_headerless_table_becomes_mobile_key_value_cards():
    document = """<div class='table-wrap'><table><tbody>
    <tr><td>Railway service</td><td><code>deepalpha-bot</code></td></tr>
    <tr><td>Deployed commit SHA</td><td><code>41f74f44964d3aa3cb8a06134aeefd64cd060d18</code></td></tr>
    </tbody></table></div>"""
    rendered = mobile._enhance_tables(document)
    assert "class='mobile-kv-table'" in rendered
    assert "Railway service" in rendered
    assert "deepalpha-bot" in rendered
    assert "41f74f44964d3aa3cb8a06134aeefd64cd060d18" in rendered


def test_non_key_value_headerless_table_is_left_unchanged():
    document = "<div class='table-wrap'><table><tbody><tr><td>A</td></tr></tbody></table></div>"
    assert mobile._enhance_tables(document) == document


def test_bootstrap_installs_global_mobile_ui_before_route_use():
    source = open("services/velia_admin_economy_bootstrap_service.py", encoding="utf-8").read()
    assert "from services.velia_admin_mobile_ui_patch import install_admin_mobile_ui_patch" in source
    assert "install_admin_mobile_ui_patch(admin_routes_module)" in source
