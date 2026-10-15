# Local State Reconciliation

Load this file when cleanup needs to inspect stale `in_progress` claims,
dependency state, Dolt/worktree health, or `review-running` locks.

## Boundary

**The coordinator is the sole mutation authority.** This cleanup pass only
collects evidence and emits recommendations. It never changes Beads, Git,
GitHub, worktrees, branches, or Dolt.

Start with the report-only [`cleanup_scan.py`](../scripts/cleanup_scan.py):

```bash
# from the beads-cleanup package directory
uv run scripts/cleanup_scan.py --repo-root "${REPO_ROOT}"
```

Treat every finding as stale. The coordinator must re-check the live command
result, assignee, and heartbeat immediately before acting. A `partial` or
`fatal` report is manual triage, never permission to infer state.

## Pass 1: Stale `in_progress` Beads

Interpret each `claims` finding using the stall heartbeat, remote-branch, and
unpublished-work fields.

| Evidence | Report recommendation |
|---|---|
| Fresh heartbeat | `preserve-live-claim` |
| Git probe failed or worktree evidence is unavailable | `manual-triage` |
| Unpublished work exists | `preserve-unpublished-work` |
| Stale heartbeat with complete negative evidence | `release-claim-candidate` for coordinator verification |
| Missing or malformed heartbeat | `manual-triage` |

Never turn a failed Git probe into a release recommendation. If another actor
has a fresh heartbeat, report manual triage and leave ownership unchanged.

## Pass 4: Blocked Beads Whose Blockers Are Closed

Use `blockers` findings to distinguish complete dependency evidence from
unknown or malformed dependency state:

| Dependency result | Report recommendation |
|---|---|
| Every dependency is closed | `unblock-candidate` for coordinator verification |
| Any dependency remains open | `remain-blocked` |
| Query or status is incomplete | `manual-triage` |

## Pass 5a: Dolt Health

The scanner reports `bd dolt status` and `bd doctor` as `healthy` or
`unhealthy`. Report unhealthy Dolt as manual triage. Do not try to repair
`.beads/dolt/` manually.

## Pass 5b: Coordinator Worktrees And Branches

Only inspect worktrees under `.worktrees/parallel-agents/`. The scanner emits
safe worktree IDs rather than paths and looks each opaque ID up as-is in
Beads before correlating it with remote-branch and unpublished-work evidence.
Never truncate an ID based on a name fragment such as `-review-`; an unknown
exact ID is manual triage.

| Bead state and evidence | Report recommendation |
|---|---|
| Closed with complete evidence | `cleanup-eligible-after-verification` |
| Open | `preserve-branch-worktree` |
| In progress or blocked | `preserve-active-worktree` |
| Unpublished work, failed Git probe, or unknown state | preserve or `manual-triage` |

The coordinator re-verifies and owns any lifecycle or repository change.

## Pass 5c: Build Artifacts

`build_artifacts` lists each `<bead-id>-*` directory under the given
`--artifact-root`s with its allocated `bytes`. The scanner resolves the longest
matching bead id and, for a bead that is not closed, its `gh-pr:` PR state.

| Evidence | Report recommendation |
|---|---|
| Bead closed, or its PR merged or closed | `delete-after-receipts` |
| Bead active and its PR open or absent | `preserve-active-bead` |
| No bead id resolves from the name | `manual-triage` |

Report the reclaimable total. The coordinator copies receipts out and deletes;
cleanup never does.

## Pass 6: Stale `review-running` Labels

`review_locks` distinguishes a live reviewer from a candidate stale lock:

- fresh `in_progress` heartbeat with a worktree: `preserve-review-lock`
- stale or missing liveness evidence: `release-review-lock-candidate` for
  coordinator verification
- malformed state: `manual-triage`

This is still a report-only liveness observation. The coordinator owns every
resulting change.
