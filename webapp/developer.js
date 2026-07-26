function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalizeLang(value) {
  return String(value || "").toLowerCase().startsWith("ru") ? "ru" : "en";
}

const I18N = {
  ru: {
    title: "API для разработчиков",
    subtitle: "Ключи, лимиты, credits и документация ваших API-проектов.",
    loading: "Загрузка API-кабинета…",
    unauthorizedTitle: "Откройте кабинет из Telegram",
    unauthorizedText: "Авторизация не выполнена. Откройте Профиль → API для разработчиков в боте.",
    googleLogin: "Войти через Google",
    retry: "Повторить",
    createProject: "Создать API-проект",
    projectName: "Название проекта",
    projectPlaceholder: "Например: My Telegram Bot",
    create: "Создать проект",
    projectsLimit: "Лимит проектов",
    testOnly: "Сейчас доступны только test-ключи. Live-доступ включим вместе с публичным запуском анализа.",
    noProjects: "API-проектов пока нет. Создайте первый проект.",
    credits: "API credits",
    requestsToday: "Запросов сегодня",
    requestsMonth: "Запросов за месяц",
    activeKeys: "Активных ключей",
    limits: "Лимиты",
    perMinute: "в минуту",
    perDay: "в сутки",
    perMonth: "в месяц",
    createKey: "Создать API-ключ",
    keyName: "Название ключа",
    keyPlaceholder: "backend-production",
    scopes: "Разрешения",
    issueKey: "Выдать test-ключ",
    prefix: "Префикс",
    environment: "Среда",
    status: "Статус",
    lastUsed: "Последнее использование",
    actions: "Действия",
    never: "Никогда",
    rotate: "Перевыпустить",
    revoke: "Отозвать",
    revoked: "Отозван",
    active: "Активен",
    confirmRotate: "Старый ключ сразу перестанет работать. Перевыпустить ключ?",
    confirmRevoke: "Отозвать ключ? Это действие нельзя отменить.",
    ledger: "Последние операции с credits",
    noLedger: "Операций пока нет.",
    productPrices: "Текущие тарифы API",
    product: "Продукт",
    price: "Цена",
    enabled: "Доступность",
    available: "Доступен",
    disabled: "Отключён",
    docs: "Быстрый старт",
    docsText: "Ключ передаётся сервером вашего проекта в заголовке Authorization. Не размещайте его в браузере или мобильном приложении.",
    currentEndpoints: "Доступно сейчас",
    plannedEndpoints: "Следующий этап",
    analysesDisabled: "Запуск анализа через Developer API пока закрыт. Уже работают account, usage и capabilities.",
    copy: "Копировать",
    copied: "Скопировано",
    saved: "Я сохранил ключ",
    secretTitle: "Сохраните ключ сейчас",
    secretWarning: "После закрытия этого окна секретный ключ больше не будет показан.",
    error: "Ошибка",
    serviceUnavailable: "Сервис временно недоступен.",
    projectLimitReached: "Достигнут лимит API-проектов.",
    keyLimitReached: "Достигнут лимит активных ключей.",
    nameRequired: "Введите название проекта.",
    scopeRequired: "Выберите хотя бы одно разрешение.",
    keyNotFound: "Ключ не найден или уже отозван.",
  },
  en: {
    title: "Developer API",
    subtitle: "Keys, limits, credits, and documentation for your API projects.",
    loading: "Loading Developer API portal…",
    unauthorizedTitle: "Open the portal from Telegram",
    unauthorizedText: "Authorization failed. Open Profile → Developer API in the bot.",
    googleLogin: "Continue with Google",
    retry: "Retry",
    createProject: "Create API project",
    projectName: "Project name",
    projectPlaceholder: "For example: My Telegram Bot",
    create: "Create project",
    projectsLimit: "Project limit",
    testOnly: "Only test keys are available now. Live access will open with public analysis execution.",
    noProjects: "No API projects yet. Create your first project.",
    credits: "API credits",
    requestsToday: "Requests today",
    requestsMonth: "Requests this month",
    activeKeys: "Active keys",
    limits: "Limits",
    perMinute: "per minute",
    perDay: "per day",
    perMonth: "per month",
    createKey: "Create API key",
    keyName: "Key name",
    keyPlaceholder: "backend-production",
    scopes: "Scopes",
    issueKey: "Issue test key",
    prefix: "Prefix",
    environment: "Environment",
    status: "Status",
    lastUsed: "Last used",
    actions: "Actions",
    never: "Never",
    rotate: "Rotate",
    revoke: "Revoke",
    revoked: "Revoked",
    active: "Active",
    confirmRotate: "The old key will stop working immediately. Rotate it?",
    confirmRevoke: "Revoke this key? This cannot be undone.",
    ledger: "Recent credit activity",
    noLedger: "No credit activity yet.",
    productPrices: "Current API pricing",
    product: "Product",
    price: "Price",
    enabled: "Availability",
    available: "Available",
    disabled: "Disabled",
    docs: "Quick start",
    docsText: "Send the key from your project server in the Authorization header. Never embed it in browser or mobile application code.",
    currentEndpoints: "Available now",
    plannedEndpoints: "Next phase",
    analysesDisabled: "Developer API analysis execution is still closed. Account, usage, and capabilities are available now.",
    copy: "Copy",
    copied: "Copied",
    saved: "I saved the key",
    secretTitle: "Save this key now",
    secretWarning: "The secret key will not be shown again after you close this window.",
    error: "Error",
    serviceUnavailable: "Service is temporarily unavailable.",
    projectLimitReached: "API project limit reached.",
    keyLimitReached: "Active key limit reached.",
    nameRequired: "Enter a project name.",
    scopeRequired: "Select at least one scope.",
    keyNotFound: "Key not found or already revoked.",
  }
};

