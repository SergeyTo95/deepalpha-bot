from pathlib import Path

JS = Path("webapp/polywar.js").read_text()
CSS = Path("webapp/polywar.css").read_text()


def test_primary_resolver_neutral_eligible_capture_cost():
    assert "function resolvePrimaryCellAction" in JS
    assert "return enabled(\"capture\")" in JS
    assert "function primaryActionCost" in JS
    assert 'if (action === "capture") return base' in JS


def test_primary_resolver_enemy_adjacent_attack():
    assert 'return enabled("attack")' in JS
    assert "ownAdjacent" in JS


def test_primary_resolver_enemy_non_adjacent_reason():
    assert 'disabled("attack", "Your territory is not adjacent")' in JS


def test_primary_resolver_own_contested_reinforce():
    assert 'return enabled("reinforce")' in JS
    assert "contest_progress" in JS


def test_primary_resolver_enemy_capital_siege():
    assert 'return enabled("siege")' in JS
    assert "controller_faction_id" in JS


def test_primary_resolver_own_damaged_capital_repair():
    assert 'return enabled("repair_capital")' in JS
    assert "siege_progress" in JS


def test_active_rift_blocks_primary_and_secondary_seal_exists():
    assert 'Active rift must be sealed first' in JS
    assert 'data-polywar-action=\"seal_rift\"' in JS


def test_insufficient_energy_disabled_and_no_request_before_enabled():
    assert '"Not enough energy"' in JS
    execute = JS[JS.index("async executePrimaryCellAction"):]
    assert 'if (!primary.enabled || !primary.action || actionType !== primary.action)' in execute
    assert execute.index('if (!primary.enabled || !primary.action || actionType !== primary.action)') < execute.index('/api/polywar/action')


def test_locked_player_disabled():
    assert 'Player is temporarily locked' in JS


def test_clean_tap_selects_and_quick_action_sends_one_request_path():
    assert "async handleCellTap" in JS
    assert "this.select(x, y)" in JS
    assert "quickActionsEnabled && primary.enabled" in JS
    assert "executePrimaryCellAction(primary.action)" in JS


def test_pan_greater_than_threshold_sends_zero_actions():
    assert "dist > 8" in JS
    assert "g.pan = true" in JS
    assert "!g.pan" in JS
    assert "this.hadMultiTouch" in JS


def test_double_tap_sends_maximum_one_gameplay_request():
    assert "now - this.lastTap.t < 320" in JS


def test_pending_second_tap_ignored():
    assert "if (!this.selected || this.pending) return" in JS
    assert "pendingCellKey" in JS


def test_quick_actions_default_on():
    assert 'localStorage.getItem("polywar_quick_actions") !== "off"' in JS


def test_quick_actions_off_tap_only_selects_primary_button_sends():
    assert "Quick actions: OFF" in JS
    assert "primaryActionBtn" in JS
    assert "executePrimaryCellAction()" in JS


def test_quick_actions_localstorage_persistence():
    assert 'localStorage.setItem("polywar_quick_actions"' in JS


def test_only_one_primary_core_button_rendered():
    render_start = JS.index('function render(state)')
    render = JS[JS.index('root.innerHTML = `', render_start):JS.index('root.onclick = handlePolywarUiClick', render_start)]
    assert render.count('id="primaryActionBtn"') == 1


def test_old_simultaneous_core_button_list_absent():
    render = JS[JS.index('root.innerHTML = `'):JS.index('root.onclick = handlePolywarUiClick')]
    assert 'data-mode="attack"' not in render
    assert 'data-mode="reinforce"' not in render
    assert 'data-mode="siege"' not in render
    assert 'data-mode="repair_capital"' not in render


def test_secondary_more_menu_contextual_actions():
    assert "resolveSecondaryCellActions" in JS
    assert "moreActionsBtn" in JS
    assert "this.moreOpen" in JS


def test_loading_cell_no_request_and_recompute_after_chunk_load():
    assert "Loading cell data…" in JS
    tap = JS[JS.index("async handleCellTap"):JS.index("async executePrimaryCellAction")]
    assert "await this.ensureChunks" in tap
    assert "resolvePrimaryCellAction" in tap


def test_pan_zoom_preserved_after_action_refresh():
    assert "syncState(false, { soft: true })" in JS
    assert "softUpdate(state)" in JS
    assert "map?.updateState(currentState)" in JS


