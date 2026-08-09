from __future__ import annotations

import html
import re
from typing import Any, Callable


_MOBILE_STYLE = """
<style id='velia-mobile-first-ui'>
@media (max-width:820px){
html{-webkit-text-size-adjust:100%;width:100%;max-width:100%;scroll-padding-bottom:calc(82px + env(safe-area-inset-bottom))}
body{width:100%;max-width:100%;min-width:0}
a,button,input,select,textarea,.button{touch-action:manipulation}
.shell{display:block!important;width:100%;max-width:100%;min-height:100dvh;padding-bottom:calc(74px + env(safe-area-inset-bottom))}
.side{position:fixed!important;z-index:40!important;inset:auto 0 0!important;top:auto!important;height:auto!important;display:flex;align-items:center;gap:6px;padding:7px 8px calc(7px + env(safe-area-inset-bottom))!important;border:0!important;border-top:1px solid var(--line)!important;background:rgba(5,7,11,.94)!important;box-shadow:0 -14px 38px rgba(0,0,0,.42);backdrop-filter:blur(22px) saturate(140%);-webkit-backdrop-filter:blur(22px) saturate(140%)}
.brand{display:none!important}.navs{min-width:0;flex:1 1 auto;display:flex!important;gap:6px!important;overflow-x:auto!important;overflow-y:hidden;padding:0!important;scroll-snap-type:x proximity;scrollbar-width:none;-webkit-overflow-scrolling:touch}.navs::-webkit-scrollbar{display:none}
.nav{min-width:max-content;min-height:48px;display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;padding:0 13px!important;border-radius:13px!important;white-space:nowrap;scroll-snap-align:center;font-size:12px;font-weight:650}.nav.active{order:-1;box-shadow:inset 0 0 0 1px rgba(138,125,255,.16)}
.logout{position:static!important;inset:auto!important;flex:0 0 auto;margin:0!important}.logout form{margin:0}.logout button{display:inline-flex!important;min-width:58px;min-height:48px;align-items:center;justify-content:center;margin:0;padding:0 10px!important;border-radius:13px!important;font-size:11px;color:#c7d0dc;background:#101620}
.main{width:100%;max-width:100vw;min-width:0;padding:max(16px,env(safe-area-inset-top)) 12px calc(24px + env(safe-area-inset-bottom))!important}.topline{gap:10px;align-items:center!important;margin-bottom:16px!important}h1{font-size:clamp(21px,6vw,26px)!important;line-height:1.12}h2{font-size:16px;line-height:1.25}.subtitle{max-width:72vw;margin-top:4px;font-size:11px;line-height:1.35}.topline>.pill{flex:0 0 auto;padding:5px 8px;font-size:10px}
.grid{gap:10px!important}.card,.card.wide,.card.full{grid-column:1/-1!important;width:100%;max-width:100%;min-width:0;padding:14px!important;border-radius:14px!important}.value{font-size:clamp(22px,7vw,28px);overflow-wrap:anywhere}.label{font-size:10px}.hint,.muted{overflow-wrap:anywhere}.pill,.status{max-width:100%;white-space:normal}.flash{margin-bottom:10px;border-radius:12px}
button,.button,input,select,textarea{min-height:46px;border-radius:11px!important}input,select,textarea{width:100%;min-width:0;padding:11px 12px!important;font-size:16px!important}button,.button{display:inline-flex;align-items:center;justify-content:center;padding:10px 13px!important}
form.inline,.action-row{display:grid!important;grid-template-columns:minmax(0,1fr);align-items:stretch!important;gap:9px!important}form.inline>*,.action-row>*{width:100%;min-width:0}form.inline button,form.inline .button,.action-row button,.action-row .button{width:100%}label{min-width:0;gap:6px}.confirm{min-height:44px;width:100%;align-items:center;padding:5px 0}.confirm input{width:22px!important;min-height:22px!important;height:22px;margin:0}.action-box{gap:10px;padding:12px!important;border-radius:13px!important}
.table-wrap{max-width:100%!important;overflow:visible!important;border:0!important;border-radius:0!important;background:transparent!important}
table.mobile-card-table{display:block;width:100%;min-width:0!important;border:0}table.mobile-card-table thead{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}table.mobile-card-table tbody{display:grid;gap:10px;width:100%}table.mobile-card-table tbody tr{display:block;width:100%;min-width:0;margin:0;padding:4px 12px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,rgba(15,21,32,.95),rgba(9,13,20,.96));box-shadow:0 7px 24px rgba(0,0,0,.13)}
table.mobile-card-table tbody td{display:grid;grid-template-columns:minmax(88px,32%) minmax(0,1fr);gap:12px;width:100%;min-width:0;padding:10px 0!important;border:0!important;border-bottom:1px solid rgba(29,39,53,.76)!important;text-align:left!important;overflow-wrap:anywhere;word-break:break-word}table.mobile-card-table tbody td:last-child{border-bottom:0!important}table.mobile-card-table tbody td:before{content:attr(data-label);min-width:0;color:var(--muted);font-size:10px;font-weight:700;line-height:1.35;letter-spacing:.055em;text-transform:uppercase;overflow-wrap:anywhere}table.mobile-card-table tbody td.mobile-colspan{grid-template-columns:minmax(0,1fr)}table.mobile-card-table tbody td.mobile-colspan:before,table.mobile-card-table tbody td[data-label='']:before{display:none}table.mobile-card-table code,table.mobile-card-table pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
table.mobile-kv-table{display:block;width:100%;min-width:0!important;border:0}table.mobile-kv-table tbody{display:grid;gap:10px;width:100%;min-width:0}table.mobile-kv-table tbody tr{display:block;width:100%;min-width:0;margin:0;padding:11px 12px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(145deg,rgba(15,21,32,.95),rgba(9,13,20,.96));box-shadow:0 7px 24px rgba(0,0,0,.13)}table.mobile-kv-table tbody td{display:block;width:100%;min-width:0;padding:0!important;border:0!important;text-align:left!important;overflow-wrap:anywhere;word-break:break-word}table.mobile-kv-table tbody td:first-child{margin-bottom:6px;color:var(--muted);font-size:10px;font-weight:700;line-height:1.35;letter-spacing:.055em;text-transform:uppercase}table.mobile-kv-table tbody td:last-child{font-size:13px}table.mobile-kv-table code{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-all}pre{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
}
@media (max-width:480px){.main{padding-left:10px!important;padding-right:10px!important}.topline>.pill{display:none}.subtitle{max-width:92vw}.card,.card.wide,.card.full{padding:13px!important}table.mobile-card-table tbody td{grid-template-columns:minmax(78px,31%) minmax(0,1fr);gap:10px}}
@media (prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition-duration:.001ms!important;animation-duration:.001ms!important}}
</style>
"""

