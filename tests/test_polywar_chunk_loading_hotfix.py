from pathlib import Path

import pytest


def test_chunk_backend_uses_request_scoped_config_and_readonly_flow():
    src = Path('services/polywar_map_service.py').read_text()
    assert 'class PolyWarMapConfig' in src
    assert 'def load_map_config(conn)' in src
    assert 'terrain_at_with_config' in src
    assert 'start_owner_with_config' in src
    assert 'terrain_chunk_with_config' in src
    build = src.split('def build_chunks', 1)[1].split('def legacy_action_duplicate_response', 1)[0]
    forbidden = ['init_polywar_schema', 'init_polywar_map_schema', 'ensure_active_season_in_transaction', 'ensure_world_caught_up', 'ensure_capitals_initialized', 'get_or_create_player', 'conn.commit()']
    for token in forbidden:
        assert token not in build
    for token in ['CREATE ', 'ALTER ', 'INSERT ', 'UPDATE ', 'DELETE ', 'FOR UPDATE']:
        assert token not in build.upper()


def test_terrain_cache_key_includes_config_values():
    src = Path('services/polywar_map_service.py').read_text()
    fn = src.split('def terrain_chunk_with_config', 1)[1].split('def _terrain_chunk', 1)[0]
    assert 'config.width' in fn
    assert 'config.height' in fn
    assert 'config.starting_area_size' in fn
    assert 'bases_key' in fn


def test_read_endpoints_do_not_bootstrap_or_write():
    for path, fn_name in [
        ('services/polywar_sector_service.py', 'def get_sectors'),
        ('services/polywar_capital_service.py', 'def get_capitals'),
        ('services/polywar_governance_service.py', 'def get_governance'),
    ]:
        src = Path(path).read_text()
        body = src.split(fn_name, 1)[1].split('\ndef ', 1)[0]
        for token in ['init_polywar_schema', 'ensure_active_season_in_transaction', 'prepare_gameplay_mutation_in_transaction', 'ensure_capitals_initialized', 'initialize_sector', 'INSERT ', 'UPDATE ', 'DELETE ', 'FOR UPDATE']:
            assert token not in body
    assert '_synthetic_sector' in Path('services/polywar_sector_service.py').read_text()


def test_frontend_chunk_loader_grid_retry_and_initial_order():
    js = Path('webapp/polywar.js').read_text()
    assert 'bootstrapInitialLoad' in js and 'Promise.allSettled([this.ensureSectors(), this.refreshCapitals(), this.refreshGovernance()])' in js
    ensure = js.split('async ensureChunks', 1)[1].split('async ensureSectors', 1)[0]
    assert 'finally' in ensure
    assert '300' in ensure and '1000' in ensure
    assert 'Map data unavailable' in ensure
    assert 'Retry map' in js
    assert 'drawCellGrid' in js and 'drawSkeleton' in js
    assert 'this.drawCellGrid(ctx)' in js


def test_chunk_rate_limit_exposes_retry_delay_without_changing_limit(monkeypatch):
    from services import polywar_map_service as maps

    maps._CHUNK_RATE.clear()
    monkeypatch.setattr(maps.time, 'monotonic', lambda: 100.0)
    maps._check_chunk_rate(42, maps.CHUNK_RATE_MAX)
    monkeypatch.setattr(maps.time, 'monotonic', lambda: 102.2)
    with pytest.raises(maps.PolyWarChunkRateLimited) as caught:
        maps._check_chunk_rate(42, 1)
    assert str(caught.value) == 'rate_limited'
    assert caught.value.retry_after_seconds == 8
    assert maps.CHUNK_RATE_MAX == 60
    assert maps.CHUNK_RATE_WINDOW == 10


