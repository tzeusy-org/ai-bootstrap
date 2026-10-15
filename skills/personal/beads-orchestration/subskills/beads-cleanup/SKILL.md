---
name: beads-cleanup
description: Use when a Beads coordinator needs to reconcile stale worker state, PR-review bookkeeping, or orphaned worktrees before dispatching new work.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-04"
compatibility: Requires a Beads-backed git repository with `bd`, `git`, `gh`, `jq`, and git worktree support. Authenticated GitHub access is required for report-only PR reconciliation.
---

# Beads Cleanup

Run this skill before a `beads-coordinator` loop when stale worker state may
have been left behind by crashes, compaction, out-of-token exits, or missed
handoffs.

This skill is a read-only reconciliation pass. It reports Beads and Git
evidence for the coordinator; it does not implement code, dispatch workers,
or mutate lifecycle state.

## Use This Skill When

- starting or resuming a `beads-coordinator` loop
- recovering after a crashed coordinator or killed worker
- auditing stuck `in_progress`, `blocked`, `pr-review`, or `review-running`
  state
- reporting orphaned coordinator worktrees or branches tied to Beads issues
- sizing build-artifact directories (`--artifact-root`) whose bead is closed
  or whose PR merged, so the coordinator can reclaim disk

## Do Not Use This Skill When

- implementing the assigned issue itself
- performing normal ready-work selection or worker dispatch
- creating new beads for discovered work
- repairing GitHub review threads inside an active PR review worker

## Source Of Truth

This skill is a routing layer over the existing Beads operating model.

- Repository workflow and ownership model: `../../../../../README.md`
- Coordinator mutation authority, atomic claim, and stall-heartbeat rules:
  `../beads-coordinator/references/runtime-and-safety.md`
- Coordinator loop and PR-review lane behavior:
  `../beads-coordinator/references/coordinator-loop.md`
- PR-review worker closure boundary and review-lock vocabulary:
  `../beads-pr-reviewer-worker/SKILL.md`

If these sources disagree with this skill, fix this skill. Do not let cleanup
become a competing doctrine.

## Non-Negotiable Boundaries

- Never implement code.
- **The coordinator is the sole mutation authority.** Cleanup never claims,
  releases, creates, closes, relabels, unblocks, removes a worktree, deletes a
  branch, or repairs Dolt. It reports evidence and recommendations only.
- Never mutate a bead held by a live actor. Decide ownership by the bead's
  `assignee` plus a fresh stall heartbeat, per
  `../beads-coordinator/references/runtime-and-safety.md`.
- **Never mutate PR-review bead state from PR outcome.** Cleanup inspects PR
  state and reports the finding plus a recommended action; the coordinator's
  Step 0 is the sole PR-state mutator.
- Never create new beads from cleanup. Missing wiring should be reported for the
  coordinator loop to self-heal.
- Never touch `.beads/dolt/` manually.
- Inspect token-efficiently (`../../references/token-efficiency.md`): project
  every `bd`/`gh` listing through `jq` to the fields the pass needs, batch
  related checks into one composite command, and keep the final report to
  findings and recommendations — not raw command output.

## Load Only The Reference You Need

- [`references/local-state-reconciliation.md`](references/local-state-reconciliation.md)
  Load for stale `in_progress` claims, dependency unblocking, Dolt/worktree
  health, and stale `review-running` labels.
- [`references/pr-review-reconciliation.md`](references/pr-review-reconciliation.md)
  Load for blocked original `pr-review` beads and blocked
  `pr-review-task` review beads.
- [`references/reporting.md`](references/reporting.md)
  Load for the cleanup report shape, rig-routing notes, command quick reference,
  and coordinator handoff summary.
- [`scripts/cleanup_scan.py`](scripts/cleanup_scan.py)
  Run first for one compact `beads-cleanup-scan/v1` read model covering claims,
  dependencies, Dolt, worktrees/branches, review locks, and nested PR findings.
  It is report-only: it has no apply mode and never replaces fresh verification
  before a coordinator-owned mutation.

## Workflow

1. Read `../beads-coordinator/references/runtime-and-safety.md` if you need the
   exact claim, stall-heartbeat, and mutation-authority rules before handing a
   finding to the coordinator.
2. From the cleanup package directory, run
   `uv run scripts/cleanup_scan.py --repo-root "${REPO_ROOT}"` (plus one
   `--artifact-root <dir>` per artifact root the repo's `AGENTS.md` names) to
   collect compact read-only findings. Treat `partial` and `fatal` as manual-triage
   reports; do not forward command output or infer a mutation from them.
3. Use the relevant passes from the reference files to interpret the report.
   When evidence is incomplete or a bead is held by a live actor (fresh
   heartbeat), report manual triage. For PR-review beads, report findings only.
4. Keep canonical PR metadata on the original implementation bead only; review
   beads must not invent their own `external_ref`.
5. Report worktrees and branches conservatively. Preserve anything that may
   still contain useful unpublished work.
6. Produce the structured report from `references/reporting.md` so the
   coordinator has a clear evidence ledger before dispatch begins.
