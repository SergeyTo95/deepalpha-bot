import test from 'node:test';
import assert from 'node:assert/strict';
import {groupCSV,groupRows,summarizeRows,workbookContext,validateZip} from '../table-tools.mjs';
import {historyDocuments} from '../history-search.mjs';
import {pdfReferences} from '../pdf-preview.mjs';
import {redactContactData} from '../document-tools.mjs';
import {diagramAllowed} from '../diagram-preview.mjs';

test('table totals and grouped totals consider all rows and export formula-like labels as text', () => {
  const rows = [['Категория','Сумма'],['Связь','3,5'],['Связь','3,5'],['=0',5]];
  assert.equal(summarizeRows(rows).duplicates,1);
  assert.deepEqual(groupRows(rows,0,1),[{label:'Связь',count:2,total:7},{label:'=0',count:1,total:5}]);
  assert.match(groupCSV(groupRows(rows,0,1)),/"'=0"/);
  assert.match(workbookContext([{name:'Лист',rows}]),/Связь: 2 строк, сумма 7/);
  assert.throws(()=>validateZip(new ArrayBuffer(8)),/Некорректный/);
});

test('local index exposes stable message coordinates and omits in-flight responses', () => {
  const results = historyDocuments([{id:'local',title:'Заметки',messages:[
    {role:'user',content:'Проверить ремонт',serverId:'saved'},
    {role:'assistant',content:'Черновой ответ',pending:true},
  ]}]);
  assert.equal(results.length,2);
  assert.equal(results[1].serverId,'saved');
  assert.equal(results[1].index,0);
});

test('PDF citations are bounded page pairs, not arbitrary links', () => {
  const ref = 'По договору [[PDF:Условия.pdf|4]] и [[PDF:Условия.pdf|4]], [[PDF:договор.pdf|000]]';
  assert.deepEqual(pdfReferences(ref),[{name:'Условия.pdf',page:4}]);
});

test('optional contact masking produces an editable text copy', () => {
  assert.deepEqual(redactContactData('a@example.com, +90 (555) 123-45-67'),{
    masked:'[почта скрыта], [телефон скрыт]',emails:1,phones:1,
  });
});

test('preview accepts diagrams with local text and excludes external click directives', () => {
  assert.equal(diagramAllowed('flowchart LR\nA --> B'),true);
  assert.equal(diagramAllowed('flowchart LR\nclick A "https://example.com"'),false);
});
