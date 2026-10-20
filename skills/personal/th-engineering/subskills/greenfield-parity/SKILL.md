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
  last_reviewed: "2026-10-20"
---

# Greenfield Parity

A codebase's shape should be a function of its current spec, not of its
history. The test: sketch what a competent engineer would build today, from
scratch, for the current goal; the actual codebase should sit within a small,
written-down delta of that sketch. Every excess is either a constraint the
sketch missed (record it, so the next sketch includes it) or residue (remove
it). How the code got that way is not a justification.

The enforcement loop is **idempotent**: an audit run against an unchanged head
yields the same backlog, not a second copy of it; a run against the
remediated head yields nothing new. Audits that re-file known findings erode
the backlog faster than they improve the code.

## Use This Skill When

- Auditing a pre-production or personal codebase where everything is up for
  refactoring and backward compatibility has no external consumer
- A module or repo "works" but a reviewer would call it over-built, layered,
  or verbose relative to the problem
- Deciding whether a large-scale restructure or rewrite beats incremental
  cleanup
- Turning audit findings into beads when a backlog already exists and may
  already cover some of them

Example trigger phrasings: "would we build it this way from scratch", "is
this over-engineered for what it does", "evaluate the greenfield complexity
and reconcile the existing code against it", "audit this and file beads
without duplicating what's already there", "this looks like a vibe-coded
mess — what would a reviewer admire instead".

## Do Not Use This Skill For

- Leftover wrappers, aliases, fallbacks from one finished refactor —
  [cruft-cleanup](../cruft-cleanup/SKILL.md); this skill covers residue at
  architecture scale, that one finishes a single migration
- Line-level naming and function shape —
  [code-readability](../code-readability/SKILL.md)
- Import direction, cycles, third-party policy —
  [dependency-hygiene](../dependency-hygiene/SKILL.md)
- Scoring a repo across health categories, or deciding what to build next —
  `/th-projects` (project-review consumes this skill as its complexity lens;
  project-direction sequences the result)
- Production code with real downstream consumers: the sketch still applies,
  but compatibility constraints enter the delta as justified excess, not
  residue

## Core Rules

1. **Sketch before you read.** Write the greenfield sketch from the spec and
   manifests only, before reading implementation. A reviewer who reads the
   code first rationalizes it; the sketch is the independent baseline.
2. **Excess is justified or it is residue.** Each component the sketch lacks
   either traces to a constraint (then write the constraint into the spec or
   doctrine, same change) or is filed for removal with the sketch's
   component as its target state.
3. **Pre-production means no compatibility debt.** With no verified external
   consumer, the target state is the sketch itself; a rewrite-to-target is a
   legitimate single finding when incremental removal would cost more.
4. **Reconcile before you create.** Every finding is matched against the
   already-known ledger (open, in-progress, and recently closed beads; prior
   audit reports) and lands as attach / supersede / reopen / close / new —
   `new` is the last resort, not the default.
5. **Convergence is the audit's definition of done.** The report records
   head SHA and new/attached/closed counts so the next run can diff; a run
   on the remediated head that files nothing new closes the loop.

## Read Order

| File | Read when | Content |
|------|-----------|---------|
| [`references/delta-workflow.md`](./references/delta-workflow.md) | Judging complexity against the spec | Spec statement, greenfield sketch, inventory, delta table, residue classes, finding contract, rewrite-vs-incremental rule |
| [`references/idempotent-audit.md`](./references/idempotent-audit.md) | Filing or re-filing findings as beads | Already-known ledger, reconciliation states, audit-key convention, progressive bead ordering, convergence record |

Beads themselves follow `beads-orchestration`'s beads-writer (fields,
complexity labels, work-allocation); this skill only adds the dedupe and
ordering contract on top.

## Trigger Sanity Check

- Should trigger: "Pretend this is greenfield: how much of this repo should exist, and what do we cut?"
- Should trigger: "Re-run last month's audit and update the backlog — don't duplicate beads."
- Should not trigger: "This rename left an old re-export behind." (cruft-cleanup)
- Should not trigger: "Score this repo's health across security, docs, and tests." (project-review)
