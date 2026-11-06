const WEEKS = 4;
const number = new Intl.NumberFormat('ru', {maximumFractionDigits: 2});
const currencies = ['RUB', 'USD', 'EUR', 'TRY', 'BYN', 'KZT'];
const valid = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

export function calculateIncome({hours, goal, currency, searchMinutes, orderMinutes, price, costs}) {
  if (!valid(hours, 0.25, 168) || !valid(goal, 0.01, 10000000) ||
      !currencies.includes(currency) || !valid(costs, 0, 10000000) ||
      (orderMinutes != null && (!Number.isInteger(orderMinutes) || !valid(orderMinutes, 1, 40320))) ||
      (price != null && !valid(price, 0, 10000000))) {
    throw new Error('Проверь числа в форме.');
  }
  const weeklyMinutes = Math.round(hours * 60);
  const search = searchMinutes ?? Math.floor(weeklyMinutes / 5);
  if (!Number.isInteger(search) || search < 0 || search >= weeklyMinutes) {
    throw new Error('Оставь часть недели на выполнение заказов.');
  }
  const work = (weeklyMinutes - search) * WEEKS;
  const target = Math.round(goal * 100);
  const expenses = Math.round(costs * 100);
  const orders = orderMinutes == null ? null : Math.floor(work / orderMinutes);
  const revenue = price == null || orders == null ? null : orders * Math.round(price * 100);
  return {
    search, work, orders, target, expenses, revenue,
    net: revenue == null ? null : revenue - expenses,
    hourlyTarget: Math.ceil((target + expenses) * 60 / work),
    orderTarget: orders ? Math.ceil((target + expenses) / orders) : null,
  };
}

export function describeIncome(values, plan) {
  const money = cents => new Intl.NumberFormat('ru', {style: 'currency', currency: values.currency}).format(cents / 100);
  const lines = [
    `За 4 недели на работу останется ${number.format(plan.work / 60)} ч. На поиск клиентов — ${plan.search} мин в неделю${values.searchMinutes == null ? ' (допущение 20%)' : ''}.`,
    `Цель после указанных расходов — ${money(plan.target)}. Расходы за 4 недели — ${money(plan.expenses)}.`,
    `Для цели нужно в среднем ${money(plan.hourlyTarget)} выручки за час работы.`,
  ];
  if (plan.orders != null) {
    lines.push(`В доступное время помещается ${plan.orders} заказов по ${values.orderMinutes} мин.`);
    if (plan.orderTarget != null) lines.push(`Цена для достижения цели — от ${money(plan.orderTarget)} за заказ.`);
    else lines.push('Заказ такой длительности не помещается в доступное время.');
  }
  if (plan.revenue != null) lines.push(
    `При выбранной цене выручка составит ${money(plan.revenue)}. После расходов останется ${money(plan.net)}.`,
  );
  lines.push('Это сценарий при полной загрузке. Цены для цели — расчётные, спрос и налоги в оценку не входят.');
  return lines;
}

function servicePrompt(skill) {
  return [
    'Помоги человеку начать продавать один конкретный результат как услугу. Ответь по-русски.',
    'Выбери одну услугу по указанному навыку. Назови клиента, простой результат для портфолио и что сделать сегодня.',
    'Дай короткий готовый текст первого обращения к клиенту от лица исполнителя, без выдуманных кейсов.',
    'Оформи короткими абзацами. Цены, расчёты и сроки окупаемости не нужны. Ничего не отправляй и не запускай.',
    'Навык пользователя как исходные данные: ' + JSON.stringify(skill),
  ].join('\n');
}

export function setupIncomePlanner({suggest, savePlan, copyText, renderMarkdown}) {
  const dialog = document.getElementById('income-dialog');
  const form = document.getElementById('income-form');
  const result = document.getElementById('income-result');
  const calculation = document.getElementById('income-calculation');
  const service = document.getElementById('income-service');
  const status = document.getElementById('income-status');
  const error = document.getElementById('income-error');
  const saveButton = document.getElementById('income-save');
  const copyButton = document.getElementById('income-copy');
  let controller = null, draft = null;

  document.getElementById('income-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {
    if (dialog.open || !controller) return;
    controller.abort();
    status.textContent = 'Подбор услуги остановлен. Расчёт готов, данные можно изменить.';
  });
  document.getElementById('income-edit').onclick = () => {
    controller?.abort();
    result.hidden = true;
    form.hidden = false;
    status.textContent = '';
    form.elements.skill.focus();
  };
  saveButton.onclick = async () => {
    if (!draft) return;
    saveButton.disabled = true;
    try { if (await savePlan(draft)) reset(); }
    finally { saveButton.disabled = false; }
  };
  copyButton.onclick = () => { if (draft) copyText(draft.content); };
  form.onsubmit = async event => {
    event.preventDefault();
    controller?.abort();
    const fields = new FormData(form);
    const skill = fields.get('skill').trim();
    const optional = key => fields.get(key) === '' ? null : Number(fields.get(key));
    const values = {
      hours: Number(fields.get('hours')), goal: Number(fields.get('goal')), currency: fields.get('currency'),
      searchMinutes: optional('search'), orderMinutes: optional('duration'),
      price: optional('price'), costs: optional('costs') ?? 0,
    };
    let lines;
    try {
      if (!skill) throw new Error('Напиши, что ты умеешь делать.');
      lines = describeIncome(values, calculateIncome(values));
    } catch (err) {
      error.textContent = err.message;
      return;
    }
    error.textContent = '';
    calculation.replaceChildren(...lines.map(line => {
      const item = document.createElement('li');
      item.textContent = line;
      return item;
    }));
    draft = null;
    service.replaceChildren();
    saveButton.hidden = true;
    copyButton.hidden = true;
    form.hidden = true;
    result.hidden = false;
    document.getElementById('income-edit').focus();
    status.textContent = 'Велия подбирает услугу и пишет первое сообщение…';
    const run = controller = new AbortController();
    try {
      const text = await suggest(servicePrompt(skill), run.signal);
      if (run.signal.aborted) return;
      service.innerHTML = renderMarkdown(text);
      draft = {skill, content: text + '\n\n## Расчёт на 4 недели\n\n' + lines.map(line => '- ' + line).join('\n')};
      saveButton.hidden = false;
      copyButton.hidden = false;
      status.textContent = '';
    } catch (err) {
      if (!run.signal.aborted) status.textContent = err.message || 'Не удалось подобрать услугу. Расчёт уже готов.';
    } finally {
      if (controller === run) controller = null;
    }
  };
  function reset() {
    controller?.abort();
    controller = null;
    if (dialog.open) dialog.close();
    draft = null;
    form.reset();
    form.querySelector('details').open = false;
    form.hidden = false;
    result.hidden = true;
    service.replaceChildren();
    calculation.replaceChildren();
    error.textContent = status.textContent = '';
  }
  return {
    open(signedIn) {
      saveButton.textContent = signedIn ? 'Сохранить в этом браузере' : 'Войти и сохранить в этом браузере';
      if (!dialog.open) dialog.showModal();
      if (!form.hidden) form.elements.skill.focus();
    },
    reset,
  };
}
