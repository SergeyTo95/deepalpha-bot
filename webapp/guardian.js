(() => {
  'use strict';
  const api = '/api/security/events';
  const items = document.getElementById('events');
  const status = document.getElementById('status');
  const readAll = document.getElementById('read-all');
  const all = document.getElementById('all');
  const unread = document.getElementById('unread');
  const refreshButton = document.getElementById('refresh');
  let events = [];
  let unreadOnly = false;
  let busy = false;
  const names = {
    web_login: 'Новый вход через браузер',
    mobile_login: 'Подключено новое устройство',
    refresh_replay: 'Повторное использование токена: сессия отозвана',
    device_mismatch: 'Устройство не совпало: сессия отозвана',
    agent_access_warning: 'Подозрительная автоматизация',
    session_revoked: 'Сессия отозвана',
  };
  const el = (tag, className, value) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = String(value);
    return node;
  };
  function render() {
    items.replaceChildren();
    const filtered = unreadOnly ? events.filter(event => !event.read) : events;
    all.setAttribute('aria-pressed', String(!unreadOnly));
    unread.setAttribute('aria-pressed', String(unreadOnly));
    if (!filtered.length) {
      items.append(el('div', 'empty', 'Нет уведомлений безопасности'));
      return;
    }
    for (const event of filtered) {
      const card = el('article', 'event ' + (event.read ? '' : 'unread ') + (event.severity || ''));
      card.append(el('span', 'dot'));
      const body = el('div', 'body');
      body.append(el('div', 'title', names[event.kind] || 'Событие безопасности'));
      body.append(el('div', 'meta', [event.source, event.detail].filter(Boolean).join(' · ')));
      body.append(el('div', 'date', String(event.created_at || '').slice(0, 16).replace('T', ' ') + ' UTC'));
      card.append(body);
      if (!event.read) {
        const action = el('button', '', 'Прочитано');
        action.addEventListener('click', () => markRead(event.id));
        card.append(action);
      }
      items.append(card);
    }
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    refreshButton.disabled = true;
    status.textContent = 'Загрузка…';
    try {
      const response = await fetch(api, { credentials: 'same-origin', cache: 'no-store' });
      if (response.status === 401) throw Error('Нужно войти в аккаунт');
      if (!response.ok) throw Error('События временно недоступны');
      const data = await response.json();
      if (!data.ok) throw Error('События временно недоступны');
      events = data.events || [];
      document.getElementById('count').textContent = 'Непрочитанных: ' + data.unread;
      readAll.hidden = data.unread === 0;
      status.textContent = '';
      render();
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; refreshButton.disabled = false; }
  }
  async function markRead(id) {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(api + '/read', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(id ? { event_id: id } : {}),
      });
      if (!response.ok) throw Error('Не удалось обновить уведомления');
      const data = await response.json();
      if (!data.ok) throw Error('Не удалось обновить уведомления');
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; refresh(); }
  }
  all.addEventListener('click', () => { unreadOnly = false; render(); });
  unread.addEventListener('click', () => { unreadOnly = true; render(); });
  refreshButton.addEventListener('click', refresh);
  readAll.addEventListener('click', () => markRead(null));
  refresh();
})();