const state = {
  lang: normalizeLang(window.Telegram?.WebApp?.initDataUnsafe?.user?.language_code || navigator.language || "ru"),
  overview: null,
};

function t(key) {
  return (I18N[state.lang] || I18N.en)[key] || key;
}

function setStaticCopy() {
  document.documentElement.lang = state.lang;
  document.getElementById("pageTitle").textContent = t("title");
  document.getElementById("pageSubtitle").textContent = t("subtitle");
  document.getElementById("secretTitle").textContent = t("secretTitle");
  document.getElementById("secretWarning").textContent = t("secretWarning");
  document.getElementById("copySecretButton").textContent = t("copy");
  document.getElementById("closeSecretButton").textContent = t("saved");
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_) {
    return { ok: false, error: "invalid_response" };
  }
}

async function authenticate() {
  let response = await fetch("/api/auth/me", { credentials: "include" });
  if (response.ok) return true;

  const initData = window.Telegram?.WebApp?.initData || "";
  if (initData) {
    response = await fetch("/api/auth/telegram", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: initData }),
    });
    if (response.ok) return true;
  }
  return false;
}

async function portalFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-DeepAlpha-Portal"] = "1";
  }
  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers,
  });
  return { response, data: await readJson(response) };
}

function formatDate(value) {
  if (!value) return t("never");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(state.lang === "ru" ? "ru-RU" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function errorMessage(code) {
  const mapping = {
    project_limit_reached: "projectLimitReached",
    key_limit_reached: "keyLimitReached",
    project_name_required: "nameRequired",
    at_least_one_scope_required: "scopeRequired",
    key_not_found: "keyNotFound",
    service_unavailable: "serviceUnavailable",
  };
  return t(mapping[code] || "serviceUnavailable");
}

function showError(message) {
  const root = document.getElementById("appRoot");
  const notice = document.createElement("section");
  notice.className = "card notice";
  notice.innerHTML = `<strong class="error">${escapeHtml(t("error"))}</strong><p>${escapeHtml(message)}</p>`;
  root.prepend(notice);
  window.setTimeout(() => notice.remove(), 6000);
}

function showSecret(rawKey) {
  const modal = document.getElementById("secretModal");
  const field = document.getElementById("secretValue");
  field.value = String(rawKey || "");
  modal.classList.remove("hidden");
  field.focus();
  field.select();
}

function closeSecret() {
  document.getElementById("secretValue").value = "";
  document.getElementById("secretModal").classList.add("hidden");
}

async function copySecret() {
  const field = document.getElementById("secretValue");
  await navigator.clipboard.writeText(field.value);
  const button = document.getElementById("copySecretButton");
  const previous = button.textContent;
  button.textContent = t("copied");
  window.setTimeout(() => { button.textContent = previous; }, 1500);
}

function scopeOptions(projectId, scopes, defaults) {
  return scopes.map((scope) => {
    const checked = defaults.includes(scope) ? "checked" : "";
    return `<label class="scope-option"><input type="checkbox" name="scope" value="${escapeHtml(scope)}" ${checked}>${escapeHtml(scope)}</label>`;
  }).join("");
}

function keyRows(project) {
  const keys = Array.isArray(project.keys) ? project.keys : [];
  if (!keys.length) {
    return `<tr><td colspan="7" class="muted">—</td></tr>`;
  }
  return keys.map((key) => {
    const active = key.status === "active";
    const actions = active
      ? `<div class="button-row"><button type="button" class="secondary rotate-key" data-key-id="${Number(key.id)}">${escapeHtml(t("rotate"))}</button><button type="button" class="danger revoke-key" data-key-id="${Number(key.id)}">${escapeHtml(t("revoke"))}</button></div>`
      : "—";
    return `<tr>
      <td><code>${escapeHtml(key.key_prefix || "")}…</code></td>
      <td>${escapeHtml(key.environment || "test")}</td>
      <td>${(key.scopes || []).map((scope) => `<code>${escapeHtml(scope)}</code>`).join("<br>")}</td>
      <td><span class="pill ${active ? "success" : "revoked"}">${escapeHtml(active ? t("active") : t("revoked"))}</span></td>
      <td>${escapeHtml(formatDate(key.last_used_at))}</td>
      <td>${escapeHtml(formatDate(key.created_at))}</td>
      <td>${actions}</td>
    </tr>`;
  }).join("");
}

function ledgerRows(project) {
  const ledger = Array.isArray(project.recent_ledger) ? project.recent_ledger : [];
  if (!ledger.length) return `<p class="muted">${escapeHtml(t("noLedger"))}</p>`;
  const rows = ledger.slice(0, 8).map((entry) => {
    const amount = Number(entry.amount || 0);
    const signed = amount > 0 ? `+${amount}` : String(amount);
    return `<tr><td>${escapeHtml(entry.event_type || "")}</td><td><strong>${escapeHtml(signed)}</strong></td><td>${Number(entry.balance_after || 0)}</td><td>${escapeHtml(formatDate(entry.created_at))}</td></tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr><th>Event</th><th>Δ</th><th>${escapeHtml(t("credits"))}</th><th>Date</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function projectCard(project, overview) {
  const usage = project.usage || {};
  const activeKeys = Number(project.active_keys || 0);
  return `<section class="card project">
    <div class="project-header">
      <div><p class="eyebrow">PROJECT #${Number(project.id)}</p><h2>${escapeHtml(project.name || "API Project")}</h2><p class="project-meta">${escapeHtml(project.status || "active")} · test environment</p></div>
      <span class="pill success">TEST</span>
    </div>
    <div class="stats">
      <div class="stat"><span>${escapeHtml(t("credits"))}</span><strong>${Number(project.credit_balance || 0)}</strong></div>
      <div class="stat"><span>${escapeHtml(t("requestsToday"))}</span><strong>${Number(usage.requests_today || project.usage_today || 0)}</strong></div>
      <div class="stat"><span>${escapeHtml(t("requestsMonth"))}</span><strong>${Number(usage.requests_month || project.usage_month || 0)}</strong></div>
      <div class="stat"><span>${escapeHtml(t("activeKeys"))}</span><strong>${activeKeys}</strong></div>
    </div>
    <p class="muted">${escapeHtml(t("limits"))}: ${Number(project.rate_limit_per_minute || 0)} ${escapeHtml(t("perMinute"))} · ${Number(project.daily_request_limit || 0)} ${escapeHtml(t("perDay"))} · ${Number(project.monthly_request_limit || 0)} ${escapeHtml(t("perMonth"))}</p>

    <details>
      <summary><strong>${escapeHtml(t("createKey"))}</strong></summary>
      <form class="create-key-form" data-project-id="${Number(project.id)}">
        <div class="grid">
          <label>${escapeHtml(t("keyName"))}<input name="name" maxlength="80" value="default" placeholder="${escapeHtml(t("keyPlaceholder"))}"></label>
          <div><label>${escapeHtml(t("environment"))}<input value="test" disabled></label></div>
        </div>
        <label>${escapeHtml(t("scopes"))}</label>
        <div class="scope-grid">${scopeOptions(project.id, overview.available_scopes || [], overview.default_scopes || [])}</div>
        <div class="button-row"><button type="submit" class="primary">${escapeHtml(t("issueKey"))}</button></div>
      </form>
    </details>

    <div class="table-wrap"><table>
      <thead><tr><th>${escapeHtml(t("prefix"))}</th><th>${escapeHtml(t("environment"))}</th><th>${escapeHtml(t("scopes"))}</th><th>${escapeHtml(t("status"))}</th><th>${escapeHtml(t("lastUsed"))}</th><th>Created</th><th>${escapeHtml(t("actions"))}</th></tr></thead>
      <tbody>${keyRows(project)}</tbody>
    </table></div>
    <h3>${escapeHtml(t("ledger"))}</h3>
    ${ledgerRows(project)}
  </section>`;
}

function productsCard(products) {
  const rows = (products || []).map((product) => `<tr><td><code>${escapeHtml(product.product_code || "")}</code><br><span class="muted">${escapeHtml(product.display_name || "")}</span></td><td><strong>${Number(product.unit_price || 0)}</strong> credits</td><td>${escapeHtml(product.enabled ? t("available") : t("disabled"))}</td></tr>`).join("");
  return `<section class="card"><h2>${escapeHtml(t("productPrices"))}</h2><div class="table-wrap"><table><thead><tr><th>${escapeHtml(t("product"))}</th><th>${escapeHtml(t("price"))}</th><th>${escapeHtml(t("enabled"))}</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function docsCard() {
  return `<section class="card" id="documentation"><h2>${escapeHtml(t("docs"))}</h2><p>${escapeHtml(t("docsText"))}</p>
    <h3>${escapeHtml(t("currentEndpoints"))}</h3>
    <pre>GET /api/v1/account\nGET /api/v1/usage\nGET /api/v1/capabilities</pre>
    <pre>curl -H "Authorization: Bearer da_test_..." \\\n  https://YOUR-DEEPALPHA-HOST/api/v1/account</pre>
    <h3>${escapeHtml(t("plannedEndpoints"))}</h3>
    <pre>POST /api/v1/analyses\nGET /api/v1/analyses/{job_id}\nGET /api/v1/opportunities</pre>
    <p class="warning">${escapeHtml(t("analysesDisabled"))}</p>
  </section>`;
}

function renderOverview() {
  const overview = state.overview;
  const root = document.getElementById("appRoot");
  const projects = overview.projects || [];
  root.innerHTML = `
    <section class="card notice"><strong>TEST MODE</strong><p>${escapeHtml(t("testOnly"))}</p></section>
    <section class="card"><h2>${escapeHtml(t("createProject"))}</h2><p class="muted">${escapeHtml(t("projectsLimit"))}: ${projects.length}/${Number(overview.limits?.projects_per_user || 0)}</p>
      <form id="createProjectForm"><div class="grid"><label>${escapeHtml(t("projectName"))}<input name="name" maxlength="120" required placeholder="${escapeHtml(t("projectPlaceholder"))}"></label><div class="button-row"><button type="submit" class="primary">${escapeHtml(t("create"))}</button></div></div></form>
    </section>
    ${projects.length ? projects.map((project) => projectCard(project, overview)).join("") : `<section class="card empty"><p>${escapeHtml(t("noProjects"))}</p></section>`}
    ${productsCard(overview.products || [])}
    ${docsCard()}
  `;
  bindPortalActions();
}

function renderGuest() {
  document.getElementById("appRoot").innerHTML = `<section class="card empty"><h2>${escapeHtml(t("unauthorizedTitle"))}</h2><p class="muted">${escapeHtml(t("unauthorizedText"))}</p><div class="button-row"><a class="button-link secondary" href="/api/auth/google/start">${escapeHtml(t("googleLogin"))}</a><button class="primary" id="retryAuth" type="button">${escapeHtml(t("retry"))}</button></div></section>`;
  document.getElementById("retryAuth")?.addEventListener("click", start);
}

async function reloadOverview() {
  const { response, data } = await portalFetch("/app-api/v1/developer/overview");
  if (!response.ok) throw new Error(data.error || "service_unavailable");
  state.overview = data;
  if (data.user?.language) state.lang = normalizeLang(data.user.language);
  setStaticCopy();
  renderOverview();
}

function setBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
}

function bindPortalActions() {
  document.getElementById("createProjectForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    setBusy(button, true);
    try {
      const form = new FormData(event.currentTarget);
      const { response, data } = await portalFetch("/app-api/v1/developer/projects", {
        method: "POST",
        body: JSON.stringify({ name: String(form.get("name") || "") }),
      });
      if (!response.ok) throw new Error(data.error || "service_unavailable");
      await reloadOverview();
    } catch (error) {
      showError(errorMessage(error.message));
    } finally {
      setBusy(button, false);
    }
  });

  document.querySelectorAll(".create-key-form").forEach((formElement) => {
    formElement.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.submitter;
      setBusy(button, true);
      try {
        const projectId = Number(event.currentTarget.dataset.projectId);
        const form = new FormData(event.currentTarget);
        const scopes = form.getAll("scope").map(String);
        const { response, data } = await portalFetch(`/app-api/v1/developer/projects/${projectId}/keys`, {
          method: "POST",
          body: JSON.stringify({ name: String(form.get("name") || "default"), scopes }),
        });
        if (!response.ok) throw new Error(data.error || "service_unavailable");
        await reloadOverview();
        showSecret(data.key?.raw_key || "");
      } catch (error) {
        showError(errorMessage(error.message));
      } finally {
        setBusy(button, false);
      }
    });
  });

  document.querySelectorAll(".revoke-key").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(t("confirmRevoke"))) return;
      setBusy(button, true);
      try {
        const keyId = Number(button.dataset.keyId);
        const { response, data } = await portalFetch(`/app-api/v1/developer/keys/${keyId}/revoke`, { method: "POST", body: "{}" });
        if (!response.ok) throw new Error(data.error || "service_unavailable");
        await reloadOverview();
      } catch (error) {
        showError(errorMessage(error.message));
      } finally {
        setBusy(button, false);
      }
    });
  });

  document.querySelectorAll(".rotate-key").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm(t("confirmRotate"))) return;
      setBusy(button, true);
      try {
        const keyId = Number(button.dataset.keyId);
        const { response, data } = await portalFetch(`/app-api/v1/developer/keys/${keyId}/rotate`, { method: "POST", body: "{}" });
        if (!response.ok) throw new Error(data.error || "service_unavailable");
        await reloadOverview();
        showSecret(data.key?.raw_key || "");
      } catch (error) {
        showError(errorMessage(error.message));
      } finally {
        setBusy(button, false);
      }
    });
  });
}

async function start() {
  setStaticCopy();
  document.getElementById("appRoot").innerHTML = `<section class="card"><p>${escapeHtml(t("loading"))}</p></section>`;
  try {
    const authenticated = await authenticate();
    if (!authenticated) {
      renderGuest();
      return;
    }
    await reloadOverview();
  } catch (_) {
    showError(t("serviceUnavailable"));
  }
}

window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();
document.getElementById("copySecretButton").addEventListener("click", copySecret);
document.getElementById("closeSecretButton").addEventListener("click", closeSecret);
document.getElementById("secretModal").addEventListener("click", (event) => {
  if (event.target.id === "secretModal") closeSecret();
});
start();
