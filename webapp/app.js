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
    analyzingQuickButton: "Analyzing market...",
    analyzingQuickStatus: "DeepAlpha is analyzing the market. This usually takes up to 30–60 seconds.",
    analyzingTopButton: "Starting Top Analysis...",
    analyzingTopStatus: "DeepAlpha is running advanced analysis. This may take 1–3 minutes.",
    validateMarketUrl: "Paste a Polymarket link.",
    comingSoonAnalysis: "Web analysis execution is not enabled yet. Full reports will appear here soon.",
    analysisOk: "Analysis completed.",
    analysisDoneTitle: "✅ Analysis completed",
    analysisDoneHint: "Open the full report to view the complete DeepAlpha Signal.",
    topAnalysisDoneTitle: "✅ Top Analysis completed",
    analysisError: "Analysis failed. Please try again.",
    notEnoughTokens: "Not enough tokens. Open cashier.",
    historyEmpty: "No analysis history yet.",
    analysisResultTitle: "Analysis result",
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
    ,
    loadMore: "Load more"
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
    analyzingQuickButton: "Анализирую рынок...",
    analyzingQuickStatus: "DeepAlpha анализирует рынок. Обычно это занимает до 30–60 секунд.",
    analyzingTopButton: "Запускаю Top Analysis...",
    analyzingTopStatus: "DeepAlpha выполняет расширенный анализ. Это может занять 1–3 минуты.",
    validateMarketUrl: "Вставьте ссылку Polymarket.",
    comingSoonAnalysis: "Пока запуск анализа в WebApp не включён. Скоро здесь появится полный отчёт.",
    analysisOk: "Анализ выполнен.",
    analysisDoneTitle: "✅ Анализ выполнен",
    analysisDoneHint: "Откройте полный отчёт, чтобы увидеть весь DeepAlpha Signal.",
    topAnalysisDoneTitle: "✅ Top Analysis выполнен",
    analysisError: "Ошибка анализа. Попробуйте снова.",
    notEnoughTokens: "Недостаточно токенов. Откройте кассу.",
    historyEmpty: "История пока пустая.",
    analysisResultTitle: "Результат анализа",
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
    historyItemUnavailable: "Для этой записи пока нет полного отчёта.",
    loadMore: "Загрузить ещё"
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


