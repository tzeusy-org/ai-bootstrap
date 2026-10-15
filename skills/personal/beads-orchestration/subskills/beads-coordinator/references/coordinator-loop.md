# Coordinator Loop

Load this file when you need the exact cycle, PR-review priority lane, claim and
heartbeat rules, worker bootstrap rules, monitoring details, or adaptive polling.

## Preflight

- Before entering the loop, run `../../beads-cleanup/SKILL.md`. This is mandatory.
- Run `bd doctor` once at startup and again after any unexpected `bd` error;
  do not spend worker cycles on a sick tracker.
- Create a fresh coordinator session ID for this run (used as the stall-heartbeat
  owner; atomic claiming is handled by `bd update --claim`).
- If running from outside the target rig, pass `bd -C <path>` on **every**
  command, including ones that take a bead ID — see "Rig Targeting" in
  `../SKILL.md`; prefix auto-routing does not work.
- Detect once per run whether the default branch is behind a merge queue, and
  carry the answer as `MERGE_QUEUE=yes|no` into every reviewer prompt and the
  direct-merge path:

```bash
BASE=$(git remote show origin | sed -n 's/.*HEAD branch: //p')
MERGE_QUEUE=$(gh api "repos/{owner}/{repo}/rules/branches/${BASE}" \
  --jq '[.[] | select(.type=="merge_queue")] | if length > 0 then "yes" else "no" end' \
  2>/dev/null || echo no)
```

  With a queue, integration (rebase onto the latest base plus one full CI run
  on the merge group) is GitHub's job: reviewers stop at `merge-queued`,
  nothing rebases a PR to "freshen" it, and Step 0 confirms `MERGED`.

## Constraints

| Constraint | Value |
|---|---|
| Worker lanes | 1 by default; open a 2nd-4th only under `../../../references/token-efficiency.md` → "Cache-first execution"; ceiling 4 (owner-raised from 3) unless the owner explicitly asks for more |
| Lane unit | one long-lived worker session running one bead at a time; each bead keeps its own claim, worktree, branch, PR, and report |
| Slot | an idle lane, or unused capacity to open one within the lane limit; implementation and reviewer lanes are separate |
| Worker isolation | each worker gets its own beads worktree and branch |
| Worktree creation | `bd worktree create` |
| Branch naming | `agent/<issue-id>` |
| Worktree root | `.worktrees/parallel-agents/` |
| Worker bootstrap | worker must prove `pwd == WORKTREE_PATH` and expected branch |
| Issue tracker | `bd` CLI only |
| Metadata persistence | shared external Dolt sql-server (`bd` never starts it; see `runtime-and-safety.md` → "Bead Mutation Safety") |
| PR review cooldown | 5 minutes after PR `createdAt` |

The stall-heartbeat model (TTL, renewal cadence), the per-runtime stall
threshold, the orchestrator wake cadence (prompt-cache economics), and the
model-selection tables live in `runtime-and-safety.md`. Do not restate them
here.

Repeat this rule during the whole run:

Before any `bd` mutation: confirm you are the bead's `assignee`, renew the stall
heartbeat if near expiry, then mutate. Never mutate a bead assigned to another
live actor.

## Step 0: Normalize PR-Review State

Step 0 is the **sole PR-state mutator** in the system. Only the coordinator
closes, reopens, or relabels beads in response to PR state (`MERGED`,
`CLOSED`-unmerged, etc.). `beads-cleanup` only inspects and reports PR-state
findings; it never mutates PR-review bead state.

When a `beads-cleanup` pass ran immediately before this loop, consume its
"PR-state findings (for coordinator Step 0)" report section instead of
re-querying `gh pr view` for those same PRs. Re-verify with `gh` only in the
moment right before an actual mutation, so a stale finding never drives an
irreversible close/reopen.

First collect the compact, read-only scan from
[`scripts/normalize_pr_review_state.py`](../scripts/normalize_pr_review_state.py):

```bash
# from the beads-coordinator package directory
uv run scripts/normalize_pr_review_state.py --repo-root "${REPO_ROOT}"
```

It emits one `beads-pr-review-normalization/v1` envelope with deterministic
lists of blocked original/review-task contexts, duplicate selections, cooldown
candidates, and open-branch self-heal candidates. How to read it:

- Its recommendations are **not authorization**. Step 0 remains the sole
  PR-state mutator and must freshly verify the exact `gh`/Beads state,
  assignee, and heartbeat immediately before each actual mutation.
- A `partial` or `fatal` envelope, and every `manual-triage` finding, is a
  report-only triage result: never guess, mutate, dispatch, or wire from it.
  The script fails the whole collection closed on any inventory, lookup,
  relation-graph, chronology, or PR-metadata defect; the exact contract lives in
  the script's module docstring and is enforced by
  [`scripts/review_relation_graph.py`](../scripts/review_relation_graph.py) —
  do not re-derive it here, and do not "repair" a partial result by hand.

Before discovering new work, and whenever a worker frees a slot, check
(projected — never dump the unfiltered JSON into context; see
`../../../references/token-efficiency.md`):

```bash
bd list --status=blocked --label pr-review --json \
  | jq -c '[.[] | {id, title, labels, assignee, external_ref}]'
```

This list may include:
- blocked original implementation beads waiting on PR outcome
- blocked dedicated PR-review beads with label `pr-review-task`

Canonical PR metadata is stored on the original implementation bead only.

### 0a. Dedupe review beads

Before creating or dispatching any review bead, dedupe by:
- original implementation bead ID
- `external_ref` PR number on the original bead

