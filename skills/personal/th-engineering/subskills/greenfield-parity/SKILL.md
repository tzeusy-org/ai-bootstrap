---
name: greenfield-parity
description: >
  Use when judging whether a codebase (or a change) carries more complexity than its
  current spec needs — "would we build it this way from scratch?", "is this
  over-engineered for what it does", "this reads vibe-coded", "reconcile the
  existing complexity against the goal" — and when an audit that files beads must be
  re-runnable without duplicating findings already in the backlog. Pre-production
  codebases where rewrite is on the table are the primary case.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-10-21"
compatibility: idempotent-audit requires the bd CLI, jq, and a Beads-backed repository; the delta workflow needs none. The closeout owner dossier uses th-projects/subskills/user-questionnaire scripts (uv, Python 3.11+) and a subagent for adversarial review.
---

# Greenfield Parity

A codebase's shape is a function of its current spec, not its history. Test:
sketch what a competent engineer would build today, from scratch, for the
current goal; actual code sits within a small, written-down delta of that
sketch. Each excess is a constraint the sketch missed (record it so the next
sketch includes it) or residue (remove it).

The enforcing audit is **idempotent**: same head → same backlog, not a second
copy; remediated head → nothing new. Audits that re-file known findings
erode the backlog faster than they improve the code.

## Use This Skill When

- Auditing a pre-production or personal codebase where everything is up for
  refactoring
- Code "works" but a reviewer would call it over-built or verbose for the
  problem
- Deciding whether a restructure or rewrite beats incremental cleanup
- Filing audit findings into a backlog that may already cover some of them

## Do Not Use This Skill For

- One finished refactor's leftover wrappers, aliases, fallbacks —
  [cruft-cleanup](../cruft-cleanup/SKILL.md); this skill is residue at
  architecture scale
- Naming and function shape — [code-readability](../code-readability/SKILL.md)
- Import direction, cycles, third-party policy —
  [dependency-hygiene](../dependency-hygiene/SKILL.md)
- Scoring repo health or deciding what to build next — `/th-projects`
  (project-review cites this skill as its complexity lens; project-direction
  sequences the result)

## Core Rules

1. **Sketch before you read.** Write the greenfield sketch from spec and
   manifests only, before reading implementation — a reviewer who reads code
   first rationalizes it. A fresh-context subagent given only spec and
   manifests is un-anchored by construction.
2. **Excess is justified or it is residue.** Justified means a named
   constraint, written into spec or doctrine in the same change; otherwise
   the finding's target state is the sketch's counterpart, often "nothing".
3. **Compatibility has one exception.** Residue stays only for a verified
   external consumer that cannot be updated in the same change, under an
   owned, dated removal plan
   ([compat-boundary](../cruft-cleanup/references/compat-boundary.md));
   it enters the delta as `justified`, time-bounded. Same-repo callers and
   private APIs never qualify. Absent the exception, the target is the
   sketch itself and a rewrite-to-target is one finding when incremental
   removal would cost more.
4. **Interfaces are where parity is enforced.** Residue behind a narrow
   interface is cheap to delete later; residue in an exported surface
   becomes tomorrow's compatibility exception. The sketch is
   interface-first — boundaries, what crosses each, deep modules behind
   small surfaces — and an interface finding outranks an internal one of
   the same class.
5. **Reconcile before you create.** A duplicate bead costs a worker a
   full session, so match every finding against the already-known ledger
   (open, in-progress, recently closed beads; prior reports) and land it as
   attach / supersede / reopen / close / new. `new` is the last resort.
6. **Convergence is done.** The report records head SHA and
   new/attached/closed counts so the next run diffs instead of restarting;
   a run on the remediated head filing nothing new closes the loop.
7. **Fix-it-now inside a change.** Applied during implementation rather
   than an audit, leaf residue inside the change's scope is removed in that
   change; only what exceeds the scope is filed (router invariant).
8. **Owner gates end in a dossier, not a bead.** What the audit cannot
   decide alone — a rewrite-to-target on an externally consumed surface,
   a trust-boundary, migration, or concurrency-model change, a `justified`
   row whose constraint amends doctrine, a compat exception needing an
   owner and removal date — becomes one
   [user-questionnaire](../../../th-projects/subskills/user-questionnaire/SKILL.md)
   packet at closeout, adversarially reviewed and served as a web form; the
   report ends with its `QUESTIONNAIRE_URL=` line
   ([idempotent-audit § Owner dossier](./references/idempotent-audit.md#owner-dossier-closeout)).
   Questionnaire items dedupe like beads: a prior packet's answered item is
   not re-asked unless its evidence changed. No owner gates → say so.

## Read Order

| File | Read when | Content |
|---|---|---|
| [`references/delta-workflow.md`](./references/delta-workflow.md) | Judging complexity against the spec | Spec statement → interface-first sketch → inventory → delta table; residue classes; compat-exception path; finding contract; rewrite rule |
| [`references/idempotent-audit.md`](./references/idempotent-audit.md) | Filing findings as beads; closing out | Ledger, reconciliation states, audit-key, progressive ordering, convergence record, owner dossier questionnaire |

[`scripts/ledger.sh`](./scripts/ledger.sh) `[repo_root] [closed_limit]`
builds the already-known ledger (open/in-progress, recently closed,
audit-keyed beads, prior packets, last convergence record); run it before
any reconciliation. Bead form (fields, complexity labels, work-allocation)
stays beads-writer's contract; this skill adds only dedupe and ordering.

## Stops

- Step order is the point: no implementation reading before the sketch is
  recorded. A sketch written after is not a baseline.
- `close` and `reopen` need file-level evidence at the audited SHA, never
  inference from the report; a bead another assignee holds `in_progress`
  gets a note, not a state change.
- A rewrite-to-target on a surface with a verified external consumer, or any
  trust-boundary, migration, or concurrency-model change, is a scope
  change: it goes to the owner through the closeout questionnaire (rule 8)
  and from there to `/th-projects` at the governing spec/allocation gate
  (`beads-orchestration/references/decision-autonomy.md`), never a bead
  filed silently.

## Trigger Sanity Check

- Should trigger: "Pretend this is greenfield: how much of this repo should exist, and what do we cut?"
- Should trigger: "Is this over-engineered for what it actually does?"
- Should trigger: "Re-run last month's audit and update the backlog — don't duplicate beads."
- Should not trigger: "This rename left an old re-export behind." (cruft-cleanup)
- Should not trigger: "Score this repo's health across security, docs, and tests." (project-review)
