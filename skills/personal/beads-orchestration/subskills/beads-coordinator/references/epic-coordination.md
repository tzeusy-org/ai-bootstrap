# Epic Coordination

Load this file when `coordinator-loop.md` Step 2 decomposes a childless epic,
when a `focus` epic qualifies for Team Lead mode (Step 5), or when a worker
reports a hard blocker that needs new bead creation. The finish-first queue
never lists an epic itself; it lists the epic's runnable children.

## Epic Decomposition

Do not dispatch epics to workers directly. First classify the epic's complexity
to choose between independent dispatch and team coordination mode.

### Classification Heuristics

| Signal | Points toward epic complexity |
|---|---|
| Cross-cutting scope across API + frontend + DB + tests | yes |
| Tightly coupled subtasks | yes |
| Integration risk across separate branches | yes |
| Shared design decisions needed up front | yes |
| More than 3 interdependent children | yes |
| Independent, parallelizable subtasks | no; use independent dispatch |
| Purely additive work | no; use independent dispatch |

Default to independent dispatch. Escalate to team coordination mode only when
two or more positive signals are present.

### Epic Complexity Tiers

Count the positive signals from the table above to assign a tier. The same
count drives both the dispatch decision and the reconciliation-model floor.

| Positive signals | Tier | Dispatch | Reconciliation model floor |
|---|---|---|---|
| 0 | low / trivial | independent dispatch | `MEDIUM_COMPLEXITY_MODEL` |
| 1 | medium | independent dispatch | `EPIC_COMPLEXITY_MODEL` |
| 2+ | high | team coordination mode | `EPIC_COMPLEXITY_MODEL` |

"Medium or higher" therefore means **1+ positive signals**, or any epic that
carries the `team-coordination` label. For those epics the terminal
reconciliation bead is dispatched at `EPIC_COMPLEXITY_MODEL` (Opus on
Claude); see "Reconciliation Floor" in `runtime-and-safety.md`.

## Independent Dispatch

1. Inspect children:
   ```bash
   bd children <epic-id> --json
   ```
2. If children exist, they enter the finish-first queue
   (`../scripts/rank_ready_work.py`) and each ready child dispatches as its own
   bead, at most `--max-lanes-per-epic` at a time.
3. If no children exist, the epic shows under `childless_epics`. Decompose it
   only when `coordinator-loop.md` Step 2 selects it. One owner does it: you,
   following `../../beads-writer/SKILL.md`'s workflow, holding the epic's
   claim while creating the children sequentially (child ids come from a
   per-parent counter that two creators can race):
   ```bash
   bd --actor "$COORDINATOR_ID" update <epic-id> --claim --json
   CHILD_JSON=$(bd --actor "$COORDINATOR_ID" create "<subtask title>" \
     --description="<details>" \
     -t task -p <priority> --parent <epic-id> \
     --json)
   CHILD_ID=$(echo "${CHILD_JSON}" | jq -r '.id // .[0].id')
   # repeat per child; wire sibling order with dep add, then:
   bd --actor "$COORDINATOR_ID" unclaim <epic-id> --reason "decomposed"
   ```
   `--parent` is what makes a bead count toward its epic's progress in the
   ranking; a plain dependency from the epic on the child only blocks the epic
   and leaves the child an orphan in the queue. Children inherit the epic's labels: add
   `--no-inherit-labels` when the epic carries lifecycle labels such as
   `team-coordination` or `needs-shaping` (the ranking reads a `focus` pin
   from ancestors either way).
4. Then dispatch the subtasks through the normal loop. Each child must pass
   the dispatch-readiness gate in `coordinator-loop.md`: a title-and-description
   stub like the one above is stamped `needs-shaping`, and the shaping lane
   there fills in its `design` and `acceptance_criteria` before it can
   dispatch.

## Team Coordination Mode

When the heuristics indicate tightly coupled work, dispatch a Team Lead agent
instead of individual workers. The Team Lead is a high-capability model
(`EPIC_COMPLEXITY_MODEL`) that acts as a mini-coordinator for code work, but it
does not own Beads lifecycle mutations.

### Slot Reservation

