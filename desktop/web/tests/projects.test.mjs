import test from 'node:test';
import assert from 'node:assert/strict';
import {projectBrief,PROJECT_FIELD_LIMITS} from '../features.mjs';
test('project brief includes configured context and revision without exporting hidden fields',()=>{const project={passport:{title:'Project',goal:'Goal',audience:'Readers',style:'Simple',constraints:'Bounded',token:'secret'},revision:3,access_token:'secret'};const brief=projectBrief(project);assert.match(brief,/Goal/);assert.match(brief,/Readers/);assert.match(brief,/Bounded/);assert.match(brief,/Версия: 3/);assert.ok(!brief.includes('secret'));assert.equal(PROJECT_FIELD_LIMITS.title,120);});
