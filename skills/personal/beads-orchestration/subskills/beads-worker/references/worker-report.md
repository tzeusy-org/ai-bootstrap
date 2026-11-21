# Worker Report

Use `scripts/emit_worker_report.py` to generate the final report instead of
hand-formatting it.

## Required Status Values

- `completed-pr-opened`
- `completed-direct-merge-candidate`
- `blocked-awaiting-coordinator`
- `invalid-runtime-context`

Do not invent synonyms such as `done`, `blocked`, or `needs-review`.

## Route → Flags

Each publish-and-handoff route maps to exactly one `--status` / `--handoff-path`
pair (the "PR path" in SKILL.md is `pr-required`; there is no `pr` value):

| Route | `--status` | `--handoff-path` | `--branch-pushed` |
|---|---|---|---|
| PR path (default; unknown policy) | `completed-pr-opened` | `pr-required` | `yes` |
| Direct-merge candidate | `completed-direct-merge-candidate` | `direct-merge-candidate` | `yes` |
| Blocked (external/hard gate) | `blocked-awaiting-coordinator` | `blocked-awaiting-coordinator` | as observed |
| Bootstrap failed | `invalid-runtime-context` | `invalid-runtime-context` | `no` |

## Quality Gates

Pass `--quality-gate lint|typecheck|tests=<result>` with one of:

- `pass` / `fail` — the gate ran with that result.
- `not-applicable` — the repo defines no such gate (no linter, no typecheck
  for a shell or prose repo).
- `not-run` — the repo defines the gate but it did not run; this reads as
  skipped and needs a reason in `--summary`. An omitted gate defaults to it.

## Recovery Fields

Omit `--recovery-state`, `--dirty-worktree`, `--unpushed-commits` and
`--remote-branch` unless you know better: the emitter reads them from git in
`--worktree-path`: status, and commits ahead of `origin/<reported branch>`
(never `@{u}`, which is `origin/main` for a worktree made from it), with
`unknown`/`n/a` when git cannot say. `Recovery-State` then defaults to
`branch-pushed` for a completed report or a pushed branch with no local work,
`no-code-changes` for a bootstrap failure or a clean worktree with nothing
unpushed, else `local-only`. A completed report requires
`branch-pushed`, so it never claims `no-code-changes`.

## Standard Usage

```bash
python3 "<loaded beads-worker package>/scripts/emit_worker_report.py" \
  --status completed-direct-merge-candidate \
  --issue-id "${ISSUE_ID}" \
  --worktree-path "${WORKTREE_PATH}" \
  --head-commit "$(git rev-parse HEAD)" \
  --branch-pushed yes \
  --handoff-path direct-merge-candidate \
  --summary "Implemented the assigned change and verified the required gates." \
  --quality-gate lint=pass \
  --quality-gate typecheck=pass \
  --quality-gate tests=pass \
  --changes "path/to/file: what changed" \
  --tests "full: <gate command>: pass"
```

`tests=pass` reports the test gate assigned to the local tier. Name its scope
first in `--tests`: `full: <command>: pass` when the full defined gate ran
locally, or `scoped: <paths>: pass; broad: hosted` when the repo's required
hosted run is the broad gate (PR handoff only). Emit the report after the
gate: a relevant content/input change invalidates its `full:` entry. A commit
with unchanged relevant content can retain evidence with an explicit equivalence
proof; `--head-commit` still identifies the current handoff head. The coordinator
fast-forwards a direct-merge candidate only on a `full:` entry.

For PR handoff add:

```bash
  --status completed-pr-opened \
  --handoff-path pr-required \
  --pr-url "${PR_URL}" \
  --pr-number "${PR_NUMBER}" \
  --base-branch "${BASE}" \
  --review-reason "Why this change should go through PR review"
```

For blockers add:

```bash
  --status blocked-awaiting-coordinator \
  --handoff-path blocked-awaiting-coordinator \
  --branch-pushed no \
  --resume-condition "Exact event required before work can resume" \
  --failing-command "Exact command that failed" \
  --blockers-json '[{"title":"Concrete blocker title","type":"task","priority":1,"depends_on":"bd-42","rationale":"What is blocked and why","unblock_condition":"What must happen before work can resume"}]'
```

## Cohesive Package Results

For a multi-bead delivery, `ISSUE_ID` is the canonical anchor. Add
`--delivery-file "$DELIVERY_FILE"` and `--member-results-json` to the same
report invocation. Every member needs exactly one result; missing, duplicate,
or foreign members are rejected:

```json
[
  {"id":"bd-a","status":"satisfied","evidence":"Adapter regression cases pass on the reported head"},
  {"id":"bd-b","status":"partial","evidence":"Representation landed; activation still requires owner authority"}
]
```

Result states are `satisfied`, `partial`, and `blocked`. A PR-opened status
describes the delivery cycle, not acceptance of every member. The emitted
`Delivery-JSON` and `Member-Results-JSON` let the controller reconcile each
member separately after confirming landing on main. Neither a foundation
merge nor a merge into a milestone branch completes the accepted feature.
The manifest owner retains ordinary corrections on the same branch, PR and
review record. A report never closes beads or releases their claims.
For `invalid-runtime-context`, omit member results: a readable manifest yields
blocked results explicitly marked not evaluated. An unreadable/mismatched
manifest emits `Delivery-Validation-Error` without claiming member acceptance.

## JSON Arrays

`Discovered-Follow-Ups-JSON` and `Blockers-JSON` must both be valid JSON arrays.

Example follow-up entry:

```json
{
  "title": "Short follow-up title",
  "type": "bug",
  "priority": 2,
  "depends_on": "bd-42",
  "rationale": "Why this should be tracked separately"
}
```

Example blocker entry:

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

## Reporting Rules

- `completed-pr-opened` requires verified PR metadata and `Branch-Pushed: yes`.
- `completed-direct-merge-candidate` requires `PR-URL: n/a`,
  `PR-Number: n/a`, and `Branch-Pushed: yes`.
- `blocked-awaiting-coordinator` requires at least one blocker object and the
  exact failing command.
- `unblock_condition` must name an external event or a hard gate from
  `../../../references/decision-autonomy.md`. "A human/coordinator decides
  which approach" is not a valid unblock condition for an ungated engineering
  choice — decide it and record a `[decision]` line instead.
- `invalid-runtime-context` means stop before touching code or Beads lifecycle
  state.

When blocked after partial progress, include recovery detail so the next worker
does not need manual forensics:
- the exact failing command,
- whether useful commits were pushed,
- the remote branch to resume from if one exists,
- whether the worktree is dirty,
- whether commits remain unpushed.
