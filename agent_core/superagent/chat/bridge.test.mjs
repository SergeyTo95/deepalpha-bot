import assert from 'node:assert/strict';
import { test } from 'node:test';
import { transition } from './bridge.mjs';
const scope = { jobId: 'job', userId: '1', conversationId: 'conversation', projectId: 'project', repositoryId: '42', baseBranch: 'develop' };
const start = () => transition({ action: 'start', scope, goal: 'Fix the endpoint', title: 'Fix endpoint' });
test('approved chat creates one upstream mandate and running assignment', () => {
  const result = start();
  assert.equal(result.authorized, true);
  assert.deepEqual(result.stage, { stage: 'working' });
  const ticket = result.state.tickets[0];
  assert.equal(ticket.mandate.actor.id, '1');
  assert.equal(ticket.mandate.strength, 'explicit');
  assert.deepEqual(ticket.mandate.basis.scope, scope);
  assert.equal(ticket.assignments[0].state, 'running');
  assert.equal(ticket.undelivered.length, 0);
  assert.equal(result.state.triggers[0].text, 'Fix the endpoint');
});
test('replayed start cannot authorize another external launch', () => {
  const result = start();
  const replay = transition({ action: 'start', scope, state: result.state });
  assert.equal(replay.authorized, false);
  assert.equal(replay.state.tickets.length, 1);
});
for (const field of Object.keys(scope)) test(`persisted mandate rejects changed ${field}`, () => {
  assert.throws(() => transition({ action: 'progress', scope: { ...scope, [field]: 'other' }, state: start().state }), /scope_mismatch/);
});
test('progress survives process snapshots and ready ends original ticket', () => {
  const first = start();
  const progress = transition({ action: 'progress', scope, state: JSON.parse(JSON.stringify(first.state)), summary: 'commit abc' });
  assert.equal(progress.state.tickets[0].assignments[0].report.summary, 'commit abc');
  const ready = transition({ action: 'ready', scope, state: progress.state, summary: 'Draft PR #7; CI pending' });
  assert.deepEqual(ready.stage, { stage: 'ended' });
  assert.equal(ready.state.tickets[0].ended.outcome, 'completed');
  assert.throws(() => transition({ action: 'progress', scope, state: ready.state }), /Ticket has ended/);
});
test('failure asks for human attention without authorizing a retry', () => {
  const failed = transition({ action: 'failed', scope, state: start().state, summary: 'github timeout' });
  assert.deepEqual(failed.stage, { stage: 'person', action: 'failed' });
  assert.equal(transition({ action: 'start', scope, state: failed.state }).authorized, false);
});
test('tampered persisted actor is rejected', () => {
  const state = start().state;
  state.tickets[0].mandate.actor.id = '2';
  assert.throws(() => transition({ action: 'ready', scope, state }), /mandate_mismatch/);
});