If multiple review beads refer to the same original bead or PR:
- keep the oldest non-closed review bead by parsed creation chronology as canonical
- if any candidate creation timestamp is invalid, emit partial manual triage;
  never choose by raw timestamp text
- remove stale `review-running` labels from duplicates
- append notes to duplicates explaining they were superseded
- close duplicates if safe, or leave them blocked for explicit cleanup if
  closure would be ambiguous

Never create a new `pr-review-task` bead while a canonical open or blocked one
already exists for the same original bead / PR.

Run this dedupe check immediately before **any** `bd create` of a review bead,
not only at the start of a cycle. Step 0c and Step 7 both create review beads;
each must rerun this check (by original bead ID and `external_ref` PR number)
right before creating, and skip creation if a canonical review bead exists.

### 0a1. Claim review beads before dispatch

Review-lane dispatch must use the same atomic `--claim` discipline as ready
work. A blocked `pr-review-task` bead is not dispatchable until the coordinator
has claimed it successfully.

Required sequence:
1. Read the review bead and inspect its `assignee`.
2. If it is assigned to another live actor, skip it.
3. Claim atomically with `bd update <id> --claim --json` (sets `assignee` and
   `status=in_progress`), then add the `review-running` label and write the
   stall heartbeat note.
4. Verify success from the command result (`assignee` is this coordinator).
5. Only then dispatch the reviewer worker.

If dispatch fails after the claim:
- return the bead to `blocked`
- remove `review-running`
- renew the stall heartbeat note so cleanup can see when the failed attempt
  went idle

### 0b. Reconcile blocked PR-review beads

For each blocked issue:
- if it is an original bead, read its own `external_ref` (`gh-pr:<number>`)
- if it is a dedicated `pr-review-task` bead, resolve its original bead first,
  then read the original bead's `external_ref`

If no PR number can be resolved, append a note and skip mutation for that bead.

When PR number is available, query queue membership directly and project only
the reconciliation fields:

```bash
PR_QUEUE_JSON=$(gh api graphql \
  -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){state mergedAt autoMergeRequest{enabledAt} mergeQueueEntry{position enqueuedAt} labels(first:20){nodes{name}}}}}' \
  -F owner="${OWNER}" -F name="${REPO}" -F number="${PR_NUMBER}" \
  | jq -e '. | select((has("errors") | not) or .errors == null or ((.errors | type) == "array" and (.errors | length) == 0)) | .data.repository.pullRequest | select(type == "object" and has("state") and (.state | type) == "string" and has("mergedAt") and (.mergedAt == null or (.mergedAt | type) == "string") and has("autoMergeRequest") and (.autoMergeRequest == null or ((.autoMergeRequest | type) == "object" and (.autoMergeRequest | has("enabledAt")) and (.autoMergeRequest.enabledAt == null or (.autoMergeRequest.enabledAt | type) == "string"))) and has("mergeQueueEntry") and (.mergeQueueEntry == null or ((.mergeQueueEntry | type) == "object" and (.mergeQueueEntry | has("position")) and (.mergeQueueEntry.position | type) == "number" and (.mergeQueueEntry | has("enqueuedAt")) and (.mergeQueueEntry.enqueuedAt | type) == "string")) and has("labels") and (.labels | type) == "object" and (.labels.nodes | type) == "array" and all(.labels.nodes[]; type == "object" and (.name | type) == "string")) | {state, mergedAt, auto_merge_armed: (.autoMergeRequest != null), merge_queue_entry: (.mergeQueueEntry | if . == null then null else {position, enqueuedAt} end), labels: [.labels.nodes[].name]}')
```

If GraphQL or the projection fails, log a warning and skip every mutation for
that PR this cycle. A successful projection distinguishes actual queue
membership from auto-merge merely being armed.

Handle each case:

| PR State | Action |
|---|---|
| `MERGED` | renew heartbeat, then close review/original beads as appropriate (a `queue-direct` PR has only an original bead); then run the post-closure branch/worktree cleanup below |
| `CLOSED` and not merged | renew heartbeat, then reopen original for re-triage; block/close review bead as appropriate |
| `OPEN` and `merge_queue_entry` non-null | queued: do nothing this cycle, regardless of `auto_merge_armed`; re-check on the next wake |
| `OPEN`, `merge_queue_entry` null, and `auto_merge_armed` true | auto-merge is armed but the PR is not yet confirmed in the queue: remove any stale `merge-queued` label, then wait and re-check; do not dispatch or rebase |
| `OPEN`, review bead labelled `merge-queued`, `merge_queue_entry` null, and `auto_merge_armed` false | membership is absent, but absence alone does not prove why: remove `merge-queued` and treat it as an `OPEN` `pr-review-task`; do not authorize `--force-rebase` without a textual merge conflict or a justified reviewer request |
| `OPEN` with `pr-review-task` | wait for cooldown; once elapsed, atomically claim the review bead with `bd update <id> --claim`, then dispatch review worker if slot is open |
| `OPEN` labelled `queue-direct` | no review bead by design; if `merge_queue_entry` is null and `auto_merge_armed` is false, re-run `gh pr merge <n> --squash --auto`; after two failed enrollment attempts drop the label and route through the normal review lane, without authorizing a rebase from queue metadata alone |
| `OPEN` without `pr-review-task` | ensure exactly one dedicated review bead exists |
| `gh` failure | log warning, skip; do not mutate on transient errors |

### Post-closure branch and worktree cleanup (merged PRs)

