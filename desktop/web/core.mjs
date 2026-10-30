import {highlightCode, CODE_RENDER_BUDGET} from './syntax.mjs';

export const MODELS = Object.freeze(
  Object.assign(Object.create(null), {
    "velia-pro": "VELIA PRO",
    "velia-flash": "VELIA FLASH",
    "velia-quantum": "VELIA QUANTUM",
  }),
);
export const escapeHTML = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
function inline(text) {
  return String(text)
    .split(/(`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g)
    .map((part) => {
      if (/^`[^`]+`$/.test(part))
        return `<code>${escapeHTML(part.slice(1, -1))}</code>`;
      if (/^\*\*[^*]+\*\*$/.test(part))
        return `<strong>${escapeHTML(part.slice(2, -2))}</strong>`;
      if (/^\*[^*]+\*$/.test(part))
        return `<em>${escapeHTML(part.slice(1, -1))}</em>`;
      const link = part.match(/^\[([^\]]+)\]\(([^\s)]+)\)$/);
      if (link) {
        try {
          const url = new URL(link[2]);
          if (["https:", "http:", "mailto:"].includes(url.protocol))
            return `<a href="${escapeHTML(url.href)}" target="_blank" rel="noopener noreferrer">${escapeHTML(link[1])}</a>`;
        } catch {}
      }
      return escapeHTML(part);
    })
    .join("");
}
/** Raw HTML is text; only this generated markup is trusted. */
export function renderMarkdown(value, {highlight = true} = {}) {
  const lines = String(value).replace(/\r\n?/g, "\n").split("\n");
  let html = "",
    paragraph = [],
    list = null,
    highlightBudget = CODE_RENDER_BUDGET;
  const flush = () => {
    if (paragraph.length) {
      html += `<p>${paragraph.map(inline).join("<br>")}</p>`;
      paragraph = [];
    }
  };
  const close = () => {
    if (list) {
      html += `</${list}>`;
      list = null;
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      close();
      const language = line.slice(3).trim().slice(0, 40),
        code = [];
      while (++i < lines.length && !/^```/.test(lines[i])) code.push(lines[i]);
      const source = code.join("\n");
      let colored = null;
      if (highlight && source.length <= highlightBudget) {
        highlightBudget -= source.length;
        colored = highlightCode(source, language);
      }
      html += `<div class="code-block"><div class="code-heading"><span>${escapeHTML(language || "Код")}</span><button class="copy-code" type="button">Копировать</button></div><pre><code>${colored ?? escapeHTML(source)}</code></pre></div>`;
      continue;
    }
    if (!line.trim()) {
      flush();
      close();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flush();
      close();
      const level = heading[1].length;
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
      continue;
    }
    const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
    if (item) {
      flush();
      const type = item[1] ? "ul" : "ol";
      if (list !== type) {
        close();
        list = type;
        html += `<${type}>`;
      }
      html += `<li>${inline(item[2])}</li>`;
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flush();
      close();
      html += `<blockquote>${inline(quote[1])}</blockquote>`;
      continue;
    }
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])
    ) {
      flush();
      close();
      const cells = (v) =>
        v
          .trim()
          .replace(/^\||\|$/g, "")
          .split("|")
          .map((c) => c.trim());
      html += `<table><thead><tr>${cells(line)
        .map((c) => `<th>${inline(c)}</th>`)
        .join("")}</tr></thead><tbody>`;
      i++;
      while (
        i + 1 < lines.length &&
        lines[i + 1].includes("|") &&
        lines[i + 1].trim()
      )
        html += `<tr>${cells(lines[++i])
          .map((c) => `<td>${inline(c)}</td>`)
          .join("")}</tr>`;
      html += "</tbody></table>";
      continue;
    }
    close();
    paragraph.push(line);
  }
  flush();
  close();
  return html;
}
export function apiError(code) {
  return (
    {
      unauthorized: "Войди в VELIA, чтобы продолжить.",
      background_tasks_unavailable: "Фоновые задачи сейчас недоступны. Отправь обычное сообщение.",
      background_task_not_found: "Задача не найдена в этом аккаунте.",
      background_task_active: "Одна задача уже выполняется. Дождись результата или отмени её. С Flash можно продолжить разговор.",
      background_task_finished: "Задача уже завершена. Можно обсудить результат или создать новую.",
      background_revision_limit: "Для одной задачи доступны три уточнения. Создай новую задачу с полным условием.",
      background_queue_full: "Очередь фоновых задач заполнена. Попробуй позже.",
      background_task_interrupted: "Выполнение задачи прервалось. Можно отправить её заново.",
      background_task_failed: "Не удалось выполнить задачу. Можно отправить её заново.",
      background_task_cancelled: "Задача отменена.",
      background_agent_unavailable: "Agent с выбранной моделью сейчас недоступен.",
      background_agent_context_too_long: "Сократи условие задачи для браузерного Agent.",
      background_agent_invalid_plan: "Не удалось подготовить задачу для Agent. Уточни условие.",
      background_agent_failed: "Не удалось подтвердить результат Agent. Проверь страницу перед повтором: действие могло выполниться.",
      background_agent_review_failed: "Agent вернул результат, но выбранная модель не смогла его проверить. Непроверенный результат сохранён в диалоге.",
      background_agent_interrupted: "Связь с Agent прервалась. Действия не повторены автоматически. Проверь текущую страницу перед продолжением.",
      guest_limit_reached: "30 гостевых сообщений использованы. Войди в VELIA, чтобы продолжить.",
      guest_session_required: "Обнови страницу, чтобы начать гостевой разговор.",
      guest_flash_only: "Без регистрации доступен только Flash. Для PRO войди в аккаунт с токенами.",
      guest_service_unavailable: "Гостевой режим сейчас недоступен. Попробуй позже или войди.",
      pairing_failed: "Код не принят. Получи новый код подключения.",
      invalid_pairing_request: "Введи код подключения из 16 символов.",
      preview_access_required: "Preview пока доступен аккаунту владельца.",
      authentication_unavailable:
        "Сервис входа временно недоступен. Попробуй ещё раз.",
      account_service_unavailable: "Не удалось связаться с аккаунтом. История сохранена; повтори позже.",
      pro_tokens_required: "Для PRO нужны токены. Сейчас доступен Flash.",
      pro_unavailable: "PRO сейчас недоступна. Повтори запрос позже.",
      pro_free_guard_required: "Бесплатный доступ PRO сейчас не подтверждён. Попробуй позже.",
      pro_access_required: "Доступ к модели PRO пока не активирован. Попробуй позже.",
      pro_spend_cap_reached: "Запрос PRO заблокирован денежным лимитом ключа. Бесплатный доступ к модели сейчас не подтверждён.",
      pro_quota_exhausted: "Сервис PRO временно ограничил запросы. Попробуй позже.",
      pro_busy: "PRO сейчас занята. Попробуй чуть позже.",
      pro_rate_limited: "Слишком много запросов к PRO. Подожди немного и повтори.",
      pro_overloaded: "Сервис PRO сейчас перегружен. Подожди 30 секунд и нажми «Повторить».",
      pro_web_search_unavailable: "Не удалось получить источники из интернета для PRO. Попробуй ещё раз.",
      pro_timeout: "PRO не успела ответить. Попробуй ещё раз.",
      pro_provider_error: "Сервис PRO временно недоступен. Попробуй позже.",
      pro_invalid_response: "Ответ PRO прервался. Можно повторить запрос.",
      pro_context_too_long: "Для PRO этот диалог слишком большой. Начни новый диалог.",
      voice_daily_user_limit_exceeded: "Твой дневной лимит голосового общения исчерпан. Текстовый чат имеет отдельный лимит.",
      voice_daily_global_limit_exceeded: "Общий дневной лимит голосового общения исчерпан. Можно продолжить текстом, если его лимит доступен.",
      pro_daily_user_limit_exceeded: "Дневной лимит PRO исчерпан. Попробуй завтра.",
      pro_daily_global_limit_exceeded: "Общий дневной лимит PRO исчерпан. Попробуй завтра.",
      token_balance_unavailable: "Не удалось проверить баланс. Пока используй Flash.",
      conversation_not_found: "Этот диалог больше не доступен в аккаунте.",
      generation_in_progress: "Ответ ещё формируется. Дождись его завершения.",
      flash_busy: "Flash сейчас занят. Повтори чуть позже.",
      flash_timeout: "Flash не успел ответить. Повтори чуть позже.",
      auth_rate_limit: "Слишком много попыток входа. Попробуй через минуту.",
      flash_context_too_long:
        "Для Flash этот диалог слишком длинный. Начни новый диалог или выбери PRO.",
      flash_unavailable: "Flash сейчас недоступен. Повтори запрос позже.",
      flash_free_guard_required: "Бесплатный доступ Flash сейчас не подтверждён. Попробуй позже.",
      flash_access_required: "Доступ к модели Flash пока не активирован. Попробуй позже.",
      flash_spend_cap_reached: "Запрос Flash заблокирован денежным лимитом ключа. Бесплатный доступ к модели сейчас не подтверждён.",
      flash_quota_exhausted: "Сервис Flash временно ограничил запросы. Попробуй позже.",
      flash_rate_limited: "Слишком много запросов к Flash. Подожди немного и повтори.",
      flash_overloaded: "Сервис Flash сейчас перегружен. Подожди 30 секунд и нажми «Повторить».",
      flash_web_search_unavailable: "Не удалось получить источники из интернета для Flash. Попробуй ещё раз.",
      flash_provider_error: "Сервис Flash временно недоступен. Попробуй позже.",
      flash_invalid_response: "Ответ Flash прервался. Можно повторить запрос.",
      flash_daily_user_limit_exceeded: "Дневной лимит Flash исчерпан. Попробуй завтра.",
      flash_daily_global_limit_exceeded: "Общий дневной лимит Flash исчерпан. Попробуй завтра.",
      quantum_unavailable: "Quantum сейчас недоступна. Выбери Flash или попробуй позже.",
      quantum_free_guard_required: "Бесплатный доступ Quantum сейчас не подтверждён. Попробуй позже.",
      quantum_access_required: "Quantum пока не активирована. Сейчас можно пользоваться Flash.",
      quantum_spend_cap_reached: "Запрос Quantum заблокирован денежным лимитом ключа. Бесплатный доступ к модели сейчас не подтверждён.",
      quantum_quota_exhausted: "Quantum временно ограничила запросы. Попробуй позже.",
      quantum_busy: "Quantum сейчас занята. Попробуй чуть позже.",
      quantum_rate_limited: "Слишком много запросов к Quantum. Подожди немного и повтори.",
      quantum_overloaded: "Сервис Quantum сейчас перегружен. Попробуй немного позже.",
      quantum_web_search_unavailable: "Не удалось получить источники из интернета. Попробуй ещё раз.",
      quantum_timeout: "Quantum не успела ответить. Попробуй ещё раз.",
      quantum_provider_error: "Сервис Quantum временно недоступен. Попробуй позже.",
      quantum_invalid_response: "Ответ Quantum прервался. Можно повторить запрос.",
      quantum_context_too_long: "Для Quantum этот диалог слишком большой. Начни новый диалог.",
      quantum_attachments_unsupported: "Чтение файлов для Quantum ещё не включено на сервере.",
      selected_model_files_unavailable: "Чтение файлов выбранной моделью сейчас недоступно. Попробуй позже.",
      image_has_no_readable_text: "На фото не удалось распознать текст. Для описания предметов и сцен нужна модель зрения.",
      document_has_no_readable_text: "В документе не найден читаемый текст. Отправь более чёткий скан или вставь текст в сообщение.",
      attachment_ocr_unavailable: "Распознавание текста сейчас недоступно. Можно вставить текст в сообщение.",
      attachment_ocr_timeout: "Распознавание заняло слишком много времени. Отправь более чёткий снимок или меньше страниц.",
      attachment_ocr_failed: "Не удалось распознать текст. Попробуй более чёткий снимок.",
      document_ocr_too_many_pages: "В скане слишком много страниц для распознавания. Раздели документ на части.",
      quantum_daily_user_limit_exceeded: "Дневной лимит Quantum исчерпан. Пока можно выбрать Flash.",
      quantum_daily_global_limit_exceeded: "Дневной лимит Quantum исчерпан. Пока можно выбрать Flash.",
      model_unavailable: "Выбранный режим сейчас недоступен.",
      request_already_running:
        "Предыдущий ответ ещё формируется. Дождись его завершения.",
      preview_capacity_exceeded: "Велия сейчас занята. Попробуй чуть позже.",
      preview_hourly_request_limit:
        "Достигнут лимит Preview: 30 запросов в час. Попробуй позже.",
      request_too_large: "Сообщение слишком большое. Сократи его.",
      invalid_messages:
        "Диалог слишком большой или содержит неподдерживаемые сообщения.",
      stream_incomplete: "Ответ прервался. Можно повторить запрос.",
      empty_response: "Не удалось получить текст ответа. Попробуй ещё раз.",
      model_request_failed: "Не удалось получить ответ. Повтори запрос.",
      model_connection_failed: "Соединение прервалось. Попробуй ещё раз.",
      web_search_unavailable: "Не удалось выполнить поиск в интернете. Повтори позже.",
      request_understanding_unavailable: "Не удалось обработать вопрос. Попробуй повторить его.",
      web_search_context_too_long: "В этот диалог не помещаются веб-источники. Начни новый диалог.",
      browser_agent_busy: "Browser Agent сейчас занят. Повтори через несколько секунд.",
      browser_agent_timeout: "Browser Agent не успел завершить задачу. Попробуй ещё раз.",
      browser_agent_unavailable: "Browser Agent временно недоступен. Попробуй позже.",
      browser_storage_capacity: "Browser Agent временно не принимает задачи: хранилище заполнено. Текущая сессия сохранена. Попробуй позже.",
      browser_profile_capacity: "Достигнут лимит данных этого браузерного диалога. Создай новый диалог Agent; прежняя сессия сохранена.",
      browser_agent_disabled: "Browser Agent пока отключён.",
      invalid_browser_task: "Не удалось понять задачу для Browser Agent.",
    }[code] || "Не удалось выполнить запрос. Попробуй ещё раз."
  );
}
/** Decode arbitrary UTF-8/SSE boundaries and distinguish completion from disconnect. */
export function safeSearch(value) {
  const sources = [];
  for (const source of Array.isArray(value?.sources) ? value.sources.slice(0, 3) : []) {
    try {
      const url = new URL(source.url);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) continue;
      if (typeof source.title !== "string" || source.title.length > 160 || source.url.length > 512) continue;
      sources.push({title: source.title, url: url.href});
    } catch {}
  }
  return {sources, retrieved_at: typeof value?.retrieved_at === "string" ? value.retrieved_at.slice(0, 40) : ""};
}
export async function readCompletion(response, onText, onSearch = () => {}, onProgress = () => {}) {
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {}
    const error = new Error(apiError(data?.error?.message || data?.error));
    error.status = response.status;
    throw error;
  }
  if (
    !response.headers.get("content-type")?.includes("text/event-stream") ||
    !response.body
  )
    throw new Error(apiError("stream_incomplete"));
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let pending = "",
    done = false,
    output = "",
    finish = null,
    search = null,
    changed = false;
  const publish = () => {
    if (!changed) return;
    changed = false;
    onText(output);
  };
  const consume = (frame) => {
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return;
    if (data === "[DONE]") {
      done = true;
      return;
    }
    let event;
    try {
      event = JSON.parse(data);
    } catch {
      throw new Error(apiError("stream_incomplete"));
    }
    if (event.error) throw new Error(apiError(event.error.message || event.error));
    if (["understanding", "generating"].includes(event.progress)) onProgress(event.progress);
    if (event.web_search) {
      search = safeSearch(event.web_search);
      onSearch(search);
    }
    if (event.reset === true) {
      output = "";
      // A reset and its replacement can arrive in different network reads.
      // Keep the last visible/spoken snapshot until replacement text exists.
      changed = false;
    }
    const choice = event.choices?.[0];
    if (choice?.finish_reason) finish = choice.finish_reason;
    const content = choice?.delta?.content;
    if (typeof content === "string" && content) {
      output += content;
      changed = true;
    }
  };
  try {
    while (!done) {
      const part = await reader.read();
      pending += decoder.decode(part.value, { stream: !part.done });
      pending = pending.replace(/\r\n/g, "\n");
      try {
        let boundary;
        while ((boundary = pending.indexOf("\n\n")) !== -1) {
          consume(pending.slice(0, boundary));
          pending = pending.slice(boundary + 2);
          if (done) break;
        }
        if (part.done) {
          if (pending.trim()) consume(pending);
          break;
        }
      } finally {
        // One network chunk may contain hundreds of already available deltas.
        // Publish their latest text immediately, without repeating Markdown work.
        publish();
      }
    }
    if (!done) throw new Error(apiError("stream_incomplete"));
    if (!output.trim()) throw new Error(apiError("empty_response"));
    return { text: output, finish, ...(search ? {search} : {}) };
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export function loadChats(storage, key) {
  try {
    const data = JSON.parse(storage.getItem(key) || "[]");
    if (!Array.isArray(data)) return [];
    return data
      .filter(
        (chat) =>
          chat &&
          typeof chat.id === "string" &&
          typeof chat.title === "string" &&
          Number.isFinite(chat.updated) &&
          Array.isArray(chat.messages) &&
          chat.messages.length <= 256 &&
          chat.messages.every(
            (m) =>
              m &&
              ["user", "assistant"].includes(m.role) &&
              typeof m.content === "string" &&
              m.content.length <= 64000,
          ),
      )
      .slice(0, 100);
  } catch {
    return [];
  }
}
export function chatPayload(chat, model) {
  if (!MODELS[model]) throw new Error("Выбери режим Велии.");
  return {
    model,
    stream: true,
    messages: chat.messages
      .filter((m) => m.role === "user" || (!m.failed && m.content.trim()))
      .map(({ role, content }) => ({ role, content })),
  };
}
