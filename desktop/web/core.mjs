export const MODELS = Object.freeze(Object.assign(Object.create(null), {
  "velia-pro": "VELIA PRO",
  "velia-flash": "VELIA FLASH",
}));
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
export function renderMarkdown(value) {
  const lines = String(value).replace(/\r\n?/g, "\n").split("\n");
  let html = "",
    paragraph = [],
    list = null;
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
      html += `<div class="code-block"><div class="code-heading"><span>${escapeHTML(language || "Код")}</span><button class="copy-code" type="button">Копировать</button></div><pre><code>${escapeHTML(code.join("\n"))}</code></pre></div>`;
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
      pairing_failed: "Код не принят. Получи новый код подключения.",
      invalid_pairing_request: "Введи код подключения из 16 символов.",
      preview_access_required: "Preview пока доступен аккаунту владельца.",
      authentication_unavailable:
        "Сервис входа временно недоступен. Попробуй ещё раз.",
      auth_rate_limit: "Слишком много попыток входа. Попробуй через минуту.",
      flash_context_too_long:
        "Для Flash этот диалог слишком длинный. Начни новый диалог или выбери PRO.",
      flash_unavailable: "Flash сейчас недоступен. Повтори запрос позже.",
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
    }[code] || "Не удалось выполнить запрос. Попробуй ещё раз."
  );
}
/** Decode arbitrary UTF-8/SSE boundaries and distinguish completion from disconnect. */
export async function readCompletion(response, onText) {
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
    finish = null;
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
    if (event.error) throw new Error(apiError("model_request_failed"));
    const choice = event.choices?.[0];
    if (choice?.finish_reason) finish = choice.finish_reason;
    const content = choice?.delta?.content;
    if (typeof content === "string") {
      output += content;
      onText(output);
    }
  };
  try {
    while (!done) {
      const part = await reader.read();
      pending += decoder.decode(part.value, { stream: !part.done });
      pending = pending.replace(/\r\n/g, "\n");
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
    }
    if (!done) throw new Error(apiError("stream_incomplete"));
    if (!output.trim()) throw new Error(apiError("empty_response"));
    return { text: output, finish };
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
