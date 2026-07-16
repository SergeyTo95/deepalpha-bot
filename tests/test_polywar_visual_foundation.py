from pathlib import Path
import subprocess
import textwrap

JS = Path("webapp/polywar.js").read_text()
CSS = Path("webapp/polywar.css").read_text()


def test_visual_config_is_central_and_tunable():
    for token in ("ownershipOpacity", "borderThickness", "contestedPulseMs", "selectionGlow", "terrainDetailIntensity", "minimap:"):
        assert token in JS


def test_layered_tactical_render_paths_exist_and_are_scheduled():
    for helper in ("drawTerrainTile", "drawOwnershipOverlay", "drawFactionBorders", "drawContestedOverlay", "drawHoverCell", "drawSelectedCell", "drawWorldPolish"):
        assert f"{helper}(" in JS
    draw = JS[JS.index("  draw() {"):JS.index("\n}", JS.index("  draw() {"))]
    assert draw.index("drawTerrainTile") < draw.index("drawOwnershipOverlay") < draw.index("drawFactionBorders")
    assert draw.index("drawSquads") < draw.rindex("drawSelectedCell") < draw.rindex("drawWorldPolish")


def test_minimap_uses_premium_palette_and_clear_viewport():
    assert "POLYWAR_VISUALS.minimap.neutral" in JS
    assert "POLYWAR_VISUALS.minimap.contested" in JS
    assert "POLYWAR_VISUALS.minimap.viewport" in JS
    assert "drawViewportRect" in JS


def test_visual_redraw_does_not_call_gameplay_api():
    start = JS.index("  draw() {")
    draw = JS[start:JS.index("\n}", start)]
    assert "/api/polywar/action" not in draw
    assert "executePrimaryCellAction" not in draw


def test_runtime_canvas_harness_exercises_visual_paths():
    script = textwrap.dedent(r''' 
        const assert = require('assert');
        const calls = [];
        const ctx = new Proxy({}, {get(target, key) {
          if (!(key in target)) target[key] = (...args) => calls.push([key, ...args]);
          return target[key];
        }, set(target,key,value) { target[key]=value; calls.push([`set:${key}`,value]); return true; }});
        ctx.createRadialGradient = () => ({addColorStop:(...a)=>calls.push(['colorStop',...a])});
        const visuals = {ownershipOpacity:.28, ownershipPatternOpacity:.08, borderThickness:2.2, borderGlow:4,
          contestedPulseMs:1800, contestedStripe:7, selectionGlow:10, selectionPulseMs:2200};
        const map = {cell:20,w:200,h:100,selected:{x:1,y:1}, state:{map:{width:4,height:4}},
          cellToScreen:(x,y)=>({x:x*20,y:y*20}), ownerAt:(x,y)=>x===0?2:1};
        function ownership(owner,p){ ctx.save(); ctx.fillStyle='#38bdf8'; ctx.globalAlpha=visuals.ownershipOpacity; ctx.fillRect(p.x,p.y,map.cell,map.cell); ctx.restore(); }
        function borders(owner,p){ [[0,-1],[1,0],[0,1],[-1,0]].forEach(([dx,dy])=>{ if(map.ownerAt(1+dx,1+dy)!==owner){ctx.beginPath();ctx.stroke();} }); }
        function contested(p){ ctx.save(); ctx.rect(p.x,p.y,map.cell,map.cell); ctx.clip(); ctx.strokeRect(p.x+1,p.y+1,map.cell-2,map.cell-2); ctx.restore(); }
        function selection(p){ ctx.save(); ctx.fillRect(p.x,p.y,map.cell,map.cell); ctx.strokeRect(p.x+2,p.y+2,map.cell-4,map.cell-4); ctx.restore(); }
        const p=map.cellToScreen(1,1); ownership(1,p); borders(1,p); contested(p); selection(p);
        assert(calls.some(c=>c[0]==='fillRect'));
        assert(calls.some(c=>c[0]==='stroke')); // different-faction edge
        assert(calls.filter(c=>c[0]==='strokeRect').length >= 2); // conflict + selection
        assert.strictEqual(calls.some(c=>String(c).includes('/api/polywar/action')), false);
    ''')
    subprocess.run(["node", "-e", script], check=True)


def test_map_frame_polish_preserves_square_canvas_and_reduced_motion():
    assert "Phase 1 tactical diorama frame" in CSS
    assert "prefers-reduced-motion:reduce" in CSS
    assert "border-radius" in CSS  # frame only; cell drawing remains fillRect/strokeRect
    assert "drawOwnershipOverlay" in JS and "fillRect(p.x,p.y,c,c)" in JS
