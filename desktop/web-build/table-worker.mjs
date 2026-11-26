import readWorkbook from 'read-excel-file/universal';
import Papa from 'papaparse';
import {boundedRows} from './table-tools.mjs';

import {validateZip} from './table-tools.mjs';

self.onmessage = async ({data:file}) => {
  try {
    let sheets;
    if (/\.csv$/iu.test(file.name)) {
      const parsed = Papa.parse(await file.text(), {skipEmptyLines:'greedy', preview:20001});
      if (parsed.errors.some(error => error.type === 'Quotes')) throw Error('Проверь кавычки в CSV.');
      sheets = [{name:'CSV', rows:boundedRows(parsed.data)}];
    } else {
      validateZip(await file.arrayBuffer());
      const workbook = await readWorkbook(file);
      if (!workbook.length || workbook.length > 8) throw Error('Поддерживаются книги от 1 до 8 листов.');
      sheets = workbook.map(({sheet, data}) => ({name:sheet, rows:boundedRows(data)}));
      boundedRows(sheets.flatMap(({rows}) => rows));
    }
    if (!sheets.some(sheet => sheet.rows.length)) throw Error('В таблице нет строк.');
    self.postMessage({sheets});
  } catch (error) {
    self.postMessage({error:error instanceof Error ? error.message.slice(0, 200) : 'Не удалось прочитать таблицу.'});
  }
};