The reviewer merges with `gh pr merge --squash` and does **not** delete the
branch, so the PR branch `agent/${ORIGINAL_ID}` survives until the coordinator
removes it. This keeps branch-name → bead correlation intact if a crash happens
between merge and the worker report. Branch deletion is therefore the
coordinator's responsibility, performed only **after** the relevant bead(s) are
closed.

Keep the two-stage review topology explicit during cleanup: `ORIGINAL_ID` owns
the remote PR branch and its checked-out local branch, while `REVIEW_ID` owns
the reviewer worktree path and any temporary `agent/${REVIEW_ID}` branch. When
the reviewer worktree was transferred sequentially to a correction worker,
the transferred worktree path remains keyed by `REVIEW_ID` even though its
checked-out branch is `agent/${ORIGINAL_ID}`. Remove that worktree before
deleting local branches so the checked-out original branch is no longer in use:

```bash
# After bd close on the review/original beads:
git push origin --delete "agent/${ORIGINAL_ID}" 2>/dev/null \
  || gh api -X DELETE "repos/<owner>/<repo>/git/refs/heads/agent/${ORIGINAL_ID}" 2>/dev/null || true
bd worktree remove ".worktrees/parallel-agents/${REVIEW_ID}" --force 2>/dev/null || true
git branch -D "agent/${ORIGINAL_ID}" 2>/dev/null || true
git branch -D "agent/${REVIEW_ID}" 2>/dev/null || true
```

Do not delete the branch before closure; correlation depends on it.

In the same step, delete the bead's build artifacts. Copy receipts (gate logs,
exit codes, screenshots, small evidence files) into the bead's evidence
directory first; then delete every `<bead-id>-*` artifact directory under the
repo's artifact roots (named in its `AGENTS.md`). Do this on PR merge or close
even if the bead stays open for an owner check: the target has no further use.
The cleanup preflight's `build_artifacts` section lists stragglers as
`delete-after-receipts`.

Priority rule:
- if any `pr-review-task` bead is dispatchable and a slot is open, dispatch it
  before selecting from `bd ready`
- do not start new implementation work while a dispatchable `pr-review-task`
  is waiting for an open slot
- dispatch review beads oldest PR first (`createdAt`), so the PR that has waited
  longest lands first and later PRs rebase (if at all) once
- never run two reviewers concurrently whose PRs touch overlapping files
  (`gh pr diff <n> --name-only`); serialize the newer one behind the older.
  Without a merge queue, each merge invalidates the CI of every overlapping
  open PR; with one, the queue serializes them anyway and the reviewer's job
  ends at `merge-queued`

### 0c. Self-heal rule

Reconcile open PRs back into Beads even if workers failed to mutate metadata:

```bash
gh pr list --state open --json number,url,headRefName,createdAt
```

For each PR whose `headRefName` matches `agent/<issue-id>`, skipping PRs that
carry the `queue-direct` label (coordinator-opened direct-merge candidates that
the merge queue integrates without a review bead):
- ensure the original bead is `status=blocked`, has
  `external_ref=gh-pr:<N>`, and label `pr-review`
- ensure exactly one dedicated `pr-review-task` bead exists; if missing, create
  it sequentially and wire the dependency with `bd dep add`
- if duplicates exist, dedupe before dispatch

Safe PR-review bead creation pattern:

```bash
REVIEW_JSON=$(bd create \
  "Conduct a thorough code review of ${PR_URL}" \
  --description="Review PR ${PR_URL} thoroughly. Original implementation bead: ${ORIGINAL_ID}. Leave resolvable PR comments on verdict-changing issues, report corrections for the implementation lane, and attest merge readiness for the exact reviewed head." \
  --design="Independent exact-head review. Classify risk before review; reviewer reports semantic corrections to the implementation lane and never merges an unreviewed moved head." \
  --acceptance="1. Record reviewer identity and risk tier. 2. Bind findings and merge verdict to the exact PR head SHA. 3. Resolve or explicitly classify every review thread. 4. Run risk-scaled gates and merge only when readiness fails closed to safe." \
  -t task -p 1 --validate --json)
REVIEW_ID=$(echo "${REVIEW_JSON}" | jq -r '.id // .[0].id')
bd dep add "${ORIGINAL_ID}" "${REVIEW_ID}"
bd update "${REVIEW_ID}" \
  --status blocked \
  --add-label pr-review \
  --add-label pr-review-task \
  --append-notes "Review target bead: ${ORIGINAL_ID}. PR: ${PR_URL}"
```

## Step 1: Discover Ready Work

Only run this step when Step 0 found no dispatchable `pr-review-task` issues
for currently available slots.

```bash
bd ready --json | jq -c '[.[] | {id, title, priority, type, labels, assignee, created_at}]'
```

Selection needs only these fields. Do not `bd show` candidates you are not
about to claim; fetch detail for the one selected bead only.

If the list is empty and nothing else is dispatchable, you are at the
no-progress frontier (Step 8).

### Dispatch readiness gate

Before claiming a candidate, read its structured fields and require a complete
dispatch packet: outcome/non-goals, governing spec or explicit maintenance
authority, surface/trust-boundary map, relevant failure/concurrency/idempotence
matrix, documentation impact, and behavior-executing verification. A blank
structured `acceptance_criteria` field is not dispatch-ready. Do not ask a
worker to reconstruct planning context.

The bar itself is correct and stays as written. The defect this gate had was the
silent dead end after a rejection, never the requirement — do not "fix" a
backlog of rejected beads by loosening the packet.

Track readiness precisely: **packet-complete** means the structured content is
complete; **runnable-now** additionally means dependencies/sign-off are clear,
ownership does not overlap, and a suitable lane is available. Dispatch only
runnable-now work.

