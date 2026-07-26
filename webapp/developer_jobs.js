(() => {
  const POLL_MS = 10000;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function isRu() {
    return String(document.documentElement.lang || "ru").toLowerCase().startsWith("ru");
  }

  function copy(key) {
    const ru = {
      title: "История API-анализов",
      refresh: "Обновить",
      loading: "Загрузка заданий…",
      empty: "Анализов через API пока нет.",
      workerOff: "API-worker сейчас недоступен. Новые задания останутся в очереди до восстановления.",
      queued: "В очереди",
      running: "В работе",
      success: "Готово",
      error: "Ошибки",
      job: "Задание",
      market: "Рынок",
      status: "Статус",
      result: "Результат",
      credits: "Credits",
      duration: "Время",
      created: "Создано",
      failed: "Не удалось загрузить историю.",
    };
    const en = {
      title: "API analysis history",
      refresh: "Refresh",
      loading: "Loading jobs…",
      empty: "No API analyses yet.",
      workerOff: "The API worker is unavailable. New jobs will stay queued until it recovers.",
      queued: "Queued",
      running: "Running",
      success: "Success",
      error: "Errors",
      job: "Job",
      market: "Market",
      status: "Status",
      result: "Result",
      credits: "Credits",
      duration: "Duration",
      created: "Created",
      failed: "Could not load job history.",
    };
    return (isRu() ? ru : en)[key] || key;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(isRu() ? "ru-RU" : "en-US", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(date);
  }

  function duration(value) {
    const seconds = Math.max(0, Math.round(Number(value || 0)));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
    return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
  }

  function statusLabel(status) {
    const map = isRu()
      ? { queued: "в очереди", running: "в работе", success: "готово", error: "ошибка", refund_pending: "возврат" }
      : { queued: "queued", running: "running", success: "success", error: "error", refund_pending: "refund" };
    return map[status] || status || "unknown";
  }

  function statusClass(status) {
    if (status === "success") return "success";
    if (status === "error" || status === "refund_pending") return "revoked";
    return "";
  }

  function projectId(card) {
    const text = card.querySelector(".eyebrow")?.textContent || "";
    const match = text.match(/PROJECT\s+#(\d+)/i);
    return match ? Number(match[1]) : 0;
  }

  function ensureHistory(card) {
    let history = card.querySelector(":scope > .job-history");
    if (history) return history;
    const id = projectId(card);
    if (!id) return null;
    history = document.createElement("div");
    history.className = "job-history";
    history.dataset.projectId = String(id);
    history.innerHTML = `<div class="job-history-header"><h3>${escapeHtml(copy("title"))}</h3><button type="button" class="secondary job-refresh">${escapeHtml(copy("refresh"))}</button></div><p class="muted job-state">${escapeHtml(copy("loading"))}</p>`;
    history.querySelector(".job-refresh")?.addEventListener("click", () => loadHistory(history, true));
    card.appendChild(history);
    return history;
  }

  function summaryHtml(summary) {
    return `<div class="job-summary">
      <span class="pill">${escapeHtml(copy("queued"))}: ${Number(summary.queued || 0)}</span>
      <span class="pill">${escapeHtml(copy("running"))}: ${Number(summary.running || 0)}</span>
      <span class="pill success">${escapeHtml(copy("success"))}: ${Number(summary.success || 0)}</span>
      <span class="pill revoked">${escapeHtml(copy("error"))}: ${Number(summary.error || 0)}</span>
    </div>`;
  }

  function jobRows(jobs) {
    return jobs.map((job) => {
      const marketUrl = String(job.market_url || "");
      const market = marketUrl
        ? `<a href="${escapeHtml(marketUrl)}" target="_blank" rel="noreferrer">${escapeHtml((marketUrl.split("/").pop() || "market").slice(0, 45))}</a>`
        : "—";
      const decision = [job.decision, job.side].filter(Boolean).join(" ") || "—";
      const credits = `${Number(job.units_reserved || 0)} / ${Number(job.units_charged || 0)} · ${escapeHtml(job.reservation_status || "—")}`;
      const error = job.error ? `<br><span class="error">${escapeHtml(job.error)}</span>` : "";
      return `<tr>
        <td><code>${escapeHtml(job.job_id || "")}</code></td>
        <td>${market}</td>
        <td><span class="pill ${statusClass(job.status)}">${escapeHtml(statusLabel(job.status))}</span><br><span class="muted">${Number(job.progress || 0)}%</span>${error}</td>
        <td>${escapeHtml(decision)}</td>
        <td>${credits}</td>
        <td>${escapeHtml(duration(job.duration_seconds))}</td>
        <td>${escapeHtml(formatDate(job.created_at))}</td>
      </tr>`;
    }).join("");
  }

  function render(history, data) {
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    const runtimeWarning = data.runtime && !data.runtime.worker_available
      ? `<div class="job-warning">${escapeHtml(copy("workerOff"))}</div>`
      : "";
    const table = jobs.length
      ? `<div class="table-wrap"><table><thead><tr><th>${escapeHtml(copy("job"))}</th><th>${escapeHtml(copy("market"))}</th><th>${escapeHtml(copy("status"))}</th><th>${escapeHtml(copy("result"))}</th><th>${escapeHtml(copy("credits"))}</th><th>${escapeHtml(copy("duration"))}</th><th>${escapeHtml(copy("created"))}</th></tr></thead><tbody>${jobRows(jobs)}</tbody></table></div>`
      : `<p class="muted">${escapeHtml(copy("empty"))}</p>`;
    const header = history.querySelector(".job-history-header")?.outerHTML || "";
    history.innerHTML = `${header}${runtimeWarning}${summaryHtml(data.summary || {})}${table}`;
    history.querySelector(".job-refresh")?.addEventListener("click", () => loadHistory(history, true));
    history.dataset.hasActive = jobs.some((job) => job.status === "queued" || job.status === "running") ? "1" : "0";
  }

  async function loadHistory(history, force = false) {
    if (!history || history.dataset.loading === "1") return;
    if (!force && history.dataset.loaded === "1" && history.dataset.hasActive !== "1") return;
    history.dataset.loading = "1";
    const button = history.querySelector(".job-refresh");
    if (button) button.disabled = true;
    try {
      const id = Number(history.dataset.projectId || 0);
      const response = await fetch(`/app-api/v1/developer/projects/${id}/jobs?limit=30`, { credentials: "include" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "service_unavailable");
      render(history, data);
      history.dataset.loaded = "1";
    } catch (_) {
      const state = history.querySelector(".job-state");
      if (state) state.textContent = copy("failed");
      else history.insertAdjacentHTML("beforeend", `<p class="error">${escapeHtml(copy("failed"))}</p>`);
    } finally {
      history.dataset.loading = "0";
      const refreshed = history.querySelector(".job-refresh");
      if (refreshed) refreshed.disabled = false;
    }
  }

  function mount() {
    document.querySelectorAll(".project").forEach((card) => {
      const history = ensureHistory(card);
      if (history && history.dataset.loaded !== "1") loadHistory(history);
    });
  }

  const observer = new MutationObserver(mount);
  observer.observe(document.getElementById("appRoot") || document.body, { childList: true, subtree: true });
  window.setInterval(() => {
    document.querySelectorAll(".job-history[data-has-active='1']").forEach((history) => loadHistory(history, true));
  }, POLL_MS);
  mount();
})();
