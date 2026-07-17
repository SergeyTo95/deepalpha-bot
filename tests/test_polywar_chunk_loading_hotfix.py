from pathlib import Path
import asyncio
import json
import types
import subprocess

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
    assert 'this.scheduleChunkLoad({delay:80,priorityKey:key,includeVisible:true})' in select
    assert 'this.isChunkCooldownActive()' in ensure
    assert 'data?.error === "rate_limited" || data?.httpStatus === 429' in ensure
    assert 'const successfulKeys=new Set(), rateLimitedKeys=new Set(), terminalFailedKeys=new Set()' in ensure
    assert 'const retryKeys' not in ensure
    assert '!this.failedChunks.has(`${x},${y}`)' in ensure
    assert 'this.chunkRecoveryTimer' in js


def test_chunk_handler_returns_429_contract():
    src = Path('web.py').read_text()
    handler = src.split('async def handle_polywar_chunks_api', 1)[1].split('async def handle_polywar_sectors_api', 1)[0]
    assert 'except PolyWarChunkRateLimited as e:' in handler
    assert '"retry_after_seconds": e.retry_after_seconds' in handler
    assert 'status=429' in handler


def test_chunk_handler_runtime_429_and_validation_contract():
    from services.polywar_map_service import PolyWarChunkRateLimited
    source = Path('web.py').read_text()
    handler = 'async def handle_polywar_chunks_api' + source.split('async def handle_polywar_chunks_api', 1)[1].split('async def handle_polywar_sectors_api', 1)[0]
    class Response:
        def __init__(self, payload, status=200): self.body=json.dumps(payload); self.status=status
    ns = {'asyncio': asyncio, 'PolyWarChunkRateLimited': PolyWarChunkRateLimited,
          '_json_response': lambda payload, status=200: Response(payload, status),
          '_polywar_unauthorized': lambda: Response({}, 401), '_polywar_read_error_response': lambda e: Response({}, 500)}
    exec(handler, ns)
    request = types.SimpleNamespace(query={'chunks': '0,0'})
    ns['_current_web_user'] = lambda _request: {'user_id': 7}
    ns['get_polywar_chunks'] = lambda *_args: (_ for _ in ()).throw(PolyWarChunkRateLimited(7))
    response = asyncio.run(ns['handle_polywar_chunks_api'](request)); body = json.loads(response.body)
    assert response.status == 429 and body == {'ok': False, 'error': 'rate_limited', 'retry_after_seconds': 7}
    ns['get_polywar_chunks'] = lambda *_args: (_ for _ in ()).throw(ValueError('out_of_bounds'))
    response = asyncio.run(ns['handle_polywar_chunks_api'](request))
    assert response.status == 400 and json.loads(response.body)['error'] == 'out_of_bounds'


def test_chunk_rate_limit_uses_required_release_slot_and_expires_old_entries(monkeypatch):
    from collections import deque
    from services import polywar_map_service as maps
    maps._CHUNK_RATE.clear()
    maps._CHUNK_RATE[9] = deque([90.5] * 3 + [96.5] * 56)
    monkeypatch.setattr(maps.time, 'monotonic', lambda: 100.0)
    with pytest.raises(maps.PolyWarChunkRateLimited) as caught:
        maps._check_chunk_rate(9, 5)
    assert caught.value.retry_after_seconds == 7
    assert 1 <= caught.value.retry_after_seconds <= maps.CHUNK_RATE_WINDOW
    monkeypatch.setattr(maps.time, 'monotonic', lambda: 107.0)
    maps._check_chunk_rate(9, 5)
    assert len(maps._CHUNK_RATE[9]) == 5


def test_chunk_rate_nonpositive_and_oversized_amounts(monkeypatch):
    from services import polywar_map_service as maps
    maps._CHUNK_RATE.clear(); monkeypatch.setattr(maps.time, 'monotonic', lambda: 10.25)
    maps._check_chunk_rate(1, 0); assert not maps._CHUNK_RATE[1]
    with pytest.raises(maps.PolyWarChunkRateLimited) as caught:
        maps._check_chunk_rate(1, maps.CHUNK_RATE_MAX + 1)
    assert caught.value.retry_after_seconds == maps.CHUNK_RATE_WINDOW


