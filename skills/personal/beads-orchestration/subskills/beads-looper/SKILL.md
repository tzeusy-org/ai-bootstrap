---
name: beads-looper
description: Use when a persistent session should own a bounded Beads outcome end to end, implementing cohesive packages, testing, diagnosing failures, correcting review findings and landing or explicitly handing off. Also use for an independent review session serving those owners, including self-paced Claude /loop or Codex continuation without a central coordinator.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-06"
compatibility: Requires a Beads-backed repository with `bd` v1.3+ (`--claim`, `--if-assignee`), `uv`, `git` with worktree support, an authenticated `gh`, `jq`, and `python3`, plus a native subagent mechanism for the verifier and a self-paced repeat primitive such as Claude Code's `/loop`.
---

# Beads Looper

Persistent outcome ownership using the existing actor registry, claims, ranker and
worker/reviewer mechanisms. Default implementation path for a bounded outcome;
several disjoint owners can share a tracker. A small coordinator is optional for
allocation/recovery and dispatched-worker lifecycle writes.

## Use when

“Own this epic until its accepted outcome is complete”, “run this pane as a
Beads lane”, “/loop through this prerequisite chain”, or “be the independent
reviewer for these outcome owners”. A supplied dispatched package uses
[beads-worker](../beads-worker/SKILL.md); backlog authoring uses
[beads-writer](../beads-writer/SKILL.md).

## Modes and contract

Read [shared delivery model](../../references/delivery-model.md) first. Tracking
beads differ from packages and outcomes; keep per-member acceptance. Normal work
lands bounded packages frequently to main. An integration branch is optional,
policy-dependent, and does not change who owns the session or independent review.

| Mode | Responsibility | Read next |
|---|---|---|
| implement (default) | Retain one outcome through packages, implementation, tests, diagnosis, corrections and landing/handoff | [implement cycle](references/implement-cycle.md) |
| review | Independently review one package in its own worktree; send findings directly to the existing owner and reuse the same record through rechecks | [review cycle](references/review-cycle.md) |

The independent reviewer can be another pane or a native subagent. A permanently
separate review looper is optional, not a prerequisite for delivery. Never review
content you authored. [Verifier guidance](references/adversarial-verifier.md)
explains early review without another mandatory review cycle.

## Launch and continuation

Resolve `REPO_ROOT` from the shared Git common directory; do not use a nested
submodule or arbitrary worker's `--show-toplevel` as the tracker root. Read target
AGENTS/CLAUDE and preserve dirt before changing state. Mint `LOOPER_ID` through
[coordinator_registry.py](../beads-coordinator/scripts/coordinator_registry.py)
`new-id`; register `--startup` once, renew on work/wake and deregister on stop.
Pass `--actor "$LOOPER_ID"` on lifecycle writes; do not share an actor with another
live session. Use existing `beads.max_lanes`, never change it without owner instruction.

Claude `/loop` and native Codex thread continuation use the same contract:

```text
Use beads-orchestration → beads-looper, implement mode, outcome <existing-id>.
Retain ownership through corrections; finish accepted scope or publish explicit handoff.
```

Resume the existing session where available. A new launch validates ownership,
current packet, published/local branch and journal before edits. Compaction state
includes actor, outcome, anchor/members, session locator, current PR/head, review
findings/evidence and waits. Do not treat session launch as continuation by name alone.

Each parent loop launches/resumes a session view through
[progress-dashboard](../../../th-projects/subskills/progress-dashboard/SKILL.md).
Load it once; retain its helper path and slug with the session locator.
Publish only changed rows and native child-agent observations at existing substantive
checkpoints; read/acknowledge feedback at those checkpoints. The dashboard owns
all files, rendering, usage collection and server lifecycle. There are no extra
model wakeups, transcript-summary passes or per-subagent servers. Its Markdown
files project Beads; they do not replace canonical lifecycle state. Update
`handover.md` only on a manual owner request, never automatically on compaction
or stop. Stop the dashboard when this loop retires unless an ongoing preview has
an explicit lifecycle owner.

## Invariants

- Mutate only held claims; adopt dead peers only under
  [runtime safety](../beads-coordinator/references/runtime-and-safety.md).
- Claim a package, not an entire backlog. Independent outcomes retain distinct
  owners and rollback paths. One shared prerequisite has one owner.
- Implementation claims and worktree remain with the owner during review/CI.
  Reviewer owns its review bead and `review/<review-id>` snapshot, never the
  implementation branch. Corrections return directly to the current owner.
- No fleet restarts, deployment or policy changes are implied by execution.
  [Decision autonomy](../../references/decision-autonomy.md) preserves actual
  authority gates; continue work that does not depend on a held decision.
- Preserve published useful work and quarantine unpublished work on failure.
  Worktree deletion is completion/recovery cleanup, not a PR-open handoff step.
- Close only individually verified and actually landed acceptance; keep the
  original outcome visibly partial until all required behavior/proof is met.

## Wait and report

Park, don't block: a bead waiting on review, CI, a dependency or an owner goes
to `blocked` with its resume event, and this lane takes the next admitted
action. An empty queue beside remaining candidates, dead peers' claims or
unwired PRs is a fault this lane repairs through the
[never-idle ladder](../beads-coordinator/references/runtime-and-safety.md#never-idle-with-runnable-work)
(refresh, park own waits, adopt the dead, repair what is now yours, review,
shape), not a reason to stop. Stop only at the genuine frontier it defines,
after parking every `in_progress` claim.

Use event-driven continuation for CI/review/dependency/owner events, with the
bounded fallback/stop rule in runtime safety. Use the same review-first admission queue in either mode; mode filters capability,
not global priority. Retained claims do not consume parked execution slots. Only
admit disjoint spillover after runnable higher-class work is admitted and execution
and WIP capacity permit it; keep resume events and returning-correction priority. P0 can preempt at a safe published checkpoint, with
explicit claim disposition and resume owner. On drift, publish recovery and retire.

Every state-change report names `LOOPER_ID`, `REPO_ROOT`, outcome, package/members,
accepted vs remaining behavior, PR, review/wait event, next action and recovery.
Final report also names released/retained claims and registry disposition. Reuse
[coordinator hold procedure](../beads-coordinator/references/coordinator-loop.md#owner-merge-hold)
and held-PR inventory when considering landing. Search
[known errors](../../references/known-errors.md) before re-deriving recovery.
