import test from 'node:test';
import assert from 'node:assert/strict';
import {researchReportView,researchReportJSON} from '../features.mjs';
test('research report preserves limitations and does not invent citations',()=>{
 const view=researchReportView({report:{mission:{goal:'Test'},conclusion:{summary:'Uncertain',boundary:'Abstracts only'},limitations:['No full text'],open_questions:['Replication?']}});
 assert.equal(view.summary,'Uncertain');assert.deepEqual(view.limitations,['No full text']);assert.equal(view.boundary,'Abstracts only');assert.deepEqual(view.sources,[]);
});
test('report source links reject executable URLs and embedded credentials',()=>{
 const view=researchReportView({report:{evidence:{citations:[{title:'Safe',url:'https://example.org/paper'},{url:'javascript:alert(1)'},{url:'https://secret:token@example.org/'},{url:'/relative'}]}}});
 assert.equal(view.sources[0].url,'https://example.org/paper');for(const source of view.sources.slice(1))assert.equal(source.url,null);
});

test('export preserves the exact saved report, Unicode and evidence limitations',()=>{const report={mission:{goal:'Проверка'},limitations:['Abstract only'],evidence:{citations:[]}};assert.deepEqual(JSON.parse(researchReportJSON({report})),report);});
test('source assessments cannot introduce references absent from the report',()=>{const view=researchReportView({report:{evidence:{citations:[{source_id:'s1',title:'Paper'}],assessments:[{source_id:'s1',notes:'Limited sample'},{source_id:'invented',notes:'False'}]}}});assert.equal(view.assessments.length,1);assert.equal(view.assessments[0].source,'Paper');});
