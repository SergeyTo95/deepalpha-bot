import { createHash } from "node:crypto";
//#region agent_core/superagent/chat/vendor/src/model.ts
/** Stable key of a thread across the whole domain. */
const threadKey = (ref) => `${ref.source}:${ref.id}`;
/**
* Normalizes valid timestamps to `Date#toISOString` form so stored timestamps compare lexicographically in memory and SQL;
* values that do not parse as a timestamp are returned unchanged.
*/
function isoTime(value) {
	const time = Date.parse(value);
	return Number.isFinite(time) ? new Date(time).toISOString() : value;
}
/**
* The tickets whose worker decides a person's follow-up: those it reached on their own Threads that can still decide it
* and did not hand it back. A ticket can while it is live, also once a person dropped its Subscription to the Thread, and
* while it is ended, not by a person, with the Trigger waiting to resume it. None once a person sent it to Eval, or while
* an Eval holds it, which an earlier version may have queued. While a ticket owns it, the message goes to no Eval, and the
* main Session may only stop the work on it.
*/
function followupOwners(trigger, records) {
	if (!trigger.human) return [];
	const disposition = records.triggers.disposition(trigger.id);
	if (!disposition || disposition.requeued) return [];
	const returned = new Set(disposition.handbacks?.map((item) => item.ticketId));
	const reached = disposition.fanout.filter((id) => !returned.has(id) && !disposition.context?.includes(id));
	if (!reached.length || records.threads.get(threadKey(trigger.threads[0]))?.evaluating?.triggerIds.includes(trigger.id)) return [];
	return reached.filter((id) => {
		const ticket = records.tickets.get(id);
		return !!ticket && (liveTicket(ticket) || ticket.ended?.by !== "human" && ticket.inputs.some((input) => input.kind === "trigger" && input.triggerId === trigger.id && ticket.undelivered.includes(input.id)));
	});
}
/**
* Whether a Decision settled what a person's message asks for: a stop on it, taken or refused, decides only that ticket's
* work, routing it to a ticket leaves it to that ticket's worker, and an unavailable one decided nothing.
*/
const settled = (decision) => !["stop", "unavailable"].includes(decision.kind) && !decision.reason.some((reason) => [
	"stopNotExplicit",
	"stopUnknown",
	"routed"
].includes(reason.code));
/** Whether a ticket is open: it has not ended. */
const liveTicket = (ticket) => !ticket.ended;
/** How a ticket ended. */
const ticketOutcome = (ticket) => ticket.ended?.outcome;
const runningAssignment = (ticket) => ticket.assignments.find((assignment) => assignment.state === "running");
/**
* Whether a Trigger reached a ticket: it started the ticket, came to it as an Input, or is on a Thread the ticket subscribes
* to; with `context` false, not a Thread the ticket follows only for context, where its worker may read but not write.
*/
function reached(ticket, trigger, thread, context = true) {
	if (ticket.origin.triggerId === trigger.id) return true;
	const input = ticket.inputs.some((item) => item.kind === "trigger" && item.triggerId === trigger.id);
	return trigger.threads.some((ref) => {
		const current = thread(threadKey(ref));
		return current?.subscriptions.includes(ticket.id) === true && (context || !current.context?.includes(ticket.id));
	}) || input && (context || !trigger.threads.every((ref) => thread(threadKey(ref))?.context?.includes(ticket.id)));
}
const latestAssignment = (ticket) => ticket.assignments.at(-1);
/** The latest report any of a ticket's Assignments made, which is what the ticket last said. */
const latestReport = (ticket) => ticket.assignments.findLast((assignment) => assignment.report)?.report;
/**
* Until when a ticket's worker had a turn to read what reached it: its latest report, but a progress report when `progress`
* is false, or the end of its latest turn, whichever is later.
*/
function readUntil(ticket, progress = true) {
	return [(ticket.assignments.findLast((assignment) => assignment.report && (progress || assignment.report.state !== "progress"))?.report)?.at ?? "", ...ticket.assignments.map((assignment) => assignment.idleAt ?? "")].reduce((a, b) => a > b ? a : b);
}
/**
* Whether a ticket waits for a person: its latest Assignment reported blocked or failed, and no person's message reached it
* since. A later report that is neither, a person's message, or a retry, which starts a new Assignment, lifts it.
*/
function waitsForPerson(ticket) {
	const report = latestAssignment(ticket)?.report;
	if (report?.state !== "blocked" && report?.state !== "failed") return false;
	return !ticket.inputs.some((input) => input.kind === "trigger" && input.human && !input.context && input.at > report.at);
}
//#endregion
//#region agent_core/superagent/chat/vendor/src/errors.ts
/** User-facing error codes mapped to their category ('missing' or 'refused') and English message template. */
const USER_ERRORS = {
	unknownTicket: ["missing", "Unknown ticket"],
	unknownProblem: ["missing", "Unavailable Eval not found"],
	unknownTrigger: ["missing", "Unknown trigger"],
	unknownAssignment: ["missing", "Unknown assignment"],
	unknownBlock: ["missing", "Unknown settings block: {block}"],
	unknownPromptRevision: ["missing", "Unknown prompt revision {revision}"],
	unknownPromptPart: ["refused", "Unknown built-in prompt part {part}"],
	ticketEnded: ["refused", "Ticket has ended"],
	assignmentEnded: ["refused", "Assignment has ended; a newer assignment owns this ticket"],
	evalFinished: ["refused", "Eval {eval} has finished or a later eval replaced it; decide the trigger's current eval"],
	notAdmissible: ["refused", "Trigger is not admissible: {reason}"],
	notExplicit: ["refused", "An accept through orchestrator_route is for explicit requests from allowed senders; assess proactive candidates with orchestrator_eval and orchestrator_eval_decide"],
	ticketStopped: ["refused", "A person stopped this ticket; new work needs an accept through orchestrator_route"],
	mandateRevoked: ["refused", "The current policy no longer holds this ticket's mandate; new work needs an accept through orchestrator_route"],
	notRetryable: ["refused", "Only an open ticket can be retried, and only when its worker last reported failed or blocked and no person has sent a message since, or when delivering an Input to its worker failed"],
	sourceThread: ["refused", "This is the thread the ticket started on; it stays subscribed"],
	followedThread: ["refused", "This thread is the ticket's own, such as its pull request's; it stays subscribed"],
	notFollowing: ["refused", "This ticket does not follow that thread"],
	questionWithResult: ["refused", "A question for a person cannot also be a ready result or an answer; report it separately"],
	askerWithoutQuestion: ["refused", "The message that raised a question needs the question it raised"],
	notAsker: ["refused", "A question must name the person's message that raised it, which reached this ticket"],
	notAnswerable: ["refused", "An answer must name the person's question it answers, which reached this ticket"],
	ackEnded: ["refused", "Ticket ended; do not acknowledge historical requests"],
	notReached: ["refused", "Trigger does not belong to this worker"],
	handedBack: ["refused", "This ticket handed that trigger back already"],
	handedBackHere: ["refused", "The worker of this ticket handed that trigger back as beyond it: accept it as new work, attach it to another ticket, or ignore it"],
	workerDecides: ["refused", "This message reached ticket {tickets} on its thread: its worker decides whether it belongs there and hands back what does not, which then arrives as a candidate. Only a stop goes through orchestrator_route, with kind stop"],
	ackNoChannel: ["refused", "Acknowledgement requires an assigned human source message"],
	ackNotPerson: ["refused", "Use the original human trigger, not a bot, CI or worker report"],
	notReady: ["refused", "Ticket is not ready for delivery. Keep progress internal and wait for a verified ready report; do not relabel progress as an answer or send it through another channel."],
	notAttention: ["refused", "Attention requires the ticket's current attention question in human_action. Routine unfinished work stays internal."],
	notQuestion: ["refused", "An answer must target the original human question, not a worker report or automated trigger"],
	noAnswer: ["refused", "No worker answer is ready for this human trigger. Wait for the worker's report, or request one with orchestrator_ticket_request_status, which acts only on a worker that reported waiting; ordinary progress cannot be relabelled as an answer."],
	noChannel: ["refused", "No reply channel for this ticket or trigger"],
	panelOnlyEnvironment: ["refused", "The execution environment (ticket directory, prepare command, blocked commands) can only be changed in the management panel"],
	panelOnlyAccounts: ["refused", "Source fields that setup reads, such as account CLIs, can only be changed in the management panel"],
	setupComplete: ["refused", "Setup is complete"],
	modelUnavailable: ["refused", "The {block} model is not available: {error}"],
	promptTooLong: ["refused", "Prompt configuration exceeds {limit} characters"],
	promptPlaceholders: ["refused", "The “{part}” template must keep {placeholders}, which the system fills in"],
	promptPartEmpty: ["refused", "The “{part}” part cannot be empty; reset it to follow its built-in text"],
	sourceNotInstalled: ["refused", "The {source} Source kind is not installed"],
	noLabels: ["refused", "This Source has no names to refresh"],
	nothingToSync: ["refused", "The {source} Source's part of the Policy has nothing to sync from, or the Source is off or waiting for setup"]
};
/**
* Structured error thrown by Engine parts and application services when a caller request is invalid or references missing state.
* Tool handlers and HTTP routes inspect its code and missing flag to return appropriate status responses, while the web panel localizes the code.
*/
var UserError = class extends Error {
	code;
	values;
	missing;
	constructor(code, values = {}) {
		const [kind, text] = USER_ERRORS[code];
		super(text.replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match));
		this.code = code;
		this.values = values;
		this.missing = kind === "missing";
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/ids.ts
/** Stable content-derived identifiers; equal inputs always produce the same id. */
function key(...values) {
	return createHash("sha256").update(JSON.stringify(values)).digest("hex").slice(0, 32);
}
//#endregion
//#region agent_core/superagent/chat/vendor/src/part.ts
/** Shared base class and configuration for Engine domain parts. */
/**
* Base class for Intake, Evals, Tickets, Assignments, and Replies providing shared access to repositories, policy, clock, and event emission.
* Each subclass mutates its own domain aggregates and delegates cross-aggregate changes to sibling parts within the transaction opened by Engine.
*/
var EnginePart = class {
	options;
	repo;
	constructor(options) {
		this.options = options;
		this.repo = options.repo;
	}
	now() {
		return this.options.now?.() ?? (/* @__PURE__ */ new Date()).toISOString();
	}
	policy() {
		return this.options.policy();
	}
	/** The tickets whose worker decides a person's follow-up: see `followupOwners`. */
	owners(trigger) {
		return followupOwners(trigger, this.repo);
	}
	/** Refuse a main-Session routing of a follow-up a ticket's worker still decides. */
	leftToWorkers(trigger) {
		const owners = this.owners(trigger);
		if (owners.length) throw new UserError("workerDecides", { tickets: owners.join(", ") });
	}
	/**
	* Record that a ticket's worker hands back a Trigger that reached the ticket, with why, and queue it for Eval when the
	* ticket decided it and no other ticket still does. One the ticket did not decide, such as a bot's, one a person sent to
	* Eval or one on a Thread it follows for context, went to Eval without it.
	* @returns whether it was queued.
	*/
	handBack(ticketId, trigger, reason) {
		const decided = this.owners(trigger).includes(ticketId);
		const disposition = this.repo.triggers.disposition(trigger.id) ?? {
			fanout: [],
			at: this.now()
		};
		const handbacks = [...disposition.handbacks ?? [], {
			ticketId,
			reason,
			at: this.now()
		}];
		this.repo.triggers.saveDisposition(trigger.id, {
			...disposition,
			handbacks
		});
		return decided && !this.owners(trigger).length && this.queueForEval(trigger);
	}
	/** Record that a ticket's worker took a person's message up as within the ticket, by acknowledging or answering it. */
	takeUp(ticketId, triggerId) {
		const disposition = this.repo.triggers.disposition(triggerId);
		if (disposition && !disposition.acked?.includes(ticketId)) this.repo.triggers.saveDisposition(triggerId, {
			...disposition,
			acked: [...disposition.acked ?? [], ticketId]
		});
	}
	/** Queue a Trigger on its primary Thread for the next Eval, unless it waits there already or an Eval holds it. */
	queueForEval(trigger) {
		const primary = this.thread(trigger.threads[0]);
		if (primary.pending.includes(trigger.id) || primary.evaluating?.triggerIds.includes(trigger.id)) return false;
		primary.pending.push(trigger.id);
		this.repo.threads.save(primary);
		return true;
	}
	emit(type, data) {
		this.options.emit?.({
			type,
			at: this.now(),
			data
		});
	}
	require(ticketId) {
		const ticket = this.repo.tickets.get(ticketId);
		if (!ticket) throw new UserError("unknownTicket");
		return ticket;
	}
	/** Returns the stored Thread or an unsaved empty Thread aggregate. */
	thread(ref) {
		const k = threadKey(ref);
		return this.repo.threads.get(k) ?? {
			key: k,
			ref: {
				source: ref.source,
				id: ref.id
			},
			createdAt: this.now(),
			subscriptions: [],
			correlated: [],
			pending: [],
			meta: {}
		};
	}
	ticketsOnThread(thread) {
		return thread.subscriptions.flatMap((id) => {
			const ticket = this.repo.tickets.get(id);
			return ticket ? [ticket] : [];
		});
	}
	source(ticket) {
		return this.repo.triggers.get(ticket.origin.triggerId);
	}
	reaches(ticket, trigger, context = true) {
		return reached(ticket, trigger, (k) => this.repo.threads.get(k), context);
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/stage.ts
/** Ticket stage classification and retry eligibility (docs/003). */
/** Classifies a ticket's stage; the rules follow the table in docs/003 in order, where the first matching row wins. */
function ticketStage(ticket) {
	if (ticket.ended) return { stage: "ended" };
	if (ticket.delivery) return {
		stage: "person",
		action: "delivery"
	};
	if (ticket.attention) return {
		stage: "person",
		action: "answer"
	};
	const assignment = latestAssignment(ticket);
	const report = assignment?.report?.state;
	if (report === "ready") return {
		stage: "person",
		action: "review"
	};
	if ((report === "blocked" || report === "failed") && waitsForPerson(ticket)) return {
		stage: "person",
		action: report
	};
	if (!assignment || assignment.state === "pending") return {
		stage: "working",
		action: "queued"
	};
	if (report === "waiting" || assignment.state === "ended") return { stage: "idle" };
	return { stage: "working" };
}
/** True when an open ticket is stalled on a blocked/failed report without a newer human follow-up, or has a failed input delivery. */
function canRetry(ticket) {
	return !ticket.ended && (waitsForPerson(ticket) || !!ticket.delivery);
}
//#endregion
//#region agent_core/superagent/chat/vendor/src/tickets.ts
/** Tickets: their lifecycle from an accept to their end, their Subscriptions and their Inputs. */
const sameChannel = (a, b) => JSON.stringify(a) === JSON.stringify(b);
/** Whether an Input is a message no person wrote, such as CI's, a bot's, or the main Session's note. */
const automatic = (input) => input?.kind === "note" || input?.kind === "trigger" && !input.human;
/**
* Whether a ticket's worker read a Trigger Input before `until`, its latest report or turn end: its Session took the message
* into a step before then. One delivered before reads were recorded counts as read once it reached the worker by then.
*/
const readBefore = (input, until) => input.readAt === void 0 ? (input.deliveredAt && input.deliveredAt > input.at ? input.deliveredAt : input.at) <= until : !!input.readAt && input.readAt < until;
const STATUS_REQUEST = "Report the current ticket status with orchestrator_report. This is a status-only request: do not resume implementation or perform external actions.";
/**
* Manages each ticket's lifecycle: creates the ticket for an accept decision with its Mandate, Subscriptions, and first
* Assignment, binds and attaches Threads, adds Inputs, resumes, retries, restarts, and ends tickets, and drops Subscriptions.
* Coordinates with Replies for the acknowledgement an accept carries and with Executor to start, deliver to, and stop assignments.
* Intake, Evals, Assignments, and Engine delegate ticket and subscription mutations here.
*/
var Tickets = class extends EnginePart {
	replies;
	constructor(options, replies) {
		super(options);
		this.replies = replies;
	}
	/**
	* Create the ticket an accept decided, under `mandate`, with the Channels and Threads of the Eval's Triggers, its first
	* Assignment, and the acknowledgement the accept carries, and queue its launch. Its origin is the admissible Trigger that
	* gave the Eval its strength, or the Eval's last Trigger for an Eval recorded before `primary` existed.
	*/
	create(id, decisionId, mandate, evaluation, outcome, at) {
		const { triggerIds } = evaluation;
		const primary = this.repo.triggers.get(evaluation.primary ?? triggerIds.at(-1));
		const origin = {
			triggerId: primary?.id ?? triggerIds[0],
			triggerIds
		};
		let ticket = {
			id,
			title: outcome.title,
			decisionId,
			mandate,
			channels: [],
			assignments: [],
			inputs: [],
			undelivered: [],
			createdAt: at,
			updatedAt: at,
			origin
		};
		for (const triggerId of triggerIds) {
			const channel = this.repo.triggers.get(triggerId)?.channel;
			if (channel && !ticket.channels.some((item) => sameChannel(item, channel))) ticket.channels.push(channel);
		}
		ticket = this.options.prepareTicket?.(ticket, outcome) ?? ticket;
		const assignment = this.newAssignment(ticket);
		this.repo.tickets.save(ticket);
		for (const triggerId of triggerIds) for (const ref of this.repo.triggers.get(triggerId)?.threads ?? []) this.subscribe(id, ref);
		if (outcome.acknowledgement && primary) this.replies.acknowledgeAccepted(id, primary, outcome.acknowledgement);
		this.options.executor.start(this.require(id), assignment);
		this.emit("ticket.created", {
			ticketId: id,
			decisionId,
			strength: mandate.strength
		});
	}
	/**
	* Subscribes an open ticket to `thread` and records it in `ticket.follows` (`own` when opened by the ticket).
	* @throws UserError when the ticket is unknown or has ended.
	*/
	bind(ticketId, thread, own) {
		if (!liveTicket(this.require(ticketId))) throw new UserError("ticketEnded");
		this.subscribe(ticketId, thread);
		const ticket = this.require(ticketId);
		const followed = threadKey(thread);
		const follow = ticket.follows?.find((item) => item.thread === followed);
		if (follow) {
			follow.own ||= own;
			delete follow.closed;
		} else ticket.follows = [...ticket.follows ?? [], {
			thread: followed,
			own
		}];
		ticket.updatedAt = this.now();
		this.repo.tickets.save(ticket);
		return ticket;
	}
	/**
	* Record that a Thread open tickets follow closed, and end each one done with its Threads (see `followsDone`), unless its
	* work has not finished (see `unfinished`); such work ends the ticket once it reports ready.
	*/
	threadClosed(thread, outcome, at) {
		const closed = threadKey(thread);
		for (const ticket of this.repo.tickets.open()) {
			const follow = ticket.follows?.find((item) => item.thread === closed && !item.closed);
			if (!follow) continue;
			follow.closed = {
				at,
				outcome
			};
			this.repo.tickets.save(ticket);
			const done = this.followsDone(ticket);
			if (!done || this.unfinished(ticket, at)) continue;
			const resumed = Date.parse(latestAssignment(ticket)?.createdAt ?? "") > Date.parse(at);
			this.end(ticket.id, "executor", done.outcome, resumed ? this.now() : done.at);
		}
	}
	/**
	* How a ticket that follows Threads is done with them: once no Thread it opened (`own`) is still open, or, when it opened none,
	* once every one it follows closed. It ends as the latest close among those Threads did; undefined while it is not done.
	*/
	followsDone(ticket) {
		const follows = ticket.follows ?? [];
		const own = follows.filter((item) => item.own);
		const counted = own.length ? own : follows;
		if (!counted.length || counted.some((item) => !item.closed)) return void 0;
		return counted.map((item) => item.closed).sort((a, b) => a.at.localeCompare(b.at)).at(-1);
	}
	/**
	* The Trigger to attach to a ticket, once the ticket may take it.
	* @throws UserError when the trigger or ticket is unknown, a ticket's worker still decides the trigger, a person stopped the ticket, or an ended ticket's Mandate no longer revalidates.
	*/
	attachable(ticketId, triggerId) {
		const trigger = this.repo.triggers.get(triggerId);
		if (!trigger) throw new UserError("unknownTrigger");
		this.leftToWorkers(trigger);
		const current = this.require(ticketId);
		if (current.ended?.by === "human") throw new UserError("ticketStopped");
		if (!liveTicket(current) && !this.policy().revalidate(current.mandate)) throw new UserError("mandateRevoked");
		return trigger;
	}
	/**
	* Subscribe a ticket to an attached Trigger's Threads and add the Trigger as an Input, with its note. The ticket's worker
	* then decides it, as it decides a message that reached the ticket on its own Thread; a worker that does not hold the
	* message as its ticket's takes it as a new Input, which wakes an ended ticket as any attached message does.
	*/
	attach(ticketId, trigger, note) {
		for (const ref of trigger.threads) this.subscribe(ticketId, ref);
		const current = this.repo.triggers.disposition(trigger.id) ?? {
			fanout: [],
			at: this.now()
		};
		const { context, requeued, ...disposition } = current;
		const followed = context?.filter((id) => id !== ticketId);
		this.repo.triggers.saveDisposition(trigger.id, {
			...disposition,
			fanout: [.../* @__PURE__ */ new Set([...disposition.fanout, ticketId])],
			...followed?.length ? { context: followed } : {}
		});
		const ticket = this.require(ticketId);
		const id = key("input", ticketId, "trigger", trigger.id);
		const existing = ticket.inputs.find((input) => input.id === id);
		const at = this.now();
		const input = (inputId) => ({
			id: inputId,
			kind: "trigger",
			triggerId: trigger.id,
			at,
			...note ? { note } : {},
			...trigger.human ? { human: true } : {}
		});
		if (existing?.kind !== "trigger") this.addInput(ticket, input(id));
		else if (note && ticket.undelivered.includes(id)) {
			existing.note = note;
			ticket.updatedAt = at;
			this.repo.tickets.save(ticket);
		} else if (requeued || !current.fanout.includes(ticketId) || context?.includes(ticketId) || !liveTicket(ticket)) this.addInput(ticket, input(key("input", ticketId, "trigger", trigger.id, "attached", at)));
		else if (note) this.addInput(ticket, input(key("input", ticketId, "trigger", trigger.id, "note", note)));
	}
	/**
	* Ask a running worker whose latest report is `waiting` for a status report; leaves any other ticket alone.
	* @returns whether the status request input was queued.
	* @throws UserError when the ticket is unknown.
	*/
	requestStatus(ticketId) {
		const ticket = this.require(ticketId);
		if (!liveTicket(ticket) || runningAssignment(ticket)?.report?.state !== "waiting") return false;
		this.addInput(ticket, {
			id: `status:${ticketId}:${ticket.updatedAt}`,
			kind: "text",
			text: STATUS_REQUEST,
			at: this.now()
		});
		return true;
	}
	/**
	* Stops a ticket on behalf of a human, ending any active assignments.
	* @throws UserError when the ticket is unknown.
	*/
	stop(ticketId, reason) {
		this.end(ticketId, "human", "stopped", this.now(), reason);
		return this.require(ticketId);
	}
	/**
	* Ends the stalled assignment of a retryable open ticket (`failed`, `blocked`, or failed input delivery) and starts a new one.
	* @throws UserError when the ticket is unknown or not retryable.
	*/
	retry(ticketId) {
		const ticket = this.require(ticketId);
		const latest = latestAssignment(ticket);
		if (!canRetry(ticket) || !latest) throw new UserError("notRetryable");
		this.endAssignment(ticket, latest, "retried");
		this.requeueUntaken(ticket, latest);
		const assignment = this.newAssignment(ticket);
		ticket.updatedAt = this.now();
		this.repo.tickets.save(ticket);
		this.options.executor.stop(ticket, latest, "retried");
		this.options.executor.start(ticket, assignment);
		return ticket;
	}
	/**
	* Drops a ticket's Subscription to a Thread its worker found unrelated, and hands back, with `reason`, the person's messages there that the ticket decides.
	* @throws UserError when the ticket is unknown, or `thread` is an origin thread, a bound `follows` thread, or not subscribed.
	*/
	markUnrelated(ticketId, thread, reason) {
		const ticket = this.require(ticketId);
		if (this.source(ticket)?.threads.some((ref) => threadKey(ref) === thread)) throw new UserError("sourceThread");
		if (ticket.follows?.some((item) => item.thread === thread)) throw new UserError("followedThread");
		if (!this.repo.threads.get(thread)?.subscriptions.includes(ticketId)) throw new UserError("notFollowing");
		this.dropSubscriptions(thread, "executor", ticketId, reason);
		for (const trigger of this.triggersOf(ticket)) if (trigger.threads.some((ref) => threadKey(ref) === thread) && this.owners(trigger).includes(ticketId)) this.handBack(ticketId, trigger, reason);
	}
	/**
	* Tell each open ticket a Policy change bears on how it does, oldest first, so their deliveries queue in the order they
	* were accepted; the others are left alone.
	*/
	policyChanged(from, to, effects) {
		const at = this.now();
		for (const ticket of this.repo.tickets.open().sort((a, b) => a.createdAt.localeCompare(b.createdAt))) {
			const effect = effects.get(ticket.id);
			if (effect) this.addInput(ticket, {
				id: key("input", ticket.id, "policy", from, to, at),
				kind: "policy-changed",
				from,
				to,
				at,
				effect
			});
		}
	}
	/**
	* Drop a Thread's Subscriptions, every ticket's or only `ticketId`'s, and say so; false when none dropped. A ticket that
	* followed the Thread follows it no more, so its close no longer ends the ticket. Each ticket keeps deciding the messages
	* it received from the Thread.
	*/
	dropSubscriptions(thread, by, ticketId, reason) {
		const current = this.repo.threads.get(thread);
		const tickets = current?.subscriptions.filter((id) => ticketId === void 0 || id === ticketId) ?? [];
		if (!current || !tickets.length) return false;
		current.subscriptions = current.subscriptions.filter((id) => !tickets.includes(id));
		this.repo.threads.save(current);
		for (const id of tickets) {
			const ticket = this.repo.tickets.get(id);
			if (!ticket?.follows?.some((item) => item.thread === thread)) continue;
			const { follows: before, ...rest } = ticket;
			const follows = before.filter((item) => item.thread !== thread);
			this.repo.tickets.save(follows.length ? {
				...rest,
				follows
			} : rest);
		}
		for (const id of tickets) this.emit("thread.unsubscribed", {
			threadKey: current.key,
			ticketId: id,
			by,
			...reason ? { reason } : {}
		});
		return true;
	}
	/**
	* Add an Input once (idempotently) and ask the Executor to deliver it, or to launch again a pending Assignment whose launch
	* failed once no person is waited for: a Policy change alone does not launch it again.
	*/
	addInput(ticket, input) {
		if (ticket.inputs.some((existing) => existing.id === input.id)) return;
		ticket.inputs.push(input);
		ticket.updatedAt = this.now();
		const held = liveTicket(ticket) && automatic(input) && waitsForPerson(ticket);
		if (!held) ticket.undelivered.push(input.id);
		this.repo.tickets.save(ticket);
		if (!liveTicket(ticket) || held) return;
		const running = runningAssignment(ticket), latest = latestAssignment(ticket);
		if (running) this.options.executor.deliver(ticket, running);
		else if (latest?.state === "pending" && !waitsForPerson(ticket)) this.options.executor.start(ticket, latest);
	}
	/**
	* Resume an ended ticket under its unchanged Mandate when a person's message on one of its Threads waits for it: no Eval
	* and no Decision, since nothing new is authorized. One whose Mandate the current Policy no longer holds stays ended and
	* takes nothing (see `refuse`). While processing is paused the message waits, and the first pass after the pause resumes
	* the ticket under the Policy then. Reads the ticket afresh, so it is idempotent and leaves an already-resumed ticket alone.
	*/
	wake(ticketId) {
		const ticket = this.repo.tickets.get(ticketId);
		if (!ticket || liveTicket(ticket) || ticket.ended?.by === "human" || !ticket.undelivered.length) return;
		const policy = this.policy();
		if (policy.paused) return;
		if (!policy.revalidate(ticket.mandate)) {
			this.refuse(ticket);
			return;
		}
		const first = ticket.undelivered.map((id) => ticket.inputs.find((input) => input.id === id)).find((input) => input?.kind === "trigger" && input.human);
		if (first?.kind !== "trigger") return;
		const at = this.now();
		delete ticket.ended;
		ticket.updatedAt = at;
		const assignment = this.newAssignment(ticket);
		const resumed = {
			id: key("input", ticket.id, "resumed", first.triggerId),
			kind: "resumed",
			triggerId: first.triggerId,
			at
		};
		ticket.inputs.push(resumed);
		ticket.undelivered.unshift(resumed.id);
		this.repo.tickets.save(ticket);
		this.options.executor.start(ticket, assignment);
		this.emit("ticket.resumed", {
			ticketId,
			triggerId: first.triggerId
		});
	}
	/** The Triggers of a ticket's trigger Inputs, or of those of them whose Input ids are `ids`. */
	triggersOf(ticket, ids) {
		return ticket.inputs.flatMap((input) => input.kind === "trigger" && (!ids || ids.includes(input.id)) ? this.repo.triggers.get(input.triggerId) ?? [] : []);
	}
	/**
	* Whether a ticket's worker took a person's message up, by acknowledging or answering it. A message acknowledged before
	* acknowledgements were recorded on it has only the acknowledgement's message in the outbox.
	*/
	takenUp(ticket, trigger) {
		const acked = this.repo.triggers.disposition(trigger.id)?.acked;
		return acked ? acked.includes(ticket.id) : this.options.outbox.status(key("ack", trigger.id)) !== void 0;
	}
	/** The person's messages among a ticket's Inputs `ids` that it decides and its worker did not take up. */
	untaken(ticket, ids) {
		return this.triggersOf(ticket, ids).filter((trigger) => this.owners(trigger).includes(ticket.id) && !this.takenUp(ticket, trigger));
	}
	/**
	* A ticket that ended, or cannot resume, lets go of the messages its worker never received, `waiting`, and of the person's
	* messages it decided but may not have taken up, `left`: it no longer counts as having reached them, and each of `left`
	* that no other ticket decides goes to Eval, as a person's message that reached no ticket does, unless a ticket took it
	* up, a Decision settled it, or it is the message a stop ended this ticket on.
	*/
	letGo(ticketId, waiting, left) {
		for (const trigger of [...waiting, ...left]) {
			const disposition = this.repo.triggers.disposition(trigger.id);
			if (disposition?.fanout.includes(ticketId)) this.repo.triggers.saveDisposition(trigger.id, {
				...disposition,
				fanout: disposition.fanout.filter((id) => id !== ticketId)
			});
		}
		for (const trigger of left) {
			const disposition = this.repo.triggers.disposition(trigger.id);
			const decision = disposition?.decisionId ? this.repo.decisions.get(disposition.decisionId) : void 0;
			const stopped = decision?.kind === "stop" && decision.ticketId === ticketId && !disposition?.handbacks?.length;
			const decided = !!decision && (settled(decision) || stopped);
			if (!this.owners(trigger).length && !disposition?.acked?.length && !decided) this.queueForEval(trigger);
		}
	}
	/** An ended ticket that cannot resume takes none of the messages that waited for it (see `letGo`). */
	refuse(ticket) {
		const waiting = this.triggersOf(ticket, ticket.undelivered), left = this.untaken(ticket, ticket.undelivered);
		ticket.undelivered = [];
		this.repo.tickets.save(ticket);
		this.letGo(ticket.id, waiting, left);
	}
	/** Replace a ticket's running Assignment with a new one that receives `note` first, and queue it for launch. */
	restart(ticket, running, reason, note) {
		this.endAssignment(ticket, running, reason);
		this.requeueUntaken(ticket, running);
		const assignment = this.newAssignment(ticket);
		const at = this.now();
		const input = {
			id: key("input", ticket.id, "restart", assignment.id),
			kind: "text",
			text: note,
			at
		};
		ticket.inputs.push(input);
		ticket.undelivered.push(input.id);
		ticket.updatedAt = at;
		this.repo.tickets.save(ticket);
		this.options.executor.start(ticket, assignment);
	}
	/**
	* Put back, ahead of what waits, the Trigger Inputs delivered to `replaced` that no step of its worker took: a cancel may
	* have dropped them, or a step that claimed them was cut off, and the new Assignment's launch takes a copy still waiting
	* in the Session out, so its delivery is the only one. An earlier end let go of what earlier Assignments left.
	*/
	requeueUntaken(ticket, replaced) {
		const untaken = ticket.inputs.filter((input) => input.kind === "trigger" && input.readAt === null && input.deliveredTo === replaced.id && !ticket.undelivered.includes(input.id));
		ticket.undelivered.unshift(...untaken.map((input) => input.id));
	}
	/**
	* End a ticket with its outcome. `at` records when the ending fact happened, such as a PR merge, and `reason` says
	* why in words, by default the outcome. The ticket lets go (see `letGo`) of the messages its worker never received, and of
	* the person's messages it decides that its worker did not read before its latest report or turn end, the report that
	* ends the ticket included (see `readBefore`), and neither acknowledged, answered nor handed back: one may have come while
	* the worker wrote the report that ends the ticket, or after its turn ended, and either way it can no longer hand it back.
	*/
	end(ticketId, by, outcome, at = this.now(), reason) {
		const ticket = this.require(ticketId);
		if (!liveTicket(ticket)) return;
		const stopping = ticket.assignments.filter((assignment) => assignment.state !== "ended");
		const why = reason && reason !== outcome ? { reason } : {};
		const until = readUntil(ticket);
		const waiting = this.triggersOf(ticket, ticket.undelivered);
		const left = this.untaken(ticket, ticket.inputs.flatMap((input) => input.kind === "trigger" && (!readBefore(input, until) || ticket.undelivered.includes(input.id)) ? [input.id] : []));
		ticket.ended = {
			at,
			by,
			outcome,
			...why
		};
		ticket.undelivered = [];
		ticket.updatedAt = this.now();
		delete ticket.attention;
		delete ticket.delivery;
		for (const assignment of stopping) {
			assignment.state = "ended";
			assignment.endedAt = at;
		}
		this.repo.tickets.save(ticket);
		this.letGo(ticketId, waiting, left);
		this.emit("ticket.ended", {
			ticketId,
			by,
			outcome,
			...why
		});
		for (const assignment of stopping) this.options.executor.stop(ticket, assignment, reason ?? outcome);
	}
	/**
	* Whether a ticket's work has not finished at a close at `at`: its worker took up a person's message the ticket decides and
	* has not reported other than progress, or ended a turn, since its Session took that message into a step, or work resumed
	* after the close, whose Assignment started after it, has no ready report or Input arrived after that report; a Policy
	* change asks for no work.
	*/
	unfinished(ticket, at) {
		const until = readUntil(ticket, false);
		const taken = this.triggersOf(ticket, ticket.inputs.flatMap((input) => input.kind === "trigger" && !readBefore(input, until) ? [input.id] : [])).some((trigger) => this.owners(trigger).includes(ticket.id) && this.takenUp(ticket, trigger));
		const latest = latestAssignment(ticket);
		if (taken || !(Date.parse(latest?.createdAt ?? "") > Date.parse(at))) return taken;
		const report = latest?.report;
		const later = ticket.inputs.some((input) => input.kind !== "policy-changed" && Date.parse(input.at) > Date.parse(report?.at ?? ""));
		return report?.state !== "ready" || ticket.undelivered.length > 0 || later;
	}
	/** Add a pending Assignment; a failed delivery to the one before it no longer concerns the ticket. */
	newAssignment(ticket) {
		const assignment = {
			id: key("assignment", ticket.id, String(ticket.assignments.length)),
			state: "pending",
			createdAt: this.now()
		};
		ticket.assignments.push(assignment);
		delete ticket.delivery;
		return assignment;
	}
	endAssignment(ticket, assignment, reason) {
		assignment.state = "ended";
		assignment.endedAt = this.now();
		assignment.endedReason = reason;
		this.emit("assignment.ended", {
			ticketId: ticket.id,
			assignmentId: assignment.id,
			reason
		});
	}
	/**
	* Give an open ticket's Executor a fact as a note Input, such as what another ticket found; it grants nothing (see `TicketInput`).
	* @throws UserError when the ticket is unknown or has ended.
	*/
	note(ticketId, text) {
		const ticket = this.require(ticketId);
		if (!liveTicket(ticket)) throw new UserError("ticketEnded");
		const at = this.now();
		this.addInput(ticket, {
			id: key("input", ticketId, "note", at, text),
			kind: "note",
			text,
			at
		});
		return this.require(ticketId);
	}
	/**
	* Subscribe an open ticket to a Thread its Executor found related, for context: see `Thread.context`.
	* @returns false when the ticket follows the Thread already.
	* @throws UserError when the ticket is unknown or has ended.
	*/
	followForContext(ticketId, ref) {
		if (!liveTicket(this.require(ticketId))) throw new UserError("ticketEnded");
		if (this.thread(ref).subscriptions.includes(ticketId)) return false;
		this.subscribe(ticketId, ref);
		const thread = this.thread(ref);
		thread.context = [...thread.context ?? [], ticketId];
		this.repo.threads.save(thread);
		return true;
	}
	/** Subscribe a ticket to a Thread; later Triggers on it reach the ticket as Inputs, as the ticket's own once it was for context. */
	subscribe(ticketId, ref) {
		const thread = this.thread(ref);
		if (!thread.subscriptions.includes(ticketId)) {
			thread.subscriptions.push(ticketId);
			this.emit("thread.subscribed", {
				threadKey: thread.key,
				ticketId
			});
		}
		if (thread.context?.includes(ticketId)) thread.context = thread.context.filter((id) => id !== ticketId);
		this.repo.threads.save(thread);
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/assignments.ts
/** What the Executor and its workers report about the Assignments that run tickets. */
const RECOVERY_NOTE = "The host restarted during this ticket. Inspect the current worktree, git history and GitHub state before continuing. Do not repeat an already completed external operation. Finish the original assignment and report its status.";
/**
* Assignments records what the Executor and its workers report about the Assignments that run tickets: one started, Inputs
* reached it or failed to, its worker's Session took Inputs into a step, a turn of its worker ended, a host restart
* interrupted it, and a worker's report with its question for a person. It depends on
* Tickets to restart or end a ticket and on Replies to withdraw a question a report replaced; the Engine routes the
* Executor's lifecycle callbacks and its workers' reports here.
*/
var Assignments = class extends EnginePart {
	tickets;
	replies;
	constructor(options, tickets, replies) {
		super(options);
		this.tickets = tickets;
		this.replies = replies;
	}
	/** Marks a non-ended assignment running, queues delivery of waiting undelivered inputs on an open ticket, and emits `assignment.started`. */
	started(ticketId, assignmentId) {
		const ticket = this.require(ticketId);
		const assignment = ticket.assignments.find((item) => item.id === assignmentId);
		if (!assignment || assignment.state === "ended") return;
		assignment.state = "running";
		assignment.startedAt ??= this.now();
		ticket.updatedAt = this.now();
		this.repo.tickets.save(ticket);
		if (liveTicket(ticket) && ticket.undelivered.length) this.options.executor.deliver(ticket, assignment);
		this.emit("assignment.started", {
			ticketId,
			assignmentId
		});
	}
	/**
	* Marks `inputIds` delivered on the ticket's running assignment and clears any prior `delivery` failure; ignored if no
	* longer running. Its worker reads each once its Session takes it into a step (see `read`).
	*/
	delivered(ticketId, assignmentId, inputIds) {
		const ticket = this.running(ticketId, assignmentId);
		if (!ticket) return;
		ticket.undelivered = ticket.undelivered.filter((id) => !inputIds.includes(id));
		ticket.updatedAt = this.now();
		runningAssignment(ticket).deliveredAt = ticket.updatedAt;
		for (const input of ticket.inputs) if (input.kind === "trigger" && inputIds.includes(input.id)) {
			input.readAt ??= null;
			input.deliveredTo = assignmentId;
		}
		delete ticket.delivery;
		this.repo.tickets.save(ticket);
	}
	/** Records when the worker's Session of an open ticket took `inputIds` into a step, from which its model reads them. */
	read(ticketId, inputIds) {
		const ticket = this.require(ticketId);
		if (!liveTicket(ticket)) return;
		for (const input of ticket.inputs) if (input.kind === "trigger" && inputIds.includes(input.id)) input.readAt ||= this.now();
		this.repo.tickets.save(ticket);
	}
	turnEnded(ticketId, assignmentId) {
		const ticket = this.running(ticketId, assignmentId);
		if (!ticket) return;
		runningAssignment(ticket).idleAt = this.now();
		this.repo.tickets.save(ticket);
	}
	deliveryFailed(ticketId, assignmentId, error) {
		const ticket = this.running(ticketId, assignmentId);
		if (!ticket) return;
		ticket.delivery = {
			error: error.slice(0, 300),
			at: this.now()
		};
		this.repo.tickets.save(ticket);
	}
	/**
	* Handles a running assignment interrupted by a host restart: restarts it with a recovery note unless its latest report is
	* `waiting`, `ready`, `blocked`, or `failed` and no input was delivered after that report, in which case it keeps its
	* session and flushes any waiting undelivered inputs.
	*/
	interrupted(ticketId, assignmentId) {
		const ticket = this.running(ticketId, assignmentId);
		const running = ticket && runningAssignment(ticket);
		if (!ticket || !running) return;
		const report = running.report;
		const busy = !!running.deliveredAt && !!report && running.deliveredAt >= report.at;
		if (!(!!report && [
			"waiting",
			"ready",
			"blocked",
			"failed"
		].includes(report.state) && !busy)) this.tickets.restart(ticket, running, "hostRestarted", RECOVERY_NOTE);
		else if (ticket.undelivered.length) this.options.executor.deliver(ticket, running);
	}
	/**
	* Records a worker status report on an open ticket's current assignment, updating `attention`, `answers`, held automated
	* inputs, and ending the ticket when `dropped` or when `ready` and not waiting on followed threads (`followsDone`).
	* @throws UserError for an unknown or ended ticket, an ended assignment, a question combined with a ready result or answer,
	* or a question/answer referencing a non-human or unreached trigger.
	*/
	report(ticketId, report) {
		const ticket = this.require(ticketId);
		if (!liveTicket(ticket)) throw new UserError("ticketEnded");
		if (report.question && (report.state === "ready" || report.answers)) throw new UserError("questionWithResult");
		if (report.askedBy && !report.question) throw new UserError("askerWithoutQuestion");
		const person = (id, channel) => {
			const trigger = this.repo.triggers.get(id);
			return !!trigger?.human && (!channel || !!trigger.channel) && this.reaches(ticket, trigger, false);
		};
		if (report.askedBy && !person(report.askedBy, true)) throw new UserError("notAsker");
		if (report.answers && !person(report.answers, false)) throw new UserError("notAnswerable");
		const assignment = runningAssignment(ticket) ?? latestAssignment(ticket);
		if (!assignment) throw new UserError("unknownAssignment");
		if (assignment.state === "ended") throw new UserError("assignmentEnded");
		const at = this.now();
		const previous = ticket.attention;
		const ask = (question) => ({
			id: key("attention", ticketId, question, at),
			question,
			...report.askedBy ? { triggerId: report.askedBy } : {}
		});
		const asked = report.question && report.question !== previous?.question ? ask(report.question) : previous;
		const attention = report.question === null || report.state === "ready" ? void 0 : asked;
		if (attention) ticket.attention = attention;
		else delete ticket.attention;
		if (previous && previous.id !== attention?.id) this.replies.withdrawAttention(previous.id);
		assignment.report = {
			state: report.state,
			summary: report.summary,
			at
		};
		ticket.updatedAt = at;
		if (report.answers && !ticket.answers?.includes(report.answers)) ticket.answers = [...ticket.answers ?? [], report.answers];
		if (report.answers) this.takeUp(ticketId, report.answers);
		if (report.state === "blocked" || report.state === "failed") ticket.undelivered = ticket.undelivered.filter((id) => !automatic(ticket.inputs.find((input) => input.id === id)));
		this.repo.tickets.save(ticket);
		const ready = !ticket.follows?.length ? "completed" : this.tickets.followsDone(ticket)?.outcome;
		const outcome = report.state === "dropped" ? this.tickets.followsDone(ticket)?.outcome ?? "dropped" : report.state === "ready" ? ready : void 0;
		if (outcome) this.tickets.end(ticketId, "executor", outcome, at);
		return this.require(ticketId);
	}
	/** The ticket, while it is open and `assignmentId` runs it. */
	running(ticketId, assignmentId) {
		const ticket = this.require(ticketId);
		return liveTicket(ticket) && runningAssignment(ticket)?.id === assignmentId ? ticket : void 0;
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/reasons.ts
/**
* Structured reason codes and templates for decisions, filtered triggers, and ended assignments.
* Policy gates and source scopes may also emit their own literal reason codes.
*/
const REASONS = {
	said: "{text}",
	timeout: "Assessment timed out",
	policyChanged: "Policy changed during assessment",
	noAdmissible: "No admissible trigger",
	startedTicket: "Already started ticket {ticket}",
	acceptedBefore: "Trigger {trigger} was accepted before",
	routed: "Routed to ticket {ticket}",
	explicitRouted: "Explicit request from an allowed sender routed by the orchestrator",
	humanStop: "Human asked to stop",
	legacyImport: "Imported from the pre-domain-model task record",
	forwarded: "Forwarded to the worker of the Ticket already on this Thread",
	senderNotMember: "The sender is not on the Source's list",
	retried: "Retried by a person",
	hostRestarted: "dsh-superagent restarted",
	stopUnknown: "Stop target is unknown or already ended",
	stopNotExplicit: "A stop needs an explicit request from a sender on the list",
	directionUnsettled: "Implementation intent or target direction is not established, or a product/design decision is still required",
	noRuleMatched: "No enabled admission rule matched",
	ignoredFromCard: "Ignored from its card, without a rule assessment",
	observed: "Observe mode: would accept ({text})"
};
const said = (text) => ({
	code: "said",
	values: { text }
});
/** Formats reasons in English (prefixed by `triggerId` when present) for agent consumption. */
function reasonText(reasons) {
	return [reasons ?? []].flat().map((reason) => {
		const text = (REASONS[reason.code] ?? reason.code).replace(/\{(\w+)\}/g, (match, name) => reason.values?.[name] ?? match);
		return reason.triggerId ? `${reason.triggerId}: ${text}` : text;
	}).join("; ");
}
//#endregion
//#region agent_core/superagent/chat/vendor/src/evals.ts
/** Evals: the Thread-level assessments of queued Triggers, and the Decisions they end with. */
/** An outcome's reason: a code it carries, else the words its assessor or person wrote. */
const reasonOf = (reason) => [typeof reason === "string" ? said(reason) : reason];
const stronger = (a, b) => a === "explicit" || b === "explicit" ? "explicit" : "inferred";
/** How many times in a row an expired Eval queues its Triggers again before it is recorded unavailable. */
const REQUEUES = 2;
/**
* Evals runs the Evals of Threads: it batches a Thread's queued Triggers into one Eval, takes its strength, actor, and basis
* from the gate, asks the assessor, keeps its deadline through a pause, and records the Decision it ends with: accept, stop,
* ignore, or unavailable. It depends on Tickets to create the ticket an accept decides and to end the one a stop names; the
* Engine routes the main Session's decisions, people's replays, worker handbacks, and the pass's Eval work here.
*/
var Evals = class extends EnginePart {
	tickets;
	constructor(options, tickets) {
		super(options);
		this.tickets = tickets;
	}
	/**
	* Expire a Thread's Eval past its deadline (see `expire`), unless processing is paused: then the Eval notes when the
	* processing pause began, and once processing resumes its deadline moves forward by the pause's length. An Eval whose
	* candidate has not reached the assessor's Session does not expire.
	*/
	due(thread, policy) {
		const evaluation = thread.evaluating;
		if (!evaluation || policy.paused && evaluation.pausedAt) return;
		const now = this.now();
		if (policy.paused) evaluation.pausedAt = now;
		else if (evaluation.pausedAt) {
			evaluation.deadline = new Date(Date.parse(evaluation.deadline) + Date.parse(now) - Date.parse(evaluation.pausedAt)).toISOString();
			delete evaluation.pausedAt;
		} else if (evaluation.delivered && evaluation.deadline <= now) {
			this.expire(thread);
			return;
		} else return;
		this.repo.threads.save(thread);
	}
	/** An Eval's candidate reached the assessor's Session: its deadline counts from now. */
	delivered(evalId, policy) {
		const thread = this.repo.threads.active().find((item) => item.evaluating?.id === evalId);
		if (!thread?.evaluating || thread.evaluating.delivered) return;
		const now = this.now();
		thread.evaluating.delivered = now;
		thread.evaluating.deadline = new Date(Date.parse(now) + policy.evalTimeoutMs).toISOString();
		this.repo.threads.save(thread);
	}
	/**
	* An Eval past its deadline queues its Triggers again for a fresh Eval, up to `REQUEUES` times in a row, so a busy
	* assessor does not drop requests; after that it is recorded unavailable.
	*/
	expire(thread) {
		const expired = (thread.expired ?? 0) + 1;
		if (expired > REQUEUES) {
			this.finish(thread, thread.evaluating, {
				kind: "unavailable",
				reason: [{ code: "timeout" }]
			});
			return;
		}
		const { id, triggerIds } = thread.evaluating;
		delete thread.evaluating;
		thread.expired = expired;
		thread.pending = [...triggerIds, ...thread.pending.filter((item) => !triggerIds.includes(item))];
		this.repo.threads.save(thread);
		this.options.assessor.release?.(id);
	}
	/** Start one Eval of the Triggers queued on a Thread, judged by the gate, and ask the assessor for it. */
	start(threadKeyValue, policy) {
		const thread = this.repo.threads.get(threadKeyValue);
		const at = this.now();
		const delivered = (id) => {
			const trigger = this.repo.triggers.get(id);
			return !!trigger && this.owners(trigger).length > 0;
		};
		if (thread.pending.some(delivered)) {
			const followups = thread.pending.filter(delivered);
			thread.pending = thread.pending.filter((id) => !followups.includes(id));
			this.repo.threads.save(thread);
			for (const triggerId of followups) this.emit("trigger.followup", { triggerId });
			if (!thread.pending.length) return;
		}
		const accepted = this.accepted(thread.key);
		const started = thread.pending.filter((id) => accepted.has(id));
		const startedTicket = (id) => ({
			code: "startedTicket",
			triggerId: id,
			values: { ticket: accepted.get(id).ticketId }
		});
		if (started.length) this.ignore(thread, started, started.map(startedTicket), policy, at);
		const triggers = thread.pending.flatMap((id) => {
			const trigger = this.repo.triggers.get(id);
			return trigger ? [trigger] : [];
		});
		const tickets = this.ticketsOnThread(thread);
		const reasons = [];
		let strongest;
		for (const trigger of triggers) {
			const gate = policy.gate(trigger, thread, tickets);
			if (!gate.admissible) {
				reasons.push({
					code: gate.reason,
					triggerId: trigger.id
				});
				continue;
			}
			const later = gate.strength === strongest?.strength && trigger.at >= strongest.trigger.at;
			if (!strongest || stronger(strongest.strength, gate.strength) !== strongest.strength || later) strongest = {
				trigger,
				strength: gate.strength,
				basis: gate.basis
			};
		}
		if (!strongest) {
			if (thread.pending.length) this.ignore(thread, [...thread.pending], reasons.length ? reasons : [{ code: "noAdmissible" }], policy, at);
			this.repo.threads.save(thread);
			return;
		}
		const evalId = key("eval", thread.key, policy.version, at, ...thread.pending);
		const deadline = new Date(Date.parse(at) + policy.evalTimeoutMs).toISOString();
		const { strength, trigger: { actor }, basis } = strongest;
		thread.evaluating = {
			id: evalId,
			threadKey: thread.key,
			triggerIds: thread.pending,
			primary: strongest.trigger.id,
			strength,
			actor,
			basis,
			policyVersion: policy.version,
			startedAt: at,
			deadline
		};
		thread.pending = [];
		this.repo.threads.save(thread);
		for (const trigger of triggers) this.repo.triggers.saveDisposition(trigger.id, {
			...this.repo.triggers.disposition(trigger.id) ?? {
				fanout: [],
				at
			},
			evalId
		});
		this.emit("eval.started", {
			evalId,
			threadKey: thread.key,
			triggerIds: thread.evaluating.triggerIds,
			strength: strongest.strength
		});
		this.options.assessor.request(this.candidateOf(thread));
	}
	/** The candidate of the Eval that holds `triggerId`, or undefined while no Eval holds it. */
	candidate(triggerId) {
		const thread = this.holding(triggerId);
		return thread && this.candidateOf(thread);
	}
	/**
	* Decide a Trigger: the Eval that holds it, or a direct Eval of it alone when none does.
	* @throws UserError when the Trigger is unknown or not admissible, `outcome.evalId` names an Eval that has finished, a
	* ticket's worker still decides the Trigger and the outcome is not a stop, or an inferred candidate is accepted outside `auto`
	* mode or without enabled matching rules.
	*/
	decide(triggerId, outcome) {
		const trigger = this.repo.triggers.get(triggerId);
		if (!trigger) throw new UserError("unknownTrigger");
		const held = this.holding(triggerId);
		if (outcome.evalId && held?.evaluating?.id !== outcome.evalId) throw new UserError("evalFinished", { eval: outcome.evalId });
		const earlier = outcome.kind === "accept" ? this.accepted(threadKey(trigger.threads[0])).get(triggerId) : void 0;
		if (earlier) {
			const others = held?.evaluating?.triggerIds.filter((id) => id !== triggerId) ?? [];
			if (held && others.length) this.reassess(held, {
				code: "acceptedBefore",
				values: { trigger: triggerId }
			}, [triggerId]);
			else if (held) this.finish(held, held.evaluating, {
				kind: "ignore",
				reason: [{
					code: "startedTicket",
					triggerId,
					values: { ticket: earlier.ticketId }
				}]
			});
			return earlier;
		}
		if (outcome.kind !== "stop") this.leftToWorkers(trigger);
		const thread = held ?? this.thread(trigger.threads[0]);
		const evaluation = held?.evaluating ?? this.directEval(thread, trigger, outcome.kind, outcome.kind === "stop" ? outcome.ticketId : void 0);
		if (outcome.kind === "accept" && evaluation.strength !== "explicit" && !this.policy().admitsInferred(outcome.rules ?? [])) throw new UserError("notExplicit");
		return this.decideEval(thread, evaluation, outcome);
	}
	/**
	* Queue a stored Trigger for a fresh Eval on its primary Thread and emit `trigger.requeued`; returns false while an Eval holds it.
	* @throws UserError when the Trigger is unknown.
	*/
	reevaluate(triggerId) {
		if (!this.requeue(triggerId)) return false;
		this.emit("trigger.requeued", { triggerId });
		return true;
	}
	/** Queue a Trigger for a fresh Eval on its primary Thread; false while an Eval holds it. */
	requeue(triggerId) {
		const trigger = this.repo.triggers.get(triggerId);
		if (!trigger) throw new UserError("unknownTrigger");
		if (this.holding(triggerId)) return false;
		this.queueForEval(trigger);
		const disposition = this.repo.triggers.disposition(triggerId) ?? {
			fanout: [],
			at: this.now()
		};
		this.repo.triggers.saveDisposition(triggerId, {
			...disposition,
			requeued: true
		});
		return true;
	}
	/**
	* Record that a ticket's Executor hands back a Trigger that reached it as beyond the ticket, and queue it for a fresh Eval
	* when the ticket decided it and no other ticket still does (see `handBack`): the gate judges it as any message on its
	* Thread, and its candidate carries the reasons, so no requeue notice comes before it. The ticket keeps its Subscription,
	* and hands a Trigger back once.
	* @returns whether it was queued: false while another ticket decides it, and for one the ticket did not decide.
	*/
	handback(ticketId, triggerId, reason) {
		if (!this.require(ticketId).inputs.some((input) => input.kind === "trigger" && input.triggerId === triggerId)) throw new UserError("notReached");
		if (this.repo.triggers.disposition(triggerId)?.handbacks?.some((item) => item.ticketId === ticketId)) throw new UserError("handedBack");
		return this.handBack(ticketId, this.repo.triggers.get(triggerId), reason);
	}
	/**
	* Marks an `unavailable` Decision dismissed so the overview lists it no more.
	* @throws UserError when the Decision is unknown or not `unavailable`.
	*/
	dismiss(decisionId) {
		const decision = this.repo.decisions.get(decisionId);
		if (decision?.kind !== "unavailable") throw new UserError("unknownProblem");
		decision.dismissed = true;
		this.repo.decisions.save(decision);
		return decision;
	}
	/** Take a Trigger out of its Thread's queue because it was routed by hand. */
	withdraw(trigger) {
		const thread = this.repo.threads.get(threadKey(trigger.threads[0]));
		if (!thread?.pending.includes(trigger.id)) return;
		thread.pending = thread.pending.filter((id) => id !== trigger.id);
		this.repo.threads.save(thread);
	}
	/**
	* End the Eval that holds a Trigger routed to a ticket, since the ticket has it now: one that holds other Triggers too
	* queues them again for a fresh Eval without it.
	*/
	routed(triggerId, ticketId) {
		const held = this.holding(triggerId);
		const reason = {
			code: "routed",
			values: { ticket: ticketId }
		};
		if (held?.evaluating?.triggerIds.length === 1) this.finish(held, held.evaluating, {
			kind: "ignore",
			reason: [reason]
		});
		else if (held) this.reassess(held, {
			...reason,
			triggerId
		}, [triggerId]);
	}
	/** Start the Evals in progress again under the new Policy version, and say that it changed. */
	policyChanged(from, to) {
		for (const thread of this.repo.threads.active()) if (thread.evaluating) this.reassess(thread, { code: "policyChanged" });
		this.emit("policy.changed", {
			from,
			to
		});
	}
	/** End a Thread's Eval as unavailable for `reason` and queue its Triggers, except `without`, for a new Eval ahead of the others. */
	reassess(thread, reason, without = []) {
		const ids = thread.evaluating.triggerIds.filter((id) => !without.includes(id));
		const decision = this.finish(thread, thread.evaluating, {
			kind: "unavailable",
			reason: [reason]
		});
		const current = this.repo.threads.get(thread.key);
		current.pending = [...ids, ...current.pending.filter((id) => !ids.includes(id))];
		this.repo.threads.save(current);
		return decision;
	}
	/** The Thread whose Eval holds a Trigger: an Eval runs on the primary Thread of each Trigger it holds. */
	holding(triggerId) {
		const trigger = this.repo.triggers.get(triggerId);
		const thread = trigger && this.repo.threads.get(threadKey(trigger.threads[0]));
		return thread?.evaluating?.triggerIds.includes(triggerId) ? thread : void 0;
	}
	candidateOf(thread) {
		const evaluation = thread.evaluating;
		const triggers = evaluation.triggerIds.flatMap((id) => {
			const trigger = this.repo.triggers.get(id);
			return trigger ? [trigger] : [];
		});
		const { id: evalId, strength, policyVersion } = evaluation;
		return {
			evalId,
			thread,
			triggers,
			strength,
			policyVersion,
			tickets: this.ticketsOnThread(thread)
		};
	}
	/**
	* Record that the queued Triggers `ids` of a Thread start no Eval, for `reason`, and take them out of its queue. Its id
	* differs from an Eval's, so it never replaces the Decision of an earlier Eval of the same Triggers.
	*/
	ignore(thread, ids, reason, policy, at) {
		const evaluation = {
			id: key("eval", thread.key, "ignored", policy.version, at, ...ids),
			threadKey: thread.key,
			triggerIds: ids,
			strength: "inferred",
			actor: {
				source: thread.ref.source,
				id: ""
			},
			basis: { via: "followup" },
			policyVersion: policy.version,
			startedAt: at,
			deadline: at
		};
		this.record(thread, evaluation, {
			kind: "ignore",
			reason
		});
		thread.pending = thread.pending.filter((id) => !ids.includes(id));
		delete thread.expired;
	}
	/**
	* The accept Decisions of the Triggers whose primary Thread is `threadKey`, by Trigger: what each one started. An Eval and
	* its Decision run on its Triggers' primary Thread, so the Thread's Decisions, read by their index, hold them all.
	*/
	accepted(threadKey) {
		const accepted = /* @__PURE__ */ new Map();
		for (const decision of this.repo.decisions.byThread(threadKey)) {
			if (decision.kind !== "accept" || !decision.ticketId) continue;
			for (const id of decision.triggerIds) if (!accepted.has(id)) accepted.set(id, decision);
		}
		return accepted;
	}
	/**
	* A gate-only Eval of one Trigger, for an outcome decided without a queued Eval, such as on a follow-up that reached a
	* ticket. It is decided at once, beside an Eval its Thread may have in progress, and takes the Trigger out of the queue.
	* Its id names the outcome's kind, so a stop and an accept of one message at the same moment are two Decisions.
	*/
	directEval(thread, trigger, kind, ticketId) {
		const policy = this.policy();
		const gate = policy.gate(trigger, thread, this.ticketsOnThread(thread));
		if (!gate.admissible) throw new UserError("notAdmissible", { reason: gate.reason });
		const at = this.now();
		if (thread.pending.includes(trigger.id)) {
			thread.pending = thread.pending.filter((id) => id !== trigger.id);
			this.repo.threads.save(thread);
		}
		return {
			id: key("eval", thread.key, "direct", trigger.id, kind, ...ticketId ? [ticketId] : [], at),
			threadKey: thread.key,
			triggerIds: [trigger.id],
			primary: trigger.id,
			strength: gate.strength,
			actor: trigger.actor,
			basis: gate.basis,
			policyVersion: policy.version,
			startedAt: at,
			deadline: at
		};
	}
	/** Apply an outcome to an Eval of a Thread: the one in progress, or a gate-only one. */
	decideEval(thread, evaluation, outcome) {
		const audit = outcome.audit ? { audit: outcome.audit } : {};
		if (this.policy().version !== evaluation.policyVersion) {
			if (thread.evaluating?.id === evaluation.id) return this.reassess(thread, { code: "policyChanged" });
			return this.finish(thread, evaluation, {
				kind: "unavailable",
				reason: [{ code: "policyChanged" }],
				...audit
			});
		}
		const unassessed = outcome.kind === "ignore" && evaluation.strength !== "explicit" && !outcome.audit ? [{ code: "ignoredFromCard" }] : [];
		if (outcome.kind === "ignore" || outcome.kind === "unavailable") return this.finish(thread, evaluation, {
			kind: outcome.kind,
			reason: [...unassessed, ...reasonOf(outcome.reason)],
			...audit
		});
		if (outcome.kind === "stop") {
			const ticket = this.repo.tickets.get(outcome.ticketId);
			if (!ticket || !liveTicket(ticket)) return this.finish(thread, evaluation, {
				kind: "ignore",
				reason: [{ code: "stopUnknown" }],
				...audit
			});
			if (evaluation.strength !== "explicit") return this.finish(thread, evaluation, {
				kind: "ignore",
				reason: [{ code: "stopNotExplicit" }],
				...audit
			});
			const decision = this.finish(thread, evaluation, {
				kind: "stop",
				reason: reasonOf(outcome.reason),
				ticketId: ticket.id,
				...audit
			});
			this.tickets.end(ticket.id, "human", "stopped", this.now(), typeof outcome.reason === "string" ? outcome.reason : void 0);
			return decision;
		}
		return this.accept(thread, evaluation, outcome);
	}
	/** Record an accept with the Mandate the Eval gives, and have Tickets create its ticket. */
	accept(thread, evaluation, outcome) {
		const at = this.now();
		const basis = {
			...evaluation.basis,
			...outcome.rules ? { rules: outcome.rules } : {}
		};
		const { strength, policyVersion, actor } = evaluation;
		const mandate = {
			strength,
			policyVersion,
			basis,
			actor,
			scope: outcome.scope
		};
		const ticketId = key("ticket", evaluation.id);
		const audit = outcome.audit ? { audit: outcome.audit } : {};
		const decision = this.finish(thread, evaluation, {
			kind: "accept",
			reason: reasonOf(outcome.reason),
			...audit,
			mandate,
			ticketId
		});
		this.tickets.create(ticketId, decision.id, mandate, evaluation, outcome, at);
		return decision;
	}
	/** Record an Eval's Decision; the Thread's own Eval ends with it, while a gate-only one leaves the Thread's Eval going. */
	finish(thread, evaluation, outcome) {
		const decision = this.record(thread, evaluation, outcome);
		if (thread.evaluating?.id !== evaluation.id) return decision;
		delete thread.evaluating;
		delete thread.expired;
		this.repo.threads.save(thread);
		this.options.assessor.release?.(evaluation.id);
		return decision;
	}
	record(thread, evaluation, outcome) {
		const at = this.now();
		const decision = {
			id: key("decision", evaluation.id),
			evalId: evaluation.id,
			threadKey: thread.key,
			triggerIds: evaluation.triggerIds,
			policyVersion: evaluation.policyVersion,
			at,
			kind: outcome.kind,
			reason: outcome.reason,
			strength: evaluation.strength,
			...outcome.ticketId ? { ticketId: outcome.ticketId } : {},
			...outcome.mandate ? { mandate: outcome.mandate } : {},
			...outcome.audit ? { audit: outcome.audit } : {}
		};
		this.repo.decisions.save(decision);
		for (const id of evaluation.triggerIds) {
			const disposition = this.repo.triggers.disposition(id) ?? {
				fanout: [],
				at
			};
			this.repo.triggers.saveDisposition(id, {
				...disposition,
				evalId: evaluation.id,
				decisionId: decision.id
			});
		}
		this.emit("decision.recorded", {
			decisionId: decision.id,
			kind: decision.kind,
			threadKey: thread.key
		});
		return decision;
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/intake.ts
/** Trigger ingestion, thread correlation, subscription fan-out, and evaluation queuing. */
/**
* Ingests incoming triggers from Sources, deduplicating and filtering them against SourceScope before recording thread correlations.
* Fans listened triggers out to subscribed tickets via Tickets and queues for Eval, on its primary thread, every trigger no
* ticket's worker decides (see `followupOwners`).
* Engine delegates trigger reception to this part.
*/
var Intake = class extends EnginePart {
	tickets;
	constructor(options, tickets) {
		super(options);
		this.tickets = tickets;
	}
	receive(received) {
		const trigger = {
			...received,
			at: isoTime(received.at)
		};
		if (!trigger.threads.length) throw new Error("A trigger must belong to at least one thread");
		if (this.repo.triggers.get(trigger.id)) return "duplicate";
		const at = this.now();
		const filter = this.options.scope.scope(trigger);
		if (!filter.listened) {
			this.repo.triggers.save(trigger);
			this.repo.triggers.saveDisposition(trigger.id, {
				filtered: { code: filter.reason },
				fanout: [],
				at
			});
			return "filtered";
		}
		this.repo.triggers.save(trigger);
		const keys = trigger.threads.map(threadKey);
		const reached = /* @__PURE__ */ new Map();
		for (const ref of trigger.threads) {
			const thread = this.thread(ref);
			if (!thread.lastAt || trigger.at > thread.lastAt) thread.lastAt = trigger.at;
			thread.correlated = [.../* @__PURE__ */ new Set([...thread.correlated, ...keys.filter((other) => other !== thread.key)])];
			for (const ticketId of thread.subscriptions) {
				const ticket = this.repo.tickets.get(ticketId);
				const followed = !!thread.context?.includes(ticketId);
				const resumes = trigger.human && ticket?.ended?.by !== "human" && !followed;
				if (!ticket || !liveTicket(ticket) && !resumes) continue;
				reached.set(ticketId, {
					ticket,
					context: followed && (reached.get(ticketId)?.context ?? true)
				});
			}
			this.repo.threads.save(thread);
		}
		for (const [ticketId, { ticket, context: followed }] of reached) this.tickets.addInput(ticket, {
			id: key("input", ticketId, "trigger", trigger.id),
			kind: "trigger",
			triggerId: trigger.id,
			at,
			...trigger.human ? { human: true } : {},
			...followed ? { context: true } : {}
		});
		const fanout = [...reached.keys()], context = fanout.filter((id) => reached.get(id).context);
		const disposition = {
			fanout,
			...context.length ? { context } : {},
			at
		};
		this.repo.triggers.saveDisposition(trigger.id, disposition);
		if (!this.owners(trigger).length) this.queueForEval(trigger);
		for (const ticketId of fanout) this.tickets.wake(ticketId);
		this.emit("trigger.received", {
			triggerId: trigger.id,
			threads: trigger.threads.map(threadKey)
		});
		return "stored";
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/replies.ts
/** The Engine's messages to Channels, each by the rule that allows it. */
/**
* Replies writes the Engine's messages to Channels through the Outbox port: an acknowledgement, the answer to a person's
* question, a ticket's result, and its question for a person, each only when its rule allows it, under a deterministic id
* that keeps it from going out twice (idempotent). The Engine routes `reply` here; Tickets asks it for the acknowledgement
* an accept carries, and Assignments asks it to withdraw a question a report replaced.
*/
var Replies = class extends EnginePart {
	/**
	* Queue a message for the Channel its purpose names: an acknowledgement or an answer goes where that person's message was
	* written, attention where the message that raised the question was (or to the ticket's starting conversation), and a
	* result to the conversation where the ticket started (`result` is allowed only for a ticket in `ready`, `completed`,
	* `merged`, or `dropped`). Each message is queued at most once (idempotent). An acknowledgement, even one already queued,
	* records that the ticket's worker took the message up.
	* @throws UserError when the ticket or trigger is unknown or the purpose's rule refuses it, such as a result of a ticket
	* that is not ready, completed, merged, or dropped.
	*/
	reply(request) {
		return request.purpose === "acknowledgement" ? this.acknowledge(request) : this.answer(request);
	}
	/**
	* Queue the acknowledgement an accept carries where the message that started the ticket was written, if it has a Channel
	* and a person wrote it.
	*/
	acknowledgeAccepted(ticketId, trigger, text) {
		if (trigger.channel && trigger.human) this.options.outbox.send({
			id: key("ack", trigger.id),
			channel: trigger.channel,
			text,
			kind: "acknowledgement",
			ticketId
		});
	}
	/** Withdraw a ticket's question for a person from the outbox, unless it went out already. */
	withdrawAttention(attentionId) {
		this.options.outbox.withdraw(key("attention-reply", attentionId));
	}
	acknowledge(request) {
		const ticket = this.require(request.ticketId);
		if (!liveTicket(ticket)) throw new UserError("ackEnded");
		const trigger = this.repo.triggers.get(request.triggerId);
		if (!trigger) throw new UserError("unknownTrigger");
		if (!this.reaches(ticket, trigger, false)) throw new UserError("notReached");
		if (!trigger.channel) throw new UserError("ackNoChannel");
		const id = key("ack", trigger.id);
		const existing = this.options.outbox.status(id);
		if (!existing && !trigger.human) throw new UserError("ackNotPerson");
		if (!existing) this.options.outbox.send({
			id,
			channel: trigger.channel,
			text: request.text,
			kind: "acknowledgement",
			ticketId: ticket.id
		});
		this.takeUp(ticket.id, trigger.id);
		return {
			id,
			status: this.options.outbox.status(id) ?? "pending",
			reused: existing !== void 0
		};
	}
	answer(request) {
		const trigger = request.triggerId ? this.repo.triggers.get(request.triggerId) : void 0;
		if (request.triggerId && !trigger) throw new UserError("unknownTrigger");
		const ticket = request.ticketId ? this.require(request.ticketId) : trigger ? this.repo.tickets.list().find((item) => this.reaches(item, trigger)) : void 0;
		if (request.purpose === "result") {
			const outcome = ticket && ticketOutcome(ticket);
			const reported = !!ticket && liveTicket(ticket) && latestAssignment(ticket)?.report?.state === "ready";
			if (!ticket || !(outcome === "merged" || outcome === "completed" || outcome === "dropped" || reported)) throw new UserError("notReady");
		}
		const attention = request.purpose === "attention" ? ticket?.attention : void 0;
		if (request.purpose === "attention" && (!attention || attention.question !== request.question)) throw new UserError("notAttention");
		if (request.purpose === "answer" && !trigger?.human) throw new UserError("notQuestion");
		const id = attention ? key("attention-reply", attention.id) : key("reply", trigger?.id ?? ticket?.id ?? "", request.text);
		const existing = this.options.outbox.status(id);
		const ready = !ticket || !trigger || ticket.answers?.includes(trigger.id);
		if (request.purpose === "answer" && !ready && !existing) throw new UserError("noAnswer");
		const asker = attention?.triggerId ? this.repo.triggers.get(attention.triggerId) : void 0;
		const channel = request.purpose === "answer" ? trigger.channel : asker?.channel ?? (ticket ? this.sourceChannel(ticket) : void 0);
		if (!channel) throw new UserError("noChannel");
		this.options.outbox.send({
			id,
			channel,
			text: request.text,
			kind: "reply",
			...ticket ? { ticketId: ticket.id } : {}
		});
		if (request.purpose === "answer" && ticket && trigger && ticket.answers?.includes(trigger.id)) {
			ticket.answers = ticket.answers.filter((item) => item !== trigger.id);
			this.repo.tickets.save(ticket);
		}
		return {
			id,
			status: this.options.outbox.status(id) ?? "pending",
			reused: existing !== void 0
		};
	}
	/** The conversation a ticket started in: its origin Trigger's Channel, else its first Channel. */
	sourceChannel(ticket) {
		return this.source(ticket)?.channel ?? ticket.channels[0];
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/engine.ts
/** The domain Engine: the single entry point of the core, and the role interfaces its callers receive. */
/**
* Single transactional entry point for mutating Triggers, Threads, Decisions, Tickets, and Assignments.
* Coordinates Intake, Evals, Tickets, Assignments, and Replies inside synchronous repository transactions while queuing side
* effects through ports. Callers receive role-scoped `Pick` interfaces rather than the full engine.
*/
var Engine = class {
	options;
	repo;
	replies;
	tickets;
	assignments;
	evals;
	intake;
	constructor(options) {
		this.options = options;
		this.repo = options.repo;
		this.replies = new Replies(options);
		this.tickets = new Tickets(options, this.replies);
		this.assignments = new Assignments(options, this.tickets, this.replies);
		this.evals = new Evals(options, this.tickets);
		this.intake = new Intake(options, this.tickets);
	}
	/**
	* A Source's request to take in a Trigger: store it once, fan it out to the tickets that subscribe to its Threads (to an
	* ended one only a person's message, which resumes it at once unless processing is paused, and to none a person stopped),
	* and queue it for Eval on its primary Thread unless a ticket's worker decides it (see `followupOwners`).
	* Triggers that arrive while an Eval of that Thread runs wait together for the next one.
	*/
	receive(trigger) {
		return this.repo.atomic(() => this.intake.receive(trigger));
	}
	/**
	* A Source's request to make an open ticket follow one of its Threads, such as a pull request's, `own` when the ticket
	* opened it: the ticket subscribes to it, and a ready report ends it only once `followsDone` holds (see `Tickets.followsDone`).
	* @throws UserError when the ticket is unknown or has ended.
	*/
	bindThread(ticketId, thread, own) {
		return this.repo.atomic(() => this.tickets.bind(ticketId, thread, own));
	}
	/**
	* A Source's fact that a Thread closed, such as its pull request's merge, at the time it happened. An open ticket that
	* follows it ends once it is done with the Threads it follows, unless work resumed since the close has not finished, or
	* its worker took up a person's message the ticket decides after its latest report.
	*/
	threadClosed(thread, outcome, at) {
		this.repo.atomic(() => {
			this.tickets.threadClosed(thread, outcome, isoTime(at));
		});
	}
	/**
	* Requeues overdue Evals up to `REQUEUES` times before recording `unavailable` (undelivered Evals never expire), starts the
	* Evals of Threads whose queued Triggers wait, and resumes ended tickets a person's message reached while processing was
	* paused. While processing is paused, nothing expires: each Eval in progress notes when the pause began, and its deadline
	* moves forward by the pause's length once processing resumes. Each Eval and each ticket update takes one transaction.
	*/
	evaluateDue() {
		const policy = this.options.policy();
		for (const thread of this.repo.threads.active()) if (thread.evaluating) this.repo.atomic(() => {
			this.evals.due(thread, policy);
		});
		if (policy.paused) return;
		for (const ticket of this.repo.tickets.active()) if (!liveTicket(ticket) && ticket.undelivered.length) this.repo.atomic(() => {
			this.tickets.wake(ticket.id);
		});
		for (const thread of this.repo.threads.active()) if (thread.pending.length && !thread.evaluating) this.repo.atomic(() => {
			this.evals.start(thread.key, policy);
		});
	}
	/** The candidate of the Eval that holds a Trigger, as its assessor sees it; undefined while no Eval holds it. */
	candidate(triggerId) {
		return this.evals.candidate(triggerId);
	}
	/**
	* Decide a Trigger: the Eval that holds it, or a direct Eval of it alone when none does, such as a person's explicit
	* request the main Session routes. A proactive candidate is accepted only in `auto` mode with enabled rules its assessment
	* matched, and a Trigger that already started a ticket returns that ticket's Decision. An invalid stop is recorded as
	* `ignore`; one judged under an older Policy as `unavailable`.
	* @throws UserError when the Trigger is unknown or not admissible, `outcome.evalId` names an Eval that has finished, a
	* ticket's worker still decides the Trigger and the outcome is not a stop, or an inferred candidate is accepted outside `auto`
	* mode or without enabled matching rules.
	*/
	decide(triggerId, outcome) {
		return this.repo.atomic(() => this.evals.decide(triggerId, outcome));
	}
	/**
	* Attach a Trigger to a ticket that the message points at: the ticket follows the Trigger's Threads and receives it as an
	* Input with the routing note, and the Eval that holds the Trigger ends, since the ticket has it now; the other Triggers of
	* its batch wait for a fresh Eval. A note for an Input the worker already received goes out as a new Input, and so does a
	* message the ticket let go, read for context only, or had before it ended. An ended ticket resumes under its Mandate, at
	* once unless processing is paused.
	* @throws UserError for a ticket a person stopped, which withdrew its Mandate, or one whose Mandate the Policy no longer holds.
	*/
	attach(ticketId, triggerId, note) {
		return this.repo.atomic(() => {
			if (this.repo.triggers.disposition(triggerId)?.handbacks?.some((item) => item.ticketId === ticketId)) throw new UserError("handedBackHere");
			const trigger = this.tickets.attachable(ticketId, triggerId);
			this.evals.withdraw(trigger);
			this.tickets.attach(ticketId, trigger, note);
			this.evals.routed(triggerId, ticketId);
			this.tickets.wake(ticketId);
			return this.repo.tickets.get(ticketId);
		});
	}
	/**
	* Ask a running worker that waits in `waiting` state for its status; any other worker is left alone.
	* @returns whether the worker was asked.
	* @throws UserError when the ticket is unknown.
	*/
	requestStatus(ticketId) {
		return this.repo.atomic(() => this.tickets.requestStatus(ticketId));
	}
	/**
	* Queue a message for the Channel its purpose names: an acknowledgement or an answer goes where the person's message was
	* written, attention where the message that raised the question was, else, like a result, to the conversation the ticket
	* started in. A message queued before is not queued again (see `Replies.reply`).
	* @throws UserError when the ticket or trigger is unknown or the purpose's rule refuses it, such as a result of a ticket that
	* is not ready, completed, merged, or dropped.
	*/
	reply(request) {
		return this.repo.atomic(() => this.replies.reply(request));
	}
	/** The assessor delivered an Eval's candidate to its Session: the Eval's deadline counts from now. */
	candidateDelivered(evalId) {
		this.repo.atomic(() => {
			this.evals.delivered(evalId, this.options.policy());
		});
	}
	/**
	* Queue a stored Trigger for a fresh Eval, such as a person's replay; returns false while an Eval holds it.
	* @throws UserError when the trigger is unknown.
	*/
	reevaluate(triggerId) {
		return this.repo.atomic(() => this.evals.reevaluate(triggerId));
	}
	/**
	* A person dismisses the problem an unavailable Decision shows: the overview lists it no more.
	* @throws UserError for a Decision that is unknown or not unavailable.
	*/
	dismiss(decisionId) {
		return this.repo.atomic(() => this.evals.dismiss(decisionId));
	}
	/**
	* Drop every ticket's Subscription to a Thread, until a new Decision subscribes one again.
	* @returns whether a Subscription was dropped.
	*/
	unsubscribe(thread) {
		return this.repo.atomic(() => this.tickets.dropSubscriptions(thread, "person"));
	}
	/**
	* A person stops a ticket, which withdraws its Mandate: it ends and receives nothing more.
	* @throws UserError when the ticket is unknown.
	*/
	stopTicket(ticketId, reason) {
		return this.repo.atomic(() => this.tickets.stop(ticketId, reason));
	}
	/**
	* A person retries a ticket whose worker reported failed or blocked (or whose input delivery failed): the stalled
	* Assignment ends and a new one starts under the same Mandate.
	* @throws UserError when the ticket is unknown, has ended, or is not retryable.
	*/
	retryTicket(ticketId) {
		return this.repo.atomic(() => this.tickets.retry(ticketId));
	}
	/**
	* The Executor's fact that an Assignment runs; the Inputs that wait for it go out.
	* @throws UserError when the ticket is unknown.
	*/
	assignmentStarted(ticketId, assignmentId) {
		this.repo.atomic(() => {
			this.assignments.started(ticketId, assignmentId);
		});
	}
	/**
	* The Executor's fact that Inputs reached the Assignment that runs the ticket, which clears a failed delivery. A fact about
	* an Assignment that no longer runs is dropped. Its worker reads each once its Session takes it into a step (`inputsRead`).
	*/
	inputsDelivered(ticketId, assignmentId, inputIds) {
		this.repo.atomic(() => {
			this.assignments.delivered(ticketId, assignmentId, inputIds);
		});
	}
	/**
	* The Executor's fact that the worker's Session of an open ticket took the messages that carried Inputs into a step, from
	* which its model reads them: a report or the end of a turn after that counts them as read at the ticket's end.
	* @throws UserError when the ticket is unknown.
	*/
	inputsRead(ticketId, inputIds) {
		this.repo.atomic(() => {
			this.assignments.read(ticketId, inputIds);
		});
	}
	/** The Executor's fact that a turn of the worker of the Assignment that runs the ticket ended, after a report too. */
	turnEnded(ticketId, assignmentId) {
		this.repo.atomic(() => {
			this.assignments.turnEnded(ticketId, assignmentId);
		});
	}
	/** The Executor's fact that delivering Inputs to the Assignment that runs the ticket failed; the ticket shows it until a delivery succeeds. */
	deliveryFailed(ticketId, assignmentId, error) {
		this.repo.atomic(() => {
			this.assignments.deliveryFailed(ticketId, assignmentId, error);
		});
	}
	/**
	* A worker's request, through its Executor, to mark a Thread attached to its ticket unrelated, for `reason`: only that
	* ticket's Subscription drops, until a new Decision subscribes it again.
	* @throws UserError when the ticket is unknown, or for the Thread the ticket started on, one a Source bound it to (such as
	* its pull request's), or one it does not follow.
	*/
	markUnrelated(ticketId, thread, reason) {
		this.repo.atomic(() => {
			this.tickets.markUnrelated(ticketId, thread, reason);
		});
	}
	/**
	* An Executor's Subscription to a Thread it found related, for context: see `Tickets.followForContext` and `Thread.context`.
	* @returns false when the ticket already follows the Thread.
	* @throws UserError when the ticket is unknown or has ended.
	*/
	followForContext(ticketId, thread) {
		return this.repo.atomic(() => this.tickets.followForContext(ticketId, thread));
	}
	/**
	* A fact the main Session gives an open ticket's Executor: see `Tickets.note`.
	* @throws UserError when the ticket is unknown or has ended.
	*/
	note(ticketId, text) {
		return this.repo.atomic(() => this.tickets.note(ticketId, text));
	}
	/**
	* An Executor's hand-back of a message that reached its ticket but asks for more than it: see `Evals.handback`.
	* @returns whether it was queued for Eval: false while another ticket still decides it, and for one this ticket did not decide (see `Evals.handback`).
	* @throws UserError when the ticket or trigger is unknown, the trigger did not reach the ticket, or the ticket already handed it back.
	*/
	handback(ticketId, triggerId, reason) {
		return this.repo.atomic(() => this.evals.handback(ticketId, triggerId, reason));
	}
	/**
	* Record a report on an open ticket's current Assignment. Its question for a person (`attention`) is asked, kept, or
	* cleared (`null` or a `ready` report), and a replaced attention question whose reply has not been sent yet is withdrawn
	* from the outbox. A `blocked` or `failed` report holds back automated inputs until a human message arrives. A `dropped`
	* report ends the ticket, and a `ready` report ends it: `completed` without followed Threads, else with the `followsDone`
	* outcome once `followsDone` holds.
	* @throws UserError for an unknown or ended ticket, an ended assignment, a question that comes with a ready result or an
	* answer, or a question or answer that names a message that is not a person's or did not reach the ticket.
	*/
	report(ticketId, report) {
		return this.repo.atomic(() => this.assignments.report(ticketId, report));
	}
	/**
	* Handles an assignment that was running when the host stopped: restarts it with a recovery note unless its latest report is
	* `waiting`, `ready`, `blocked`, or `failed` and no Input reached its worker after that report, in which case it keeps its
	* Session and state and gets the Inputs that wait for it.
	*/
	assignmentInterrupted(ticketId, assignmentId) {
		this.repo.atomic(() => {
			this.assignments.interrupted(ticketId, assignmentId);
		});
	}
	/**
	* The Policy changed: each open ticket in `effects` is told how the change bears on it, and Evals in progress start again
	* under the new version.
	*/
	policyChanged(from, to, effects = /* @__PURE__ */ new Map()) {
		this.repo.atomic(() => {
			this.tickets.policyChanged(from, to, effects);
			this.evals.policyChanged(from, to);
		});
	}
};
//#endregion
//#region agent_core/superagent/chat/vendor/src/memory.ts
/** In-memory repository implementation for testing and ephemeral engine instances. */
const clone = (value) => structuredClone(value);
/**
* In-memory implementation of the Repositories port backed by cloned Map entries so caller mutations do not affect stored state.
* Provides snapshot-based atomic rollback and matches the ordering semantics of the SQLite store for Engine tests.
*/
var MemoryRepositories = class {
	triggerRows = /* @__PURE__ */ new Map();
	dispositions = /* @__PURE__ */ new Map();
	threadRows = /* @__PURE__ */ new Map();
	ticketRows = /* @__PURE__ */ new Map();
	decisionRows = /* @__PURE__ */ new Map();
	atomic(work) {
		const maps = [
			this.triggerRows,
			this.dispositions,
			this.threadRows,
			this.ticketRows,
			this.decisionRows
		];
		const snapshot = maps.map((map) => new Map(map));
		try {
			return work();
		} catch (error) {
			maps.forEach((map, index) => {
				map.clear();
				for (const [id, value] of snapshot[index]) map.set(id, value);
			});
			throw error;
		}
	}
	triggers = {
		get: (id) => clone(this.triggerRows.get(id)),
		save: (trigger) => {
			this.triggerRows.set(trigger.id, clone(trigger));
		},
		disposition: (id) => clone(this.dispositions.get(id)),
		saveDisposition: (id, value) => {
			this.dispositions.set(id, clone(value));
		}
	};
	threads = {
		get: (key) => clone(this.threadRows.get(key)),
		save: (thread) => {
			this.threadRows.set(thread.key, clone(thread));
		},
		list: () => [...this.threadRows.values()].map(clone),
		active: () => this.threads.list().filter((thread) => thread.pending.length > 0 || thread.evaluating !== void 0)
	};
	tickets = {
		get: (id) => clone(this.ticketRows.get(id)),
		save: (ticket) => {
			this.ticketRows.set(ticket.id, clone(ticket));
		},
		/** Newest first, matching the SQLite Store's order. */
		list: () => [...this.ticketRows.values()].reverse().map(clone),
		open: () => this.tickets.list().filter((ticket) => !ticket.ended),
		active: () => this.tickets.list().filter((ticket) => ticket.undelivered.length > 0 || ["pending", "running"].includes(latestAssignment(ticket)?.state ?? ""))
	};
	decisions = {
		get: (id) => clone(this.decisionRows.get(id)),
		save: (decision) => {
			this.decisionRows.set(decision.id, clone(decision));
		},
		byThread: (key) => [...this.decisionRows.values()].filter((decision) => decision.threadKey === key).map(clone)
	};
};
//#endregion
export { Engine, MemoryRepositories, REASONS, USER_ERRORS, UserError, canRetry, followupOwners, isoTime, key, latestAssignment, latestReport, liveTicket, reached, readUntil, reasonText, runningAssignment, said, settled, threadKey, ticketOutcome, ticketStage, waitsForPerson };
