# Coordinator loop

Small allocation/recovery controller for persistent outcome owners. The shared
[delivery model](../../../references/delivery-model.md) owns execution units,
branch/session choices, packets, evidence and per-member closure. This file owns
controller actions; a looper applies the same transitions to its own package.

## Preflight

Read target AGENTS/CLAUDE and relevant craft guidance; inspect dirty state. Run
cleanup once at startup, consume its findings, and preserve foreign/unpublished
work. A tracker fault is an actual failed read/write after the known-errors
retry, not every doctor warning; never reset a shared tracker to repair it.
Register a unique actor using `scripts/coordinator_registry.py new-id` and
`register --startup`. Pass `--actor "$COORDINATOR_ID"` on lifecycle writes.

Detect integration base, required checks and queue policy. Failure to read
protection policy is unknown, never permission for a direct push. Preserve
repository-required gates and owner merge holds. Native runtime dispatch only;
do not launch assistant CLIs from shell.

## Owner merge hold

The exact label `owner-merge` reserves an OPEN PR for the owner. This overlay
outranks normalizer v1 recommendations, queue-direct, and earlier readiness or
inventory observations. Resolve `OWNER`/`REPO` to the target repository; load
these Bash functions in the acting shell. They perform reads only. The projection
validates every page before emitting a verdict or inventory; missing, partial,
or malformed evidence fails closed.

```bash
# owner-merge-procedure: begin
owner_merge_projection() {
  jq -er --arg mode "$1" '
    def connection: if $mode == "guard" then .data.repository.pullRequest.labels else .data.repository.pullRequests end;
    if type != "array" or length == 0 then error("missing pages") else . end
    | if all(.[]; (.errors == null or (.errors | type == "array" and length == 0))
        and (connection | type == "object")
        and (connection.nodes | type == "array")
        and (connection.pageInfo.hasNextPage | type == "boolean")
        and (connection.pageInfo | has("endCursor"))
        and (connection.pageInfo.endCursor == null or (connection.pageInfo.endCursor | type == "string")))
      and all(.[0:-1][]; connection.pageInfo.hasNextPage == true
        and (connection.pageInfo.endCursor | type == "string" and length > 0))
      and (.[-1] | connection.pageInfo.hasNextPage == false)
      and (([.[0:-1][] | connection.pageInfo.endCursor] | unique | length) == (length - 1))
      then . else error("incomplete pages") end
    | if $mode == "guard" then
        if all(.[]; (.data.repository.pullRequest.state | IN("OPEN", "MERGED", "CLOSED"))
            and all(connection.nodes[]; type == "object" and (.name | type == "string")))
          and ([.[].data.repository.pullRequest.state] | unique | length) == 1
        then if .[0].data.repository.pullRequest.state != "OPEN" then "terminal-pr"
          elif any(.[] | connection.nodes[]; .name == "owner-merge") then "held-for-owner"
          else "unheld" end
        else error("invalid PR labels/state") end
      else
        if all(.[] | connection.nodes[]; type == "object" and .state == "OPEN"
            and (.number | type == "number" and . > 0 and floor == .)
            and (.title | type == "string"))
        then if $mode == "branch" then
          if all(.[]; (connection.nodes | length) == 0) then "unheld"
          else "existing-open-pr" end
        else [.[] | connection.nodes[] | {number, title,
          ci_state: (if (.statusCheckRollup | type) == "object"
            and (.statusCheckRollup.state | IN("SUCCESS", "FAILURE", "PENDING", "ERROR", "EXPECTED"))
            then .statusCheckRollup.state else "UNKNOWN" end)}]
          | if length == 0 then "Held PRs: none"
            else .[] | "Held PR: " + tojson end
        end
        else error("invalid held PR inventory") end
      end'
}

owner_merge_guard() (
  set -o pipefail
  local pages verdict
  pages=$(gh api graphql --paginate --slurp \
    -f query='query($owner:String!,$name:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$name){pullRequest(number:$number){state labels(first:100,after:$endCursor){nodes{name} pageInfo{hasNextPage endCursor}}}}}' \
    -F owner="$OWNER" -F name="$REPO" -F number="$1") \
    || { printf '%s\n' 'owner-merge: unavailable' >&2; exit 1; }
  verdict=$(printf '%s\n' "$pages" | owner_merge_projection guard) \
    || { printf '%s\n' 'owner-merge: unavailable' >&2; exit 1; }
  case "$verdict" in
    unheld) exit 0 ;;
    held-for-owner) printf '%s\n' "$verdict" >&2; exit 3 ;;
    *) printf '%s\n' "$verdict" >&2; exit 1 ;;
  esac
)

owner_merge_inventory() (
  set -o pipefail
  local pages report
  pages=$(gh api graphql --paginate --slurp \
    -f query='query($owner:String!,$name:String!,$endCursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$endCursor,states:OPEN,labels:["owner-merge"]){nodes{number title state statusCheckRollup{state}} pageInfo{hasNextPage endCursor}}}}' \
    -F owner="$OWNER" -F name="$REPO") \
    || { printf '%s\n' 'Held PRs: unavailable/partial'; exit 1; }
  report=$(printf '%s\n' "$pages" | owner_merge_projection inventory) \
    || { printf '%s\n' 'Held PRs: unavailable/partial'; exit 1; }
  printf '%s\n' "$report"
)
owner_merge_branch_guard() (
  set -o pipefail
  local pages verdict
  pages=$(gh api graphql --paginate --slurp \
    -f query='query($owner:String!,$name:String!,$branch:String!,$endCursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$endCursor,states:OPEN,headRefName:$branch){nodes{number title state} pageInfo{hasNextPage endCursor}}}}' \
    -F owner="$OWNER" -F name="$REPO" -f branch="$1") \
    || { printf '%s\n' 'branch PRs: unavailable' >&2; exit 1; }
  verdict=$(printf '%s\n' "$pages" | owner_merge_projection branch) \
    || { printf '%s\n' 'branch PRs: unavailable' >&2; exit 1; }
  if [ "$verdict" = unheld ]; then exit 0; fi
  printf '%s\n' 'existing-open-pr: route through existing PR lane' >&2
  exit 3
)
# owner-merge-procedure: end
```

