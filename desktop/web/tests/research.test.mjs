import test from 'node:test';
import assert from 'node:assert/strict';
import {researchReportView} from '../features.mjs';
test('research report preserves limitations and does not invent citations',()=>{
 const view=researchReportView({report:{mission:{goal:'Test'},conclusion:{summary:'Uncertain',boundary:'Abstracts only'},limitations:['No full text'],open_questions:['Replication?']}});
 assert.equal(view.summary,'Uncertain');assert.deepEqual(view.limitations,['No full text']);assert.equal(view.boundary,'Abstracts only');assert.deepEqual(view.sources,[]);
});
test('report source links reject executable URLs and embedded credentials',()=>{
 const view=researchReportView({report:{evidence:{citations:[{title:'Safe',url:'https://example.org/paper'},{url:'javascript:alert(1)'},{url:'https://secret:token@example.org/'},{url:'/relative'}]}}});
 assert.equal(view.sources[0].url,'https://example.org/paper');for(const source of view.sources.slice(1))assert.equal(source.url,null);
});
