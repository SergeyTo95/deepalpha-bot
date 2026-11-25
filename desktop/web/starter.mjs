const examples = {
  meeting: {
    title: 'Разобрать встречу',
    input: 'Анна готовит макет к пятнице. Олег пришлёт смету завтра к 12:00. Клиент ещё не подтвердил бюджет.',
    result: ['Макет — Анна, к пятнице.', 'Смета — Олег, завтра к 12:00.', 'Бюджет — ждём подтверждения клиента.'],
    prompt: 'Выполни миссию: разбери мои заметки со встречи. Выдели решения, ответственных, сроки и вопросы без ответа. Если сроки относительные, спроси дату встречи. Подготовь черновик письма с итогами.\n\nЗаметки со встречи:\n',
  },
  comparison: {
    title: 'Сравнить варианты',
    input: 'Бюджет 15 000 ₽. Вариант А стоит 12 000 ₽ и займёт три дня. Вариант Б стоит 14 500 ₽ и займёт один день. Важнее всего уложиться в два дня.',
    result: ['А — остаётся 3 000 ₽, срок превышен на день.', 'Б — остаётся 500 ₽, срок подходит.', 'Выбор для данных условий — Б. Уточнить, что входит в цену.'],
    prompt: 'Выполни миссию: сравни варианты и помоги выбрать. Сделай таблицу критериев, укажи источники и дату проверки, обозначь неизвестное и дай следующий шаг. Подготовь PDF.\n\nВарианты, бюджет и критерии:\n',
  },
  day: {
    title: 'Спланировать день',
    input: 'В 11:00 созвон на 30 минут. Смету нужно отправить до 13:00. На неё нужен час. После обеда есть 45 минут на разбор почты.',
    result: ['09:45–10:45 · подготовить смету.', '11:00–11:30 · созвон.', '11:30–12:00 · проверить и отправить смету.', '14:00–14:45 · разобрать почту.'],
    prompt: 'Помоги спланировать мой день по указанным встречам, срокам и длительности дел. Дай порядок действий с учётом перерывов, предложи одно напоминание для главного срока.\n\nМои дела и встречи:\n',
  },
};
const element = (tag, text) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
};

export function mountStarter({onDraft, onEvent = () => {}}) {
  const dialog = document.getElementById('starter-example');
  const title = document.getElementById('starter-title');
  const content = document.getElementById('starter-content');
  const use = document.getElementById('starter-use');
  const close = document.getElementById('starter-close');
  let selected;
  close.onclick = () => dialog.close();
  use.onclick = () => {
    if (onDraft(selected.prompt) === false) return;
    onEvent('example_used', selected.key);
    dialog.close();
  };
  for (const button of document.querySelectorAll('[data-starter]')) {
    const key = button.dataset.starter;
    button.addEventListener('click', () => {
      selected = {...examples[key], key};
      title.textContent = selected.title;
      const list = element('ul');
      for (const item of selected.result) list.append(element('li', item));
      const quote = element('blockquote', selected.input);
      content.replaceChildren(element('h3', 'Входные данные'), quote, element('h3', 'Пример результата'), list);
      dialog.showModal();
      onEvent('example_viewed', key);
    });
  }
  document.getElementById('starter-demo-game').addEventListener('click', () => onEvent('example_viewed', 'game'));
  document.getElementById('starter-own-game').addEventListener('click', () => {
    if (onDraft('Создай оригинальный ретрошутер от первого лица. Назови игру «…» и выбери оформление.') !== false)
      onEvent('example_used', 'game');
  });
  const calculator = document.getElementById('starter-calculator');
  calculator.addEventListener('input', () => {
    const data = new FormData(calculator);
    const values = ['count', 'cost', 'price'].map(key => Number(data.get(key)));
    if (values.some(value => !Number.isFinite(value) || value < 0 || value > 1e7)) return;
    const [count, cost, price] = values;
    calculator.querySelector('output').textContent = 'Прибыль ' +
      new Intl.NumberFormat('ru', {style:'currency', currency:'RUB', maximumFractionDigits:2}).format(count * (price - cost));
  });
  document.getElementById('starter-own-calculator').onclick = () => {
    if (onDraft('Сделай интерактивный калькулятор прибыли с полями для количества, себестоимости и цены продажи.') !== false)
      onEvent('example_used', 'calculator');
  };
  calculator.addEventListener('submit', event => event.preventDefault());
}