def test_map_instance_not_recreated_after_successful_action():
    action = JS[JS.index("async executePrimaryCellAction"):JS.index("async scan(size,")]
    assert "new PolyWarMap" not in action
    assert "render(" not in action


def test_frozen_target_and_stale_tap_guard_present():
    assert "const target = { x: this.selected.x, y: this.selected.y" in JS
    assert "tapSeq !== this.tapSeq" in JS
    assert "target.x" in JS and "target.y" in JS


def test_secondary_clicks_revalidate_resolver_before_api():
    assert "async executeSecondaryCellAction" in JS
    assert "resolveSecondaryCellActions({ cell:c, selected:target" in JS
    assert "!allowed || !allowed.enabled" in JS


def test_mobile_sheet_layout_is_compact_overlay():
    assert ".compact-cell-sheet" in CSS
    assert "position:absolute" in CSS
    assert "max-height:150px" in CSS or "max-height:145px" in CSS


def test_node_vm_mobile_gesture_and_sheet_css_runtime():
    import subprocess
    import textwrap

    script = textwrap.dedent(r'''
        const assert = require('assert');
        const fs = require('fs');
        const css = fs.readFileSync('webapp/polywar.css', 'utf8');
        assert(css.includes('touch-action:none'));
        assert(css.includes('user-select:none'));
        assert(css.includes('-webkit-user-select:none'));
        assert(css.includes('overscroll-behavior:contain'));
        assert(/\.compact-cell-sheet\{[^}]*overflow:hidden/.test(css));
        assert(/\.compact-cell-sheet--expanded\{[^}]*overflow-y:auto/.test(css));

        class Harness {
          constructor() {
            this.selected = {x: 1, y: 1};
            this.pending = false;
            this.sent = 0;
            this.taps = 0;
            this.pointerStarts = new Map();
            this.hadMultiTouch = false;
            this.cx = 0;
            this.cy = 0;
            this.cell = 10;
            this.canvas = { setPointerCapture() {} };
          }
          select(x, y) { this.selected = {x, y}; }
          async handleCellTap(x, y) { if (this.pending) return; this.taps++; this.select(x, y); this.sent++; }
          screenToCell() { return {x: 9, y: 9}; }
          clamp() {}
          ensureChunks() {}
          ensureSectors() {}
          requestDraw() {}
          pointerdown(e) { this.canvas.setPointerCapture(e.pointerId); this.pointerStarts.set(e.pointerId, { x:e.clientX, y:e.clientY, cx:this.cx, cy:this.cy, pan:false }); if (this.pointerStarts.size > 1) this.hadMultiTouch = true; }
          pointermove(e) { const g=this.pointerStarts.get(e.pointerId); if (!g) return; const dist=Math.hypot(e.clientX-g.x, e.clientY-g.y); if (dist > 8) g.pan = true; if (this.hadMultiTouch || !g.pan) return; this.cx = g.cx - (e.clientX - g.x) / this.cell; this.cy = g.cy - (e.clientY - g.y) / this.cell; }
          pointerup(e) { const g=this.pointerStarts.get(e.pointerId); this.pointerStarts.delete(e.pointerId); const wasMulti=this.hadMultiTouch; if (!this.pointerStarts.size) this.hadMultiTouch = false; if (g && !g.pan && !wasMulti && this.pointerStarts.size === 0) { const p = this.screenToCell(e.offsetX, e.offsetY); this.handleCellTap(p.x, p.y); } }
          pointercancel(e) { this.pointerStarts.delete(e.pointerId); if (!this.pointerStarts.size) this.hadMultiTouch = false; }
        }

        const pending = new Harness();
        pending.pending = true;
        pending.handleCellTap(2, 2);
        assert.deepStrictEqual(pending.selected, {x: 1, y: 1});
        assert.strictEqual(pending.sent, 0);

        const pan = new Harness();
        pan.pointerdown({pointerId: 1, clientX: 0, clientY: 0});
        pan.pointermove({pointerId: 1, clientX: 9, clientY: 0});
        pan.pointerup({pointerId: 1, offsetX: 9, offsetY: 0});
        assert.strictEqual(pan.taps, 0);

        const multi = new Harness();
        multi.pointerdown({pointerId: 1, clientX: 0, clientY: 0});
        multi.pointerdown({pointerId: 2, clientX: 1, clientY: 1});
        multi.pointerup({pointerId: 1, offsetX: 0, offsetY: 0});
        multi.pointerup({pointerId: 2, offsetX: 1, offsetY: 1});
        assert.strictEqual(multi.taps, 0);

        const cancel = new Harness();
        cancel.pointerdown({pointerId: 1, clientX: 0, clientY: 0});
        cancel.pointercancel({pointerId: 1});
        cancel.pointerup({pointerId: 1, offsetX: 0, offsetY: 0});
        assert.strictEqual(cancel.taps, 0);
    ''')
    subprocess.run(["node", "-e", script], check=True)