Immediately before **every** enqueue, merge, or close-PR attempt, run
`owner_merge_guard "$PR_NUMBER" && <action>` in the same shell. Exit 3 means
`held-for-owner`; any other nonzero means unavailable/terminal: stop that action
and freshly reconcile state. This includes reviewer direct `gh pr merge`,
`--auto`, a GraphQL `enqueuePullRequest` fallback, queue-direct initial
enrollment, re-enrollment, and every retry. A failed attempt does not authorize
the next one: recheck immediately before its fallback/retry. For close, comment
first, then guard again immediately before `gh pr close`. Preserve all existing
exact-head, CI, independence, cooldown and dedupe gates; a guard exit 0 alone
never authorizes the action. Label read and action are separate remote requests:
there is a residual TOCTOU race, with no atomic server guarantee.

Before each direct fast-forward/base push or queue-direct creation, run
`owner_merge_branch_guard "$HEAD_BRANCH" && <base-push-or-create>` to freshly
enumerate **all** existing OPEN PRs for that exact branch. Errors stop the push.
If one exists, route through its existing PR lane and run the fresh hold guard
there; never bypass it with a base push or duplicate PR. Only a successfully
verified empty branch-PR collection permits the direct path.

For OPEN held PRs, review and already-authorized corrections remain allowed;
do not enqueue, merge, or close them. Keep one canonical original/review
relationship, release `review-running` when review ends, and record
`held-for-owner` without creating a hold-only blocker bead or repeatedly
redispatching the same head solely to attempt merge. Preserve actionable
findings and correction handoffs; exceptional pushed fixes still require a
fresh independent reviewer. Recompute the hold next fresh cycle: label removal
resumes normal rules without a persistent Beads hold latch or extra reset.
An already queued/auto-merge-armed held PR is reported with no further fleet
action; automatic dequeue/cancellation is outside this procedure. Actual
owner-performed MERGED/CLOSED state still follows terminal reconciliation,
even if the label remains; never freeze truthful Beads closure/re-triage.

