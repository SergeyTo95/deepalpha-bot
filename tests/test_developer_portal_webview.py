from pathlib import Path


def test_quick_analysis_patch_is_idempotent_and_does_not_watch_full_subtree():
    source = Path("webapp/developer_quick_analysis.js").read_text(encoding="utf-8")

    assert 'if (paragraph.textContent !== message) paragraph.textContent = message;' in source
    assert 'observer.observe(document.getElementById("appRoot") || document.body, { childList: true });' in source
    assert 'document.documentElement, { childList: true, subtree: true }' not in source


def test_commercial_observer_remains_scoped_by_developer_html_wrapper():
    html = Path("webapp/developer.html").read_text(encoding="utf-8")

    assert 'target?.id === "appRoot"' in html
    assert 'subtree: false' in html
    assert 'developer_commercial.js?v=2.0' in html
