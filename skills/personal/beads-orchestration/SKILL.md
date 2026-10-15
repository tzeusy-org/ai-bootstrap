---
name: beads-orchestration
description: Use for any Beads (`bd`) issue workflow in a Beads-backed repository — coordinating unattended execution of ready issues through long-lived worker lanes, implementing a single dispatched issue in a worktree, handling PR-review follow-up tasks, reconciling stale worker/PR/worktree state, or creating and decomposing backlog issues. Route to exactly one subskill per task.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-10-04"
compatibility: Requires bd (beads) CLI v1.0.4+, git, and a Beads-backed repository. Execution subskills additionally require gh (authenticated), jq, python3, and git worktree support.
---

# Beads Orchestration

Superskill router for the Beads issue-execution fleet. Five subskills live under
`subskills/`; each is a complete standard skill package (own `SKILL.md`,
`references/`, `scripts/`). Subskills are **not** installed in the global skill
catalog — discover them lazily from this package, and load **at most one**
subskill body per task (the coordinator may additionally point dispatched
workers at a second one; the workers load it themselves).

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

Resolve every bundled `scripts/` and `references/` path from the absolute
location of the `SKILL.md` the runtime loaded. A dispatched worker's cwd is its
worktree in the target repository, not this package, so a bare relative path
resolves against the wrong tree.

## Routing table

| Task intent | Subskill | Typical trigger |
|---|---|---|
| Run the unattended loop: select ready issues, claim, dispatch workers, reconcile outcomes. Owns ALL `bd` lifecycle mutations. | [subskills/beads-coordinator/SKILL.md](subskills/beads-coordinator/SKILL.md) | "while true, tackle the next issue", "keep N workers processing the backlog" |
| Implement exactly one Beads issue; requires ISSUE_ID and WORKTREE_PATH from a coordinator or operator. | [subskills/beads-worker/SKILL.md](subskills/beads-worker/SKILL.md) | Dispatched with a worker contract for one implementation bead |
| Process one `pr-review-task` bead: triage threads, report corrections to the implementation lane, attest exact-head merge readiness. | [subskills/beads-pr-reviewer-worker/SKILL.md](subskills/beads-pr-reviewer-worker/SKILL.md) | Dispatched with a `pr-review-task` bead for an open GitHub PR |
| Reconcile stale worker state, PR-review bookkeeping, and orphaned worktrees before dispatching new work. | [subskills/beads-cleanup/SKILL.md](subskills/beads-cleanup/SKILL.md) | Coordinator preflight; recovery after a crashed loop; "clean up stale beads state" |
| Create or decompose backlog issues: features, bugs, epics, grooming, vague ask → actionable beads. | [subskills/beads-writer/SKILL.md](subskills/beads-writer/SKILL.md) | "file beads for X", "break this epic down", backlog grooming |

## Routing rules

- **Coordinator vs. worker**: if the request is to *process the backlog*, route
  to the coordinator — never start implementing issues yourself. Route to a
  worker subskill only when a coordinator/operator has already supplied the
  worker contract (ISSUE_ID, WORKTREE_PATH).
- **Cleanup is a preflight, not a loop**: the coordinator invokes it before its
  first cycle; route to it directly only for explicit recovery/audit asks.
- **Writer is standalone**: backlog creation needs no worktrees and no
  coordinator; it never dispatches execution.
- **Fallback**: if the task is Beads-adjacent but none of the rows fit (e.g.
  one-off `bd` queries, project prioritization), do not load a subskill — use
  plain `bd` commands or a dedicated skill such as `th-projects`
  (project-direction subskill).

## Shared invariants (all subskills)

- During an execution run, the coordinator is the **sole mutation authority**
  for canonical lifecycle state (`bd create/update/dep/close`); dispatched
  workers and cleanup report instead of mutating. The standalone beads-writer
  may create/update/depend backlog items during explicit authoring before
  dispatch, but hands mutation authority to the coordinator once execution
  starts.
