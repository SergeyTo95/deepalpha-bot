import test from 'node:test';
import assert from 'node:assert/strict';
import {AGENTIX_MODES,AGENTIX_LAYERS,AGENTIX_DELIVERY} from '../agentix.mjs';

test('Agentix preview describes three non-operational integration modes',()=>{
  assert.deepEqual(AGENTIX_MODES.map(x=>x.title),['Agentix API','Agentix Embed','Agentix Agents']);
  assert.ok(AGENTIX_MODES.every(x=>x.points.length>=3));
});
test('Agentix architecture covers isolation, execution control and billing planning',()=>{
  assert.equal(AGENTIX_LAYERS.length,6);
  assert.ok(AGENTIX_LAYERS.some(x=>x[2].includes('проекты')));
  assert.ok(AGENTIX_DELIVERY.some(x=>x[2].includes('биллинг')));
});
