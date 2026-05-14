function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeLang(value) {
  const v = String(value || "").toLowerCase();
  return v.startsWith("ru") ? "ru" : "en";
}

function guestLangFallback() {
  return normalizeLang(window.Telegram?.WebApp?.initDataUnsafe?.user?.language_code || "en");
}

const I18N = {
  en: {
    title: "Web Dashboard",
    subtitle: "AI-powered prediction market intelligence",
    guestPrompt: "Sign in to access your DeepAlpha dashboard",
    tg: "Continue with Telegram",
    gg: "Continue with Google",
    tgNote: "Open from Telegram for automatic login.",
    user: "User",
    balance: "Token balance",
    subscription: "Subscription",
    activeUntil: "Active until",
    notActive: "Not active",
    top: "Top Analysis",
    price: "Price",
    tokens: "tokens",
    coming: "Analyze market · Coming soon on WebApp",
    marketAnalysis: "Market Analysis",
    marketAnalysisDesc: "Paste a Polymarket link to prepare analysis in WebApp.",
    marketPlaceholder: "https://polymarket.com/event/...",
    quickAnalysis: "🔍 Quick Analysis",
    topAnalysisAction: "🔥 Top Analysis",
    validateMarketUrl: "Paste a Polymarket link.",
    comingSoonAnalysis: "Web analysis execution is not enabled yet. Full reports will appear here soon.",
    analysisOk: "Analysis completed.",
    analyzingMarket: "Analyzing market...",
    startingTop: "Starting Top Analysis. This may take 1–3 minutes...",
    analysisError: "Analysis failed. Please try again.",
    notEnoughTokens: "Not enough tokens. Open cashier.",
    historyEmpty: "No analysis history yet.",
    analysisResultTitle: "Analysis result",
    reasoningLabel: "Reasoning",
    mainScenarioLabel: "Main scenario",
    altScenarioLabel: "Alternative scenario",
    sourcesLabel: "Sources",
    copy: "Copy",
    close: "Close",
    copied: "Copied",
    openReport: "Open report",
    telegramDelivered: "Report was also sent to Telegram.",
    marketLabel: "Market",
    forecastLabel: "Forecast",
    marketProbabilityLabel: "Market probability",
    confidenceLabel: "Confidence",
    categoryLabel: "Category",
    conclusionLabel: "Conclusion",
    historyTitle: "Analysis history",
    authError: "Authorization error. Please reopen the dashboard.",
    invalidMarketUrl: "Invalid Polymarket link.",
    cashier: "Cashier",
    cashierDesc: "Buy tokens and manage payments",
    openCashier: "Open Cashier",
    actions: "Quick actions",
    logout: "Logout",
    active: "Active",
    telegramAuthUnavailable: "Telegram auth unavailable. Open this page from Telegram WebApp.",
    historyItemUnavailable: "This history item has no full report yet."
  },
  ru: {
    title: "Личный кабинет",
    subtitle: "AI-анализ рынков прогнозов",
    guestPrompt: "Войдите, чтобы открыть личный кабинет DeepAlpha",
    tg: "Продолжить через Telegram",
    gg: "Продолжить через Google",
    tgNote: "Откройте из Telegram для автоматического входа.",
    user: "Пользователь",
    balance: "Баланс токенов",
    subscription: "Подписка",
    activeUntil: "Активна до",
    notActive: "Не активна",
    top: "Top Analysis",
    price: "Цена",
    tokens: "токенов",
    coming: "Анализ рынка · скоро в WebApp",
    marketAnalysis: "Анализ рынка",
    marketAnalysisDesc: "Вставьте ссылку Polymarket, чтобы подготовить анализ в WebApp.",
    marketPlaceholder: "https://polymarket.com/event/...",
    quickAnalysis: "🔍 Быстрый анализ",
    topAnalysisAction: "🔥 Top Analysis",
    validateMarketUrl: "Вставьте ссылку Polymarket.",
    comingSoonAnalysis: "Пока запуск анализа в WebApp не включён. Скоро здесь появится полный отчёт.",
    analysisOk: "Анализ выполнен.",
    analyzingMarket: "Анализирую рынок...",
    startingTop: "Запускаю Top Analysis. Это может занять 1–3 минуты...",
    analysisError: "Ошибка анализа. Попробуйте снова.",
    notEnoughTokens: "Недостаточно токенов. Откройте кассу.",
    historyEmpty: "История пока пустая.",
    analysisResultTitle: "Результат анализа",
    reasoningLabel: "Логика",
    mainScenarioLabel: "Основной сценарий",
    altScenarioLabel: "Альтернативный сценарий",
    sourcesLabel: "Источники",
    copy: "Копировать",
    close: "Закрыть",
    copied: "Скопировано",
    openReport: "Открыть отчёт",
    telegramDelivered: "Отчёт также отправлен в Telegram.",
    marketLabel: "Рынок",
    forecastLabel: "Прогноз",
    marketProbabilityLabel: "Вероятность рынка",
    confidenceLabel: "Уверенность",
    categoryLabel: "Категория",
    conclusionLabel: "Вывод",
    historyTitle: "История анализов",
    authError: "Ошибка авторизации. Откройте кабинет заново.",
    invalidMarketUrl: "Некорректная ссылка Polymarket.",
    cashier: "Касса",
    cashierDesc: "Покупка токенов и управление оплатой",
    openCashier: "Открыть кассу",
    actions: "Быстрые действия",
    logout: "Выйти",
    active: "Активна",
    telegramAuthUnavailable: "Авторизация Telegram недоступна. Откройте страницу из Telegram WebApp.",
    historyItemUnavailable: "Для этой записи пока нет полного отчёта."
  }
};

