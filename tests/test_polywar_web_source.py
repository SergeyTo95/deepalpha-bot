from pathlib import Path


def test_private_polywar_api_requires_session_and_ignores_client_user_id():
    source = Path("web.py").read_text()
    assert "async def handle_polywar_state_api" in source
    assert "return _polywar_unauthorized()" in source
    join_block = source[source.index("async def handle_polywar_join_api"):source.index("app = web.Application()")]
    assert 'data.get("faction_id")' in join_block
    assert 'data.get("user_id")' not in join_block
    assert 'current.get("user_id")' in join_block


def test_polywar_page_route_and_redirect_are_registered():
    source = Path("web.py").read_text()
    assert 'app.router.add_get("/polywar", handle_polywar_page)' in source
    assert 'web.HTTPFound("/polywar")' in source
    assert Path("webapp/polywar.html").read_text().count("telegram-web-app.js") == 1
    assert "Global War Map — coming in Phase 2" in Path("webapp/polywar.js").read_text()


def test_polywar_endpoints_are_registered():
    source = Path("web.py").read_text()
    for route in ["/api/polywar/state", "/api/polywar/factions", "/api/polywar/player", "/api/polywar/events", "/api/polywar/join"]:
        assert route in source


def test_polywar_static_files_exist_and_are_fullscreen():
    assert Path("webapp/polywar.html").exists()
    css = Path("webapp/polywar.css").read_text()
    assert "safe-area-inset" in css
    assert "min-height:100vh" in css


def test_dashboard_has_polywar_button():
    js = Path("webapp/app.js").read_text()
    assert '<a href="/polywar"><button class="btn btn-primary">PolyWar</button></a>' in js


def test_polywar_join_accepts_only_faction_id_payload():
    source = Path("web.py").read_text()
    join_block = source[source.index("async def handle_polywar_join_api"):source.index("app = web.Application()")]
    assert 'faction_id = int(data.get("faction_id") or 0)' in join_block


def test_polywar_api_does_not_render_inside_approot():
    assert "polywarRoot" in Path("webapp/polywar.html").read_text()
    assert "appRoot" not in Path("webapp/polywar.html").read_text()


def test_polywar_js_has_live_energy_countdown_sync_and_unavailable_state():
    js = Path("webapp/polywar.js").read_text()
    assert "setInterval" in js
    assert "seconds_until_next_energy" in js
    assert "syncTimer" in js
    assert "clearTimers" in js
    assert "PolyWar is temporarily unavailable" in js


def test_polywar_stats_are_season_scoped_not_global_faction_stats():
    service = Path("services/polywar_service.py").read_text()
    assert "polywar_faction_season_stats" in service
    create_factions = service[service.index("CREATE TABLE IF NOT EXISTS polywar_factions"):service.index("CREATE TABLE IF NOT EXISTS polywar_faction_season_stats")]
    assert "seasonal_influence_score" not in create_factions
    assert "active_members_count" not in create_factions
