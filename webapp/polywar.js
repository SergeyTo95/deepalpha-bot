const root = document.getElementById("polywarRoot");
const tg = window.Telegram?.WebApp;
let energyTimer = null;
let syncTimer = null;
let currentState = null;
let map = null;
let actionMode = "capture"; // Capture, Attack, Reinforce, Scan 3×3, Scan 5×5, Flag mine

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
    this.sectorCache = new Map();
    this.sectorLoading = new Set();
    this.sectorSeq = 0;
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
    this.refreshCapitals();
    polywarGovernanceUi?.refresh?.();
  }
  bind() {
    const signal = this.abort.signal;
    this.onResize = () => this.resize();
    window.addEventListener("resize", this.onResize, { signal });
    this.canvas.addEventListener("pointerdown", e => { this.canvas.setPointerCapture(e.pointerId); this.drag = { x: e.clientX, y: e.clientY, cx: this.cx, cy: this.cy }; }, { signal });
    this.canvas.addEventListener("pointermove", e => { if (!this.drag) return; this.cx = this.drag.cx - (e.clientX - this.drag.x) / this.cell; this.cy = this.drag.cy - (e.clientY - this.drag.y) / this.cell; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); }, { signal });
    this.canvas.addEventListener("pointerup", e => { if (this.drag && Math.hypot(e.clientX - this.drag.x, e.clientY - this.drag.y) < 5) { const p = this.screenToCell(e.offsetX, e.offsetY); this.select(p.x, p.y); } this.drag = null; }, { signal });
    this.canvas.addEventListener("wheel", e => { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1.25 : 0.8); }, { passive: false, signal });
    document.getElementById("zoomIn").addEventListener("click", () => this.zoom(1.25), { signal });
    document.getElementById("zoomOut").addEventListener("click", () => this.zoom(0.8), { signal });
    document.getElementById("goBase").addEventListener("click", () => { const b = baseFor(currentState?.selected_faction?.id); if (b) { this.cx = b.x; this.cy = b.y; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); } }, { signal });
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
    this.sectorLoading.clear();
  }
  updateState(state) { this.state = state; this.updatePanel(); }
  resize() { if (this.destroyed) return; this.dpr = Math.max(1, window.devicePixelRatio || 1); const r = this.canvas.getBoundingClientRect(); this.canvas.width = Math.floor(r.width * this.dpr); this.canvas.height = Math.floor(r.height * this.dpr); this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); this.w = r.width; this.h = r.height; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); }
  zoom(f) { this.cell = Math.max(2, Math.min(36, this.cell * f)); this.ensureChunks(); this.ensureSectors(); this.updatePanel(); this.requestDraw(); }
  clamp() { this.cx = Math.max(0, Math.min(this.state.map.width - 1, this.cx)); this.cy = Math.max(0, Math.min(this.state.map.height - 1, this.cy)); }
  screenToCell(px, py) { return { x: Math.floor(this.cx + (px - this.w / 2) / this.cell), y: Math.floor(this.cy + (py - this.h / 2) / this.cell) }; }
  cellToScreen(x, y) { return { x: this.w / 2 + (x - this.cx) * this.cell, y: this.h / 2 + (y - this.cy) * this.cell }; }
  rules() { return this.state.rules || {}; }
  combatRules() { return this.rules().combat || {}; }
  sectorRules() { return this.rules().sectors || {}; }
  sectorSize() { return Number(this.sectorRules().sector_size || 100); }
  sectorColumns() { return Math.max(1, Math.ceil(Number(this.state.map.width || 1) / this.sectorSize())); }
  sectorRows() { return Math.max(1, Math.ceil(Number(this.state.map.height || 1) / this.sectorSize())); }
  visibleSectorRange() { const ss=this.sectorSize(), min=this.screenToCell(0,0), max=this.screenToCell(this.w,this.h), cols=this.sectorColumns(), rows=this.sectorRows(); return {minX:Math.max(0,Math.min(cols-1,Math.floor(min.x/ss))), maxX:Math.max(0,Math.min(cols-1,Math.floor(max.x/ss))), minY:Math.max(0,Math.min(rows-1,Math.floor(min.y/ss))), maxY:Math.max(0,Math.min(rows-1,Math.floor(max.y/ss)))}; }
  sectorTiles(r, max) { const tiles=[]; max=Math.max(1, Number(max||100)); const width=r.maxX-r.minX+1; const tileW=Math.max(1, Math.min(width, max)); const tileH=Math.max(1, Math.floor(max/tileW)); for(let y=r.minY;y<=r.maxY;y+=tileH) for(let x=r.minX;x<=r.maxX;x+=tileW) tiles.push({minX:x,maxX:Math.min(r.maxX,x+tileW-1),minY:y,maxY:Math.min(r.maxY,y+tileH-1)}); return tiles; }
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

  async ensureSectors(forceKey) {
    if (this.destroyed) return;
    const seq = ++this.sectorSeq, r = this.visibleSectorRange(), max = Number(this.sectorRules().max_sectors_per_request || 100);
    if (forceKey) this.sectorCache.delete(forceKey);
    const tiles = this.sectorTiles(r, max);
    for (const tile of tiles) {
      if (this.destroyed) return;
      const wanted=[];
      for(let sy=tile.minY; sy<=tile.maxY; sy++) for(let sx=tile.minX; sx<=tile.maxX; sx++) wanted.push([sx,sy]);
      const missing=wanted.filter(([sx,sy])=>!this.sectorCache.has(`${sx},${sy}`)&&!this.sectorLoading.has(`${sx},${sy}`));
      if(!missing.length) continue;
      missing.forEach(([sx,sy])=>this.sectorLoading.add(`${sx},${sy}`));
      let d;
      try {
        d = await api(`/api/polywar/map/sectors?min_sector_x=${tile.minX}&max_sector_x=${tile.maxX}&min_sector_y=${tile.minY}&max_sector_y=${tile.maxY}`);
      } finally {
        missing.forEach(([sx,sy])=>this.sectorLoading.delete(`${sx},${sy}`));
      }
      if (this.destroyed) return;
      if (seq !== this.sectorSeq) { this.requestDraw(); this.ensureSectors(); return; }
      if (d?.ok) {
        const stamp=Number(d.server_timestamp || Date.now());
        (d.sectors||[]).forEach(sec=>{ const key=`${sec.sector_x},${sec.sector_y}`, old=this.sectorCache.get(key), oldStamp=Number(old?._loadedAt||0); if(stamp>=oldStamp) this.sectorCache.set(key,{...sec,_loadedAt:stamp}); });
      }
      this.pruneSectorCache(); this.requestDraw();
    }
    this.requestDraw();
  }
  pruneSectorCache() { const r=this.visibleSectorRange(); for (const k of this.sectorCache.keys()) { const [sx,sy]=k.split(',').map(Number); if ((sx<r.minX-1||sx>r.maxX+1||sy<r.minY-1||sy>r.maxY+1) && this.sectorCache.size>200) this.sectorCache.delete(k); } }
  refreshSelectedSector() { if (!this.selected) return; const ss=this.sectorSize(), key=`${Math.floor(this.selected.x/ss)},${Math.floor(this.selected.y/ss)}`; return this.ensureSectors(key); }
  pruneCache() { const keep = new Set(this.visibleChunks().map(c => c.join(","))); for (const k of this.cache.keys()) if (!keep.has(k) && this.cache.size > 80) this.cache.delete(k); }
  status(t) { const el = document.getElementById("chunkStatus"); if (el) el.textContent = t; }
  select(x, y) { if (x < 0 || y < 0 || x >= this.state.map.width || y >= this.state.map.height) return; this.selected = { x, y }; this.ensureChunks(); this.updatePanel(); this.requestDraw(); }
  getCell(x, y) { const cs = this.state.map.chunk_size, cx = Math.floor(x / cs), cy = Math.floor(y / cs), ch = this.cache.get(`${cx},${cy}`); if (!ch) return {}; const lx = x - cx * cs, ly = y - cy * cs; const intel=(ch.intel||[]).find(i=>+i.x===+x&&+i.y===+y); const flags=(ch.flags||[]).find(f=>+f.x===+x&&+f.y===+y); const contest=(ch.contested_cells||[]).find(q=>+q.x===+x&&+q.y===+y); const capital=(ch.capitals||[]).find(q=>+q.x===+x&&+q.y===+y); const orders=(ch.orders||[]).filter(o=>+o.x===+x&&+o.y===+y); return { terrain: ch.terrain?.[ly]?.[lx], owner: ch.owners?.[ly]?.[lx], intel, flags, contest, capital, orders }; }
  isFrontline(x, y, fid) { return [[1,0],[-1,0],[0,1],[0,-1]].some(([dx,dy]) => +this.getCell(x+dx,y+dy).owner === +fid); }
  updatePanel() {
    const s = this.selected || {}, c = this.getCell(s.x, s.y), fid = currentState?.selected_faction?.id, base = TERRAIN_COST[c.terrain], locked=!!currentState?.energy?.is_locked;
    const cr=this.combatRules(), attackCost = base == null ? null : base + Number(cr.enemy_attack_extra_energy || 1), reinforceCost = Number(cr.reinforce_energy_cost || 1);
    document.getElementById("cellCoords").textContent = s.x == null ? "—" : `${s.x}, ${s.y}`;
    document.getElementById("cellTerrain").textContent = c.terrain || "loading";
    document.getElementById("cellOwner").textContent = c.owner ? `Faction ${c.owner}` : "Neutral";
    document.getElementById("cellCost").textContent = base == null ? "Unavailable" : (c.owner && +c.owner !== +fid ? `Attack ${attackCost}` : c.contest && +c.owner === +fid ? `Reinforce ${reinforceCost}` : base);
    if (document.getElementById("cellSector")) document.getElementById("cellSector").textContent = s.x == null ? "—" : `${Math.floor(s.x/this.sectorSize())},${Math.floor(s.y/this.sectorSize())}`;
    document.getElementById("cellHint").textContent = c.intel?.intel_type === "safe_hint" ? c.intel.adjacent_mines : "—";
    document.getElementById("cellMineIntel").textContent = c.contest ? `Contested by Faction ${c.contest.contesting_faction_id}: ${c.contest.contest_progress}/${c.contest.contest_required}` : (c.intel?.intel_type === "triggered_mine" ? "Triggered mine" : "—");
    document.getElementById("cellFlags").textContent = c.flags ? `${c.flags.flag_count}${c.flags.current_user_flagged ? " (yours)" : ""}` : "0";
    const capPanel = document.getElementById("capitalPanel");
    if (capPanel) capPanel.innerHTML = c.capital ? polywarCapitalUi.panel(c.capital, currentState) : "";
    const capRules = this.rules().capitals || {};
    const isCapital = !!c.capital;
    const siegeCost = base == null ? null : base + Number(capRules.siege_extra_energy || 0);
    const repairCost = Number(capRules.repair_energy_cost || 0);
    const canCapture = !isCapital && !locked && fid && c.terrain && base != null && !c.owner && !this.pending && +currentState.energy.current_energy >= base;
    const canAttack = !isCapital && !locked && fid && c.terrain && base != null && c.owner && +c.owner !== +fid && this.isFrontline(s.x,s.y,fid) && !this.pending && +currentState.energy.current_energy >= attackCost;
    const canReinforce = !isCapital && !locked && fid && c.terrain && base != null && +c.owner === +fid && c.contest && +c.contest.contest_progress > 0 && !this.pending && +currentState.energy.current_energy >= reinforceCost;
    const canSiege = isCapital && !locked && fid && +c.capital.controller_faction_id !== +fid && !this.pending && +currentState.energy.current_energy >= siegeCost;
    const canRepair = isCapital && !locked && fid && +c.capital.controller_faction_id === +fid && +c.capital.siege_progress > 0 && !this.pending && +currentState.energy.current_energy >= repairCost;
    const combatModes = new Set(["capture", "attack", "reinforce", "siege", "repair_capital"]);
    if (combatModes.has(actionMode)) { if (canSiege) actionMode = "siege"; else if (canRepair) actionMode = "repair_capital"; else if (canAttack) actionMode = "attack"; else if (canReinforce) actionMode = "reinforce"; else if (canCapture) actionMode = "capture"; }
    document.getElementById("currentMode").textContent = actionMode;
    const btn = document.getElementById("captureBtn");
    const canMain = actionMode === "attack" ? canAttack : actionMode === "reinforce" ? canReinforce : actionMode === "siege" ? canSiege : actionMode === "repair_capital" ? canRepair : canCapture;
    btn.disabled = !canMain; btn.textContent = this.pending ? "Working…" : (!fid ? "Choose faction" : actionMode === "attack" ? `Attack — ${attackCost} energy` : actionMode === "reinforce" ? `Reinforce — ${reinforceCost} energy` : actionMode === "siege" ? `Siege capital — ${siegeCost} energy` : actionMode === "repair_capital" ? `Repair capital — ${repairCost} energy` : "Capture");
    document.getElementById("scan3Btn").disabled = locked || this.pending || +currentState.energy.current_energy < 2;
    document.getElementById("scan5Btn").disabled = locked || this.pending || +currentState.energy.current_energy < 4;
    document.getElementById("flagAddBtn").disabled = !fid || !!c.owner || base == null || this.pending;
    document.getElementById("flagRemoveBtn").disabled = !c.flags?.current_user_flagged || this.pending;
  }
  async refreshCapitals() { const d = await polywarCapitalUi.refresh(); this.requestDraw(); this.updatePanel(); return d; }
  async refreshGovernance() { const d = await polywarGovernanceUi.refresh(); this.requestDraw(); return d; }
  async capture() { if (!this.selected || this.pending) return; this.pending = true; this.updatePanel(); const d = await api("/api/polywar/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_type: (["attack","reinforce","siege","repair_capital"].includes(actionMode)) ? actionMode : "capture", x: this.selected.x, y: this.selected.y, idempotency_key: `cap-${Date.now()}-${Math.random().toString(16).slice(2)}` }) }); this.pending = false; if (!d.ok) { alert(d.error || "Action failed"); this.updatePanel(); return; } currentState.energy = d.energy; if (d.mine_hit) { this.blast = {x:this.selected.x,y:this.selected.y,t:Date.now()}; alert(`Mine hit — actions locked until ${d.locked_until || d.energy?.locked_until || "server unlock"} (${fmtTime(d.energy?.lock_seconds_remaining || 0)} remaining)`); } const cs = this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x / cs)},${Math.floor(this.selected.y / cs)}`); await this.refreshCapitals(); await this.refreshSelectedSector(); await syncState(false, { soft: true }); updateEnergyUI(); if (d.outcome) alert(d.outcome); this.updatePanel(); this.requestDraw(); }
  async scan(size) { if (!this.selected || this.pending) return; if (!confirm(`Scan ${size}×${size} around ${this.selected.x},${this.selected.y}?`)) return; this.pending = true; this.updatePanel(); const d = await api("/api/polywar/scan", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({center_x:this.selected.x, center_y:this.selected.y, size, idempotency_key:`scan-${size}-${Date.now()}-${Math.random().toString(16).slice(2)}`})}); this.pending=false; if(!d.ok){ alert(d.error || "Scan failed"); this.updatePanel(); return; } currentState.energy=d.energy; alert(`Active mines detected: ${d.active_mine_count}`); const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x/cs)},${Math.floor(this.selected.y/cs)}`); updateEnergyUI(); this.updatePanel(); this.requestDraw(); }
  async flag(active) { if (!this.selected || this.pending) return; this.pending=true; this.updatePanel(); const d=await api("/api/polywar/flag", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({x:this.selected.x,y:this.selected.y,active})}); this.pending=false; if(!d.ok){ alert(d.error || "Flag failed"); this.updatePanel(); return; } const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(this.selected.x/cs)},${Math.floor(this.selected.y/cs)}`); this.updatePanel(); this.requestDraw(); }
  requestDraw() { if (this.destroyed || this.drawFrame) return; this.drawFrame = requestAnimationFrame(() => { this.drawFrame = null; if (!this.destroyed) this.draw(); }); }
  draw() { const ctx = this.ctx; ctx.clearRect(0, 0, this.w, this.h); const visible = new Set(this.visibleChunks().map(c => c.join(","))); const cs = this.state.map.chunk_size; for (const [key, ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (let yy = 0; yy < ch.height; yy++) for (let xx = 0; xx < ch.width; xx++) { const x = ch.chunk_x * cs + xx, y = ch.chunk_y * cs + yy, p = this.cellToScreen(x, y); if (p.x + this.cell < 0 || p.y + this.cell < 0 || p.x > this.w || p.y > this.h) continue; ctx.fillStyle = TERRAIN_COLOR[ch.terrain[yy][xx]] || "#555"; ctx.fillRect(p.x, p.y, this.cell + 0.5, this.cell + 0.5); const own = ch.owners[yy][xx]; if (own) { ctx.fillStyle = (currentState.factions || []).find(f => f.id === own)?.color || "rgba(255,255,255,.5)"; ctx.globalAlpha = 0.45; ctx.fillRect(p.x, p.y, this.cell, this.cell); ctx.globalAlpha = 1; } const contest=(ch.contested_cells||[]).find(q=>+q.x===x&&+q.y===y); if(contest){ ctx.strokeStyle="#fff200"; ctx.lineWidth=2; ctx.strokeRect(p.x+1,p.y+1,this.cell-2,this.cell-2); ctx.fillStyle=(currentState.factions||[]).find(f=>+f.id===+contest.contesting_faction_id)?.color||"#fff"; ctx.fillRect(p.x+2,p.y+this.cell-5,Math.max(2,(this.cell-4)*(contest.contest_progress/contest.contest_required)),3); ctx.fillText("⚔",p.x+2,p.y+12); ctx.lineWidth=1; } if (this.cell > 12) { ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.strokeRect(p.x, p.y, this.cell, this.cell); const intel=(ch.intel||[]).find(i=>+i.x===x&&+i.y===y); const fl=(ch.flags||[]).find(f=>+f.x===x&&+f.y===y); if(intel?.intel_type==="safe_hint"){ ctx.fillStyle="#fff"; ctx.font=`${Math.max(10,this.cell*.65)}px sans-serif`; ctx.fillText(String(intel.adjacent_mines), p.x+3, p.y+this.cell-3); } if(intel?.intel_type==="triggered_mine"){ ctx.fillStyle="#111"; ctx.fillText("✹", p.x+3, p.y+this.cell-3); } if(fl){ ctx.fillStyle="#ffeb3b"; ctx.fillText(`⚑${fl.flag_count}`, p.x+2, p.y+12); } } } } if (this.cell < 8) { const ss=this.sectorSize(), r=this.visibleSectorRange(); for(let sy=r.minY; sy<=r.maxY; sy++) for(let sx=r.minX; sx<=r.maxX; sx++){ const sec=this.sectorCache.get(`${sx},${sy}`), p=this.cellToScreen(sx*ss, sy*ss), size=ss*this.cell; if(sec?.controller_faction_id){ ctx.fillStyle=(currentState.factions||[]).find(f=>+f.id===+sec.controller_faction_id)?.color||"#fff"; ctx.globalAlpha=.16; ctx.fillRect(p.x,p.y,size,size); ctx.globalAlpha=1; } if(sec?.is_contested){ ctx.fillStyle="rgba(255,255,255,.16)"; for(let k=0;k<size;k+=8){ ctx.fillRect(p.x+k,p.y,3,size); } } ctx.strokeStyle="rgba(255,255,255,.25)"; ctx.strokeRect(p.x,p.y,size,size); if(this.cell>3){ ctx.fillStyle="#fff"; ctx.font="11px sans-serif"; ctx.fillText(`${sx},${sy} ${sec?.dominance_percent??0}%`,p.x+4,p.y+14); } } } for (const b of this.state.map.bases || []) { const p = this.cellToScreen(b.x, b.y); ctx.fillStyle = b.color || "#fff"; ctx.beginPath(); ctx.arc(p.x + this.cell / 2, p.y + this.cell / 2, Math.max(5, this.cell * 0.9), 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.stroke(); } for (const [key,ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (const sc of ch.scans||[]) { const p=this.cellToScreen(sc.center_x-sc.size/2, sc.center_y-sc.size/2); ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.strokeRect(p.x,p.y,sc.size*this.cell,sc.size*this.cell); const cp=this.cellToScreen(sc.center_x,sc.center_y); ctx.fillStyle="#fff"; ctx.fillText(String(sc.active_mine_count), cp.x+2, cp.y+12); } } if (actionMode.startsWith("scan") && this.selected) { const size=actionMode==="scan5"?5:3, p=this.cellToScreen(this.selected.x-size/2, this.selected.y-size/2); ctx.strokeStyle="#00e5ff"; ctx.setLineDash([4,3]); ctx.strokeRect(p.x,p.y,size*this.cell,size*this.cell); ctx.setLineDash([]); } if (this.blast && Date.now()-this.blast.t<1800) { const p=this.cellToScreen(this.blast.x,this.blast.y); ctx.fillStyle="rgba(255,80,0,.55)"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2, this.cell*2,0,Math.PI*2); ctx.fill(); setTimeout(()=>this.requestDraw(),80); } polywarCapitalUi.draw(ctx, (x,y)=>this.cellToScreen(x,y), currentState.factions || []); polywarGovernanceUi.drawOrders(ctx, (x,y)=>this.cellToScreen(x,y)); if (this.selected) { const p = this.cellToScreen(this.selected.x, this.selected.y); ctx.strokeStyle = "#fff"; ctx.lineWidth = 3; ctx.strokeRect(p.x, p.y, this.cell, this.cell); ctx.lineWidth = 1; } }
}

function renderUnavailable(message) { clearTimers(); map?.destroy(); map = null; root.innerHTML = `<section class="glass card"><h2>PolyWar is temporarily unavailable</h2><p class="muted">${esc(message || "Please check back later.")}</p><a class="btn" href="/app">Back to DeepAlpha</a></section>`; }
function render(state) {
  currentState = state;
  if (state && state.enabled === false) { renderUnavailable(state.message); return; }
  const p = state.player || {}, e = state.energy || {}, season = state.season || {}, selected = state.selected_faction, needsJoin = !selected;
  map?.destroy();
  root.innerHTML = `<section class="grid"><div class="glass card"><h2>Season</h2><p class="metric">${esc(season.name || "Active Season")}</p><p class="muted">${esc(season.starts_at)} → ${esc(season.ends_at)}</p></div><div class="glass card"><h2>Energy</h2><p class="metric" id="energyValue">${esc(e.current_energy)}/${esc(e.max_energy)}</p><p class="muted">Next charge: <span id="energyCountdown">${fmtTime(e.seconds_until_next_energy)}</span> · ${esc(e.recharge_minutes)} min/energy</p><p class="muted">Status: <b id="lockStatus">${e.is_locked ? "Mine locked" : "Active"}</b></p></div></section><section class="glass card ${selected ? "confirm" : ""}"><h2>Faction</h2>${selected ? `<p class="metric">${factionDot(selected)}${esc(selected.name)}</p><p class="muted">Faction locked for this season.</p>` : `<p class="muted">Choose your faction to capture cells. Preview map is available before selection.</p>`}</section>${needsJoin ? `<section class="glass card"><h2>Choose faction</h2><div class="factions">${(state.factions || []).map(f => `<button class="faction" data-faction="${esc(f.id)}">${factionDot(f)}${esc(f.name)}<small>${esc(f.description)}</small></button>`).join("")}</div></section>` : ""}<section class="glass card map-card"><div class="map-head"><h2>Global War Map</h2><span id="chunkStatus" class="muted"></span><button class="btn mini" id="goBase">Base</button><button class="btn mini" id="zoomOut">−</button><button class="btn mini" id="zoomIn">+</button></div><canvas id="polywarCanvas"></canvas><div class="action-panel"><b>Cell <span id="cellCoords">—</span></b><span>Terrain: <b id="cellTerrain">—</b></span><span>Owner: <b id="cellOwner">—</b></span><span>Cost: <b id="cellCost">—</b></span><span>Sector: <b id="cellSector">—</b></span><span>Hint: <b id="cellHint">—</b></span><span>Mine intel: <b id="cellMineIntel">—</b></span><span>Flags: <b id="cellFlags">0</b></span><span>Mode: <b id="currentMode">capture</b></span><div class="mode-row"><button class="btn mini" data-mode="capture">Capture</button><button class="btn mini" data-mode="attack">Attack</button><button class="btn mini" data-mode="reinforce">Reinforce</button><button class="btn mini" data-mode="scan3">Scan 3×3</button><button class="btn mini" data-mode="scan5">Scan 5×5</button><button class="btn mini" data-mode="flag">Flag mine</button><button class="btn mini" data-mode="siege">Siege</button><button class="btn mini" data-mode="repair_capital">Repair capital</button></div><button class="btn" id="captureBtn" disabled>${needsJoin ? "Choose faction" : "Capture"}</button><button class="btn" id="scan3Btn">Scan 3×3</button><button class="btn" id="scan5Btn">Scan 5×5</button><button class="btn" id="flagAddBtn">Add mine flag</button><button class="btn" id="flagRemoveBtn">Remove my flag</button><div id="capitalPanel"></div></div></section><section class="glass card polywar-governance-panel" id="polywarGovernancePanel" data-polywar-governance><h2>Governance</h2></section><section class="grid" id="factionStats"><div class="glass card"><h3>Season Points</h3><p class="metric">${esc(p.season_spendable_points || 0)}</p></div><div class="glass card"><h3>Faction Contribution</h3><p class="metric">${esc(p.faction_contribution || 0)}</p></div></section><section class="glass card"><h2>Faction ranking</h2><div id="factionRanking"></div></section><section class="glass card"><h2>Latest events</h2><div id="latestEvents"></div></section>`;
  document.querySelectorAll("[data-faction]").forEach(b => b.onclick = () => joinFaction(b.dataset.faction));
  root.onclick = handlePolywarUiClick;
  updateFactionStats();
  updateFactionRanking();
  updateLatestEvents();
  map = new PolyWarMap(state);
  startEnergyTimers();
}

function updateFactionStats() {
  const p=currentState?.player||{}, el=document.getElementById("factionStats");
  if (el) el.innerHTML = `<div class="glass card"><h3>Season Points</h3><p class="metric">${esc(p.season_spendable_points || 0)}</p></div><div class="glass card"><h3>Faction Contribution</h3><p class="metric">${esc(p.faction_contribution || 0)}</p></div>`;
}
function updateFactionRanking() {
  const el=document.getElementById("factionRanking"); if(!el) return;
  el.innerHTML=(currentState?.faction_ranking||[]).map((f,i)=>`<div class="rank"><span>${i+1}. ${factionDot(f)}${esc(f.name)} <small class="muted">cells ${esc(f.controlled_cells_count||0)} · sectors ${esc(f.controlled_sectors_count||0)}</small></span><b>${esc(f.influence_score||0)}</b></div>`).join("");
}
function updateLatestEvents() {
  const el=document.getElementById("latestEvents"); if(!el) return; const events=currentState?.events||[];
  el.innerHTML=events.length ? events.map(ev=>`<div class="event"><b>${esc(ev.message)}</b><p class="muted">${esc(ev.created_at||"")}</p></div>`).join("") : '<p class="muted">No events yet.</p>';
}

function softUpdate(state) {
  if (!currentState || !state.ok || currentState.selected_faction?.id !== state.selected_faction?.id) { render(state); return; }
  currentState = { ...currentState, ...state };
  updateEnergyUI();
  updateFactionStats();
  updateFactionRanking();
  updateLatestEvents();
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

// Phase 5 PolyWar capitals/governance integration hooks.
// The existing dirty Canvas renderer consumes chunk.capitals and faction-scoped chunk.orders.
window.polywarPhase5 = Object.assign(window.polywarPhase5 || {}, {
  endpoints: {
    capitals: '/api/polywar/capitals',
    governance: '/api/polywar/governance',
    nominate: '/api/polywar/governance/nominate',
    vote: '/api/polywar/governance/vote',
    orders: '/api/polywar/orders'
  },
  actionTypes: ['siege', 'repair_capital'],
  rulesFromServer(state) { return { capitals: state?.rules?.capitals || {}, governance: state?.rules?.governance || {} }; },
  capitalLabel(capital) { return `Capital ${capital?.x},${capital?.y}`; },
  hasCapitalCanvasUi: true,
  hasGovernanceUi: true,
  softSyncKeepsCanvas: true
});

// Phase 5 concrete UI helpers: capital cache, capital/governance panels, and order marker soft-sync.
const polywarCapitalUi = window.polywarCapitalUi = window.polywarCapitalUi || {
  cache: new Map(),
  max: 128,
  async refresh() {
    const data = await api('/api/polywar/capitals');
    if (data.ok) {
      this.cache.clear();
      (data.capitals || []).slice(0, this.max).forEach(c => this.cache.set(`${c.x},${c.y}`, c));
      map?.requestDraw?.();
    }
    return data;
  },
  draw(ctx, worldToScreen, factions = []) {
    const byId = new Map(factions.map(f => [f.id, f]));
    for (const cap of this.cache.values()) {
      const p = worldToScreen ? worldToScreen(cap.x, cap.y) : { x: cap.x, y: cap.y };
      const original = byId.get(cap.original_faction_id)?.color || '#ffffff';
      const controller = byId.get(cap.controller_faction_id)?.color || original;
      ctx.save();
      ctx.fillStyle = controller;
      ctx.strokeStyle = original;
      ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(p.x, p.y, 8, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      if (cap.original_faction_id !== cap.controller_faction_id) { ctx.fillStyle = '#ffd166'; ctx.fillRect(p.x - 3, p.y - 14, 6, 4); }
      if (cap.is_under_siege) {
        ctx.strokeStyle = byId.get(cap.besieging_faction_id)?.color || '#ff006e';
        ctx.beginPath(); ctx.arc(p.x, p.y, 12, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, cap.siege_progress / cap.siege_required)); ctx.stroke();
      }
      ctx.restore();
    }
  },
  panel(cap, state) {
    const rules = state?.rules?.capitals || {};
    return `<section class="glass card polywar-capital-panel"><h3>Capital</h3><p>Original faction: ${esc(cap.original_faction_id)}</p><p>Controller: ${esc(cap.controller_faction_id)}</p><p>Besieging faction: ${esc(cap.besieging_faction_id || 'none')}</p><p>Siege progress: ${esc(cap.siege_progress)}/${esc(cap.siege_required)} (${cap.siege_percent || 0}%)</p><p>Siege cost: terrain + ${rules.siege_extra_energy ?? 0}</p><p>Repair cost: ${rules.repair_energy_cost ?? 0}</p><button data-polywar-action="siege">Siege capital</button><button data-polywar-action="repair_capital">Repair capital</button></section>`;
  }
};

const polywarGovernanceUi = window.polywarGovernanceUi = window.polywarGovernanceUi || {
  orders: [],
  async refresh() { const data = await api('/api/polywar/governance'); if (data.ok) { this.orders = data.orders || []; this.render(data); map?.requestDraw?.(); } return data; },
  render(data) {
    const root = document.getElementById('polywarGovernancePanel') || document.querySelector('[data-polywar-governance]');
    if (!root) return;
    const candidates = (data.candidates || []).map(c => `<li><b>${esc(c.user_id)}</b> — ${esc(c.statement || '')} — votes: ${esc(c.vote_count || 0)}<button data-polywar-vote="${c.user_id}">${Number(data.current_user_vote) === Number(c.user_id) ? 'Current vote' : 'Vote / Change vote'}</button></li>`).join('');
    const orders = (data.orders || []).map(o => `<li><button data-polywar-goto-order="${o.x},${o.y}">${esc(o.order_type)} ${esc(o.x)},${esc(o.y)}</button> ${esc(o.message || '')}<button data-polywar-cancel-order="${o.id}">Cancel</button></li>`).join('');
    root.innerHTML = `<h3>Governance</h3><p>Commander: ${esc(data.commander?.commander_user_id || 'none')}</p><p>Term ends: ${esc(data.commander?.commander_term_ends_at || '—')}</p><p>Election ends: ${esc(data.active_election?.ends_at || '—')}</p><ul>${candidates}</ul><button data-polywar-nominate="true">Nominate myself</button><button data-polywar-nominate="false">Withdraw</button><h4>Orders</h4><ul>${orders}</ul>`;
  },
  drawOrders(ctx, worldToScreen) {
    for (const o of this.orders || []) { const p = worldToScreen ? worldToScreen(o.x, o.y) : o; ctx.save(); ctx.strokeStyle = '#fff'; ctx.strokeRect(p.x - 6, p.y - 6, 12, 12); ctx.fillText(o.order_type, p.x + 8, p.y); ctx.restore(); }
  }
};


async function handlePolywarUiClick(e) {
  const vote = e.target.closest('[data-polywar-vote]');
  const nom = e.target.closest('[data-polywar-nominate]');
  const action = e.target.closest('[data-polywar-action]');
  const gotoOrder = e.target.closest('[data-polywar-goto-order]');
  const cancelOrder = e.target.closest('[data-polywar-cancel-order]');
  if (action) { actionMode = action.dataset.polywarAction; map?.updatePanel(); await map?.capture(); return; }
  if (vote) { const d = await api('/api/polywar/governance/vote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({candidate_user_id:Number(vote.dataset.polywarVote)})}); if(!d.ok) alert(d.error || 'Vote failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (nom) { const active = nom.dataset.polywarNominate === 'true'; const statement = active ? (prompt('Candidate statement') || '') : ''; const d = await api('/api/polywar/governance/nominate', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({active, statement})}); if(!d.ok) alert(d.error || 'Nomination failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (cancelOrder) { const d = await api('/api/polywar/orders', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:Number(cancelOrder.dataset.polywarCancelOrder), active:false})}); if(!d.ok) alert(d.error || 'Order cancel failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (gotoOrder) { const [x,y] = gotoOrder.dataset.polywarGotoOrder.split(',').map(Number); if (map) { map.cx=x; map.cy=y; map.clamp(); map.ensureChunks(); map.ensureSectors(); map.select(x,y); map.requestDraw(); } }
}

init();