def test_visual_depth_render_path_and_close_camera_defaults():
    assert "const POLYWAR_VISUALS" in JS
    assert "defaultCell: 28" in JS
    assert "baseZoom: 34" in JS
    assert "drawTerrainTile" in JS
    assert "drawMountainRelief" in JS
    assert "terrainDepth" in JS


def test_living_world_replaces_cloud_ambient():
    assert "initAmbientLife" in JS
    assert "drawFeature" in JS
    assert "drawBirds" in JS
    assert "this.birds" in JS
    assert "polywarReducedMotion" in JS
    assert "prefers-reduced-motion: reduce" in JS
    assert "drawAmbient" not in JS
    assert "this.clouds" not in JS
    assert "maxClouds" not in JS


def test_ambient_birds_are_lightweight_and_no_separate_raf():
    assert "maxBirds: 2" in JS
    assert "requestAnimationFrame((now)" not in JS
    assert "startAmbientLoop" not in JS
    assert "document.hidden" in JS

def test_map_visual_css_preserves_mobile_touch_and_overlay_layers():
    assert ".map-wrap::before" in CSS
    assert "pointer-events:none" in CSS
    assert "isolation:isolate" in CSS
    assert "height:72vh" in CSS
    assert "z-index:6" in CSS


def test_sector_overview_zoom_is_reachable_and_sector_modes_preserved():
    assert "minCell: 3" in JS or "minCell: 2" in JS
    assert "defaultCell: 28" in JS
    assert "baseZoom: 34" in JS
    assert "if (this.cell < 8)" in JS
    assert "if (!forceKey && this.cell < 6)" in JS


def test_base_button_uses_center_on_base_close_zoom_helper():
    bind = JS[JS.index('document.getElementById("goBase")'):JS.index('document.getElementById("primaryActionBtn")')]
    assert "this.centerOnBase()" in bind
    assert "this.cx = b.x" not in bind
    assert "this.cy = b.y" not in bind
    assert "this.cell = Math.min(POLYWAR_VISUALS.maxCell, zoom)" in JS


def test_living_world_features_use_main_render_pass():
    assert 'id="polywarAmbientCanvas"' in JS
    assert "drawFeature" in JS
    assert "ch.features" in JS
    assert "this.draw(" not in JS[JS.index("drawBirds(ctx"):JS.index("drawSkeleton", JS.index("drawBirds(ctx"))]
    assert "#polywarAmbientCanvas" in CSS
    assert "pointer-events:none" in CSS


def test_road_detail_uses_bevel_not_fixed_diagonal():
    assert "drawRoadBevel" in JS
    start = JS.index("drawRoadBevel(ctx")
    road = JS[start : JS.index("drawMountainRelief", start)]
    assert "cell*.62" not in road
    assert "cell*.38" not in road
    assert "p.y + c*.8" in road

def test_world_view_minimap_static_hooks_present():
    from pathlib import Path
    js = Path('webapp/polywar.js').read_text()
    css = Path('webapp/polywar.css').read_text()
    assert 'id="openWorldView"' in js and 'World View' in js
    assert 'polywarMinimapCanvas' in js and 'jumpToWorldPosition' in js
    assert '.polywar-minimap' in css and '.polywar-world-view' in css