Run `owner_merge_inventory` independently on **every cycle**, including frontier
and final reports, across all ownership/branch prefixes. Show every number,
title and actual CI state; missing CI is UNKNOWN. Successful emptiness is
`Held PRs: none`; failed/partial enumeration is unavailable/partial, never
none or green. Listing a foreign PR grants no ownership or mutation rights.

## Step 0: Reconcile changed delivery state

This controller is the sole PR-state mutator for its dispatched work. A looper
is that controller for its own package; the review session owns only its review
bead. Read `scripts/normalize_pr_review_state.py` (module docstring defines the
fail-closed contract) and consume its compact `beads-pr-review-normalization/v1`
output. Cleanup is report-only. A partial/fatal/manual-triage result authorizes
no mutations. Freshly check assignee and PR state immediately before each actual
mutation. Cached findings may guide what to inspect, never authorize closure.

Canonical association is PR → anchor → members. Before creating a review bead,
dedupe by anchor/PR, retain the oldest valid parsed creation chronology; invalid
chronology is partial manual triage. Never copy `external_ref` to members or
review beads. Review bead has `pr-review-task,pr-review`, structured acceptance
for independent findings/evidence/current-head readiness, and a link to anchor.
Only anchor depends on that review bead. Mark an anchor awaiting an open PR with
`pr-review` so the current lifecycle inventory distinguishes it from historical
PR associations. After terminal reconciliation, remove that active lifecycle label
when it is no longer waiting on the PR. A partial outcome can retain its historical
external_ref while remaining blocked on real unfinished work; it must not look like
an unreviewed open delivery forever. Reuse this record and reviewer for
corrections. `review-running` plus guarded acquisition prevents duplicate reviewers.
After Step 1 admits a review action, acquire its review record. Native `--claim`
refuses blocked beads: read the assignee and acquire an unassigned blocked review
using both guards, then verify command success. Step 0 does not start review jobs:

```bash
bd --actor "$COORDINATOR_ID" update <review-id> --if-assignee '' \
  --if-status blocked --assignee "$COORDINATOR_ID" --status in_progress \
  --add-label review-running --json
```

For a review record already held by this controller, keep that assignee and use
its guarded blocked→in_progress transition. Exit 13 means another actor won;
dispatch nothing. An independent review looper uses its own actor for this record,
without acquiring the implementation package.

Query actual queue state rather than inferring it from CI or an armed auto-merge:

```bash
PR_QUEUE_JSON=$(gh api graphql \
  -f query='query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){state mergedAt autoMergeRequest{enabledAt} mergeQueueEntry{position enqueuedAt} labels(first:20){nodes{name}}}}}' \
  -F owner="${OWNER}" -F name="${REPO}" -F number="${PR_NUMBER}" \
  | jq -e '. | select((has("errors") | not) or .errors == null or ((.errors | type) == "array" and (.errors | length) == 0)) | .data.repository.pullRequest | select(type == "object" and has("state") and (.state | type) == "string" and has("mergedAt") and (.mergedAt == null or (.mergedAt | type) == "string") and has("autoMergeRequest") and (.autoMergeRequest == null or ((.autoMergeRequest | type) == "object" and (.autoMergeRequest | has("enabledAt")) and (.autoMergeRequest.enabledAt == null or (.autoMergeRequest.enabledAt | type) == "string"))) and has("mergeQueueEntry") and (.mergeQueueEntry == null or ((.mergeQueueEntry | type) == "object" and (.mergeQueueEntry | has("position")) and (.mergeQueueEntry.position | type) == "number" and (.mergeQueueEntry | has("enqueuedAt")) and (.mergeQueueEntry.enqueuedAt | type) == "string")) and has("labels") and (.labels | type) == "object" and (.labels.nodes | type) == "array" and all(.labels.nodes[]; type == "object" and (.name | type) == "string")) | {state, mergedAt, auto_merge_armed: (.autoMergeRequest != null), merge_queue_entry: (.mergeQueueEntry | if . == null then null else {position, enqueuedAt} end), labels: [.labels.nodes[].name]}')
```

