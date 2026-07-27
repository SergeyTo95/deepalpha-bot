(() => {
  const POLL_MS = 12000;
  let commercial = null;
  let loading = false;

  function ru() {
    return String(document.documentElement.lang || navigator.language || "ru").toLowerCase().startsWith("ru");
  }

  const copy = {
    ru: {
      title: "Коммерческий API",
      disabled: "Коммерческий запуск пока выключен администратором.",
      treasuryOff: "Приём TON-платежей временно выключен.",
      packages: "Купить API credits",
      noPackages: "Тарифы ещё не настроены администратором.",
      buy: "Купить",
      credits: "credits",
      payment: "Счёт на оплату",
      amount: "Сумма",
      address: "Адрес Treasury",
      comment: "Обязательный комментарий",
      expires: "Действует до",
      openWallet: "Открыть TON-кошелёк",
      refresh: "Проверить оплату",
      cancel: "Отменить счёт",
      copy: "Копировать",
      copied: "Скопировано",
      paid: "Оплачен",
      pending: "Ожидает оплату",
      expired: "Истёк",
      cancelled: "Отменён",
      liveTitle: "Live API",
      liveEnabled: "Live-доступ одобрен",
      globalLiveOff: "Выдача live-ключей временно закрыта глобальным переключателем.",
      livePending: "Заявка на live-доступ рассматривается.",
      liveRejected: "Предыдущая заявка отклонена. Можно отправить новую с уточнённым описанием.",
      requestLive: "Запросить live-доступ",
      useCase: "Опишите продукт и использование API",
      expected: "Ожидаемых запросов в месяц",
      terms: "Я принимаю условия beta API и отвечаю за безопасное хранение ключа",
      sendRequest: "Отправить заявку",
      issueLive: "Создать live-ключ",
      keyName: "Название live-ключа",
      spend: "Контроль расходов",
      monthlyLimit: "Лимит расхода credits в месяц (0 — без лимита)",
      lowBalance: "Предупреждать при балансе не выше",
      save: "Сохранить",
      used: "Потрачено в этом месяце",
      balance: "Баланс",
      low: "Низкий баланс API credits",
      invoices: "Последние счета",
      noInvoices: "Счетов пока нет.",
      status: "Статус",
      transaction: "TON-транзакция",
      error: "Ошибка",
      service: "Коммерческий API временно недоступен.",
      confirmCancel: "Отменить этот неоплаченный счёт?",
      testLive: "TEST + LIVE",
    },
    en: {
      title: "Commercial API",
      disabled: "Commercial launch is currently disabled by the administrator.",
      treasuryOff: "TON payment intake is temporarily disabled.",
      packages: "Buy API credits",
      noPackages: "Credit packages have not been configured by the administrator.",
      buy: "Buy",
      credits: "credits",
      payment: "Payment invoice",
      amount: "Amount",
      address: "Treasury address",
      comment: "Required comment",
      expires: "Expires",
      openWallet: "Open TON wallet",
      refresh: "Check payment",
      cancel: "Cancel invoice",
      copy: "Copy",
      copied: "Copied",
      paid: "Paid",
      pending: "Awaiting payment",
      expired: "Expired",
      cancelled: "Cancelled",
      liveTitle: "Live API",
      liveEnabled: "Live access approved",
      globalLiveOff: "Live key issuance is temporarily closed by the global gate.",
      livePending: "Your live access request is under review.",
      liveRejected: "The previous request was rejected. You may submit a clearer use case.",
      requestLive: "Request live access",
      useCase: "Describe your product and API use case",
      expected: "Expected requests per month",
      terms: "I accept the beta API terms and am responsible for secure key storage",
      sendRequest: "Submit request",
      issueLive: "Create live key",
      keyName: "Live key name",
      spend: "Spend controls",
      monthlyLimit: "Monthly credit spend limit (0 means unlimited)",
      lowBalance: "Warn when balance is at or below",
      save: "Save",
      used: "Spent this month",
      balance: "Balance",
      low: "Low API credit balance",
      invoices: "Recent invoices",
      noInvoices: "No invoices yet.",
      status: "Status",
      transaction: "TON transaction",
      error: "Error",
      service: "Commercial API is temporarily unavailable.",
      confirmCancel: "Cancel this unpaid invoice?",
      testLive: "TEST + LIVE",
    },
  };

  function tx(key) {
    return copy[ru() ? "ru" : "en"][key] || key;
  }

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function projectId(card) {
    const label = card.querySelector(".eyebrow")?.textContent || "";
    const match = label.match(/PROJECT\s+#(\d+)/i);
    return match ? Number(match[1]) : 0;
  }

  function projectData(id) {
    return (commercial?.projects || []).find((item) => Number(item.id) === Number(id)) || null;
  }

  function projectInvoices(id) {
    return (commercial?.invoices || []).filter((item) => Number(item.client_id) === Number(id));
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(ru() ? "ru-RU" : "en-US", { dateStyle: "short", timeStyle: "short" }).format(date);
  }

  function statusText(status) {
    return tx(["paid", "pending", "expired", "cancelled"].includes(status) ? status : "status") + (status && !["paid", "pending", "expired", "cancelled"].includes(status) ? `: ${status}` : "");
  }

  function statusClass(status) {
    if (status === "paid") return "success";
    if (["expired", "cancelled"].includes(status)) return "revoked";
    return "";
  }

  function existingScopes(card) {
    const values = [...card.querySelectorAll(".create-key-form input[name='scope']")].map((input) => input.value).filter(Boolean);
    return [...new Set(values)];
  }

  function scopeHtml(scopes) {
    const preferred = new Set(["account:read", "usage:read", "analysis:run", "analysis:read", "opportunities:run", "opportunities:read", "webhooks:manage"]);
    return scopes.map((scope) => `<label class="scope-option"><input type="checkbox" name="scope" value="${esc(scope)}" ${preferred.has(scope) ? "checked" : ""}>${esc(scope)}</label>`).join("");
  }

  function packageHtml(project) {
    if (!commercial.enabled) return `<div class="commercial-warning">${esc(tx("disabled"))}</div>`;
    if (!commercial.treasury_incoming_enabled) return `<div class="commercial-warning commercial-danger">${esc(tx("treasuryOff"))}</div>`;
    const packages = Array.isArray(commercial.packages) ? commercial.packages : [];
    if (!packages.length) return `<div class="commercial-warning">${esc(tx("noPackages"))}</div>`;
    return `<h4>${esc(tx("packages"))}</h4><div class="package-grid">${packages.map((item) => `<div class="package-card">
      <div><strong>${esc(item.display_name || item.package_code)}</strong><br><span class="muted"><code>${esc(item.package_code)}</code></span></div>
      <div><strong>${Number(item.credits || 0)} ${esc(tx("credits"))}</strong><br><span>${esc(item.price_ton)} TON</span></div>
      <button type="button" class="primary commercial-buy" data-project-id="${Number(project.id)}" data-package="${esc(item.package_code)}">${esc(tx("buy"))}</button>
    </div>`).join("")}</div>`;
  }

  function invoiceHtml(invoice) {
    const pending = invoice.status === "pending";
    const paid = invoice.status === "paid";
    const actions = pending ? `<div class="button-row">
      ${invoice.ton_transfer_url ? `<a class="wallet-link primary" href="${esc(invoice.ton_transfer_url)}">${esc(tx("openWallet"))}</a>` : ""}
      <button type="button" class="secondary commercial-refresh" data-invoice-id="${esc(invoice.invoice_id)}">${esc(tx("refresh"))}</button>
      <button type="button" class="danger commercial-cancel" data-invoice-id="${esc(invoice.invoice_id)}">${esc(tx("cancel"))}</button>
    </div>` : "";
    return `<div class="invoice-card ${esc(invoice.status)}">
      <div class="invoice-header"><h4>${esc(tx("payment"))} <code>${esc(invoice.invoice_id)}</code></h4><span class="pill ${statusClass(invoice.status)}">${esc(statusText(invoice.status))}</span></div>
      <div class="invoice-meta">
        <span>${esc(tx("amount"))}</span><strong>${esc(invoice.price_ton)} TON → ${Number(invoice.credits || 0)} ${esc(tx("credits"))}</strong>
        <span>${esc(tx("expires"))}</span><span>${esc(formatDate(invoice.expires_at))}</span>
        ${invoice.tx_hash ? `<span>${esc(tx("transaction"))}</span><code>${esc(invoice.tx_hash)}</code>` : ""}
        ${invoice.last_error && !paid ? `<span>${esc(tx("error"))}</span><code>${esc(invoice.last_error)}</code>` : ""}
      </div>
      ${pending ? `<div class="invoice-payment">
        <label>${esc(tx("address"))}<code class="copy-field">${esc(invoice.treasury_address)}</code><button type="button" class="secondary commercial-copy" data-copy="${esc(invoice.treasury_address)}">${esc(tx("copy"))}</button></label>
        <label>${esc(tx("comment"))}<code class="copy-field">${esc(invoice.public_reference)}</code><button type="button" class="secondary commercial-copy" data-copy="${esc(invoice.public_reference)}">${esc(tx("copy"))}</button></label>
        ${actions}
      </div>` : actions}
    </div>`;
  }

  function invoicesHtml(project) {
    const invoices = projectInvoices(project.id).slice(0, 10);
    return `<h4>${esc(tx("invoices"))}</h4>${invoices.length ? `<div class="invoice-grid">${invoices.map(invoiceHtml).join("")}</div>` : `<p class="muted">${esc(tx("noInvoices"))}</p>`}`;
  }

  function liveHtml(project, card) {
    const request = project.live_access_request || null;
    const liveApproved = Boolean(project.live_keys_enabled) && project.commercial_status === "live_enabled";
    const scopes = existingScopes(card);
    if (liveApproved) {
      return `<div class="live-access-card commercial-success"><div class="live-status-row"><h4>${esc(tx("liveTitle"))}</h4><span class="pill success">${esc(tx("liveEnabled"))}</span></div>
        ${commercial.live_keys_enabled ? `<form class="commercial-live-key" data-project-id="${Number(project.id)}">
          <label>${esc(tx("keyName"))}<input name="name" value="production" maxlength="80"></label>
          <div class="scope-grid">${scopeHtml(scopes)}</div>
          <button type="submit" class="primary">${esc(tx("issueLive"))}</button>
        </form>` : `<div class="commercial-warning">${esc(tx("globalLiveOff"))}</div>`}
      </div>`;
    }
    if (request?.status === "pending") {
      return `<div class="live-access-card commercial-warning"><h4>${esc(tx("liveTitle"))}</h4><p>${esc(tx("livePending"))}</p><code>${esc(request.request_id || "")}</code></div>`;
    }
    const rejected = request?.status === "rejected" ? `<p class="commercial-warning commercial-danger">${esc(tx("liveRejected"))}</p>` : "";
    return `<div class="live-access-card"><h4>${esc(tx("requestLive"))}</h4>${rejected}<form class="commercial-live-request" data-project-id="${Number(project.id)}">
      <label>${esc(tx("useCase"))}<textarea name="use_case" minlength="20" maxlength="1000" required></textarea></label>
      <label>${esc(tx("expected"))}<input class="compact-input" name="expected_monthly_requests" type="number" min="0" max="100000000" value="1000"></label>
      <label class="scope-option"><input type="checkbox" name="terms_accepted" required>${esc(tx("terms"))}</label>
      <button type="submit" class="primary">${esc(tx("sendRequest"))}</button>
    </form></div>`;
  }

  function spendHtml(project) {
    const spend = project.spend || {};
    return `<div class="spend-control-card"><h4>${esc(tx("spend"))}</h4>
      ${spend.low_balance ? `<div class="commercial-warning">${esc(tx("low"))}</div>` : ""}
      <p>${esc(tx("used"))}: <strong>${Number(spend.used || 0)}</strong>${Number(spend.limit || 0) > 0 ? ` / ${Number(spend.limit)}` : ""} · ${esc(tx("balance"))}: <strong>${Number(spend.balance || project.credit_balance || 0)}</strong></p>
      <form class="commercial-settings" data-project-id="${Number(project.id)}"><div class="commercial-grid">
        <label>${esc(tx("monthlyLimit"))}<input name="monthly_spend_limit_credits" type="number" min="0" max="1000000000" value="${Number(project.monthly_spend_limit_credits || 0)}"></label>
        <label>${esc(tx("lowBalance"))}<input name="low_balance_threshold" type="number" min="0" max="1000000000" value="${Number(project.low_balance_threshold || 0)}"></label>
      </div><button type="submit" class="secondary">${esc(tx("save"))}</button></form>
    </div>`;
  }

  function renderProject(card) {
    const id = projectId(card);
    const project = projectData(id);
    if (!id || !project) return;
    let panel = card.querySelector(":scope > .commercial-panel");
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "commercial-panel";
      card.appendChild(panel);
    }
    panel.innerHTML = `<div class="commercial-header"><h3>${esc(tx("title"))}</h3><span class="pill ${project.live_keys_enabled ? "success" : ""}">${esc(project.commercial_status || "test_only")}</span></div>
      ${packageHtml(project)}
      <div class="commercial-grid">${spendHtml(project)}${liveHtml(project, card)}</div>
      ${invoicesHtml(project)}`;
    bindPanel(panel);
  }

  function setBusy(element, value) {
    if (element) element.disabled = value;
  }

  async function mutate(path, payload, headers = {}) {
    const result = await portalFetch(path, { method: "POST", headers, body: JSON.stringify(payload || {}) });
    if (!result.response.ok) throw new Error(result.data.error || "service_unavailable");
    return result.data;
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
    } finally {
      loading = false;
    }
  }

  function bindPanel(panel) {
    panel.querySelectorAll(".commercial-copy").forEach((button) => button.addEventListener("click", async () => {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      const previous = button.textContent;
      button.textContent = tx("copied");
      window.setTimeout(() => { button.textContent = previous; }, 1200);
    }));

    panel.querySelectorAll(".commercial-buy").forEach((button) => button.addEventListener("click", async () => {
      setBusy(button, true);
      try {
        const projectId = Number(button.dataset.projectId);
        const idempotency = `api-credit-${projectId}-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`;
        await mutate(`/app-api/v1/developer/projects/${projectId}/credit-invoices`, { package_code: button.dataset.package }, { "Idempotency-Key": idempotency });
        await reloadCommercial();
      } catch (error) {
        showError(error.message || tx("service"));
      } finally { setBusy(button, false); }
    }));

    panel.querySelectorAll(".commercial-refresh").forEach((button) => button.addEventListener("click", async () => {
      setBusy(button, true);
      try {
        await mutate(`/app-api/v1/developer/credit-invoices/${encodeURIComponent(button.dataset.invoiceId)}/refresh`, {});
        await reloadCommercial();
        if (typeof reloadOverview === "function") await reloadOverview();
      } catch (error) { showError(error.message || tx("service")); }
      finally { setBusy(button, false); }
    }));

    panel.querySelectorAll(".commercial-cancel").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm(tx("confirmCancel"))) return;
      setBusy(button, true);
      try {
        await mutate(`/app-api/v1/developer/credit-invoices/${encodeURIComponent(button.dataset.invoiceId)}/cancel`, {});
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); }
      finally { setBusy(button, false); }
    }));

    panel.querySelectorAll(".commercial-settings").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      setBusy(button, true);
      try {
        const data = new FormData(form);
        await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/commercial-settings`, {
          monthly_spend_limit_credits: Number(data.get("monthly_spend_limit_credits") || 0),
          low_balance_threshold: Number(data.get("low_balance_threshold") || 0),
        });
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); }
      finally { setBusy(button, false); }
    }));

    panel.querySelectorAll(".commercial-live-request").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      setBusy(button, true);
      try {
        const data = new FormData(form);
        await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/live-access/request`, {
          use_case: String(data.get("use_case") || ""),
          expected_monthly_requests: Number(data.get("expected_monthly_requests") || 0),
          terms_accepted: data.get("terms_accepted") === "on",
          terms_version: commercial.terms_version || "2026-07",
        });
        await reloadCommercial();
      } catch (error) { showError(error.message || tx("service")); }
      finally { setBusy(button, false); }
    }));

    panel.querySelectorAll(".commercial-live-key").forEach((form) => form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      setBusy(button, true);
      try {
        const data = new FormData(form);
        const response = await mutate(`/app-api/v1/developer/projects/${Number(form.dataset.projectId)}/live-keys`, {
          name: String(data.get("name") || "production"),
          scopes: data.getAll("scope").map(String),
        });
        if (typeof reloadOverview === "function") await reloadOverview();
        await reloadCommercial();
        showSecret(response.key?.raw_key || "");
      } catch (error) { showError(error.message || tx("service")); }
      finally { setBusy(button, false); }
    }));
  }

  function mount() {
    if (!document.querySelector(".project")) return;
    if (commercial) document.querySelectorAll(".project").forEach(renderProject);
    else reloadCommercial();
  }

  const observer = new MutationObserver(mount);
  observer.observe(document.getElementById("appRoot") || document.body, { childList: true, subtree: true });
  window.setInterval(() => {
    if ((commercial?.invoices || []).some((invoice) => invoice.status === "pending")) reloadCommercial();
  }, POLL_MS);
  mount();
})();