#### Stamp every rejection

A rejection is a mutation the coordinator owns, so it lands on the bead — never
only in your own head:

```bash
bd update <id> --add-label needs-shaping \
  --append-notes "dispatch-readiness <YYYY-MM-DD> <coordinator-session-id>: missing <field list>"
```

Name the exact missing packet items (`acceptance_criteria blank`, `design
blank`, `no governing spec`, `no verification`, …); never a bare "incomplete".
Idempotence:
- read existing notes first and skip the note when an identical
  `dispatch-readiness` note with the same missing set is already present
- stamp a bead at most once per coordinator run; a later poll cycle that
  re-rejects the same bead reuses the existing stamp
- remove `needs-shaping` only when a re-run of this gate passes

Run a **cohesion check** against ready/in-progress beads and active PRs. When a
candidate shares two or more allocation signals (module/interface,
tests/fixtures, migration/config/contract, review surface, micro size), bundle
before work starts or serialize it behind the active owner. Do not dispatch
overlapping siblings in parallel. Record each deferred candidate's id and the
bead/PR it overlaps with for the report; a cohesion deferral carries no label.

### Shaping lane (readiness exit path)

Rejected beads are work, not silence. When `needs-shaping` beads exist and a
worker slot is free, dispatch **one** shaping worker for a batch of rejected
beads sharing a parent epic:

- worker skill: `../../beads-writer/SKILL.md`; its workflow Phase 3 "Structured
  Dispatch Packet" is the shaping target (there is no separate shaping entry
  point in that skill). Its deliverable is specification text, so dispatch it
  at `DESIGN_AND_SPECIFICATION_MODEL`.
- prompt carries the bead ids, each one's missing-field list, the parent epic
  id, and the evidence the beads already cite (pursuit dossier JSON, spec paths)
- the worker returns **proposed field text only** — `design` and
  `acceptance_criteria` per bead — and runs no `bd` mutation; the "workers never
  mutate Beads lifecycle" invariant is unchanged
- the coordinator applies it as assignee, then re-runs the readiness gate on
  each shaped bead in this cycle:

```bash
bd update <id> --design="<proposed>" --acceptance="<proposed>" \
  --remove-label needs-shaping
```

Caps: one shaping dispatch per parent epic per run, and one shaping attempt per
bead per run. A bead that still fails the gate after shaping keeps its
`needs-shaping` stamp and is escalated in the escalation format of
`../../../references/decision-autonomy.md`. Never loop shaping on it.

## Step 2: Select Next Issue

Pick the issue with the lowest `priority` number, breaking ties by oldest
`created_at`. Skip any issue that:
- is already assigned to a running worker
- is assigned to another live actor (per `assignee`)
- is blocked by a dispatchable review task that should run first

Within a lane, **context locality** outranks the created-at tie-break and may
outrank a small priority gap. When an idle lane frees up, prefer the ready bead
that shares the most cohesion signals with that lane's last bead (module, spec
area, fixtures, review surface). Priority still decides which lane opens next,
and a P0/P1 in a different area never waits behind locality: it preempts at the
next bead boundary or opens its own lane.

Otherwise, preserve **context affinity**: prefer the worker/recovery lane that already owns the same active
subsystem or PR when it can be resumed safely. Prefer the same independent reviewer for exact-head
rechecks. Rotate only for independence, risk-tier, availability, or stale-
context reasons; affinity never overrides isolation or conflicting ownership.

## Step 3: Claim The Issue

Claim atomically with `bd update <id> --claim`. This is the real mutual-exclusion
mechanism, not the notes-based heartbeat.

1. Read the bead and confirm its `assignee` is empty or already this coordinator.
   If it is assigned to another live actor, skip this bead.
2. Claim atomically:

```bash
bd update <id> --claim --json
```

   `--claim` sets `assignee` to you and `status=in_progress` in one atomic,
   idempotent operation (a no-op if you already hold it). This provides
   cross-actor mutual exclusion: only one actor wins the assignee.
3. Verify success from the command result (`assignee` is this coordinator,
   `status=in_progress`).
4. Write the initial stall heartbeat note (see the durable-heartbeat model in
   `runtime-and-safety.md`). The heartbeat is stall detection only; it is NOT
   the claim mechanism.
5. Only after a verified claim may the coordinator create the worktree or
   dispatch a worker. If `bd worktree create` then fails because `agent/<id>`
   already exists, the bead is already in flight: do not dispatch a second
   worker (the two-layer exclusion model is in `runtime-and-safety.md`).

## Step 4: Prepare Worker Environment

```bash
(cd "${REPO_ROOT}" && bd worktree create .worktrees/parallel-agents/<id> --branch agent/<id>)
```

This creates an isolated code worktree for the worker branch. Beads metadata is
shared across worktrees via a Dolt DB redirect file.
The subshell is required: `bd -C "${REPO_ROOT}"` selects the owning tracker but
does not guarantee that Git worktree operations run against that repository.

PR-review worktrees are the exception to the generic `agent/<id>` branch
invariant. Their path and initial branch are keyed by the review bead. The
reviewer then switches that worktree to the canonical PR head during its
preparation phase. Apply the two-stage attestation in Step 6a rather than
requiring both branch states in one bootstrap check.

## Step 5: Build The Worker Prompt

Choose worker skill by issue type:
- epic-complexity issue: dispatch as team lead; see `epic-coordination.md`
- default implementation issue: `../../beads-worker/SKILL.md`
- `pr-review-task` issue: `../../beads-pr-reviewer-worker/SKILL.md`