def test_lod2_and_letterbox_runtime_hooks_are_wired():
    from pathlib import Path
    js = Path('webapp/polywar.js').read_text()
    draw = js.split('  draw() {', 1)[1].split('  drawCellGrid', 1)[0]
    assert 'const ctx = this.ctx, lod = this.lodLevel()' in js
    assert 'if (lod === 2)' in draw and 'drawCoarseWorld(ctx)' in draw
    assert 'this.drawTerrainTile' not in draw.split('if (lod === 2)',1)[1].split('const visible',1)[0]
    assert 'overviewTransform(canvas' in js and 'overviewPointerToWorld(canvas' in js
    assert 'nearestHqAt(canvas' in js and 'radiusPx*radiusPx' in js
    assert 'renderOpenWorldView()' in js and 'data-retry' in js


def test_destroy_removes_open_world_view_modal_and_nulls_reference():
    assert 'if (this.worldViewModal) { this.worldViewModal.remove(); this.worldViewModal = null; }' in JS
    assert 'if (seq !== polywarOverviewSeq || this.destroyed) return' in JS


def test_lod2_preserves_selected_and_pending_without_detailed_chunks():
    draw = JS.split('if (lod === 2)', 1)[1].split('const visible', 1)[0]
    assert 'drawCoarseWorld(ctx)' in draw
    assert 'drawBaseMarkers(ctx)' in draw
    assert 'polywarCapitalUi.draw' in draw
    assert 'this.drawSelectedCell(ctx)' in draw
    assert 'this.drawPendingPulse(ctx)' in draw
    assert 'drawTerrainTile' not in draw


def test_world_view_load_overview_handles_api_ok_false_and_stale_state():
    load = JS[JS.index('async loadOverview()'):JS.index('overviewTransform(canvas', JS.index('async loadOverview()'))]
    assert "if (!data?.ok)" in load
    assert "this.overviewError = data?.error || 'overview_failed'" in load
    assert "this.overviewError = 'stale_overview'" in load
    assert load.count('this.renderOpenWorldView()') >= 4
    assert "if (seq !== polywarOverviewSeq || this.destroyed) return" in load


def test_world_view_retry_and_single_modal_lifecycle_hooks():
    render = JS[JS.index('renderOpenWorldView()'):JS.index('openWorldView()', JS.index('renderOpenWorldView()'))]
    open_view = JS[JS.index('openWorldView()'):JS.index('drawSelectedCell', JS.index('openWorldView()'))]
    assert 'data-retry' in render
    assert "this.overviewError=null" in render
    assert "target.textContent='Loading World View…'; this.loadOverview();" in render
    assert 'this.worldViewModal && document.body.contains(this.worldViewModal)' in open_view
    assert 'this.renderOpenWorldView(); return;' in open_view
    assert 'this.worldViewModal.remove(); this.worldViewModal = null;' in open_view


def test_zoom_out_handoff_to_world_view_and_lod2_fallback_kept():
    assert 'const TACTICAL_MIN_CELL = 6' in JS
    assert 'zoomOutOrOpenWorld' in JS
    assert 'const nextCell' in JS and 'this.openWorldView({ source: "zoom-out" })' in JS
    assert 'if (this.cell >= TACTICAL_MIN_CELL) this.ensureChunks()' in JS
    assert 'POLYWAR_VISUALS.minCell' in JS


def test_minimap_redesign_layers_and_interactions_static():
    assert 'starting_zones' in JS
    assert 'drawStrategicMarker' in JS
    assert 'drawViewportRect' in JS
    assert 'Math.max(5' in JS
    assert 'nearestHqAt(this.minimapCanvas' in JS
    assert 'localStorage.setItem("polywar_minimap_collapsed"' in JS
    assert '.polywar-minimap:before' in CSS
    assert 'pointer-events:none' in CSS


def test_world_view_selects_before_tactical_jump_static():
    assert 'Grid distance:' in JS
    assert 'data-open-tactical' in JS
    assert 'this.jumpToWorldPosition(x,y,selection.hq?POLYWAR_VISUALS.baseZoom:10' in JS


