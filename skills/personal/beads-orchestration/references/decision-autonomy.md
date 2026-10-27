# Decision Autonomy

Load this file whenever you are about to mark work blocked on a decision, file
a "needs input" blocker, or triage existing decision-shaped blocked beads.

## Default: Decide, Don't Defer

A decision is not a blocker. If a choice can be resolved by engineering
judgment, the fleet resolves it and keeps moving. Blocking on a human is
reserved for the hard-gate list below — nothing else. Deferred decisions
compound: each one parks a bead, and parked beads block dependents until a
human drains the queue.

## Core Principles

Orchestration decisions are made under four principles, in this order:

1. **Adhere to the project's pillars** (`th-projects`): doctrine, design
   contracts, and specs are the first authority. If the project's shape docs
   already answer the question, that answer wins — it is not a decision at all.
2. **Maintain engineering quality** (`th-engineering`): prefer the option with
   less tech debt, cleaner architecture, a more maintainable/readable result.
3. **Honor the ideal UX** (`th-design`): for anything with a user surface,
   prefer the option closer to the design bar the project has set.
4. **Prioritize unblocks**: prefer the option that unblocks you and other
   agents soonest. Throughput of the fleet outranks local perfection.

## Decision Protocol

1. **Gate check.** Does the choice implicate any hard gate below? If yes,
   escalate using the escalation format. If no, continue.
2. **Decide** by walking the core principles in order — project pillars, then
   engineering quality, then UX ideal, then unblock value. Within principle 2,
   apply these tie-breaks:
   1. less tech debt (no duplicated logic, no compatibility shims, no TODO trails)
   2. cleaner architecture (better module boundaries, fewer dependencies crossed)
   3. more maintainable and readable result
   4. more reversible option
   5. smaller blast radius
3. **Record** the decision (format below) and continue work immediately.

## Hard Gates — the ONLY reasons to block on a human

Human insight is needed in exactly two decision classes:

- **Core architectural or feature decisions**: choices that reshape the
  project's pillars — amending a spec or design contract, adding/dropping
  acceptance criteria, changing user-visible behavior or feature scope beyond
  the bead's stated intent.
- **Genuinely agnostic user preferences**: subjective taste calls on a
  user-facing surface that survive all four core principles — after checking
  pillars, engineering quality, UX ideal, and unblock value, the options are
  still distinguished only by the user's personal preference. (If the surface
  is internal-only, this gate never applies: pick the reversible option and
  move on.)

Plus the standing safety gates, which are about actions rather than decisions:

- Irreversible or destructive operations: data deletion, migrations without
  rollback, history rewrites, force pushes to shared branches.
- Security or auth posture, secrets, credentials.
- Spending money, new paid services, external account changes.
- Outward-facing actions: publishing, emailing, posting, releasing.
- Direct conflict with an explicit written instruction (bead text, AGENTS.md /
  CLAUDE.md, operator message).

Missing external facts (absent credentials, API down, unmerged dependency) are
ordinary blockers, not decisions — file them with a concrete, non-human
unblock condition.

## Decision Record

Workers put this in the report summary and the relevant commit message; the
coordinator appends it to bead notes:

```text
[decision] chose <A> over <B>: <one-line rationale citing a tie-break>. Reversible: yes|no.
```

The record is an audit trail, not a request for permission. A human can
override later by reopening; nothing waits for them. Write one record per real
choice between alternatives, in plain words a cold reader can follow. Routine
steps a dispatch already authorizes (implementing, testing, correcting,
committing, pushing, opening the PR) and progress receipts are not decisions:
they belong in the worker report and the PR, never as `[decision]` notes.

## Escalation Format (hard-gated decisions only)

Never file an open-ended "needs human input" bead. A hard-gated escalation
bead must contain: the concrete options (2-4), a recommendation with rationale,
the consequence of each option, and a default that takes effect if unanswered
(e.g. "default to option A when its blocker bead goes stale"). One decision per
bead. The goal is a one-word human answer.

The default must never enact the gated change itself — an unanswered
escalation defaults to the status quo (criterion kept, action not taken). A
default that performs the gated action without an answer defeats the gate.

## Coordinator Decision Sweep

On every transition into the no-progress frontier (coordinator Step 8) — and
at least once per active session — triage
decision-shaped blocked beads (blocked beads whose blocker text asks for a
choice rather than an external event, including `bd human` flags):

1. Apply the gate check. Hard-gated: retrofit the escalation format if missing,
   then leave it.
2. Not hard-gated: decide via the protocol, close the blocker bead with the
   decision record as the close reason, append the record to the original
   bead's notes, unblock it, and re-dispatch with the decision inlined in the
   worker prompt.

## Rationalizations

| Excuse | Reality |
|---|---|
| "The human might prefer the other option" | They can override via the decision record. A parked bead costs more than a reversed decision. |
| "It touches shared code, safer to ask" | Blast radius is tie-break 5, not a gate. Shared code is what the tie-breaks are for. |
| "Escalating is the conservative choice" | Escalation is not free: it blocks this bead and every dependent. Conservative means reversible, not deferred. |
| "Both options seem equally good" | Then the decision is cheap and reversible — take the tie-break winner and record it. |
| "Decide-don't-defer means I can skip the gate check for throughput" | The gate check is step 1 of the protocol, always. Autonomy applies after it passes, never instead of it. |