A `reconciliation`-labelled bead still uses `../../beads-worker/SKILL.md`, but its
model is floored at `EPIC_COMPLEXITY_MODEL` for medium-or-higher epics — apply
the "Reconciliation Floor" in `runtime-and-safety.md` when picking the model in
Step 6.

The prompt carries:
- the worker skill's absolute path (the worker reads it; never paste its body)
- `ISSUE_ID`, `WORKTREE_PATH`, `REPO_ROOT`
- a 2-4 line issue summary plus its acceptance criteria
- when they apply: the `LANE-CONTINUATION` header (Step 6), `MERGE_QUEUE`
  (reviewer dispatches), the correction-mode variables
  (`REVIEW_CORRECTION_MODE`, `EXISTING_PR_NUMBER`, `REVIEW_BEAD_ID`,
  `CORRECTION_THREADS_JSON`), an inlined `[decision]` record, a coordinator
  authorization the reviewer skill requires (reviewer-as-fixer,
  `--force-rebase`), and likely edit targets

No other issue content. Do not inline full `bd show <id> --json` output.
`ISSUE_JSON` is deprecated as a dispatch field: the worker runs
`bd show <id> --json` itself when it needs more detail. Team Lead and
shaping-lane prompts are defined in their own sections and are not bound by
this list.

## Step 6: Dispatch The Worker

**Lane continuation first.** If a lane is idle (its last worker report has
been reconciled) and the selected bead fits it (shares context and needs the
same model tier), continue that worker's session with the constructed prompt
headed `LANE-CONTINUATION` (Claude Code: `SendMessage` to the worker's agent
id/name; Codex: send input to the same thread). The worker re-runs its Phase 1
bootstrap against the new `WORKTREE_PATH`, and Step 6a applies unchanged. Keep
a lane table `{lane_id, agent_ref, model, beads_done, last_bead,
compactions_seen}` in the coordinator's run state.

Spawn a **new** worker only to open a lane, or to replace one that must retire
(`../../../references/token-efficiency.md` → "Retire a lane"). Retire the lane,
without forcing it past its limits, when any of these holds: its worker reports
`blocked-awaiting-coordinator` for context reasons, it gets a
bootstrap/worktree mismatch on continuation, its reports get vaguer, or it
contradicts its own earlier decisions.

Spawn via the runtime's native subagent mechanism using the constructed prompt.
Use `fork_context=false` for Codex worker dispatches unless you are explicitly
dispatching a coordinator-like helper that must inherit thread history.

Reviewer-worker precondition:
- before dispatching a reviewer worker for PR `agent/<original-id>`, remove the
  original implementation worktree if it still exists so the reviewer can check
  out the PR branch in its own worktree safely

## Step 6a: Bootstrap The Worker

A spawned worker is not considered running until it proves it is operating from
the assigned worktree.

Bootstrap contract:
- the worker's actual process cwd and `pwd -P` must equal `WORKTREE_PATH`
- current branch must equal expected worker branch
- `pwd` must not equal `REPO_ROOT`
- `WORKTREE_PATH` must share `REPO_ROOT`'s canonical common Git directory,
  verified without inherited `GIT_*` repository overrides

For a PR-review worker, branch attestation has two explicit stages:

- **Stage 1 — dispatch bootstrap:** the initial branch is
  `agent/<review-id>`, created by Step 4. This proves isolated dispatch context
  before the reviewer runs any branch preparation.
- **Stage 2 — prepared-head attestation:** after
  `prepare_pr_branch.py` succeeds, require an interim acknowledgement that the
  expected reviewer branch is `agent/<original-id>`, its reported
  `head_commit` equals local `HEAD`, and the same SHA is the remote PR head.
  Substantive review cannot begin until this attestation passes.

Re-review hand-back starts at Stage 2 because Step 7 restores the now-idle
worktree directly to `agent/<original-id>`. Do not reapply the Stage 1 branch
expectation to a transferred re-review worktree.

If the runtime supports interim updates, require a short bootstrap
acknowledgement before continuing. If bootstrap never arrives, or if the worker
reports repo-root / `main` / `master` context, treat dispatch as failed and
renew the heartbeat before releasing the bead.

Codex note: a missing completion event is not a bootstrap failure. Bootstrap
failure requires explicit invalid context or no bootstrap evidence within the
bootstrap window.

## Step 7: Monitor Workers

- Track each worker's issue ID, branch, worktree path, start time, bootstrap
  status, and last progress signal.
- Do not count a lane as occupied until bootstrap succeeds, including a
  continuation's re-bootstrap.
- A missing bootstrap acknowledgement is a dispatch failure, not an
  implementation stall.
- If `main` or the repo-root checkout advances unexpectedly while a worker is
  supposedly active, stop and investigate worktree misbinding before
  dispatching more workers.
- Renew the coordinator stall heartbeat before every mutation and after long external
  checks.

Implementation-worker report contract:
- accepted `Status` values:
  - `completed-pr-opened`
  - `completed-direct-merge-candidate`
  - `blocked-awaiting-coordinator`
  - `invalid-runtime-context`
- required fields include `Branch`, `Head-Commit`, `Branch-Pushed`,
  `Handoff-Path`, `Recovery-State`, and `Resume-Condition`
- `Discovered-Follow-Ups-JSON` and `Blockers-JSON` must be valid JSON arrays
- parse those JSON arrays with a real JSON parser such as `jq`; do not infer
  structure from prose
