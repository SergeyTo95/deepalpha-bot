import test from 'node:test';
import assert from 'node:assert/strict';
import {workspaceFor,suggestedChatTask,CHAT_TASKS,SECTIONS,WORKSPACE} from '../features.mjs';
test('every specialist stays reachable within one workspace group',()=>{
  for(const [key] of SECTIONS)assert.ok(workspaceFor(key),key);
  assert.equal(workspaceFor('medical'),'tools');
  assert.equal(workspaceFor('balance'),'account');
  assert.equal(workspaceFor('projects'),'materials');
  assert.equal(workspaceFor('untrusted'),null);
  assert.equal(new Set(WORKSPACE.flatMap(([, , children])=>children)).size,SECTIONS.length);
});
test('task suggestions do not invent browser actions for ordinary text',()=>{
  assert.equal(suggestedChatTask('Открой сайт https://example.com'),'browser');
  assert.equal(suggestedChatTask('Объясни медицинское заключение'),'medical');
  assert.equal(suggestedChatTask('Найди информацию о теме'),'research');
  for(const text of ['Расскажи, что такое браузер','Привет','',null,{url:'https://example.com'}])assert.equal(suggestedChatTask(text),null);
});
test('medical chat entry retains evidence limits and separate specialist screens',()=>{
  const medical=CHAT_TASKS.find(task=>task.id==='medical');
  assert.match(medical.prompt,/только данные файлов/);
  assert.match(medical.prompt,/не назначай лечение/);
  assert.equal(medical.section,'medical');
  assert.equal(CHAT_TASKS.find(task=>task.id==='research').section,'research');
  assert.ok(!CHAT_TASKS.some(task=>task.id==='studio'));
});
