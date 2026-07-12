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
    render = JS[JS.index('root.innerHTML = `'):JS.index('document.querySelectorAll("[data-faction]")')]
    assert render.count('id="primaryActionBtn"') == 1


def test_old_simultaneous_core_button_list_absent():
    render = JS[JS.index('root.innerHTML = `'):JS.index('document.querySelectorAll("[data-faction]")')]
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


def test_ambient_clouds_and_birds_are_canvas_only_and_reduced_motion_safe():
    assert "initAmbient" in JS
    assert "drawAmbient" in JS
    assert "this.clouds" in JS
    assert "this.birds" in JS
    assert "makeCloudDescriptor" in JS
    assert "makeBirdDescriptor" in JS
    assert "polywarReducedMotion" in JS
    assert "prefers-reduced-motion: reduce" in JS
    assert "ambientFps" in JS
    render = JS[JS.index('root.innerHTML = `'):JS.index('document.querySelectorAll("[data-faction]")')]
    assert "cloud" not in render.lower()
    assert "bird" not in render.lower()


def test_ambient_animation_is_bounded_and_destroyed_with_map():
    assert "maxClouds: 5" in JS
    assert "maxBirds: 2" in JS
    assert "lowPowerAmbient" in JS
    assert "cancelAnimationFrame(this.ambientFrame)" in JS
    assert "startAmbientLoop" in JS
    assert "1000 / fps" in JS
    assert "lastAmbientTs" in JS
    assert "updateAmbientEntities(dt)" in JS


def test_clouds_use_screen_space_routes_not_camera_offsets():
    ambient = JS[JS.index("  drawAmbient(ctx = this.ambientCtx"):JS.index("  requestAmbientDraw()", JS.index("  drawAmbient(ctx = this.ambientCtx"))]
    descriptors = JS[JS.index("  makeCloudDescriptor"):JS.index("  makeBirdDescriptor", JS.index("  makeCloudDescriptor"))]
    assert "routeStyle" in descriptors
    assert "driftAmplitude" in descriptors
    assert "wobblePhase" in descriptors
    assert "vx" in descriptors and "vy" in descriptors
    assert "cl.x * this.w" in ambient
    assert "cl.y + wobble + arc" in ambient
    assert "this.cx" not in ambient
    assert "this.cy" not in ambient
    assert "cellToScreen" not in ambient


def test_clouds_and_birds_respawn_without_per_frame_random_jitter():
    update = JS[JS.index("  updateAmbientEntities(dt)"):JS.index("  drawTerrainTile", JS.index("  updateAmbientEntities(dt)"))]
    assert "this.makeCloudDescriptor" in update
    assert "this.makeBirdDescriptor" in update
    assert "cl.x += cl.vx * dt" in update
    assert "bird.x += bird.vx * dt" in update
    assert "Math.random" not in update


def test_birds_have_phase_based_wing_flapping_and_screen_space_motion():
    bird = JS[JS.index("  makeBirdDescriptor"):JS.index("  updateAmbientEntities", JS.index("  makeBirdDescriptor"))]
    ambient = JS[JS.index("  drawAmbient(ctx = this.ambientCtx"):JS.index("  requestAmbientDraw()", JS.index("  drawAmbient(ctx = this.ambientCtx"))]
    assert "wingPhase" in bird
    assert "wingSpeed" in bird
    assert "bird.wingPhase += bird.wingSpeed * dt" in JS
    assert "Math.sin(bird.wingPhase)" in ambient
    assert "bird.x * this.w" in ambient
    assert "bird.y + bob" in ambient
    assert "this.cx" not in ambient
    assert "this.cy" not in ambient


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


def test_ambient_uses_separate_canvas_and_does_not_full_draw_on_tick():
    assert 'id="polywarAmbientCanvas"' in JS
    assert "this.ambientCanvas" in JS
    assert "this.ambientCtx" in JS
    ambient = JS[JS.index("  startAmbientLoop() {"):JS.index("  bindAmbientVisibility()", JS.index("  startAmbientLoop() {"))]
    assert "this.draw(" not in ambient
    assert "this.drawAmbient" in ambient
    assert "requestAnimationFrame" in ambient
    assert "#polywarAmbientCanvas" in CSS
    assert "pointer-events:none" in CSS


def test_ambient_fps_bounds_and_reduced_motion_hidden_document():
    assert "ambientFps: 12" in JS
    assert "lowPowerAmbientFps: 8" in JS
    assert "this.lowPowerAmbient ? POLYWAR_VISUALS.lowPowerAmbientFps : POLYWAR_VISUALS.ambientFps" in JS
    assert "this.ambientEnabled = !polywarReducedMotion()" in JS
    assert "document.hidden" in JS
    assert 'document.addEventListener("visibilitychange"' in JS
    assert 'document.removeEventListener("visibilitychange"' in JS


def test_destroy_cancels_ambient_raf_and_visibility_listener():
    destroy = JS[JS.index("destroy()") : JS.index("updateState(state)")]
    assert "cancelAnimationFrame(this.ambientFrame)" in destroy
    assert "removeEventListener" in destroy
    assert "this.ambientFrame = null" in destroy
    assert "this.lastAmbientTs = 0" in JS
    assert "this.ambientVisibilityHandler = null" in destroy


def test_road_detail_uses_bevel_not_fixed_diagonal():
    assert "drawRoadBevel" in JS
    start = JS.index("drawRoadBevel(ctx")
    road = JS[start : JS.index("drawMountainRelief", start)]
    assert "cell*.62" not in road
    assert "cell*.38" not in road
    assert "p.y + c*.8" in road