The `merge_queue_entry` is non-null only for confirmed queue membership.
Auto-merge armed with no entry is a wait. Queue metadata alone never proves why
an entry disappeared, nor authorizes rebase or retry. Check complete fresh labels
using the owner hold procedure before every action.

| Observed state | Reconcile |
|---|---|
| MERGED to actual delivery base | Verify landed content and each member acceptance; apply closure-plan, close review as review-complete; keep incomplete members/outcome open. A merge to an intermediate integration branch is accumulation only. |
| CLOSED unmerged | Preserve branch/worktree and findings; owner re-triages same package, no success closure. |
| OPEN owner-merge | Review/corrections allowed; no merge/enqueue/close. Preserve `Merge-Performed: no`, `PR-Closed: no`; no hold-only blocker or repeated same-content review solely to merge. |
| OPEN queued/armed | Retain owner and event wait; refresh queue/CI when its state changes. |
| OPEN corrections-required | Send findings directly to existing implementation owner; keep same claims/PR/review. |
| OPEN review-ready | Resume independent reviewer with changed content and prior findings. |

## Step 1: Admit from one review-first queue

Step 0 produces a compact normalization envelope. Save that existing result to
`REVIEW_STATE_FILE`, then reuse it with the current tracker snapshot:

```bash
uv run scripts/rank_ready_work.py --repo-root "$REPO_ROOT" \
  --coordinator "$COORDINATOR_ID" --review-state-file "$REVIEW_STATE_FILE"
```

The module docstring owns `beads-work-queue/v2`. Dispatch only from `queue` and
its `admission` decision, never diagnostic `candidates`, raw `bd ready`, or a
mode-specific side queue. Missing/partial/inconsistent review evidence permits
no dispatch. Refresh changed state before acting; no extra whole-fleet scan is
needed just to re-read a verdict. Priority ordering occurs before the output
limit, so a candidate cutoff cannot hide review work. A failed claim/race refreshes
admission before choosing another item.

Reviews outrank ordinary new implementation, then corrections/resumes retain their
owner; P0 keeps emergency precedence. Once higher-class actions are admitted or
running, spare slots can advance disjoint work. Runtime safety owns active-slot,
WIP, parked-owner and native-thread rules; claims and sessions survive waits.
An author before review handoff remains running work when normalization binds
the exact discovered PR/branch to a stable current head and admission confirms
the same live author and clear package gates, independently of CI status. Keep
the missing-wiring advisory visible; it does not create an unstarted review job.
Absent or conflicting evidence keeps the existing reconciliation barrier.

A prerequisite shared by several outcomes gets one owner. A hard hold gates its
dependent work, rather than demanding universal idleness. Allow bounded disjoint
spillover only when no useful higher-class action waits and both execution and WIP
capacity allow it; retain concrete resume events and checkpoint priority.
An all-children-closed candidate is not proof of accepted completion: check original
observable acceptance, including required activation/proof.

## Step 2: Form the next cohesive package

Use the current allocation packet, source and applicable governing contract. Several
existing cohesive beads can share one package without another wrapper issue.
Independent outcomes keep separate rollback/ownership. Inspect other live claims
and branches before assigning overlapping interfaces. A different hard dependency,
authority or risk boundary remains explicit; it is never hidden by bundling.
If the packet is missing a material decision or source assumption, return that
specific gap to shaping. Do not fan out a fresh research hierarchy for every task.