- `blocked-awaiting-coordinator` is a valid terminal worker outcome, not a
  stall
- implementation workers are single-writer executors; if a worker reports that
  the issue needs multiple code-writing tracks, route it back through the
  coordinator or team-lead flow instead of letting the worker fan out locally

When an implementation worker completes, reconcile from the explicit Worker
Report first, then verify the reported branch / PR state:

1. Parse the report. If `Status` is missing, ambiguous, or contradicts the
   artifacts, do one recovery probe. If it remains ambiguous, treat it as a
   reconciliation failure instead of guessing.
2. Verify any reported side effects:
   - if a PR was reported, confirm it via `gh pr view <number>` or
     `gh pr list --state open --head "agent/<id>"`
   - if `Branch-Pushed=yes`, confirm the remote branch exists
   - if `Head-Commit` was reported, confirm the branch tip matches or explain
     the divergence before mutating Beads state
3. Handle by `Status`:
   - `completed-pr-opened`:
     - require a verified open PR
     - renew heartbeat, then block the original bead and set
       `external_ref=gh-pr:<N>`
     - run the Step 0a dedupe check, then ensure exactly one dedicated
       `pr-review-task` bead exists (create only if the dedupe check finds none)
     - classify discovered items before creating any bead (see Discovery
       classification below)
     - re-run Step 0 immediately so review/merge is prioritized
   - `completed-direct-merge-candidate`:
     - require `Branch-Pushed=yes` and no open PR
     - if `MERGE_QUEUE=yes`: the base rejects direct pushes, so open a PR and
       hand it to the queue without a review bead:
       ```bash
       PR_URL=$(gh pr create --base main --head agent/<id> --label queue-direct \
         --title "<worker title> [<id>]" --body "<worker summary>")
       gh pr merge "${PR_URL}" --squash --auto
       ```
       then renew heartbeat, block the original bead with
       `external_ref=gh-pr:<N>`, and let Step 0b close it on `MERGED`. The
       merge-group CI run is the gate; the `queue-direct` label tells Step 0c
       not to create a review bead
     - otherwise attempt fast-forward merge:
       ```bash
       git fetch origin
       git checkout main && git pull --ff-only
       git merge --ff-only origin/agent/<id>
       git push origin main
       ```
     - on success: renew heartbeat, then `bd close <id> --reason "Simple change merged to main"`
     - on failure: open a PR, set `external_ref`, ensure the review bead, and
       route through the PR-review lane
   - `blocked-awaiting-coordinator`:
     - do not treat this as stalled
     - renew heartbeat
     - triage each `Blockers-JSON` entry against
       `../../../references/decision-autonomy.md` first: a decision-shaped
       entry with no hard gate is decided by the coordinator, recorded as a
       `[decision]` note on the original bead, and the bead is re-dispatched
       with the decision inlined — no blocker bead is created for it
     - convert the remaining (external or hard-gated) `Blockers-JSON` entries
       into blocker beads and wire the original bead to depend on them;
       hard-gated decisions must use the escalation format from
       `../../../references/decision-autonomy.md`
     - classify `Discovered-Follow-Ups-JSON` entries before creating linked work
     - set the original bead to `blocked`
     - preserve recovery state explicitly:
       - if `Recovery-State=branch-pushed`, keep the remote branch for the next
         worker and clean the local worktree only after verifying the remote
         branch exists
       - if `Recovery-State=local-only`, quarantine the worktree under
         `.worktrees/recovery/<id>-<timestamp>` and append the recovery path in
         notes if the schema allows it; never silently delete unrecoverable
         local progress
       - if `Recovery-State=no-code-changes`, no quarantine is needed
   - `invalid-runtime-context`:
     - renew heartbeat, release the bead back to `open`, and clean the worktree
     - do not create blocker beads unless the report includes a separate
       project-level blocker that truly belongs in Beads
4. Only treat the run as stalled if the worker disappears after bootstrap or
   the report remains unusable after the recovery probe.

### Discovery classification

Classify every worker or reviewer discovery before any `bd create`:

| Class | Coordinator action |
|---|---|
| **current-PR correctness** required for accepted outcome | Keep it in the original bead/PR and return it to the implementation/recovery lane; no follow-up bead. |
| **prerequisite blocker** with a different subsystem, trust boundary, architecture decision, or risk class | Create one linked blocker, return through the governing spec/design gate, and serialize the original behind it. |
| **new behavior** or adjacent idea | Create or link spec-first work outside the active outcome; do not expand the PR. |
| **duplicate** symptom/outcome | Link provenance to the existing bead/PR and close or suppress the duplicate. |

Classification precedence: a new trust boundary, subsystem, architecture
decision, or risk class wins over "current-PR correctness" even when required
for the current outcome; it must become a prerequisite blocker.

Search open/recently closed beads, active PRs, and concrete symbols/files before
materializing the last three classes. Only genuinely new work is converted by
the coordinator using sequential creation:

```bash
NEW_JSON=$(bd create "<title>" --description="<details>" -t <type> -p <priority> --json)
NEW_ID=$(echo "${NEW_JSON}" | jq -r '.id // .[0].id')
bd dep add "<original-id>" "${NEW_ID}"
```

Recommended extraction pattern:

```bash
FOLLOWUPS_JSON='<parse fenced JSON block from Worker Report>'
BLOCKERS_JSON='<parse fenced JSON block from Worker Report>'

echo "${FOLLOWUPS_JSON}" | jq -e 'type == "array"' >/dev/null
echo "${BLOCKERS_JSON}" | jq -e 'type == "array"' >/dev/null
```