def test_chunk_hotfix_coalesces_camera_and_selection_loads_and_handles_429():
    js = Path('webapp/polywar.js').read_text()
    pointer = js.split('this.canvas.addEventListener("pointermove"', 1)[1].split('}, { signal });', 1)[0]
    select = js.split('select(x, y)', 1)[1].split('getCell(x, y)', 1)[0]
    ensure = js.split('async ensureChunks', 1)[1].split('async ensureSectors', 1)[0]
    assert 'this.scheduleChunkLoad()' in pointer
    assert 'this.ensureChunks()' not in pointer
    assert 'if (!this.cache.has(key) && !this.loading.has(key)) this.scheduleChunkLoad()' in select
    assert 'Date.now() < this.chunkCooldownUntil' in ensure
    assert 'data?.error === "rate_limited" || data?.httpStatus === 429' in ensure
    assert 'if (!rateLimited) requestedKeys.forEach' in ensure
    assert 'this.chunkRecoveryTimer' in js


def test_chunk_handler_returns_429_contract():
    src = Path('web.py').read_text()
    handler = src.split('async def handle_polywar_chunks_api', 1)[1].split('async def handle_polywar_sectors_api', 1)[0]
    assert 'except PolyWarChunkRateLimited as e:' in handler
    assert '"retry_after_seconds": e.retry_after_seconds' in handler
    assert 'status=429' in handler


def test_mobile_compact_sheet_buttons_stay_on_one_row():
    css = Path('webapp/polywar.css').read_text()
    assert '.compact-cell-sheet .sheet-actions .btn{grid-column:auto;width:auto}' in css
    assert 'grid-template-columns:minmax(0,1fr) auto' in css


