import {
  MODELS,
  renderMarkdown,
  readCompletion,
  loadChats,
  chatPayload,
  apiError,
  safeSearch,
} from "./core.mjs";
const $ = (id) => document.getElementById(id);
const icons = {
  plus: "M12 5v14M5 12h14",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0a8 8 0 0 1 16 0",
  lock: "M7 10V7a5 5 0 0 1 10 0v3M6 10h12v11H6zM12 14v3",
  user: "M20 21v-2a8 8 0 0 0-16 0v2M16 6a4 4 0 1 1-8 0a4 4 0 0 1 8 0",
  chevron: "M8 10l4 4 4-4",
  menu: "M4 6h16M4 12h16M4 18h16",
  sun: "M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4M16 12a4 4 0 1 1-8 0a4 4 0 0 1 8 0",
  moon: "M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10",
  spark: "M12 3l2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8z",
  bolt: "M13 2L4 14h7l-1 8 10-12h-7z",
  pen: "M16 3l5 5L8 21H3v-5zM14 5l5 5",
  layers: "M12 3L2 8l10 5 10-5zM2 12l10 5 10-5M2 16l10 5 10-5",
  code: "M8 5l-7 7 7 7M16 5l7 7-7 7M14 3l-4 18",
  browser: "M3 5h18v14H3zM3 9h18M7 7h.01M10 7h.01",
  arrow: "M12 19V5M5 12l7-7 7 7",
  stop: "M5 5h14v14H5z",
  close: "M6 6l12 12M6 18L18 6",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  copy: "M9 9h12v12H9zM15 9V3H3v12h6",
  retry: "M20 8a8 8 0 1 0 0 8M20 3v5h-5",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.spark}"/></svg>`;
for (const el of document.querySelectorAll("[data-icon]"))
  el.innerHTML = icon(el.dataset.icon);
let profile = null,
  guest = null,
  guestLoading = null,
  ready = false,
  internetAvailable = false,
  chats = [],
  currentId = null,
  storageKey = null,
  abort = null,
  busy = false,
  deleteId = null;
let model = "velia-flash",
  agentMode = false,
  theme = "dark",
  toastTimer,
  saveTimer,
  slowTimer;
try {
  model = MODELS[localStorage.getItem("velia-web-model")]
    ? localStorage.getItem("velia-web-model")
    : model;
  theme =
    localStorage.getItem("velia-web-theme") === "light" ? "light" : "dark";
} catch {}
const request = (path, data, signal) =>
  fetch("/web-api/v1/" + path, {
    method: data === undefined ? "GET" : "POST",
    credentials: "same-origin",
    headers:
      data === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Velia-Request": "1" },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal,
  });
const current = () => chats.find((c) => c.id === currentId);
const browserStorage = { getItem: (key) => localStorage.getItem(key) };
function toast(text) {
  clearTimeout(toastTimer);
  $("toast").textContent = text;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4500);
}
function save() {
  if (!storageKey) return;
  try {
    localStorage.setItem(storageKey, JSON.stringify(chats.slice(0, 100)));
    localStorage.setItem(storageKey + ":active", currentId || "");
  } catch {
    toast(
      "Не удалось сохранить историю в браузере. Освободи место в хранилище.",
    );
  }
}
function scheduleSave() {
  if (saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    save();
  }, 500);
}
function setTheme(value) {
  theme = value;
  document.documentElement.dataset.theme = value;
  $("theme").setAttribute(
    "aria-label",
    value === "dark" ? "Светлая тема" : "Тёмная тема",
  );
  $("theme").innerHTML = icon(value === "dark" ? "sun" : "moon");
  try {
    localStorage.setItem("velia-web-theme", value);
  } catch {}
}
function closeModels() {
  $("model-menu").hidden = true;
  $("model-button").setAttribute("aria-expanded", "false");
}
function setModel(value) {
  if (busy || !MODELS[value] || (profile && !profile.models.includes(value)) || (!profile && value !== "velia-flash")) return;
  model = value;
  $("model-label").textContent = MODELS[value];
  $("model-icon").innerHTML = icon(value === "velia-flash" ? "bolt" : "spark");
  for (const option of document.querySelectorAll("[data-model]")) {
    option.setAttribute(
      "aria-selected",
      String(option.dataset.model === value),
    );
    option.disabled = profile ? !profile.models.includes(option.dataset.model) : option.dataset.model !== "velia-flash";
  }
  try {
    localStorage.setItem("velia-web-model", value);
  } catch {}
  closeModels();
}
function setAgentMode(value) {
  const enabled = !!value && !!profile?.browser_agent;
  if (enabled && model !== "velia-flash") setModel("velia-flash");
  if (agentMode !== enabled && !busy) {
    currentId = null;
    render();
    save();
  }
  agentMode = enabled;
  const button = $("agent-toggle");
  button.hidden = !profile?.browser_agent;
  button.classList.toggle("active", enabled);
  button.setAttribute("aria-pressed", String(enabled));
  button.title = enabled
    ? "Browser Agent включён · VELIA Flash может открывать сайты и выполнять действия"
    : "VELIA Agent Core · браузерные действия через Flash";
  button.querySelector("span:last-child").textContent = enabled ? "AGENT ON" : "AGENT";
  $("model-button").disabled = busy || enabled;
  try { localStorage.setItem("velia-web-agent", enabled ? "1" : "0"); } catch {}
}

