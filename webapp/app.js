async function checkMe(){
  const r=await fetch('/api/auth/me',{credentials:'include'});return r.json();
}
async function tgAuth(){
  const initData=(window.Telegram&&window.Telegram.WebApp&&window.Telegram.WebApp.initData)||'';
  if(!initData) return false;
  const r=await fetch('/api/auth/telegram',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:initData})});
  return r.ok;
}
function renderAuthed(data){
  const el=document.getElementById('authed');
  el.hidden=false;el.innerHTML=`<p>Authenticated as ${data.user.first_name||data.user.username||data.user.user_id}</p><p>User ID: ${data.user.user_id}</p><p>Balance: (placeholder)</p><a href='/pay'>Open payment section</a>`;
}
function renderGuest(){
  document.getElementById('status').textContent='Not authenticated';
  document.getElementById('actions').hidden=false;
  document.getElementById('tgBtn').onclick=async()=>{const ok=await tgAuth(); if(ok) location.reload(); else alert('Telegram auth unavailable. Open from Telegram WebApp.');};
  document.getElementById('gBtn').onclick=()=>{window.location.href='/api/auth/google/start';};
}
(async()=>{
  await tgAuth();
  const me=await checkMe();
  document.getElementById('status').textContent='';
  if(me&&me.ok&&me.auth&&me.auth.authenticated) renderAuthed(me); else renderGuest();
})();