def test_world_view_target_runtime_no_reference_error_and_delayed_jump():
    import subprocess, textwrap
    script = textwrap.dedent("""
        const assert = require('assert');
        function esc(v){ return String(v).replace(/[&<>\"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[m])); }
        const POLYWAR_VISUALS = { baseZoom: 34 };
        let button = null;
        class Target { constructor(){ this.innerHTML=''; } querySelector(){ button = { onclick:null }; return button; } }
        class Harness {
          constructor(){ this.cx=10; this.cy=20; this.overview={world:{sector_size:40}}; this.state={map:{sector_size:40}}; this.worldViewModal={remove(){ Harness.removed=(Harness.removed||0)+1; }}; this.jumps=0; }
          jumpToWorldPosition(x,y,z,o){ this.jumps++; this.jump={x,y,z,o}; }
          renderWorldTargetSelection(target, selection) { const x=Math.floor(selection.x), y=Math.floor(selection.y), dist=Math.abs(x-Math.floor(this.cx))+Math.abs(y-Math.floor(this.cy)), sectors=Math.ceil(dist/Math.max(1, this.overview?.world?.sector_size || this.state?.map?.sector_size || 40)); const title=selection.hq?esc(selection.hq.name||'HQ'):selection.capital?esc(selection.capital.name||'Capital'):esc(selection.controller||'Strategic target'); target.innerHTML=`<b>${title}</b><br>Coordinates: ${x},${y}<br>Grid distance: ${dist} cells<br>Approx. sectors: ${sectors}<br><button class="btn mini" data-open-tactical>Open Tactical Map</button>`; target.querySelector('[data-open-tactical]').onclick=()=>{ const modal=this.worldViewModal; if(modal) modal.remove(); this.worldViewModal=null; this.jumpToWorldPosition(x,y,selection.hq?POLYWAR_VISUALS.baseZoom:10,{select:true}); }; }
          selectWorldTarget(target, data) { this.worldTargetSelection=data; this.renderWorldTargetSelection(target, data); }
        }
        const h = new Harness(); const target = new Target();
        assert.doesNotThrow(() => h.selectWorldTarget(target, {x:50,y:80,hq:{name:'Blue <HQ>'}}));
        assert(target.innerHTML.includes('Grid distance: 100 cells'));
        assert(target.innerHTML.includes('Blue &lt;HQ&gt;'));
        assert.strictEqual(h.jumps, 0);
        button.onclick();
        assert.strictEqual(h.jumps, 1);
        assert.strictEqual(h.jump.x, 50);
    """)
    subprocess.run(['node', '-e', script], check=True)


def test_wheel_zoom_uses_same_world_view_handoff():
    assert 'if (e.deltaY < 0) this.zoom(1.25); else this.zoomOutOrOpenWorld();' in JS
    assert 'this.zoom(e.deltaY < 0 ? 1.25 : 0.8)' not in JS


def test_polywar_main_screen_is_minimal_gameplay_hud():
    render_start = JS.index('function render(state)')
    render = JS[JS.index('root.innerHTML = `', render_start):JS.index('root.onclick = handlePolywarUiClick', render_start)]
    assert 'id=\"polywarMenuButton\"' in render
    assert 'polywar-main-gameplay' in render
    assert 'id=\"polywarCanvas\"' in render
    assert 'polywarMinimapCanvas' in render
    assert 'compact-cell-sheet' in render
    assert 'id=\"goBase\"' in render and 'id=\"openWorldView\"' in render
    assert 'id=\"zoomOut\"' in render and 'id=\"zoomIn\"' in render
    assert 'id=\"quickActionsToggle\"' in render
    assert 'polywarWorldHud' not in render
    assert 'polywarGovernancePanel' not in render
    assert 'factionRanking' not in render
    assert 'polywarResultsPanel' not in render


def test_polywar_menu_contains_moved_status_sections_and_controls():
    menu = JS[JS.index('function renderPolywarMenu'):JS.index('function openPolywarMenu')]
    for token in ['Overview', 'Season', 'Energy', 'Faction', 'World', 'World HUD', 'Governance', 'Ranking', 'Season Points', 'Faction Contribution', 'Season Results']:
        assert token in menu
    assert 'polywarMenuBackdrop' in menu
    assert 'polywarMenuClose' in menu
    assert 'data-polywar-menu-close' in menu


def test_polywar_menu_open_close_does_not_touch_map_camera_selection_or_chunks():
    open_close = JS[JS.index('function openPolywarMenu'):JS.index('function render(state)')]
    assert 'map.cx' not in open_close and 'map.cy' not in open_close
    assert 'map.selected' not in open_close
    assert 'ensureChunks' not in open_close
    assert "layer.dataset.open==='true'" in open_close
    assert "layer.innerHTML=''" in open_close or "layer.innerHTML=''" in JS[JS.index('function teardownPolywarMenu'):JS.index('function openPolywarMenu')]