Reviewer-worker report contract:
- accepted `Status` values:
  - `merged-pr`
  - `merge-queued`
  - `corrections-required`
  - `pushed-review-fixes`
  - `blocked-awaiting-coordinator`
  - `invalid-runtime-context`
- `Review-Actions-JSON`, `Discovered-Follow-Ups-JSON`, and `Blockers-JSON`
  must be valid JSON arrays
- `Head-Commit` and `Reviewed-Head-Commit` are both required; `merged-pr`
  requires them to match the confirmed PR head
- `Reviewer-Identity` and `Risk-Tier` are required so affinity/rotation policy
  is auditable
- `merged-pr` means GitHub merge is complete but Beads closure still belongs to
  the coordinator
- `merge-queued` means the exact reviewed head passed every gate and was handed
  to the base branch's merge queue (`Merge-Performed: queued`); the queue
  integrates and re-tests it, and Step 0b closes on `MERGED`
- `corrections-required` means the independent reviewer left unresolved,
  actionable threads for the implementation/recovery lane and did not author
  semantic code
- `pushed-review-fixes` means the worker pushed code under the exceptional
  reviewer-as-fixer path; a fresh independent reviewer must assess the
  resulting head

When a reviewer worker completes:
- if it reports `merged-pr`, confirm the PR is merged, then renew the heartbeat,
  close the review and original beads, and run the post-closure branch/worktree
  cleanup in Step 0b (delete the `ORIGINAL_ID` PR branch, remove the
  `REVIEW_ID` worktree, then delete the original and temporary review local
  branches) — the reviewer left the PR branch in place on purpose
- if it reports `merge-queued`, run the Step 0b GraphQL projection and confirm
  `merge_queue_entry` is non-null, then renew the heartbeat, add label `merge-queued` to the review
  bead, remove `review-running`, and keep both beads blocked; Step 0b performs
  closure and cleanup when the PR reports `MERGED`; missing/error metadata fails
  closed, and an absent entry never authorizes `--force-rebase` by itself
- if it reports `blocked-awaiting-coordinator`, keep the review bead blocked
  and create any follow-up merge-blocker bead from the structured report if one
  does not already exist
- if it reports `corrections-required`, keep the one canonical review bead
  blocked, remove `review-running`, and return current-PR correctness to the
  original author when resumable or a recovery worker on the same PR branch.
  Do not create another review bead. Use this explicit sequential transition;
  it temporarily detaches the original from the review gate so correction work
  is runnable, then restores the same gate:

  ```bash
  # Reviewer agent has exited; its worktree may be transferred sequentially.
  bd dep remove <original-id> <review-id>
  bd update <original-id> --status in_progress

  # Dispatch beads-worker in the now-idle PR worktree with:
  REVIEW_CORRECTION_MODE=yes
  EXISTING_PR_NUMBER=<pr-number>
  REVIEW_BEAD_ID=<review-id>
  CORRECTION_THREADS_JSON='<unresolved-current-correctness-threads>'

  # After completed-pr-opened confirms the existing PR head was pushed:
  bd update <original-id> --status blocked
  bd dep add <original-id> <review-id>

  # Correction-to-re-review hand-back; the correction agent has exited.
  git -C <review-worktree> status --porcelain  # must be empty
  git -C <review-worktree> fetch origin agent/<original-id>
  git -C <review-worktree> checkout -B agent/<original-id> origin/agent/<original-id>
  ```

  The correction-mode handler takes precedence over generic
  `completed-pr-opened` reconciliation: reuse the existing PR/review bead, do
  not create either again. Never run reviewer and correction agents in the
  transferred worktree concurrently. Before redispatching the reviewer, require
  an empty worktree; the expected reviewer branch is `agent/<original-id>`.
  Let the reviewer preparation helper verify and
  push the exact prepared head. If the hand-back is dirty or the branch cannot
  be restored, preserve the recovery state or remove and recreate a fresh
  review worktree from the remote PR head. On correction failure, preserve
  recovery state, restore the original→review dependency, and keep both beads
  blocked before retrying.

  After correction, prefer the same
  independent reviewer for an exact-head recheck unless the risk tier requires
  rotation. Track review state in one replace-in-place note on the original
  bead (never append duplicate blocks):

  ```text
  [review-cycle]
  substantive_reopenings=<integer>
  reviewer_identity=<runtime-id-or-login>
  risk_tier=<high|standard|low>
  reviewed_head_sha=<sha>
  [/review-cycle]
  ```

  At two
  substantive reopenings, apply the allocation contract's two-correction
  checkpoint: the coordinator updates the original bead's structured
  acceptance criteria/failure matrix and reruns dispatch readiness for the same invariant, or
  split and spec-gate a new subsystem/trust-boundary/risk-class prerequisite.
  For a prerequisite split, keep the PR open and mark it draft when supported,
  remove any partial boundary implementation from its diff, then rebase and
  re-review only after the prerequisite lands.
- if it reports `pushed-review-fixes`, keep the review bead blocked and rely on
  the PR-review lane to dispatch a fresh independent reviewer for the new head
- if it reports `invalid-runtime-context`, release the review bead back to
  `blocked`, remove `review-running`, and retry later after environment repair

If a worker fails bootstrap, or stalls after bootstrap:
- log the failure
- in Codex, send one interrupt heartbeat/status request before release
- renew heartbeat
- release the issue:
  - `pr-review-task`: `bd update <id> --status blocked --remove-label review-running --json`
  - otherwise: `bd update <id> --status open --json`
- clean up the worktree

## Step 8: Adaptive Polling And Loop

