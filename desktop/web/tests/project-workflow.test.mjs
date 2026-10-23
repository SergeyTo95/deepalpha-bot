import test from 'node:test';
import assert from 'node:assert/strict';
import {projectWorkflowQuery, PROJECT_WORKFLOW_PREFIX} from '../features.mjs';

test('workflow handoff preserves Unicode goals in the versioned resource protocol',()=>{
  const seed=projectWorkflowQuery('https://example.com/product','Удобная запись · Türkçe · 中文');
  assert.ok(seed.startsWith(PROJECT_WORKFLOW_PREFIX));
  assert.deepEqual(JSON.parse(seed.slice(PROJECT_WORKFLOW_PREFIX.length)),{
    version:'velia-replica-v1',url:'https://example.com/product',goal:'Удобная запись · Türkçe · 中文'});
  assert.ok(seed.length<=2000);
});

test('workflow handoff rejects credentials, fragments and unbounded brief text',()=>{
  for(const [url,goal] of [['javascript:alert(1)','Goal'],['https://user:secret@example.com','Goal'],
    ['https://example.com/#private','Goal'],['https://example.com',' '],['https://example.com','x'.repeat(801)]]){
    assert.throws(()=>projectWorkflowQuery(url,goal));
  }
});
