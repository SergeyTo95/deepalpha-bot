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

  function text(key) {
    const ru = {
      title: "История Opportunity Scan",
      refresh: "Обновить",
      loading: "Загрузка сканирований…",
      empty: "Сканирований через API пока нет.",
      workerOff: "Opportunity-worker сейчас недоступен. Новые задания останутся в очереди до восстановления.",
      queued: "В очереди",
      running: "В работе",
      success: "Готово",
      error: "Ошибки",
      job: "Задание",
      filters: "Фильтры",
      status: "Статус",
      candidates: "Кандидаты",
      credits: "Credits",
      duration: "Время",
      created: "Создано",
      failed: "Не удалось загрузить историю Opportunity Scan.",
    };
    const en = {
      title: "Opportunity Scan history",
      refresh: "Refresh",
      loading: "Loading scans…",
      empty: "No API opportunity scans yet.",
      workerOff: "The Opportunity worker is unavailable. New jobs will stay queued until it recovers.",
      queued: "Queued",
      running: "Running",
      success: "Success",
      error: "Errors",
      job: "Job",
      filters: "Filters",
      status: "Status",
      candidates: "Candidates",
      credits: "Credits",
      duration: "Duration",
      created: "Created",
      failed: "Could not load Opportunity Scan history.",
    };
    return (isRu() ? ru : en)[key] || key;
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
    return `${minutes}m ${seconds % 60}s`;
  }

  function projectId(card) {
    const label = card.querySelector(".eyebrow")?.textContent || "";
    const match = label.match(/PROJECT\s+#(\d+)/i);
    return match ? Number(match[1]) : 0;
  }

  function ensureHistory(card) {
    let history = card.querySelector(":scope > .opportunity-history");
    if (history) return history;
    const id = projectId(card);
    if (!id) return null;
    history = document.createElement("div");
    history.className = "job-history opportunity-history";
    history.dataset.projectId = String(id);
    history.innerHTML = `<div class="job-history-header"><h3>${escapeHtml(text("title"))}</h3><button type="button" class="secondary opportunity-refresh">${escapeHtml(text("refresh"))}</button></div><p class="muted opportunity-state">${escapeHtml(text("loading"))}</p>`;
    history.querySelector(".opportunity-refresh")?.addEventListener("click", () => loadHistory(history, true));
    card.appendChild(history);
    return history;
  }

  function summaryHtml(summary) {
    return `<div class="job-summary">
      <span class="pill">${escapeHtml(text("queued"))}: ${Number(summary.queued || 0)}</span>
      <span class="pill">${escapeHtml(text("running"))}: ${Number(summary.running || 0)}</span>
      <span class="pill success">${escapeHtml(text("success"))}: ${Number(summary.success || 0)}</span>
      <span class="pill revoked">${escapeHtml(text("error"))}: ${Number(summary.error || 0)}</span>
    </div>`;
  }

  function candidatePreview(job) {
    const candidates = Array.isArray(job.top_candidates) ? job.top_candidates : [];
    if (!candidates.length) return `${Number(job.candidate_count || 0)}`;
    const first = candidates[0] || {};
    const label = `${Number(job.candidate_count || candidates.length)} · ${Number(first.score || 0)}/100`;
    if (!first.url) return `${escapeHtml(label)}<br><span class="muted">${escapeHtml(first.question || "")}</span>`;
    return `${escapeHtml(label)}<br><a href="${escapeHtml(first.url)}" target="_blank" rel="noreferrer">${escapeHtml(String(first.question || "market").slice(0, 70))}</a>`;
  }

  function rows(jobs) {
    return jobs.map((job) => {
      const filters = `${escapeHtml(job.category || "All")} · limit ${Number(job.result_limit || 0)} · score ≥ ${Number(job.min_score || 0)}`;
      const credits = `${Number(job.units_reserved || 0)} / ${Number(job.units_charged || 0)} · ${escapeHtml(job.reservation_status || "—")}`;
      const error = job.error ? `<br><span class="error">${escapeHtml(job.error)}</span>` : "";
      return `<tr>
        <td><code>${escapeHtml(job.job_id || "")}</code></td>
        <td>${filters}</td>
        <td><span class="pill ${statusClass(job.status)}">${escapeHtml(statusLabel(job.status))}</span><br><span class="muted">${Number(job.progress || 0)}%</span>${error}</td>
        <td>${candidatePreview(job)}</td>
        <td>${credits}</td>
        <td>${escapeHtml(duration(job.duration_seconds))}</td>
        <td>${escapeHtml(formatDate(job.created_at))}</td>
      </tr>`;
    }).join("");
  }

  function render(history, data) {
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    const warning = data.runtime && !data.runtime.worker_available
      ? `<div class="job-warning">${escapeHtml(text("workerOff"))}</div>`
      : "";
    const table = jobs.length
      ? `<div class="table-wrap"><table><thead><tr><th>${escapeHtml(text("job"))}</th><th>${escapeHtml(text("filters"))}</th><th>${escapeHtml(text("status"))}</th><th>${escapeHtml(text("candidates"))}</th><th>${escapeHtml(text("credits"))}</th><th>${escapeHtml(text("duration"))}</th><th>${escapeHtml(text("created"))}</th></tr></thead><tbody>${rows(jobs)}</tbody></table></div>`
      : `<p class="muted">${escapeHtml(text("empty"))}</p>`;
    const header = history.querySelector(".job-history-header")?.outerHTML || "";
    history.innerHTML = `${header}${warning}${summaryHtml(data.summary || {})}${table}`;
    history.querySelector(".opportunity-refresh")?.addEventListener("click", () => loadHistory(history, true));
    history.dataset.hasActive = jobs.some((job) => job.status === "queued" || job.status === "running") ? "1" : "0";
  }

  async function loadHistory(history, force = false) {
    if (!history || history.dataset.loading === "1") return;
    if (!force && history.dataset.loaded === "1" && history.dataset.hasActive !== "1") return;
    history.dataset.loading = "1";
    const button = history.querySelector(".opportunity-refresh");
    if (button) button.disabled = true;
    try {
      const id = Number(history.dataset.projectId || 0);
      const response = await fetch(`/app-api/v1/developer/projects/${id}/opportunity-scans?limit=30`, { credentials: "include" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || "service_unavailable");
      render(history, data);
      history.dataset.loaded = "1";
    } catch (_) {
      const state = history.querySelector(".opportunity-state");
      if (state) state.textContent = text("failed");
      else history.insertAdjacentHTML("beforeend", `<p class="error">${escapeHtml(text("failed"))}</p>`);
    } finally {
      history.dataset.loading = "0";
      const refreshed = history.querySelector(".opportunity-refresh");
      if (refreshed) refreshed.disabled = false;
    }
  }

  function appendDocs() {
    const docs = document.getElementById("documentation");
    if (!docs || docs.dataset.opportunityApiReady === "1") return;
    docs.dataset.opportunityApiReady = "1";
    const section = document.createElement("section");
    section.className = "opportunity-api-docs";
    section.innerHTML = isRu()
      ? `<h3>Opportunity Scan API v1</h3>
         <p>Детерминированный zero-LLM сканер Polymarket. Цена по умолчанию — 1 API credit. Для запуска нужен <code>opportunities:run</code>, для чтения — <code>opportunities:read</code>.</p>
         <pre>POST /api/v1/opportunity-scans
Idempotency-Key: scan_01J...
{
  "category": "All",
  "language": "ru",
  "scan_limit": 100,
  "result_limit": 10,
  "min_score": 52,
  "min_liquidity": 1000,
  "min_volume_24h": 500,
  "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"]
}</pre>
         <pre>GET /api/v1/opportunity-scans/{job_id}</pre>
         <p>Результат ранжирует рынки для дальнейшего анализа. Fair probability, edge и BUY-сигнал не рассчитываются.</p>`
      : `<h3>Opportunity Scan API v1</h3>
         <p>Deterministic zero-LLM Polymarket scanner. The default price is 1 API credit. Starting a scan requires <code>opportunities:run</code>; reading it requires <code>opportunities:read</code>.</p>
         <pre>POST /api/v1/opportunity-scans
Idempotency-Key: scan_01J...
{
  "category": "All",
  "language": "en",
  "scan_limit": 100,
  "result_limit": 10,
  "min_score": 52,
  "min_liquidity": 1000,
  "min_volume_24h": 500,
  "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"]
}</pre>
         <pre>GET /api/v1/opportunity-scans/{job_id}</pre>
         <p>The result ranks markets for further analysis. It does not calculate fair probability, edge, or a BUY signal.</p>`;
    docs.appendChild(section);
  }

  function mount() {
    appendDocs();
    document.querySelectorAll(".project").forEach((card) => {
      const history = ensureHistory(card);
      if (history && history.dataset.loaded !== "1") loadHistory(history);
    });
  }

  const observer = new MutationObserver(mount);
  observer.observe(document.getElementById("appRoot") || document.body, { childList: true, subtree: true });
  window.setInterval(() => {
    document.querySelectorAll(".opportunity-history[data-has-active='1']").forEach((history) => loadHistory(history, true));
  }, POLL_MS);
  mount();
})();
