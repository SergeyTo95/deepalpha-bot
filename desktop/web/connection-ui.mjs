import {serviceIcon} from './service-icons.mjs';
export function connectionRow(parent, title, description) {
  const row = document.createElement("section");
  row.className = "connection-row";
  row.dataset.connection = title.toLowerCase();
  const icon = document.createElement("span");
  icon.className = "connection-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.append(serviceIcon(title));
  const copy = document.createElement("div");
  copy.className = "connection-copy";
  const name = document.createElement("h3");
  name.textContent = title;
  const status = document.createElement("p");
  status.textContent = description;
  status.setAttribute("aria-live", "polite");
  copy.append(name, status);
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "connection-toggle";
  toggle.textContent = "Подключить";
  toggle.disabled = true;
  const detail = document.createElement("div");
  detail.className = "connection-detail";
  detail.hidden = true;
  row.append(icon, copy, toggle, detail);
  parent.append(row);
  let connected = false,
    busy = false,
    available = false;
  function render() {
    row.dataset.connected = String(connected);
    toggle.textContent = connected ? "Отключить" : "Подключить";
    toggle.setAttribute("aria-label", toggle.textContent + " " + title);
    toggle.disabled = busy || !available;
    toggle.setAttribute("aria-busy", String(busy));
  }
  return {
    row,
    status,
    toggle,
    detail,
    get connected() {
      return connected;
    },
    show(value, message) {
      connected = Boolean(value);
      available = true;
      status.textContent = message || (connected ? "Подключено" : description);
      render();
    },
    async run(action, { signal, toast }) {
      if (busy) return;
      busy = true;
      render();
      try {
        await action();
      } catch (error) {
        if (!signal.aborted) toast(error.message);
      } finally {
        if (!signal.aborted) {
          busy = false;
          render();
        }
      }
    },
  };
}