- Worktrees live at `.worktrees/parallel-agents/<id>` on branch `agent/<id>`.
- `.beads/` is Dolt-backed and gitignored — never commit it to code branches.
- **Build artifacts die with the bead.** A bead owns at most one build target,
  tied to its worktree and reused across its commits, never a fresh one per
  commit. Name any artifact directory outside the worktree `<bead-id>-…`. When
  the bead's PR merges or closes, the coordinator copies receipts (logs, exit
  codes, screenshots) to the bead's evidence and deletes the target with the
  worktree. Full disk is not an operator blocker while targets of merged or
  closed beads remain. A repo may tighten this, but "keep everything" without
  a retention rule is not a policy.
- bd 1.0.4 has no `--rig` flag: target another project with `bd -C <path> …`.
- **Spirit > spec > DAG.** Beads are the implementation DAG derived from the
  project's specs, which serve its doctrine (`th-projects`). The writer derives
  beads from approved specs, the coordinator executes the DAG in priority
  order, and a worker implements one node. None of them re-plans: a bead that
  conflicts with its spec or doctrine, or has no governing spec, is routed back
  through `th-projects` (feature-request amendment or direction), not
  reinterpreted, re-scoped, or quietly dispatched.
- **Decide, don't defer.** Engineering-judgment choices are resolved
  autonomously per [`references/decision-autonomy.md`](references/decision-autonomy.md);
  blocking on a human is reserved for its hard-gate list. Load that file before
  filing or reconciling any decision-shaped blocker.
- **Spend tokens like money; cache first.** All subskills follow
  [`references/token-efficiency.md`](references/token-efficiency.md). Lanes
  over fan-out: one long-lived session works through a chain of beads that
  share context, one at a time, rides autocompaction, and keeps its cache warm,
  instead of spawning a fresh cold worker per bead. A bead stays the unit of
  claim, PR, review, and closure. Also: project JSON through `jq`, route gate
  output to files, run targeted tests before the single full gate, and keep one
  model per lane. Load it once per session before the first `bd`/`gh` query
  loop.
- **Quality gates travel with the work.** `beads-worker` and
  `beads-pr-reviewer-worker` share
  [`references/craft-and-care-gate.md`](references/craft-and-care-gate.md)
  (the repo-defined execution-quality bar) and
  [`references/test-growth-gate.md`](references/test-growth-gate.md) (one gate
  species per behavior, a `Tests: +a ~b -c` delta in every PR). Load them at
  the subskill's first edit or first finding, not at dispatch.
- **Finish on a report, not a question.** A dispatched subskill ends only in
  its defined report (a terminal status, or the shaping writer's returned bead
  text), never mid-task on a plan, a promise, or a "shall I". It decides what
  `references/decision-autonomy.md` lets it decide; only an external or
  hard-gated blocker is reported as blocked. The coordinator ends at its
  frontier stop, an owner stop, or a tracker fault it cannot recover. Every
  report is written for a reader who has not seen the session.
- **The owner's instructions outrank skill defaults.** A direct instruction
  from the human owner or operator (not a dispatching agent), or the target
  repo's `AGENTS.md` / `CLAUDE.md`, overrides this package's defaults: lane
  count, routing, cadence, model tier. It does not lift mutation authority,
  worktree isolation, exact-head review, or the hard gates in
  `references/decision-autonomy.md`. When one of those pauses work, name the
  rule and its file in the report.

## When `bd` itself misbehaves

On any unexpected `bd` error (connection failures, unknown flags, refused
mutations, weird exit states), consult
[`references/known-errors.md`](references/known-errors.md) **before**
debugging from scratch — it catalogs known errors, deprecations, and
workarounds. Search it instead of reading it whole: run
`rg -i -n '<distinctive error text>' <this package>/references/known-errors.md`,
then read only the matching section (fall back to skimming the headers via
`rg -n '^##' …` if the search misses). If you hit a rough edge that is not
listed, append an entry after resolving it: that file is this skill's
persistent memory, and an unrecorded fix gets re-debugged by the next session.
