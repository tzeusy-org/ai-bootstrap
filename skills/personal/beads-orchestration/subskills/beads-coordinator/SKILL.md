---
name: beads-coordinator
description: Coordinate unattended Beads issue execution — rank ready work finish-first (epics closest to done before new ones), claim atomically, and allocate cohesive delivery packages to persistent outcome owners, alone or as one of several coordinators sharing a backlog. Use for unattended issue throughput in Beads-backed repos.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-06"
compatibility: Requires a Beads-backed repository with `bd` v1.3+ (`parent` on list rows, `bd epic status`, `--if-assignee`), `uv`, `git`, an authenticated `gh`, `jq`, git worktree support, and network access for `gh` PR operations.
---

# Beads Coordinator

Allocate bounded outcomes to persistent implementation owners and recover their
delivery. This controller handles lifecycle writes for dispatched workers;
implementation, diagnosis and routine corrections remain with the same owner.
For a session doing its own delivery use [beads-looper](../beads-looper/SKILL.md).
For a supplied package use [beads-worker](../beads-worker/SKILL.md).

Use for “keep N workers finishing the backlog”, “finish started outcomes first”,
or “coordinate several owners on this tracker”. Backlog authoring routes to
[beads-writer](../beads-writer/SKILL.md), recovery-only to
[beads-cleanup](../beads-cleanup/SKILL.md).

## Contract and reading order

1. [Shared delivery model](../../references/delivery-model.md): units, persistent
   owners, branch tradeoffs, packets, claim journal, evidence and acceptance.
2. [Coordinator loop](references/coordinator-loop.md): allocation, review wiring,
   corrections, landing, per-member reconciliation and reports. Start here after
   the shared model; load other references only for the step that needs them.
3. [Runtime and safety](references/runtime-and-safety.md): actor/lease, capacity,
   guarded adoption, model selection and bounded waits.
4. [Epic coordination](references/epic-coordination.md): shared prerequisites and
   finite programme ownership; no extra mini-coordinator tier.
5. [Commands](references/commands.md): CLI lookup and session completion.

Claim with atomic `--claim`, use a unique actor and never mutate another live
actor's claims. Multi-bead packages use the shared helper and journal. Dispatch
native runtime sessions with absolute skill paths and concise current packet
locators. Require worker bootstrap proof before editing. Never commit Dolt-managed
`.beads/` files, run production actions without authority, or bypass a repo gate.

## Executable consumers

- [rank_ready_work.py](scripts/rank_ready_work.py) ranks finite outcomes and
  prerequisites and review-first admission. Pass the Step 0 normalization file;
  `queue` is executable order, `candidates` diagnostic only. Missing review evidence
  dispatches nothing; parked owners keep claims without consuming execution slots.
- [check_packet.py](scripts/check_packet.py) reports per bead whether the packet
  slots of Step 2 are filled (structure only, never semantic readiness).
- [coordinator_registry.py](scripts/coordinator_registry.py) is the registry writer
  and shared liveness reader; do not hand-write registry expiries.
- [normalize_pr_review_state.py](scripts/normalize_pr_review_state.py) reports
  canonical anchor/review associations and terminal-state recommendations.
- [review_relation_graph.py](scripts/review_relation_graph.py) validates relations
  without I/O; partial relation/inventory evidence permits no mutation.

Target trackers using `bd -C /absolute/repo` on every command when outside that
repo. A bead prefix does not route across databases. Search
[known errors](../../references/known-errors.md) for unexpected errors, append
confirmed new fixes there, and apply [decision autonomy](../../references/decision-autonomy.md)
when a blocker is actually an engineering choice. Use
[token efficiency](../../references/token-efficiency.md) for projected output
and reuse of evidence rather than repeated full scans.
