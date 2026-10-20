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
3. **No external consumer, no compatibility debt.** Then the target is the
   sketch itself, and a rewrite-to-target is one finding when incremental
   removal would cost more.
4. **Reconcile before you create.** Match every finding against the
   already-known ledger (open, in-progress, recently closed beads; prior
   reports); land it as attach / supersede / reopen / close / new. `new` is
   the last resort.
5. **Convergence is done.** The report records head SHA and
   new/attached/closed counts so the next run diffs; a run on the remediated
   head filing nothing new closes the loop.

## Read Order

| File | Read when | Content |
|---|---|---|
| [`references/delta-workflow.md`](./references/delta-workflow.md) | Judging complexity against the spec | Spec statement → sketch → inventory → delta table; residue classes; finding contract; rewrite rule |
| [`references/idempotent-audit.md`](./references/idempotent-audit.md) | Filing findings as beads | Ledger, reconciliation states, audit-key, progressive ordering, convergence record |

Bead form (fields, complexity labels, work-allocation) stays beads-writer's
contract; this skill adds only dedupe and ordering.

## Trigger Sanity Check

- Should trigger: "Pretend this is greenfield: how much of this repo should exist, and what do we cut?"
- Should trigger: "Is this over-engineered for what it actually does?"
- Should trigger: "Re-run last month's audit and update the backlog — don't duplicate beads."
- Should not trigger: "This rename left an old re-export behind." (cruft-cleanup)
- Should not trigger: "Score this repo's health across security, docs, and tests." (project-review)
