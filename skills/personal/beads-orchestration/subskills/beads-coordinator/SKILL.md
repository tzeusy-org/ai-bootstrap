---
name: beads-coordinator
description: Coordinate unattended Beads issue execution — rank ready work finish-first (epics closest to done before new ones), claim atomically, and run each bead in an isolated worktree on a long-lived, cache-warm worker lane, alone or as one of several coordinators sharing a backlog. Use for unattended issue throughput in Beads-backed repos.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-10-27"
compatibility: Requires a Beads-backed repository with `bd` v1.3+ (`parent` on list rows, `bd epic status`, `--if-assignee`), `uv`, `git`, an authenticated `gh`, `jq`, git worktree support, and network access for `gh` PR operations.
---

# Beads Coordinator

Run a coordinator loop that repeatedly pulls ready Beads work in finish-first
order and feeds it, one bead at a time, to long-lived worker **lanes** (by
default one), each bead in its own isolated branch and worktree. Several
coordinators may share one tracker; they converge on the same queue and one
global lane budget. This skill is a routing layer over the Beads operating
model in [`../../../../../README.md`](../../../../../README.md); it
coordinates, it never implements code.

This router states the non-negotiable invariants once, then points at the
reference that owns each detailed procedure. Load only the reference you need.

## Use This Skill When

- "while true, tackle the next highest-priority Beads issue"
- "work through the backlog with a worker"
- "keep N workers processing the `bd ready` backlog"
- "coordinate beads work across parallel agents"
- "run another coordinator on the same backlog"
- "finish the epics we started before opening new ones"

## Do Not Use This Skill When

- implementing a single assigned issue yourself: use `../beads-worker/SKILL.md`
- reviewing one open PR: use `../beads-pr-reviewer-worker/SKILL.md`
- only reconciling stale state before a run: use `../beads-cleanup/SKILL.md`
- authoring or decomposing backlog beads: use `../beads-writer/SKILL.md`

## Non-Negotiable Invariants

State these hold for the entire run; the references elaborate, never override.

- **Sole mutation authority.** The coordinator owns every canonical Beads
  lifecycle mutation (`bd create`, `bd update`, `bd dep add`, `bd close`).
  Workers read state, push code, and report; they never mutate Beads lifecycle.
- **Claim atomically, then mutate as assignee.** Claim with
  `bd --actor "$COORDINATOR_ID" update <id> --claim`; every `bd` command
  carries that `--actor`, since the shared git identity would make peer claims
  look like yours. Before mutating a bead, confirm you are its `assignee` and
  renew the stall heartbeat. Never mutate a bead assigned to another live
  actor; the notes heartbeat is stall detection, not mutual exclusion.
- **Finish first, one shared queue.** Select work from
  `scripts/rank_ready_work.py`: P0 first, then pinned epics, then by priority
  (an epic already holding a lane counts one level higher) the epics closest
  to completion. Every coordinator on the
  tracker reads the same queue and the same global lane budget, so a pool of N
  lanes finishes a few epics instead of half-building many. Coordinators
  register liveness in the tracker and adopt a peer's beads only when it is
  dead (`references/runtime-and-safety.md` → "Parallel Coordinators"). When
  the ranker fails, dispatch nothing that wake; never fall back to unranked
  order.
- **Closure only after merge confirmation.** A bead is closed only after the
  coordinator confirms its change reached `main` (direct fast-forward merge) or
  its PR is merged. Workers never call `bd close`.
- **Native dispatch only.** Spawn workers through the runtime's native subagent
  mechanism. Never shell out to `claude`/`codex`/`opencode`/`gemini` binaries.
- **`.beads/` is Dolt-managed.** Beads data lives in the Dolt DB, not git-tracked
  files. Never commit `.beads/` contents on a code branch; strip accidental
  `.beads/` diffs.
- **Decide, don't defer.** Decision-shaped blockers are resolved by the
  coordinator under `../../references/decision-autonomy.md`, not parked for a
  human. Only its hard-gate list may block on a human, and only in its
  escalation format.
- **Execute the DAG, never re-plan it.** The dispatch-readiness gate demands
  a governing spec. A bead that lacks one, or conflicts with it, goes to the
  shaping lane or back to `th-projects` — the coordinator does not reinterpret
  scope to keep a slot busy.
- **Lanes over fan-out.** Feed the next bead to the lane that already holds
  the relevant context by continuing that worker's session, not by spawning a
  cold worker; a lane stays with its epic until the epic has no ready work.
  Run one lane by default and open another only under the rule in
  `../../references/token-efficiency.md` → "Cache-first execution", within the
  global budget. Each bead keeps its own claim, worktree, branch, PR, report,
  and closure.
- **Stay inside the cache TTL.** While work is in flight, every wake lands
  inside the runtime's prompt-cache TTL; at the no-progress frontier, widen to
  60 minutes and stop after 3 no-op wakes (`references/runtime-and-safety.md`
  → "Orchestrator Wake Cadence").

## Read Order

