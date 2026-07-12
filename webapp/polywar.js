const root = document.getElementById("polywarRoot");
let polywarWorldSeq = 0;
let polywarResultsSeq = 0;
let polywarRewardSeq = 0;
const tg = window.Telegram?.WebApp;
let energyTimer = null;
let syncTimer = null;
let worldCountdownTimer = null;
const polywarClaimKeys = new Map();
const polywarActionKeys = new Map();
let currentState = null;
let map = null;
let actionMode = "capture"; // core modes: new Set(["capture", "attack", "reinforce", "siege", "repair_capital"]); legacy checks actionMode === "siege" / actionMode === "repair_capital"
let quickActionsEnabled = localStorage.getItem("polywar_quick_actions") !== "off"; // Quick actions: OFF when persisted off

try { tg?.ready(); tg?.expand(); } catch (_) {}

const TERRAIN_COST = { plain: 1, forest: 1, mountain: 2, swamp: 2, desert: 1, road: 1, ruins: 1, water: null, river: null };
const TERRAIN_COLOR = { plain: "#76a35b", forest: "#20723d", mountain: "#807a73", swamp: "#476a50", desert: "#c7a35a", road: "#b8935a", ruins: "#8d6e92", water: "#245ea8", river: "#39a7d8" };

function selectedFactionId(state = currentState) { return Number(state?.selected_faction?.id || state?.player?.faction_id || 0); }
function terrainEnergyCost(terrain) { return Object.prototype.hasOwnProperty.call(TERRAIN_COST, terrain) ? TERRAIN_COST[terrain] : null; }
function primaryActionCost(action, cell, state, mapRef) {
  const base = terrainEnergyCost(cell?.terrain);
  const rules = state?.rules || {};
  if (action === "capture") return base;
  if (action === "attack") return base == null ? null : base + Number(rules.combat?.enemy_attack_extra_energy || 1);
  if (action === "reinforce") return Number(rules.combat?.reinforce_energy_cost || 1);
  if (action === "siege") return base == null ? null : base + Number(rules.capitals?.siege_extra_energy || 0);
  if (action === "repair_capital") return Number(rules.capitals?.repair_energy_cost || 0);
  return null;
}
function primaryActionLabel(action) { return ({capture:"Capture",attack:"Attack",reinforce:"Reinforce",siege:"Siege",repair_capital:"Repair capital"})[action] || "No action"; }
function enoughEnergy(state, cost) { return cost != null && Number(state?.energy?.current_energy || 0) >= Number(cost || 0); }
function resolvePrimaryCellAction({ cell, selected, state, map }) {
  const c = cell || {}, fid = selectedFactionId(state), energy = state?.energy || {}, terrain = c.terrain;
  const disabled = (action, reason) => ({ action, label: primaryActionLabel(action), energyCost: primaryActionCost(action, c, state, map), enabled: false, reason });
  const enabled = action => ({ action, label: primaryActionLabel(action), energyCost: primaryActionCost(action, c, state, map), enabled: true, reason: null });
  if (!terrain) return { action: null, label: "Loading", energyCost: null, enabled: false, reason: "Loading cell data…" };
  if (c.rift?.status === "active") return { action: null, label: "Seal rift", energyCost: null, enabled: false, reason: "Active rift must be sealed first" };
  if (!fid) return { action: null, label: "Choose faction", energyCost: null, enabled: false, reason: "Choose a faction first" };
  if (energy.is_locked) return { action: null, label: "Locked", energyCost: null, enabled: false, reason: "Player is temporarily locked" };
  const ownAdjacent = !!(selected && map?.isFrontline?.(selected.x, selected.y, fid));
  const base = terrainEnergyCost(terrain);
  const terrainReason = terrain === "water" || terrain === "river" ? "Water cannot be captured" : terrain === "mountain" && base == null ? "Mountain is unavailable" : "Cell terrain is unavailable";
  if (c.capital) {
    if (Number(c.capital.controller_faction_id) !== fid) {
      const cost = primaryActionCost("siege", c, state, map);
      if (!ownAdjacent) return disabled("siege", "Capital requires a siege");
      if (!enoughEnergy(state, cost)) return disabled("siege", "Not enough energy");
      return enabled("siege");
    }
    const cost = primaryActionCost("repair_capital", c, state, map);
    if (Number(c.capital.siege_progress || 0) <= 0) return disabled("repair_capital", "Capital requires a siege");
    if (!ownAdjacent) return disabled("repair_capital", "Your territory is not adjacent");
    if (!enoughEnergy(state, cost)) return disabled("repair_capital", "Not enough energy");
    return enabled("repair_capital");
  }
  if (base == null) return { action: null, label: "Unavailable", energyCost: null, enabled: false, reason: terrainReason };
  if (c.owner && Number(c.owner) !== fid) {
    const cost = primaryActionCost("attack", c, state, map);
    if (!ownAdjacent) return disabled("attack", "Your territory is not adjacent");
    if (!enoughEnergy(state, cost)) return disabled("attack", "Not enough energy");
    return enabled("attack");
  }
  if (Number(c.owner || 0) === fid) {
    if (c.contest && Number(c.contest.contest_progress || 0) > 0) {
      const cost = primaryActionCost("reinforce", c, state, map);
      if (!enoughEnergy(state, cost)) return disabled("reinforce", "Not enough energy");
      return enabled("reinforce");
    }
    return { action: null, label: "Controlled", energyCost: null, enabled: false, reason: "Cell is already controlled by your faction" };
  }
  const cost = primaryActionCost("capture", c, state, map);
  if (!enoughEnergy(state, cost)) return disabled("capture", "Not enough energy");
  return enabled("capture");
}
function toast(message, critical = false) { const old=document.querySelector('.polywar-toast'); old?.remove(); const el=document.createElement('div'); el.className='polywar-toast'; el.textContent=message; document.body.appendChild(el); setTimeout(()=>el.remove(), critical ? 4200 : 1800); }
function actionToast(d, action) { if (d?.mine_hit) return; const labels={capture:'Captured',attack:'Attack progress',reinforce:'Reinforced',siege:'Siege progress',repair_capital:'Capital repaired'}; toast(labels[action] || d?.outcome || 'Done'); }
function shortCellReason(reason) {
  const text = String(reason || "Ready");
  const known = [
    [/already controlled/i, "Already controlled"],
    [/not adjacent|territory is not adjacent/i, "Not adjacent"],
    [/not enough energy/i, "Not enough energy"],
    [/choose a faction/i, "Choose a faction"],
    [/capital requires.*siege|requires a siege/i, "Capital requires siege"],
    [/loading/i, "Map data loading"],
    [/locked/i, "Temporarily locked"],
    [/water|terrain.*unavailable|unavailable/i, "Unavailable"],
  ];
  return (known.find(([rx]) => rx.test(text)) || [null, text])[1];
}

function resolveSecondaryCellActions({ cell, selected, state, map }) {
  const c = cell || {}, fid = selectedFactionId(state), energy = state?.energy || {}, out = [];
  const ownAdjacent = !!(fid && selected && map?.isFrontline?.(selected.x, selected.y, fid));
  const disabledReason = cost => !fid ? "Choose a faction first" : energy.is_locked ? "Player is temporarily locked" : !ownAdjacent ? "Your territory is not adjacent" : !enoughEnergy(state, cost) ? "Not enough energy" : map?.pending ? "Action in progress" : null;
  const push = (action, label, cost, relevant, extraEnabled = true, extraReason = null) => {
    if (!relevant) return;
    const reason = extraReason || disabledReason(cost) || (!extraEnabled ? "Action is no longer available" : null);
    out.push({ action, label, energyCost: cost, enabled: !reason && extraEnabled, reason });
  };
  const worldRules = state?.rules?.world || {}, rebellionRules = state?.rules?.rebellions || {};
  push("seal_rift", "Seal rift", Number(worldRules.seal_energy_cost || 0), c.rift?.status === "active");
  push("support_rebellion", "Support rebellion", Number(rebellionRules.support_energy_cost || 0), c.rebellion?.status === "active" && fid === Number(c.rebellion?.capital_original_faction_id));
  push("suppress_rebellion", "Suppress rebellion", Number(rebellionRules.suppress_energy_cost || 0), c.rebellion?.status === "active" && fid === Number(c.rebellion?.controller_faction_id));
  const scanReason = !fid ? "Choose a faction first" : energy.is_locked ? "Player is temporarily locked" : map?.pending ? "Action in progress" : null;
  [3, 5].forEach(size => {
    const cost = size === 3 ? 2 : 4;
    const reason = scanReason || (!enoughEnergy(state, cost) ? "Not enough energy" : null);
    if (fid) out.push({ action: `scan_${size}`, label: `Scan ${size}×${size}`, energyCost: cost, enabled: !reason, reason });
  });
  const base = terrainEnergyCost(c.terrain), flagRelevant = !!(c.terrain && !c.owner && base != null && c.rift?.status !== "active");
  if (flagRelevant) {
    const reason = !fid ? "Choose a faction first" : map?.pending ? "Action in progress" : null;
    out.push({ action: c.flags?.current_user_flagged ? "remove_flag" : "flag_mine", label: c.flags?.current_user_flagged ? "Remove my flag" : "Flag mine", energyCost: null, enabled: !reason, reason });
  }
  return out;
}
function selectedKey(selected) { return selected ? `${selected.x},${selected.y}` : null; }
function isDuplicateSuccess(d) { return !!(d?.ok || d?.duplicate); }