function sidebar(open) {
  $("sidebar").classList.toggle("open", open);
  $("scrim").hidden = !open;
  $("menu").setAttribute("aria-expanded", String(open));
}
function resizePrompt() {
  $("prompt").style.height = "auto";
  $("prompt").style.height = Math.min(180, $("prompt").scrollHeight) + "px";
  $("send").disabled = !ready || busy || !$("prompt").value.trim();
}
function renderHistory() {
  $("history").replaceChildren();
  const filter = $("search").value.trim().toLocaleLowerCase(),
    items = [...chats]
      .filter(
        (c) =>
          (c.remote || c.messages.length) && c.title.toLocaleLowerCase().includes(filter),
      )
      .sort((a, b) => b.updated - a.updated);
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "history-empty";
    p.textContent = filter
      ? "Диалоги не найдены."
      : profile
        ? "Здесь будут твои разговоры с Велией."
        : "Начни разговор без регистрации. Flash доступен сразу.";
    $("history").append(p);
    return;
  }
  let group = "";
  for (const chat of items) {
    const label =
      new Date(chat.updated).toDateString() === new Date().toDateString()
        ? "Сегодня"
        : "Ранее";
    if (label !== group) {
      group = label;
      const h = document.createElement("div");
      h.className = "history-group";
      h.textContent = label;
      $("history").append(h);
    }
    const row = document.createElement("div");
    row.className = "history-row" + (chat.id === currentId ? " active" : "");
    const open = document.createElement("button");
    open.className = "history-open";
    open.textContent = chat.title;
    open.title = chat.title;
    open.disabled = busy;
    open.onclick = () => openChat(chat);
    const del = document.createElement("button");
    del.className = "history-delete";
    del.innerHTML = icon("trash");
    del.setAttribute("aria-label", "Удалить диалог «" + chat.title + "»");
    del.disabled = busy;
    del.onclick = () => {
      deleteId = chat.id;
      $("confirm-dialog").showModal();
    };
    row.append(open, del);
    $("history").append(row);
  }
}
function messageNode(message, index) {
  const article = document.createElement("article");
  article.className = "message " + message.role;
  article.dataset.index = index;
  if (message.role === "assistant") {
    const heading = document.createElement("div");
    heading.className = "message-heading";
    heading.innerHTML = '<img src="/web/favicon.svg" alt="">VELIA';
    const badge = document.createElement("span");
    badge.className = "message-model";
    badge.textContent = message.agent ? "AGENT" : message.model === "velia-flash" ? "FLASH" : "PRO";
    heading.append(badge);
    article.append(heading);
  }
  const content = document.createElement("div");
  content.className = "message-content";
  if (message.role === "user") content.textContent = message.content;
  else
    content.innerHTML = message.content
      ? renderMarkdown(message.content)
      : busy && !message.failed
        ? '<div class="thinking" aria-label="Велия готовит ответ"><span></span><span></span><span></span></div>'
        : "";
  article.append(content);
  if (message.role === "assistant" && message.search) {
    const sources = sourceNode(message.search);
    if (sources) article.append(sources);
  }
  if (message.role === "assistant" && !busy) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    if (message.content) {
      const copy = document.createElement("button");
      copy.innerHTML = icon("copy") + "Копировать";
      copy.onclick = () => copyText(message.content);
      actions.append(copy);
    }
    if (index === current().messages.length - 1) {
      const retry = document.createElement("button");
      retry.innerHTML = icon("retry") + "Повторить";
      retry.onclick = () => generate(true);
      actions.append(retry);
    }
    article.append(actions);
  }
  if (message.failed || message.stopped || message.finish === "length") {
    const state = document.createElement("div");
    state.className = "message-state";
    state.textContent =
      message.failed ||
      (message.stopped
        ? "Ответ остановлен."
        : "Достигнута длина ответа. Можно попросить продолжить.");
    article.append(state);
  }
  return article;
}
function sourceNode(value) {
  const result = safeSearch(value);
  if (!result.sources.length) return null;
  const section = document.createElement("div");
  section.className = "message-sources";
  const label = document.createElement("span");
  label.textContent = "Источники";
  section.append(label);
  result.sources.forEach((source, index) => {
    const link = document.createElement("a");
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "[" + (index + 1) + "] " + source.title;
    section.append(link);
  });
  if (result.retrieved_at) section.title = "Поиск выполнен: " + result.retrieved_at;
  return section;
}
function render() {
  const chat = current(),
    nonempty = !!chat?.messages.length;
  $("welcome").hidden = nonempty;
  $("messages").hidden = !nonempty;
  $("messages").replaceChildren();
  if (chat)
    chat.messages.forEach((m, i) => $("messages").append(messageNode(m, i)));
  renderHistory();
}
function scrollDown(force = false) {
  const el = $("conversation-scroll");
  if (force || el.scrollHeight - el.scrollTop - el.clientHeight < 180)
    el.scrollTop = el.scrollHeight;
}
function setBusy(value) {
  busy = value;
  $("send").hidden = value;
  $("stop").hidden = !value;
  $("prompt").disabled = value;
  $("model-button").disabled = value || agentMode;
  $("agent-toggle").disabled = value || !profile?.browser_agent;
  $("new-chat").disabled = value;
  $("account").disabled = value;
  $("send").disabled = value || !$("prompt").value.trim();
  for (const el of document.querySelectorAll(".suggestion"))
    el.disabled = value;
  renderHistory();
}
function newChat() {
  if (busy) return;
  currentId = null;
  $("prompt").value = "";
  $("generation-status").textContent = "";
  resizePrompt();
  render();
  save();
  sidebar(false);
  $("prompt").focus();
}
function openAuth() {
  $("auth-error").textContent = "";
  $("auth-dialog").showModal();
  setTimeout(() => $("pairing-code").focus(), 0);
}
async function jsonRequest(path, data) {
  const response = await request(path, data, AbortSignal.timeout(25000));
  const value = await response.json();
  if (!response.ok) throw new Error(apiError(value.error?.message || value.error));
  return value;
}
async function openChat(chat) {
  if (busy || (!profile && !guest)) return;
  const account = profile?.account;
  currentId = chat.id;
  sidebar(false);
  if (chat.remote) {
    $("generation-status").textContent = "Загружаю диалог…";
    try {
      const data = await jsonRequest("conversations/" + chat.id + "/messages");
      if (profile?.account !== account || currentId !== chat.id) return;
      const previous = chat.messages;
      chat.messages = data.messages.map((m, i) => ({
        role: m.role, content: m.content || "",
        requestId: previous[i]?.role === m.role && previous[i]?.content === m.content ? previous[i]?.requestId : undefined,
        model: m.chat_mode === "flash" ? "velia-flash" : "velia-pro",
        pending: m.status === "pending",
        failed: m.status === "error" ? "Не удалось получить ответ. Можно повторить запрос." : undefined,
        internet: m.web_search === true,
        search: m.role === "assistant" && m.web_search ? safeSearch(m.web_search) : undefined,
      }));
      chat.loaded = true;
    } catch (error) { toast(error.message); }
    finally { $("generation-status").textContent = ""; }
  }
  save();
  render();
}
async function syncHistory() {
  if (!profile || busy) return;
  const account = profile.account;
  try {
    const data = await jsonRequest("conversations");
    if (profile?.account !== account || busy) return;
    const cached = new Map(chats.map((c) => [c.id, c]));
    const remote = data.conversations.map((c) => ({
      ...cached.get(c.id), id: c.id, title: c.title || "Новый диалог",
      updated: Date.parse(c.updated_at || c.created_at) || Date.now(),
      messages: cached.get(c.id)?.messages || [], remote: true,
    }));
    chats = [...remote, ...chats.filter((c) => !c.remote && !remote.some((r) => r.id === c.id))].slice(0, 100);
    if (currentId && !current()) currentId = null;
    $("history-state").textContent = "История в твоём аккаунте";
    save(); render();
    if (current()?.remote) await openChat(current());
  } catch (error) {
    $("history-state").textContent = "Не удалось обновить историю";
    toast(error.message);
  }
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано.");
  } catch {
    toast("Не удалось скопировать. Выдели текст и скопируй вручную.");
  }
}
function applyProfile(value) {
  const changed = profile?.account !== value.account;
  profile = value;
  internetAvailable = !!value.web_search;
  $("guest-notice").hidden = true;
  let restoreAgent = false;
  try { restoreAgent = localStorage.getItem("velia-web-agent") === "1"; } catch {}
  setAgentMode(restoreAgent && !!value.browser_agent);
  $("research-link").hidden = false;
  storageKey = "velia-web-chats-v1:" + value.account;
  if (changed) {
    chats = loadChats(browserStorage, storageKey);
    try {
      currentId = localStorage.getItem(storageKey + ":active");
    } catch {
      currentId = null;
    }
    if (!current()) currentId = null;
    for (const chat of chats)
      for (const m of chat.messages)
        if (m.pending) {
          m.pending = false;
          m.stopped = true;
        }
    save();
  }
  $("account-label").replaceChildren(document.createTextNode(value.name || "Аккаунт VELIA"));
  const small = document.createElement("small");
  small.textContent = (Number.isInteger(value.credits) ? value.credits + " токенов · " : "") + "Выйти из аккаунта";
  $("account-label").append(small);
  if (!value.models.includes(model)) model = value.models[0] || "velia-flash";
  $("pro-description").textContent = value.pro_locked_reason ?
    apiError(value.pro_locked_reason) : "Доступен с токенами на балансе";
  setModel(model);
  render();
  syncHistory();
}
function applyGuest(value) {
  if (profile) return;
  $("research-link").hidden = true;
  const changed = guest?.account !== value.account || storageKey !== "velia-web-guest-v1:" + value.account;
  guest = value;
  internetAvailable = !!value.web_search;
  setAgentMode(false);
  $("agent-toggle").hidden = true;
  storageKey = "velia-web-guest-v1:" + value.account;
  if (changed) {
    chats = loadChats(browserStorage, storageKey);
    try { currentId = localStorage.getItem(storageKey + ":active"); } catch { currentId = null; }
    if (!current()) currentId = null;
    for (const c of chats) for (const m of c.messages) if (m.pending) { m.pending = false; m.stopped = true; }
  }
  model = "velia-flash";
  setModel(model);
  $("history-state").textContent = "Гостевая история в этом браузере";
  $("account-label").innerHTML = "Войти в VELIA<small>Синхронизация истории и доступ к PRO</small>";
  $("pro-description").textContent = "Войди в аккаунт с токенами для PRO";
  $("guest-notice").hidden = false;
  $("guest-counter").textContent = value.remaining > 0 ? "Без регистрации · осталось " + value.remaining + " из 30 сообщений" : "30 гостевых сообщений использованы. Войди, чтобы продолжить.";
  save(); render();
}
async function refreshGuest() {
  if (profile) return;
  if (guestLoading) return guestLoading;
  guestLoading = (async () => {
  try {
    const response = await request("guest", undefined, AbortSignal.timeout(15000));
    if (response.ok && !profile) applyGuest(await response.json());
  } catch {}
  })();
  try { await guestLoading; } finally { guestLoading = null; }
}
async function generate(retry = false) {
  if (busy || !ready) return;
  if (agentMode && !profile) { openAuth(); return; }
  if (!profile) {
    if (!guest) await refreshGuest();
    if (busy) return;
    if (!guest || guest.remaining <= 0) { openAuth(); return; }
  }
  if (profile && !agentMode && !profile.models.includes(model)) {
    toast(apiError(profile.pro_locked_reason || "pro_tokens_required"));
    return;
  }
  const text = $("prompt").value.trim();
  if (!retry && !text) return;
  let chat = current();
  if (retry) {
    if (!chat || chat.messages.at(-1)?.role !== "assistant") return;
    chat.messages.pop();
  } else {
    if (!chat) {
      if (!profile || agentMode) {
        chat = {id: crypto.randomUUID(), title: text.replace(/\s+/g, " ").slice(0, 70),
          updated: Date.now(), messages: [], agent: agentMode};
      } else {
      setBusy(true);
      try {
        const result = await jsonRequest("conversations", {title: text.replace(/\s+/g, " ").slice(0, 70)});
        chat = {id: result.conversation.id, title: result.conversation.title, updated: Date.now(), messages: [], remote: true, loaded: true};
      } catch (error) {
        toast(error.message);
        return;
      } finally { setBusy(false); }
      }
      chats.unshift(chat);
      chats = chats.slice(0, 100);
      currentId = chat.id;
    }
    chat.messages.push({ role: "user", content: text, requestId: crypto.randomUUID(),
      internet: !agentMode && internetAvailable, agent: agentMode });
    $("prompt").value = "";
    resizePrompt();
  }
  const selected = agentMode ? "velia-flash" : model,
    user = chat.messages.at(-1),
    payload = agentMode ? {prompt: user.content, session_id: chat.id} : chat.remote ? {content: user.content, model: selected,
      idempotency_key: user.requestId || (user.requestId = crypto.randomUUID())} : chatPayload(chat, selected),
    answer = { role: "assistant", content: "", model: selected, pending: true, agent: agentMode };
  if (!agentMode && internetAvailable) payload.web_search = true;
  chat.messages.push(answer);
  chat.updated = Date.now();
  abort = new AbortController();
  setBusy(true);
  render();
  scrollDown(true);
  save();
  $("generation-status").textContent = agentMode
    ? "VELIA Agent открывает браузер…"
    : internetAvailable ? "Ищу информацию в интернете…" : "Велия готовит ответ…";
  slowTimer = setTimeout(() => {
    $("generation-status").textContent =
      agentMode
        ? "VELIA Agent выполняет задачу в браузере. Сложные действия могут занять несколько минут."
        : selected === "velia-flash"
          ? "Flash готовит ответ. Первый запрос может занять несколько минут."
          : "Велия работает над ответом…";
  }, 20000);
  let paintFrame = null, textPainted = false;
  const article = $("messages").lastElementChild,
    contentNode = article.querySelector(".message-content"),
    conversationScroll = $("conversation-scroll");
  const paint = () => {
    paintFrame = null;
    const shouldScroll = conversationScroll.scrollHeight - conversationScroll.scrollTop - conversationScroll.clientHeight < 180;
    contentNode.innerHTML = renderMarkdown(answer.content);
    textPainted = true;
    if (shouldScroll) scrollDown(true);
  };
  const queuePaint = () => {
    // Show the first text immediately; later bursts share a browser frame.
    if (!textPainted) paint();
    else if (paintFrame === null) paintFrame = requestAnimationFrame(paint);
  };
  try {
    if (agentMode) {
      const response = await request("agent/browser", payload, abort.signal);
      const data = await response.json();
      if (!response.ok || data.ok !== true) {
        const error = new Error(apiError(data.error));
        error.status = response.status;
        throw error;
      }
      answer.content = data.text || "";
      answer.finish = "stop";
      $("generation-status").textContent = "";
      queuePaint();
      scheduleSave();
    } else {
    const response = await request(!profile ? "guest/chat/completions" : chat.remote ? "conversations/" + chat.id + "/messages/stream" : "chat/completions", payload, abort.signal);
    if (!profile && guest && response.headers.has("X-Velia-Guest-Remaining")) {
      guest.remaining = Number(response.headers.get("X-Velia-Guest-Remaining"));
      $("guest-counter").textContent = guest.remaining > 0 ? "Без регистрации · осталось " + guest.remaining + " из 30 сообщений" : "30 гостевых сообщений использованы. Войди, чтобы продолжить.";
    }
    const result = await readCompletion(response, (content) => {
      answer.content = content;
      $("generation-status").textContent = "";
      queuePaint();
      scheduleSave();
    }, (search) => {
      answer.search = search;
      const article = $("messages").lastElementChild;
      article.querySelector(".message-sources")?.remove();
      const sources = sourceNode(search);
      if (sources) article.append(sources);
      $("generation-status").textContent = "Велия изучает источники…";
      scheduleSave();
    });
    answer.finish = result.finish;
    }
  } catch (error) {
    if (error.name === "AbortError") answer.stopped = true;
    else {
      answer.failed =
        error.message || "Соединение прервалось. Попробуй ещё раз.";
      if (error.status === 401) {
        profile = null;
        toast("Сессия завершилась. Войди снова.");
        $("account-label").textContent = "Войти в VELIA";
      }
      if (error.status === 402 || error.status === 503) {
        request("session").then(async (r) => { if (r.ok) applyProfile(await r.json()); }).catch(() => {});
      }
      if (!profile && error.status === 429) {
        await refreshGuest();
        if (guest?.remaining === 0) openAuth();
      }
    }
  } finally {
    if (paintFrame !== null) cancelAnimationFrame(paintFrame);
    clearTimeout(slowTimer);
    clearTimeout(saveTimer);
    saveTimer = null;
    answer.pending = false;
    abort = null;
    setBusy(false);
    $("generation-status").textContent = "";
    save();
    render();
    scrollDown();
    $("prompt").focus();
    if (!profile) refreshGuest();
  }
}
$("composer").onsubmit = (e) => {
  e.preventDefault();
  generate();
};
$("prompt").oninput = resizePrompt;
$("prompt").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    generate();
  }
};
$("stop").onclick = () => abort?.abort();
$("new-chat").onclick = newChat;
$("search").oninput = renderHistory;
$("menu").onclick = () => sidebar(!$("sidebar").classList.contains("open"));
$("scrim").onclick = () => sidebar(false);
$("theme").onclick = () => setTheme(theme === "dark" ? "light" : "dark");
$("agent-toggle").onclick = () => {
  if (!profile) { openAuth(); return; }
  if (!profile.browser_agent) { toast("Browser Agent пока недоступен."); return; }
  setAgentMode(!agentMode);
  $("prompt").focus();
};
$("model-button").onclick = () => {
  const open = $("model-menu").hidden;
  $("model-menu").hidden = !open;
  $("model-button").setAttribute("aria-expanded", String(open));
  if (open) $("model-menu").querySelector("[aria-selected=true]").focus();
};
for (const option of document.querySelectorAll("[data-model]"))
  option.onclick = () => {
    setModel(option.dataset.model);
    $("model-button").focus();
  };
