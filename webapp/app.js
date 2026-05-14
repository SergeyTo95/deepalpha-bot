function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function callMe() {
  const r = await fetch('/api/auth/me', { credentials: 'include' });
  return r.json();
}

async function callSummary() {
  const r = await fetch('/api/webapp/summary', { credentials: 'include' });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function telegramAuthIfAvailable() {
  const initData = window.Telegram?.WebApp?.initData || '';
  if (!initData) return false;
  const r = await fetch('/api/auth/telegram', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data: initData }),
  });
  return r.ok;
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' });
  renderGuest();
}

function renderGuest() {
  document.getElementById('appRoot').innerHTML = `
    <section class="card">
      <h2>Welcome</h2>
      <p class="meta">Sign in to access your DeepAlpha dashboard</p>
      <div class="actions">
        <button id="tgBtn" class="btn btn-primary">Continue with Telegram</button>
        <button id="gBtn" class="btn btn-secondary">Continue with Google</button>
      </div>
      <p class="small">Open from Telegram for automatic login.</p>
    </section>`;

  document.getElementById('tgBtn').onclick = async () => {
    const ok = await telegramAuthIfAvailable();
    if (ok) return init();
    alert('Telegram auth unavailable. Open this page from Telegram WebApp.');
  };
  document.getElementById('gBtn').onclick = () => { window.location.href = '/api/auth/google/start'; };
}

function renderAuthed(summary) {
  const user = summary.user || {};
  const balance = summary.balance || {};
  const sub = summary.subscription || {};
  const pricing = summary.pricing || {};
  const subscriptionLine = sub.active ? `Active until ${escapeHtml(sub.until || '-')}` : 'Not active';

  document.getElementById('appRoot').innerHTML = `
    <section class="card">
      <h2>👤 User</h2>
      <p class="value">${escapeHtml(user.username ? '@' + user.username : (user.first_name || 'DeepAlpha User'))}</p>
      <p class="meta">ID: ${escapeHtml(user.user_id)}</p>
    </section>

    <div class="grid-2">
      <section class="card">
        <h2>💎 Token balance</h2>
        <p class="value">${escapeHtml(balance.tokens || 0)} tokens</p>
      </section>

      <section class="card">
        <h2>🔔 Subscription</h2>
        <p class="meta">${subscriptionLine}</p>
        ${sub.active ? '<span class="status-pill">Active</span>' : ''}
      </section>
    </div>

    <section class="card">
      <h2>🔥 Top Analysis</h2>
      <p class="meta">Price: ${escapeHtml(pricing.top_analysis_price_tokens || '70')} tokens</p>
      <button class="btn btn-secondary" disabled>Analyze market · Coming soon on WebApp</button>
    </section>

    <section class="card">
      <h2>💳 Cashier</h2>
      <p class="meta">Buy tokens and manage payments</p>
      <a href="/pay"><button class="btn btn-primary">Open Cashier</button></a>
    </section>

    <section class="card">
      <h2>Quick actions</h2>
      <div class="inline-links">
        <a href="/pay"><button class="btn btn-secondary">Open Cashier</button></a>
        <button id="logoutBtn" class="btn btn-secondary">Logout</button>
      </div>
    </section>`;

  document.getElementById('logoutBtn').onclick = logout;
}

async function init() {
  await telegramAuthIfAvailable();
  const me = await callMe();
  if (!(me && me.ok && me.auth && me.auth.authenticated)) return renderGuest();

  const summaryResp = await callSummary();
  if (!summaryResp.ok) return renderGuest();
  renderAuthed(summaryResp.data);
}

init();