function esc(v) { return String(v ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }
async function telegramAuthIfAvailable() { const initData = tg?.initData || ""; if (!initData) return false; const r = await fetch("/api/auth/telegram", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ init_data: initData }) }); return r.ok; }
async function api(path, opts = {}) {
  const controller = new AbortController();
  const timeoutMs = Number(opts.timeoutMs || 20000);
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let r;
  try {
    const requestOpts = { ...opts, signal: opts.signal || controller.signal };
    delete requestOpts.timeoutMs;
    r = await fetch(path, requestOpts);
  } catch (err) {
    if (err?.name === "AbortError") return { ok: false, error: "request_timeout", httpStatus: 0 };
    return { ok: false, error: "network_error", httpStatus: 0 };
  } finally {
    clearTimeout(timer);
  }
  const text = await r.text();
  let d = null;
  try {
    d = text ? JSON.parse(text) : {};
  } catch (_) {
    const contentType = r.headers?.get?.("content-type") || "";
    const fragment = String(text || "").replace(/\s+/g, " ").slice(0, 120);
    console.error("PolyWar API invalid JSON", { status: r.status, contentType, fragment });
    d = { ok: false, error: r.status >= 500 ? "server_error" : "invalid_server_response" };
  }
  if (!r.ok) d.httpStatus = r.status;
  return d;
}
function fmtTime(sec) { sec = Math.max(0, Number(sec || 0)); return `${Math.floor(sec / 60)}m ${String(sec % 60).padStart(2, "0")}s`; }
function factionDot(f) { return `<span class="dot" style="background:${esc(f?.color || "#777")}"></span>`; }
function clearTimers() { if (energyTimer) clearInterval(energyTimer); if (syncTimer) clearInterval(syncTimer); if (worldCountdownTimer) clearInterval(worldCountdownTimer); energyTimer = syncTimer = worldCountdownTimer = null; }
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
    this.worldSeq = 0; this.riftCache = new Map(); this.rebellionCache = new Map();
    this.sectorCache = new Map();
    this.sectorLoading = new Set();
    this.sectorSeq = 0;
    this.loading = new Set();
    this.pendingRequests = new Map();
    this.failedChunks = new Set();
    this.initialChunksReady = false;
    this.abort = new AbortController();
    this.destroyed = false;
    this.drawFrame = null;
    this.loadSeq = 0;
    this.selected = null;
    this.cell = 10;
    this.pending = false;
    this.pendingCellKey = null;
    this.lastTap = null;
    this.moreOpen = false;
    this.lastSuccess = null;
    this.pointerStarts = new Map();
    this.hadMultiTouch = false;
    this.tapSeq = 0;
    const b = baseFor(state.selected_faction?.id) || { x: Math.floor(state.map.width / 2), y: Math.floor(state.map.height / 2) };
    this.cx = b.x;
    this.cy = b.y;
    this.bind();
    this.resize({ loadData: false });
    this.select(b.x, b.y);
    this.requestDraw();
    this.bootstrapInitialLoad();
  }
  bind() {
    const signal = this.abort.signal;
    this.onResize = () => this.resize();
    window.addEventListener("resize", this.onResize, { signal });
    this.canvas.addEventListener("pointerdown", e => { this.canvas.setPointerCapture(e.pointerId); this.pointerStarts.set(e.pointerId, { x:e.clientX, y:e.clientY, cx:this.cx, cy:this.cy, pan:false }); if (this.pointerStarts.size > 1) this.hadMultiTouch = true; }, { signal });
    this.canvas.addEventListener("pointermove", e => { const g=this.pointerStarts.get(e.pointerId); if (!g) return; const dist=Math.hypot(e.clientX-g.x, e.clientY-g.y); if (dist > 8) g.pan = true; if (this.hadMultiTouch || !g.pan) return; this.cx = g.cx - (e.clientX - g.x) / this.cell; this.cy = g.cy - (e.clientY - g.y) / this.cell; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); }, { signal });
    this.canvas.addEventListener("pointerup", e => { const g=this.pointerStarts.get(e.pointerId); this.pointerStarts.delete(e.pointerId); const wasMulti=this.hadMultiTouch; if (!this.pointerStarts.size) this.hadMultiTouch = false; if (g && !g.pan && !wasMulti && this.pointerStarts.size === 0) { const p = this.screenToCell(e.offsetX, e.offsetY); this.handleCellTap(p.x, p.y); } }, { signal });
    this.canvas.addEventListener("pointercancel", e => { this.pointerStarts.delete(e.pointerId); if (!this.pointerStarts.size) this.hadMultiTouch = false; }, { signal });
    this.canvas.addEventListener("wheel", e => { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1.25 : 0.8); }, { passive: false, signal });
    document.getElementById("zoomIn").addEventListener("click", () => this.zoom(1.25), { signal });
    document.getElementById("zoomOut").addEventListener("click", () => this.zoom(0.8), { signal });
    document.getElementById("goBase").addEventListener("click", () => { const b = baseFor(currentState?.selected_faction?.id); if (b) { this.cx = b.x; this.cy = b.y; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); } }, { signal });
    document.getElementById("primaryActionBtn")?.addEventListener("click", () => this.executePrimaryCellAction(), { signal });
    document.getElementById("quickActionsToggle")?.addEventListener("click", () => { quickActionsEnabled = !quickActionsEnabled; localStorage.setItem("polywar_quick_actions", quickActionsEnabled ? "on" : "off"); this.updatePanel(); }, { signal });
    document.getElementById("moreActionsBtn")?.addEventListener("click", () => { this.moreOpen = !this.moreOpen; this.updatePanel(); }, { signal });
    this.canvas.tabIndex = 0;
    this.canvas.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && this.selected) { e.preventDefault(); this.executePrimaryCellAction(); } }, { signal });
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
    this.pendingRequests.clear();
    this.sectorLoading.clear();
  }
  updateState(state) { this.state = state; this.updatePanel(); }
  async bootstrapInitialLoad() { await this.ensureChunks(); if (this.destroyed) return; this.initialChunksReady = true; await Promise.allSettled([this.ensureSectors(), this.refreshCapitals(), this.refreshGovernance()]); }
  resize({ loadData = true } = {}) { if (this.destroyed) return; this.dpr = Math.max(1, window.devicePixelRatio || 1); const r = this.canvas.getBoundingClientRect(); this.canvas.width = Math.floor(r.width * this.dpr); this.canvas.height = Math.floor(r.height * this.dpr); this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); this.w = r.width; this.h = r.height; this.clamp(); if (loadData && this.initialChunksReady) { this.ensureChunks(); this.ensureSectors(); } this.requestDraw(); }
  zoom(f) { this.cell = Math.max(2, Math.min(36, this.cell * f)); if (this.cell >= 6) this.ensureChunks(); this.ensureSectors(); this.updatePanel(); this.requestDraw(); }
  clamp() { this.cx = Math.max(0, Math.min(this.state.map.width - 1, this.cx)); this.cy = Math.max(0, Math.min(this.state.map.height - 1, this.cy)); }
  centerOnBase(zoom = 18) { const b = baseFor(currentState?.selected_faction?.id); if (!b) return; this.cx = b.x; this.cy = b.y - 3; this.cell = Math.max(this.cell, zoom); this.clamp(); this.ensureChunks(); this.ensureSectors(); this.requestDraw(); }
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
    const visible = this.visibleChunks();
    if (!forceKey && this.cell < 6) { this.ensureSectors(); return; }
    const retryKeys = !forceKey ? [...this.failedChunks].map(k => k.split(",").map(Number)) : [];
    const wanted = forceKey ? [[...forceKey.split(",").map(Number), true]] : visible.concat(retryKeys).map(([x,y]) => [x,y,false]);
    const missing = [];
    for (const [x, y, forced] of wanted) {
      const key = `${x},${y}`;
      if (forced) this.cache.delete(key);
      if (!this.cache.has(key) && !this.loading.has(key)) missing.push([x,y]);
    }
    const unique = [...new Map(missing.map(c => [c.join(","), c])).values()];
    if (!unique.length) return;
    this.status("Loading chunks…");
    const limit = Math.max(1, Number(this.state.map.max_chunks_per_request || 9));
    const retryable = new Set(["server_error", "request_timeout", "network_error", "deadlock_retryable"]);
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const loadBatch = async (batch, batchKey) => {
      const batchKeys = batch.map(c => c.join(","));
      let promiseForThisBatch = null;
      batchKeys.forEach(k => this.loading.add(k));
      try {
        let data = null;
        for (let attempt = 0; attempt < 3; attempt++) {
          data = await api("/api/polywar/map/chunks?chunks=" + batch.map(c => c.join(",")).join(";"));
          if (data?.ok || !retryable.has(data?.error)) break;
          if (attempt < 2) await sleep(attempt === 0 ? 300 : 1000);
        }
        if (this.destroyed) return data;
        if (data?.ok) {
          const returned = new Set();
          data.chunks.forEach(ch => { const key = `${ch.chunk_x},${ch.chunk_y}`; returned.add(key); this.cache.set(key, ch); this.failedChunks.delete(key); });
          batchKeys.forEach(k => { if (!returned.has(k)) this.failedChunks.add(k); });
          this.pruneCache();
        } else {
          batchKeys.forEach(k => this.failedChunks.add(k));
        }
        this.updateChunkStatus();
        this.requestDraw(); this.updatePanel();
        return data;
      } finally {
        batchKeys.forEach(k => this.loading.delete(k));
        if (this.pendingRequests.has(batchKey)) this.pendingRequests.delete(batchKey);
        this.updateChunkStatus();
      }
    };
    const tasks = [];
    for (let i = 0; i < unique.length && !this.destroyed; i += limit) {
      const batch = unique.slice(i, i + limit);
      const key = batch.map(c => c.join(",")).join(";");
      if (!this.pendingRequests.has(key)) { const promise = loadBatch(batch, key); this.pendingRequests.set(key, promise); }
      tasks.push(this.pendingRequests.get(key));
    }
    await Promise.allSettled(tasks);
  }

  updateChunkStatus() { if (this.loading.size) this.status("Loading chunks…"); else if (this.failedChunks.size) { this.status("Map data unavailable"); this.showRetryMap(); } else this.status(""); }

  showRetryMap() {
    let btn = document.getElementById("retryMapBtn");
    if (btn) return;
    const status = document.getElementById("chunkStatus");
    btn = document.createElement("button");
    btn.className = "btn mini"; btn.id = "retryMapBtn"; btn.textContent = "Retry map";
    btn.onclick = () => { btn.remove(); this.ensureChunks(); };
    status?.after(btn);
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
  select(x, y) { if (x < 0 || y < 0 || x >= this.state.map.width || y >= this.state.map.height) return; if (this.selected?.x !== x || this.selected?.y !== y) this.moreOpen = false; this.selected = { x, y }; this.ensureChunks(); this.updatePanel(); this.requestDraw(); }
  getCell(x, y) { const cs = this.state.map.chunk_size, cx = Math.floor(x / cs), cy = Math.floor(y / cs), ch = this.cache.get(`${cx},${cy}`); if (!ch) return {}; const lx = x - cx * cs, ly = y - cy * cs; const intel=(ch.intel||[]).find(i=>+i.x===+x&&+i.y===+y); const rift=(ch.rifts||[]).find(r=>+r.x===+x&&+r.y===+y); const rebellion=(ch.rebellions||[]).find(r=>+r.x===+x&&+r.y===+y); const flags=(ch.flags||[]).find(f=>+f.x===+x&&+f.y===+y); const contest=(ch.contested_cells||[]).find(q=>+q.x===+x&&+q.y===+y); const chunkCapital=(ch.capitals||[]).find(q=>+q.x===+x&&+q.y===+y); const cachedCapital=polywarCapitalUi?.cache?.get(`${x},${y}`); const capital=cachedCapital ? {...chunkCapital, ...cachedCapital} : chunkCapital; const orders=(ch.orders||[]).filter(o=>+o.x===+x&&+o.y===+y); return { terrain: ch.terrain?.[ly]?.[lx], owner: ch.owners?.[ly]?.[lx], intel, flags, contest, capital, orders, rift, rebellion }; }
  isFrontline(x, y, fid) { return [[1,0],[-1,0],[0,1],[0,-1]].some(([dx,dy]) => +this.getCell(x+dx,y+dy).owner === +fid); }
  refreshTargetSector(target) { if (!target) return; const ss=this.sectorSize(), key=`${Math.floor(target.x/ss)},${Math.floor(target.y/ss)}`; return this.ensureSectors(key); }
  secondaryActions(c, selected = this.selected) {
    return resolveSecondaryCellActions({ cell: c, selected, state: currentState, map: this })
      .map(a => `<button class="secondary-action-pill" data-polywar-secondary="${esc(a.action)}" ${a.enabled ? "" : "disabled"} title="${esc(shortCellReason(a.reason || ""))}">${esc(a.label)}${a.energyCost == null ? "" : ` · ${esc(a.energyCost)}⚡`}</button>`)
      .join("");
  }
  updatePanel() {
    const s = this.selected || {}, c = this.getCell(s.x, s.y);
    const primary = resolvePrimaryCellAction({ cell: c, selected: s, state: currentState, map: this });
    actionMode = primary.action || "capture";
    const owner = c.owner ? ((currentState?.factions || []).find(f => Number(f.id) === Number(c.owner))?.name || `Faction ${c.owner}`) : "Neutral";
    const reason = this.pending ? "Working…" : shortCellReason(primary.reason || "Ready");
    const el = id => document.getElementById(id);
    if (el("cellCoords")) el("cellCoords").textContent = s.x == null ? "—" : `${s.x}, ${s.y}`;
    if (el("cellTerrain")) el("cellTerrain").textContent = c.terrain || "loading";
    if (el("cellOwner")) el("cellOwner").textContent = owner;
    if (el("cellCost")) el("cellCost").textContent = primary.energyCost == null ? "—" : `${primary.energyCost} ⚡`;
    if (el("cellReason")) { el("cellReason").textContent = reason; el("cellReason").title = primary.reason || reason; }
    if (el("quickActionsToggle")) { el("quickActionsToggle").textContent = `Quick actions: ${quickActionsEnabled ? "ON" : "OFF"}`; el("quickActionsToggle").setAttribute("aria-pressed", String(quickActionsEnabled)); }
    const sheet = document.querySelector(".compact-cell-sheet");
    sheet?.classList.toggle("compact-cell-sheet--expanded", !!this.moreOpen);
    const btn = el("primaryActionBtn");
    if (btn) { btn.disabled = !primary.enabled || this.pending; btn.textContent = this.pending ? "Working…" : primary.label; btn.classList.toggle("status-pill", !primary.enabled && !this.pending); btn.setAttribute("aria-label", `${primary.label} selected cell`); }
    const more = el("moreActionsBtn");
    if (more) { more.setAttribute("aria-expanded", String(this.moreOpen)); more.classList.toggle("is-open", !!this.moreOpen); more.innerHTML = this.moreOpen ? 'Less <span class="more-chevron">▴</span>' : 'More <span class="more-chevron">▾</span>'; }
    const menu = el("secondaryActionsMenu");
    if (menu) { menu.hidden = !this.moreOpen; menu.innerHTML = this.secondaryActions(c, s); }
    const details = el("cellDetails");
    if (details) details.innerHTML = `<b id="cellOwner" class="cell-owner-line">${esc(owner)}</b><b id="cellCost" class="cell-cost">${primary.energyCost == null ? "—" : `${esc(primary.energyCost)} ⚡`}</b><span class="sheet-extra-detail">${c.capital ? `Capital siege ${esc(c.capital.siege_progress || 0)}/${esc(c.capital.siege_required || currentState?.rules?.capitals?.siege_required || 0)}` : ""}${c.contest ? ` · Contested ${esc(c.contest.contest_progress)}/${esc(c.contest.contest_required)}` : ""}</span>`;
  }
  async handleCellTap(x, y) {
    if (this.pending) return;
    const targetKey = `${x},${y}`;
    this.tapSeq = (this.tapSeq || 0) + 1;
    const tapSeq = this.tapSeq;
    this.select(x, y);
    const now = Date.now();
    if (this.lastTap?.key === targetKey && now - this.lastTap.t < 320) return;
    this.lastTap = { key: targetKey, t: now };
    let c = this.getCell(x, y);
    if (!c.terrain) {
      await this.ensureChunks(`${Math.floor(x / this.state.map.chunk_size)},${Math.floor(y / this.state.map.chunk_size)}`);
      if (tapSeq !== this.tapSeq || this.pending || !this.selected || `${this.selected.x},${this.selected.y}` !== targetKey) return;
      c = this.getCell(x, y); this.updatePanel();
    }
    const primary = resolvePrimaryCellAction({ cell: c, selected: this.selected, state: currentState, map: this });
    if (quickActionsEnabled && primary.enabled) await this.executePrimaryCellAction(primary.action);
    else if (primary.reason) toast(primary.reason);
  }
  async executePrimaryCellAction(action = null) {
    if (!this.selected || this.pending) return;
    const target = { x: this.selected.x, y: this.selected.y, key: `${this.selected.x},${this.selected.y}` };
    const c = this.getCell(target.x, target.y);
    const primary = resolvePrimaryCellAction({ cell: c, selected: target, state: currentState, map: this });
    const actionType = action || primary.action;
    if (!primary.enabled || !primary.action || actionType !== primary.action) { toast(primary.reason || "Action is no longer available"); return; }
    const keyId=`${currentState?.season?.id}:${actionType}:${target.x}:${target.y}`;
    const idem=polywarActionKeys.get(keyId)||`${keyId}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
    polywarActionKeys.set(keyId, idem);
    this.moreOpen = false;
    this.pending = true; this.pendingCellKey = target.key; this.updatePanel(); this.requestDraw();
    const d = await api("/api/polywar/action", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ action_type: actionType, x:target.x, y:target.y, idempotency_key: idem }) });
    this.pending = false; this.pendingCellKey = null;
    if (!d.ok && !d.duplicate) { toast(d.httpStatus === 401 ? "Authentication required" : (d.error || "Action failed"), true); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d; }
    polywarActionKeys.delete(keyId); currentState.energy = d.energy || currentState.energy;
    if (d.mine_hit) { this.blast = {x:target.x,y:target.y,t:Date.now()}; alert(`Mine hit — actions locked until ${d.locked_until || d.energy?.locked_until || "server unlock"} (${fmtTime(d.energy?.lock_seconds_remaining || 0)} remaining)`); }
    else { this.lastSuccess = { x:target.x, y:target.y, t:Date.now() }; actionToast(d, actionType); }
    const cs = this.state.map.chunk_size;
    await this.ensureChunks(`${Math.floor(target.x / cs)},${Math.floor(target.y / cs)}`);
    await this.refreshCapitals(); await this.refreshTargetSector(target); await syncState(false, { soft: true }); await syncPolywarResults().catch(()=>{});
    updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d;
  }
  async refreshCapitals() { const d = await polywarCapitalUi.refresh(this); this.requestDraw(); this.updatePanel(); return d; }
  async refreshGovernance() { const d = await polywarGovernanceUi.refresh(this); this.requestDraw(); return d; }
  async refreshWorld() { const seq = ++this.worldSeq, expectedSeason=currentState?.season?.id, expectedMap=this; const d = await api("/api/polywar/world"); if (seq !== this.worldSeq || this.destroyed || expectedMap!==map || (d.world?.season_id && expectedSeason && +d.world.season_id!==+expectedSeason)) return {ok:false, stale:true}; if (d.ok && d.world) { currentState.world = d.world; const hud=document.getElementById('polywarWorldHud'); if(hud) hud.innerHTML=`<h2>World HUD</h2>${renderWorldHud(currentState)}`; startWorldCountdownTimer(); this.requestDraw(); } return d; }
  async sealRift() { return this.executeSecondaryCellAction("seal_rift"); }
  async supportRebellion() { return this.executeSecondaryCellAction("support_rebellion"); }
  async suppressRebellion() { return this.executeSecondaryCellAction("suppress_rebellion"); }
  async executeSecondaryCellAction(action) {
    if (!this.selected || this.pending) return;
    const target = { x:this.selected.x, y:this.selected.y, key:`${this.selected.x},${this.selected.y}` };
    const c = this.getCell(target.x, target.y);
    const allowed = resolveSecondaryCellActions({ cell:c, selected:target, state:currentState, map:this }).find(a => a.action === action);
    if (!allowed || !allowed.enabled) { toast(allowed?.reason || "Action is no longer available"); return; }
    if (action === "scan_3") return this.scan(3, target);
    if (action === "scan_5") return this.scan(5, target);
    if (action === "flag_mine") return this.flag(true, target);
    if (action === "remove_flag") return this.flag(false, target);
    return this.specialAction(action, target);
  }
  async specialAction(action_type, target = null) {
    if (!this.selected || this.pending) return;
    target = target || { x:this.selected.x, y:this.selected.y, key:`${this.selected.x},${this.selected.y}` };
    const keyId=`${currentState?.season?.id}:${action_type}:${target.x}:${target.y}`;
    const idem=polywarActionKeys.get(keyId)||`${keyId}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
    polywarActionKeys.set(keyId,idem);
    this.pending = true; this.pendingCellKey = target.key; this.moreOpen = false; this.updatePanel();
    const d = await api("/api/polywar/action", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action_type, x:target.x, y:target.y, idempotency_key:idem})});
    this.pending = false; this.pendingCellKey = null;
    if(!d.ok && !d.duplicate){ toast(d.error || "Action failed", true); if (selectedKey(this.selected) === target.key) this.updatePanel(); return d; }
    polywarActionKeys.delete(keyId); currentState.energy=d.energy||currentState.energy;
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`); await this.refreshWorld(); await syncPolywarResults().catch(()=>{}); updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d;
  }
  async capture() { return this.executePrimaryCellAction(actionMode); }
  async scan(size, target = null) {
    if (!this.selected || this.pending) return;
    target = target || { x:this.selected.x, y:this.selected.y, key:`${this.selected.x},${this.selected.y}` };
    const action = `scan_${size}`;
    const c = this.getCell(target.x, target.y);
    const allowed = resolveSecondaryCellActions({ cell:c, selected:target, state:currentState, map:this }).find(a => a.action === action);
    if (!allowed || !allowed.enabled) { toast(allowed?.reason || "Action is no longer available"); return; }
    if (!confirm(`Scan ${size}×${size} around ${target.x},${target.y}?`)) return;
    this.pending = true; this.pendingCellKey = target.key; this.moreOpen = false; this.updatePanel();
    const d = await api("/api/polywar/scan", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({center_x:target.x, center_y:target.y, size, idempotency_key:`scan-${size}-${Date.now()}-${Math.random().toString(16).slice(2)}`})});
    this.pending=false; this.pendingCellKey = null;
    if(!d.ok){ toast(d.error || "Scan failed", true); if (selectedKey(this.selected) === target.key) this.updatePanel(); return; }
    currentState.energy=d.energy; toast(`Active mines detected: ${d.active_mine_count}`);
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`); updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw();
  }
  async flag(active, target = null) {
    if (!this.selected || this.pending) return;
    target = target || { x:this.selected.x, y:this.selected.y, key:`${this.selected.x},${this.selected.y}` };
    const action = active ? "flag_mine" : "remove_flag";
    const c = this.getCell(target.x, target.y);
    const allowed = resolveSecondaryCellActions({ cell:c, selected:target, state:currentState, map:this }).find(a => a.action === action);
    if (!allowed || !allowed.enabled) { toast(allowed?.reason || "Action is no longer available"); return; }
    this.pending=true; this.pendingCellKey = target.key; this.moreOpen = false; this.updatePanel();
    const d=await api("/api/polywar/flag", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({x:target.x,y:target.y,active})});
    this.pending=false; this.pendingCellKey = null;
    if(!d.ok){ toast(d.error || "Flag failed", true); if (selectedKey(this.selected) === target.key) this.updatePanel(); return; }
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw();
  }
  visibleCellBounds() { const a=this.screenToCell(0,0), b=this.screenToCell(this.w,this.h); return {minX:Math.max(0,Math.floor(Math.min(a.x,b.x))-1), maxX:Math.min(this.state.map.width-1,Math.ceil(Math.max(a.x,b.x))+1), minY:Math.max(0,Math.floor(Math.min(a.y,b.y))-1), maxY:Math.min(this.state.map.height-1,Math.ceil(Math.max(a.y,b.y))+1)}; }
  drawSkeleton(ctx) { const b=this.visibleCellBounds(); if (this.cell < 6) return; for(let y=b.minY;y<=b.maxY;y++) for(let x=b.minX;x<=b.maxX;x++){ const key=`${Math.floor(x/this.state.map.chunk_size)},${Math.floor(y/this.state.map.chunk_size)}`; if(this.cache.has(key)) continue; const p=this.cellToScreen(x,y); ctx.fillStyle=((x+y)&1)?"rgba(255,255,255,.035)":"rgba(0,0,0,.035)"; ctx.fillRect(p.x,p.y,this.cell,this.cell); } }
  drawCellGrid(ctx) { const b=this.visibleCellBounds(); if (this.cell < 6) { const ss=this.sectorSize(), r=this.visibleSectorRange(); ctx.strokeStyle="rgba(255,255,255,.18)"; ctx.lineWidth=1; ctx.beginPath(); for(let sx=r.minX;sx<=r.maxX+1;sx++){ const p=this.cellToScreen(sx*ss,b.minY); ctx.moveTo(Math.round(p.x)+.5,0); ctx.lineTo(Math.round(p.x)+.5,this.h); } for(let sy=r.minY;sy<=r.maxY+1;sy++){ const p=this.cellToScreen(b.minX,sy*ss); ctx.moveTo(0,Math.round(p.y)+.5); ctx.lineTo(this.w,Math.round(p.y)+.5); } ctx.stroke(); return; } ctx.strokeStyle="rgba(255,255,255,.10)"; ctx.lineWidth=1; ctx.beginPath(); for(let x=b.minX;x<=b.maxX+1;x++){ const p=this.cellToScreen(x,b.minY); ctx.moveTo(Math.round(p.x)+.5,0); ctx.lineTo(Math.round(p.x)+.5,this.h); } for(let y=b.minY;y<=b.maxY+1;y++){ const p=this.cellToScreen(b.minX,y); ctx.moveTo(0,Math.round(p.y)+.5); ctx.lineTo(this.w,Math.round(p.y)+.5); } ctx.stroke(); }
  drawBaseMarkers(ctx) { for (const b of this.state.map.bases || []) { const p=this.cellToScreen(b.x,b.y); const cx=p.x+this.cell/2, cy=p.y+this.cell/2, r=Math.max(4, Math.min(14, this.cell*.36)); ctx.save(); ctx.shadowColor=b.color||"#fff"; ctx.shadowBlur=8; ctx.fillStyle=b.color||"#fff"; ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.lineWidth=1.25; ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0; ctx.stroke(); ctx.fillStyle="rgba(7,10,24,.82)"; ctx.font=`${Math.max(8,Math.min(13,r*1.15))}px sans-serif`; ctx.textAlign="center"; ctx.textBaseline="middle"; ctx.fillText("⌂",cx,cy+.5); ctx.restore(); } }
  drawSelectedCell(ctx) { if (!this.selected) return; const p=this.cellToScreen(this.selected.x,this.selected.y); ctx.save(); ctx.fillStyle="rgba(53,166,255,.16)"; ctx.fillRect(p.x,p.y,this.cell,this.cell); ctx.strokeStyle="#70d7ff"; ctx.lineWidth=2; ctx.strokeRect(p.x+1,p.y+1,Math.max(2,this.cell-2),Math.max(2,this.cell-2)); ctx.restore(); }
  drawPendingPulse(ctx) { if (!this.pendingCellKey) return; const [px,py]=this.pendingCellKey.split(",").map(Number), p=this.cellToScreen(px,py), phase=(Date.now()%900)/900, pad=2+phase*Math.max(1,this.cell*.18); ctx.save(); ctx.strokeStyle=`rgba(53,166,255,${.8-phase*.45})`; ctx.lineWidth=2; ctx.strokeRect(p.x+pad,p.y+pad,Math.max(2,this.cell-pad*2),Math.max(2,this.cell-pad*2)); ctx.restore(); setTimeout(()=>this.requestDraw(),120); }
  requestDraw() { if (this.destroyed || this.drawFrame) return; this.drawFrame = requestAnimationFrame(() => { this.drawFrame = null; if (!this.destroyed) this.draw(); }); }
  draw() { const ctx = this.ctx; ctx.clearRect(0, 0, this.w, this.h); this.drawSkeleton(ctx); const visible = new Set(this.visibleChunks().map(c => c.join(","))); const cs = this.state.map.chunk_size; for (const [key, ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (let yy = 0; yy < ch.height; yy++) for (let xx = 0; xx < ch.width; xx++) { const x = ch.chunk_x * cs + xx, y = ch.chunk_y * cs + yy, p = this.cellToScreen(x, y); if (p.x + this.cell < 0 || p.y + this.cell < 0 || p.x > this.w || p.y > this.h) continue; ctx.fillStyle = TERRAIN_COLOR[ch.terrain[yy][xx]] || "#555"; ctx.fillRect(p.x, p.y, this.cell + 0.5, this.cell + 0.5); const own = ch.owners[yy][xx]; if (own) { ctx.fillStyle = (+own===8 ? "rgba(20,0,35,.85)" : (currentState.factions || []).find(f => f.id === own)?.color || "rgba(255,255,255,.5)"); ctx.globalAlpha = 0.45; ctx.fillRect(p.x, p.y, this.cell, this.cell); ctx.globalAlpha = 1; } if (+own===8) { ctx.strokeStyle="rgba(210,120,255,.75)"; ctx.beginPath(); ctx.moveTo(p.x,p.y); ctx.lineTo(p.x+this.cell,p.y+this.cell); ctx.moveTo(p.x+this.cell,p.y); ctx.lineTo(p.x,p.y+this.cell); ctx.stroke(); } const rift=(ch.rifts||[]).find(q=>+q.x===x&&+q.y===y); if(rift){ ctx.fillStyle=rift.status==="sealed"?"#30d987":"#e879f9"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2,Math.max(4,this.cell*.35),0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#fff"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2,Math.max(5,this.cell*.48),-Math.PI/2,-Math.PI/2+Math.PI*2*((rift.health_percent||0)/100)); ctx.stroke(); } const contest=(ch.contested_cells||[]).find(q=>+q.x===x&&+q.y===y); if(contest){ ctx.strokeStyle="#fff200"; ctx.lineWidth=2; ctx.strokeRect(p.x+1,p.y+1,this.cell-2,this.cell-2); ctx.fillStyle=(currentState.factions||[]).find(f=>+f.id===+contest.contesting_faction_id)?.color||"#fff"; ctx.fillRect(p.x+2,p.y+this.cell-5,Math.max(2,(this.cell-4)*(contest.contest_progress/contest.contest_required)),3); ctx.fillText("⚔",p.x+2,p.y+12); ctx.lineWidth=1; } if (this.cell > 12) { ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.strokeRect(p.x, p.y, this.cell, this.cell); const intel=(ch.intel||[]).find(i=>+i.x===x&&+i.y===y); const fl=(ch.flags||[]).find(f=>+f.x===x&&+f.y===y); if(intel?.intel_type==="safe_hint"){ ctx.fillStyle="#fff"; ctx.font=`${Math.max(10,this.cell*.65)}px sans-serif`; ctx.fillText(String(intel.adjacent_mines), p.x+3, p.y+this.cell-3); } if(intel?.intel_type==="triggered_mine"){ ctx.fillStyle="#111"; ctx.fillText("✹", p.x+3, p.y+this.cell-3); } if(fl){ ctx.fillStyle="#ffeb3b"; ctx.fillText(`⚑${fl.flag_count}`, p.x+2, p.y+12); } } } } if (this.cell < 8) { const ss=this.sectorSize(), r=this.visibleSectorRange(); for(let sy=r.minY; sy<=r.maxY; sy++) for(let sx=r.minX; sx<=r.maxX; sx++){ const sec=this.sectorCache.get(`${sx},${sy}`), p=this.cellToScreen(sx*ss, sy*ss), size=ss*this.cell; if(sec?.controller_faction_id){ ctx.fillStyle=(currentState.factions||[]).find(f=>+f.id===+sec.controller_faction_id)?.color||"#fff"; ctx.globalAlpha=.16; ctx.fillRect(p.x,p.y,size,size); ctx.globalAlpha=1; } if(sec?.is_contested){ ctx.fillStyle="rgba(255,255,255,.16)"; for(let k=0;k<size;k+=8){ ctx.fillRect(p.x+k,p.y,3,size); } } ctx.strokeStyle="rgba(255,255,255,.25)"; ctx.strokeRect(p.x,p.y,size,size);  } } this.drawBaseMarkers(ctx); for (const [key,ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (const sc of ch.scans||[]) { const p=this.cellToScreen(sc.center_x-sc.size/2, sc.center_y-sc.size/2); ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.strokeRect(p.x,p.y,sc.size*this.cell,sc.size*this.cell); const cp=this.cellToScreen(sc.center_x,sc.center_y); ctx.fillStyle="#fff"; ctx.fillText(String(sc.active_mine_count), cp.x+2, cp.y+12); } } if (actionMode.startsWith("scan") && this.selected) { const size=actionMode==="scan5"?5:3, p=this.cellToScreen(this.selected.x-size/2, this.selected.y-size/2); ctx.strokeStyle="#00e5ff"; ctx.setLineDash([4,3]); ctx.strokeRect(p.x,p.y,size*this.cell,size*this.cell); ctx.setLineDash([]); } if (this.blast && Date.now()-this.blast.t<1800) { const p=this.cellToScreen(this.blast.x,this.blast.y); ctx.fillStyle="rgba(255,80,0,.55)"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2, this.cell*2,0,Math.PI*2); ctx.fill(); setTimeout(()=>this.requestDraw(),80); } polywarCapitalUi.draw(ctx, (x,y)=>this.cellToScreen(x,y), currentState.factions || []); polywarGovernanceUi.drawOrders(ctx, (x,y)=>this.cellToScreen(x,y));  if (this.lastSuccess && Date.now()-this.lastSuccess.t<900) { const p=this.cellToScreen(this.lastSuccess.x,this.lastSuccess.y); ctx.fillStyle="rgba(48,217,135,.45)"; ctx.fillRect(p.x,p.y,this.cell,this.cell); setTimeout(()=>this.requestDraw(),80); } this.drawCellGrid(ctx); this.drawSelectedCell(ctx); this.drawPendingPulse(ctx); }
}

function renderUnavailable(message) { clearTimers(); map?.destroy(); map = null; root.innerHTML = `<section class="glass card"><h2>PolyWar is temporarily unavailable</h2><p class="muted">${esc(message || "Please check back later.")}</p><a class="btn" href="/app">Back to DeepAlpha</a></section>`; }

function renderWorldHud(state){ const w=state.world||{}, season=state.season||{}; return `<div class="polywar-world-grid"><div class="polywar-world-stat">Null State<br><b>${esc(w.status||"dormant")}</b></div><div class="polywar-world-stat">Activation<br><b id="polywarActivationCountdown" data-countdown="${esc(w.activation_at||"")}">${esc(w.activation_at||"—")}</b></div><div class="polywar-world-stat">Next tick<br><b id="polywarNextTickCountdown" data-countdown="${esc(w.next_tick_at||"")}">${esc(w.next_tick_at||"—")}</b></div><div class="polywar-world-stat">Season end<br><b id="polywarSeasonCountdown" data-countdown="${esc(season.ends_at||"")}">${esc(season.ends_at||"—")}</b></div><div class="polywar-world-stat">Domination hold<br><b id="polywarDominationCountdown" data-countdown="${esc(w.domination_hold_until||"")}">${esc(w.domination_hold_until||"—")}</b></div><div class="polywar-world-stat">Corruption<br><b>${esc(w.corruption_level||0)}</b></div><div class="polywar-world-stat">Cells / Sectors / Capitals<br><b>${esc(w.controlled_cells_count||0)} / ${esc(w.controlled_sectors_count||0)} / ${esc(w.controlled_capitals_count||0)}</b></div><div class="polywar-world-stat">Rifts<br><b>${esc((w.active_rifts||[]).length)} active · ${esc((w.sealed_rifts||[]).length)} sealed</b></div></div><p class="muted">Countdowns corrected by server_timestamp ${esc(w.server_timestamp||0)}.</p>`; }
function hasOwnAdjacent(cell,state){ const fid=Number(state.player?.faction_id||0); if(!fid || !map?.selected) return false; return map.isFrontline(map.selected.x, map.selected.y, fid); }
function startWorldCountdownTimer(){ if(worldCountdownTimer) clearInterval(worldCountdownTimer); const offset=(Number(currentState?.world?.server_timestamp||0)*1000)-Date.now(); const tick=()=>document.querySelectorAll('[data-countdown]').forEach(el=>{ const target=Date.parse(el.dataset.countdown||''); el.textContent=Number.isFinite(target)?fmtTime(Math.ceil((target-(Date.now()+offset))/1000)):'—'; }); tick(); worldCountdownTimer=setInterval(tick,1000); }
function polywarRiftPanel(rift, cell, state){ const rules=state.rules?.world||{}; const cost=Number(rules.seal_energy_cost||0); const canSeal = rift.status === "active" && Number(state.player?.faction_id||0)>0 && hasOwnAdjacent(cell,state) && Number(state.energy?.current_energy||0)>=cost && !state.energy?.is_locked && !map?.pending; return `<section class="glass card polywar-rift-panel"><h3>Rift</h3><p>Status: ${esc(rift.status)}</p><p>Health: ${esc(rift.health)}/${esc(rift.max_health)} (${esc(rift.health_percent||0)}%)</p><p>Seal energy cost: ${esc(cost)}</p><p>Frontline eligibility: ${esc(canSeal ? "ready" : "requires active rift, energy and adjacent faction cell")}</p>${canSeal ? `<button class="btn" data-polywar-action="seal_rift">Seal Rift</button>` : ""}</section>`; }
function polywarRebellionPanel(rebellion, cell, state){ const rules=state.rules?.rebellions||{}; const fid=Number(state.player?.faction_id||0); const canSupport=fid===Number(rebellion.capital_original_faction_id); const canSuppress=fid===Number(rebellion.controller_faction_id); return `<section class="glass card polywar-rebellion-panel"><h3>Capital rebellion</h3><p>Original faction: ${esc(rebellion.capital_original_faction_id)}</p><p>Occupier: ${esc(rebellion.controller_faction_id)}</p><p>Status: ${esc(rebellion.status)}</p><p>Progress: ${esc(rebellion.progress)}/${esc(rebellion.required_progress)}</p><p>Support cost: ${esc(rules.support_energy_cost||0)} · Suppress cost: ${esc(rules.suppress_energy_cost||0)}</p>${canSupport ? `<button class="btn" data-polywar-action="support_rebellion">Support rebellion</button>` : ""}${canSuppress ? `<button class="btn" data-polywar-action="suppress_rebellion">Suppress rebellion</button>` : ""}</section>`; }
function renderResultsPanel(state){ const r=state.latest_completed_season||state.results||{}; const rew=state.current_user_pending_reward||{}; return `<p>Victory type: ${esc(r.victory_type||"—")}</p><p>Winner: ${esc(r.winner_faction_id||"—")}</p><p>Results hash: ${esc((r.results_hash||"").slice(0,12))}</p><p>Reward: ${esc(rew.total_reward||0)} · ${esc(rew.status||"not ready")}</p>${rew.total_reward ? `<button class="btn" id="polywarClaimReward">Claim reward</button>` : ""}`; }
async function syncPolywarResults(){ const seq=++polywarResultsSeq, expectedMap=map, expectedSeason=currentState?.latest_completed_season?.id; const d=await api('/api/polywar/results/latest'); if(seq!==polywarResultsSeq || expectedMap!==map || (expectedSeason && (Number(currentState?.latest_completed_season?.id||0)!==Number(expectedSeason) || (d.season?.id && Number(d.season.id)!==Number(expectedSeason)))) || !d.ok) return d; currentState.results=d; currentState.current_user_pending_reward=d.current_user_reward||currentState.current_user_pending_reward; const panel=document.getElementById('polywarResultsPanel'); if(panel) panel.innerHTML=`<h2>Season Results</h2>${renderResultsPanel(currentState)}`; return d; }
async function claimPolywarReward(season_id){ const key=polywarClaimKeys.get(season_id)||`claim-${season_id}-${Date.now()}-${Math.random().toString(16).slice(2)}`; polywarClaimKeys.set(season_id,key); const btn=document.getElementById('polywarClaimReward'); if(btn){ btn.disabled=true; btn.textContent='Claiming…'; } const seq=++polywarRewardSeq; const d=await api('/api/polywar/rewards/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({season_id,idempotency_key:key})}); if(seq!==polywarRewardSeq) return {ok:false,stale:true}; if(d.ok||d.duplicate){ polywarClaimKeys.delete(season_id); await syncPolywarResults(); await syncState(false,{soft:true}); } else if(btn){ btn.disabled=false; btn.textContent='Claim reward'; } return d; }

function render(state) {
  currentState = state;
  if (state && state.enabled === false) { renderUnavailable(state.message); return; }
  const p = state.player || {}, e = state.energy || {}, season = state.season || {}, selected = state.selected_faction, needsJoin = !selected;
  map?.destroy();
  root.innerHTML = `<section class="grid"><div class="glass card"><h2>Season</h2><p class="metric">${esc(season.name || "Active Season")}</p><p class="muted">${esc(season.starts_at)} → ${esc(season.ends_at)}</p></div><div class="glass card"><h2>Energy</h2><p class="metric" id="energyValue">${esc(e.current_energy)}/${esc(e.max_energy)}</p><p class="muted">Next charge: <span id="energyCountdown">${fmtTime(e.seconds_until_next_energy)}</span> · ${esc(e.recharge_minutes)} min/energy</p><p class="muted">Status: <b id="lockStatus">${e.is_locked ? "Mine locked" : "Active"}</b></p></div></section><section class="glass card ${selected ? "confirm" : ""}"><h2>Faction</h2>${selected ? `<p class="metric">${factionDot(selected)}${esc(selected.name)}</p><p class="muted">Faction locked for this season.</p>` : `<p class="muted">Choose your faction to capture cells. Preview map is available before selection.</p>`}</section>${needsJoin ? `<section class="glass card"><h2>Choose faction</h2><div class="factions">${(state.factions || []).map(f => `<button class="faction" data-faction="${esc(f.id)}">${factionDot(f)}${esc(f.name)}<small>${esc(f.description)}</small></button>`).join("")}</div></section>` : ""}<section class="glass card polywar-world-hud" id="polywarWorldHud"><h2>World HUD</h2>${renderWorldHud(state)}</section><section class="glass card map-card"><div class="map-head"><h2>Global War Map</h2><span id="chunkStatus" class="muted"></span><button class="btn mini" id="quickActionsToggle" aria-pressed="true">Quick actions: ON</button><button class="btn mini" id="goBase">Base</button><button class="btn mini" id="zoomOut">−</button><button class="btn mini" id="zoomIn">+</button></div><div class="map-wrap"><canvas id="polywarCanvas" aria-label="PolyWar map. Tap a cell, then press Enter or Space to perform the primary action."></canvas><div class="action-panel compact-cell-sheet" aria-live="polite"><div class="sheet-main"><b>Cell <span id="cellCoords">—</span> · <span id="cellTerrain">—</span></b><span id="cellDetails" class="muted"><b id="cellOwner">Neutral</b> · <b id="cellCost">—</b></span><span id="cellReason" class="muted">Select a cell</span></div><div class="sheet-actions"><button class="btn" id="primaryActionBtn" aria-label="Primary cell action" disabled>${needsJoin ? "Choose faction" : "Capture"}</button><button class="btn mini" id="moreActionsBtn" aria-expanded="false">More ···</button></div><div id="secondaryActionsMenu" class="secondary-actions" hidden></div></div></div></section><section class="glass card polywar-governance-panel" id="polywarGovernancePanel" data-polywar-governance><h2>Governance</h2></section><section class="grid" id="factionStats"><div class="glass card"><h3>Season Points</h3><p class="metric">${esc(p.season_spendable_points || 0)}</p></div><div class="glass card"><h3>Faction Contribution</h3><p class="metric">${esc(p.faction_contribution || 0)}</p></div></section><section class="glass card"><h2>Faction ranking</h2><div id="factionRanking"></div></section><section class="glass card polywar-results-panel" id="polywarResultsPanel"><h2>Season Results</h2>${renderResultsPanel(state)}</section><section class="glass card"><h2>Latest events</h2><div id="latestEvents"></div></section>`;
  document.querySelectorAll("[data-faction]").forEach(b => b.onclick = () => joinFaction(b.dataset.faction));
  root.onclick = handlePolywarUiClick;
  updateFactionStats();
  updateFactionRanking();
  updateLatestEvents();
  map = new PolyWarMap(state);
  if (selected) map.centerOnBase(18);
  if (state.latest_completed_season) syncPolywarResults();
  startEnergyTimers();
  startWorldCountdownTimer();
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
  if (!state.ok && showErrors) { alert(state.error === "request_timeout" ? "PolyWar is taking too long to initialize. Please retry." : (state.error || "Unable to load PolyWar")); return; }
  opts.soft && map ? softUpdate(state) : render(state);
}
async function joinFaction(id) { const d = await api("/api/polywar/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ faction_id: Number(id) }) }); if (!d.ok) { alert(d.error || "Join failed"); await syncState(false, { soft: true }); return; } render(d); }
async function init() { await telegramAuthIfAvailable(); await syncState(true); }
window.addEventListener("pagehide", () => { clearTimers(); map?.destroy(); map = null; });

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
  seq: 0,
  lastServerTimestamp: 0,
  async refresh(expectedMap = map) {
    const seq = ++this.seq;
    const data = await api('/api/polywar/capitals');
    if (seq !== this.seq || expectedMap !== map || expectedMap?.destroyed) return { ok:false, stale:true };
    const stamp = Number(data.server_timestamp || 0);
    if (data.ok && stamp >= this.lastServerTimestamp) {
      this.lastServerTimestamp = stamp;
      this.cache.clear();
      (data.capitals || []).slice(0, this.max).forEach(c => this.cache.set(`${c.x},${c.y}`, c));
      expectedMap?.requestDraw?.();
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
    const percent = cap.siege_percent ?? Math.floor((Number(cap.siege_progress || 0) * 100) / Math.max(1, Number(cap.siege_required || rules.siege_required || 1)));
    const siegeBtn = cap.canSiege ? '<button data-polywar-action="siege">Siege capital</button>' : '';
    const repairBtn = cap.canRepair ? '<button data-polywar-action="repair_capital">Repair capital</button>' : '';
    return `<section class="glass card polywar-capital-panel"><h3>Capital</h3><p>Original faction: ${esc(cap.original_faction_id)}</p><p>Controller: ${esc(cap.controller_faction_id)}</p><p>Besieging faction: ${esc(cap.besieging_faction_id || 'none')}</p><p>Siege progress: ${esc(cap.siege_progress)}/${esc(cap.siege_required || rules.siege_required)} (${esc(percent)}%)</p><p>Siege cost: terrain + ${esc(rules.siege_extra_energy ?? 0)}</p><p>Repair cost: ${esc(rules.repair_energy_cost ?? 0)}</p><p>Controlled since: ${esc(cap.controlled_since || '—')}</p><p>Captured at: ${esc(cap.captured_at || '—')}</p>${siegeBtn}${repairBtn}</section>`;
  }
};

const polywarGovernanceUi = window.polywarGovernanceUi = window.polywarGovernanceUi || {
  orders: [],
  editingOrderId: null,
  seq: 0,
  lastServerTimestamp: 0,
  async refresh(expectedMap = map) { const seq = ++this.seq; const data = await api('/api/polywar/governance'); if (seq !== this.seq || expectedMap !== map || expectedMap?.destroyed) return {ok:false, stale:true}; const stamp = Number(data.server_timestamp || 0); if (data.ok && stamp >= this.lastServerTimestamp) { this.lastServerTimestamp = stamp; this.orders = data.orders || []; this.render(data); expectedMap?.requestDraw?.(); } return data; },
  setEditingOrder(id, type = 'attack', message = '') { this.editingOrderId = id ? Number(id) : null; const msg = document.getElementById('polywarOrderMessage'); const typ = document.getElementById('polywarOrderType'); const btn = document.querySelector('[data-polywar-update-order]'); const status = document.getElementById('polywarEditingOrder'); if (msg) msg.value = message || ''; if (typ) typ.value = type || 'attack'; if (btn) btn.disabled = !this.editingOrderId; if (status) status.textContent = this.editingOrderId ? `Editing order #${this.editingOrderId}` : 'No order selected for edit'; },
  render(data) {
    const root = document.getElementById('polywarGovernancePanel') || document.querySelector('[data-polywar-governance]');
    if (!root) return;
    const candidates = (data.candidates || []).map(c => `<li><b>${esc(c.user_id)}</b> — ${esc(c.statement || '')} — votes: ${esc(c.vote_count || 0)}<button data-polywar-vote="${c.user_id}">${Number(data.current_user_vote) === Number(c.user_id) ? 'Current vote' : 'Vote / Change vote'}</button></li>`).join('');
    const isCommander = Number(data.commander?.commander_user_id || 0) === Number(currentState?.player?.user_id || 0);
    const orders = (data.orders || []).map(o => `<li><button data-polywar-goto-order="${o.x},${o.y}">${esc(o.order_type)} ${esc(o.x)},${esc(o.y)}</button> ${esc(o.message || '')}${isCommander ? `<button data-polywar-edit-order="${o.id}" data-order-type="${esc(o.order_type)}" data-order-message="${esc(o.message || '')}">Edit</button><button data-polywar-cancel-order="${o.id}">Cancel</button>` : ''}</li>`).join('');
    const controls = isCommander ? `<div class="order-controls"><select id="polywarOrderType"><option value="attack">attack</option><option value="defend">defend</option><option value="rally">rally</option><option value="recon">recon</option><option value="siege">siege</option></select><input id="polywarOrderMessage" maxlength="280" placeholder="Order message"><button data-polywar-create-order="true">Create order</button><button data-polywar-update-order="true" ${this.editingOrderId ? '' : 'disabled'}>Update order</button><span id="polywarEditingOrder">${this.editingOrderId ? `Editing order #${esc(this.editingOrderId)}` : 'No order selected for edit'}</span><span>${esc((data.orders||[]).length)}/${esc(data.rules?.max_orders || '')} active</span></div>` : '';
    root.innerHTML = `<h3>Governance</h3><p>Commander: ${esc(data.commander?.commander_user_id || 'none')}</p><p>Term ends: ${esc(data.commander?.commander_term_ends_at || '—')}</p><p>Election ends: ${esc(data.active_election?.ends_at || '—')}</p><ul>${candidates}</ul><button data-polywar-nominate="true">Nominate myself</button><button data-polywar-nominate="false">Withdraw</button><h4>Orders</h4>${controls}<ul>${orders}</ul>`;
  },
  drawOrders(ctx, worldToScreen) {
    for (const o of this.orders || []) { const p = worldToScreen ? worldToScreen(o.x, o.y) : o; ctx.save(); ctx.strokeStyle = '#fff'; ctx.strokeRect(p.x - 6, p.y - 6, 12, 12); ctx.fillText(o.order_type, p.x + 8, p.y); ctx.restore(); }
  }
};


async function handlePolywarUiClick(e) {
  const vote = e.target.closest('[data-polywar-vote]');
  const nom = e.target.closest('[data-polywar-nominate]');
  const claim = e.target.closest('#polywarClaimReward');
  const action = e.target.closest('[data-polywar-action]');
  const gotoOrder = e.target.closest('[data-polywar-goto-order]');
  const cancelOrder = e.target.closest('[data-polywar-cancel-order]');
  const editOrder = e.target.closest('[data-polywar-edit-order]');
  const createOrder = e.target.closest('[data-polywar-create-order]');
  const updateOrder = e.target.closest('[data-polywar-update-order]');
  const secondary = e.target.closest('[data-polywar-secondary]');
  if (secondary) { await map?.executeSecondaryCellAction?.(secondary.dataset.polywarSecondary); return; }
  if (claim) { const sid=currentState?.current_user_pending_reward?.season_id || currentState?.latest_completed_season?.id || currentState?.results?.season?.id; const d=await claimPolywarReward(sid); if(!d.ok && !d.duplicate) alert(d.error || 'Claim failed'); return; }
  if (action) { const a=action.dataset.polywarAction; if(['seal_rift','support_rebellion','suppress_rebellion'].includes(a)){ /* legacy source token: a==='seal_rift' */ await map?.executeSecondaryCellAction?.(a); return; } await map?.executePrimaryCellAction?.(a); return; }
  if (vote) { const d = await api('/api/polywar/governance/vote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({candidate_user_id:Number(vote.dataset.polywarVote)})}); if(!d.ok) alert(d.error || 'Vote failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (nom) { const active = nom.dataset.polywarNominate === 'true'; const statement = active ? (prompt('Candidate statement') || '') : ''; const d = await api('/api/polywar/governance/nominate', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({active, statement})}); if(!d.ok) alert(d.error || 'Nomination failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (editOrder) { polywarGovernanceUi.setEditingOrder(Number(editOrder.dataset.polywarEditOrder), editOrder.dataset.orderType || 'attack', editOrder.dataset.orderMessage || ''); return; }
  if (createOrder) { const order_id = null; const order_type = document.getElementById('polywarOrderType')?.value || 'attack'; const message = document.getElementById('polywarOrderMessage')?.value || ''; const x = map?.selected?.x, y = map?.selected?.y; const d = await api('/api/polywar/orders', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id, order_type, x, y, message, active:true})}); if(!d.ok) alert(d.error || 'Order save failed'); else { polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (updateOrder) { if (!polywarGovernanceUi.editingOrderId) { alert('Choose an order to edit first'); return; } const order_id = polywarGovernanceUi.editingOrderId; const order_type = document.getElementById('polywarOrderType')?.value || 'attack'; const message = document.getElementById('polywarOrderMessage')?.value || ''; const x = map?.selected?.x, y = map?.selected?.y; const d = await api('/api/polywar/orders', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id, order_type, x, y, message, active:true})}); if(!d.ok) alert(d.error || 'Order save failed'); else { polywarGovernanceUi.setEditingOrder(null); polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (cancelOrder) { const d = await api('/api/polywar/orders', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id:Number(cancelOrder.dataset.polywarCancelOrder), active:false})}); if(!d.ok) alert(d.error || 'Order cancel failed'); else { polywarGovernanceUi.setEditingOrder(null); polywarGovernanceUi.render(d); await map?.refreshGovernance?.(); } return; }
  if (gotoOrder) { const [x,y] = gotoOrder.dataset.polywarGotoOrder.split(',').map(Number); if (map) { map.cx=x; map.cy=y; map.clamp(); map.ensureChunks(); map.ensureSectors(); map.select(x,y); map.requestDraw(); } }
}

init();

window.addEventListener('pagehide', clearTimers);

window.resolvePrimaryCellAction = resolvePrimaryCellAction;
window.resolveSecondaryCellActions = resolveSecondaryCellActions;
window.__polywarTapToAct = { resolvePrimaryCellAction, resolveSecondaryCellActions, primaryActionCost, primaryActionLabel };
