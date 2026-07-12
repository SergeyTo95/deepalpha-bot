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
