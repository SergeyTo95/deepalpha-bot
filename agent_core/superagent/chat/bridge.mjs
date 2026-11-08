// Velia chat Source/Policy/Executor adapter for the pinned upstream domain core.
// This process never receives GitHub credentials, model keys, or repository files.
import { Engine, MemoryRepositories, ticketStage } from './vendor/dist/index.mjs';

export function transition(input) {
  const { action, scope, goal, title, summary = '', state } = input;
  const fields = ['jobId', 'userId', 'conversationId', 'projectId', 'repositoryId', 'baseBranch'];
  if (!scope || fields.some(k => typeof scope[k] !== 'string' || !scope[k])) throw Error('invalid_scope');
  const same = value => fields.every(k => value?.[k] === scope[k]);
  if (state && (!same(state.scope) || state.version !== 1)) throw Error('scope_mismatch');
  if (!['start', 'progress', 'ready', 'failed'].includes(action)) throw Error('invalid_action');
  if (action !== 'start' && !state) throw Error('state_missing');
  const repo = new MemoryRepositories();
  for (const item of state?.triggers ?? []) repo.triggers.save(item);
  for (const [id, item] of state?.dispositions ?? []) repo.triggers.saveDisposition(id, item);
  for (const item of state?.threads ?? []) repo.threads.save(item);
  for (const item of state?.tickets ?? []) repo.tickets.save(item);
  for (const item of state?.decisions ?? []) repo.decisions.save(item);
  for (const item of repo.tickets.list()) {
    if (item.mandate.actor.id !== scope.userId || !same(item.mandate.basis.scope)) throw Error('mandate_mismatch');
  }
  const candidates = [], launches = [], deliveries = [], events = [];
  const source = 'velia-chat';
  const policy = {
    version: 'velia-chat-v1', paused: false, evalTimeoutMs: 10000,
    gate: trigger => trigger.human && trigger.addressed === 'direct' && trigger.actor.id === scope.userId && same(trigger.meta)
      ? { admissible: true, strength: 'explicit', basis: { via: 'addressed', addressed: 'direct', source, sender: scope.userId, scope } }
      : { admissible: false, reason: 'unauthorized_chat' },
    revalidate: mandate => mandate.actor.source === source && mandate.actor.id === scope.userId && same(mandate.basis.scope),
    admitsInferred: () => false,
  };
  const engine = new Engine({ repo, policy: () => policy,
    scope: { scope: value => value.source === source && same(value.meta)
      ? { listened: true, observed: false } : { listened: false, reason: 'scope_mismatch' } },
    assessor: { request: candidate => candidates.push(candidate) },
    executor: { start: (ticket, assignment) => launches.push([ticket, assignment]),
      deliver: (ticket, assignment) => deliveries.push([ticket, assignment]), stop() {} },
    outbox: { send() {}, status() { return undefined; }, withdraw() {} },
    emit: event => events.push(event),
  });
  const triggerId = `velia:${scope.jobId}`;
  if (action === 'start' && !state) {
    if (typeof goal !== 'string' || !goal.trim() || goal.length > 12000) throw Error('invalid_goal');
    engine.receive({ id: triggerId, source, threads: [{ source, id: `${scope.userId}/${scope.conversationId}` }],
      actor: { source, id: scope.userId }, human: true, addressed: 'direct',
      at: new Date().toISOString(), text: goal, meta: scope });
    engine.evaluateDue();
    const candidate = candidates[0];
    if (!candidate) throw Error('candidate_missing');
    engine.decide(triggerId, { kind: 'accept', evalId: candidate.evalId,
      title: String(title || goal).slice(0, 200), scope: `${scope.projectId}:${scope.baseBranch}`, reason: 'explicit_chat_approval' });
    for (const [ticket, assignment] of launches) engine.assignmentStarted(ticket.id, assignment.id);
    for (const [ticket, assignment] of deliveries) {
      engine.inputsDelivered(ticket.id, assignment.id, ticket.undelivered);
      engine.inputsRead(ticket.id, ticket.undelivered);
    }
  }
  const tickets = repo.tickets.list();
  if (tickets.length !== 1) throw Error('ticket_missing');
  const ticketId = tickets[0].id;
  if (action !== 'start') engine.report(ticketId, { state: action, summary: String(summary).slice(0, 2000) });
  const ticket = repo.tickets.get(ticketId);
  const threads = repo.threads.list();
  return { state: { version: 1, scope, triggers: [repo.triggers.get(triggerId)],
    dispositions: [[triggerId, repo.triggers.disposition(triggerId)]], threads,
    tickets: repo.tickets.list(), decisions: threads.flatMap(t => repo.decisions.byThread(t.key)) },
    ticketId, stage: ticketStage(ticket), events,
    authorized: action === 'start' && !state && launches.length === 1 && !ticket.ended };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    let raw = '';
    for await (const chunk of process.stdin) {
      raw += chunk;
      if (Buffer.byteLength(raw) > 1000000) throw Error('input_too_large');
    }
    process.stdout.write(JSON.stringify(transition(JSON.parse(raw))));
  } catch {
    process.stderr.write('superagent_transition_failed\n');
    process.exitCode = 1;
  }
}