function showReportModal(text, lang, telegramSent) {
  const t = I18N[normalizeLang(lang)] || I18N.en;
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  const tgNote = telegramSent ? '<p class="small">Отчёт также отправлен в Telegram.</p>' : "";
  overlay.innerHTML = `
    <div class="modal-card">
      <h2>${escapeHtml(t.analysisResultTitle)}</h2>
      ${tgNote}
      <div class="report-text">${escapeHtml(text || "")}</div>
      <div class="analysis-actions" style="margin-top:12px;">
        <button class="btn btn-secondary" id="copyReportBtn">Copy</button>
        <button class="btn btn-primary" id="closeReportBtn">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector("#closeReportBtn").onclick = () => overlay.remove();
  overlay.querySelector("#copyReportBtn").onclick = async () => {
    await navigator.clipboard.writeText(text || "");
  };
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
}

async function callHistory(limit = 10, offset = 0) {
  const r = await fetch(`/api/webapp/history?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`, { credentials: "include" });
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
  let isRunning = false;
  let historyOffset = 0;
  const historyLimit = 10;
  let historyHasMore = false;
  let historyItems = [];

  const setRunningState = (running, mode = "quick") => {
    isRunning = running;
    quickBtn.disabled = running;
    topBtn.disabled = running;
    if (running) {
      quickBtn.textContent = mode === "quick" ? t.analyzingQuickButton : t.quickAnalysis;
      topBtn.textContent = mode === "top" ? t.analyzingTopButton : t.topAnalysisAction;
      status.textContent = mode === "top" ? t.analyzingTopStatus : t.analyzingQuickStatus;
    } else {
      quickBtn.textContent = t.quickAnalysis;
      topBtn.textContent = t.topAnalysisAction;
    }
  };

  const formatTypeLabel = (analysisType) => {
    const key = String(analysisType || "").toLowerCase();
    return key === "top" ? "Top Analysis" : (lang === "ru" ? "Быстрый анализ" : "Quick Analysis");
  };

  const formatStatusLabel = (statusValue) => {
    const key = String(statusValue || "").toLowerCase();
    if (lang === "ru") {
      if (key === "success") return "готово";
      if (key === "error") return "ошибка";
      if (key === "coming_soon") return "скоро";
    }
    if (key === "coming_soon") return "coming soon";
    return key || "error";
  };

  const renderHistoryItems = () => {
    if (!historyItems.length) {
      historyBox.innerHTML = `<p class="meta">${escapeHtml(t.historyEmpty)}</p>`;
      return;
    }
    const rows = historyItems.map((item) => `
      <div class="history-item" data-history-id="${escapeHtml(item.id || "")}" data-status="${escapeHtml(item.status || "")}">
        <div><b>${escapeHtml(formatTypeLabel(item.analysis_type))}</b> · ${escapeHtml(formatStatusLabel(item.status || ""))}</div>
        <div class="small">${escapeHtml(item.question || item.market_slug || item.market_url || "")}</div>
        <div class="small">${escapeHtml(item.display_prediction || "")}</div>
        <div class="small">${escapeHtml(item.created_at || "")}</div>
      </div>
    `).join("");
    const loadMoreBtn = historyHasMore ? `<div class="history-load-more-wrap"><button id="historyLoadMoreBtn" class="btn btn-secondary">${escapeHtml(t.loadMore)}</button></div>` : "";
    historyBox.innerHTML = rows + loadMoreBtn;
    const btn = document.getElementById("historyLoadMoreBtn");
    if (btn) btn.onclick = () => loadHistory(false);
  };

  const loadHistory = async (reset = true) => {
    if (reset) {
      historyOffset = 0;
      historyItems = [];
      historyHasMore = false;
    }
    const res = await callHistory(historyLimit, historyOffset);
    if (!res.ok || !res.data?.ok) {
      historyBox.innerHTML = `<p class="meta">${escapeHtml(t.authError)}</p>`;
      return;
    }
    const items = Array.isArray(res.data.items) ? res.data.items : [];
    historyItems = historyItems.concat(items);
    historyOffset += items.length;
    historyHasMore = Boolean(res.data?.pagination?.has_more);
    renderHistoryItems();
  };

  const runAnalyze = async (mode) => {
    if (isRunning) return;
    const url = String(input?.value || "").trim();
    if (!url || !url.toLowerCase().includes("polymarket.com")) {
      status.textContent = t.validateMarketUrl;
      return;
    }

    setRunningState(true, mode);
    const res = await callAnalyze(url, mode);
    setRunningState(false);
    if (res.status === 401) {
      status.textContent = t.authError;
      return;
    }

    if (!res.ok) {
      if (res.data?.error === "invalid_url") {
        status.textContent = t.invalidMarketUrl;
        resultBox.innerHTML = "";
        return;
      }
      if (res.data?.error === "not_enough_tokens") {
        status.textContent = t.notEnoughTokens;
        resultBox.innerHTML = "";
        return;
      }
      status.textContent = t.authError;
      resultBox.innerHTML = "";
      return;
    }

    if (res.data?.ok && res.data?.status === "coming_soon") {
      status.textContent = t.comingSoonAnalysis;
      resultBox.innerHTML = "";
      await loadHistory(true);
      return;
    }

    if (res.data?.ok && res.data?.status === "success") {
      const out = res.data.result || {};
      status.textContent = mode === "top" ? t.topAnalysisDoneTitle : t.analysisDoneTitle;
      resultBox.innerHTML = `
        <p><b>${escapeHtml(mode === "top" ? t.topAnalysisDoneTitle : t.analysisDoneTitle)}</b></p>
        <p class="small">${escapeHtml(t.analysisDoneHint)}</p>
        <p class="small"><b>${escapeHtml(t.marketLabel)}:</b> ${escapeHtml(out.question || "")}</p>
        <p><b>${escapeHtml(t.forecastLabel)}:</b> ${escapeHtml(out.display_prediction || "")}</p>
        <p class="small"><b>${escapeHtml(t.marketProbabilityLabel)}:</b> ${escapeHtml(out.market_probability || "")}</p>
        <p class="small"><b>${escapeHtml(t.confidenceLabel)}:</b> ${escapeHtml(out.confidence || "")}</p>
        <p class="small"><b>${escapeHtml(t.categoryLabel)}:</b> ${escapeHtml(out.category || "")}</p>
        <p class="small"><b>${escapeHtml(t.conclusionLabel)}:</b> ${escapeHtml(out.summary || "")}</p>
      `;
      showReportModal(out.canonical_text || out.copy_text || out.telegram_text || "", lang, Boolean(res.data?.telegram_delivery?.sent));
      await loadHistory(true);
      return;
    }

    status.textContent = t.analysisError;
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
      const canonical = result.canonical_text || result.copy_text || result.telegram_text || "";
      if (canonical) {
        showReportModal(canonical, lang, false);
        status.textContent = t.analysisOk;
        return;
      }
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
  loadHistory(true);
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
