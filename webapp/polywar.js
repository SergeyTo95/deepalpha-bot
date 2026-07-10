const root = document.getElementById("polywarRoot");
const tg = window.Telegram?.WebApp;
try { tg?.ready(); tg?.expand(); } catch (_) {}
function esc(v){return String(v ?? "").replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));}
async function telegramAuthIfAvailable(){const initData=tg?.initData||""; if(!initData) return false; const r=await fetch('/api/auth/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:initData})}); return r.ok;}
async function api(path, opts){const r=await fetch(path,opts); const data=await r.json().catch(()=>({ok:false,error:'bad_json'})); if(!r.ok) data.httpStatus=r.status; return data;}
function fmtTime(sec){sec=Math.max(0,Number(sec||0)); const m=Math.floor(sec/60), s=sec%60; return `${m}m ${String(s).padStart(2,'0')}s`;}
function factionDot(f){return `<span class="dot" style="background:${esc(f?.color||'#777')}"></span>`}
function render(state){const p=state.player||{}, e=state.energy||{}, season=state.season||{}, selected=state.selected_faction; const needsJoin=!selected; root.innerHTML=`
<section class="grid">
  <div class="glass card"><h2>Season</h2><p class="metric">${esc(season.name||'Active Season')}</p><p class="muted">${esc(season.starts_at)} → ${esc(season.ends_at)}</p></div>
  <div class="glass card"><h2>Energy</h2><p class="metric">${esc(e.current_energy)}/${esc(e.max_energy)}</p><p class="muted">Next charge: ${fmtTime(e.seconds_until_next_energy)} · ${esc(e.recharge_minutes)} min/energy</p></div>
</section>
<section class="glass card ${selected?'confirm':''}"><h2>Faction</h2>${selected?`<p class="metric">${factionDot(selected)}${esc(selected.name)}</p><p class="muted">Faction locked for this season.</p>`:`<p class="muted">Choose your faction to enter the consensus war. You cannot freely change it during the current season.</p>`}</section>
${needsJoin?`<section class="glass card"><h2>Choose faction</h2><div class="factions">${(state.factions||[]).map(f=>`<button class="faction" data-faction="${esc(f.id)}">${factionDot(f)}${esc(f.name)}<small>${esc(f.description)}</small></button>`).join('')}</div></section>`:''}
<section class="grid"><div class="glass card"><h3>Season Points</h3><p class="metric">${esc(p.season_spendable_points||0)}</p></div><div class="glass card"><h3>Faction Contribution</h3><p class="metric">${esc(p.faction_contribution||0)}</p></div></section>
<section class="glass card"><h2>Global War Map</h2><div class="map-placeholder"><h2>Global War Map — coming in Phase 2</h2></div></section>
<section class="glass card"><h2>Faction ranking</h2>${(state.faction_ranking||[]).map((f,i)=>`<div class="rank"><span>${i+1}. ${factionDot(f)}${esc(f.name)}</span><b>${esc(f.seasonal_influence_score||0)}</b></div>`).join('')}</section>
<section class="glass card"><h2>Latest events</h2>${(state.events||[]).length?(state.events||[]).map(ev=>`<div class="event"><b>${esc(ev.message)}</b><p class="muted">${esc(ev.created_at||'')}</p></div>`).join(''):'<p class="muted">No events yet.</p>'}</section>`; document.querySelectorAll('[data-faction]').forEach(b=>b.onclick=()=>joinFaction(b.dataset.faction));}
async function joinFaction(id){const data=await api('/api/polywar/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({faction_id:Number(id)})}); if(!data.ok){alert(data.error||'Join failed'); return;} render(data);}
async function init(){await telegramAuthIfAvailable(); const state=await api('/api/polywar/state'); if(state.httpStatus===401){root.innerHTML='<section class="glass card"><h2>Telegram auth required</h2><p class="muted">Open PolyWar from the Telegram WebApp and try again.</p><a class="btn" href="/app">Back to DeepAlpha</a></section>'; return;} render(state);}
init();