async function callMe() {
  const r = await fetch("/api/auth/me", { credentials: "include" });
  return r.json();
}

async function callSummary() {
  const r = await fetch("/api/webapp/summary", { credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function telegramAuthIfAvailable() {
  const initData = window.Telegram?.WebApp?.initData || "";
  if (!initData) return false;

  const r = await fetch("/api/auth/telegram", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData })
  });

  return r.ok;
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
  renderGuest(guestLangFallback());
}

async function callAnalyze(url, mode) {
  const r = await fetch("/api/webapp/analyze", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, mode })
  });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function callHistory() {
  const r = await fetch("/api/webapp/history", { credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function callHistoryItem(id) {
  const r = await fetch(`/api/webapp/history/${id}`, { credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

function setHeroText(lang) {
  const t = I18N[lang] || I18N.en;
  const subtitle = document.getElementById("heroSubtitle");
  const title = document.getElementById("heroTitle");

  if (subtitle) subtitle.textContent = t.subtitle;
  if (title) title.textContent = t.title;
}

function renderGuest(lang) {
  lang = normalizeLang(lang);
  const t = I18N[lang] || I18N.en;

  setHeroText(lang);

  document.getElementById("appRoot").innerHTML = `
    <section class="card">
      <p class="meta">${t.guestPrompt}</p>
      <div class="actions">
        <button id="tgBtn" class="btn btn-primary">${t.tg}</button>
        <button id="gBtn" class="btn btn-secondary">${t.gg}</button>
      </div>
      <p class="small">${t.tgNote}</p>
    </section>
  `;

  document.getElementById("tgBtn").onclick = async () => {
    const ok = await telegramAuthIfAvailable();
    if (ok) return init();
    alert(t.telegramAuthUnavailable);
  };

  document.getElementById("gBtn").onclick = () => {
    window.location.href = "/api/auth/google/start";
  };
}


function copyText(text) {
  const value = String(text || "");
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
  return Promise.resolve();
}

function renderReportModal(report, t, deliverySent) {
  const old = document.getElementById("analysisModal"); if (old) old.remove();
  const sources = Array.isArray(report.sources) ? report.sources : [];
  const m = document.createElement("div");
  m.id = "analysisModal"; m.className = "modal-overlay";
  m.innerHTML = `<div class="modal-card"><h3>${escapeHtml(t.analysisResultTitle)}</h3>
  <p><b>${t.marketLabel}:</b> ${escapeHtml(report.question || report.market_slug || report.market_url || "-")}</p>
  <p><b>${t.forecastLabel}:</b> ${escapeHtml(report.display_prediction || "-")}</p>
  <p><b>${t.marketProbabilityLabel}:</b> ${escapeHtml(report.market_probability || "-")}</p>
  <p><b>${t.confidenceLabel}:</b> ${escapeHtml(report.confidence || "-")}</p>
  <p><b>${t.categoryLabel}:</b> ${escapeHtml(report.category || "-")}</p>
  <p><b>${t.reasoningLabel}:</b> ${escapeHtml(report.reasoning || "-")}</p>
  <p><b>${t.mainScenarioLabel}:</b> ${escapeHtml(report.main_scenario || "-")}</p>
  <p><b>${t.altScenarioLabel}:</b> ${escapeHtml(report.alternative_scenario || "-")}</p>
  <p><b>${t.conclusionLabel}:</b> ${escapeHtml(report.conclusion || "-")}</p>
  ${sources.length ? `<p><b>${t.sourcesLabel}:</b> ${escapeHtml(sources.join(', '))}</p>` : ''}
  ${deliverySent ? `<p class="meta">${escapeHtml(t.telegramDelivered)}</p>` : ''}
  <div class="analysis-actions"><button id="copyReportBtn" class="btn btn-primary">${t.copy}</button><button id="closeModalBtn" class="btn btn-secondary">${t.close}</button></div>
  <p id="copyStatus" class="small"></p></div>`;
  document.body.appendChild(m);
  document.getElementById("closeModalBtn").onclick=()=>m.remove();
  document.getElementById("copyReportBtn").onclick=async()=>{await copyText(report.copy_text || report.telegram_text || "");document.getElementById("copyStatus").textContent=t.copied;};
}

function renderAuthed(summary, lang) {
  lang = normalizeLang(lang);
  const t = I18N[lang] || I18N.en;

  setHeroText(lang);

  const user = summary.user || {};
  const balance = summary.balance || {};
  const sub = summary.subscription || {};
  const pricing = summary.pricing || {};

  const displayName = user.username
    ? "@" + user.username
    : (user.first_name || "DeepAlpha User");

  const subscriptionLine = sub.active
    ? `${t.activeUntil} ${escapeHtml(sub.until || sub.raw_subscription_until || "-")}`
    : t.notActive;

  document.getElementById("appRoot").innerHTML = `
    <section class="card">
      <h2>👤 ${t.user}</h2>
      <p class="value">${escapeHtml(displayName)}</p>
      <p class="meta">ID: ${escapeHtml(user.user_id)}</p>
    </section>

    <div class="grid-2">
      <section class="card">
        <h2>💎 ${t.balance}</h2>
        <p class="value">${escapeHtml(balance.tokens || 0)} ${t.tokens}</p>
      </section>

      <section class="card">
        <h2>🔔 ${t.subscription}</h2>
        <p class="meta">${subscriptionLine}</p>
        ${sub.active ? `<span class="status-pill">${t.active}</span>` : ""}
      </section>
    </div>

    <section class="card analysis-card">
      <h2>🔥 ${t.marketAnalysis}</h2>
      <p class="meta">${t.marketAnalysisDesc}</p>
      <input id="marketUrlInput" class="analysis-input" type="url" placeholder="${escapeHtml(t.marketPlaceholder)}" />
      <div class="analysis-actions">
        <button id="quickAnalysisBtn" class="btn btn-primary">${t.quickAnalysis}</button>
        <button id="topAnalysisBtn" class="btn btn-secondary">${t.topAnalysisAction}</button>
      </div>
      <p id="analysisStatus" class="analysis-status"></p>
      <div id="analysisResult" class="analysis-result"></div>
    </section>
    <section class="card">
      <h2>🕓 ${t.historyTitle}</h2>
      <div id="analysisHistory" class="history-list"></div>
    </section>

    <section class="card">
      <h2>💳 ${t.cashier}</h2>
      <p class="meta">${t.cashierDesc}</p>
      <a href="/pay"><button class="btn btn-primary">${t.openCashier}</button></a>
    </section>

    <section class="card">
      <h2>${t.actions}</h2>
      <div class="inline-links">
        <a href="/pay"><button class="btn btn-secondary">${t.openCashier}</button></a>
        <button id="logoutBtn" class="btn btn-secondary">${t.logout}</button>
      </div>
    </section>
  `;

  document.getElementById("logoutBtn").onclick = logout;

  const input = document.getElementById("marketUrlInput");
  const status = document.getElementById("analysisStatus");
  const quickBtn = document.getElementById("quickAnalysisBtn");
  const topBtn = document.getElementById("topAnalysisBtn");
  const resultBox = document.getElementById("analysisResult");
  const historyBox = document.getElementById("analysisHistory");

  const renderHistory = async () => {
    const res = await callHistory();
    if (!res.ok || !res.data?.ok) {
      historyBox.innerHTML = `<p class="meta">${escapeHtml(t.authError)}</p>`;
      return;
    }
    const items = Array.isArray(res.data.items) ? res.data.items : [];
    if (!items.length) {
      historyBox.innerHTML = `<p class="meta">${escapeHtml(t.historyEmpty)}</p>`;
      return;
    }
    historyBox.innerHTML = items.map((item) => `
<<<<<<< HEAD
      <div class="history-item" data-id="${item.id}">
=======
      <div class="history-item" data-history-id="${escapeHtml(item.id || "")}" data-status="${escapeHtml(item.status || "")}">
>>>>>>> pr-81-webapp-delivery-history-sync
        <div><b>${escapeHtml((item.analysis_type || "").toUpperCase())}</b> · ${escapeHtml(item.status || "")}</div>
        <div class="small">${escapeHtml(item.question || item.market_slug || item.market_url || "")}</div>
        <div class="small">${escapeHtml(item.display_prediction || "")}</div>
        <div class="small">${escapeHtml(item.created_at || "")}</div>
      </div>
    `).join("");
    historyBox.querySelectorAll(".history-item").forEach((el)=>{el.onclick=async()=>{const id=el.getAttribute("data-id");const d=await callHistoryItem(id);if(d.ok&&d.data?.ok&&d.data.item?.result){renderReportModal(d.data.item.result,t,false);}}});
  };

  const runAnalyze = async (mode) => {
    const url = String(input?.value || "").trim();
    if (!url || !url.toLowerCase().includes("polymarket.com")) {
      status.textContent = t.validateMarketUrl;
      return;
    }
    quickBtn.disabled = true;
    topBtn.disabled = true;
    status.textContent = mode === "top" ? t.startingTop : t.analyzingMarket;
    try {
      const res = await callAnalyze(url, mode);
      if (res.status === 401) { status.textContent = t.authError; return; }
      if (!res.ok) {
        status.textContent = res.data?.error === "invalid_url" ? t.invalidMarketUrl : (res.data?.error === "not_enough_tokens" ? t.notEnoughTokens : t.analysisError);
        resultBox.innerHTML = "";
        return;
      }
      if (res.data?.status === "coming_soon") {
        status.textContent = t.comingSoonAnalysis;
        resultBox.innerHTML = "";
        await renderHistory();
        return;
      }
      if (res.data?.status === "success") {
        const result = res.data?.result || {};
        const deliverySent = !!res.data?.telegram_delivery?.sent;
        status.textContent = t.analysisOk;
        resultBox.innerHTML = `<div class="history-item"><p><b>${escapeHtml(t.forecastLabel)}:</b> ${escapeHtml(result.display_prediction || "-")}</p><p class="small"><b>${escapeHtml(t.confidenceLabel)}:</b> ${escapeHtml(result.confidence || "-")}</p><p class="small"><b>${escapeHtml(t.conclusionLabel)}:</b> ${escapeHtml((result.conclusion || "").slice(0, 180))}</p><button id="openReportBtn" class="btn btn-secondary">${t.openReport}</button></div>`;
        const openBtn = document.getElementById("openReportBtn");
        if (openBtn) openBtn.onclick = () => renderReportModal(result, t, deliverySent);
        renderReportModal(result, t, deliverySent);
        await renderHistory();
        return;
      }
      status.textContent = t.analysisError;
    } finally {
      quickBtn.disabled = false;
      topBtn.disabled = false;
    }
  };


  historyBox.onclick = async (ev) => {
    const row = ev.target.closest(".history-item");
    if (!row) return;
    const itemId = row.getAttribute("data-history-id");
    const itemStatus = row.getAttribute("data-status") || "";
    if (!itemId) return;
    const resp = await fetch(`/api/webapp/history/${encodeURIComponent(itemId)}`, { credentials: "include" });
    const data = await resp.json();
    const item = data?.item || {};
    const result = item.result_json || {};
    const hasMeaningful = Boolean(
      result.question || result.display_prediction || result.conclusion || result.copy_text
    );
    if (itemStatus === "success" && hasMeaningful) {
      const out = result;
      resultBox.innerHTML = `
        <p><b>${escapeHtml(t.analysisResultTitle)}</b></p>
        <p class="small"><b>${escapeHtml(t.marketLabel)}:</b> ${escapeHtml(out.question || item.question || "")}</p>
        <p><b>${escapeHtml(t.forecastLabel)}:</b> ${escapeHtml(out.display_prediction || item.display_prediction || "")}</p>
        <p class="small"><b>${escapeHtml(t.marketProbabilityLabel)}:</b> ${escapeHtml(out.market_probability || item.market_probability || "")}</p>
        <p class="small"><b>${escapeHtml(t.confidenceLabel)}:</b> ${escapeHtml(out.confidence || item.confidence || "")}</p>
        <p class="small"><b>${escapeHtml(t.categoryLabel)}:</b> ${escapeHtml(out.category || item.category || "")}</p>
        <p class="small"><b>${escapeHtml(t.conclusionLabel)}:</b> ${escapeHtml(out.conclusion || out.copy_text || "")}</p>
      `;
      status.textContent = t.analysisOk;
      return;
    }
    status.textContent = t.historyItemUnavailable;
  };

  quickBtn.onclick = () => runAnalyze("quick");
  topBtn.onclick = () => runAnalyze("top");
  renderHistory();
}

async function init() {
  await telegramAuthIfAvailable();

  const me = await callMe();
  if (!(me && me.ok && me.auth && me.auth.authenticated)) {
    return renderGuest(guestLangFallback());
  }

  const summaryResp = await callSummary();
  if (!summaryResp.ok) {
    return renderGuest(guestLangFallback());
  }

  const lang = normalizeLang(summaryResp.data?.language || summaryResp.data?.user?.language || "en");
  renderAuthed(summaryResp.data, lang);
}

init();
