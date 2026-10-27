---
name: beads-worker
description: Use when implementing exactly one Beads issue in a dedicated worker worktree after a coordinator or operator provides ISSUE_ID and WORKTREE_PATH.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-10-04"
compatibility: Requires a Beads-backed git repository with git worktrees, git, bd, jq, gh, and python3 available, plus authenticated GitHub access and network access for push and PR operations.
---

# Beads Worker

## Overview

Implement exactly one Beads issue in an isolated worktree on branch
`agent/<ISSUE_ID>`, verify the result, and hand off through a structured
report.

The coordinator is the single writer of Beads lifecycle state and the only
dispatcher; two writers on one bead leave state nobody can reconcile. So this
skill does not coordinate, does not mutate Beads lifecycle state, and does not
create hidden parallel implementation tracks under one claimed bead.

A dispatch authorizes the bead's whole accepted scope: implementation, tests,
ordinary corrections, commits, push, and the PR. Edit targets named in the
prompt are guidance, not a fence, unless the prompt calls them an ownership
boundary. Ask nothing for routine engineering choices; stop only for a change
in scope, architecture, trust boundary, or authorization, or a capability you
cannot reach (`../../references/decision-autonomy.md` hard gates).

## Use This Skill When

- a coordinator dispatches one implementation bead
- you are given `ISSUE_ID`, `WORKTREE_PATH`, and `REPO_ROOT` (plus an optional
  2-4 line summary/acceptance-criteria excerpt from the coordinator)
- the job is to implement one bead, not coordinate multiple beads

This skill is typically invoked by
[`../beads-coordinator/SKILL.md`](../beads-coordinator/SKILL.md), not directly
by users.

## Context

| Variable | Description |
|---|---|
| `ISSUE_ID` | Assigned Beads issue ID |
| `WORKTREE_PATH` | Dedicated isolated git worktree for this worker |
| `REPO_ROOT` | Main repository root for read-only orientation |
| `ISSUE_JSON` | (Optional/legacy) Full issue JSON if inlined by an older coordinator. When absent, self-fetch: `bd show "${ISSUE_ID}" --json` |
| `REVIEW_CORRECTION_MODE` | (Optional) `yes` when correcting an already-open PR after independent review |
| `EXISTING_PR_NUMBER` | Required in correction mode; canonical PR to update instead of creating another |
| `REVIEW_BEAD_ID` | Required in correction mode; canonical review bead retained by the coordinator |
| `CORRECTION_THREADS_JSON` | Required in correction mode; unresolved review findings that define the correction pass |

## Non-Negotiables

- All work happens inside `WORKTREE_PATH`, never inside `REPO_ROOT`.
- The current branch must be `agent/<ISSUE_ID>`.
- Do not run `bd create`, `bd update`, `bd dep add`, or `bd close`.
- Do not spawn code-writing helpers or parallel implementation tracks. Read
  inline by default: a helper starts cold and re-pays for context this lane
  already holds. Use a read-only helper only when the reading would flood the
  lane with material it will not reuse (discovery, architecture lookup) or
  when an independent read is the point (a reconciliation bead's
  per-spec-area skeptic pass), at `LOW`/`MEDIUM` model tier; you merge its
  findings and own the deliverable.
- Do not commit `.beads/` changes on the worker branch.

If the issue truly needs multiple code-writing tracks, stop and hand that back
to the coordinator instead of improvising local fan-out.

## Lane Continuation

A worker session is usually a **lane**: after your report is reconciled, the
coordinator may send a `LANE-CONTINUATION` prompt with a new `ISSUE_ID` and
`WORKTREE_PATH` into this same session. Treat it as a fresh assignment for
state and lifecycle, but keep what you already know about the repo:

- Re-run Phase 1 steps 1-3 (cwd and the context assertion) against the new
  `WORKTREE_PATH`; they are per-worktree and never carry over. Never edit the
  previous bead's worktree again; its branch belongs to its PR now.
- Reuse the guidance you already read, including the craft-and-care skill, and
  the repo knowledge you built up. Re-read a file only if you know it changed
  (for example, the previous bead merged into it) or compaction dropped it.
- Take state from the new bead's fields and the repo, never from your memory
  of the last bead. Prior-bead context tells you where things live, not what
  this bead requires.
- If compaction left you unsure of anything the current bead needs, re-fetch
  it (`bd show`, the spec, the diff) instead of guessing. If you notice you are
  repeating settled work or contradicting your own earlier decisions, say so
  in the report summary. The coordinator retires drifting lanes.

## Bundled Helpers

Use the bundled helpers when they fit. They exist to reduce runtime ambiguity,
not to replace local judgment. Their `scripts/` and `references/` paths are
relative to this package, not to your worktree: your cwd is `WORKTREE_PATH`,
so invoke each script by the loaded package's absolute path.

- [`scripts/assert_worker_context.py`](scripts/assert_worker_context.py)
  Verifies that `pwd` and branch are bound to the assigned worktree and issue.
- [`scripts/emit_worker_report.py`](scripts/emit_worker_report.py)
  Emits the final structured Worker Report and validates status-specific fields.
