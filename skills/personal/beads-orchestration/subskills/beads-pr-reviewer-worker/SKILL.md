---
name: beads-pr-reviewer-worker
description: Use when a coordinator dispatches a dedicated `pr-review-task` bead for an open GitHub PR that needs review follow-up, thread triage, merge assessment, or retry handling in an isolated worktree.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-11-06"
compatibility: Requires a Beads-backed git repository with git worktrees, git, bd, jq, gh, and python3 available, plus authenticated GitHub access and network access for review, push, and merge operations.
---

# Beads PR Reviewer Worker

## Overview

Process exactly one dedicated `pr-review-task` bead tied to a GitHub PR:
- resolve the original implementation bead and canonical PR,
- review the exact head's diff, its unresolved threads, and its tests,
- leave actionable, resolvable findings for the implementation/recovery lane,
- decide whether the PR is mergeable,
- report the outcome in a machine-readable form for the coordinator.

You may mutate GitHub review state and merge or close the PR when warranted.
You do **not** mutate Beads lifecycle state.

## Use This Skill When

- the coordinator dispatches one dedicated `pr-review-task` bead
- you are given `ISSUE_ID`, `WORKTREE_PATH`, and `REPO_ROOT` (plus an optional
  2-4 line summary/acceptance-criteria excerpt from the coordinator)
- the PR already exists and needs review follow-up, retry triage, or merge
  evaluation

Typically invoked by [`../beads-coordinator/SKILL.md`](../beads-coordinator/SKILL.md),
not directly by users.

## Context

| Variable | Description |
|---|---|
| `ISSUE_ID` | Assigned PR-review bead ID |
| `WORKTREE_PATH` | Dedicated isolated git worktree for this worker |
| `REPO_ROOT` | Main repository root for read-only orientation |
| `ISSUE_JSON` | (Optional/legacy) Full issue JSON if inlined by an older coordinator. When absent, self-fetch with the projected `bd show` in Phase 1 |
| `MERGE_QUEUE` | (Optional) `yes`/`no` from the coordinator's preflight. When absent, trust `merge_queue` in the merge-readiness helper output |

## Non-Negotiable Boundaries

- All git operations happen inside `WORKTREE_PATH`, never inside `REPO_ROOT`.
- Never `cd` to `REPO_ROOT`. Use absolute paths or `git -C "${REPO_ROOT}"`.
- Do not run `bd create`, `bd update`, `bd dep add`, or `bd close`.
- Assume the coordinator already claimed the review bead and applied the
  `review-running` lock. Do not try to claim or release that lock yourself.
- No hidden parallel writers; on `LANE-CONTINUATION`, redo bootstrap + attestations.
- Preserve reviewer independence: do not author semantic corrections on the
  head being reviewed. The original author or a recovery worker owns fixes.
- Do not require a synthetic "no issues detected" marker thread in order to
  merge. Audit comments are optional, not merge gates.

## Optional Project-Level Craft-And-Care Gate

Before the first finding, apply
[`../../references/craft-and-care-gate.md`](../../references/craft-and-care-gate.md):
discover a repo-owned `craft-and-care` skill, read it once if present, use it
as the bar for findings and merge readiness, and run its final standards pass
against the diff before handoff.

## Bundled Helpers

Use the bundled helpers for deterministic operations (prepare, reply, resolve,
and comment mutate GitHub or push), each by the loaded package's absolute path:

- [`scripts/resolve_review_context.py`](scripts/resolve_review_context.py)
  Resolves the original bead, PR number, repo, branches, and current PR state.
  Every `bd show` response it uses must contain exactly one record whose `id`
  matches the requested ID; empty, multiple, non-record, or foreign responses
  fail closed rather than selecting a first record.
- [`scripts/prepare_pr_branch.py`](scripts/prepare_pr_branch.py)
  Checks out `review/<review-id>` at the published package head and probes
  merge compatibility. Never rewrites the implementation branch; conflicts
  return to its existing owner.
- [`scripts/list_review_threads.py`](scripts/list_review_threads.py)
  Lists review threads with full pagination and returns unresolved-thread
  details in JSON.
