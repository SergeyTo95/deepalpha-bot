const MAX_ROWS = 20000;
const MAX_COLUMNS = 128;
const MAX_CELLS = 250000;

export const spreadsheetType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
export const isSpreadsheet = file => file.type === spreadsheetType || /\.csv$/iu.test(file.name);

export function boundedRows(rows) {
  let cells = 0;
  if (rows.length > MAX_ROWS) throw Error('В таблице больше 20 000 строк.');
  for (const row of rows) {
    cells += row.length;
    if (row.length > MAX_COLUMNS || cells > MAX_CELLS) throw Error('В таблице слишком много ячеек.');
  }
  return rows;
}

export function formatCell(value) {
  return value instanceof Date ? value.toISOString().slice(0, 10) : String(value ?? '');
}

function numeric(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string' || !/^[+-]?\d+(?:[.,]\d+)?$/u.test(value.trim())) return null;
  const number = Number(value.trim().replace(',', '.'));
  return Number.isFinite(number) ? number : null;
}

export function summarizeRows(rows) {
  const [headers = [], ...records] = boundedRows(rows);
  const width = records.reduce((max, row) => Math.max(max, row.length), headers.length);
  const counts = new Map();
  for (const row of records) {
    const padded = Array.from({length: width}, (_, index) => formatCell(row[index]));
    const key = JSON.stringify(padded);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return {
    rows: records.length,
    duplicates: records.length - counts.size,
    columns: Array.from({length: width}, (_, index) => {
      const column = {name: formatCell(headers[index]) || 'Колонка ' + (index + 1), blanks: 0, numeric: 0, total: 0};
      let corrections = 0;
      for (const row of records) {
        const value = row[index];
        if (formatCell(value).trim() === '') column.blanks++;
        const number = numeric(value);
        if (number !== null) {
          const adjusted = number - corrections;
          const next = column.total + adjusted;
          corrections = (next - column.total) - adjusted;
          column.total = next;
          column.numeric++;
        }
      }
      return column;
    }),
  };
}

export function groupRows(rows, groupColumn, amountColumn) {
  const totals = new Map();
  for (const row of rows.slice(1)) {
    const label = formatCell(row[groupColumn]).trim() || 'Без категории';
    const item = totals.get(label) || {label, count: 0, total: 0};
    const amount = numeric(row[amountColumn]);
    item.count++;
    if (amount !== null) item.total += amount;
    totals.set(label, item);
  }
  return Array.from(totals.values()).sort((a, b) => b.total - a.total || a.label.localeCompare(b.label));
}

function csvValue(value) {
  const text = formatCell(value);
  const escaped = typeof value === 'string' && /^[\s\uFEFF]*[=+@\-\t\r\n]/u.test(text) ? "'" + text : text;
  return '"' + escaped.replaceAll('"', '""') + '"';
}

export const groupCSV = groups => [['Категория', 'Строки', 'Сумма'], ...groups.map(item => [item.label, item.count, item.total])]
  .map(row => row.map(csvValue).join(';')).join('\r\n');

export function workbookContext(sheets) {
  const clean = value => formatCell(value).replace(/[\r\n\t]/gu, ' ').slice(0, 160);
  const details = sheets.map(({name, rows}) => {
    const summary = summarizeRows(rows);
    const text = [`Лист «${clean(name)}». Строк данных: ${summary.rows}, повторов строк: ${summary.duplicates}.`];
    for (const column of summary.columns.slice(0, 24)) {
      text.push(`${clean(column.name)}: пустых ${column.blanks}` +
        (column.numeric ? `, чисел ${column.numeric}, сумма ${column.total}` : ''));
    }
    const category = summary.columns.findIndex(column => !column.numeric && /категор|category|тип расхода/iu.test(column.name));
    const amount = summary.columns.findIndex(column => column.numeric && /сумм|расход|amount|итого|total/iu.test(column.name));
    if (category >= 0 && amount >= 0) {
      const groups = groupRows(rows, category, amount);
      text.push('Группировка по категориям (до 16 с наибольшей суммой):',
        ...groups.slice(0,16).map(item => `${clean(item.label)}: ${item.count} строк, сумма ${item.total}`));
    }
    text.push('Первые строки (образец, не вся таблица):',
      ...rows.slice(0, 13).map(row => row.slice(0, 24).map(clean).join('\t')));
    return text.join('\n').slice(0, Math.floor(16000 / sheets.length));
  });
  return 'Локальные итоги по листам. Пересчёт формул не выполнялся. Используй рассчитанные итоги, а для запросов вне этих данных укажи ограничение.\n\n' +
    details.join('\n\n');
}

export async function readTable(file, signal) {
  const worker = new Worker('/web/table-worker.mjs', {type: 'module'});
  const timeout = AbortSignal.timeout(20000);
  const stop = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    return await new Promise((resolve, reject) => {
      const abort = () => reject(stop.reason?.name === 'TimeoutError' ? Error('Таблица обрабатывается слишком долго.') : stop.reason);
      stop.addEventListener('abort', abort, {once: true});
      worker.onmessage = event => event.data.error ? reject(Error(event.data.error)) : resolve(event.data.sheets);
      worker.onerror = () => reject(Error('Не удалось прочитать таблицу.'));
      if (stop.aborted) abort();
      else worker.postMessage(file);
    });
  } finally { worker.terminate(); }
}

export async function workbookAttachment(file, signal) {
  if (file.type !== spreadsheetType) return file;
  const sheets = await readTable(file, signal);
  return new File([workbookContext(sheets)], file.name, {type: 'text/plain'});
}

export function validateZip(bytes, maxBytes = 32 * 1024 * 1024) {
  const view = new DataView(bytes);
  let footer = view.byteLength - 22;
  const minimum = Math.max(0, footer - 65535);
  while (footer >= minimum && view.getUint32(footer, true) !== 0x06054b50) footer--;
  if (footer < minimum) throw Error('Некорректный документ.');
  const entries = view.getUint16(footer + 10, true);
  let offset = view.getUint32(footer + 16, true);
  let total = 0;
  if (entries > 512) throw Error('В книге слишком много вложенных файлов.');
  for (let index = 0; index < entries; index++) {
    if (offset + 46 > footer || view.getUint32(offset, true) !== 0x02014b50) throw Error('Некорректный документ.');
    total += view.getUint32(offset + 24, true);
    if (total > maxBytes) throw Error('Распакованный файл превышает допустимый размер.');
    offset += 46 + view.getUint16(offset + 28, true) + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
  }
}
