const root = document.getElementById("polywarRoot");
let polywarWorldSeq = 0;
let polywarResultsSeq = 0;
let polywarRewardSeq = 0;
const tg = window.Telegram?.WebApp;
let energyTimer = null;
let syncTimer = null;
let worldCountdownTimer = null;
let presenceTimer = null;
const polywarClaimKeys = new Map();
const polywarActionKeys = new Map();
let currentState = null;
let map = null;
let polywarLastMenuTrigger = null;
let polywarOverviewSeq = 0;
let actionMode = "capture"; // core modes: new Set(["capture", "attack", "reinforce", "siege", "repair_capital"]); legacy checks actionMode === "siege" / actionMode === "repair_capital"
let quickActionsEnabled = localStorage.getItem("polywar_quick_actions") !== "off"; // Quick actions: OFF when persisted off

try { tg?.ready(); tg?.expand(); } catch (_) {}

const TERRAIN_COST = { plain: 1, forest: 1, mountain: 2, swamp: 2, desert: 1, road: 1, ruins: 1, water: null, river: null };
const TERRAIN_COLOR = { plain: "#76a35b", forest: "#20723d", mountain: "#807a73", swamp: "#476a50", desert: "#c7a35a", road: "#b8935a", ruins: "#8d6e92", water: "#245ea8", river: "#39a7d8" };
const TACTICAL_MIN_CELL = 6;
const POLYWAR_VISUALS = {
  defaultCell: 28,
  minCell: 3,
  maxCell: 58,
  baseZoom: 34,
  detailedCell: 14,
  maxBirds: 2,
  terrainDepth: { plain: .14, forest: .25, mountain: .55, swamp: .2, desert: .16, road: .1, ruins: .28, water: -.08, river: -.04 },
  terrainDetailIntensity: .82,
  ownershipOpacity: .28,
  ownershipPatternOpacity: .08,
  borderThickness: 2.2,
  borderGlow: 4,
  contestedPulseMs: 1800,
  contestedStripe: 7,
  selectionGlow: 10,
  selectionPulseMs: 2200,
  selectionAnimationMs: 1400,
  lowPowerAnimations: false,
  minimap: { neutral: "#17243a", grid: "rgba(148,163,184,.13)", contested: "#fbbf24", viewport: "#ecfeff" }
};
function polywarReducedMotion() { return !!window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches; }
function polywarLowPowerMode() { return polywarReducedMotion() || (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2); }
function polywarShouldDrawFactionEdge(owner, neighbor, side) {
  if (neighbor == null || Number(neighbor) === Number(owner)) return false;
  return Number(neighbor) === 0 || side === "right" || side === "bottom";
}
function polywarFactionEdgeStyle(owner, neighbor, ownerColor) {
  return Number(neighbor) > 0
    ? { color: "#dbeafe", glow: "rgba(125,211,252,.45)", frontline: true }
    : { color: ownerColor || "#e2e8f0", glow: ownerColor || "transparent", frontline: false };
}
window.polywarVisualTestHooks = { polywarShouldDrawFactionEdge, polywarFactionEdgeStyle };

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
  const adjacency = selected ? map?.ownedOrthogonalAdjacencyState?.(selected.x, selected.y, fid) : null;
  const ownAdjacent = adjacency === true;
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
    if (adjacency === null) return disabled("attack", "Loading adjacent territory…");
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
  if (adjacency === null) return disabled("capture", "Loading adjacent territory…");
  if (adjacency === false) return disabled("capture", "Capture requires an adjacent faction cell");
  if (!enoughEnergy(state, cost)) return disabled("capture", "Not enough energy");
  return enabled("capture");
}
function toast(message, critical = false) { const old=document.querySelector('.polywar-toast'); old?.remove(); const el=document.createElement('div'); el.className='polywar-toast'; el.textContent=message; document.body.appendChild(el); setTimeout(()=>el.remove(), critical ? 4200 : 1800); }
function humanizePolywarError(error) { return ({not_adjacent:"Capture requires an adjacent faction cell"})[error] || error; }
function actionToast(d, action) { if (d?.mine_hit) return; const labels={capture:'Captured',attack:'Attack progress',reinforce:'Reinforced',siege:'Siege progress',repair_capital:'Capital repaired'}; toast(labels[action] || d?.outcome || 'Done'); }
function shortCellReason(reason) {
  const text = String(reason || "Ready");
  const known = [
    [/already controlled/i, "Already controlled"],
    [/not adjacent|territory is not adjacent/i, "Not adjacent"],
    [/capture requires an adjacent faction cell/i, "Capture requires an adjacent faction cell"],
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
function darkenFactionColor(color, amount = 0.24) {
  const fallback = "#334155";
  const hex = String(color || "").trim();
  const special = hex.toLowerCase();
  if (special === "#000" || special === "#000000" || special === "black") return amount >= 0.35 ? "#111827" : "#20242d";
  if (special === "#fff" || special === "#ffffff" || special === "white") return amount >= 0.35 ? "#4b5563" : "#9ca3af";
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return fallback;
  const n = parseInt(m[1], 16), clamp = v => Math.max(0, Math.min(255, Math.round(v)));
  const f = Math.max(0, Math.min(1, 1 - Number(amount || 0)));
  const r = clamp(((n >> 16) & 255) * f), g = clamp(((n >> 8) & 255) * f), b = clamp((n & 255) * f);
  return `#${[r,g,b].map(v => v.toString(16).padStart(2,"0")).join("")}`;
}
function contrastBorderForFaction(color) {
  const c = darkenFactionColor(color, 0.38).replace("#", "");
  const n = parseInt(c, 16); const lum = (((n>>16)&255)*299 + ((n>>8)&255)*587 + (n&255)*114) / 1000;
  return lum < 90 ? "#e5e7eb" : "#0f172a";
}
function clearTimers() { if (energyTimer) clearInterval(energyTimer); if (syncTimer) clearInterval(syncTimer); if (worldCountdownTimer) clearInterval(worldCountdownTimer); if (presenceTimer) clearInterval(presenceTimer); energyTimer = syncTimer = worldCountdownTimer = presenceTimer = null; }
async function sendPresenceHeartbeat() { if (document.hidden || !document.getElementById("polywarRoot")) return; try { await api("/api/polywar/presence", {method:"POST"}); } catch (e) { console.debug("PolyWar presence heartbeat failed", e); } }
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
  sendPresenceHeartbeat();
  presenceTimer = setInterval(sendPresenceHeartbeat, 60000);
}

class PolyWarMap {
  constructor(state) {
    this.state = state;
    this.canvas = document.getElementById("polywarCanvas");
    this.ctx = this.canvas.getContext("2d");
    this.ambientCanvas = document.getElementById("polywarAmbientCanvas");
    this.ambientCtx = this.ambientCanvas?.getContext("2d") || null;
    this.minimapCanvas = document.getElementById("polywarMinimapCanvas");
    this.minimapCtx = this.minimapCanvas?.getContext("2d") || null;
    this.cache = new Map();
    this.worldSeq = 0; this.riftCache = new Map(); this.rebellionCache = new Map();
    this.sectorCache = new Map();
    this.overview = null; this.overviewError = null; this.worldViewModal = null;
    this.squads = []; this.squadPressure = []; this.squadSeq = 0; this.squadAbort = null; this.squadRefreshTimer = null; this.squadDebounceTimer = null; this.squadOverviewTimer = null; this.lastSquadOverviewRefresh = 0; this.squadAnimations = new Map(); this.squadSupportEnergyCost = 1; this.squadRules = {}; this.serverTimeOffsetMs = 0;
    this.sectorLoading = new Set();
    this.sectorSeq = 0;
    this.loading = new Set();
    this.pendingRequests = new Map();
    this.chunkRequestsByKey = new Map();
    this.failedChunks = new Set();
    this.initialChunksReady = false;
    this.abort = new AbortController();
    this.destroyed = false;
    this.drawFrame = null;
    this.visualAnimationTimer = null;
    this.visualAnimationNeeded = false;
    this.selectionAnimationUntil = 0;
    this.visualLowPower = polywarLowPowerMode();
    this.factionVisualsById = new Map();
    this.loadSeq = 0;
    this.selected = null;
    this.hovered = null;
    this.cell = POLYWAR_VISUALS.defaultCell;
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
    this.rebuildFactionVisualCache();
    this.bind();
    this.resize({ loadData: false });
    this.select(b.x, b.y);
    this.initAmbientLife();
    this.requestDraw();
    this.bootstrapInitialLoad();
  }
  bind() {
    const signal = this.abort.signal;
    this.onResize = () => this.resize();
    window.addEventListener("resize", this.onResize, { signal });
    this.canvas.addEventListener("pointerdown", e => { this.canvas.setPointerCapture(e.pointerId); this.pointerStarts.set(e.pointerId, { x:e.clientX, y:e.clientY, cx:this.cx, cy:this.cy, pan:false }); if (this.pointerStarts.size > 1) this.hadMultiTouch = true; }, { signal });
    this.canvas.addEventListener("pointermove", e => { const g=this.pointerStarts.get(e.pointerId); if (!g) return; const dist=Math.hypot(e.clientX-g.x, e.clientY-g.y); if (dist > 8) g.pan = true; if (this.hadMultiTouch || !g.pan) return; this.cx = g.cx - (e.clientX - g.x) / this.cell; this.cy = g.cy - (e.clientY - g.y) / this.cell; this.clamp(); this.ensureChunks(); this.ensureSectors(); this.scheduleSquadRefreshAfterCameraMove(); this.requestDraw(); }, { signal });
    this.canvas.addEventListener("pointermove", e => { if (this.pointerStarts.size || e.pointerType === "touch") return; const p=this.screenToCell(e.offsetX,e.offsetY), inside=p.x>=0&&p.y>=0&&p.x<this.state.map.width&&p.y<this.state.map.height, next=inside?`${p.x},${p.y}`:null; if (next !== this.hovered) { this.hovered=next; this.requestDraw(); } }, { signal });
    this.canvas.addEventListener("pointerleave", () => { this.hovered=null; this.requestDraw(); }, { signal });
    this.canvas.addEventListener("pointerup", e => { const g=this.pointerStarts.get(e.pointerId); this.pointerStarts.delete(e.pointerId); const wasMulti=this.hadMultiTouch; if (!this.pointerStarts.size) this.hadMultiTouch = false; if (g?.pan) this.scheduleSquadRefreshAfterCameraMove(160); if (g && !g.pan && !wasMulti && this.pointerStarts.size === 0) { const p = this.screenToCell(e.offsetX, e.offsetY); this.handleCellTap(p.x, p.y); } }, { signal });
    this.canvas.addEventListener("pointercancel", e => { this.pointerStarts.delete(e.pointerId); if (!this.pointerStarts.size) this.hadMultiTouch = false; }, { signal });
    this.canvas.addEventListener("wheel", e => { e.preventDefault(); if (e.deltaY < 0) this.zoom(1.25); else this.zoomOutOrOpenWorld(); }, { passive: false, signal });
    document.getElementById("zoomIn").addEventListener("click", () => this.zoom(1.25), { signal });
    document.getElementById("zoomOut").addEventListener("click", () => this.zoomOutOrOpenWorld(), { signal });
    document.getElementById("goBase").addEventListener("click", () => this.centerOnBase(), { signal });
    document.getElementById("openWorldView")?.addEventListener("click", () => this.openWorldView(), { signal });
    document.getElementById("polywarMinimapToggle")?.addEventListener("click", () => this.toggleMinimapCollapse(), { signal });
    if (localStorage.getItem("polywar_minimap_collapsed") === "1") document.querySelector(".polywar-minimap")?.classList.add("is-collapsed");
    this.minimapCanvas?.addEventListener("pointerdown", e => this.handleMinimapPointer(e), { signal });
    document.getElementById("primaryActionBtn")?.addEventListener("click", () => this.executePrimaryCellAction(), { signal });
    document.getElementById("quickActionsToggle")?.addEventListener("click", () => { quickActionsEnabled = !quickActionsEnabled; localStorage.setItem("polywar_quick_actions", quickActionsEnabled ? "on" : "off"); this.updatePanel(); }, { signal });
    document.getElementById("moreActionsBtn")?.addEventListener("click", () => { this.moreOpen = !this.moreOpen; this.updatePanel(); }, { signal });
    this.canvas.tabIndex = 0;
    this.canvas.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && this.selected) { e.preventDefault(); this.executePrimaryCellAction(); } }, { signal });
    document.getElementById("scan3Btn")?.addEventListener("click", () => this.scan(3), { signal });
    document.getElementById("scan5Btn")?.addEventListener("click", () => this.scan(5), { signal });
    document.getElementById("flagAddBtn")?.addEventListener("click", () => this.flag(true), { signal });
    document.getElementById("flagRemoveBtn")?.addEventListener("click", () => this.flag(false), { signal });
    document.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => { actionMode = b.dataset.mode; this.updatePanel(); this.requestDraw(); this.drawMinimap(); }, { signal }));
  }
  destroy() {
    this.destroyed = true;
    if (this.worldViewModal) { this.worldViewModal.remove(); this.worldViewModal = null; }
    this.abort.abort();
    if (this.drawFrame) cancelAnimationFrame(this.drawFrame);
    if (this.visualAnimationTimer) clearTimeout(this.visualAnimationTimer);
    this.visualAnimationTimer = null;
    if (this.squadAbort) this.squadAbort.abort();
    if (this.squadRefreshTimer) clearTimeout(this.squadRefreshTimer);
    if (this.squadDebounceTimer) clearTimeout(this.squadDebounceTimer);
    if (this.squadOverviewTimer) clearTimeout(this.squadOverviewTimer);
    this.drawFrame = null;
    this.loading.clear();
    this.pendingRequests.clear();
    this.chunkRequestsByKey.clear();
    this.sectorLoading.clear();
  }
  updateState(state) { this.state = state; this.rebuildFactionVisualCache(); this.updatePanel(); }
  rebuildFactionVisualCache() { this.factionVisualsById = new Map((this.state?.factions || currentState?.factions || []).map(f => [+f.id, { id:+f.id, color:f.color || "#94a3b8", borderColor:contrastBorderForFaction(f.color) }])); }
  async bootstrapInitialLoad() { await this.ensureChunks(); if (this.destroyed) return; this.initialChunksReady = true; await Promise.allSettled([this.ensureSectors(), this.refreshCapitals(), this.refreshGovernance()]); await this.refreshSquads(); this.loadOverview(); }
  resize({ loadData = true } = {}) { if (this.destroyed) return; this.dpr = Math.max(1, window.devicePixelRatio || 1); const r = this.canvas.getBoundingClientRect(); this.canvas.width = Math.floor(r.width * this.dpr); this.canvas.height = Math.floor(r.height * this.dpr); this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); if (this.ambientCanvas && this.ambientCtx) { this.ambientCanvas.width = this.canvas.width; this.ambientCanvas.height = this.canvas.height; this.ambientCtx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); } this.w = r.width; this.h = r.height; this.clamp(); if (loadData && this.initialChunksReady) { this.ensureChunks(); this.ensureSectors(); } this.requestDraw(); this.drawMinimap(); }
  zoom(f) { this.cell = Math.max(POLYWAR_VISUALS.minCell, Math.min(POLYWAR_VISUALS.maxCell, this.cell * f)); if (this.cell >= TACTICAL_MIN_CELL) this.ensureChunks(); this.ensureSectors(); this.updatePanel(); this.requestDraw(); }
  zoomOutOrOpenWorld() { const nextCell = Math.max(POLYWAR_VISUALS.minCell, Math.min(POLYWAR_VISUALS.maxCell, this.cell * 0.8)); if (nextCell >= TACTICAL_MIN_CELL) return this.zoom(0.8); this.openWorldView({ source: "zoom-out" }); this.updatePanel(); this.requestDraw(); }
  clamp() { this.cx = Math.max(0, Math.min(this.state.map.width - 1, this.cx)); this.cy = Math.max(0, Math.min(this.state.map.height - 1, this.cy)); }
  centerOnBase(zoom = POLYWAR_VISUALS.baseZoom) { const b = baseFor(currentState?.selected_faction?.id); if (!b) return; return this.jumpToWorldPosition(b.x, b.y - 3, zoom, { select: true }); }
  async jumpToWorldPosition(x, y, zoom = 12, options = {}) {
    const seq = ++this.loadSeq;
    x = Math.max(0, Math.min(this.state.map.width - 1, Math.floor(Number(x) || 0)));
    y = Math.max(0, Math.min(this.state.map.height - 1, Math.floor(Number(y) || 0)));
    const targetX = x, targetY = y, chunkSize = this.state.map.chunk_size;
    const centerChunkKey = `${Math.floor(targetX / chunkSize)},${Math.floor(targetY / chunkSize)}`;
    this.cx = targetX; this.cy = targetY;
    this.cell = Math.max(POLYWAR_VISUALS.minCell, Math.min(POLYWAR_VISUALS.maxCell, Number(zoom) || 12));
    this.clamp();
    if (options.select !== false) { const changed=this.selected?.x!==targetX||this.selected?.y!==targetY; this.selected = { x: targetX, y: targetY }; if(changed) this.selectionAnimationUntil=polywarReducedMotion()||this.visualLowPower?0:performance.now()+POLYWAR_VISUALS.selectionAnimationMs; }
    this.removeRetryMap();
    this.status("Loading map…");
    this.updatePanel();
    this.requestDraw();
    this.drawMinimap();
    if (this.cell >= TACTICAL_MIN_CELL) {
      const centerResult = await this.ensureChunks(centerChunkKey, { generation: seq, forceRefresh: false, includeVisible: false });
      if (this.destroyed || seq !== this.loadSeq) return { ok: false, stale: true };
      if (!this.cache.has(centerChunkKey)) { this.updateChunkStatus({ generation: seq }); this.updatePanel(); this.requestDraw(); return { ok: false, error: centerResult?.error || "center_chunk_unavailable" }; }
      this.updatePanel();
      this.requestDraw();
      this.drawMinimap();
      await Promise.allSettled([this.ensureChunks(null, { generation: seq }), this.ensureSectors(), this.refreshSquads(true)]);
    } else {
      await Promise.allSettled([this.ensureSectors(), this.refreshSquads(true)]);
    }
    if (this.destroyed || seq !== this.loadSeq) return { ok: false, stale: true };
    this.updatePanel();
    this.requestDraw();
    this.drawMinimap();
    this.updateChunkStatus({ generation: seq });
    return { ok: true };
  }
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
  visibleFailedChunkKeys() { const visible = new Set(this.visibleChunks().map(([x,y]) => `${x},${y}`)); return [...this.failedChunks].filter(k => visible.has(k) && !this.cache.has(k)); }
  async ensureChunks(forceKey = null, options = {}) {
    if (this.destroyed) return { ok: false, destroyed: true };
    if (forceKey && typeof forceKey === "object" && !Array.isArray(forceKey)) { options = forceKey; forceKey = null; }
    const isCurrentGeneration = () => options.generation == null || options.generation === this.loadSeq;
    const visible = this.visibleChunks();
    const includeVisible = options.includeVisible !== false;
    const forceRefresh = options.forceRefresh ?? !!forceKey;
    if (!forceKey && includeVisible && this.cell < 6) { this.ensureSectors(); return { ok: true, skipped: true }; }
    const explicitKeys = options.keys ? options.keys.map(([x,y]) => [Number(x), Number(y), true]) : (forceKey ? [[...String(forceKey).split(",").map(Number), true]] : []);
    const retryKeys = (!forceKey && includeVisible ? this.visibleFailedChunkKeys().map(k => k.split(",").map(Number)) : []).map(([x,y]) => [x,y,false]);
    const visibleKeys = includeVisible ? visible.map(([x,y]) => [x,y,false]) : [];
    const wanted = [...new Map(explicitKeys.concat(visibleKeys, retryKeys).map(([x,y,forced]) => [`${x},${y}`, [x,y,forced]])).values()];
    const requestedKeys = wanted.map(([x,y]) => `${x},${y}`);
    const chunksToRequest = [], inFlightPromisesToAwait = new Set(), forceAfterInFlight = [];
    for (const [x, y, forced] of wanted) {
      const key = `${x},${y}`;
      const forceRefreshForThisKey = !!(forceRefresh && forced);
      if (this.cache.has(key) && !forceRefreshForThisKey) continue;
      const inFlight = this.chunkRequestsByKey.get(key);
      if (inFlight) {
        inFlightPromisesToAwait.add(inFlight);
        if (forceRefreshForThisKey && !options._skipPostInFlightForceRefresh) forceAfterInFlight.push([x,y]);
      } else {
        chunksToRequest.push([x,y]);
      }
    }
    if (isCurrentGeneration() && (chunksToRequest.length || inFlightPromisesToAwait.size)) this.status("Loading chunks…");
    const limit = Math.max(1, Number(this.state.map.max_chunks_per_request || 9));
    const retryable = new Set(["server_error", "request_timeout", "network_error", "deadlock_retryable"]);
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const createBatchRequest = (batch, batchKey) => {
      const batchKeys = batch.map(c => c.join(","));
      let promiseForThisBatch = null;
      promiseForThisBatch = (async () => {
        batchKeys.forEach(k => this.loading.add(k));
        try {
          let data = null;
          for (let attempt = 0; attempt < 3; attempt++) {
            data = await api("/api/polywar/map/chunks?chunks=" + batch.map(c => c.join(",")).join(";"));
            if (this.destroyed) return data;
            if (options.generation != null && options.generation !== this.loadSeq) { /* stale camera generation may still cache valid chunks below */ }
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
          if (isCurrentGeneration()) { this.updateChunkStatus({ generation: options.generation }); this.requestDraw(); this.updatePanel(); }
          return data;
        } finally {
          batchKeys.forEach(k => {
            this.loading.delete(k);
            if (this.chunkRequestsByKey.get(k) === promiseForThisBatch) this.chunkRequestsByKey.delete(k);
          });
          if (this.pendingRequests.get(batchKey) === promiseForThisBatch) this.pendingRequests.delete(batchKey);
          if (isCurrentGeneration()) this.updateChunkStatus({ generation: options.generation });
        }
      })();
      batchKeys.forEach(key => this.chunkRequestsByKey.set(key, promiseForThisBatch));
      return promiseForThisBatch;
    };
    const tasks = [];
    for (let i = 0; i < chunksToRequest.length && !this.destroyed; i += limit) {
      const batch = chunksToRequest.slice(i, i + limit);
      const key = batch.map(c => c.join(",")).join(";");
      let promise = this.pendingRequests.get(key);
      if (!promise) { promise = createBatchRequest(batch, key); this.pendingRequests.set(key, promise); }
      tasks.push(promise);
    }
    const awaitedInFlight = !!inFlightPromisesToAwait.size;
    const results = await Promise.allSettled([...tasks, ...inFlightPromisesToAwait]);
    if (forceAfterInFlight.length && !this.destroyed) await this.ensureChunks({ keys: forceAfterInFlight, includeVisible: false, forceRefresh: true, generation: options.generation, _skipPostInFlightForceRefresh: true });
    requestedKeys.forEach(key => { if (!this.cache.has(key) && !this.loading.has(key)) this.failedChunks.add(key); });
    if (isCurrentGeneration()) { this.updateChunkStatus({ generation: options.generation }); this.updatePanel(); this.requestDraw(); }
    const allAvailable = requestedKeys.every(key => this.cache.has(key));
    return { ok: allAvailable && results.every(r => r.status === "fulfilled" && r.value?.ok !== false), results, awaitedInFlight, cached: allAvailable && !tasks.length && !awaitedInFlight };
  }

  updateChunkStatus(options = {}) { if (options.generation != null && options.generation !== this.loadSeq) return; const visibleFailed = this.visibleFailedChunkKeys(); const visibleLoading = this.visibleChunks().some(([x,y]) => this.loading.has(`${x},${y}`)); if (visibleLoading) this.status("Loading chunks…"); else if (visibleFailed.length) { this.status("Map data unavailable"); this.showRetryMap(); } else { this.status(""); this.removeRetryMap(); } }

  removeRetryMap() { document.getElementById("retryMapBtn")?.remove(); }

  showRetryMap() {
    let btn = document.getElementById("retryMapBtn");
    if (btn) return;
    const status = document.getElementById("chunkStatus");
    btn = document.createElement("button");
    btn.className = "btn mini"; btn.id = "retryMapBtn"; btn.textContent = "Retry map";
    btn.onclick = async () => { btn.disabled = true; try { await this.ensureChunks(null, { forceRefresh: false, generation: this.loadSeq }); } finally { btn.disabled = false; this.updateChunkStatus({ generation: this.loadSeq }); } };
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
  select(x, y) { if (x < 0 || y < 0 || x >= this.state.map.width || y >= this.state.map.height) return; const changed=this.selected?.x !== x || this.selected?.y !== y; if (this.selected?.x !== x || this.selected?.y !== y) this.moreOpen = false; if(changed) this.selectionAnimationUntil = polywarReducedMotion() || this.visualLowPower ? 0 : performance.now() + POLYWAR_VISUALS.selectionAnimationMs; this.selected = { x, y }; this.ensureChunks(); this.updatePanel(); this.requestDraw(); }
  getCell(x, y) { const cs = this.state.map.chunk_size, cx = Math.floor(x / cs), cy = Math.floor(y / cs), ch = this.cache.get(`${cx},${cy}`); if (!ch) return {}; const lx = x - cx * cs, ly = y - cy * cs; const intel=(ch.intel||[]).find(i=>+i.x===+x&&+i.y===+y); const rift=(ch.rifts||[]).find(r=>+r.x===+x&&+r.y===+y); const rebellion=(ch.rebellions||[]).find(r=>+r.x===+x&&+r.y===+y); const flags=(ch.flags||[]).find(f=>+f.x===+x&&+f.y===+y); const contest=(ch.contested_cells||[]).find(q=>+q.x===+x&&+q.y===+y); const chunkCapital=(ch.capitals||[]).find(q=>+q.x===+x&&+q.y===+y); const cachedCapital=polywarCapitalUi?.cache?.get(`${x},${y}`); const capital=cachedCapital ? {...chunkCapital, ...cachedCapital} : chunkCapital; const orders=(ch.orders||[]).filter(o=>+o.x===+x&&+o.y===+y); return { terrain: ch.terrain?.[ly]?.[lx], owner: ch.owners?.[ly]?.[lx], intel, flags, contest, capital, orders, rift, rebellion }; }
  ownedOrthogonalAdjacencyState(x, y, fid) { let unknown=false; for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1]]){ const nx=x+dx,ny=y+dy; if(nx<0||ny<0||nx>=this.state.map.width||ny>=this.state.map.height) continue; const owner=this.ownerAt(nx,ny); if(owner===Number(fid)) return true; if(owner===null) unknown=true; } return unknown ? null : false; }
  captureAdjacencyState(x, y, fid) { return this.ownedOrthogonalAdjacencyState(x,y,fid); }
  isFrontline(x, y, fid) { return this.ownedOrthogonalAdjacencyState(x,y,fid) === true; }
  drawCaptureFrontierHint(ctx, terrain, owner, rift, p, x, y) { if(this.cell<10 || Number(owner||0)!==0 || terrainEnergyCost(terrain)==null || rift?.status==="active" || this.captureAdjacencyState(x,y,selectedFactionId())!==true) return; const color=this.factionVisualsById.get(selectedFactionId())?.color||"#dbeafe"; ctx.save();ctx.strokeStyle=color;ctx.globalAlpha=.72;ctx.lineWidth=1.25;ctx.strokeRect(p.x+2,p.y+2,Math.max(2,this.cell-4),Math.max(2,this.cell-4));ctx.restore(); }
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
    const loadingCell = !!s && s.x != null && !c.terrain;
    const owner = loadingCell ? "loading" : (c.owner ? ((currentState?.factions || []).find(f => Number(f.id) === Number(c.owner))?.name || `Faction ${c.owner}`) : "Neutral");
    const reason = loadingCell ? "Map data loading" : (this.pending ? "Working…" : shortCellReason(primary.reason || "Ready"));
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
    if (more) { more.setAttribute("aria-expanded", String(this.moreOpen)); more.classList.toggle("is-open", !!this.moreOpen); more.innerHTML = `${this.moreOpen ? 'Less' : 'More'} <span class="more-chevron">▾</span>`; }
    const menu = el("secondaryActionsMenu");
    if (menu) { menu.hidden = !this.moreOpen; menu.innerHTML = this.secondaryActions(c, s); }
    const details = el("cellDetails");
    const squads = this.squadsAt(s.x, s.y), pressures = this.pressureAt(s.x, s.y);
    const squadHtml = squads.length ? squads.map(squad => { const awaiting=squad.status==="awaiting_reinforcement", allied=+squad.faction_id===+(currentState?.selected_faction?.id||0), cost=awaiting?Number(this.squadRules.reinforcement_energy_cost ?? 1):Number(this.squadRules.support_energy_cost ?? this.squadSupportEnergyCost ?? 1); const extra=awaiting?` · Reinforcement in: ${esc(this.reinforcementRemaining(squad))} · Expires: ${esc(squad.expires_at||"—")}`:` · Target: ${esc(squad.target_x ?? "—")},${esc(squad.target_y ?? "—")}`; const btn=allied?`<button class="secondary-action-pill" data-polywar-support-squad="${esc(squad.id)}" data-polywar-support-type="${awaiting?'reinforcement':'heal'}">${awaiting?'Send reinforcement':'Support'} · ${esc(cost)} ⚡</button>`:""; return `<span class="sheet-extra-detail"><b>${esc(this.factionById(squad.faction_id).name||'Faction')} Vanguard</b> HP: ${esc(squad.hp)}/${esc(squad.max_hp)} · Status: ${esc(awaiting?'Awaiting reinforcement':squad.status)}${extra} ${btn}</span>`; }).join("") : "";
    const pressureHtml = pressures.length ? `<span class="sheet-extra-detail">Temporary pressure: ${pressures.map(p=>`${esc(this.factionById(p.faction_id).name||p.faction_id)} ${esc(p.pressure)}%`).join(" · ")}${pressures.some(p=>+p.faction_id===+(currentState?.selected_faction?.id||0)) ? " · Secure position uses normal capture rules" : ""}</span>` : "";
    if (details) details.innerHTML = `<b id="cellOwner" class="cell-owner-line">${esc(owner)}</b><b id="cellCost" class="cell-cost">${primary.energyCost == null ? "—" : `${esc(primary.energyCost)} ⚡`}</b><span class="sheet-extra-detail">${c.capital ? `Capital siege ${esc(c.capital.siege_progress || 0)}/${esc(c.capital.siege_required || currentState?.rules?.capitals?.siege_required || 0)}` : ""}${c.contest ? ` · Contested ${esc(c.contest.contest_progress)}/${esc(c.contest.contest_required)}` : ""}</span>${pressureHtml}${squadHtml}`;
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
      await this.ensureChunks(`${Math.floor(x / this.state.map.chunk_size)},${Math.floor(y / this.state.map.chunk_size)}`, { forceRefresh: false });
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
    if (!d.ok && !d.duplicate) { toast(d.httpStatus === 401 ? "Authentication required" : humanizePolywarError(d.error || "Action failed"), true); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d; }
    polywarActionKeys.delete(keyId); currentState.energy = d.energy || currentState.energy;
    if (d.mine_hit) { this.blast = {x:target.x,y:target.y,t:Date.now()}; alert(`Mine hit — actions locked until ${d.locked_until || d.energy?.locked_until || "server unlock"} (${fmtTime(d.energy?.lock_seconds_remaining || 0)} remaining)`); }
    else { this.lastSuccess = { x:target.x, y:target.y, t:Date.now() }; actionToast(d, actionType); }
    const cs = this.state.map.chunk_size;
    await this.ensureChunks(`${Math.floor(target.x / cs)},${Math.floor(target.y / cs)}`, { forceRefresh: true });
    await this.refreshCapitals(); await this.refreshTargetSector(target); await syncState(false, { soft: true }); await syncPolywarResults().catch(()=>{});
    updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d;
  }
  async refreshCapitals() { const d = await polywarCapitalUi.refresh(this); this.requestDraw(); this.updatePanel(); return d; }
  async refreshGovernance() { const d = await polywarGovernanceUi.refresh(this); this.requestDraw(); return d; }
  scheduleSquadRefreshAfterCameraMove(delay = 250) { if (this.destroyed) return; if (this.squadDebounceTimer) clearTimeout(this.squadDebounceTimer); this.squadDebounceTimer = setTimeout(() => this.refreshSquads(true), delay); }
  refreshSquadOverviewIfDue(force = false) { if (this.destroyed || document.hidden) return; const now=Date.now(); if(!force && this.lastSquadOverviewRefresh && now-this.lastSquadOverviewRefresh<60000) return; if(this.squadOverviewTimer) return; this.squadOverviewTimer=setTimeout(async()=>{ this.squadOverviewTimer=null; if(this.destroyed || document.hidden) return; await this.loadOverview(); }, force ? 0 : 250); }
  visibleBounds(pad = 4) { const a=this.screenToCell(0,0), b=this.screenToCell(this.w,this.h); return {min_x:Math.floor(Math.min(a.x,b.x)-pad), min_y:Math.floor(Math.min(a.y,b.y)-pad), max_x:Math.ceil(Math.max(a.x,b.x)+pad), max_y:Math.ceil(Math.max(a.y,b.y)+pad)}; }
  async refreshSquads(force = false) { if (this.destroyed) return; const now=Date.now(); if(!force && this.lastSquadRefresh && now-this.lastSquadRefresh<20000) return; this.lastSquadRefresh=now; const seq=++this.squadSeq; if(this.squadAbort) this.squadAbort.abort(); this.squadAbort=new AbortController(); const b=this.visibleBounds(12); const qs=new URLSearchParams(b).toString(); try { const d=await api(`/api/polywar/squads/visible?${qs}`, {signal:this.squadAbort.signal}); if(seq!==this.squadSeq || this.destroyed || !d.ok) return; this.squadRules=d.squad_rules||{}; this.squadSimulation={mode:d.simulation_mode||"active",activePlayerCount:Number(d.active_player_count||0),windowMinutes:Number(d.active_player_window_minutes||5)}; /* legacy guard: squadSupportEnergyCost=Number(d.support_energy_cost ?? 1); Support · ${esc(this.squadSupportEnergyCost ?? 1)} ⚡ */ this.squadSupportEnergyCost=Number((d.squad_rules?.support_energy_cost) ?? d.support_energy_cost ?? 1); this.serverTimeOffsetMs=Number(d.server_timestamp||Math.floor(Date.now()/1000))*1000-Date.now(); if(d.squads_enabled===false){ this.squads=[]; this.squadPressure=[]; this.squadAnimations.clear(); if(this.overview){ this.overview.squads_enabled=false; this.overview.squads=[]; this.overview.squad_pressure_bins=[]; } this.requestDraw(); this.drawMinimap(); this.renderOpenWorldView(); this.refreshSquadOverviewIfDue(false); return; } const old=new Map((this.squads||[]).map(s=>[String(s.id),s])); this.squads=d.squads||[]; this.squadPressure=d.pressure||[]; const reduce=polywarReducedMotion()||document.hidden; for(const sq of this.squads){ const prev=old.get(String(sq.id)); if(!reduce && prev && (+prev.x!==+sq.x || +prev.y!==+sq.y) && Math.abs(+prev.x-+sq.x)+Math.abs(+prev.y-+sq.y)===1) this.squadAnimations.set(String(sq.id), {fromX:+prev.x,fromY:+prev.y,toX:+sq.x,toY:+sq.y,start:performance.now(),duration:600}); } this.requestDraw(); this.refreshSquadOverviewIfDue(false); } catch(e) { if(e?.name !== "AbortError") console.warn("PolyWar squad refresh failed", e); } finally { if(!this.destroyed) { if(this.squadRefreshTimer) clearTimeout(this.squadRefreshTimer); this.squadRefreshTimer=setTimeout(()=>this.refreshSquads(false), 25000); } } }
  squadsAt(x,y){ return (this.squads||[]).filter(s=>+s.x===+x&&+s.y===+y); }
  squadAt(x,y){ const all=this.squadsAt(x,y); return all.find(s=>s.status!=="awaiting_reinforcement") || all[0]; }
  reinforcementRemaining(sq){ if(!sq?.reinforcement_at) return "—"; const ms=new Date(sq.reinforcement_at).getTime()-(Date.now()+this.serverTimeOffsetMs); return fmtTime(Math.max(0, Math.floor(ms/1000))); }
  expirationRemaining(sq){ if(!sq?.expires_at) return "—"; const ms=new Date(sq.expires_at).getTime()-(Date.now()+this.serverTimeOffsetMs); return fmtTime(Math.max(0, Math.floor(ms/1000))); }
  pressureAt(x,y){ return (this.squadPressure||[]).filter(p=>+p.x===+x&&+p.y===+y); }
  drawSquadPressure(ctx){ for(const p of this.squadPressure||[]){ const f=this.factionById(p.faction_id); const pos=this.cellToScreen(+p.x,+p.y); if(pos.x+this.cell<0||pos.y+this.cell<0||pos.x>this.w||pos.y>this.h) continue; ctx.globalAlpha=Math.max(.25,Math.min(.65,(+p.pressure||0)/100*.65)); ctx.fillStyle=darkenFactionColor(f.color, .24); ctx.fillRect(pos.x+1,pos.y+1,Math.max(1,this.cell-2),Math.max(1,this.cell-2)); ctx.globalAlpha=1; const all=this.pressureAt(+p.x,+p.y); if(all.length>1){ ctx.strokeStyle=darkenFactionColor(this.factionById(all[1].faction_id).color,.24); ctx.beginPath(); ctx.moveTo(pos.x,pos.y+this.cell); ctx.lineTo(pos.x+this.cell,pos.y); ctx.stroke(); } } }
  drawSquads(ctx){ const now=performance.now(); let anim=false; const sorted=[...(this.squads||[])].sort((a,b)=>(a.status==="awaiting_reinforcement")-(b.status==="awaiting_reinforcement")); for(const sq of sorted){ let x=+sq.x,y=+sq.y; const awaiting=sq.status==="awaiting_reinforcement", attacking=sq.status==="attacking_cell", capital=sq.status==="pressuring_capital"; const a=this.squadAnimations.get(String(sq.id)); if(a && !awaiting && !polywarReducedMotion() && !document.hidden){ const t=Math.min(1,(now-a.start)/a.duration); x=a.fromX+(a.toX-a.fromX)*t; y=a.fromY+(a.toY-a.fromY)*t; if(t<1) anim=true; else this.squadAnimations.delete(String(sq.id)); } const same=this.squadsAt(+sq.x,+sq.y); const offset=awaiting && same.some(o=>o.status!=="awaiting_reinforcement") ? this.cell*.18 : 0; const f=this.factionById(sq.faction_id); const p=this.cellToScreen(x,y), cx=p.x+this.cell/2+offset, cy=p.y+this.cell/2+offset, r=Math.max(3,Math.min(7,this.cell*.18)); ctx.save(); if(attacking && !polywarReducedMotion() && !document.hidden){ anim=true; ctx.globalAlpha=.35+.25*Math.sin(now/120); ctx.strokeStyle=contrastBorderForFaction(f.color); ctx.lineWidth=2; ctx.beginPath(); ctx.arc(cx,cy,r*3,0,Math.PI*2); ctx.stroke(); ctx.globalAlpha=1; } if(capital){ ctx.strokeStyle="#f59e0b"; ctx.lineWidth=2; ctx.beginPath(); ctx.rect(cx-r*2,cy-r*2,r*4,r*4); ctx.stroke(); } if(awaiting){ ctx.globalAlpha=.45; ctx.strokeStyle=contrastBorderForFaction(f.color); ctx.fillStyle=darkenFactionColor(f.color,.62); ctx.lineWidth=1.5; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.arc(cx,cy,r*2.2,0,Math.PI*2); ctx.stroke(); ctx.setLineDash([]); ctx.beginPath(); ctx.arc(cx,cy,r*1.4,0,Math.PI*2); ctx.stroke(); ctx.beginPath(); ctx.moveTo(cx-r,cy); ctx.lineTo(cx+r,cy); ctx.moveTo(cx,cy-r); ctx.lineTo(cx,cy+r); ctx.stroke(); } else { ctx.fillStyle=darkenFactionColor(f.color,.38); ctx.strokeStyle=contrastBorderForFaction(f.color); ctx.lineWidth=1.5; for(const [dx,dy] of [[0,-r],[-r,r],[r,r]]){ ctx.beginPath(); ctx.arc(cx+dx,cy+dy,r,0,Math.PI*2); ctx.fill(); ctx.stroke(); } } ctx.globalAlpha=1; ctx.fillStyle=contrastBorderForFaction(f.color); ctx.font=`${Math.max(8,Math.min(12,this.cell*.32))}px sans-serif`; ctx.textAlign="center"; ctx.fillText(this.factionLabel(f), cx, cy-r-3); const hp=Math.max(0,Math.min(1,(+sq.hp||0)/(+sq.max_hp||1))); ctx.fillStyle="rgba(15,23,42,.85)"; ctx.fillRect(p.x+2,p.y+this.cell-5,this.cell-4,3); ctx.fillStyle=hp<=0?'rgba(148,163,184,.55)':hp>.5?'#22c55e':hp>=.25?'#eab308':'#ef4444'; ctx.fillRect(p.x+2,p.y+this.cell-5,Math.max(0,(this.cell-4)*hp),3); ctx.restore(); } if(anim) this.requestDraw(); }
  async supportSelectedSquad(id){ const key=`${currentState?.season?.id}:support_squad:${id}`; const idem=polywarActionKeys.get(key)||`${key}:${Date.now()}:${Math.random().toString(16).slice(2)}`; polywarActionKeys.set(key, idem); const d=await api(`/api/polywar/squads/${id}/support`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({idempotency_key:idem,support_type:(document.querySelector(`[data-polywar-support-squad="${id}"]`)?.dataset?.polywarSupportType)||"auto"})}); if(!d.ok){ toast(d.error||"Support failed", true); return d; } polywarActionKeys.delete(key); currentState.energy=d.energy||currentState.energy; toast(d.support_type==="reinforcement"?"Reinforcement sent":"Squad supported"); const selectedBefore=this.selected?{...this.selected}:null; await this.refreshSquads(true); if(selectedBefore) this.selected=selectedBefore; this.refreshSquadOverviewIfDue(true); updateEnergyUI(); this.updatePanel(); refreshOpenPolywarMenu(); updateSharedCountdowns(); return d; }
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
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`, { forceRefresh: true }); await this.refreshWorld(); await syncPolywarResults().catch(()=>{}); updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw(); return d;
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
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`, { forceRefresh: true }); updateEnergyUI(); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw();
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
    const cs=this.state.map.chunk_size; await this.ensureChunks(`${Math.floor(target.x/cs)},${Math.floor(target.y/cs)}`, { forceRefresh: true }); if (selectedKey(this.selected) === target.key) this.updatePanel(); this.requestDraw();
  }
  lodLevel() { if (this.cell >= 14) return 0; if (this.cell >= 6) return 1; if (this.cell >= 3) return 2; return 3; }
  async loadOverview() {
    this.lastSquadOverviewRefresh = Date.now();
    const seq = ++polywarOverviewSeq;
    try {
      const data = await api('/api/polywar/world/overview');
      if (seq !== polywarOverviewSeq || this.destroyed) return;
      if (!data?.ok) {
        this.overview = null;
        this.overviewError = data?.error || 'overview_failed';
        this.drawMinimap();
        this.renderOpenWorldView();
        return;
      }
      if (Number(data.season_id) !== Number(currentState?.season?.id)) {
        this.overview = null;
        this.overviewError = 'stale_overview';
        this.drawMinimap();
        this.renderOpenWorldView();
        return;
      }
      this.overview = data;
      this.overviewError = null;
      this.drawMinimap();
      this.renderOpenWorldView();
    } catch (e) {
      if (seq !== polywarOverviewSeq || this.destroyed) return;
      this.overview = null;
      this.overviewError = e?.message || 'overview_failed';
      this.drawMinimap();
      this.renderOpenWorldView();
    }
  }
  overviewTransform(canvas, overview=this.overview) { const r=canvas.getBoundingClientRect(), pad=10, scale=Math.min((r.width-pad*2)/overview.world.width,(r.height-pad*2)/overview.world.height), renderedWidth=overview.world.width*scale, renderedHeight=overview.world.height*scale, ox=(r.width-renderedWidth)/2, oy=(r.height-renderedHeight)/2; return {cssWidth:r.width,cssHeight:r.height,padding:pad,scale,ox,oy,renderedWidth,renderedHeight}; }
  overviewPointerToWorld(canvas, clientX, clientY, clamp=false) { const ov=this.overview; if(!ov||!canvas) return null; const t=this.overviewTransform(canvas, ov), r=canvas.getBoundingClientRect(), px=clientX-r.left, py=clientY-r.top; if(!clamp && (px<t.ox || py<t.oy || px>t.ox+t.renderedWidth || py>t.oy+t.renderedHeight)) return null; const x=(Math.max(t.ox,Math.min(t.ox+t.renderedWidth,px))-t.ox)/t.scale, y=(Math.max(t.oy,Math.min(t.oy+t.renderedHeight,py))-t.oy)/t.scale; return {x:Math.max(0,Math.min(ov.world.width-1,x)), y:Math.max(0,Math.min(ov.world.height-1,y)), transform:t}; }
  nearestHqAt(canvas, clientX, clientY, radiusPx) { const ov=this.overview; if(!ov) return null; const t=this.overviewTransform(canvas, ov), r=canvas.getBoundingClientRect(), px=clientX-r.left, py=clientY-r.top; let best=null, bd=radiusPx*radiusPx; for(const h of ov.hq||[]){ const sx=t.ox+h.x*t.scale, sy=t.oy+h.y*t.scale, d=(sx-px)**2+(sy-py)**2; if(d<=bd){bd=d; best=h;} } return best; }
  nearestOverviewSquadAt(canvas, clientX, clientY, radiusPx=18) { const ov=this.overview; if(!ov) return null; const t=this.overviewTransform(canvas, ov), r=canvas.getBoundingClientRect(), px=clientX-r.left, py=clientY-r.top; let best=null, bd=radiusPx*radiusPx; for(const sq of ov.squads||[]){ const sx=t.ox+(+sq.x)*t.scale, sy=t.oy+(+sq.y)*t.scale, d=(sx-px)**2+(sy-py)**2; if(d<=bd){bd=d; best=sq;} } return best; }
  factionById(id) { return (this.overview?.factions || []).find(f => +f.id === +id) || {}; }
  factionLabel(f) { const n=String(f?.name||'F'); return (n.match(/[A-ZА-Я]/g)||[n[0]||'F']).slice(0,2).join('').toUpperCase(); }
  drawStrategicMarker(ctx, x, y, color, label, { player=false, capital=false, siege=0, border='#ffffff', hollow=false, dimmed=false } = {}) { const r=player?8:capital?7:6; ctx.save(); if(player){ ctx.shadowColor=color; ctx.shadowBlur=10; } ctx.beginPath(); for(let i=0;i<6;i++){ const a=Math.PI/6+i*Math.PI/3; const px=x+Math.cos(a)*r, py=y+Math.sin(a)*r; i?ctx.lineTo(px,py):ctx.moveTo(px,py); } ctx.closePath(); ctx.globalAlpha=dimmed?.55:1; if(hollow){ ctx.globalAlpha=.45; ctx.setLineDash([3,2]); ctx.fillStyle='rgba(148,163,184,.10)'; ctx.fill(); } else { ctx.fillStyle=color||'#e5e7eb'; ctx.fill(); } ctx.shadowBlur=0; ctx.lineWidth=player?2.4:1.4; ctx.strokeStyle=border; ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha=1; ctx.fillStyle=(color||'').toLowerCase()==='#111827'?'#fff':'#08111f'; ctx.font=`700 ${capital?8:7}px system-ui`; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillText(label||'•',x,y+.4); if(siege){ ctx.beginPath(); ctx.strokeStyle='rgba(250,204,21,.95)'; ctx.lineWidth=2; ctx.arc(x,y,r+4,-Math.PI/2,-Math.PI/2+Math.PI*2*Math.max(0,Math.min(1,siege)),false); ctx.stroke(); } ctx.restore(); }
  drawViewportRect(ctx, t, scale, ox, oy) { const a=this.screenToCell(0,0), b=this.screenToCell(this.w,this.h); const x1=Math.max(0,Math.min(a.x,b.x)), y1=Math.max(0,Math.min(a.y,b.y)), x2=Math.min(this.state.map.width,Math.max(a.x,b.x)), y2=Math.min(this.state.map.height,Math.max(a.y,b.y)); let rx=ox+x1*scale, ry=oy+y1*scale, rw=Math.max(5,(x2-x1)*scale), rh=Math.max(5,(y2-y1)*scale); rx=Math.max(ox,Math.min(ox+t.renderedWidth-rw,rx)); ry=Math.max(oy,Math.min(oy+t.renderedHeight-rh,ry)); ctx.fillStyle='rgba(34,211,238,.14)'; ctx.strokeStyle=POLYWAR_VISUALS.minimap.viewport; ctx.lineWidth=1.5; ctx.fillRect(rx,ry,rw,rh); ctx.strokeRect(rx,ry,rw,rh); }
  drawOverviewCanvas(canvas) { const ov=this.overview, ctx=canvas?.getContext('2d'); if(!ov||!ctx) return; const dpr=Math.min(2,window.devicePixelRatio||1), t=this.overviewTransform(canvas, ov); canvas.width=Math.max(1,Math.floor(t.cssWidth*dpr)); canvas.height=Math.max(1,Math.floor(t.cssHeight*dpr)); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,t.cssWidth,t.cssHeight); const {scale,ox,oy}=t, factions=ov.factions||[]; const grad=ctx.createLinearGradient(0,0,t.cssWidth,t.cssHeight); grad.addColorStop(0,'rgba(20,34,56,.96)'); grad.addColorStop(1,'rgba(7,16,31,.96)'); ctx.fillStyle=grad; ctx.fillRect(0,0,t.cssWidth,t.cssHeight); ctx.fillStyle=POLYWAR_VISUALS.minimap.neutral; ctx.fillRect(ox,oy,t.renderedWidth,t.renderedHeight); const wpcol=ov.overview_grid.world_per_column, wprow=ov.overview_grid.world_per_row; for(const cell of ov.overview_grid.cells||[]){ const f=factions.find(q=>+q.id===+cell.controller_faction_id); if(!f) continue; const x=ox+cell.grid_x*wpcol*scale,y=oy+cell.grid_y*wprow*scale,w=Math.ceil(wpcol*scale)+1,h=Math.ceil(wprow*scale)+1; ctx.globalAlpha=Math.max(.22,Math.min(.82,(cell.dominance_percent||35)/100)); ctx.fillStyle=f.color; ctx.fillRect(x,y,w,h); ctx.globalAlpha=1; if(cell.is_contested){ ctx.strokeStyle=POLYWAR_VISUALS.minimap.contested; ctx.lineWidth=1; ctx.strokeRect(x+.5,y+.5,Math.max(1,w-1),Math.max(1,h-1)); ctx.beginPath(); ctx.strokeStyle='rgba(15,23,42,.55)'; for(let hx=x-w; hx<x+w; hx+=6){ ctx.moveTo(hx,y+h); ctx.lineTo(hx+h,y); } ctx.stroke(); }} ctx.strokeStyle=POLYWAR_VISUALS.minimap.grid; ctx.lineWidth=1; const step=Math.max(8, ov.world.sector_size*scale*4); for(let gx=ox;gx<=ox+t.renderedWidth;gx+=step){ctx.beginPath();ctx.moveTo(gx,oy);ctx.lineTo(gx,oy+t.renderedHeight);ctx.stroke();} for(let gy=oy;gy<=oy+t.renderedHeight;gy+=step){ctx.beginPath();ctx.moveTo(ox,gy);ctx.lineTo(ox+t.renderedWidth,gy);ctx.stroke();} for(const z of ov.starting_zones||[]){ const f=this.factionById(z.faction_id); ctx.strokeStyle=f.color||'rgba(255,255,255,.5)'; ctx.globalAlpha=.8; ctx.setLineDash([3,2]); ctx.strokeRect(ox+z.min_x*scale,oy+z.min_y*scale,Math.max(2,(z.max_x-z.min_x)*scale),Math.max(2,(z.max_y-z.min_y)*scale)); ctx.setLineDash([]); ctx.globalAlpha=1; } for(const p of ov.squad_pressure_bins||[]){ const f=this.factionById(p.faction_id); const x=ox+p.grid_x*wpcol*scale,y=oy+p.grid_y*wprow*scale,w=Math.ceil(wpcol*scale)+1,h=Math.ceil(wprow*scale)+1; ctx.globalAlpha=Math.max(.25,Math.min(.65,(p.pressure||0)/100*.65)); ctx.fillStyle=darkenFactionColor(f.color,.24); ctx.fillRect(x,y,w,h); ctx.globalAlpha=1; if(p.is_contested){ ctx.strokeStyle=contrastBorderForFaction(f.color); ctx.strokeRect(x,y,w,h); }} for(const sq of ov.squads||[]){ const f=this.factionById(sq.faction_id); this.drawStrategicMarker(ctx,ox+sq.x*scale,oy+sq.y*scale,darkenFactionColor(f.color,sq.status==="awaiting_reinforcement"?.62:.38),this.factionLabel(f),{border:contrastBorderForFaction(f.color),hollow:sq.status==="awaiting_reinforcement",dimmed:sq.status==="awaiting_reinforcement"}); } for(const c of ov.capitals||[]){ const orig=this.factionById(c.original_faction_id), ctrl=this.factionById(c.controller_faction_id); this.drawStrategicMarker(ctx,ox+c.x*scale,oy+c.y*scale,ctrl.color||orig.color||'#fff',this.factionLabel(orig),{capital:true,border:orig.color||'#fff',siege:(c.siege_progress||0)/1000}); } for(const h of ov.hq||[]){ const f=this.factionById(h.faction_id); this.drawStrategicMarker(ctx,ox+h.x*scale,oy+h.y*scale,h.color||f.color||'#fff',this.factionLabel(f),{player:+h.faction_id===+(currentState?.selected_faction?.id||0),border:'#f8fafc'}); } for(const o of ov.major_objects||[]){ const x=ox+o.x*scale,y=oy+o.y*scale; ctx.fillStyle=o.status==='active'?'#f43f5e':'#94a3b8'; ctx.beginPath(); ctx.moveTo(x,y-5); ctx.lineTo(x+5,y); ctx.lineTo(x,y+5); ctx.lineTo(x-5,y); ctx.closePath(); ctx.fill(); } this.drawViewportRect(ctx,t,scale,ox,oy); }
  drawMinimap() { if(!this.minimapCanvas) return; this.drawOverviewCanvas(this.minimapCanvas); }
  toggleMinimapCollapse() { const el=document.querySelector(".polywar-minimap"); if(!el) return; el.classList.toggle("is-collapsed"); localStorage.setItem("polywar_minimap_collapsed", el.classList.contains("is-collapsed") ? "1" : "0"); }
  async handleMinimapPointer(e) { e.preventDefault(); e.stopPropagation(); this.minimapCanvas.setPointerCapture?.(e.pointerId); if(!this.overview) { this.loadOverview(); return; } const h=this.nearestHqAt(this.minimapCanvas,e.clientX,e.clientY,16); if(h) return await this.jumpToWorldPosition(h.x,h.y,POLYWAR_VISUALS.baseZoom,{select:true}); const p=this.overviewPointerToWorld(this.minimapCanvas,e.clientX,e.clientY,false); if(p) return await this.jumpToWorldPosition(p.x,p.y,10,{select:true}); }
  renderWorldTargetSelection(target, selection) { if(!target || !selection) return; const x=Math.floor(selection.x), y=Math.floor(selection.y), dist=Math.abs(x-Math.floor(this.cx))+Math.abs(y-Math.floor(this.cy)), sectors=Math.ceil(dist/Math.max(1, this.overview?.world?.sector_size || this.state?.map?.sector_size || 40)); if(selection.squad){ const sq=selection.squad, awaiting=sq.status==="awaiting_reinforcement", f=this.factionById(sq.faction_id); const detail=awaiting?`Reinforcement in: ${esc(this.reinforcementRemaining(sq))}<br>Return anchor: ${esc(sq.supply_x??"—")}, ${esc(sq.supply_y??"—")}<br>Expires in: ${esc(this.expirationRemaining(sq))}`:`${sq.status==="attacking_cell"?`Attacking: ${esc(sq.attack_target_x??"—")},${esc(sq.attack_target_y??"—")} / ${esc(sq.attack_progress_required??100)} (${esc(sq.attack_progress??0)})`:sq.status==="pressuring_capital"?`Capital pressure: ${esc(sq.attack_progress??0)}`:`Target: ${esc(sq.target_x??"—")},${esc(sq.target_y??"—")}`}`; target.innerHTML=`<b>${esc(f.name||"Faction")} Vanguard</b><br>HP: ${esc(sq.hp)} / ${esc(sq.max_hp)}<br>Status: ${esc(awaiting?"Awaiting reinforcement":sq.status)}<br>${detail}<br><button class="btn mini" data-open-tactical>Open Tactical Map</button>`; target.querySelector('[data-open-tactical]').onclick=()=>{ const modal=this.worldViewModal; if(modal) modal.remove(); this.worldViewModal=null; this.jumpToWorldPosition(+sq.x,+sq.y,10,{select:true}); this.refreshSquads(true); }; return; } const title=selection.hq?esc(selection.hq.name||'HQ'):selection.capital?esc(selection.capital.name||'Capital'):esc(selection.controller||'Strategic target'); const status=selection.capitalStatus?`<br>Capital: ${esc(selection.capitalStatus)}`:''; const controlled=Number.isFinite(selection.controlledSectors)?`<br>Controlled sectors: ${selection.controlledSectors}`:''; const contested=Number.isFinite(selection.contestedSectors)?`<br>Contested sectors: ${selection.contestedSectors}`:''; target.innerHTML=`<b>${title}</b><br>Coordinates: ${x},${y}<br>Grid distance: ${dist} cells<br>Approx. sectors: ${sectors}${status}${controlled}${contested}<br><button class="btn mini" data-open-tactical>Open Tactical Map</button>`; target.querySelector('[data-open-tactical]').onclick=()=>{ const modal=this.worldViewModal; if(modal) modal.remove(); this.worldViewModal=null; this.jumpToWorldPosition(x,y,selection.hq?POLYWAR_VISUALS.baseZoom:10,{select:true}); }; }
  selectWorldTarget(target, data) { this.worldTargetSelection=data; this.renderWorldTargetSelection(target, data); }
  renderOpenWorldView() { const modal=this.worldViewModal; if(!modal || !document.body.contains(modal)) return; const canvas=modal.querySelector('canvas'), target=modal.querySelector('.world-target'); if(this.overview){ if(!this.worldTargetSelection) target.textContent='Tap a HQ, capital, object or territory to inspect it.'; this.drawOverviewCanvas(canvas); } else if(this.overviewError){ target.innerHTML='<b>Overview failed.</b> <button class="btn mini" data-retry>Retry</button>'; target.querySelector('[data-retry]').onclick=()=>{ this.overviewError=null; target.textContent='Loading World View…'; this.loadOverview(); }; } else { target.textContent='Loading World View…'; } }
  openWorldView() { if (this.worldViewModal && document.body.contains(this.worldViewModal)) { this.refreshSquadOverviewIfDue(true); this.renderOpenWorldView(); return; } if (this.worldViewModal) { this.worldViewModal.remove(); this.worldViewModal = null; } this.worldTargetSelection=null; this.refreshSquadOverviewIfDue(!this.overview || Date.now()-(this.lastSquadOverviewRefresh||0)>60000); const modal=document.createElement('div'); this.worldViewModal=modal; modal.className='polywar-world-view'; modal.innerHTML='<div class="world-view-panel"><header><b>World View</b><button class="btn mini" data-myhq>My HQ</button><button class="btn mini" data-close>Close</button></header><canvas class="polywar-world-canvas"></canvas><div class="world-target muted">Loading World View…</div></div>'; document.body.appendChild(modal); const canvas=modal.querySelector('canvas'); const target=modal.querySelector('.world-target'); modal.querySelector('[data-close]').onclick=()=>{ modal.remove(); if(this.worldViewModal===modal) this.worldViewModal=null; }; modal.querySelector('[data-myhq]').onclick=()=>{ const b=baseFor(currentState?.selected_faction?.id); if(b){ this.selectWorldTarget(target,{x:b.x,y:b.y,hq:{name:'My HQ'},capitalStatus:'Active'}); }}; canvas.addEventListener('pointerdown', e=>{ e.preventDefault(); e.stopPropagation(); if(!this.overview) return; const sq=this.nearestOverviewSquadAt(canvas,e.clientX,e.clientY,22); const h=sq?null:this.nearestHqAt(canvas,e.clientX,e.clientY,22); const p=sq?{x:+sq.x,y:+sq.y,squad:sq}:h?{x:h.x,y:h.y,hq:h,capitalStatus:'Active'}:this.overviewPointerToWorld(canvas,e.clientX,e.clientY,false); if(!p) return; if(!h){ const gx=Math.floor(p.x/(this.overview.overview_grid.world_per_column||1)), gy=Math.floor(p.y/(this.overview.overview_grid.world_per_row||1)); const cell=(this.overview.overview_grid.cells||[]).find(c=>c.grid_x===gx&&c.grid_y===gy); const f=this.factionById(cell?.controller_faction_id); p.controller=f.name||'Neutral territory'; p.controlledSectors=cell?.controlled_sector_count; p.contestedSectors=cell?.is_contested?1:0; } this.selectWorldTarget(target,p); }); requestAnimationFrame(()=>this.renderOpenWorldView()); }

  visibleCellBounds() { const a=this.screenToCell(0,0), b=this.screenToCell(this.w,this.h); return {minX:Math.max(0,Math.floor(Math.min(a.x,b.x))-1), maxX:Math.min(this.state.map.width-1,Math.ceil(Math.max(a.x,b.x))+1), minY:Math.max(0,Math.floor(Math.min(a.y,b.y))-1), maxY:Math.min(this.state.map.height-1,Math.ceil(Math.max(a.y,b.y))+1)}; }
  drawTerrainTile(ctx, terrain, p, x, y) {
    const cell = this.cell, depth = POLYWAR_VISUALS.terrainDepth[terrain] ?? .12, base = TERRAIN_COLOR[terrain] || "#555";
    const configuredIntensity=Math.max(0,Math.min(1,Number(POLYWAR_VISUALS.terrainDetailIntensity)||0)), intensity=configuredIntensity*(this.visualLowPower?.55:1);
    ctx.save();
    ctx.fillStyle = base;
    ctx.fillRect(p.x, p.y, cell + .6, cell + .6);
    if (cell >= POLYWAR_VISUALS.detailedCell) {
      const shade = Math.max(0, depth);
      ctx.fillStyle = `rgba(255,255,255,${(.08 + shade * .18)*intensity})`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + cell, p.y); ctx.lineTo(p.x, p.y + cell); ctx.closePath(); ctx.fill();
      ctx.fillStyle = `rgba(0,0,0,${(.08 + Math.abs(depth) * .22)*intensity})`;
      ctx.beginPath(); ctx.moveTo(p.x + cell, p.y); ctx.lineTo(p.x + cell, p.y + cell); ctx.lineTo(p.x, p.y + cell); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,.08)"; ctx.strokeRect(p.x + 1, p.y + 1, Math.max(2, cell - 2), Math.max(2, cell - 2));
      if (terrain === "mountain") this.drawMountainRelief(ctx, p, intensity);
      else if (terrain === "forest") { ctx.fillStyle = `rgba(7,45,24,${.32*intensity})`; ctx.beginPath(); ctx.arc(p.x + cell*.62, p.y + cell*.38, cell*.18, 0, Math.PI*2); ctx.fill(); }
      else if (terrain === "water" || terrain === "river") { ctx.strokeStyle = `rgba(180,235,255,${.22*intensity})`; ctx.beginPath(); ctx.moveTo(p.x + cell*.16, p.y + cell*.58); ctx.quadraticCurveTo(p.x + cell*.5, p.y + cell*.45, p.x + cell*.84, p.y + cell*.58); ctx.stroke(); }
      else if (terrain === "road") { this.drawRoadBevel(ctx, p, intensity); }
    }
    ctx.restore();
  }
  drawRoadBevel(ctx, p, intensity=1) {
    const c = this.cell;
    ctx.strokeStyle = `rgba(255,235,188,${.22*intensity})`; ctx.lineWidth = 1; ctx.strokeRect(p.x + c*.16, p.y + c*.2, c*.68, c*.6);
    ctx.strokeStyle = `rgba(52,32,18,${.2*intensity})`; ctx.beginPath(); ctx.moveTo(p.x + c*.16, p.y + c*.8); ctx.lineTo(p.x + c*.84, p.y + c*.8); ctx.stroke();
  }
  drawMountainRelief(ctx, p, intensity=1) {
    const c = this.cell;
    ctx.fillStyle = `rgba(40,38,42,${.42*intensity})`; ctx.beginPath(); ctx.moveTo(p.x+c*.18,p.y+c*.78); ctx.lineTo(p.x+c*.52,p.y+c*.16); ctx.lineTo(p.x+c*.86,p.y+c*.78); ctx.closePath(); ctx.fill();
    ctx.fillStyle = `rgba(255,255,255,${.24*intensity})`; ctx.beginPath(); ctx.moveTo(p.x+c*.52,p.y+c*.16); ctx.lineTo(p.x+c*.36,p.y+c*.58); ctx.lineTo(p.x+c*.58,p.y+c*.48); ctx.closePath(); ctx.fill();
    ctx.fillStyle = `rgba(0,0,0,${.22*intensity})`; ctx.beginPath(); ctx.moveTo(p.x+c*.52,p.y+c*.16); ctx.lineTo(p.x+c*.86,p.y+c*.78); ctx.lineTo(p.x+c*.58,p.y+c*.48); ctx.closePath(); ctx.fill();
  }
  initAmbientLife() {
    const seed = (this.state?.season?.id || 1) * 97;
    this.birds = polywarReducedMotion() ? [] : Array.from({ length: polywarLowPowerMode() ? 1 : POLYWAR_VISUALS.maxBirds }, (_, i) => ({
      start: seed * 13 + i * 4200,
      duration: 9000 + i * 1700,
      y: .18 + i * .24,
      scale: .7 + i * .15
    }));
  }
  drawFeature(ctx, feature, p, owner) {
    if (!feature || this.cell < 9) return;
    const c = this.cell, cx = p.x + c / 2, cy = p.y + c / 2, type = feature.type;
    ctx.save();
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    if (type === "village" || type === "city" || type === "house") {
      ctx.fillStyle = type === "city" ? "#d9c38a" : "#c9a46b"; ctx.fillRect(p.x + c*.25, p.y + c*.35, c*.5, c*.38);
      ctx.fillStyle = "rgba(80,45,28,.75)"; ctx.beginPath(); ctx.moveTo(p.x+c*.2,p.y+c*.38); ctx.lineTo(cx,p.y+c*.14); ctx.lineTo(p.x+c*.8,p.y+c*.38); ctx.closePath(); ctx.fill();
      if (type === "city") { ctx.fillStyle = "rgba(255,245,170,.65)"; ctx.fillRect(p.x+c*.44,p.y+c*.48,c*.12,c*.12); }
    } else if (type === "factory" || type === "factory_smoke") {
      ctx.fillStyle = "#6f747c"; ctx.fillRect(p.x+c*.2,p.y+c*.42,c*.58,c*.32); ctx.fillRect(p.x+c*.58,p.y+c*.22,c*.12,c*.52);
      if (type === "factory_smoke") { ctx.fillStyle = "rgba(220,220,210,.35)"; ctx.beginPath(); ctx.arc(p.x+c*.68,p.y+c*.16,c*.11,0,Math.PI*2); ctx.fill(); }
    } else if (type === "radio_tower") {
      ctx.strokeStyle = "#d7f4ff"; ctx.beginPath(); ctx.moveTo(cx,p.y+c*.18); ctx.lineTo(p.x+c*.3,p.y+c*.78); ctx.moveTo(cx,p.y+c*.18); ctx.lineTo(p.x+c*.7,p.y+c*.78); ctx.moveTo(p.x+c*.38,p.y+c*.5); ctx.lineTo(p.x+c*.62,p.y+c*.5); ctx.stroke();
    } else if (type === "flag") {
      ctx.strokeStyle = "rgba(255,255,255,.75)"; ctx.beginPath(); ctx.moveTo(cx,p.y+c*.2); ctx.lineTo(cx,p.y+c*.78); ctx.stroke(); ctx.fillStyle = owner ? ((currentState.factions||[]).find(f=>+f.id===+owner)?.color||"#fff") : "#fff"; ctx.fillRect(cx,p.y+c*.22,c*.28,c*.16);
    } else if (type === "roadlet") {
      ctx.strokeStyle = "rgba(245,214,142,.34)"; ctx.beginPath(); ctx.moveTo(p.x+c*.08,cy); ctx.lineTo(p.x+c*.92,cy); ctx.stroke();
    } else {
      ctx.fillStyle = type === "grove" || type === "forest_camp" ? "#123d25" : "#7d7280"; ctx.font = `${Math.max(9, Math.min(15, c*.8))}px sans-serif`; ctx.fillText(type === "battlefield" ? "⚔" : type === "abandoned_outpost" ? "▣" : "◆", cx, cy);
    }
    ctx.restore();
  }
  drawBirds(ctx, now = Date.now()) {
    if (!this.birds?.length || document.hidden || this.cell < 12) return;
    ctx.save(); ctx.strokeStyle = "rgba(8,14,30,.28)"; ctx.lineWidth = 1.2;
    for (const bird of this.birds) {
      const phase = ((now + bird.start) % bird.duration) / bird.duration, bx = -24 + phase * (this.w + 48), by = bird.y * this.h + Math.sin(phase * Math.PI * 2) * 16, s = 7 * bird.scale;
      ctx.beginPath(); ctx.moveTo(bx, by); ctx.quadraticCurveTo(bx+s*.7, by-s*.6, bx+s*1.4, by); ctx.moveTo(bx, by); ctx.quadraticCurveTo(bx-s*.7, by-s*.6, bx-s*1.4, by); ctx.stroke();
    }
    ctx.restore();
  }

  drawSkeleton(ctx) { const b=this.visibleCellBounds(); if (this.cell < 6) return; for(let y=b.minY;y<=b.maxY;y++) for(let x=b.minX;x<=b.maxX;x++){ const key=`${Math.floor(x/this.state.map.chunk_size)},${Math.floor(y/this.state.map.chunk_size)}`; if(this.cache.has(key)) continue; const p=this.cellToScreen(x,y); ctx.fillStyle=((x+y)&1)?"rgba(255,255,255,.035)":"rgba(0,0,0,.035)"; ctx.fillRect(p.x,p.y,this.cell,this.cell); } }
  ownerAt(x, y) {
    if (x < 0 || y < 0 || x >= this.state.map.width || y >= this.state.map.height) return 0;
    const cs=this.state.map.chunk_size, ch=this.cache.get(`${Math.floor(x/cs)},${Math.floor(y/cs)}`);
    if (!ch) return null;
    const row=ch.owners?.[y-ch.chunk_y*cs], value=row?.[x-ch.chunk_x*cs];
    return value == null ? null : Number(value || 0);
  }
  drawOwnershipOverlay(ctx, owner, p, x, y) {
    if (!owner) return;
    const isNull=+owner===8, color=isNull?"#241033":this.factionVisualsById.get(+owner)?.color||"#94a3b8", c=this.cell;
    ctx.save(); ctx.fillStyle=color; ctx.globalAlpha=isNull?.52:POLYWAR_VISUALS.ownershipOpacity; ctx.fillRect(p.x,p.y,c,c);
    if (!this.visualLowPower && c >= POLYWAR_VISUALS.detailedCell) {
      ctx.globalAlpha=isNull?.18:POLYWAR_VISUALS.ownershipPatternOpacity; ctx.strokeStyle=isNull?"#d8b4fe":"#fff"; ctx.lineWidth=1;
      const step=Math.max(6,c*.32), offset=((x*3+y*5)%7); for(let k=-c;k<c*2;k+=step){ctx.beginPath();ctx.moveTo(p.x+k+offset,p.y);ctx.lineTo(p.x+k+c+offset,p.y+c);ctx.stroke();if(isNull){ctx.beginPath();ctx.moveTo(p.x+k+c+offset,p.y);ctx.lineTo(p.x+k+offset,p.y+c);ctx.stroke();}}
    }
    ctx.restore();
  }
  drawFactionBorders(ctx, owner, p, x, y) {
    if (!owner) return;
    const ownerColor=+owner===8?"#d8b4fe":this.factionVisualsById.get(+owner)?.color||"#e2e8f0", c=this.cell;
    const edges=[{dx:0,dy:-1,side:"top",line:[p.x,p.y,p.x+c,p.y]},{dx:1,dy:0,side:"right",line:[p.x+c,p.y,p.x+c,p.y+c]},{dx:0,dy:1,side:"bottom",line:[p.x,p.y+c,p.x+c,p.y+c]},{dx:-1,dy:0,side:"left",line:[p.x,p.y,p.x,p.y+c]}];
    ctx.save(); ctx.lineCap="square";
    for(const edge of edges) { const neighbor=this.ownerAt(x+edge.dx,y+edge.dy); if(!polywarShouldDrawFactionEdge(owner,neighbor,edge.side))continue; const style=polywarFactionEdgeStyle(owner,neighbor,ownerColor), [x1,y1,x2,y2]=edge.line; ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.strokeStyle="rgba(5,10,22,.88)";ctx.lineWidth=POLYWAR_VISUALS.borderThickness+2;ctx.stroke();ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.strokeStyle=style.color;ctx.lineWidth=POLYWAR_VISUALS.borderThickness;ctx.shadowColor=style.glow;ctx.shadowBlur=style.frontline&&!this.visualLowPower&&this.cell>=10?POLYWAR_VISUALS.borderGlow:0;if(+owner===8&&!style.frontline)ctx.setLineDash([3,2]);ctx.stroke();ctx.setLineDash([]);ctx.shadowBlur=0; }
    ctx.restore();
  }
  drawContestedOverlay(ctx, contest, p) {
    if (!contest) return;
    const c=this.cell, animate=!this.visualLowPower&&!polywarReducedMotion()&&!document.hidden, phase=animate?(Date.now()%POLYWAR_VISUALS.contestedPulseMs)/POLYWAR_VISUALS.contestedPulseMs:.5;
    const color=this.factionVisualsById.get(+contest.contesting_faction_id)?.color||"#fbbf24";
    ctx.save();ctx.beginPath();ctx.rect(p.x,p.y,c,c);ctx.clip();ctx.globalAlpha=.12+.08*Math.sin(phase*Math.PI*2);ctx.fillStyle=color;for(let k=-c;k<c*2;k+=POLYWAR_VISUALS.contestedStripe){ctx.save();ctx.translate(p.x+k,p.y);ctx.rotate(-Math.PI/4);ctx.fillRect(0,-c,2,c*3);ctx.restore();}ctx.globalAlpha=1;ctx.strokeStyle="#fde68a";ctx.lineWidth=2;ctx.shadowColor=color;ctx.shadowBlur=this.visualLowPower?0:5;ctx.strokeRect(p.x+1,p.y+1,Math.max(2,c-2),Math.max(2,c-2));ctx.shadowBlur=0;ctx.fillStyle=color;ctx.fillRect(p.x+3,p.y+c-5,Math.max(2,(c-6)*(contest.contest_progress/Math.max(1,contest.contest_required))),2);ctx.restore();
    if(animate)this.visualAnimationNeeded=true;
  }
  drawHoverCell(ctx) { if (!this.hovered || this.cell<8) return; const [x,y]=this.hovered.split(",").map(Number);if(x<0||y<0||x>=this.state.map.width||y>=this.state.map.height)return;const p=this.cellToScreen(x,y);ctx.save();ctx.fillStyle="rgba(226,245,255,.08)";ctx.fillRect(p.x+1,p.y+1,this.cell-2,this.cell-2);ctx.strokeStyle="rgba(190,231,255,.48)";ctx.strokeRect(p.x+1.5,p.y+1.5,this.cell-3,this.cell-3);ctx.restore(); }
  drawWorldPolish(ctx) { if(this.visualLowPower){ctx.fillStyle="rgba(2,6,18,.08)";ctx.fillRect(0,0,this.w,this.h);return;}const g=ctx.createRadialGradient(this.w*.46,this.h*.38,Math.min(this.w,this.h)*.12,this.w*.5,this.h*.5,Math.max(this.w,this.h)*.7);g.addColorStop(0,"rgba(190,225,255,.025)");g.addColorStop(.68,"rgba(7,13,27,0)");g.addColorStop(1,"rgba(2,6,18,.25)");ctx.fillStyle=g;ctx.fillRect(0,0,this.w,this.h); }
  drawCellGrid(ctx) { const b=this.visibleCellBounds(); if (this.cell < 6) { const ss=this.sectorSize(), r=this.visibleSectorRange(); ctx.strokeStyle="rgba(255,255,255,.18)"; ctx.lineWidth=1; ctx.beginPath(); for(let sx=r.minX;sx<=r.maxX+1;sx++){ const p=this.cellToScreen(sx*ss,b.minY); ctx.moveTo(Math.round(p.x)+.5,0); ctx.lineTo(Math.round(p.x)+.5,this.h); } for(let sy=r.minY;sy<=r.maxY+1;sy++){ const p=this.cellToScreen(b.minX,sy*ss); ctx.moveTo(0,Math.round(p.y)+.5); ctx.lineTo(this.w,Math.round(p.y)+.5); } ctx.stroke(); return; } ctx.strokeStyle="rgba(255,255,255,.10)"; ctx.lineWidth=1; ctx.beginPath(); for(let x=b.minX;x<=b.maxX+1;x++){ const p=this.cellToScreen(x,b.minY); ctx.moveTo(Math.round(p.x)+.5,0); ctx.lineTo(Math.round(p.x)+.5,this.h); } for(let y=b.minY;y<=b.maxY+1;y++){ const p=this.cellToScreen(b.minX,y); ctx.moveTo(0,Math.round(p.y)+.5); ctx.lineTo(this.w,Math.round(p.y)+.5); } ctx.stroke(); }
  drawBaseMarkers(ctx) { const capitalKeys = polywarCapitalUi?.cache || new Map(); for (const b of this.state.map.bases || []) { if (capitalKeys.has(`${b.x},${b.y}`)) continue; const p=this.cellToScreen(b.x,b.y); const cx=p.x+this.cell/2, cy=p.y+this.cell/2, r=Math.max(4, Math.min(14, this.cell*.36)); ctx.save(); ctx.shadowColor=b.color||"#fff"; ctx.shadowBlur=8; ctx.fillStyle=b.color||"#fff"; ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.lineWidth=1.25; ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0; ctx.stroke(); ctx.fillStyle="rgba(7,10,24,.82)"; ctx.font=`${Math.max(8,Math.min(13,r*1.15))}px sans-serif`; ctx.textAlign="center"; ctx.textBaseline="middle"; ctx.fillText("⌂",cx,cy+.5); ctx.restore(); } }
  drawSelectedCell(ctx) { if(!this.selected)return;const now=performance.now(),animate=!this.visualLowPower&&!polywarReducedMotion()&&now<this.selectionAnimationUntil,phase=animate?(now%POLYWAR_VISUALS.selectionPulseMs)/POLYWAR_VISUALS.selectionPulseMs:.5,inset=2+Math.sin(phase*Math.PI*2)*.7,p=this.cellToScreen(this.selected.x,this.selected.y);ctx.save();ctx.fillStyle="rgba(53,166,255,.16)";ctx.fillRect(p.x,p.y,this.cell,this.cell);ctx.strokeStyle="#b9efff";ctx.shadowColor="#38bdf8";ctx.shadowBlur=this.visualLowPower?0:POLYWAR_VISUALS.selectionGlow;ctx.lineWidth=Math.max(2,Math.min(3,this.cell*.1));ctx.strokeRect(p.x+inset,p.y+inset,Math.max(2,this.cell-inset*2),Math.max(2,this.cell-inset*2));ctx.shadowBlur=0;ctx.strokeStyle="rgba(255,255,255,.9)";ctx.lineWidth=1;const n=Math.min(6,this.cell*.22);for(const [sx,sy,dx,dy] of [[p.x,p.y,1,1],[p.x+this.cell,p.y,-1,1],[p.x,p.y+this.cell,1,-1],[p.x+this.cell,p.y+this.cell,-1,-1]]){ctx.beginPath();ctx.moveTo(sx+dx*n,sy);ctx.lineTo(sx,sy);ctx.lineTo(sx,sy+dy*n);ctx.stroke();}ctx.restore();if(animate)this.visualAnimationNeeded=true; }
  drawPendingPulse(ctx) { if(!this.pendingCellKey)return;const [px,py]=this.pendingCellKey.split(",").map(Number),p=this.cellToScreen(px,py),animate=!this.visualLowPower&&!polywarReducedMotion(),phase=animate?(Date.now()%900)/900:.5,pad=2+phase*Math.max(1,this.cell*.18);ctx.save();ctx.strokeStyle=`rgba(53,166,255,${.8-phase*.45})`;ctx.lineWidth=2;ctx.strokeRect(p.x+pad,p.y+pad,Math.max(2,this.cell-pad*2),Math.max(2,this.cell-pad*2));ctx.restore();if(animate)this.visualAnimationNeeded=true; }
  scheduleVisualAnimation(delay=160) { if(this.destroyed||document.hidden||polywarReducedMotion()||this.visualLowPower||this.visualAnimationTimer)return;this.visualAnimationTimer=setTimeout(()=>{this.visualAnimationTimer=null;if(!this.destroyed&&!document.hidden)this.requestDraw();},delay); }
  finishVisualFrame() { if(this.visualAnimationNeeded)this.scheduleVisualAnimation(); }
  drawCoarseWorld(ctx) { const b=this.visibleCellBounds(), ss=this.sectorSize(), factions=currentState.factions||[]; ctx.fillStyle='rgba(18,28,48,.96)'; ctx.fillRect(0,0,this.w,this.h); const minSx=Math.max(0,Math.floor(b.minX/ss)), maxSx=Math.min(this.sectorColumns()-1,Math.floor(b.maxX/ss)), minSy=Math.max(0,Math.floor(b.minY/ss)), maxSy=Math.min(this.sectorRows()-1,Math.floor(b.maxY/ss)); for(let sy=minSy; sy<=maxSy; sy++) for(let sx=minSx; sx<=maxSx; sx++){ const sec=this.sectorCache.get(`${sx},${sy}`), p=this.cellToScreen(sx*ss,sy*ss), size=ss*this.cell; if(sec?.controller_faction_id){ ctx.globalAlpha=.26; ctx.fillStyle=factions.find(f=>+f.id===+sec.controller_faction_id)?.color||'#fff'; ctx.fillRect(p.x,p.y,size,size); ctx.globalAlpha=1; } if(sec?.is_contested){ ctx.fillStyle='rgba(255,255,255,.18)'; for(let k=-size;k<size*2;k+=10){ ctx.fillRect(p.x+k,p.y,3,size); } } ctx.strokeStyle='rgba(255,255,255,.24)'; ctx.strokeRect(p.x,p.y,size,size); } }

  requestDraw() { if (this.destroyed || this.drawFrame) return; this.drawFrame = requestAnimationFrame(() => { this.drawFrame = null; if (!this.destroyed) this.draw(); }); }
  draw() { this.visualAnimationNeeded=false;this.visualLowPower=polywarLowPowerMode();const ctx = this.ctx, lod = this.lodLevel(); ctx.clearRect(0, 0, this.w, this.h); this.drawSkeleton(ctx); if (lod === 2) { this.drawCoarseWorld(ctx); this.drawSquadPressure(ctx); this.drawBaseMarkers(ctx); polywarCapitalUi.draw(ctx, (x,y)=>this.cellToScreen(x,y), currentState.factions || [], this.cell, new Set((this.state.map.bases || []).map(b => `${b.x},${b.y}`))); this.drawSquads(ctx); this.drawSelectedCell(ctx); this.drawPendingPulse(ctx); this.drawWorldPolish(ctx);this.finishVisualFrame();return; } const visible = new Set(this.visibleChunks().map(c => c.join(","))); const cs = this.state.map.chunk_size; for (const [key, ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (let yy = 0; yy < ch.height; yy++) for (let xx = 0; xx < ch.width; xx++) { const x = ch.chunk_x * cs + xx, y = ch.chunk_y * cs + yy, p = this.cellToScreen(x, y); if (p.x + this.cell < 0 || p.y + this.cell < 0 || p.x > this.w || p.y > this.h) continue; this.drawTerrainTile(ctx, ch.terrain[yy][xx], p, x, y); const own = ch.owners[yy][xx]; this.drawOwnershipOverlay(ctx, own, p, x, y); this.drawFactionBorders(ctx, own, p, x, y); const rift=(ch.rifts||[]).find(q=>+q.x===x&&+q.y===y); if(rift){ ctx.fillStyle=rift.status==="sealed"?"#30d987":"#e879f9"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2,Math.max(4,this.cell*.35),0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#fff"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2,Math.max(5,this.cell*.48),-Math.PI/2,-Math.PI/2+Math.PI*2*((rift.health_percent||0)/100)); ctx.stroke(); } this.drawCaptureFrontierHint(ctx,ch.terrain[yy][xx],own,rift,p,x,y); const contest=(ch.contested_cells||[]).find(q=>+q.x===x&&+q.y===y); if(contest) this.drawContestedOverlay(ctx, contest, p); if (this.cell > 12) { ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.strokeRect(p.x, p.y, this.cell, this.cell); const intel=(ch.intel||[]).find(i=>+i.x===x&&+i.y===y); const fl=(ch.flags||[]).find(f=>+f.x===x&&+f.y===y); if(intel?.intel_type==="safe_hint"){ ctx.fillStyle="#fff"; ctx.font=`${Math.max(10,this.cell*.65)}px sans-serif`; ctx.fillText(String(intel.adjacent_mines), p.x+3, p.y+this.cell-3); } if(intel?.intel_type==="triggered_mine"){ ctx.fillStyle="#111"; ctx.fillText("✹", p.x+3, p.y+this.cell-3); } if(fl){ ctx.fillStyle="#ffeb3b"; ctx.fillText(`⚑${fl.flag_count}`, p.x+2, p.y+12); } } const feature=(ch.features||[]).find(q=>+q.x===x&&+q.y===y); this.drawFeature(ctx, feature, p, own); } } if (this.cell < 8) { const ss=this.sectorSize(), r=this.visibleSectorRange(); for(let sy=r.minY; sy<=r.maxY; sy++) for(let sx=r.minX; sx<=r.maxX; sx++){ const sec=this.sectorCache.get(`${sx},${sy}`), p=this.cellToScreen(sx*ss, sy*ss), size=ss*this.cell; if(sec?.controller_faction_id){ ctx.fillStyle=(currentState.factions||[]).find(f=>+f.id===+sec.controller_faction_id)?.color||"#fff"; ctx.globalAlpha=.16; ctx.fillRect(p.x,p.y,size,size); ctx.globalAlpha=1; } if(sec?.is_contested){ ctx.fillStyle="rgba(255,255,255,.16)"; for(let k=0;k<size;k+=8){ ctx.fillRect(p.x+k,p.y,3,size); } } ctx.strokeStyle="rgba(255,255,255,.25)"; ctx.strokeRect(p.x,p.y,size,size);  } } this.drawCellGrid(ctx); this.drawBaseMarkers(ctx); for (const [key,ch] of this.cache.entries()) { if (!visible.has(key)) continue; for (const sc of ch.scans||[]) { const p=this.cellToScreen(sc.center_x-sc.size/2, sc.center_y-sc.size/2); ctx.strokeStyle="rgba(255,255,255,.9)"; ctx.strokeRect(p.x,p.y,sc.size*this.cell,sc.size*this.cell); const cp=this.cellToScreen(sc.center_x,sc.center_y); ctx.fillStyle="#fff"; ctx.fillText(String(sc.active_mine_count), cp.x+2, cp.y+12); } } if (actionMode.startsWith("scan") && this.selected) { const size=actionMode==="scan5"?5:3, p=this.cellToScreen(this.selected.x-size/2, this.selected.y-size/2); ctx.strokeStyle="#00e5ff"; ctx.setLineDash([4,3]); ctx.strokeRect(p.x,p.y,size*this.cell,size*this.cell); ctx.setLineDash([]); } if (this.blast && Date.now()-this.blast.t<1800) { const p=this.cellToScreen(this.blast.x,this.blast.y); ctx.fillStyle="rgba(255,80,0,.55)"; ctx.beginPath(); ctx.arc(p.x+this.cell/2,p.y+this.cell/2, this.cell*2,0,Math.PI*2); ctx.fill();this.visualAnimationNeeded=!this.visualLowPower; } this.drawSquadPressure(ctx); polywarCapitalUi.draw(ctx, (x,y)=>this.cellToScreen(x,y), currentState.factions || [], this.cell, new Set((this.state.map.bases || []).map(b => `${b.x},${b.y}`))); this.drawSquads(ctx); polywarGovernanceUi.drawOrders(ctx, (x,y)=>this.cellToScreen(x,y)); this.drawHoverCell(ctx);  if (this.lastSuccess && Date.now()-this.lastSuccess.t<900) { const p=this.cellToScreen(this.lastSuccess.x,this.lastSuccess.y); ctx.fillStyle="rgba(48,217,135,.45)"; ctx.fillRect(p.x,p.y,this.cell,this.cell);this.visualAnimationNeeded=!this.visualLowPower; } this.drawSelectedCell(ctx); this.drawPendingPulse(ctx); this.drawBirds(ctx);this.drawWorldPolish(ctx);this.finishVisualFrame(); }
}

function renderUnavailable(message) { teardownPolywarMenu({ restartTimers: false }); clearTimers(); map?.destroy(); map = null; root.innerHTML = `<section class="glass card"><h2>PolyWar is temporarily unavailable</h2><p class="muted">${esc(message || "Please check back later.")}</p><a class="btn" href="/app">Back to DeepAlpha</a></section>`; }

function renderWorldHud(state){ const w=state.world||{}, season=state.season||{}; return `<div class="polywar-world-grid"><div class="polywar-world-stat">Null State<br><b>${esc(w.status||"dormant")}</b></div><div class="polywar-world-stat">Activation<br><b id="polywarActivationCountdown" data-countdown="${esc(w.activation_at||"")}">${esc(w.activation_at||"—")}</b></div><div class="polywar-world-stat">Next tick<br><b id="polywarNextTickCountdown" data-countdown="${esc(w.next_tick_at||"")}">${esc(w.next_tick_at||"—")}</b></div><div class="polywar-world-stat">Season end<br><b id="polywarSeasonCountdown" data-countdown="${esc(season.ends_at||"")}">${esc(season.ends_at||"—")}</b></div><div class="polywar-world-stat">Domination hold<br><b id="polywarDominationCountdown" data-countdown="${esc(w.domination_hold_until||"")}">${esc(w.domination_hold_until||"—")}</b></div><div class="polywar-world-stat">Corruption<br><b>${esc(w.corruption_level||0)}</b></div><div class="polywar-world-stat">Cells / Sectors / Capitals<br><b>${esc(w.controlled_cells_count||0)} / ${esc(w.controlled_sectors_count||0)} / ${esc(w.controlled_capitals_count||0)}</b></div><div class="polywar-world-stat">Rifts<br><b>${esc((w.active_rifts||[]).length)} active · ${esc((w.sealed_rifts||[]).length)} sealed</b></div></div><p class="muted">Countdowns corrected by server_timestamp ${esc(w.server_timestamp||0)}.</p>`; }
function hasOwnAdjacent(cell,state){ const fid=Number(state.player?.faction_id||0); if(!fid || !map?.selected) return false; return map.isFrontline(map.selected.x, map.selected.y, fid); }
function updateSharedCountdowns(){ if(document.hidden) return; const serverNow=Date.now()+Number(map?.serverTimeOffsetMs ?? (((Number(currentState?.world?.server_timestamp||0)*1000)-Date.now())||0)); document.querySelectorAll('[data-countdown]').forEach(el=>{ const target=Date.parse(el.dataset.countdown||''); el.textContent=Number.isFinite(target)?fmtTime(Math.max(0,Math.ceil((target-serverNow)/1000))):'—'; }); document.querySelectorAll('[data-squad-countdown]').forEach(el=>{ const raw=el.dataset.reinforcementAt||el.dataset.expiresAt||''; const target=Date.parse(raw); el.textContent=Number.isFinite(target)?fmtTime(Math.max(0,Math.ceil((target-serverNow)/1000))):'—'; }); }
function startWorldCountdownTimer(){ if(worldCountdownTimer) clearInterval(worldCountdownTimer); updateSharedCountdowns(); worldCountdownTimer=setInterval(updateSharedCountdowns,1000); }
function refreshOpenPolywarMenu(){ const layer=document.getElementById('polywarMenuLayer'); if(!layer || layer.dataset.open!=='true') return; const scroller=layer.querySelector?.('.polywar-menu-scroll'), top=scroller?.scrollTop||0; layer.innerHTML=renderPolywarMenu(currentState||{}); const nextScroller=layer.querySelector?.('.polywar-menu-scroll'); if(nextScroller) nextScroller.scrollTop=top; updateFactionStats(); updateFactionRanking(); updateLatestEvents(); polywarGovernanceUi.render(polywarGovernanceUi.lastData||{}); updateSharedCountdowns(); }
document.addEventListener?.('visibilitychange',()=>{ if(!document.hidden) updateSharedCountdowns(); });
function polywarRiftPanel(rift, cell, state){ const rules=state.rules?.world||{}; const cost=Number(rules.seal_energy_cost||0); const canSeal = rift.status === "active" && Number(state.player?.faction_id||0)>0 && hasOwnAdjacent(cell,state) && Number(state.energy?.current_energy||0)>=cost && !state.energy?.is_locked && !map?.pending; return `<section class="glass card polywar-rift-panel"><h3>Rift</h3><p>Status: ${esc(rift.status)}</p><p>Health: ${esc(rift.health)}/${esc(rift.max_health)} (${esc(rift.health_percent||0)}%)</p><p>Seal energy cost: ${esc(cost)}</p><p>Frontline eligibility: ${esc(canSeal ? "ready" : "requires active rift, energy and adjacent faction cell")}</p>${canSeal ? `<button class="btn" data-polywar-action="seal_rift">Seal Rift</button>` : ""}</section>`; }
function polywarRebellionPanel(rebellion, cell, state){ const rules=state.rules?.rebellions||{}; const fid=Number(state.player?.faction_id||0); const canSupport=fid===Number(rebellion.capital_original_faction_id); const canSuppress=fid===Number(rebellion.controller_faction_id); return `<section class="glass card polywar-rebellion-panel"><h3>Capital rebellion</h3><p>Original faction: ${esc(rebellion.capital_original_faction_id)}</p><p>Occupier: ${esc(rebellion.controller_faction_id)}</p><p>Status: ${esc(rebellion.status)}</p><p>Progress: ${esc(rebellion.progress)}/${esc(rebellion.required_progress)}</p><p>Support cost: ${esc(rules.support_energy_cost||0)} · Suppress cost: ${esc(rules.suppress_energy_cost||0)}</p>${canSupport ? `<button class="btn" data-polywar-action="support_rebellion">Support rebellion</button>` : ""}${canSuppress ? `<button class="btn" data-polywar-action="suppress_rebellion">Suppress rebellion</button>` : ""}</section>`; }
function renderResultsPanel(state){ const r=state.latest_completed_season||state.results||{}; const rew=state.current_user_pending_reward||{}; return `<p>Victory type: ${esc(r.victory_type||"—")}</p><p>Winner: ${esc(r.winner_faction_id||"—")}</p><p>Results hash: ${esc((r.results_hash||"").slice(0,12))}</p><p>Reward: ${esc(rew.total_reward||0)} · ${esc(rew.status||"not ready")}</p>${rew.total_reward ? `<button class="btn" id="polywarClaimReward">Claim reward</button>` : ""}`; }
function renderPolywarMenu(state){ const p=state.player||{}, e=state.energy||{}, season=state.season||{}, selected=state.selected_faction, needsJoin=!selected; const alliedSquads=(map?.squads||[]).filter(s=>!selected||+s.faction_id===+selected.id), activeSquads=alliedSquads.filter(s=>s.status!=="awaiting_reinforcement"), awaitingSquads=alliedSquads.filter(s=>s.status==="awaiting_reinforcement"); const sim=map?.squadSimulation||{mode:"active",activePlayerCount:0,windowMinutes:5}; const simStatus=sim.mode==="dormant"?"Squad war paused · waiting for active players":`Squad war active · ${sim.activePlayerCount} players recently active`; const squadSummary=`<p class="muted">${esc(simStatus)}</p><h4>Active squads</h4>${activeSquads.map(s=>`<div class="polywar-info-card"><span>Squad #${esc(s.id)}</span><b>${esc(s.status)}</b><small>HP ${esc(s.hp)}/${esc(s.max_hp)} · Next ${esc(s.next_move_at||"—")}</small></div>`).join("")||`<p class="muted">No active allied squads in view.</p>`}<h4>Awaiting reinforcement</h4>${awaitingSquads.map(s=>`<div class="polywar-info-card"><span>Squad #${esc(s.id)}</span><b>Awaiting reinforcement</b><small>HP 0/${esc(s.max_hp)} · Position ${esc(s.x)},${esc(s.y)} · Supply ${esc(s.supply_x??"—")},${esc(s.supply_y??"—")} · Reinforcement <span data-squad-countdown data-reinforcement-at="${esc(s.reinforcement_at||"")}">${esc(map?.reinforcementRemaining?.(s)||"—")}</span> · Expires <span data-squad-countdown data-expires-at="${esc(s.expires_at||"")}">${esc(map?.expirationRemaining?.(s)||"—")}</span> · Boosts ${esc(s.reinforcement_boost_count||0)}</small>${selected?`<button class="secondary-action-pill" data-polywar-support-squad="${esc(s.id)}" data-polywar-support-type="reinforcement">Send reinforcement · ${esc(Number(map?.squadRules?.reinforcement_energy_cost ?? 1))} ⚡</button>`:""}</div>`).join("")||`<p class="muted">No awaiting allied squads in view.</p>`}`; return `<div class="polywar-menu-backdrop" id="polywarMenuBackdrop" data-polywar-menu-close="backdrop"></div><aside class="polywar-menu-sheet glass" id="polywarMenuSheet" role="dialog" aria-modal="true" aria-labelledby="polywarMenuTitle"><header class="polywar-menu-head"><div><p class="eyebrow">Command menu</p><h2 id="polywarMenuTitle">PolyWar status</h2></div><div class="polywar-menu-head-actions"><a class="btn mini secondary-link" href="/app">Back to DeepAlpha</a><button class="btn mini" id="polywarMenuClose" data-polywar-menu-close="button" aria-label="Close PolyWar menu">Close</button></div></header><div class="polywar-menu-scroll"><section class="polywar-menu-section"><h3>Overview</h3><div class="polywar-menu-grid"><div class="polywar-info-card"><span>Season</span><b>${esc(season.name||"Active Season")}</b><small>${esc(season.starts_at||"—")} → ${esc(season.ends_at||"—")}</small></div><div class="polywar-info-card"><span>Energy</span><b id="energyValue">${esc(e.current_energy)}/${esc(e.max_energy)}</b><small>Next charge: <span id="energyCountdown">${fmtTime(e.seconds_until_next_energy)}</span> · ${esc(e.recharge_minutes)} min</small><small>Status: <b id="lockStatus">${e.is_locked?"Mine locked":"Active"}</b></small></div><div class="polywar-info-card ${selected?"confirm":""}"><span>Faction</span><b>${selected?`${factionDot(selected)}${esc(selected.name)}`:"Not selected"}</b><small>${selected?"Faction locked for this season.":"Choose a faction to capture cells."}</small></div></div>${needsJoin?`<div class="factions polywar-menu-factions">${(state.factions||[]).map(f=>`<button class="faction" data-faction="${esc(f.id)}">${factionDot(f)}${esc(f.name)}<small>${esc(f.description)}</small></button>`).join("")}</div>`:""}</section><section class="polywar-menu-section polywar-world-hud" id="polywarWorldHud"><h3>World HUD</h3>${renderWorldHud(state)}</section><section class="polywar-menu-section"><h3>Frontline Squads</h3><div class="polywar-menu-grid">${squadSummary}</div></section><section class="polywar-menu-section polywar-governance-panel" id="polywarGovernancePanel" data-polywar-governance><h3>Governance</h3></section><section class="polywar-menu-section"><h3>Ranking</h3><div class="polywar-menu-grid" id="factionStats"><div class="polywar-info-card"><span>Season Points</span><b>${esc(p.season_spendable_points||0)}</b></div><div class="polywar-info-card"><span>Faction Contribution</span><b>${esc(p.faction_contribution||0)}</b></div></div><div id="factionRanking" class="polywar-ranking-list"></div></section><section class="polywar-menu-section polywar-results-panel" id="polywarResultsPanel"><h3>Season Results</h3>${renderResultsPanel(state)}</section><section class="polywar-menu-section latest-events-section"><h3>Latest events</h3><div id="latestEvents" class="polywar-menu-events"></div></section></div></aside>`; }
function setPolywarMenuExpanded(expanded){ const btn=document.getElementById('polywarMenuButton'); if(btn) btn.setAttribute('aria-expanded', expanded ? 'true' : 'false'); }
function teardownPolywarMenu({ restartTimers = false } = {}) { const layer=document.getElementById('polywarMenuLayer'); if(layer){ layer.innerHTML=''; layer.dataset.open='false'; } document.body.classList.remove('polywar-menu-open'); setPolywarMenuExpanded(false); if(restartTimers){ startEnergyTimers(); startWorldCountdownTimer(); } }
function openPolywarMenu(){ const layer=document.getElementById('polywarMenuLayer'); if(!layer||layer.dataset.open==='true') return; polywarLastMenuTrigger=document.getElementById('polywarMenuButton'); layer.innerHTML=renderPolywarMenu(currentState||{}); layer.dataset.open='true'; document.body.classList.add('polywar-menu-open'); setPolywarMenuExpanded(true); updateFactionStats(); updateFactionRanking(); updateLatestEvents(); polywarGovernanceUi.render(polywarGovernanceUi.lastData||{}); startEnergyTimers(); startWorldCountdownTimer(); document.getElementById('polywarMenuClose')?.focus?.(); }
function closePolywarMenu(){ teardownPolywarMenu({restartTimers:true}); polywarLastMenuTrigger?.focus?.(); }

async function syncPolywarResults(){ const seq=++polywarResultsSeq, expectedMap=map, expectedSeason=currentState?.latest_completed_season?.id; const d=await api('/api/polywar/results/latest'); if(seq!==polywarResultsSeq || expectedMap!==map || (expectedSeason && (Number(currentState?.latest_completed_season?.id||0)!==Number(expectedSeason) || (d.season?.id && Number(d.season.id)!==Number(expectedSeason)))) || !d.ok) return d; currentState.results=d; currentState.current_user_pending_reward=d.current_user_reward||currentState.current_user_pending_reward; const panel=document.getElementById('polywarResultsPanel'); if(panel) panel.innerHTML=`<h3>Season Results</h3>${renderResultsPanel(currentState)}`; return d; }
async function claimPolywarReward(season_id){ const key=polywarClaimKeys.get(season_id)||`claim-${season_id}-${Date.now()}-${Math.random().toString(16).slice(2)}`; polywarClaimKeys.set(season_id,key); const btn=document.getElementById('polywarClaimReward'); if(btn){ btn.disabled=true; btn.textContent='Claiming…'; } const seq=++polywarRewardSeq; const d=await api('/api/polywar/rewards/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({season_id,idempotency_key:key})}); if(seq!==polywarRewardSeq) return {ok:false,stale:true}; if(d.ok||d.duplicate){ polywarClaimKeys.delete(season_id); await syncPolywarResults(); await syncState(false,{soft:true}); } else if(btn){ btn.disabled=false; btn.textContent='Claim reward'; } return d; }

function render(state) {
  teardownPolywarMenu({ restartTimers: false });
  currentState = state;
  if (state && state.enabled === false) { renderUnavailable(state.message); return; }
  const selected = state.selected_faction, needsJoin = !selected;
  map?.destroy();
  root.innerHTML = `<section class="polywar-game-toolbar glass"><div><p class="eyebrow">DeepAlpha Game Lab</p><h1>PolyWar</h1></div><button class="btn mini" id="polywarMenuButton" aria-haspopup="dialog" aria-controls="polywarMenuSheet" aria-expanded="false">Menu</button></section><section class="glass card map-card polywar-main-gameplay"><div class="map-head"><h2>Tactical Map</h2><span id="chunkStatus" class="muted"></span><button class="btn mini" id="goBase">Base</button><button class="btn mini" id="openWorldView">World</button><button class="btn mini" id="zoomOut">−</button><button class="btn mini" id="zoomIn">+</button><button class="btn mini" id="quickActionsToggle" aria-pressed="true">Quick actions: ON</button></div><div class="map-wrap"><canvas id="polywarCanvas" aria-label="PolyWar map. Tap a cell, then press Enter or Space to perform the primary action."></canvas><canvas id="polywarAmbientCanvas" aria-hidden="true"></canvas><div class="polywar-minimap"><button id="polywarMinimapToggle" class="polywar-minimap-toggle">—</button><canvas id="polywarMinimapCanvas" aria-label="PolyWar minimap"></canvas></div><div class="action-panel compact-cell-sheet" aria-live="polite"><div class="sheet-main"><b>Cell <span id="cellCoords">—</span> · <span id="cellTerrain">—</span></b><span id="cellDetails" class="muted"><b id="cellOwner">Neutral</b> · <b id="cellCost">—</b></span><span id="cellReason" class="muted">Select a cell</span></div><div class="sheet-actions"><button class="btn" id="primaryActionBtn" aria-label="Primary cell action" disabled>${needsJoin ? "Choose faction" : "Capture"}</button><button class="btn mini" id="moreActionsBtn" aria-expanded="false">More ···</button></div><div id="secondaryActionsMenu" class="secondary-actions" hidden></div></div></div></section><div id="polywarMenuLayer" class="polywar-menu-layer" data-open="false"></div>`;
  root.onclick = handlePolywarUiClick;
  map = new PolyWarMap(state);
  if (selected) map.centerOnBase();
  if (state.latest_completed_season) syncPolywarResults();
  startEnergyTimers();
  startWorldCountdownTimer();
}

function updateFactionStats() {
  const p=currentState?.player||{}, el=document.getElementById("factionStats");
  if (el) el.innerHTML = `<div class="polywar-info-card"><span>Season Points</span><b>${esc(p.season_spendable_points || 0)}</b></div><div class="polywar-info-card"><span>Faction Contribution</span><b>${esc(p.faction_contribution || 0)}</b></div>`;
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
  if (state.httpStatus === 401) { teardownPolywarMenu({ restartTimers: false }); clearTimers(); map?.destroy(); map = null; root.innerHTML = '<section class="glass card"><h2>Telegram auth required</h2><p class="muted">Open PolyWar from the Telegram WebApp and try again.</p><a class="btn" href="/app">Back to DeepAlpha</a></section>'; return; }
  if (!state.ok && showErrors) { alert(state.error === "request_timeout" ? "PolyWar is taking too long to initialize. Please retry." : (state.error || "Unable to load PolyWar")); return; }
  opts.soft && map ? softUpdate(state) : render(state);
}
async function joinFaction(id) { const d = await api("/api/polywar/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ faction_id: Number(id) }) }); if (!d.ok) { alert(d.error || "Join failed"); await syncState(false, { soft: true }); return; } render(d); }
async function init() { await telegramAuthIfAvailable(); await syncState(true); }
document.addEventListener("visibilitychange", () => { if (!document.hidden && map) sendPresenceHeartbeat(); });
window.addEventListener("pagehide", () => { clearTimers(); teardownPolywarMenu({ restartTimers: false }); map?.destroy(); map = null; });

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
  draw(ctx, worldToScreen, factions = [], cellSize = 16, baseKeys = new Set()) {
    const byId = new Map(factions.map(f => [f.id, f]));
    for (const cap of this.cache.values()) {
      const p = worldToScreen ? worldToScreen(cap.x, cap.y) : { x: cap.x, y: cap.y };
      const cx = p.x + cellSize / 2, cy = p.y + cellSize / 2;
      const r = Math.max(5, Math.min(13, cellSize * .36));
      const original = byId.get(cap.original_faction_id)?.color || '#ffffff';
      const controller = byId.get(cap.controller_faction_id)?.color || original;
      ctx.save();
      ctx.shadowColor = controller; ctx.shadowBlur = 8;
      ctx.fillStyle = controller;
      ctx.strokeStyle = original;
      ctx.lineWidth = cap.original_faction_id !== cap.controller_faction_id ? 2.5 : 1.5;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0; ctx.stroke();
      if (baseKeys.has(`${cap.x},${cap.y}`)) { ctx.fillStyle = 'rgba(7,10,24,.82)'; ctx.font = `${Math.max(8, Math.min(13, r * 1.15))}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('⌂', cx, cy + .5); }
      if (cap.original_faction_id !== cap.controller_faction_id) { ctx.fillStyle = '#ffd166'; ctx.fillRect(cx - 3, cy - r - 6, 6, 4); }
      if (cap.is_under_siege) {
        ctx.strokeStyle = byId.get(cap.besieging_faction_id)?.color || '#ff006e'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, cy, r + 4, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, cap.siege_progress / cap.siege_required)); ctx.stroke();
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
  async refresh(expectedMap = map) { const seq = ++this.seq; const data = await api('/api/polywar/governance'); this.lastData = data; if (seq !== this.seq || expectedMap !== map || expectedMap?.destroyed) return {ok:false, stale:true}; const stamp = Number(data.server_timestamp || 0); if (data.ok && stamp >= this.lastServerTimestamp) { this.lastServerTimestamp = stamp; this.lastData = data; this.orders = data.orders || []; this.render(data); expectedMap?.requestDraw?.(); } return data; },
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
  const menuButton = e.target.closest('#polywarMenuButton');
  const menuClose = e.target.closest('[data-polywar-menu-close]');
  const factionButton = e.target.closest('[data-faction]');
  const secondary = e.target.closest('[data-polywar-secondary]');
  const supportSquad = e.target.closest('[data-polywar-support-squad]');
  if (menuButton) { openPolywarMenu(); return; }
  if (menuClose) { closePolywarMenu(); return; }
  if (factionButton) { const factionId = Number(factionButton.dataset.faction); if (!Number.isFinite(factionId) || factionId <= 0 || factionButton.disabled) return; factionButton.disabled = true; try { await joinFaction(factionId); } finally { if (document.body.contains(factionButton)) factionButton.disabled = false; } return; }
  if (secondary) { await map?.executeSecondaryCellAction?.(secondary.dataset.polywarSecondary); return; }
  if (supportSquad) { await map?.supportSelectedSquad?.(Number(supportSquad.dataset.polywarSupportSquad)); return; }
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

window.addEventListener('keydown', e => { if (e.key === 'Escape' && document.getElementById('polywarMenuLayer')?.dataset.open === 'true') closePolywarMenu(); });

init();


window.resolvePrimaryCellAction = resolvePrimaryCellAction;
window.resolveSecondaryCellActions = resolveSecondaryCellActions;
window.__polywarTapToAct = { resolvePrimaryCellAction, resolveSecondaryCellActions, primaryActionCost, primaryActionLabel };