_LOGIN_STYLE = """
<style id='velia-mobile-login-ui'>
:root{color-scheme:dark}html{-webkit-text-size-adjust:100%}body{min-height:100dvh!important;padding:max(16px,env(safe-area-inset-top)) 14px max(16px,env(safe-area-inset-bottom))!important}.box{width:min(440px,100%)!important}a,button,input{touch-action:manipulation}a,button{min-height:50px;display:flex!important;align-items:center;justify-content:center}input{min-height:52px;font-size:16px!important}
@media(max-width:480px){body{align-items:start!important;padding-top:max(18px,env(safe-area-inset-top))!important}.box{padding:20px!important;border-radius:20px!important}h1{font-size:23px!important;line-height:1.15}p{margin:9px 0}.step{padding:12px!important;border-radius:13px!important}}
</style>
"""

_TABLE_RE = re.compile(r"<table(?P<attrs>[^>]*)>(?P<body>.*?)</table>", re.IGNORECASE | re.DOTALL)
_HEAD_RE = re.compile(r"<thead[^>]*>(?P<body>.*?)</thead>", re.IGNORECASE | re.DOTALL)
_TH_RE = re.compile(r"<th[^>]*>(?P<body>.*?)</th>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<tr(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_OPEN_RE = re.compile(r"<td(?P<attrs>[^>]*)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_label(fragment: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", str(fragment or ""))).split())


def _append_table_class(attrs: str, class_name: str) -> str:
    raw = str(attrs or "")
    if re.search(r"\bclass=(['\"])", raw, re.IGNORECASE):
        return re.sub(
            r"\bclass=(['\"])(.*?)\1",
            lambda m: f"class={m.group(1)}{m.group(2)} {class_name}{m.group(1)}",
            raw,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return raw + f" class='{class_name}'"


def _mobile_table(table_match: re.Match[str]) -> str:
    attrs = table_match.group("attrs") or ""
    body = table_match.group("body") or ""
    head = _HEAD_RE.search(body)
    if head is None:
        data_rows = [row for row in _ROW_RE.finditer(body) if "<td" in (row.group("body") or "").lower()]
        if not data_rows:
            return table_match.group(0)
        if any(len(list(_TD_OPEN_RE.finditer(row.group("body") or ""))) != 2 for row in data_rows):
            return table_match.group(0)
        return f"<table{_append_table_class(attrs, 'mobile-kv-table')}>{body}</table>"

    labels = [_plain_label(match.group("body")) for match in _TH_RE.finditer(head.group("body"))]
    if not labels:
        return table_match.group(0)

    def patch_row(row_match: re.Match[str]) -> str:
        row_body = row_match.group("body") or ""
        if "<td" not in row_body.lower():
            return row_match.group(0)
        index = 0

        def patch_cell(cell_match: re.Match[str]) -> str:
            nonlocal index
            raw_attrs = cell_match.group("attrs") or ""
            colspan = re.search(r"\bcolspan\s*=", raw_attrs, re.IGNORECASE) is not None
            label = "" if colspan else (labels[index] if index < len(labels) else "")
            index += 1
            classes = " mobile-colspan" if colspan else ""
            class_match = re.search(r"\bclass=(['\"])(.*?)\1", raw_attrs, re.IGNORECASE | re.DOTALL)
            if classes:
                if class_match:
                    current = class_match.group(2)
                    replacement = f"class={class_match.group(1)}{current}{classes}{class_match.group(1)}"
                    raw_attrs = raw_attrs[:class_match.start()] + replacement + raw_attrs[class_match.end():]
                else:
                    raw_attrs += " class='mobile-colspan'"
            escaped_label = html.escape(label, quote=True)
            return f"<td{raw_attrs} data-label='{escaped_label}'>"

        patched = _TD_OPEN_RE.sub(patch_cell, row_body)
        return f"<tr{row_match.group('attrs') or ''}>{patched}</tr>"

    patched_body = _ROW_RE.sub(patch_row, body)
    attrs = _append_table_class(attrs, "mobile-card-table")
    return f"<table{attrs}>{patched_body}</table>"


def _enhance_tables(document: str) -> str:
    return _TABLE_RE.sub(_mobile_table, str(document or ""))


def _enhance_navigation(document: str) -> str:
    return str(document or "").replace("class='nav active'", "class='nav active' aria-current='page'", 1)


def _inject(document: str, marker: str, fragment: str) -> str:
    text = str(document or "")
    if fragment.strip() in text:
        return text
    return text.replace(marker, fragment + marker, 1) if marker in text else text + fragment


def _wrap(renderer: Callable[..., str], *, login: bool) -> Callable[..., str]:
    def wrapped(*args: Any, **kwargs: Any) -> str:
        document = renderer(*args, **kwargs)
        if not login:
            document = _enhance_navigation(_enhance_tables(document))
        return _inject(document, "</head>", _LOGIN_STYLE if login else _MOBILE_STYLE)
    return wrapped


def install_admin_mobile_ui_patch(admin_routes_module: Any) -> None:
    """Install a presentation-only, mobile-first owner-console layer."""
    if getattr(admin_routes_module, "_velia_admin_mobile_ui_installed", False):
        return
    original_layout = getattr(admin_routes_module, "_layout", None)
    original_login_page = getattr(admin_routes_module, "_login_page", None)
    if not callable(original_layout) or not callable(original_login_page):
        raise RuntimeError("admin_mobile_ui_renderer_missing")
    admin_routes_module._layout = _wrap(original_layout, login=False)
    admin_routes_module._login_page = _wrap(original_login_page, login=True)
    admin_routes_module._velia_admin_mobile_ui_installed = True