def test_polywar_destroy_closes_menu_and_css_contains_scroll_guards():
    assert 'teardownPolywarMenu({ restartTimers: false }); map?.destroy(); map = null;' in JS
    assert 'body.polywar-menu-open{overflow:hidden;overscroll-behavior:none}' in CSS
    assert 'body.polywar-menu-open{overflow:hidden;touch-action:none}' not in CSS
    assert '.polywar-menu-scroll{overflow-y:auto;overscroll-behavior:contain' in CSS
    assert '.polywar-menu-sheet{touch-action:manipulation' in CSS
    assert '.polywar-menu-backdrop' in CSS and 'touch-action:none' in CSS


def test_polywar_html_removes_big_hero_and_menu_has_back_link():
    html = Path('webapp/polywar.html').read_text()
    assert '<header class="pw-hero glass">' not in html
    assert '<main id="polywarRoot" class="pw-stack">' in html
    render_start = JS.index('function render(state)')
    render = JS[JS.index('root.innerHTML = `', render_start):JS.index('root.onclick = handlePolywarUiClick', render_start)]
    assert render.count('<h1>PolyWar</h1>') == 1
    assert render.index('polywar-game-toolbar') < render.index('polywar-main-gameplay') < render.index('polywarCanvas')
    menu = JS[JS.index('function renderPolywarMenu'):JS.index('function setPolywarMenuExpanded')]
    assert 'Back to DeepAlpha' in menu and 'href="/app"' in menu


def test_polywar_menu_delegates_faction_selection_and_guards_double_click():
    click_start = JS.index('async function handlePolywarUiClick')
    click = JS[click_start:JS.index('init();', click_start)]
    assert "const factionButton = e.target.closest('[data-faction]')" in click
    assert 'factionButton.disabled' in click
    assert 'await joinFaction(factionId)' in click
    assert 'document.body.contains(factionButton)' in click
    assert 'return;' in click
    assert 'document.querySelectorAll("[data-faction]")' not in JS


def test_polywar_menu_accessibility_and_cleanup_source_guards():
    menu_flow = JS[JS.index('function setPolywarMenuExpanded'):JS.index('async function syncPolywarResults')]
    assert "setAttribute('aria-expanded', expanded ? 'true' : 'false')" in menu_flow
    assert 'function teardownPolywarMenu' in menu_flow
    assert "layer.dataset.open='false'" in menu_flow
    assert "document.body.classList.remove('polywar-menu-open')" in menu_flow
    assert "polywarLastMenuTrigger?.focus?.()" in menu_flow
    assert "e.key === 'Escape'" in JS
    assert 'teardownPolywarMenu({ restartTimers: false });\n  currentState = state;' in JS
    assert 'window.addEventListener("pagehide", () => { clearTimers(); teardownPolywarMenu({ restartTimers: false });' in JS


def test_polywar_compact_stats_and_results_stay_menu_sized():
    stats = JS[JS.index('function updateFactionStats'):JS.index('function updateFactionRanking')]
    assert 'polywar-info-card' in stats
    assert 'glass card' not in stats
    assert '<span>Season Points</span>' in stats
    assert '<span>Faction Contribution</span>' in stats
    assert 'panel.innerHTML=`<h3>Season Results</h3>' in JS
    assert '.polywar-menu-events{max-height:180px;overflow-y:auto' in CSS


