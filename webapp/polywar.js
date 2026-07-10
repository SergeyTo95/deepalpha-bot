const root = document.getElementById("polywarRoot");
const tg = window.Telegram?.WebApp;
let energyTimer = null;
let syncTimer = null;
let currentState = null;
let map = null;
let actionMode = "capture"; // Capture, Scan 3×3, Scan 5×5, Flag mine

try { tg?.ready(); tg?.expand(); } catch (_) {}

const TERRAIN_COST = { plain: 1, forest: 1, mountain: 2, swamp: 2, desert: 1, road: 1, ruins: 1, water: null, river: null };
const TERRAIN_COLOR = { plain: "#76a35b", forest: "#20723d", mountain: "#807a73", swamp: "#476a50", desert: "#c7a35a", road: "#b8935a", ruins: "#8d6e92", water: "#245ea8", river: "#39a7d8" };

function esc(v) { return String(v ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }
async function telegramAuthIfAvailable() { const initData = tg?.initData || ""; if (!initData) return false; const r = await fetch("/api/auth/telegram", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ init_data: initData }) }); return r.ok; }
async function api(path, opts) { const r = await fetch(path, opts); const d = await r.json().catch(() => ({ ok: false, error: "bad_json" })); if (!r.ok) d.httpStatus = r.status; return d; }
function fmtTime(sec) { sec = Math.max(0, Number(sec || 0)); return `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, "0")}s`; }
function factionDot(f) { return `<span class="dot" style="background:${esc(f?.color || "#777")}"></span>`; }
function clearTimers() { if (energyTimer) clearInterval(energyTimer); if (syncTimer) clearInterval(syncTimer); energyTimer = syncTimer = null; }
function baseFor(fid) { return (currentState?.map?.bases || []).find(b => +b.faction_id === +fid); }

function updateEnergyUI() {
  const e = currentState?.energy;
  if (!e) return;
  const countdown = document.getElementById("energyCountdown");
  const value = document.getElementById("energyValue");
  if (countdown) countdown.textContent = fmtTime(e.seconds_until_next_energy);
  if (value) value.textContent = `${e.current_energy}/${e.max_energy}`;
  const lock = document.getElementById("lockStatus");
  if (e.is_locked && e.lock_seconds_remaining > 0) { e.lock_seconds_remaining = Math.max(0, Number(e.lock_seconds_remaining || 0)); }
  if (lock) lock.textContent = e.is_locked ? `Mine locked until ${e.locked_until} · ${fmtTime(e.lock_seconds_remaining)} remaining · energy keeps regenerating` : "Active";
  map?.updateState(currentState);
}

function startEnergyTimers() {
  clearTimers();
  updateEnergyUI();
  energyTimer = setInterval(() => {
    const e = currentState?.energy;
    if (!e) return;
    if (Number(e.current_energy || 0) >= Number(e.max_energy || 0)) e.seconds_until_next_energy = 0;
    else e.seconds_until_next_energy = Math.max(0, Number(e.seconds_until_next_energy || 0) - 1);
    updateEnergyUI();
    if (e.is_locked) e.lock_seconds_remaining = Math.max(0, Number(e.lock_seconds_remaining || 0) - 1);
    if (e.is_locked && e.lock_seconds_remaining === 0) syncState(false, { soft: true });
    if (e.seconds_until_next_energy === 0 && Number(e.current_energy || 0) < Number(e.max_energy || 0)) syncState(false, { soft: true });
  }, 1000);
  syncTimer = setInterval(() => syncState(false, { soft: true }), 60000);
}