| File | Read when you need to | Owns |
|------|-----------------------|------|
| [`references/coordinator-loop.md`](references/coordinator-loop.md) | run the cycle | Exact steps 0-8, PR-review priority lane, finish-first selection, worker bootstrap, report contracts, reconciliation, progress report, adaptive polling |
| [`references/runtime-and-safety.md`](references/runtime-and-safety.md) | dispatch, mutate, schedule a wake, or share the tracker with another coordinator | Model-selection tables, atomic `--claim` + stall-heartbeat model + thresholds, parallel coordinators (identity, registry, global lane budget, guarded adoption), orchestrator wake cadence (prompt-cache economics + runtime bindings), mutation safety, closure rule, runtime dispatch notes, quality gates |
| [`references/epic-coordination.md`](references/epic-coordination.md) | decompose a childless epic, start a Team Lead for a focus epic, or handle a worker's hard blocker | Epic classification, independent dispatch, Team Lead mode + prompt, blocker handling |
| [`references/commands.md`](references/commands.md) | a quick command lookup | `bd` quick reference, claim/heartbeat checklist, session-completion checklist |
| [`scripts/rank_ready_work.py`](scripts/rank_ready_work.py) | pick work in Steps 1-2 | Compact `beads-work-queue/v1` finish-first queue with a reason per row, focus and held epics, global lane capacity, and this coordinator's registration; report-only, ranking contract in its module docstring |
| [`scripts/coordinator_registry.py`](scripts/coordinator_registry.py) | mint an id, register, renew, or deregister | The only writer of this coordinator's kv registry entry (`new-id`, `register`, `deregister`), plus the pure liveness and lane-budget readers shared with `beads-cleanup` |
| [`scripts/normalize_pr_review_state.py`](scripts/normalize_pr_review_state.py) | inspect Step 0 candidates | Compact `beads-pr-review-normalization/v1` findings for blocked originals/review tasks, cooldowns, duplicates, and self-heal candidates; report-only, never a mutator |
| [`scripts/review_relation_graph.py`](scripts/review_relation_graph.py) | validate normalized review-task relations | Total pure canonical validation shared by Step 0 and cleanup; rejects malformed entries, duplicate LHS, cross-role reuse, and cycles before recommendations, without I/O |
| [`../../references/decision-autonomy.md`](../../references/decision-autonomy.md) | a worker reports a decision-shaped blocker, or blocked/human-flagged beads accumulate | Decision protocol, hard-gate list, decision record, escalation format, decision sweep |
| [`../../references/token-efficiency.md`](../../references/token-efficiency.md) | once, before the first query loop | Output projection rules, gate-log routing, targeted-test policy, model right-sizing |


Diagrams, only when you need a visual: [cycle](assets/coordinator-cycle.svg),
[PR-review lane](assets/pr-review-lane.svg); sources
[cycle](assets/coordinator-cycle.excalidraw), [lane](assets/pr-review-lane.excalidraw).

## Rig Targeting

`bd` uses the `.beads/` DB discovered from `$PWD`. To target another project,
pass `bd -C /path/to/rig …` on every command; bead-ID prefixes do not
auto-route across workspaces (`../../references/known-errors.md`).

## Dispatch Quickstart

1. Run `../beads-cleanup/SKILL.md` before entering the loop. Mandatory.
2. Mint a `COORDINATOR_ID` and register it with `scripts/coordinator_registry.py`
   (`new-id`, then `register --startup`), and pass it as `--actor` on every
   `bd` command (`references/runtime-and-safety.md` → "Parallel Coordinators").
3. Loop the steps in `references/coordinator-loop.md`: normalize the PR-review
   lane (Step 0), rank ready work with `scripts/rank_ready_work.py`, then
   atomically `--claim` and dispatch from the top of that queue.
4. Enforce the dispatch-readiness and cohesion gates before claiming work;
   malformed or overlapping beads return to planning rather than consuming a
   worker slot. A rejected bead is stamped `needs-shaping`, listed by id in the
   report, and routed to the bounded shaping lane in
   `references/coordinator-loop.md` — never silently dropped.
5. Build a **compact** dispatch prompt: the worker skill's absolute path,
   `ISSUE_ID`, `WORKTREE_PATH`, `REPO_ROOT`, and a 2-4 line summary plus
   acceptance criteria (Step 5 of `references/coordinator-loop.md` lists the
   conditional fields); never inline `bd show` JSON or the skill body. Send it
   as a **lane continuation** to an idle lane holding related context (Step
   6); spawn a new worker only to open or replace a lane.
6. Choose the worker skill by issue type:
   - default implementation issue → `../beads-worker/SKILL.md`
   - `pr-review-task` issue → `../beads-pr-reviewer-worker/SKILL.md`
   - a `focus` epic meeting the complexity heuristics → Team Lead, see
     `references/epic-coordination.md`
7. Pick the model per the assignment rules in `references/runtime-and-safety.md`.
8. Require bootstrap proof (actual process cwd equals `WORKTREE_PATH`, expected
   branch, not `REPO_ROOT`, and the worktree shares `REPO_ROOT`'s canonical
   common Git directory) before counting a worker as running.

Worker skill paths: resolve them from this package's absolute location and
put the absolute path in the dispatch prompt, because a worker's cwd is its
worktree and a relative path does not resolve there. Under Claude Code's
default install:
- `~/.claude/skills/beads-orchestration/subskills/beads-worker/SKILL.md`
- `~/.claude/skills/beads-orchestration/subskills/beads-pr-reviewer-worker/SKILL.md`