document.addEventListener("click", (e) => {
  if (!e.target.closest(".model-control")) closeModels();
  const copy = e.target.closest(".copy-code");
  if (copy)
    copyText(copy.closest(".code-block").querySelector("code").textContent);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeModels();
    sidebar(false);
  }
  if (
    e.target.closest(".model-menu") &&
    ["ArrowDown", "ArrowUp"].includes(e.key)
  ) {
    e.preventDefault();
    const options = [
        ...$("model-menu").querySelectorAll("button:not(:disabled)"),
      ],
      index = options.indexOf(document.activeElement);
    options[
      (index + (e.key === "ArrowDown" ? 1 : -1) + options.length) %
        options.length
    ].focus();
  }
});
for (const suggestion of document.querySelectorAll(".suggestion"))
  suggestion.onclick = () => {
    $("prompt").value = suggestion.dataset.prompt;
    resizePrompt();
    $("prompt").focus();
  };
$("auth-close").onclick = () => $("auth-dialog").close();
$("guest-login").onclick = openAuth;
$("pairing-link").onclick = () => {
  try { sessionStorage.setItem("velia-auth-pending", "1"); } catch {}
  $("auth-title").textContent = "Введи код из Telegram";
  $("auth-help").textContent = "Скопируй одноразовый код от бота и вернись сюда. Поле ввода уже открыто.";
};
$("code-ready").onclick = () => $("pairing-code").focus();
function resumeAuth() {
  if (profile || document.visibilityState === "hidden") return;
  try { if (sessionStorage.getItem("velia-auth-pending") !== "1") return; } catch { return; }
  if (!$("auth-dialog").open) openAuth();
  $("pairing-code").focus();
}
window.addEventListener("focus", resumeAuth);
window.addEventListener("pageshow", resumeAuth);
document.addEventListener("visibilitychange", resumeAuth);
$("auth-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = $("auth-submit");
  button.disabled = true;
  $("auth-error").textContent = "";
  try {
    const response = await request(
        "auth/exchange",
        { pairing_code: $("pairing-code").value },
        AbortSignal.timeout(25000),
      ),
      data = await response.json();
    if (!response.ok) throw new Error(apiError(data.error));
    applyProfile(data);
    try { sessionStorage.removeItem("velia-auth-pending"); } catch {}
    $("auth-dialog").close();
    $("pairing-code").value = "";
    toast("Ты в VELIA. Можно начинать.");
    $("prompt").focus();
  } catch (error) {
    $("auth-error").textContent =
      error.message || "Не удалось войти. Попробуй ещё раз.";
  } finally {
    button.disabled = false;
  }
};
$("account").onclick = async () => {
  if (!profile) {
    openAuth();
    return;
  }
  try {
    const response = await request(
      "auth/logout",
      {},
      AbortSignal.timeout(20000),
    );
    if (!response.ok) throw new Error();
    profile = null;
    $("research-link").hidden = true;
    storageKey = null;
    chats = [];
    currentId = null;
    $("prompt").value = "";
    resizePrompt();
    $("account-label").innerHTML =
      "Войти в VELIA<small>Твоё личное пространство</small>";
    $("pro-description").textContent = "Доступен с токенами на балансе";
    $("history-state").textContent = "История в твоём аккаунте";
    render();
    toast("Ты вышел из аккаунта.");
    await refreshGuest();
  } catch {
    toast("Не удалось выйти. Попробуй ещё раз.");
  }
};
$("delete-cancel").onclick = () => $("confirm-dialog").close();
$("delete-confirm").onclick = async () => {
  const chat = chats.find((c) => c.id === deleteId);
  const button = $("delete-confirm");
  button.disabled = true;
  try {
    if (chat?.remote) await jsonRequest("conversations/" + chat.id + "/delete", {});
  chats = chats.filter((c) => c.id !== deleteId);
  if (currentId === deleteId) currentId = null;
  $("confirm-dialog").close();
  save();
  render();
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
};
window.addEventListener("pagehide", save);
window.addEventListener("storage", (e) => {
  if (e.key === storageKey && !busy) {
    chats = loadChats(browserStorage, storageKey);
    if (!current()) currentId = null;
    render();
  }
});
setTheme(theme);
setModel(model);
renderHistory();
resizePrompt();
resumeAuth();
try {
  const response = await request(
    "session",
    undefined,
    AbortSignal.timeout(20000),
  );
  if (response.ok) applyProfile(await response.json());
  else if (response.status === 401) await refreshGuest();
  else if (response.status !== 401)
    toast("Сервис входа временно недоступен. Попробуй позже.");
} catch {
  toast("Не удалось проверить вход. Можно попробовать войти вручную.");
}
ready = true;
resizePrompt();