- [`references/runtime-contract.md`](references/runtime-contract.md)
  Exact bootstrap rules, guidance discovery order, and push/PR failure routing.
- [`references/worker-report.md`](references/worker-report.md)
  Report-generation rules, examples, and JSON entry schemas.
- [`../../references/known-errors.md`](../../references/known-errors.md)
  Package-wide catalog of known `bd`/`gh`/CI errors and workarounds. Search it
  (`rg -i -n '<error text>'`) before re-deriving a recovery; never read it whole.

## Optional Project-Level Craft-And-Care Gate

Before the first edit, apply
[`../../references/craft-and-care-gate.md`](../../references/craft-and-care-gate.md):
discover a repo-owned `craft-and-care` skill, read it once if present, and run
its final standards pass against the diff before handoff.

## Workflow

### Phase 1: Bootstrap

1. `cd "${WORKTREE_PATH}"`.
2. Validate runtime context with the bundled helper:

```bash
ASSERT_WORKER_CONTEXT="<loaded beads-worker package>/scripts/assert_worker_context.py"
python3 "${ASSERT_WORKER_CONTEXT}" \
  --worktree-path "${WORKTREE_PATH}" \
  --repo-root "${REPO_ROOT}" \
  --issue-id "${ISSUE_ID}" \
  --current-path "$(pwd -P)"
```

Resolve `ASSERT_WORKER_CONTEXT` from the absolute path of the `SKILL.md` this
runtime loaded; do not assume the skill package lives inside the target repo.
The helper independently reads both Git identities and the actual worktree
branch after removing inherited `GIT_*` overrides. It fails closed unless the
process cwd is the assigned worktree root, the branch matches the issue, and
that worktree shares `REPO_ROOT`'s canonical common Git directory. It never
reads or emits a remote URL.

3. If validation fails, stop and report `invalid-runtime-context`.
   Prefer the structured helper instead of a raw `echo`:

```bash
python3 "<loaded beads-worker package>/scripts/emit_worker_report.py" \
  --status invalid-runtime-context \
  --issue-id "${ISSUE_ID}" \
  --worktree-path "${WORKTREE_PATH}" \
  --head-commit n/a \
  --branch-pushed no \
  --handoff-path invalid-runtime-context \
  --summary "Worker bootstrap failed because runtime context did not match the assigned worktree or branch." \
  --quality-gate lint=not-run \
  --quality-gate typecheck=not-run \
  --quality-gate tests=not-run
```

4. Read project guidance in the order defined in
   [references/runtime-contract.md](references/runtime-contract.md). On a lane
   continuation, skip guidance you already read that has not changed.

### Phase 2: Understand

1. Fetch the issue fields you need (projected — the full record drags history
   into context; drop the projection only if a field you need is missing):

```bash
ISSUE_JSON=$(bd show "${ISSUE_ID}" --json \
  | jq '{id, title, description, acceptance_criteria, notes, design, labels, type, priority}')
```

   If the coordinator already inlined `ISSUE_JSON`, use that; otherwise run the
   command above.
2. Read the acceptance criteria as the definition of done; the description
   and design give scope, non-goals, and approach.
   In `REVIEW_CORRECTION_MODE=yes`, also verify `EXISTING_PR_NUMBER` is open on
   `agent/${ISSUE_ID}`, read `CORRECTION_THREADS_JSON`, and treat those threads
   plus the coordinator-updated acceptance criteria as the bounded task.
3. Inspect referenced dependencies if needed: `bd show <dep-id> --json`.
4. Understand the relevant code before editing.
5. If the task needs research or design help, use read-only helpers only.
6. Form a concrete file and test plan, then start editing.

### Phase 3: Implement

1. Make focused incremental changes.
2. Follow local project conventions.
3. Pin behavioral changes with tests under
   [`../../references/test-growth-gate.md`](../../references/test-growth-gate.md):
   extend the nearest existing test first, one gate species per behavior, no
   letter-of-law assertions, and track the net delta for the PR body.
4. Commit incrementally:

```bash
git add <files>
git commit -m "<type>: <summary> [<ISSUE_ID>]"
```