class PolyWarMap {
  constructor(state) {
    this.state = state;
    this.canvas = document.getElementById("polywarCanvas");
    this.ctx = this.canvas.getContext("2d");
    this.cache = new Map();
    this.loading = new Set();
    this.abort = new AbortController();
    this.destroyed = false;
    this.drawFrame = null;
    this.loadSeq = 0;
    this.selected = null;
    this.cell = 10;
    this.pending = false;
    const b = baseFor(state.selected_faction?.id) || { x: Math.floor(state.map.width / 2), y: Math.floor(state.map.height / 2) };
    this.cx = b.x;
    this.cy = b.y;
    this.bind();
    this.resize();
    this.select(b.x, b.y);
  }
  bind() {
    const signal = this.abort.signal;
    this.onResize = () => this.resize();
    window.addEventListener("resize", this.onResize, { signal });
    this.canvas.addEventListener("pointerdown", e => { this.canvas.setPointerCapture(e.pointerId); this.drag = { x: e.clientX, y: e.clientY, cx: this.cx, cy: this.cy }; }, { signal });
    this.canvas.addEventListener("pointermove", e => { if (!this.drag) return; this.cx = this.drag.cx - (e.clientX - this.drag.x) / this.cell; this.cy = this.drag.cy - (e.clientY - this.drag.y) / this.cell; this.clamp(); this.ensureChunks(); this.requestDraw(); }, { signal });
    this.canvas.addEventListener("pointerup", e => { if (this.drag && Math.hypot(e.clientX - this.drag.x, e.clientY - this.drag.y) < 5) { const p = this.screenToCell(e.offsetX, e.offsetY); this.select(p.x, p.y); } this.drag = null; }, { signal });
    this.canvas.addEventListener("wheel", e => { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1.25 : 0.8); }, { passive: false, signal });
    document.getElementById("zoomIn").addEventListener("click", () => this.zoom(1.25), { signal });
    document.getElementById("zoomOut").addEventListener("click", () => this.zoom(0.8), { signal });
    document.getElementById("goBase").addEventListener("click", () => { const b = baseFor(currentState?.selected_faction?.id); if (b) { this.cx = b.x; this.cy = b.y; this.clamp(); this.ensureChunks(); this.requestDraw(); } }, { signal });
    document.getElementById("captureBtn").addEventListener("click", () => this.capture(), { signal });
    document.getElementById("scan3Btn")?.addEventListener("click", () => this.scan(3), { signal });
    document.getElementById("scan5Btn")?.addEventListener("click", () => this.scan(5), { signal });
    document.getElementById("flagAddBtn")?.addEventListener("click", () => this.flag(true), { signal });
    document.getElementById("flagRemoveBtn")?.addEventListener("click", () => this.flag(false), { signal });
    document.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => { actionMode = b.dataset.mode; this.updatePanel(); this.requestDraw(); }, { signal }));
  }
  destroy() {
    this.destroyed = true;
    this.abort.abort();
    if (this.drawFrame) cancelAnimationFrame(this.drawFrame);
    this.drawFrame = null;
    this.loading.clear();
  }
  updateState(state) { this.state = state; this.updatePanel(); }
  resize() { if (this.destroyed) return; this.dpr = Math.max(1, window.devicePixelRatio || 1); const r = this.canvas.getBoundingClientRect(); this.canvas.width = Math.floor(r.width * this.dpr); this.canvas.height = Math.floor(r.height * this.dpr); this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); this.w = r.width; this.h = r.height; this.clamp(); this.ensureChunks(); this.requestDraw(); }
  zoom(f) { this.cell = Math.max(2, Math.min(36, this.cell * f)); this.ensureChunks(); this.updatePanel(); this.requestDraw(); }
  clamp() { this.cx = Math.max(0, Math.min(this.state.map.width - 1, this.cx)); this.cy = Math.max(0, Math.min(this.state.map.height - 1, this.cy)); }
  screenToCell(px, py) { return { x: Math.floor(this.cx + (px - this.w / 2) / this.cell), y: Math.floor(this.cy + (py - this.h / 2) / this.cell) }; }
  cellToScreen(x, y) { return { x: this.w / 2 + (x - this.cx) * this.cell, y: this.h / 2 + (y - this.cy) * this.cell }; }
  visibleChunks() {
    const cs = this.state.map.chunk_size, min = this.screenToCell(-this.w * 0.25, -this.h * 0.25), max = this.screenToCell(this.w * 1.25, this.h * 1.25), out = [];
    for (let cy = Math.floor(min.y / cs); cy <= Math.floor(max.y / cs); cy++) for (let cx = Math.floor(min.x / cs); cx <= Math.floor(max.x / cs); cx++) if (cx >= 0 && cy >= 0 && cx * cs < this.state.map.width && cy * cs < this.state.map.height) out.push([cx, cy]);
    return out;
  }
  async ensureChunks(forceKey) {
    if (this.destroyed) return;
    const seq = ++this.loadSeq;
    const visible = this.visibleChunks();
    const missing = visible.filter(([x, y]) => !this.cache.has(`${x},${y}`) && !this.loading.has(`${x},${y}`));
    if (forceKey) { this.cache.delete(forceKey); missing.push(forceKey.split(",").map(Number)); }
    const unique = [...new Map(missing.map(c => [c.join(","), c])).values()];
    if (!unique.length) return;
    unique.forEach(([x, y]) => this.loading.add(`${x},${y}`));
    this.status("Loading chunks…");
    const limit = Math.max(1, Number(this.state.map.max_chunks_per_request || 9));
    for (let i = 0; i < unique.length && !this.destroyed; i += limit) {
      const batch = unique.slice(i, i + limit);
      const d = await api("/api/polywar/map/chunks?chunks=" + batch.map(c => c.join(",")).join(";"));
      batch.forEach(([x, y]) => this.loading.delete(`${x},${y}`));
      if (this.destroyed) return;
      if (d.ok) d.chunks.forEach(ch => this.cache.set(`${ch.chunk_x},${ch.chunk_y}`, ch));
      if (seq !== this.loadSeq) { this.requestDraw(); this.ensureChunks(); return; }
      if (!d.ok) { this.status(d.error || "Chunk error"); continue; }
      this.pruneCache();
      this.requestDraw();
      this.updatePanel();
    }
    if (!this.loading.size) this.status("");
  }
  pruneCache() { const keep = new Set(this.visibleChunks().map(c => c.join(","))); for (const k of this.cache.keys()) if (!keep.has(k) && this.cache.size > 80) this.cache.delete(k); }
  status(t) { const el = document.getElementById("chunkStatus"); if (el) el.textContent = t; }
  select(x, y) { if (x < 0 || y < 0 || x >= this.state.map.width || y >= this.state.map.height) return; this.selected = { x, y }; this.ensureChunks(); this.updatePanel(); this.requestDraw(); }
  getCell(x, y) { const cs = this.state.map.chunk_size, cx = Math.floor(x / cs), cy = Math.floor(y / cs), ch = this.cache.get(`${cx},${cy}`); if (!ch) return {}; const lx = x - cx * cs, ly = y - cy * cs; const intel=(ch.intel||[]).find(i=>+i.x===+x&&+i.y===+y); const flags=(ch.flags||[]).find(f=>+f.x===+x&&+f.y===+y); return { terrain: ch.terrain?.[ly]?.[lx], owner: ch.owners?.[ly]?.[lx], intel, flags }; }
  updatePanel() { const s = this.selected || {}, c = this.getCell(s.x, s.y), fid = currentState?.selected_faction?.id, cost = TERRAIN_COST[c.terrain], locked=!!currentState?.energy?.is_locked; document.getElementById("cellCoords").textContent = s.x == null ? "—" : `${s.x}, ${s.y}`; document.getElementById("cellTerrain").textContent = c.terrain || "loading"; document.getElementById("cellOwner").textContent = c.owner ? `Faction ${c.owner}` : "Neutral"; document.getElementById("cellCost").textContent = cost == null ? "Unavailable" : cost; document.getElementById("cellHint").textContent = c.intel?.intel_type === "safe_hint" ? c.intel.adjacent_mines : "—"; document.getElementById("cellMineIntel").textContent = c.intel?.intel_type === "triggered_mine" ? "Triggered mine" : "—"; document.getElementById("cellFlags").textContent = c.flags ? `${c.flags.flag_count}${c.flags.current_user_flagged ? " (yours)" : ""}` : "0"; document.getElementById("currentMode").textContent = actionMode; const btn = document.getElementById("captureBtn"); const disabled = locked || !fid || !c.terrain || cost == null || c.owner || this.pending || +currentState.energy.current_energy < cost; btn.disabled = disabled; btn.textContent = this.pending ? "Working…" : (!fid ? "Choose faction" : "Capture"); document.getElementById("scan3Btn").disabled = locked || this.pending || +currentState.energy.current_energy < 2; document.getElementById("scan5Btn").disabled = locked || this.pending || +currentState.energy.current_energy < 4; document.getElementById("flagAddBtn").disabled = !fid || !!c.owner || cost == null || this.pending; document.getElementById("flagRemoveBtn").disabled = !c.flags?.current_user_flagged || this.pending; }
  async capture() { if (!this.selected || this.pending) return; this.pending = true; this.updatePanel(); const d = await api("/api/polywar/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_type: "capture", x: this.selected.x, y: this.selected.y, idempotency_key: `cap-${Date.now()}-${Math.random().toString(16).slice(2)}` }) }); this.pending = false; if (!d.ok) { alert(d.error || "Capture failed"); this.updatePanel(); return; } currentState.energy = d.energy; if (d.mine_hit) { this.blast = {x:this.selected.x,y:this.selected.y,t:Date.now()}; alert("Mine hit — actions locked for 3 hours"); } const cs = this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x / cs)},${Math.floor(this.selected.y / cs)}`); updateEnergyUI(); this.updatePanel(); this.requestDraw(); }
  async scan(size) { if (!this.selected || this.pending) return; if (!confirm(`Scan ${size}×${size} around ${this.selected.x},${this.selected.y}?`)) return; this.pending = true; this.updatePanel(); const d = await api("/api/polywar/scan", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({center_x:this.selected.x, center_y:this.selected.y, size, idempotency_key:`scan-${size}-${Date.now()}-${Math.random().toString(16).slice(2)}`})}); this.pending=false; if(!d.ok){ alert(d.error || "Scan failed"); this.updatePanel(); return; } currentState.energy=d.energy; alert(`Active mines detected: ${d.active_mine_count}`); const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x/cs)},${Math.floor(this.selected.y/cs)}`); updateEnergyUI(); this.updatePanel(); this.requestDraw(); }
  async flag(active) { if (!this.selected || this.pending) return; this.pending=true; this.updatePanel(); const d=await api("/api/polywar/flag", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({x:this.selected.x,y:this.selected.y,active})}); this.pending=false; if(!d.ok){ alert(d.error || "Flag failed"); this.updatePanel(); return; } const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x/cs)},${Math.floor(this.selected.y/cs)}`); this.updatePanel(); this.requestDraw(); }
  requestDraw() { if (this.destroyed || this.drawFrame) return; this.drawFrame = requestAnimationFrame(() => { this.drawFrame = null; if (!this.destroyed) this.draw(); }); }
  draw() { const ctx = this.ctx; ctx.clearRect(0, 0, this.w, this.h); const visible = new Set(this.visibleChunks().map(c => c.join(","))); const cs = this.state.map.chunk_size; for (const [key, ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (let yy = 0; yy < ch.height; yy++) for (let xx = 0; xx < ch.width; xx++) { const x = ch.chunk_x * cs + xx, y = ch.chunk_y * cs + yy, p = this.cellToScreen(x, y); if (p.x + this.cell < 0 || p.y + this.cell < 0 || p.x > this.w || p.y > this.h) continue; ctx.fillStyle = TERRAIN_COLOR[ch.terrain[yy][xx]] || "#555"; ctx.fillRect(p.x, p.y, this.cell + 0.5, this.cell + 0.5); const own = ch.owners[yy][xx]; if (own) { ctx.fillStyle = (currentState.factions || []).find(f => f.id === own)?.color || "rgba(255,255,255,.5)"; ctx.globalAlpha = 0.45; ctx.fillRect(p.x, p.y, this.cell, this.cell); ctx.globalAlpha = 1; } if (this.cell > 12) { ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.strokeRect(p.x, p.y, this.cell, this.cell); const intel=(ch.intel||[]).find(i=>+i.x===x&&+i.y===y); const fl=(ch.flags||[]).find(f=>+f.x===x&&+f.y===y); if(intel?.intel_type==="safe_hint"){ ctx.fillStyle="#fff"; ctx.font=`${Math.max(10,this.cell*.65)}px sans-serif`; ctx.fillText(String(intel.adjacent_mines), p.x+3, p.y+this.cell-3); } if(intel?.intel_type==="triggered_mine"){ ctx.fillStyle="#111"; ctx.fillText("✹", p.x+3, p.y+this.cell-3); } if(fl){ ctx.fillStyle="#ffeb3b"; ctx.fillText(`⚑${fl.flag_count}`, p.x+2, p.y+12); } } } } for (const b of this.state.map.bases || []) { const p = this.cellToScreen(b.x, b.y); ctx.fillStyle = b.color || "#fff"; ctx.beginPath(); ctx.arc(p.x + this.cell / 2, p.y + this.cell / 2, Math.max(5, this.cell * 0.9), 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke(); } for (const [key,ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (const sc of ch.scans||[]) { const p=this.cellToScreen(sc.center_x-sc.size/2, sc.center_y-sc.size/2); ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.strokeRect(p.x,p.y,sc.size*this.cell,sc.size*this.cell); const cp=this.cellToScreen(sc.center_x,sc.center_y); ctx.fillStyle="#fff"; ctx.fillText(String(sc.active_mine_count), cp.x+2, cp.y+12); } } if (actionMode.startsWith("scan") && this.selected) { const size=actionMode==="scan5"?5:3, p=this.cellToScreen(this.selected.x-size/2, this.selected.y-size/2); ctx.strokeStyle="#00e5ff"; ctx.setLineDash([4,3]); ctx.strokeRect(p.x,p.y,size*this.cell,size*this.cell); ctx.setLineDash([]); } if (this.blast && Date.now()-this.blast.t<1800) { const p=this.cellToScreen(this.blast.x,this.blast.y); ctx.fillStyle="rgba(255,80,0,.55)"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2, this.cell*2,0,Math.PI*2); ctx.fill(); setTimeout(()=>this.requestDraw(),80); } if (this.selected) { const p = this.cellToScreen(this.selected.x, this.selected.y); ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.strokeRect(p.x, p.y, this.cell, this.cell); ctx.lineWidth = 1; } }
}

function renderUnavailable(message) { clearTimers(); map?.destroy(); map = null; root.innerHTML = `<section class="glass card"><h2>PolyWar is temporarily unavailable</h2><p class="muted">${esc(message || "Please check back later.")}</p><a class="btn" href="/app">Back to DeepAlpha</a></section>`; }
function render(state) {
  currentState = state;
  if (state && state.enabled === false) { renderUnavailable(state.message); return; }
  const p = state.player || {}, e = state.energy || {}, season = state.season || {}, selected = state.selected_faction, needsJoin = !selected;
  map?.destroy();
  root.innerHTML = `<section class="grid"><div class="glass card"><h2>Season</h2><p class="metric">${esc(season.name || "Active Season")}</p><p class="muted">${esc(season.starts_at)} → ${esc(season.ends_at)}</p></div><div class="glass card"><h2>Energy</h2><p class="metric" id="energyValue">${esc(e.current_energy)}/${esc(e.max_energy)}</p><p class="muted">Next charge: <span id="energyCountdown">${fmtTime(e.seconds_until_next_energy)}</span> · ${esc(e.recharge_minutes)} min/energy</p><p class="muted">Status: <b id="lockStatus">${e.is_locked ? "Mine locked" : "Active"}</b></p></div></section><section class="glass card ${selected ? "confirm" : ""}"><h2>Faction</h2>${selected ? `<p class="metric">${factionDot(selected)}${esc(selected.name)}</p><p class="muted">Faction locked for this season.</p>` : `<p class="muted">Choose your faction to capture cells. Preview map is available before selection.</p>`}</section>${needsJoin ? `<section class="glass card"><h2>Choose faction</h2><div class="factions">${(state.factions || []).map(f => `<button class="faction" data-faction="${esc(f.id)}">${factionDot(f)}${esc(f.name)}<small>${esc(f.description)}</small></button>`).join("")}</div></section>` : ""}<section class="glass card map-card"><div class="map-head"><h2>Global War Map</h2><span id="chunkStatus" class="muted"></span><button class="btn mini" id="goBase">Base</button><button class="btn mini" id="zoomOut">−</button><button class="btn mini" id="zoomIn">+</button></div><canvas id="polywarCanvas"></canvas><div class="action-panel"><b>Cell <span id="cellCoords">—</span></b><span>Terrain: <b id="cellTerrain">—</b></span><span>Owner: <b id="cellOwner">—</b></span><span>Cost: <b id="cellCost">—</b></span><span>Hint: <b id="cellHint">—</b></span><span>Mine intel: <b id="cellMineIntel">—</b></span><span>Flags: <b id="cellFlags">0</b></span><span>Mode: <b id="currentMode">capture</b></span><div class="mode-row"><button class="btn mini" data-mode="capture">Capture</button><button class="btn mini" data-mode="scan3">Scan 3×3</button><button class="btn mini" data-mode="scan5">Scan 5×5</button><button class="btn mini" data-mode="flag">Flag mine</button></div><button class="btn" id="captureBtn" disabled>${needsJoin ? "Choose faction" : "Capture"}</button><button class="btn" id="scan3Btn">Scan 3×3</button><button class="btn" id="scan5Btn">Scan 5×5</button><button class="btn" id="flagAddBtn">Add mine flag</button><button class="btn" id="flagRemoveBtn">Remove my flag</button></div></section><section class="grid"><div class="glass card"><h3>Season Points</h3><p class="metric">${esc(p.season_spendable_points || 0)}</p></div><div class="glass card"><h3>Faction Contribution</h3><p class="metric">${esc(p.faction_contribution || 0)}</p></div></section><section class="glass card"><h2>Faction ranking</h2>${(state.faction_ranking || []).map((f, i) => `<div class="rank"><span>${i + 1}. ${factionDot(f)}${esc(f.name)}</span><b>${esc(f.influence_score || 0)}</b></div>`).join("")}</section><section class="glass card"><h2>Latest events</h2>${(state.events || []).length ? (state.events || []).map(ev => `<div class="event"><b>${esc(ev.message)}</b><p class="muted">${esc(ev.created_at || "")}</p></div>`).join("") : '<p class="muted">No events yet.</p>'}</section>`;
  document.querySelectorAll("[data-faction]").forEach(b => b.onclick = () => joinFaction(b.dataset.faction));
  map = new PolyWarMap(state);
  startEnergyTimers();
}

function softUpdate(state) {
  if (!currentState || !state.ok || currentState.selected_faction?.id !== state.selected_faction?.id) { render(state); return; }
  currentState = { ...currentState, ...state };
  updateEnergyUI();
  map?.updateState(currentState);
}

async function syncState(showErrors = true, opts = {}) {
  const state = await api("/api/polywar/state");
  if (state.httpStatus === 401) { clearTimers(); map?.destroy(); map = null; root.innerHTML = '<section class="glass card"><h2>Telegram auth required</h2><p class="muted">Open PolyWar from the Telegram WebApp and try again.</p><a class="btn" href="/app">Back to DeepAlpha</a></section>'; return; }
  if (!state.ok && showErrors) { alert(state.error || "Unable to load PolyWar"); return; }
  opts.soft && map ? softUpdate(state) : render(state);
}
async function joinFaction(id) { const d = await api("/api/polywar/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ faction_id: Number(id) }) }); if (!d.ok) { alert(d.error || "Join failed"); await syncState(false, { soft: true }); return; } render(d); }
async function init() { await telegramAuthIfAvailable(); await syncState(true); }
window.addEventListener("pagehide", () => { clearTimers(); map?.destroy(); map = null; });
init();
