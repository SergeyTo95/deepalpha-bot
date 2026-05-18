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

if (window.__tonRefreshIntervalId) {
  clearInterval(window.__tonRefreshIntervalId);
  window.__tonRefreshIntervalId = null;
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
    tgNote: "Open from Telegram for automatic login, or continue with Google.",
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
    topMainRunning: "DeepAlpha is running advanced Top Analysis. This is a deep multi-agent analysis and may take 2–4 minutes. Do not close the window.",
    topStillRunningTimeout: "Analysis is still running. Refresh history in a minute — the report will appear there after completion.",
    validateMarketUrl: "Paste a Polymarket link.",
    comingSoonAnalysis: "Web analysis execution is not enabled yet. Full reports will appear here soon.",
    analysisOk: "Analysis completed.",
    analysisDoneTitle: "✅ Analysis completed",
    analysisDoneHint: "Open the full report to view the complete DeepAlpha Signal.",
    topAnalysisDoneTitle: "✅ Top Analysis completed",
    topAnalysisDoneHint: "Open the full report to view the complete DeepAlpha Top Analysis.",
    openReport: "Open report",
    copy: "Copy",
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
    ,
    tonWallet: "TON Wallet",
    tonTokensDisabled: "TON tokens are not enabled yet.",
    refresh: "Refresh",
    copyAddress: "Copy address",
    sendTon: "Send TON",
    sendMax: "Send MAX"
  },
  ru: {
    title: "Личный кабинет",
    subtitle: "AI-анализ рынков прогнозов",
    guestPrompt: "Войдите, чтобы открыть личный кабинет DeepAlpha",
    tg: "Продолжить через Telegram",
    gg: "Продолжить через Google",
    tgNote: "Откройте из Telegram для авто-входа или войдите через Google.",
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
    topMainRunning: "DeepAlpha выполняет расширенный Top Analysis. Это глубокий анализ с несколькими ИИ-модулями и может занять 2–4 минуты. Не закрывайте окно.",
    topStillRunningTimeout: "Анализ всё ещё выполняется. Обновите историю через минуту — отчёт появится там после завершения.",
    validateMarketUrl: "Вставьте ссылку Polymarket.",
    comingSoonAnalysis: "Пока запуск анализа в WebApp не включён. Скоро здесь появится полный отчёт.",
    analysisOk: "Анализ выполнен.",
    analysisDoneTitle: "✅ Анализ выполнен",
    analysisDoneHint: "Откройте полный отчёт, чтобы увидеть весь DeepAlpha Signal.",
    topAnalysisDoneTitle: "✅ Top Analysis выполнен",
    topAnalysisDoneHint: "Откройте полный отчёт, чтобы увидеть расширенный DeepAlpha Top Analysis.",
    openReport: "Открыть отчёт",
    copy: "Копировать",
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
    ,
    tonWallet: "TON кошелёк",
    tonTokensDisabled: "TON-токены пока не подключены.",
    refresh: "Обновить",
    copyAddress: "Скопировать адрес",
    sendTon: "Отправить TON",
    sendMax: "Отправить всё"
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

async function callTopAnalyzeStart(url) {
  const r = await fetch("/api/webapp/analyze/start", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, mode: "top" })
  });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function callAnalyzeStatus(jobId) {
  const r = await fetch(`/api/webapp/analyze/status/${encodeURIComponent(jobId)}`, { credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}

async function callTonWallet() {
  const r = await fetch("/api/wallets/ton", { credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}
async function callTonRefresh() {
  const r = await fetch("/api/wallets/ton/refresh", { method: "POST", credentials: "include" });
  return { ok: r.ok, status: r.status, data: await r.json() };
}
async function callTonSend(destination_address, amount_ton, comment) {
  const r = await fetch("/api/wallets/ton/send", {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ destination_address, amount_ton, comment })
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

async function copyToClipboardSafe(text) {
  const value = String(text || "");
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  try {
    return document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
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
    window.location.href = "https://t.me/DeepAlphaAI_bot?start=web_login";
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
  const parsedTokens = Number(balance.tokens || 0);
  const safeTokens = Number.isFinite(parsedTokens) ? parsedTokens : 0;

  const displayName = user.username
    ? "@" + user.username
    : (user.first_name || user.name || user.email || "User");

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
        <p class="value">${escapeHtml(safeTokens)} ${t.tokens}</p>
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
      <h2>💎 ${t.tonWallet}</h2>
      <p class="meta" id="tonNetworkLine">-</p>
      <p class="small" id="tonAddressLine">-</p>
      <p class="value" id="tonBalanceLine">-</p>
      <div class="inline-links">
        <button id="tonRefreshBtn" class="btn btn-secondary">🔄 ${t.refresh}</button>
        <button id="tonCopyBtn" class="btn btn-secondary">📋 ${t.copyAddress}</button>
      </div>
      <div class="inline-links" style="margin-top:8px;">
        <input id="tonDestInput" class="analysis-input" placeholder="EQ..." />
        <input id="tonAmountInput" class="analysis-input" placeholder="0.1" />
      </div>
      <div class="inline-links" style="margin-top:8px;">
        <button id="tonSendBtn" class="btn btn-primary">📤 ${t.sendTon}</button>
        <button id="tonMaxBtn" class="btn btn-secondary">💰 ${t.sendMax}</button>
      </div>
      <p class="small" id="tonJettonsLine">${t.tonTokensDisabled}</p>
      <p class="small" id="tonStatusLine"></p>
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
  const tonNetworkLine = document.getElementById("tonNetworkLine");
  const tonAddressLine = document.getElementById("tonAddressLine");
  const tonBalanceLine = document.getElementById("tonBalanceLine");
  const tonStatusLine = document.getElementById("tonStatusLine");
  let isRunning = false;
  let historyOffset = 0;
  const historyLimit = 10;
  let historyHasMore = false;
  let historyItems = [];
  let tonWalletData = null;
  let tonWalletState = {
    balanceNano: 0n,
    balanceDisplay: "0",
    feeReserveNano: 0n,
    feeReserveDisplay: "0",
    maxSendNano: 0n,
    maxSendDisplay: "0"
  };
  let tonRefreshRunning = false;

  const parseTonToNanoClient = (value) => {
    const s = String(value || "").trim().replace(",", ".");
    if (!/^\d+(\.\d+)?$/.test(s)) return null;
    const parts = s.split(".");
    const whole = parts[0] || "0";
    const fracRaw = parts[1] || "";
    const frac = (fracRaw + "000000000").slice(0, 9);
    try {
      return BigInt(whole) * 1000000000n + BigInt(frac);
    } catch (_e) {
      return null;
    }
  };

  const tonDisplayFromNanoClient = (nano) => {
    const n = nano < 0n ? 0n : nano;
    const whole = n / 1000000000n;
    const frac = (n % 1000000000n).toString().padStart(9, "0").replace(/0+$/, "");
    return frac ? `${whole.toString()}.${frac}` : whole.toString();
  };

  const mapTonError = (code, details, errorDetail = "unknown") => {
    if (String(code || "") === "insufficient_balance") {
      return lang === "ru"
        ? `Недостаточно TON с учётом комиссии.\nБаланс: ${details.balanceDisplay} TON\nРезерв комиссии: ~${details.feeReserveDisplay} TON\nМаксимум к отправке: ${details.maxSendDisplay} TON\n\nНажмите “Отправить всё”, чтобы отправить максимум.`
        : `Insufficient TON including fee reserve.\nBalance: ${details.balanceDisplay} TON\nFee reserve: ~${details.feeReserveDisplay} TON\nMaximum send amount: ${details.maxSendDisplay} TON\n\nTap “Send MAX” to use the maximum.`;
    }
    if (String(code || "") === "send_failed") {
      const detailMapRu = {
        toncenter_rejected: "Сеть TON отклонила транзакцию. Попробуйте уменьшить сумму или обновить баланс.",
        insufficient_network_fee: "Недостаточно TON с учётом реальной комиссии сети. Уменьшите сумму.",
        seqno_or_account_state: "Не удалось подтвердить состояние кошелька. Обновите баланс и попробуйте ещё раз.",
        toncenter_unavailable: "TON Center временно недоступен. Попробуйте позже.",
        unknown: "Ошибка отправки в сеть."
      };
      const detailMapEn = {
        toncenter_rejected: "The TON network rejected the transaction. Try a smaller amount or refresh balance.",
        insufficient_network_fee: "Not enough TON after real network fee. Reduce the amount.",
        seqno_or_account_state: "Wallet state could not be confirmed. Refresh balance and try again.",
        toncenter_unavailable: "TON Center is temporarily unavailable. Please try later.",
        unknown: "Network send failed."
      };
      return (lang === "ru" ? detailMapRu : detailMapEn)[String(errorDetail || "unknown")] || (lang === "ru" ? detailMapRu.unknown : detailMapEn.unknown);
    }
    const en = {
      invalid_address: "Invalid TON address.",
      invalid_amount: "Invalid amount.",
      disabled: "TON wallet is temporarily unavailable.",
      setup_required: "TON wallet backend is not fully configured.",
      send_failed: "Network send failed.",
      signing_failed: "Signing failed.",
      balance_unavailable: "Unable to fetch on-chain balance."
    };
    const ru = {
      invalid_address: "Неверный TON адрес.",
      invalid_amount: "Неверная сумма.",
      disabled: "TON кошелёк временно недоступен.",
      setup_required: "TON кошелёк не настроен на сервере.",
      send_failed: "Ошибка отправки в сеть.",
      signing_failed: "Ошибка подписи транзакции.",
      balance_unavailable: "Не удалось получить баланс из сети."
    };
    return (lang === "ru" ? ru : en)[String(code || "")] || (lang === "ru" ? "Ошибка отправки TON." : "TON send failed.");
  };

  const loadTonWallet = async (doRefresh) => {
    if (tonRefreshRunning) return;
    tonRefreshRunning = true;
    const res = doRefresh ? await callTonRefresh() : await callTonWallet();
    if (!res.ok || !res.data?.ok) {
      tonStatusLine.textContent = "TON wallet unavailable";
      tonRefreshRunning = false;
      return;
    }
    tonWalletData = res.data;
    tonNetworkLine.textContent = `Network: ${String(res.data.network || "").toUpperCase()}`;
    tonAddressLine.textContent = `Address: ${res.data.wallet_address || "-"}`;
    tonBalanceLine.textContent = `${res.data.balance_display || "0"} TON`;
    tonStatusLine.textContent = "";
    const balanceNano = BigInt(String(res.data.balance_nano || "0"));
    const feeReserveNano = BigInt(String(res.data.fee_reserve_nano || "0"));
    let maxSendNano = balanceNano - feeReserveNano;
    if (maxSendNano < 0n) maxSendNano = 0n;
    tonWalletState = {
      balanceNano,
      balanceDisplay: String(res.data.balance_display || "0"),
      feeReserveNano,
      feeReserveDisplay: String(res.data.fee_reserve_display || tonDisplayFromNanoClient(feeReserveNano)),
      maxSendNano,
      maxSendDisplay: tonDisplayFromNanoClient(maxSendNano)
    };
    const jettons = Array.isArray(res.data.enabled_jettons) ? res.data.enabled_jettons : [];
    document.getElementById("tonJettonsLine").textContent = jettons.length ? `Tokens on TON: ${jettons.length}` : t.tonTokensDisabled;
    tonRefreshRunning = false;
  };

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
  document.getElementById("tonRefreshBtn").onclick = async () => { await loadTonWallet(true); };
  document.getElementById("tonCopyBtn").onclick = async () => { if (tonWalletData?.wallet_address) await copyToClipboardSafe(tonWalletData.wallet_address); };
  document.getElementById("tonMaxBtn").onclick = async () => {
    if (tonWalletState.maxSendNano <= 0n) {
      tonStatusLine.textContent = lang === "ru"
        ? `Недостаточно TON для отправки с учётом резерва комиссии.\nБаланс: ${tonWalletState.balanceDisplay} TON\nРезерв: ~${tonWalletState.feeReserveDisplay} TON`
        : `Not enough TON to send after fee reserve.\nBalance: ${tonWalletState.balanceDisplay} TON\nReserve: ~${tonWalletState.feeReserveDisplay} TON`;
      return;
    }
    document.getElementById("tonAmountInput").value = tonWalletState.maxSendDisplay;
    tonStatusLine.textContent = lang === "ru"
      ? `Будет отправлено всё доступное за вычетом резерва комиссии: ${tonWalletState.maxSendDisplay} TON`
      : `Will send all available after fee reserve: ${tonWalletState.maxSendDisplay} TON`;
  };
  document.getElementById("tonSendBtn").onclick = async () => {
    const destination = String(document.getElementById("tonDestInput").value || "").trim();
    const amount = String(document.getElementById("tonAmountInput").value || "").trim();
    const amountNano = parseTonToNanoClient(amount);
    if (amountNano === null || amountNano <= 0n) {
      tonStatusLine.textContent = mapTonError("invalid_amount", tonWalletState);
      return;
    }
    if (amountNano > tonWalletState.maxSendNano) {
      tonStatusLine.textContent = mapTonError("insufficient_balance", tonWalletState);
      return;
    }
    const sent = await callTonSend(destination, amount, "");
    if (sent.data?.ok) {
      tonStatusLine.textContent = lang === "ru" ? "✅ TON отправлен.\nБаланс обновляется..." : "✅ TON sent.\nRefreshing balance...";
      await loadTonWallet(true);
      return;
    }
    const details = {
      balanceDisplay: String(sent.data?.balance_display || tonWalletState.balanceDisplay || "0"),
      feeReserveDisplay: String(sent.data?.fee_reserve_display || tonWalletState.feeReserveDisplay || "0"),
      maxSendDisplay: String(sent.data?.max_send_display || tonWalletState.maxSendDisplay || "0")
    };
    tonStatusLine.textContent = mapTonError(sent.data?.error || "send_failed", details, sent.data?.error_detail || "unknown");
  };
  loadTonWallet(false).then(() => loadTonWallet(true));
  if (window.__tonRefreshIntervalId) clearInterval(window.__tonRefreshIntervalId);
  window.__tonRefreshIntervalId = setInterval(() => {
    if (document.getElementById("tonNetworkLine")) loadTonWallet(true);
  }, 30000);

  const topProgressPhrases = {
    en: [
      "Top Analysis started...",
      "Preparing base market context...",
      "Researching sources and news...",
      "Checking risks and weak points...",
      "Building the final DeepAlpha forecast...",
      "Almost ready, assembling the report..."
    ],
    ru: [
      "Top Analysis запущен...",
      "Готовлю базовый рыночный контекст...",
      "Исследую источники и новости...",
      "Проверяю риски и слабые места прогноза...",
      "Формирую финальный прогноз DeepAlpha...",
      "Почти готово, собираю отчёт..."
    ]
  };

  const runTopAnalyzeJob = async (url) => {
    setRunningState(true, "top");
    status.textContent = t.topMainRunning;
    resultBox.innerHTML = "";
    const start = await callTopAnalyzeStart(url);
    if (!start.ok || !start.data?.ok || !start.data?.job_id) {
      setRunningState(false);
      if (start.status === 401) status.textContent = t.authError;
      else if (start.data?.error === "invalid_url") status.textContent = t.invalidMarketUrl;
      else if (start.data?.error === "not_enough_tokens") status.textContent = t.notEnoughTokens;
      else status.textContent = t.analysisError;
      return;
    }
    const jobId = start.data.job_id;
    const startedAt = Date.now();
    let tick = 0;
    while (Date.now() - startedAt < 6 * 60 * 1000) {
      const polled = await callAnalyzeStatus(jobId);
      if (polled.status === 401) {
        setRunningState(false);
        status.textContent = t.authError;
        return;
      }
      if (!polled.ok || !polled.data?.ok) {
        setRunningState(false);
        status.textContent = t.analysisError;
        return;
      }
      const st = String(polled.data.status || "").toLowerCase();
      if (st === "queued" || st === "running") {
        const phrases = topProgressPhrases[lang] || topProgressPhrases.en;
        const technicalProgressCodes = new Set([
          "top_analysis_started",
          "queued",
          "running",
          "top_analysis_running"
        ]);
        const backendProgress = String(polled.data.progress || "").trim();
        const phrase = backendProgress && !technicalProgressCodes.has(backendProgress)
          ? backendProgress
          : phrases[tick % phrases.length];
        status.textContent = `${t.topMainRunning} ${phrase}`;
        tick += 1;
        await new Promise((r) => setTimeout(r, 3500));
        continue;
      }
      if (st === "error") {
        setRunningState(false);
        status.textContent = polled.data?.error === "not_enough_tokens" ? t.notEnoughTokens : t.analysisError;
        return;
      }
      if (st === "success") {
        setRunningState(false);
        const out = polled.data.result || {};
        const reportText = out.canonical_text || out.copy_text || out.telegram_text || "";
        const telegramSent = Boolean(polled.data?.telegram_delivery?.sent || out?.telegram_delivery?.sent);
        status.textContent = t.topAnalysisDoneTitle;
        if (reportText) {
          resultBox.innerHTML = `<p><b>${escapeHtml(t.topAnalysisDoneTitle)}</b></p><p class="small">${escapeHtml(t.topAnalysisDoneHint)}</p><div class="analysis-actions" style="margin-top:12px;"><button id="openReportInlineBtn" class="btn btn-secondary">${escapeHtml(t.openReport)}</button><button id="copyReportInlineBtn" class="btn btn-primary">${escapeHtml(t.copy)}</button></div>`;
          showReportModal(reportText, lang, telegramSent);
          const openBtn = document.getElementById("openReportInlineBtn");
          if (openBtn) openBtn.onclick = () => showReportModal(reportText, lang, telegramSent);
          const copyBtn = document.getElementById("copyReportInlineBtn");
          if (copyBtn) copyBtn.onclick = async () => { await copyToClipboardSafe(reportText); };
        }
        await loadHistory(true);
        return;
      }
      await new Promise((r) => setTimeout(r, 3500));
    }
    setRunningState(false);
    status.textContent = t.topStillRunningTimeout;
    await loadHistory(true);
  };

  const runAnalyze = async (mode) => {
    if (isRunning) return;
    const url = String(input?.value || "").trim();
    if (!url || !url.toLowerCase().includes("polymarket.com")) {
      status.textContent = t.validateMarketUrl;
      return;
    }

    if (mode === "top") {
      await runTopAnalyzeJob(url);
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

    if (res.data?.ok && res.data?.status === "success") {
      const out = res.data.result || {};
      const reportText = out.canonical_text || out.copy_text || out.telegram_text || "";
      const telegramSent = Boolean(res.data?.telegram_delivery?.sent);
      status.textContent = mode === "top" ? t.topAnalysisDoneTitle : t.analysisDoneTitle;
      if (reportText) {
        resultBox.innerHTML = `
          <p><b>${escapeHtml(mode === "top" ? t.topAnalysisDoneTitle : t.analysisDoneTitle)}</b></p>
          <p class="small">${escapeHtml(mode === "top" ? t.topAnalysisDoneHint : t.analysisDoneHint)}</p>
          <div class="analysis-actions" style="margin-top:12px;">
            <button id="openReportInlineBtn" class="btn btn-secondary">${escapeHtml(t.openReport)}</button>
            <button id="copyReportInlineBtn" class="btn btn-primary">${escapeHtml(t.copy)}</button>
          </div>
        `;
        showReportModal(reportText, lang, telegramSent);
        const openBtn = document.getElementById("openReportInlineBtn");
        if (openBtn) openBtn.onclick = () => showReportModal(reportText, lang, telegramSent);
        const copyBtn = document.getElementById("copyReportInlineBtn");
        if (copyBtn) copyBtn.onclick = async () => { await copyToClipboardSafe(reportText); };
      } else {
        resultBox.innerHTML = `
          <p><b>${escapeHtml(mode === "top" ? t.topAnalysisDoneTitle : t.analysisDoneTitle)}</b></p>
          <p class="small"><b>${escapeHtml(t.marketLabel)}:</b> ${escapeHtml(out.question || "")}</p>
          <p><b>${escapeHtml(t.forecastLabel)}:</b> ${escapeHtml(out.display_prediction || "")}</p>
          <p class="small"><b>${escapeHtml(t.marketProbabilityLabel)}:</b> ${escapeHtml(out.market_probability || "")}</p>
          <p class="small"><b>${escapeHtml(t.confidenceLabel)}:</b> ${escapeHtml(out.confidence || "")}</p>
          <p class="small"><b>${escapeHtml(t.categoryLabel)}:</b> ${escapeHtml(out.category || "")}</p>
          <p class="small"><b>${escapeHtml(t.conclusionLabel)}:</b> ${escapeHtml(out.summary || "")}</p>
        `;
      }
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