- [`scripts/evaluate_merge_readiness.py`](scripts/evaluate_merge_readiness.py)
  Computes merge gates in one place, failing closed when required checks cannot
  be verified, and reports whether the base branch sits behind a merge queue
  (`merge_queue`, `merge_command`).
- [`scripts/discover_quality_gates.py`](scripts/discover_quality_gates.py)
  Discovers likely lint, typecheck, and test commands from common project
  manifests when project docs do not name them explicitly.
- [`scripts/reply_to_review_thread.py`](scripts/reply_to_review_thread.py)
  Adds an idempotent reply to an existing review thread.
- [`scripts/resolve_review_thread.py`](scripts/resolve_review_thread.py)
  Resolves a review thread idempotently.
- [`scripts/create_inline_review_comment.py`](scripts/create_inline_review_comment.py)
  Adds an idempotent line-level review comment for a newly discovered issue.

Use the bundled references in `references/` when you need the exact GitHub
review-thread operations or the failure protocol:

- [`references/thread-operations.md`](references/thread-operations.md)
- [`references/failure-protocol.md`](references/failure-protocol.md)
- [`references/evaluation.md`](references/evaluation.md)

## Workflow

### Phase 1: Bootstrap

1. `cd "${WORKTREE_PATH}"`.
2. Fetch full issue details when `ISSUE_JSON` was not inlined by the coordinator:

```bash
ISSUE_JSON=$(bd show "${ISSUE_ID}" --json \
  | jq -e --arg id "$ISSUE_ID" '
      if type == "array" and length == 1 and .[0].id == $id then
        .[0] | {id, title, status, labels, external_ref, description, acceptance_criteria, design, notes}
      else error("expected exactly the requested bead") end')
```

Project to those fields only (`../../references/token-efficiency.md`); the
full record carries history you do not need.

3. Verify runtime context:

```bash
ASSERT_WORKER_CONTEXT="<loaded beads-worker package>/scripts/assert_worker_context.py"
python3 "${ASSERT_WORKER_CONTEXT}" \
  --worktree-path "${WORKTREE_PATH}" \
  --repo-root "${REPO_ROOT}" \
  --issue-id "${ISSUE_ID}" \
  --review-id "${ISSUE_ID}" \
  --current-path "$(pwd -P)"
```

Resolve the helper from the loaded `beads-worker/SKILL.md` path. It derives the
branch and repository identity without inherited `GIT_*` overrides. Run it and
later Git/worktree commands with actual cwd set to `WORKTREE_PATH`; `bd -C`
does not set Git's cwd. The worktree branch is `review/<ISSUE_ID>` on
both the initial review and later rechecks. Keep the implementation owner's
worktree and `agent/<anchor>` branch intact.
4. Confirm GitHub access before doing anything expensive:

```bash
gh auth status
```

5. If a repository-level `craft-and-care` skill exists, read it now.
6. Resolve review context with the bundled helper. The output must include:
   - `original_id`
   - `pr_number`
   - `pr_url`
   - `owner`
   - `repo`
   - `head_branch`
   - `base_branch`
