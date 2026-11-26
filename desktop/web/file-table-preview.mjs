import {formatCell, groupCSV, groupRows, readTable, summarizeRows} from './table-tools.mjs';

export async function mountTable(body, file, signal, button, download) {
  const status = document.createElement('p');
  status.textContent = 'Читаю таблицу на устройстве…';
  body.append(status);
  const sheets = await readTable(file, signal).finally(() => status.remove());
  signal.throwIfAborted();
  const controls = document.createElement('div');
  controls.className = 'file-pager';
  const picker = document.createElement('select');
  picker.setAttribute('aria-label','Лист таблицы');
  for (const [index,sheet] of sheets.entries()) picker.add(new Option(sheet.name, index));
  picker.hidden = sheets.length < 2;
  controls.append(picker);
  const content = document.createElement('section');
  body.append(controls, content);

  function table(rows, headers) {
    const viewport = document.createElement('div');
    viewport.className = 'file-table-scroll';
    const view = document.createElement('table');
    const columns = view.createTHead().insertRow();
    for (const title of headers) {
      const label = document.createElement('th');
      label.scope = 'col';label.textContent = title;columns.append(label);
    }
    const tbody = view.createTBody();
    for (const values of rows) {
      const line = tbody.insertRow();
      for (let index = 0; index < headers.length; index++) {
        line.insertCell().textContent = formatCell(values[index]).slice(0,500);
      }
    }
    viewport.append(view);
    return viewport;
  }

  function show() {
    const {rows} = sheets[Number(picker.value)];
    const summary = summarizeRows(rows);
    const overview = document.createElement('p');
    overview.textContent = `${summary.rows} строк данных · ${summary.duplicates} повторов строк`;
    content.replaceChildren(overview);
    if (!summary.columns.length) return;
    if (summary.rows > 50) {
      const note = document.createElement('p');
      note.textContent = 'Показаны первые 50 строк. Итоги учитывают весь лист.';
      content.append(note);
    }
    content.append(table(rows.slice(1, 51), summary.columns.map(item => item.name)));

    const numeric = summary.columns.map((column,index) => ({...column,index})).filter(column => column.numeric);
    if (!numeric.length) return;
    const group = document.createElement('select');
    group.setAttribute('aria-label','Столбец с категориями');
    for (const [index,column] of summary.columns.entries()) group.add(new Option(column.name,index));
    const amount = document.createElement('select');
    amount.setAttribute('aria-label','Числовой столбец');
    for (const column of numeric) amount.add(new Option(column.name,column.index));
    const toolbar = document.createElement('div');
    toolbar.className = 'file-pager';
    const output = document.createElement('section');

    toolbar.append(group, amount, button('Сгруппировать и сложить', () => {
      const values = groupRows(rows, Number(group.value), Number(amount.value));
      const view = table(values.slice(0, 100).map(item => [item.label,item.count,item.total]), ['Категория','Строки','Сумма']);
      output.replaceChildren(view, button('Скачать сводку CSV', () => {
        download('\uFEFF' + groupCSV(values), 'VELIA-summary.csv', 'text/csv');
      }));
    }));
    content.append(toolbar, output);
  }
  picker.onchange = show;
  show();
}