Read `references/epic-coordination.md` only for programme allocation or shared
prerequisites. No Team Lead/subworker mini-coordinator tier is required.

## Step 3: Claim, publish current packet and isolate

For one bead, retain the native atomic claim. For several, write a
`beads-delivery/v1` manifest and use `../../../scripts/delivery_package.py`
claim with a durable recovery journal. It validates membership, external gates
and ownership, persists anchor/member association, and rolls back newly acquired
claims on partial failure. Do not dispatch until the complete claim succeeds.
Journal paths and the current packet must be recorded on the anchor for recovery.
The manifest owner is the lifecycle actor; notes also identify implementation
session and resume locator. Never bulk-claim future outcome descendants.

Create one worktree bound to the actual repository:

```bash
(cd "${REPO_ROOT}" && bd worktree create ".worktrees/parallel-agents/${ISSUE_ID}" --branch "agent/${ISSUE_ID}")
```

Recover an existing branch/worktree rather than overwriting it. Publish useful
commits before a wait/preemption. Never edit or move the shared root checkout.

## Step 4: Continue the accountable owner

Dispatch absolute worker SKILL path, anchor `ISSUE_ID`, `WORKTREE_PATH`,
`REPO_ROOT`, optional `DELIVERY_FILE`, current packet locator and a short current
instruction. Do not inline full issue JSON or archives. Continue the existing
Claude session/Codex thread before launching a new owner. Bootstrap verifies
actual cwd, shared Git identity, branch and manifest anchor. A fresh launch is
recovery with explicit actor/session handoff; it is not a warm continuation.

The owner can implement, test, diagnose, correct and push within accepted scope
without narrow coordinator permissions per edit. Trust/architecture/scope changes
still follow their governing gates. The coordinator does not relay each finding:
reviewer and owner communicate directly; state changes return here for lifecycle.

## Step 5: Review and correct

On a worker handoff, park the anchor as blocked while keeping all member claims.
Create/reuse its review record, normalize the changed state, then use the shared
admission queue for its independent review. No implementation-WIP vacancy is needed
for review/correction, only an execution slot. Set the admitted record in_progress
immediately before its job starts; return it to blocked when that execution ends.
Do not park a still-running test/correction process to manufacture free capacity.


One independent reviewer per package, normally the same session through rechecks.
Create its worktree on `review/<review-id>` fetched from the published package
head. Keep the implementation worktree. Never transfer the author's branch
between review and implementation sessions. The reviewer checks substantive
correctness and acceptance, reports every evidenced finding, and binds the
current-head receipt to new or explicitly reused content evidence. Repository
merge/CI/owner authorization is checked separately at the action.

