from pathlib import Path
import subprocess
import textwrap

JS = Path("webapp/polywar.js").read_text()
CSS = Path("webapp/polywar.css").read_text()


def method_source(name, next_name):
    start = JS.index(f"  {name}(")
    end = JS.index(f"  {next_name}(", start)
    return JS[start:end].strip()


def test_visual_config_and_layered_paths_are_tunable():
    for token in ("ownershipOpacity", "borderThickness", "contestedPulseMs", "selectionAnimationMs", "lowPowerAnimations", "terrainDetailIntensity", "minimap:"):
        assert token in JS
    for helper in ("drawTerrainTile", "drawOwnershipOverlay", "drawFactionBorders", "drawContestedOverlay", "drawSelectedCell", "drawWorldPolish"):
        assert f"{helper}(" in JS


def test_static_performance_and_chunk_border_regressions():
    owner = method_source("ownerAt", "drawOwnershipOverlay")
    borders = method_source("drawFactionBorders", "drawContestedOverlay")
    contested = method_source("drawContestedOverlay", "drawHoverCell")
    selected = method_source("drawSelectedCell", "drawPendingPulse")
    assert "if (!ch) return null" in owner and "value == null ? null" in owner
    assert "polywarShouldDrawFactionEdge" in borders
    assert 'side === "right" || side === "bottom"' in JS
    assert "visualAnimationTimer" in JS and "clearTimeout(this.visualAnimationTimer)" in JS
    assert "setTimeout" not in contested and "setTimeout" not in selected
    assert JS.count("terrainDetailIntensity") > 1
    assert "polywarLowPowerMode()" in JS
    assert 'isNull=+owner===8' in method_source("drawOwnershipOverlay", "drawFactionBorders")
    assert "factionVisualsById.get" in borders and "factionVisualsById.get" in contested


def test_minimap_mobile_and_visual_only_redraw_guards():
    assert "POLYWAR_VISUALS.minimap.neutral" in JS
    assert "POLYWAR_VISUALS.minimap.contested" in JS
    assert "POLYWAR_VISUALS.minimap.viewport" in JS
    assert "@media (max-width:680px)" in CSS or "@media(max-width:680px)" in CSS
    assert "filter:none" in CSS
    draw = JS[JS.index("  draw() {"):JS.index("\n}", JS.index("  draw() {"))]
    assert "/api/polywar/action" not in draw and "executePrimaryCellAction" not in draw


def test_production_visual_helpers_runtime_contracts():
    hooks_start = JS.index("function polywarShouldDrawFactionEdge")
    hooks_end = JS.index("window.polywarVisualTestHooks", hooks_start)
    hooks = JS[hooks_start:hooks_end]
    owner = method_source("ownerAt", "drawOwnershipOverlay")
    scheduler = method_source("scheduleVisualAnimation", "finishVisualFrame")
    selected = method_source("drawSelectedCell", "drawPendingPulse")
    script = textwrap.dedent(f"""
      const assert=require('assert');
      {hooks}
      const ownerAt=({{{owner}}}).ownerAt;
      const scheduleVisualAnimation=({{{scheduler}}}).scheduleVisualAnimation;
      const drawSelectedCell=({{{selected}}}).drawSelectedCell;

      // Production ownerAt distinguishes unknown chunks from loaded neutral cells.
      const base={{state:{{map:{{width:4,height:4,chunk_size:2}}}},cache:new Map()}};
      assert.strictEqual(ownerAt.call(base,1,1),null);
      base.cache.set('0,0',{{chunk_x:0,chunk_y:0,owners:[[1,0],[0,0]]}});
      assert.strictEqual(ownerAt.call(base,1,0),0);

      // Production edge predicate skips unknown, draws neutral, and emits shared A/B once.
      assert.strictEqual(polywarShouldDrawFactionEdge(1,null,'right'),false);
      assert.strictEqual(polywarShouldDrawFactionEdge(1,0,'top'),true);
      const shared=[polywarShouldDrawFactionEdge(1,2,'right'),polywarShouldDrawFactionEdge(2,1,'left')];
      assert.deepStrictEqual(shared,[true,false]);
      assert.deepStrictEqual(polywarFactionEdgeStyle(1,2,'#f00'),polywarFactionEdgeStyle(2,1,'#00f'));

      // Twenty contested requests still create one production scheduler timer.
      let timers=0;global.document={{hidden:false}};global.polywarReducedMotion=()=>false;global.setTimeout=(fn)=>{{timers++;return 1;}};
      const animated={{destroyed:false,visualLowPower:false,visualAnimationTimer:null,requestDraw(){{}}}};
      for(let i=0;i<20;i++)scheduleVisualAnimation.call(animated,160);
      assert.strictEqual(timers,1);
      const low={{destroyed:false,visualLowPower:true,visualAnimationTimer:null,requestDraw(){{}}}};
      scheduleVisualAnimation.call(low,160);assert.strictEqual(timers,1);

      // Production selection renderer is static after expiry and active only in its window.
      const noop=()=>{{}}, ctx=new Proxy({{}},{{get:(t,k)=>t[k]||(t[k]=noop),set:(t,k,v)=>(t[k]=v,true)}});
      global.performance={{now:()=>5000}};global.POLYWAR_VISUALS={{selectionPulseMs:2200,selectionGlow:10}};
      const map={{selected:{{x:1,y:1}},cell:20,visualLowPower:false,selectionAnimationUntil:4000,visualAnimationNeeded:false,cellToScreen:()=>({{x:0,y:0}})}};
      drawSelectedCell.call(map,ctx);assert.strictEqual(map.visualAnimationNeeded,false);
      map.selectionAnimationUntil=6000;drawSelectedCell.call(map,ctx);assert.strictEqual(map.visualAnimationNeeded,true);
      map.visualAnimationNeeded=false;map.visualLowPower=true;drawSelectedCell.call(map,ctx);assert.strictEqual(map.visualAnimationNeeded,false);
    """)
    subprocess.run(["node", "-e", script], check=True)


def test_null_state_and_low_power_have_distinct_static_paths():
    ownership = method_source("drawOwnershipOverlay", "drawFactionBorders")
    borders = method_source("drawFactionBorders", "drawContestedOverlay")
    assert 'isNull?"#241033"' in ownership
    assert "if(isNull)" in ownership
    assert "!this.visualLowPower" in ownership
    assert "!this.visualLowPower&&this.cell>=10" in borders