def test_build_chunks_deduplicates_before_limits_and_expected_logging():
    src = Path('services/polywar_map_service.py').read_text()
    build = src.split('def build_chunks', 1)[1].split('def legacy_action_duplicate_response', 1)[0]
    dedupe = 'chunks = list(dict.fromkeys((int(cx), int(cy)) for cx, cy in chunks))'
    assert dedupe in build and build.index(dedupe) < build.index('_check_chunk_rate')
    expected = build.split('except PolyWarChunkRateLimited as exc:', 1)[1].split('except Exception as exc:', 1)[0]
    assert 'logger.info(' in expected and 'logger.exception(' not in expected and 'raise' in expected


def test_production_chunk_coordinator_node_runtime(tmp_path):
    js = Path('webapp/polywar.js').read_text()
    start = js.index('class PolyWarMap')
    end = js.index('\nif (typeof window !== "undefined") window.polywarChunkTestHooks', start)
    class_source = js[start:end]
    script = f'''const assert=require("assert"), vm=require("vm");
let now=1000,next=1,timers=new Map();
const sandbox={{Map,Set,Promise,Math,Number,String,Array,Object,Date:{{now:()=>now}},performance:{{now:()=>now}},
 setTimeout:(fn,ms)=>{{const id=next++;timers.set(id,{{fn,at:now+ms}});return id;}},clearTimeout:id=>timers.delete(id),
 polywarReducedMotion:()=>true,polywarLowPowerMode:()=>false,POLYWAR_VISUALS:{{defaultCell:12,minCell:3,maxCell:58,selectionAnimationMs:1}},
 TACTICAL_MIN_CELL:6,document:{{getElementById:()=>null}},window:{{}},currentState:{{factions:[]}},actionMode:"capture",
 resolvePrimaryCellAction:()=>({{enabled:true,label:"Capture"}}),shortCellReason:x=>x,polywarCapitalUi:{{cache:new Map()}},quickActionsEnabled:true}};
vm.createContext(sandbox);vm.runInContext({class_source!r}+";this.C=PolyWarMap",sandbox);const C=sandbox.C;
function h(){{const x=Object.create(C.prototype);Object.assign(x,{{destroyed:false,state:{{map:{{width:1000,height:1000,chunk_size:10}}}},cache:new Map(),loading:new Set(),chunkRequestsByKey:new Map(),failedChunks:new Set(),initialLoadStarted:true,selected:null,moreOpen:false,visualLowPower:true,chunkCooldownUntil:0,chunkLoadDebounceTimer:null,queuedPriorityChunkKey:null,queuedVisibleChunkLoad:false,chunkRecoveryTimer:null,chunkRecoveryPromise:null,chunkManualRetryPromise:null,chunkLoadQueuedDuringCooldown:false,loadSeq:0,updatePanel(){{}},requestDraw(){{}},visibleChunks(){{return [[0,0],[1,0]]}},ensureChunks(o){{this.calls.push(o);return Promise.resolve({{ok:true}})}},calls:[]}});return x;}}
let x=h();for(let i=0;i<20;i++)x.select(i,0);assert.equal(x.calls.length,0);assert.equal(timers.size,1);[...timers.values()][0].fn();assert.equal(x.calls.length,1);assert.equal(x.calls[0].keys[0].join(","),"1,0");
timers.clear();x=h();x.cache.set("0,0",{{}});for(let i=0;i<9;i++)x.select(i,0);assert.equal(timers.size,0);assert.equal(x.calls.length,0);
x=h();for(let i=0;i<50;i++)x.scheduleChunkLoad();assert.equal(timers.size,1);[...timers.values()][0].fn();assert.equal(x.calls.length,1);timers.clear();
x=h();x.scheduleChunkLoad({{priorityKey:"4,4"}});x.chunkCooldownUntil=6000;x.scheduleChunkLoad();assert.equal(timers.size,2);C.prototype.destroy.call(Object.assign(x,{{abort:{{abort(){{}}}},loading:new Set(),pendingRequests:new Map(),sectorLoading:new Set()}}));[...timers.values()].forEach(t=>t.fn());assert(x.destroyed);assert.equal(x.queuedPriorityChunkKey,null);assert.equal(x.queuedVisibleChunkLoad,false);'''
    path = tmp_path / 'chunk-runtime.js'; path.write_text(script)
    subprocess.run(['node', str(path)], check=True)


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