`corrections-required` returns directly to the existing implementation owner with
`REVIEW_CORRECTION_MODE=yes`, existing PR and review IDs, and findings. For anchor
correction work only, detach anchor→review dependency while retaining all claims;
restore the same relation after push. Member hard gates remain. The same owner
amends current acceptance/failure matrix when same-seam corrections reveal a bad
assumption. A new trust boundary/subsystem/architecture choice follows its real
spec/authority gate, with a separately owned prerequisite only when needed.
No new review bead, correction bead or coordinator dispatch cascade. When findings
are returned, persist the typed review receipt from the independent report as in
[delivery evidence](../../../references/delivery-model.md#evidence-and-review),
then park the review. The queue admits correction to the existing owner; only then
set the anchor in_progress. After correction push, park it again and let the changed
head invalidate the old receipt for the same review record. Admission is not
permission to seize a foreign claim or bypass an actual prerequisite.

A same-head ready/held/queued receipt does not dispatch another source review.
Step 0 resumes its fresh merge/CI/owner/queue checks on the actual event and may
perform an already-authorized merge using the independent content evidence. If a
same-head CI failure needs author repair, record current check evidence and set
the receipt disposition to corrections; the queue resumes the same owner. For
fresh reviewer execution after an actual environment/assumption change, preserve
the prior independent report and clear only the operative review_receipt, then
readmit that reviewer through the queue. The shared evidence contract owns these
event transitions; a wake or pending CI alone never invalidates source review.
Never treat cached source evidence as current merge authorization.

## Step 6: Land or wait with explicit state

Use repository-required independent review, checks, queue and current hold guard.
Direct fast-forward is limited to repository-permitted trivial work with the
required local gate and independent verdict; branch guard must prove no existing
PR. Queue-direct never bypasses a required review. An integration-branch merge
cannot close main-delivery acceptance. An unchanged source verdict can be reused;
current queue/label/environment facts are refreshed when authorizing an action.

Ordinary CI failure belongs to the implementation owner. External blockers name
the event and dependent scope. Do not manufacture a new blocker bead for a wait
already represented in CI, review or an existing prerequisite. Classify discoveries:
current-PR correctness stays here; prerequisite blocker gets one canonical owner;
new behavior goes outside accepted scope; duplicate links existing provenance.
Create the follow-up first, then wire one edge by class. Only a prerequisite
blocker serializes the original:

```bash
bd --actor "$COORDINATOR_ID" dep add <original-id> <new-id>                       # prerequisite blocker
bd --actor "$COORDINATOR_ID" dep add <new-id> <original-id> --type discovered-from  # new behavior / adjacent idea
```

`discovered-from` records provenance without blocking either bead; the default
`blocks` type on a new-behavior follow-up would park the original behind it.
From worker reports, `Blockers-JSON` entries take the blocker edge and
`Discovered-Follow-Ups-JSON` entries take `discovered-from`, whatever their
`depends_on` says. A duplicate creates nothing: link the existing bead to the
original with `discovered-from`.

## Step 7: Per-member reconciliation and safe cleanup

Require structured member results and evidence. `closure-plan` checks each member
independently; a merged package never implies all members or the parent outcome
complete. Close only accepted, verified and actually landed members as holder;
record unmet acceptance in place for the rest. Close a foundation-only member
when that is its actual acceptance, leaving the larger feature visibly partial.

Before release or session-loss recovery, use the durable journal and guarded
assignee checks; never clear foreign or pre-existing claims as compensation.
A failed bootstrap with useful work gets recovery, not deletion. Keep published
branches and local-only work. Remove worktrees/branches only after verifying
clean state, no unpublished commits, no pending correction and completed/handoff
recovery receipts. Never delete unrelated stashes or worktrees.

## Step 8: Event-driven continuation and report

Wake on review/CI/dependency/owner events, with the bounded fallback and no-progress
frontier in `runtime-and-safety.md`. Renew registry using
`coordinator_registry.py register`; on exit use `coordinator_registry.py deregister`.
Do not rescan the fleet or re-run broad verdicts just because a session woke up.
A nonempty diagnostic candidates list is not runnable admission. If queue is empty
because execution/WIP capacity or a real gate prevents work, resume on the named
event with bounded fallback, rather than repeatedly waking to inspect the same list.
At a frontier, classify your actual blocked work under decision-autonomy; resolve
engineering choices, preserve owner hard gates, then stop at the bounded no-op limit.

Report meaningful state changes: accepted outcome and remaining acceptance,
package anchor/members/PR, merged source vs verified runtime vs partial/planning,
wait type and resume event, exact claims/session and recovery paths. Include fresh
`owner_merge_inventory`, including frontier/final reports. List evaluated but
undispatched IDs with their concrete reason; do not dump the whole backlog.
Final report explains stop reason, delivered and partial outcomes, hard gates,
published/local recovery work and registry/claim disposition. Activity counts
never substitute for accepted outcome completion.