7. Record reviewer identity (runtime agent ID when available plus authenticated
   GitHub login) and classify the PR as `high`, `standard`, or `low` using the
   coordinator's
   [review risk tiers](../beads-coordinator/references/runtime-and-safety.md#review-risk-tiers).

If review context cannot be resolved, stop and report
`blocked-awaiting-coordinator`.

Concrete invocation:

```bash
CONTEXT_JSON=$(python3 "<loaded beads-pr-reviewer-worker package>/scripts/resolve_review_context.py" --issue-id "${ISSUE_ID}")
```

### Phase 2: Prepare The Review Snapshot

Run the helper in the isolated review worktree:

```bash
PREP_JSON=$(python3 "<loaded beads-pr-reviewer-worker package>/scripts/prepare_pr_branch.py" \
  --base-branch "${PR_BASE_BRANCH}" \
  --head-branch "${PR_HEAD_BRANCH}" \
  --review-id "${ISSUE_ID}")
```

The helper checks out `review/<review-id>` at the fetched PR head and probes
`git merge-tree --write-tree`. Clean base drift does not rewrite the author's
head. `corrections-required` (conflict or `.beads/` divergence) returns to the
existing implementation owner on the same PR; `blocked` requires diagnosis.
An unsupported merge probe fails closed. Never remove the implementation
worktree, stash its state, rebase its branch, or force-push it as preparation.

Before reviewing, verify local HEAD and GitHub PR head equal the reported
`head_commit`. Load the current packet and member acceptance criteria from
`delivery_members` / `delivery_packet` in the resolved context. The package
has one canonical review bead targeting its anchor. Do not create a review
per member or assign the anchor's `gh-pr:N` to members.

### Phase 3: Review And Classify Findings

1. Fetch review threads with:

```bash
THREADS_JSON=$(python3 "<loaded beads-pr-reviewer-worker package>/scripts/list_review_threads.py" \
  --owner "${OWNER}" \
  --repo "${REPO}" \
  --pr-number "${PR_NUMBER}")
```

2. If the thread helper reports incomplete or ambiguous evidence, stop and
   report `blocked-awaiting-coordinator`. Do not review from partial thread
   context.
3. For each unresolved thread:
   - inspect the feedback in code and tests,
   - classify it as current-PR correctness, prerequisite blocker, new behavior,
     or duplicate,
   - reply in-thread with a concrete verdict: `correction-required`,
     `answered`, `won't-fix`, or `duplicate`,
   - leave correction threads unresolved for the implementation/recovery lane;
     resolve only after the answer or corrected exact head is real.
4. Use the bundled thread helpers instead of raw ad hoc API calls:

```bash
python3 "<loaded beads-pr-reviewer-worker package>/scripts/reply_to_review_thread.py" \
  --owner "${OWNER}" \
  --repo "${REPO}" \
  --pr-number "${PR_NUMBER}" \
  --comment-id "${TOP_LEVEL_COMMENT_ID}" \
  --thread-id "${THREAD_ID}" \
  --body "${BODY}" \
  --dedupe-key "${ISSUE_ID}:${THREAD_ID}:reply"

python3 "<loaded beads-pr-reviewer-worker package>/scripts/resolve_review_thread.py" \
  --thread-id "${THREAD_ID}"
```

5. Review the exact-head diff against the original bead's acceptance criteria
   and applicable governing contract; threads alone are not the review. Leave a line-level
   comment (a resolvable thread) for each untracked verdict-changing issue:
   correctness, contract, acceptance, the craft-and-care bar, or a test-growth
   gate. When unsure, thread it; the rest go to `Discovered-Follow-Ups-JSON`:

```bash
python3 "<loaded beads-pr-reviewer-worker package>/scripts/create_inline_review_comment.py" \
  --owner "${OWNER}" \
  --repo "${REPO}" \
  --pr-number "${PR_NUMBER}" \
  --commit-id "${HEAD_SHA}" \
  --path "${FILE_PATH}" \
  --line "${LINE}" \
  --body "${BODY}" \
  --dedupe-key "${ISSUE_ID}:${FILE_PATH}:${LINE}"
```

6. Review the tests as part of the diff using
   [`../../references/test-growth-gate.md`](../../references/test-growth-gate.md):
   the PR body states its net test delta, new tests extend the nearest existing
   test rather than cloning setup, each behavior is pinned by one gate species,
   and no test asserts source text, log lines, or exact strings that are not a
   contract. A violation is a `correction-required` thread like any other.

7. Default to **no code changes**. Return current-outcome corrections to the
   existing implementation owner directly, with substantive findings and the
   reviewed content identity. Resume that session for corrections and this
   reviewer for recheck. A coordinator relays only when the runtime requires
   it; ordinary repairs need no new task, allocation, or permission. Recovery
   replaces the owner only after its lease is dead or an explicit handoff.

   Fail-closed behavior, auth/authorization, approvals, persistence, migrations,
   cross-schema access, concurrency, replay/idempotence, and data-loss handling
   are semantic regardless of line count.

8. If a reviewer has already authored semantic fixes in an exceptional
   recovery, report `pushed-review-fixes`: a fresh independent reviewer is
   required. This is a recovery report, not the ordinary correction route.
   Keep session URLs and attribution links out of commits and PR bodies.

9. Repeated same-seam corrections update the current behavior matrix in place
   with the same owner/reviewer. A new trust boundary, subsystem, or risk
   class returns to its governing scope/authorization gate.

### Phase 4: Verify

Run all required project quality gates needed to substantiate the review, and
the repository's required landing checks. Typical gates:
- lint
- typecheck
- tests

If project docs do not name the commands clearly, use:

```bash
python3 "<loaded beads-pr-reviewer-worker package>/scripts/discover_quality_gates.py"
```

Treat discovered commands as candidates that still need judgment, not blind
truth. Do not claim completion with failing gates.

Run gates token-efficiently (`../../references/token-efficiency.md`): log to a
file, read exit status plus failure tail, iterate on covering tests only. Hosted
CI on the exact reviewed head is the broad evidence; recompute it locally only if
the repo names a local broad gate, no hosted run covers it, or to reproduce one.

### Phase 5: Merge Or Report Retry

1. Evaluate merge readiness with:

```bash
MERGE_JSON=$(python3 "<loaded beads-pr-reviewer-worker package>/scripts/evaluate_merge_readiness.py" \
  --owner "${OWNER}" \
  --repo "${REPO}" \
  --pr-number "${PR_NUMBER}")
```

   Every read targets `OWNER/REPO` explicitly, so the cwd's repository never
   answers. A returned PR whose URL or number is not the requested one exits
   with `error_code: pr-identity-mismatch`: treat it as blocked, never as
   readiness or merged evidence.

2. Reuse substantive review and test evidence according to
   [delivery policy](../../references/delivery-model.md): record source/tree
   scope, diff and relevant base assumptions. Reuse unchanged content after
   a session wake or identity-only head change; assess the changed content
   and interactions when its inputs change. Runtime/environment evidence has
   its own freshness requirement. Bind the resulting verdict to the current
   `Reviewed-Head-Commit` immediately before merge assessment.
   Merge is allowed only when `git rev-parse HEAD` and the GitHub PR head both
   still equal that exact head SHA and all are true:
   - PR state is `OPEN`
   - PR is not draft
   - unresolved review thread count is zero
   - required checks are green or neutral/skipped
   - `reviewDecision` is not `CHANGES_REQUESTED`
   - `mergeStateStatus` is one of `CLEAN`, `HAS_HOOKS`, or `UNSTABLE`
3. Apply the canonical [Owner merge hold](../beads-coordinator/references/coordinator-loop.md#owner-merge-hold)
   immediately before every direct/auto merge, GraphQL enqueue fallback and
   retry: `owner_merge_guard "$PR_NUMBER" && <action>`. No action on nonzero.
   Exact OPEN held PRs report `held-for-owner`; unavailable labels fail closed.
   Never call `gh pr merge` unless the merge-readiness helper returns
   `merge_ok: true`. If required checks could not be fetched or validated, that
   is a blocker and must fail closed.
4. If the head moved after review, stop and report
   `blocked-awaiting-coordinator`; never merge an unreviewed head.
5. If merge is safe, run the helper's `merge_command` (do **not** delete the
   branch; `MERGE_QUEUE=yes` from the coordinator overrides a `null` detection):

```bash
owner_merge_guard "$PR_NUMBER" && gh pr merge "${PR_NUMBER}" --repo "${OWNER}/${REPO}" --squash          # no queue; confirm state=MERGED -> merged-pr
owner_merge_guard "$PR_NUMBER" && gh pr merge "${PR_NUMBER}" --repo "${OWNER}/${REPO}" --squash --auto   # queue; confirm mergeQueueEntry -> merge-queued
```

   Under a queue the reviewer's job ends at `merge-queued`; the queue rebuilds
   and re-tests once and the coordinator confirms `MERGED`. Never bypass the
   queue with `--admin`. Leave `agent/<id>` in place: the coordinator deletes
   it after closure so the branch → bead correlation survives a crash.
   Confirm the PR is actually merged (or queued) before reporting; details in
   [`references/failure-protocol.md`](references/failure-protocol.md).

6. If merge is not safe, do not mutate Beads state. Report the retry reason in
   `Blockers-JSON` or `Discovered-Follow-Ups-JSON` so the coordinator can
   create or wire the right follow-up work.

7. If you intentionally close the PR instead of merging it, leave an
   explanatory comment first, then freshly guard immediately before closing
   (see [thread operations](references/thread-operations.md#close-a-pr-instead-of-merging)).

## Failure Protocol

Use [`references/failure-protocol.md`](references/failure-protocol.md) for
status selection, owner holds, failures and confirmed merge/queue outcomes.
Do not mutate Beads lifecycle state; the coordinator reconciles the report.

## Validation

For trigger checks, helper smoke tests, and end-to-end dry-run scenarios, use
[`references/evaluation.md`](references/evaluation.md). Run that validation
loop whenever you materially change this skill.

## Output Format

This report is the only valid ending; never stop on a plan or a question. Only
an external or hard-gated blocker is `blocked-awaiting-coordinator`. Produce
exactly this high-level structure: plain-text scalars, compact JSON arrays.

````text
## PR Reviewer Report: <ISSUE_ID>

Status: merged-pr | merge-queued | held-for-owner | corrections-required | pushed-review-fixes | blocked-awaiting-coordinator | invalid-runtime-context
Issue: <ISSUE_ID>
Original-Issue: <original bead id or unknown>
Branch: <head branch or n/a>
Worktree: <WORKTREE_PATH>
Head-Commit: <git rev-parse HEAD or n/a>
Reviewed-Head-Commit: <exact reviewed head SHA or n/a>
Reviewer-Identity: <runtime agent id and/or GitHub login>
Risk-Tier: high | standard | low
Branch-Pushed: yes | no
PR-URL: <url or n/a>
PR-Number: <number or n/a>
Base-Branch: <branch or n/a>
Merge-Performed: yes | queued | no
PR-Closed: yes | no
Summary: <1-2 sentence description of what was done>

Quality-Gates:
- lint: pass | fail | not-run
- typecheck: pass | fail | not-run
- tests: pass | fail | not-run

Review-Actions-JSON:
```json
[]
```

Discovered-Follow-Ups-JSON:
```json
[]
```

Blockers-JSON:
```json
[]
```
````

Rules:
- use exactly one `Status` value
- `merged-pr` means the PR was merged and confirmed, but no Beads closure was
  performed here
- `merge-queued` means the exact reviewed head passed every merge gate and was
  enqueued with `gh pr merge --squash --auto` (confirmed via an actual
  `mergeQueueEntry`); `Merge-Performed: queued`; the coordinator confirms the
  eventual merge and performs closure
- `held-for-owner` means an OPEN owner hold; `Merge-Performed: no` and
  `PR-Closed: no`; preserve all findings, identity/head/risk and correction
  evidence per the failure protocol (including fresh-independent-review needs)
- `corrections-required` means actionable unresolved threads were returned to
  the original author or recovery worker; the reviewer did not author semantic
  corrections
- `pushed-review-fixes` means exceptional reviewer-authored code was pushed;
  another fresh independent reviewer must assess the new head
- `blocked-awaiting-coordinator` requires at least one blocker object
- `invalid-runtime-context` means bootstrap failed before meaningful review work
- if there are no review actions, follow-ups, or blockers, use `[]`

JSON object schemas:

`Review-Actions-JSON` entries:

```json
{
  "thread_url": "https://github.com/owner/repo/pull/123#discussion_r1",
  "action": "correction-required",
  "summary": "Fail closed when the evidence field is malformed"
}
```

`Discovered-Follow-Ups-JSON` entries:

```json
{
  "title": "Short follow-up title",
  "type": "bug",
  "priority": 2,
  "depends_on": "bd-42",
  "rationale": "Why this should be tracked separately"
}
```

`Blockers-JSON` entries:

```json
{
  "title": "Concrete blocker title",
  "type": "task",
  "priority": 1,
  "depends_on": "bd-42",
  "rationale": "What is blocked and why",
  "unblock_condition": "What must happen before work can resume"
}
```