Commit types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`.

Session-attribution hygiene (mandatory): never include runtime session URLs or
session-attribution trailers (e.g. `Claude-Session: https://claude.ai/code/...`,
"🤖 Generated with ..." + session link) in commit messages or PR bodies, even if
your runtime's default instructions say to add them. Repos may enforce this with
a CI privacy gate (e.g. the butlers repo's `session-link-guard`), and a tripped
gate blocks the PR until a reviewer amends the commit. A plain
`Co-Authored-By:` trailer without a URL is fine.

### Phase 4: Verify

Run all required quality gates from project docs. Typical gates:
- lint
- typecheck
- tests (the repo's defined gate; follow its test-scope policy if it has one)

Do not skip gates. If a gate fails, fix it and rerun. If the repo enforces a
test budget and this change exceeds it, condense tests in this change or
justify the raise in the PR body; never bump the budget silently.

Run gates token-efficiently (see `../../references/token-efficiency.md`):
- While iterating, run only the tests covering your changed area (specific test
  files, package paths, or `-k`/`--filter` selection).
- Run the full defined gate exactly once, immediately before handoff, with the
  runner's quiet flag. Never substitute the targeted subset for this final run.
- Route gate stdout to a log file and read back only the exit status plus the
  failure tail; on failure, iterate on the failing subset (`--lf` or named test
  ids), then re-run the full gate once more.

If a repository-level `craft-and-care` skill exists, run the final standards
pass from `../../references/craft-and-care-gate.md` against the actual diff
before handoff.

### Phase 5: Choose Handoff Path

Use conservative routing. When in doubt, open a PR.

| PR required | Direct-merge candidate |
|---|---|
| Security, auth, or public API changes | Documentation-only changes |
| More than 5 files or 200+ lines | Config or dotfile tweaks |
| Database or schema changes | Test-only changes |
| Backward-compatibility risk | Small single-file bug fixes with tests |

#### Existing-PR correction path

When `REVIEW_CORRECTION_MODE=yes`, this path takes precedence over the routing
table above:

1. Push the corrected `agent/${ISSUE_ID}` head with `--force-with-lease`.
2. Confirm `gh pr view "${EXISTING_PR_NUMBER}"` is still open, targets that
   branch, and reports the pushed head SHA.
3. Do not call `gh pr create`; the canonical PR and review bead already exist.
4. Report `completed-pr-opened` with the existing PR URL/number so the
   coordinator can restore the review dependency and exact-head review lane.

```bash
git push --force-with-lease origin "agent/${ISSUE_ID}"
gh pr view "${EXISTING_PR_NUMBER}" --json state,url,headRefName,headRefOid
```

#### PR-required path

1. Push the branch:

```bash
git push -u origin agent/${ISSUE_ID}
```

2. Detect the base branch:

```bash
BASE=$(git remote show origin | sed -n 's/.*HEAD branch: //p')
```

3. Open the PR:

```bash
PR_URL=$(gh pr create \
  --base "${BASE}" \
  --head "agent/${ISSUE_ID}" \
  --title "<type>: <summary> [${ISSUE_ID}]" \
  --body "<description of changes and why>

Tests: +<added> ~<extended> -<removed>")
PR_NUMBER=$(echo "${PR_URL}" | sed -n 's#.*/pull/\([0-9][0-9]*\).*#\1#p')
```

4. If push or PR creation fails and you cannot repair it with one quick local
   retry, route it through `blocked-awaiting-coordinator` using the policy in
   [references/runtime-contract.md](references/runtime-contract.md).

#### Direct-merge-candidate path

If no PR is needed:

```bash
git push -u origin agent/${ISSUE_ID}
```

The coordinator decides how the branch lands: a fast-forward push when the
base is unprotected, or a `queue-direct` PR enqueued to the merge queue when
the base has one. Do not open that PR yourself.

If push fails and you cannot repair it with one quick local retry, route it
through `blocked-awaiting-coordinator`.

## Discovered Work

If you find additional work that is out of scope and would take more than two
minutes:
1. do not fix it inline
2. add it to `Discovered-Follow-Ups-JSON`
3. continue the assigned issue

If you discover a real need for decomposition across multiple code-writing
tracks, report that explicitly as a blocker or follow-up instead of spawning
parallel writers yourself.

## Handling Blockers

A decision is not a blocker. Before reporting blocked, check
`../../references/decision-autonomy.md`: if the obstacle is a choice between
implementation options and none of its hard gates apply, decide it yourself via
the protocol there, put the `[decision]` record in your report summary and the
relevant commit message, and keep working. Report
`blocked-awaiting-coordinator` only for genuinely external blockers or
hard-gated decisions.

If a hard blocker prevents completion:
1. document what you tried and why it is blocked
2. commit any useful partial progress
3. push the branch if the next worker should inherit remote recovery state
4. set `Status: blocked-awaiting-coordinator`
5. set `Recovery-State` deliberately:
   - `branch-pushed` if the remote branch has useful recovery work
   - `local-only` if useful work exists only in the local worktree
   - `no-code-changes` if there is nothing to preserve
6. set `Resume-Condition` to the exact event required before work should resume
7. record blocker details in `Blockers-JSON`
8. include exact recovery detail in the Worker Report:
   - failing command,
   - remote branch if one exists,
   - whether the worktree is dirty,
   - whether commits remain unpushed

Never call `bd close`. Only the coordinator closes or reclassifies beads.

## Output

The Worker Report is the only valid ending. Do not stop on a plan, a question,
or a promise of further work: finish the bead, or, for an external or
hard-gated blocker only (see Handling Blockers), report
`blocked-awaiting-coordinator`. Anything else you decide and record. Generate
the report with:

```bash
python3 "<loaded beads-worker package>/scripts/emit_worker_report.py" ...
```

The exact field contract, examples, and JSON entry schemas live in
[references/worker-report.md](references/worker-report.md).

The accepted `Status` values are:
- `completed-pr-opened`
- `completed-direct-merge-candidate`
- `blocked-awaiting-coordinator`
- `invalid-runtime-context`