A Team Lead holds one lane (the epic's claim) plus `TEAM_SIZE` subworker
lanes of the global budget. Set `TEAM_SIZE` to at most `capacity.free - 1`,
read before claiming the epic, so other epics keep their lanes. Subworkers are
invisible to the tracker, so publish them for every coordinator until the lead
reports, and drop back to 0 after:

```bash
uv run scripts/coordinator_registry.py register --repo-root "$REPO_ROOT" \
  --id "$COORDINATOR_ID" --wake-minutes <planned delay> --extra-lanes <TEAM_SIZE>
```

`rank_ready_work.py` counts them as occupied, and while the epic is
`in_progress` with the `team-coordination` label it keeps the epic's children
out of every coordinator's queue.

While a Team Lead is active, the coordinator keeps looping for PR-review
handling, monitoring, and whatever budget remains.

### Team Lead Rules

- The Team Lead may plan and integrate code work.
- The Team Lead may spawn code-writing subworkers.
- Every code-writing subworker must have its own worktree and branch.
- The Team Lead must not ask multiple writers to share a worktree.
- The Team Lead must not run `bd create`, `bd update`, `bd dep add`, or
  `bd close`.
- If new child beads are needed, the Team Lead reports proposals and the
  coordinator creates them sequentially.

### Team Lead Dispatch

1. Label the epic:
   ```bash
   bd --actor "$COORDINATOR_ID" update <epic-id> --add-label team-coordination
   ```
2. Claim it atomically with `bd --actor "$COORDINATOR_ID" update <epic-id> --claim` and write the stall heartbeat.
3. Create Team Lead worktree:
   ```bash
   (cd "${REPO_ROOT}" && bd worktree create .worktrees/parallel-agents/<epic-id> --branch agent/<epic-id>)
   ```
4. Build prompt:

```text
Lead one epic-complexity issue: settle the shared design, coordinate
sub-workers, integrate their output, and deliver one consolidated PR.

ISSUE_ID: <epic-id>
WORKTREE_PATH: <team-lead-worktree-path>
REPO_ROOT: <repo-root>
TEAM_SIZE: <number of sub-workers you may spawn>
WORKER_SKILL: <absolute path to beads-worker/SKILL.md>

Summary: <2-4 lines plus the acceptance criteria; run `bd show` for more>

Rules:
- Follow WORKER_SKILL for bootstrap, verification, handoff, and the final
  Worker Report. This dispatch lifts only its ban on spawning code-writing
  helpers.
- Subworkers commit and push their own branch and report to you. They have no
  bead of their own and open no PR.
- Do not mutate Beads lifecycle.
- If you need follow-up beads, report concrete proposals for the coordinator.
- Every code-writing subworker must get its own worktree and branch.
- You own integration and final verification.
```

5. Dispatch using `EPIC_COMPLEXITY_MODEL`.

### Team Lead Subworker Isolation

Recommended naming:
- Team Lead branch: `agent/<epic-id>`
- Subworker branch: `agent/<epic-id>-<slice-id>` (not `agent/<epic-id>/<slice-id>`:
  Git cannot hold a branch nested under the existing `agent/<epic-id>` ref)
- Subworker worktree:
  `.worktrees/parallel-agents/<epic-id>-<slice-id>`

The Team Lead integrates subworker branches into `agent/<epic-id>` after each
subworker completes, then removes that slice's worktree and branch so cleanup
does not later report them as orphans of an unknown bead.

### Monitoring Team Leads

Use the same monitoring process as normal workers, with these additions:
- Team Leads get 3x the normal stall timeout before intervention.
- If a Team Lead stalls but has pushed sub-branches:
  1. check which slices are complete versus still open
  2. release the epic with
     `bd --actor "$COORDINATOR_ID" unclaim <epic-id> --reason "<completed slices>"`
     and re-register with `--extra-lanes 0`
  3. on the next cycle, re-evaluate whether a fresh Team Lead should finish
     integration only
- When the Team Lead finishes, apply the same closure rule as regular workers.

## Handling Blockers

If a worker or Team Lead reports a hard blocker:

0. Triage the blocker against `../../../references/decision-autonomy.md`. If it
   is decision-shaped (asks for a choice rather than an external event) and no
   hard gate applies, do NOT create a blocker bead: decide via the protocol,
   append the `[decision]` record to the bead's notes, and re-dispatch the same
   bead with the decision inlined in the worker prompt. Only genuinely external
   blockers and hard-gated decisions proceed to step 1.
1. The coordinator creates a blocker issue:
   ```bash
   BLOCKER_JSON=$(bd --actor "$COORDINATOR_ID" create "Blocker: <description>" \
     --description="<details>" \
     -t bug -p <priority of the blocked bead> \
     --json)
   BLOCKER_ID=$(echo "${BLOCKER_JSON}" | jq -r '.id // .[0].id')
   bd --actor "$COORDINATOR_ID" dep add <original-id> "${BLOCKER_ID}"
   ```
   The blocker takes the blocked bead's priority, not P0: the ranking's
   prerequisite rule already queues it with the blocked bead's epic, and P0
   is reserved for incidents that preempt everything.
2. The coordinator releases the original with
   `bd --actor "$COORDINATOR_ID" unclaim <original-id>`: its new dependency
   keeps it out of the ready queue until the blocker closes, and then any
   coordinator can take it. Keep it `blocked` under your claim instead only
   when `coordinator-loop.md` Step 7 ties its recovery state to your lane.
