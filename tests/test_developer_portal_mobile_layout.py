from pathlib import Path


def test_portal_root_cannot_expand_beyond_mobile_viewport():
    css = Path("webapp/developer.css").read_text(encoding="utf-8")

    assert "html {\n  width: 100%;\n  max-width: 100%;\n  overflow-x: hidden;" in css
    assert "body {\n  width: 100%;\n  max-width: 100%;" in css
    assert ".shell {\n  width: 100%;\n  max-width: 980px;\n  min-width: 0;" in css
    assert ".stack {\n  display: grid;\n  min-width: 0;\n  max-width: 100%;" in css
    assert ".card {\n  width: 100%;\n  min-width: 0;\n  max-width: 100%;\n  overflow: hidden;" in css


def test_wide_tables_scroll_inside_card_instead_of_widening_page():
    css = Path("webapp/developer.css").read_text(encoding="utf-8")

    assert ".table-wrap {\n  display: block;\n  width: 100%;\n  min-width: 0;\n  max-width: 100%;\n  overflow-x: auto;" in css
    assert "table {\n  width: max-content;\n  min-width: 100%;\n  max-width: none;" in css
    assert "overscroll-behavior-x: contain;" in css
    assert "-webkit-overflow-scrolling: touch;" in css


def test_mobile_forms_and_buttons_use_single_column_full_width_layout():
    css = Path("webapp/developer.css").read_text(encoding="utf-8")

    mobile = css.split("@media (max-width: 720px)", 1)[1].split("@media (max-width: 520px)", 1)[0]
    assert ".grid { grid-template-columns: 1fr; }" in mobile
    assert ".stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in mobile
    assert ".button-row > * { flex: 1 1 100%; width: 100%; }" in mobile


def test_css_cache_is_busted_for_telegram_webview():
    html = Path("webapp/developer.html").read_text(encoding="utf-8")

    assert '/webapp/developer.css?v=1.1' in html
    assert '/webapp/developer.css?v=1.0' not in html
