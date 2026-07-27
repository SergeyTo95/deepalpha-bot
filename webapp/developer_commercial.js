(() => {
  const POLL_MS = 12000;
  const OPEN_STATUSES = new Set(["pending", "awaiting_payment", "payment_detected", "paid", "crediting"]);
  let commercial = null;
  let loading = false;

  function isRu() {
    return String(document.documentElement.lang || navigator.language || "ru").toLowerCase().startsWith("ru");
  }

  const copy = {
    ru: {
      title: "Коммерческий API", disabled: "Коммерческий запуск выключен администратором.",
      purchasesOff: "Покупка API credits временно выключена.", packages: "Купить API credits",
      noPackages: "Пакеты ещё не настроены.", buy: "Создать счёт", credits: "credits",
      invoices: "Счета", noInvoices: "Счетов пока нет.", amount: "Сумма", expires: "Действует до",
      reference: "Платёжная ссылка", address: "Адрес оплаты", instructions: "Инструкция",
      openWallet: "Открыть кошелёк", refresh: "Проверить оплату", cancel: "Отменить",
      copy: "Копировать", copied: "Скопировано", live: "LIVE ACCESS", company: "Компания / проект",
      website: "Сайт (http/https)", useCase: "Как будет использоваться API", expected: "Запросов в месяц",
      contact: "Контакт", requestLive: "Отправить заявку", approved: "Live-доступ одобрен",
      requested: "Заявка рассматривается", rejected: "Заявка отклонена", suspended: "Live-доступ приостановлен",
      adminComment: "Комментарий администратора", issueLive: "Создать LIVE-ключ", keyName: "Название ключа",
      liveGlobalOff: "Глобальная выдача LIVE-ключей выключена.", spend: "SPEND CONTROLS",
      daily: "Дневной лимит credits", monthly: "Месячный лимит credits", threshold: "Порог низкого баланса",
      optional: "пусто — выключено", save: "Сохранить", balance: "Баланс", usedToday: "Расход сегодня",
      usedMonth: "Расход за месяц", remainingQuick: "Осталось Quick Analysis", remainingScan: "Осталось Opportunity Scan",
      low: "Низкий баланс", autoRechargeOff: "Auto recharge недоступен без reusable payment method.",
      service: "Коммерческий API временно недоступен.", confirmCancel: "Отменить неоплаченный счёт?",
      manual: "Оплата подтверждается администратором вручную.", automatic: "TON-транзакция проверяется Treasury worker.",
      testLive: "TEST + LIVE",
    },
    en: {
      title: "Commercial API", disabled: "Commercial launch is disabled by the administrator.",
      purchasesOff: "API credit purchases are temporarily disabled.", packages: "Buy API credits",
      noPackages: "No packages are configured.", buy: "Create invoice", credits: "credits",
      invoices: "Invoices", noInvoices: "No invoices yet.", amount: "Amount", expires: "Expires",
      reference: "Payment reference", address: "Payment address", instructions: "Instructions",
      openWallet: "Open wallet", refresh: "Check payment", cancel: "Cancel",
      copy: "Copy", copied: "Copied", live: "LIVE ACCESS", company: "Company / project",
      website: "Website (http/https)", useCase: "How the API will be used", expected: "Requests per month",
      contact: "Contact", requestLive: "Submit request", approved: "Live access approved",
      requested: "Application under review", rejected: "Application rejected", suspended: "Live access suspended",
      adminComment: "Administrator comment", issueLive: "Create LIVE key", keyName: "Key name",
      liveGlobalOff: "Global LIVE key issuance is disabled.", spend: "SPEND CONTROLS",
      daily: "Daily credit cap", monthly: "Monthly credit cap", threshold: "Low balance threshold",
      optional: "empty means disabled", save: "Save", balance: "Balance", usedToday: "Spent today",
      usedMonth: "Spent this month", remainingQuick: "Remaining Quick Analyses", remainingScan: "Remaining Opportunity Scans",
      low: "Low balance", autoRechargeOff: "Auto recharge is unavailable without a reusable payment method.",
      service: "Commercial API is temporarily unavailable.", confirmCancel: "Cancel this unpaid invoice?",
      manual: "Payment is confirmed manually by an administrator.", automatic: "TON payment is verified by the Treasury worker.",
      testLive: "TEST + LIVE",
    },
  };

  function tx(key) { return copy[isRu() ? "ru" : "en"][key] || key; }
  function esc(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(isRu() ? "ru-RU" : "en-US", { dateStyle: "short", timeStyle: "short" }).format(date);
  }
  function projectId(card) {
    const match = (card.querySelector(".eyebrow")?.textContent || "").match(/PROJECT\s+#(\d+)/i);
    return match ? Number(match[1]) : 0;
  }
  function projectData(id) {
    return (commercial?.projects || []).find((item) => Number(item.id) === Number(id)) || null;
  }
  function projectInvoices(id) {
    return (commercial?.invoices || []).filter((item) => Number(item.client_id) === Number(id));
  }
  function statusClass(status) {
    if (["credited", "paid", "live_approved"].includes(status)) return "success";
    if (["expired", "cancelled", "failed", "refunded", "live_rejected", "live_suspended"].includes(status)) return "revoked";
    return "";
  }
  function scopeHtml(card) {
    const allowed = ["account:read", "usage:read", "analysis:run", "analysis:read", "opportunities:run", "opportunities:read", "webhooks:manage"];
    const existing = [...card.querySelectorAll(".create-key-form input[name='scope']")].map((input) => input.value);
    const scopes = [...new Set([...allowed, ...existing])].filter((scope) => !scope.startsWith("wallet:"));
    return scopes.map((scope) => `<label class="scope-option"><input type="checkbox" name="scope" value="${esc(scope)}" checked>${esc(scope)}</label>`).join("");
  }

  function packagesHtml(project) {
    if (!commercial.enabled) return `<div class="commercial-warning">${esc(tx("disabled"))}</div>`;
    if (!commercial.credit_purchases_enabled) return `<div class="commercial-warning">${esc(tx("purchasesOff"))}</div>`;
    const packages = commercial.packages || [];
    if (!packages.length) return `<div class="commercial-warning">${esc(tx("noPackages"))}</div>`;
    return `<h4>${esc(tx("packages"))}</h4><p class="muted">${esc(commercial.automatic_payment_verification ? tx("automatic") : tx("manual"))}</p>
      <div class="package-grid">${packages.map((item) => `<div class="package-card">
        <div><strong>${esc(item.display_name || item.package_code)}</strong><br><code>${esc(item.package_code)}</code></div>
        <div><strong>${Number(item.credits || 0)} ${esc(tx("credits"))}</strong><br>${esc(item.price_amount)} ${esc(item.price_currency)}</div>
        <button type="button" class="primary commercial-buy" data-project-id="${Number(project.id)}" data-package="${esc(item.package_code)}">${esc(tx("buy"))}</button>
      </div>`).join("")}</div>`;
  }

  function invoiceHtml(invoice) {
    const open = OPEN_STATUSES.has(invoice.status);
    const canCancel = ["pending", "awaiting_payment"].includes(invoice.status);
    const canRefresh = invoice.payment_provider === "ton_treasury" && open;
    const actions = `<div class="button-row">
      ${invoice.checkout_url && open ? `<a class="wallet-link primary" href="${esc(invoice.checkout_url)}">${esc(tx("openWallet"))}</a>` : ""}
      ${canRefresh ? `<button type="button" class="secondary commercial-refresh" data-invoice-id="${esc(invoice.invoice_id)}">${esc(tx("refresh"))}</button>` : ""}
      ${canCancel ? `<button type="button" class="danger commercial-cancel" data-invoice-id="${esc(invoice.invoice_id)}">${esc(tx("cancel"))}</button>` : ""}
    </div>`;
    return `<article class="invoice-card ${esc(invoice.status)}">
      <div class="invoice-header"><h4><code>${esc(invoice.invoice_id)}</code></h4><span class="pill ${statusClass(invoice.status)}">${esc(invoice.status)}</span></div>
      <div class="invoice-meta">
        <span>${esc(tx("amount"))}</span><strong>${esc(invoice.amount)} ${esc(invoice.currency)} → ${Number(invoice.credits || 0)} ${esc(tx("credits"))}</strong>
        <span>${esc(tx("expires"))}</span><span>${esc(formatDate(invoice.expires_at))}</span>
        <span>Provider</span><span>${esc(invoice.payment_provider)}</span>
        ${invoice.paid_at ? `<span>Paid</span><span>${esc(formatDate(invoice.paid_at))}</span>` : ""}
        ${invoice.credited_at ? `<span>Credited</span><span>${esc(formatDate(invoice.credited_at))}</span>` : ""}
        ${invoice.tx_hash ? `<span>Transaction</span><code>${esc(invoice.tx_hash)}</code>` : ""}
        ${invoice.last_error && invoice.status !== "credited" ? `<span>Error</span><code>${esc(invoice.last_error)}</code>` : ""}
      </div>
      ${open ? `<div class="invoice-payment">
        ${invoice.payment_address ? `<label>${esc(tx("address"))}<code class="copy-field">${esc(invoice.payment_address)}</code><button type="button" class="secondary commercial-copy" data-copy="${esc(invoice.payment_address)}">${esc(tx("copy"))}</button></label>` : ""}
        <label>${esc(tx("reference"))}<code class="copy-field">${esc(invoice.payment_reference)}</code><button type="button" class="secondary commercial-copy" data-copy="${esc(invoice.payment_reference)}">${esc(tx("copy"))}</button></label>
        ${invoice.payment_instructions ? `<p><strong>${esc(tx("instructions"))}:</strong> ${esc(invoice.payment_instructions)}</p>` : ""}
      </div>` : ""}${actions}
    </article>`;
  }

  function invoicesHtml(project) {
    const invoices = projectInvoices(project.id).slice(0, 10);
    return `<h4>${esc(tx("invoices"))}</h4>${invoices.length ? `<div class="invoice-grid">${invoices.map(invoiceHtml).join("")}</div>` : `<p class="muted">${esc(tx("noInvoices"))}</p>`}`;
  }

  function liveHtml(project, card) {
    const request = project.live_access_request || {};
    const state = project.commercial_status || "test_only";
    const comment = request.admin_comment || request.review_note || "";
    if (state === "live_approved") {
      return `<div class="live-access-card commercial-success"><div class="live-status-row"><h4>${esc(tx("live"))}</h4><span class="pill success">${esc(tx("approved"))}</span></div>
        ${commercial.live_keys_enabled ? `<form class="commercial-live-key" data-project-id="${Number(project.id)}">
          <label>${esc(tx("keyName"))}<input name="name" value="production" maxlength="80" required></label>
          <div class="scope-grid">${scopeHtml(card)}</div><button type="submit" class="primary">${esc(tx("issueLive"))}</button>
        </form>` : `<div class="commercial-warning">${esc(tx("liveGlobalOff"))}</div>`}</div>`;
    }
    if (state === "live_requested") {
      return `<div class="live-access-card commercial-warning"><h4>${esc(tx("live"))}</h4><p>${esc(tx("requested"))}</p><code>${esc(request.request_id || "")}</code></div>`;
    }
    const notice = state === "live_rejected" ? tx("rejected") : state === "live_suspended" ? tx("suspended") : "";
    return `<div class="live-access-card"><h4>${esc(tx("live"))}</h4>
      ${notice ? `<div class="commercial-warning commercial-danger">${esc(notice)}${comment ? `<p><strong>${esc(tx("adminComment"))}:</strong> ${esc(comment)}</p>` : ""}</div>` : ""}
      <form class="commercial-live-request" data-project-id="${Number(project.id)}">
        <label>${esc(tx("company"))}<input name="company_name" minlength="2" maxlength="160" required></label>
        <label>${esc(tx("website"))}<input name="website" type="url" maxlength="300" placeholder="https://example.com"></label>
        <label>${esc(tx("useCase"))}<textarea name="use_case" minlength="20" maxlength="2000" required></textarea></label>
        <div class="commercial-grid"><label>${esc(tx("expected"))}<input name="expected_monthly_requests" type="number" min="0" max="100000000" value="1000" required></label>
        <label>${esc(tx("contact"))}<input name="contact" maxlength="160" placeholder="@username or email" required></label></div>
        <button type="submit" class="primary">${esc(tx("requestLive"))}</button>
      </form></div>`;
  }

  function nullableValue(value) { return value === null || value === undefined ? "" : String(value); }
  function spendHtml(project) {
    const spend = project.spend || {};
    return `<div class="spend-control-card"><h4>${esc(tx("spend"))}</h4>
      ${spend.low_balance ? `<div class="commercial-warning">${esc(tx("low"))}</div>` : ""}
      <div class="commercial-stats">
        <span>${esc(tx("balance"))}<strong>${Number(spend.balance || 0)}</strong></span>
        <span>${esc(tx("usedToday"))}<strong>${Number(spend.daily_spend || 0)}</strong></span>
        <span>${esc(tx("usedMonth"))}<strong>${Number(spend.monthly_spend || 0)}</strong></span>
        <span>${esc(tx("remainingQuick"))}<strong>${Number(spend.estimated_remaining_quick_analyses || 0)}</strong></span>
        <span>${esc(tx("remainingScan"))}<strong>${Number(spend.estimated_remaining_opportunity_scans || 0)}</strong></span>
      </div>
      <form class="commercial-controls" data-project-id="${Number(project.id)}"><div class="commercial-grid">
        <label>${esc(tx("daily"))}<input name="max_daily_credit_spend" type="number" min="0" max="1000000000" value="${esc(nullableValue(spend.max_daily_credit_spend))}" placeholder="${esc(tx("optional"))}"></label>
        <label>${esc(tx("monthly"))}<input name="max_monthly_credit_spend" type="number" min="0" max="1000000000" value="${esc(nullableValue(spend.max_monthly_credit_spend))}" placeholder="${esc(tx("optional"))}"></label>
        <label>${esc(tx("threshold"))}<input name="low_balance_threshold" type="number" min="0" max="1000000000" value="${esc(nullableValue(spend.low_balance_threshold))}" placeholder="${esc(tx("optional"))}"></label>
      </div><p class="muted">${esc(tx("autoRechargeOff"))}</p><button type="submit" class="secondary">${esc(tx("save"))}</button></form>
    </div>`;
  }

  function renderProject(card) {
    const project = projectData(projectId(card));
    if (!project) return;
    let panel = card.querySelector(":scope > .commercial-panel");
    if (!panel) { panel = document.createElement("div"); panel.className = "commercial-panel"; card.appendChild(panel); }
    panel.innerHTML = `<div class="commercial-header"><h3>${esc(tx("title"))}</h3><span class="pill ${statusClass(project.commercial_status)}">${esc(project.commercial_status || "test_only")}</span></div>
      ${packagesHtml(project)}<div class="commercial-grid">${spendHtml(project)}${liveHtml(project, card)}</div>${invoicesHtml(project)}`;
    bindPanel(panel);
  }

  function setBusy(element, value) { if (element) element.disabled = value; }
  async function mutate(path, payload, options = {}) {
    const result = await portalFetch(path, { method: options.method || "POST", headers: options.headers || {}, body: JSON.stringify(payload || {}) });
    if (!result.response.ok) throw new Error(result.data.error || "service_unavailable");
    return result.data;
  }
  function nullableFormNumber(data, key) {
    const value = String(data.get(key) ?? "").trim();
    return value === "" ? null : Number(value);
  }

  async function reloadCommercial() {
    if (loading) return;
    loading = true;
    try {
      const result = await portalFetch("/app-api/v1/developer/commercial/overview");
      if (!result.response.ok) throw new Error(result.data.error || "service_unavailable");
      commercial = result.data;
      const badge = document.getElementById("environmentBadge");
      if (badge && commercial.live_keys_enabled) badge.textContent = tx("testLive");
      document.querySelectorAll(".project").forEach(renderProject);
    } catch (_) {
      document.querySelectorAll(".project").forEach((card) => {
        let panel = card.querySelector(":scope > .commercial-panel");
        if (!panel) { panel = document.createElement("div"); panel.className = "commercial-panel"; card.appendChild(panel); }
        panel.innerHTML = `<div class="commercial-warning commercial-danger">${esc(tx("service"))}</div>`;
      });
    } finally { loading = false; }
  }

  function bindPanel(panel) {
    panel.querySelectorAll(".commercial-copy").forEach((button) => button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      const previous = button.textContent; button.textContent = tx("copied");
      window.setTimeout(() => { button.textContent = previous; }, 1200);
    }));
    panel.querySelectorAll(".commercial-buy").forEach((button) => button.addEventListener("click", async () => {
      setBusy(button, true);
      try {
        const id = Number(button.dataset.projectId);
        const requestId = `api-credit-${id}-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`;
        await mutate(`/app-api/v1/developer/projects/${id}/credit-invoices`, { package_code: button.dataset.package, client_request_id: requestId }, { headers: { "Idempotency-Key": requestId } });
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
    panel.querySelectorAll(".commercial-refresh").forEach((button) => button.addEventListener("click", async () => {
      setBusy(button, true);
      try { await mutate(`/app-api/v1/developer/credit-invoices/${encodeURIComponent(button.dataset.invoiceId)}/refresh`, {}); await reloadCommercial(); if (typeof reloadOverview === "function") await reloadOverview(); }
      catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
    panel.querySelectorAll(".commercial-cancel").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm(tx("confirmCancel"))) return;
      setBusy(button, true);
      try { await mutate(`/app-api/v1/developer/credit-invoices/${encodeURIComponent(button.dataset.invoiceId)}/cancel`, {}); await reloadCommercial(); }
      catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
    panel.querySelectorAll(".commercial-controls").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault(); const button = event.submitter; setBusy(button, true);
      try {
        const data = new FormData(form);
        await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/billing-controls`, {
          max_daily_credit_spend: nullableFormNumber(data, "max_daily_credit_spend"),
          max_monthly_credit_spend: nullableFormNumber(data, "max_monthly_credit_spend"),
          low_balance_threshold: nullableFormNumber(data, "low_balance_threshold"),
          auto_recharge_enabled: false,
        }, { method: "PATCH" });
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
    panel.querySelectorAll(".commercial-live-request").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault(); const button = event.submitter; setBusy(button, true);
      try {
        const data = new FormData(form);
        await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/live-request`, {
          company_name: String(data.get("company_name") || ""), website: String(data.get("website") || ""),
          use_case: String(data.get("use_case") || ""), expected_monthly_requests: Number(data.get("expected_monthly_requests") || 0),
          contact: String(data.get("contact") || ""),
        });
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
    panel.querySelectorAll(".commercial-live-key").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault(); const button = event.submitter; setBusy(button, true);
      try {
        const data = new FormData(form);
        const response = await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/live-keys`, { name: String(data.get("name") || "production"), scopes: data.getAll("scope").map(String) });
        if (typeof reloadOverview === "function") await reloadOverview(); await reloadCommercial(); showSecret(response.key?.raw_key || "");
      } catch (error) { showError(error.message || tx("service")); } finally { setBusy(button, false); }
    }));
  }

  function mount() {
    if (!document.querySelector(".project")) return;
    if (commercial) document.querySelectorAll(".project").forEach(renderProject); else reloadCommercial();
  }
  new MutationObserver(mount).observe(document.getElementById("appRoot") || document.body, { childList: true, subtree: true });
  window.setInterval(() => { if ((commercial?.invoices || []).some((invoice) => OPEN_STATUSES.has(invoice.status))) reloadCommercial(); }, POLL_MS);
  mount();
})();