Use an **event-driven** primary loop. The full cleanup pass runs at startup;
afterward prefer targeted state refreshes caused by real events.

Immediate recheck triggers:
- worker completion
- worker bootstrap failure
- slot freed
- PR discovered, merged, or closed
- review bead created or deduped

Polling modes (wake cadence and its cost rationale are canonical in
`runtime-and-safety.md` → "Orchestrator Wake Cadence"; do not restate the
numbers here):
- active mode: whenever near-term work exists (an active worker, a dispatchable
  `pr-review-task`, a PR cooldown counting down). Wait for events when the
  runtime supports it, but never let a gap between wakes exceed the runtime's
  cache-TTL fallback.
  Tighten to 1-2 minutes only when an event is imminent (a cooldown about to
  expire, a lane just freed). A cache-hit poll is cheap, not free.
- no-progress frontier: once a sweep finds nothing dispatchable, widen to the
  60-minute cadence and 3-consecutive-no-op-wake stop condition in
  `runtime-and-safety.md`. Each wake in this mode still runs the full safety
  sweep covering ready work, PR state, released dependencies, stalled claims,
  and tracker health — the sweep's content doesn't shrink, only its cadence
  widens.

Decision sweep: on every transition into the no-progress frontier — and at
least once per session even if it's never reached — run the Coordinator
Decision Sweep from `../../../references/decision-autonomy.md` over blocked
and human-flagged beads, as part of that wake's safety sweep, **before**
counting the wake as a no-op. Decision debt is dispatchable work: a
swept-and-decided bead re-enters the ready pool this cycle, so re-run Step 0
after a sweep that unblocked anything (this also resets the no-op counter — see
`runtime-and-safety.md`). A coordinator at the no-progress frontier with
decision-shaped blocked beads is not actually at the frontier; it is avoiding a
decision.

Prefer low-cost evidence over narrative heartbeats:
- worktree exists
- branch moved
- new commit
- PR state changed

Batch each poll into **one** composite command that emits a compact summary,
instead of separate `bd`/`git`/`gh` invocations whose full output each lands in
context:

```bash
{ bd list --status=in_progress --json | jq -c '[.[] | {id, assignee}]'
  git for-each-ref --format='%(refname:short) %(objectname:short)' 'refs/remotes/origin/agent/*'
  gh pr list --state open --json number,headRefName,mergeStateStatus \
    --jq 'map({number, headRefName, mergeStateStatus})'
} 2>&1
```

After each cycle, return to Step 0.

## Progress Report

Render a progress report only when bead status actually changed since the last
report. Never derive the report from an unfiltered `bd list --json`; use
filtered queries plus targeted checks of the ids dispatched this cycle.

1. Query active work and the ids you dispatched or reconciled this cycle:
   ```bash
   bd list --status=in_progress --json | jq -c '[.[] | {id, title, status, assignee}]'
   ```
   Then `bd show <id> --json | jq '{id, status, assignee, external_ref}'` for
   each recently dispatched / reconciled id to confirm closures, merges, or
   status moves.
2. If nothing changed, skip the report and proceed to Step 0.
3. If something changed, print the table once. For each closed bead, confirm
   the merge route:
   ```bash
   gh pr view <number> --json state,mergedAt   # PR-based merges
   ```
   The "Merged to main?" column shows `Yes`, `No`, or `—`.

```
═══════════════════════════════════════════════════════════════
  Beads Coordinator — Progress Report
  <TZ=Asia/Singapore date '+%Y-%m-%d %H:%M SGT'>
═══════════════════════════════════════════════════════════════

| # | Bead ID   | Title                    | Status      | Merged to main? |
|---|-----------|--------------------------|-------------|-----------------|
| 1 | beads-042 | Fix login redirect       | closed      | Yes             |
| 2 | beads-045 | Add pagination           | in_progress | —               |

Total closed this session: 1 / 10 open at start
═══════════════════════════════════════════════════════════════
```

### Not-dispatched disclosure (required)

Every progress report and the final report must list non-dispatched candidates
**by id**, each under its own heading. An aggregate adjective sentence ("the
rest are incomplete, overlapping, owner-gated, or unsafe") is not a report; it
is the silence this contract exists to prevent. Print the heading with `none`
when a set is empty.

Readiness-rejected (`needs-shaping`):

| Bead ID | Parent epic | Missing packet items | Shaping this run |
|---------|-------------|----------------------|------------------|
| bu-abcd | bu-7exe4 | acceptance_criteria, design | dispatched |
| bu-efgh | bu-7exe4 | acceptance_criteria | epic cap reached |

Cohesion-deferred:

| Bead ID | Overlaps with | Shared allocation signals |
|---------|---------------|---------------------------|
| bu-ijkl | bu-mnop (in_progress) | module/interface, tests/fixtures |

Other non-dispatched candidates (owner-gated, foreign-assignee, externally
dependent) are listed the same way: id, one-phrase reason, and the concrete
unblock condition.

### Final report (required when the loop stops)

The final report is the only record the owner and the next session get, so
write it for a reader who has not seen this run. On top of the last progress
report and the not-dispatched disclosure above, state:

- why the loop stopped: frontier stop, owner stop, or tracker fault, with the
  evidence for it
- every `[decision]` recorded this run: bead id plus the one-line record
- every open hard-gate escalation: bead id, the question, your recommendation,
  and the default that applies if the owner does not answer
- recovery state for the next session: quarantined worktrees, unreconciled
  lanes, and beads left `in_progress`, each by id

Print each heading with `none` when it is empty.
