function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeLang(value) {
  const v = String(value || '').toLowerCase();
  return v.startsWith('ru') ? 'ru' : 'en';
}

function guestLangFallback() {
  return normalizeLang(window.Telegram?.WebApp?.initDataUnsafe?.user?.language_code || 'en');
}

const I18N = {
  en: {
    title: 'Web Dashboard', subtitle: 'AI-powered prediction market intelligence', guestPrompt: 'Sign in to access your DeepAlpha dashboard',
    tg: 'Continue with Telegram', gg: 'Continue with Google', tgNote: 'Open from Telegram for automatic login.',
    user: 'User', balance: 'Token balance', subscription: 'Subscription', activeUntil: 'Active until', notActive: 'Not active',
    top: 'Top Analysis', price: 'Price', tokens: 'tokens', coming: 'Analyze market · Coming soon on WebApp',
    cashier: 'Cashier', cashierDesc: 'Buy tokens and manage payments', openCashier: 'Open Cashier', actions: 'Quick actions', logout: 'Logout', active: 'Active', telegramAuthUnavailable: 'Telegram auth unavailable. Open this page from Telegram WebApp.'
  },
  ru: {
    title: 'Личный кабинет', subtitle: 'AI-анализ рынков прогнозов', guestPrompt: 'Войдите, чтобы открыть личный кабинет DeepAlpha',
    tg: 'Продолжить через Telegram', gg: 'Продолжить через Google', tgNote: 'Откройте из Telegram для автоматического входа.',
    user: 'Пользователь', balance: 'Баланс токенов', subscription: 'Подписка', activeUntil: 'Активна до', notActive: 'Не активна',
    top: 'Top Analysis', price: 'Цена', tokens: 'токенов', coming: 'Анализ рынка · скоро в WebApp',
    cashier: 'Касса', cashierDesc: 'Покупка токенов и управление оплатой', openCashier: 'Открыть кассу', actions: 'Быстрые действия', logout: 'Выйти', active: 'Активна', telegramAuthUnavailable: 'Авторизация Telegram недоступна. Откройте страницу из Telegram WebApp.'
  }
};

async function callMe(){ const r=await fetch('/api/auth/me',{credentials:'include'}); return r.json(); }
async function callSummary(){ const r=await fetch('/api/webapp/summary',{credentials:'include'}); return {ok:r.ok,status:r.status,data:await r.json()}; }
async function telegramAuthIfAvailable(){ const initData=window.Telegram?.WebApp?.initData||''; if(!initData) return false; const r=await fetch('/api/auth/telegram',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:initData})}); return r.ok; }
async function logout(){ await fetch('/api/auth/logout',{method:'POST',credentials:'include'}); renderGuest(guestLangFallback()); }

function setHeroText(lang){ const subtitle=document.getElementById('heroSubtitle'); const title=document.getElementById('heroTitle'); if(subtitle) subtitle.textContent=I18N[lang].subtitle; if(title) title.textContent=I18N[lang].title; }

function renderGuest(lang){
  const t=I18N[lang]; setHeroText(lang);
  document.getElementById('appRoot').innerHTML=`<section class="card"><p class="meta">${t.guestPrompt}</p><div class="actions"><button id="tgBtn" class="btn btn-primary">${t.tg}</button><button id="gBtn" class="btn btn-secondary">${t.gg}</button></div><p class="small">${t.tgNote}</p></section>`;
  document.getElementById('tgBtn').onclick=async()=>{const ok=await telegramAuthIfAvailable(); if(ok) return init(); alert(t.telegramAuthUnavailable);};
  document.getElementById('gBtn').onclick=()=>{window.location.href='/api/auth/google/start';};
}

function renderAuthed(summary,lang){
  const t=I18N[lang]; setHeroText(lang);
  const user=summary.user||{}, balance=summary.balance||{}, sub=summary.subscription||{}, pricing=summary.pricing||{};
  const subscriptionLine=sub.active?`${t.activeUntil} ${escapeHtml(sub.until||sub.raw_subscription_until||'-')}`:t.notActive;
  document.getElementById('appRoot').innerHTML=`<section class="card"><h2>👤 ${t.user}</h2><p class="value">${escapeHtml(user.username?'@'+user.username:(user.first_name||'DeepAlpha User'))}</p><p class="meta">ID: ${escapeHtml(user.user_id)}</p></section>
  <div class="grid-2"><section class="card"><h2>💎 ${t.balance}</h2><p class="value">${escapeHtml(balance.tokens||0)} ${t.tokens}</p></section><section class="card"><h2>🔔 ${t.subscription}</h2><p class="meta">${subscriptionLine}</p>${sub.active?`<span class="status-pill">${t.active}</span>`:''}</section></div>
  <section class="card"><h2>🔥 ${t.top}</h2><p class="meta">${t.price}: ${escapeHtml(pricing.top_analysis_price_tokens||'70')} ${t.tokens}</p><button class="btn btn-secondary" disabled>${t.coming}</button></section>
  <section class="card"><h2>💳 ${t.cashier}</h2><p class="meta">${t.cashierDesc}</p><a href="/pay"><button class="btn btn-primary">${t.openCashier}</button></a></section>
  <section class="card"><h2>${t.actions}</h2><div class="inline-links"><a href="/pay"><button class="btn btn-secondary">${t.openCashier}</button></a><button id="logoutBtn" class="btn btn-secondary">${t.logout}</button></div></section>`;
  document.getElementById('logoutBtn').onclick=logout;
}

async function init(){
  await telegramAuthIfAvailable();
  const me=await callMe();
  if(!(me&&me.ok&&me.auth&&me.auth.authenticated)) return renderGuest(guestLangFallback());
  const summaryResp=await callSummary();
  if(!summaryResp.ok) return renderGuest(guestLangFallback());
  const lang=normalizeLang(summaryResp.data?.language||summaryResp.data?.user?.language||'en');
  renderAuthed(summaryResp.data,lang);
}
init();