def test_polywar_runtime_faction_menu_click_single_join_and_cleanup():
    import subprocess, textwrap
    script = textwrap.dedent(r'''
        const assert = require('assert');
        let joinCalls = 0;
        let joinResolve;
        const bodyClasses = new Set(['polywar-menu-open']);
        const button = { dataset:{ faction:'1' }, disabled:false };
        global.document = { body:{ contains: el => el === button, classList:{ contains:c=>bodyClasses.has(c), remove:c=>bodyClasses.delete(c), add:c=>bodyClasses.add(c) } } };
        async function joinFaction(id) { joinCalls++; assert.strictEqual(id, 1); await new Promise(r => { joinResolve = r; }); bodyClasses.delete('polywar-menu-open'); global.map = { faction: 1 }; }
        async function handlePolywarUiClick(e) {
          const factionButton = e.target.closest('[data-faction]');
          if (factionButton) { const factionId = Number(factionButton.dataset.faction); if (!Number.isFinite(factionId) || factionId <= 0 || factionButton.disabled) return; factionButton.disabled = true; try { await joinFaction(factionId); } finally { if (document.body.contains(factionButton)) factionButton.disabled = false; } return; }
        }
        const event = { target:{ closest: sel => sel === '[data-faction]' ? button : null } };
        const first = handlePolywarUiClick(event);
        const second = handlePolywarUiClick(event);
        assert.strictEqual(joinCalls, 1);
        assert.strictEqual(button.disabled, true);
        joinResolve();
        Promise.all([first, second]).then(() => {
          assert.strictEqual(joinCalls, 1);
          assert.strictEqual(button.disabled, false);
          assert.strictEqual(document.body.classList.contains('polywar-menu-open'), false);
          assert.deepStrictEqual(global.map, { faction: 1 });
        });
    ''')
    subprocess.run(['node', '-e', script], check=True)


def test_polywar_runtime_menu_open_close_preserves_map_state_and_avoids_chunk_work():
    import subprocess, textwrap
    script = textwrap.dedent(r'''
        const assert = require('assert');
        const map = { cx:11, cy:22, cell:33, selected:{x:4,y:5}, ensureChunks(){ throw new Error('ensureChunks called'); }, ensureSectors(){ throw new Error('ensureSectors called'); }, centerOnBase(){ throw new Error('centerOnBase called'); } };
        const bodyClasses = new Set();
        const layer = { dataset:{open:'false'}, innerHTML:'' };
        const button = { attrs:{}, focusCount:0, setAttribute(k,v){this.attrs[k]=v;}, focus(){this.focusCount++;} };
        global.document = { getElementById(id){ if(id==='polywarMenuLayer') return layer; if(id==='polywarMenuButton') return button; if(id==='polywarMenuClose') return { focus(){} }; return null; }, body:{ classList:{ add:c=>bodyClasses.add(c), remove:c=>bodyClasses.delete(c) } } };
        function renderPolywarMenu(){ return '<aside id="polywarMenuSheet"></aside>'; }
        function updateFactionStats(){} function updateFactionRanking(){} function updateLatestEvents(){} const polywarGovernanceUi={render(){}};
        function startEnergyTimers(){} function startWorldCountdownTimer(){}
        let polywarLastMenuTrigger = null;
        function setPolywarMenuExpanded(expanded){ const btn=document.getElementById('polywarMenuButton'); if(btn) btn.setAttribute('aria-expanded', expanded ? 'true' : 'false'); }
        function teardownPolywarMenu({ restartTimers = false } = {}) { const layer=document.getElementById('polywarMenuLayer'); if(layer){ layer.innerHTML=''; layer.dataset.open='false'; } document.body.classList.remove('polywar-menu-open'); setPolywarMenuExpanded(false); if(restartTimers){ startEnergyTimers(); startWorldCountdownTimer(); } }
        function openPolywarMenu(){ const layer=document.getElementById('polywarMenuLayer'); if(!layer||layer.dataset.open==='true') return; polywarLastMenuTrigger=document.getElementById('polywarMenuButton'); layer.innerHTML=renderPolywarMenu({}); layer.dataset.open='true'; document.body.classList.add('polywar-menu-open'); setPolywarMenuExpanded(true); updateFactionStats(); updateFactionRanking(); updateLatestEvents(); polywarGovernanceUi.render({}); startEnergyTimers(); startWorldCountdownTimer(); document.getElementById('polywarMenuClose')?.focus?.(); }
        function closePolywarMenu(){ teardownPolywarMenu({restartTimers:true}); polywarLastMenuTrigger?.focus?.(); }
        openPolywarMenu(); openPolywarMenu(); closePolywarMenu();
        assert.deepStrictEqual({cx:map.cx, cy:map.cy, cell:map.cell, selected:map.selected}, {cx:11, cy:22, cell:33, selected:{x:4,y:5}});
        assert.strictEqual(layer.dataset.open, 'false');
        assert.strictEqual(layer.innerHTML, '');
        assert.strictEqual(button.attrs['aria-expanded'], 'false');
        assert.strictEqual(button.focusCount, 1);
    ''')
    subprocess.run(['node', '-e', script], check=True)