def test_postgresql_get_capitals_uses_module_map_import(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_capital_service as caps
    calls = []
    class Cursor:
        def execute(self, sql, params=()): calls.append(sql)
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def __init__(self): self.closed = False
        def cursor(self): return Cursor()
        def rollback(self): calls.append('rollback')
        def close(self): self.closed = True; calls.append('close')
    conn = Conn()
    monkeypatch.setattr(caps.polywar, '_is_sqlite', lambda c: False)
    monkeypatch.setattr(caps.polywar, 'get_connection', lambda: conn)
    monkeypatch.setattr(caps.m, 'begin_polywar_readonly', lambda c: calls.append('begin_readonly'))
    monkeypatch.setattr(caps.m, 'load_map_config', lambda c, season=None, season_id=None: SimpleNamespace(capital_siege_required=777))
    monkeypatch.setattr(caps.m, 'get_active_season_readonly', lambda c: {'id': 9})
    out = caps.get_capitals(123)
    assert out['ok'] and out['siege_required'] == 777
    assert calls[0] == 'begin_readonly'
    assert 'close' in calls


def test_postgresql_get_governance_uses_config_rules_without_min_contribution(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_governance_service as gov
    calls = []
    class Cursor:
        def execute(self, sql, params=()): calls.append(sql)
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def cursor(self): return Cursor()
        def rollback(self): calls.append('rollback')
        def close(self): calls.append('close')
    rules = {'election_hours':24,'term_hours':168,'min_contribution':5,'min_members':2,'max_statement_length':280,'max_orders':5,'order_duration_hours':24}
    monkeypatch.setattr(gov.polywar, '_is_sqlite', lambda c: False)
    monkeypatch.setattr(gov.polywar, 'get_connection', lambda: Conn())
    monkeypatch.setattr(gov.m, 'begin_polywar_readonly', lambda c: calls.append('begin_readonly'))
    monkeypatch.setattr(gov.m, 'load_map_config', lambda c: SimpleNamespace(governance_rules=rules))
    monkeypatch.setattr(gov.m, 'get_active_season_readonly', lambda c: {'id': 9})
    monkeypatch.setattr(gov, 'min_contribution', lambda: (_ for _ in ()).throw(AssertionError('min_contribution called')))
    out = gov.get_governance(123)
    assert out['ok'] and out['rules']['min_contribution'] == 5
    assert out['nomination_eligibility']['eligible'] is False
    assert calls[0] == 'begin_readonly' and 'close' in calls


def test_legacy_terrain_helpers_build_full_config(monkeypatch):
    from services import polywar_map_service as m
    monkeypatch.setattr(m, 'map_width', lambda: 512)
    monkeypatch.setattr(m, 'map_height', lambda: 512)
    monkeypatch.setattr(m, 'chunk_size', lambda: 16)
    monkeypatch.setattr(m, 'starting_area_size', lambda: 15)
    monkeypatch.setattr(m, 'max_chunks_per_request', lambda: 9)
    monkeypatch.setattr(m, 'faction_base_positions', lambda width=None, height=None: {1: (20, 20)})
    monkeypatch.setattr(m, '_setting_int', lambda *args: args[1])
    assert m.terrain_at('seed', 1, 1)
    assert m._start_owner(20, 20) == 1
    chunk = m._terrain_chunk(1, 'seed', 0, 0, 16)
    assert len(chunk) == 16 and len(chunk[0]) == 16


def test_get_sectors_uses_config_sector_size_without_get_setting(monkeypatch):
    from types import SimpleNamespace
    from services import polywar_sector_service as sectors
    conn_count = {'n': 0}
    class Cursor:
        def execute(self, sql, params=()): pass
        def fetchall(self): return []
        def fetchone(self): return None
        @property
        def description(self): return []
    class Conn:
        def cursor(self): return Cursor()
        def rollback(self): pass
        def close(self): pass
    def get_conn(): conn_count['n'] += 1; return Conn()
    cfg = SimpleNamespace(width=1000, height=1000, sector_size=77, max_sectors_per_request=10)
    monkeypatch.setattr(sectors.polywar, 'get_connection', get_conn)
    monkeypatch.setattr(sectors.polywar, 'get_setting', lambda *a, **k: (_ for _ in ()).throw(AssertionError('get_setting called')))
    from services import polywar_map_service as map_service
    monkeypatch.setattr(map_service, 'load_map_config', lambda c, season=None, season_id=None: cfg)
    monkeypatch.setattr(map_service, 'get_active_season_readonly', lambda c: {'id': 3})
    monkeypatch.setattr(sectors, '_set_read_timeouts', lambda c: None)
    out = sectors.get_sectors(1, 0, 0, 0, 0)
    assert out['ok'] and out['sector_size'] == 77 and conn_count['n'] == 1


def test_polywar_mobile_marker_render_hierarchy_is_simplified():
    js = Path('webapp/polywar.js').read_text()
    assert 'drawBaseMarkers(ctx)' in js
    assert 'Math.min(14, this.cell*.36)' in js
    assert 'this.cell * 0.9' not in js
    assert 'drawSelectedCell(ctx)' in js
    selected = js.split('drawSelectedCell(ctx)', 1)[1].split('drawPendingPulse(ctx)', 1)[0]
    assert selected.count('strokeRect') == 1
    assert 'rgba(53,166,255,.16)' in selected
    assert 'drawPendingPulse(ctx)' in js and 'setLineDash([3,3])' not in js


def test_polywar_no_noisy_sector_dominance_labels_at_normal_zoom():
    js = Path('webapp/polywar.js').read_text()
    assert 'dominance_percent??0' not in js
    assert 'fillText(`${sx},${sy}' not in js


def test_polywar_compact_sheet_collapses_secondary_actions_and_uses_status_pill():
    css = Path('webapp/polywar.css').read_text()
    js = Path('webapp/polywar.js').read_text()
    assert 'max-height:132px' in css and 'max-height:min(140px' in css
    assert '.compact-cell-sheet:not(.compact-cell-sheet--expanded) .secondary-actions{display:none}' in css
    assert '.compact-cell-sheet--expanded{max-height:40vh;overflow-y:auto}' in css
    assert 'btn.classList.toggle("status-pill", !primary.enabled && !this.pending)' in js
    assert 'shortCellReason(primary.reason || "Ready")' in js


def test_polywar_more_toggle_accessible_and_resets_on_cell_or_primary():
    js = Path('webapp/polywar.js').read_text()
    assert 'more.setAttribute("aria-expanded", String(this.moreOpen))' in js
    assert 'more.classList.toggle("is-open", !!this.moreOpen)' in js
    assert "`${this.moreOpen ? 'Less' : 'More'} <span class=\"more-chevron\">▾</span>`" in js
    assert 'Less <span class="more-chevron">▴</span>' not in js
    assert 'if (this.selected?.x !== x || this.selected?.y !== y) this.moreOpen = false' in js
    assert 'this.moreOpen = false;\n    this.pending = true; this.pendingCellKey = target.key' in js


def test_polywar_primary_and_secondary_actions_are_separate_compact_containers():
    css = Path('webapp/polywar.css').read_text()
    js = Path('webapp/polywar.js').read_text()
    assert '<div class="sheet-actions"><button class="btn" id="primaryActionBtn"' in js
    assert '<div id="secondaryActionsMenu" class="secondary-actions" hidden>' in js
    assert 'class="secondary-action-pill" data-polywar-secondary' in js
    assert 'class="btn mini" data-polywar-secondary' not in js
    assert '.secondary-actions{grid-column:1/-1;border-top:' in css
    assert '.secondary-action-pill{min-height:38px' in css


def test_polywar_owner_reason_truncation_and_one_row_actions():
    css = Path('webapp/polywar.css').read_text()
    assert '.cell-owner-line{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis' in css
    assert '.compact-cell-sheet #cellReason{display:-webkit-box;-webkit-line-clamp:1' in css
    assert '.compact-cell-sheet--expanded #cellReason{-webkit-line-clamp:2}' in css
    assert '.sheet-actions{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px' in css
    assert '.compact-cell-sheet .sheet-actions .btn{grid-column:auto;width:auto' in css



def test_polywar_capital_marker_is_centered_and_unified_with_base():
    js = Path('webapp/polywar.js').read_text()
    capital_draw = js.split('draw(ctx, worldToScreen, factions = [], cellSize = 16, baseKeys = new Set())', 1)[1].split('panel(cap, state)', 1)[0]
    assert 'const cx = p.x + cellSize / 2, cy = p.y + cellSize / 2' in capital_draw
    assert 'ctx.arc(cx, cy, r, 0, Math.PI * 2)' in capital_draw
    assert 'ctx.arc(p.x, p.y, 8' not in capital_draw
    assert 'ctx.arc(p.x, p.y, 12' not in capital_draw
    assert 'if (capitalKeys.has(`${b.x},${b.y}`)) continue' in js
    assert 'new Set((this.state.map.bases || []).map(b => `${b.x},${b.y}`))' in js


def test_polywar_unified_capital_marker_preserves_fill_stroke_siege_and_home():
    js = Path('webapp/polywar.js').read_text()
    capital_draw = js.split('draw(ctx, worldToScreen, factions = [], cellSize = 16, baseKeys = new Set())', 1)[1].split('panel(cap, state)', 1)[0]
    assert 'ctx.fillStyle = controller' in capital_draw
    assert 'ctx.strokeStyle = original' in capital_draw
    assert 'cap.original_faction_id !== cap.controller_faction_id ? 2.5 : 1.5' in capital_draw
    assert 'baseKeys.has(`${cap.x},${cap.y}`)' in capital_draw and "ctx.fillText('⌂', cx, cy + .5)" in capital_draw
    assert 'if (cap.is_under_siege)' in capital_draw
    assert 'ctx.arc(cx, cy, r + 4' in capital_draw


def test_polywar_selected_outline_draws_after_unified_marker_without_extra_marker_shape():
    js = Path('webapp/polywar.js').read_text()
    draw = js.split('  draw() {', 1)[1].split('\n}', 1)[0]
    assert draw.index('polywarCapitalUi.draw(ctx') < draw.index('this.drawSelectedCell(ctx)')
    selected = js.split('drawSelectedCell(ctx)', 1)[1].split('drawPendingPulse(ctx)', 1)[0]
    assert selected.count('strokeRect') == 1
    assert 'ctx.arc(' not in selected


def test_polywar_more_chevron_uses_fixed_symbol_with_css_rotation_only():
    js = Path('webapp/polywar.js').read_text()
    css = Path('webapp/polywar.css').read_text()
    assert "`${this.moreOpen ? 'Less' : 'More'} <span class=\"more-chevron\">▾</span>`" in js
    assert 'Less <span class="more-chevron">▴</span>' not in js
    assert '#moreActionsBtn.is-open .more-chevron{transform:rotate(180deg)}' in css
    assert 'more.classList.toggle("is-open", !!this.moreOpen)' in js
