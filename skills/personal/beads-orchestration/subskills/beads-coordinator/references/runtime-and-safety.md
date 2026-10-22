# Runtime And Safety

Load this file before dispatching workers or mutating Beads state.

## Claiming And The Stall Heartbeat

Mutual exclusion comes from two real mechanisms, **not** from notes-based
tokens:

1. **Atomic claim** — `bd update <id> --claim` atomically sets the bead's
   `assignee` to you and `status=in_progress` in one operation. It is idempotent
   if you already hold it. This is the cross-actor mutual-exclusion primitive:
   only one actor can win the `assignee`. Verify success from the command
   result (`assignee` is this coordinator), not from a later reread.
2. **Worktree creation backstop** — `bd worktree create` fails when the
   `agent/<id>` branch already exists. This prevents a same-actor session from
   double-dispatching a bead it already has in flight. If worktree creation
   fails on an existing branch, do not dispatch a second worker.

Never model claiming as a read-then-write of a token in `notes`/`design`. There
is no atomic conditional update / compare-and-swap via the notes field; that
would be a read-then-write race. The `bd update --claim` flag is the atomic
operation.

### Stall heartbeat (stall detection only)

The coordinator writes a heartbeat timestamp so a crashed/abandoned claim can be
detected and reclaimed by a recovery pass (cleanup or another coordinator). This
heartbeat is **explicitly not a mutual-exclusion mechanism** — `assignee` is.

Store exactly one canonical heartbeat block in `notes` (replace it in place;
never append duplicates):

```text
[beads-heartbeat]
owner=coordinator:<session-id>
last_heartbeat_at=<iso8601>
[/beads-heartbeat]
```

Heartbeat and stall parameters (canonical; do not restate elsewhere):
- Heartbeat TTL: 20 minutes.
- Renewal target: every 5 minutes while active, and before every mutation.
- Worker stall threshold: at least 30 minutes without a progress signal before
  force-release, unless a runtime-specific note below states otherwise. Team
  Leads get 3x this threshold (see `epic-coordination.md`).

Repeat this rule during long runs:

Before any `bd` mutation: confirm you are the bead's `assignee`, renew the stall
heartbeat if near expiry, then mutate.

Mandatory heartbeat-renewal checkpoints (a checkpoint may be **skipped** when
the heartbeat was already renewed within the last 5 minutes — with a 20-minute
TTL that is always safe, and it lets a burst of consecutive mutations share one
renewal instead of paying one `bd update` per mutation):
1. Immediately after claim.
2. Before every `bd create`, `bd update`, `bd dep add`, or `bd close`.
3. After any long external action such as `gh`, tests, rebase, or merge work.
4. Before sleeping, polling, or waiting on agents for a long window.

Ownership rule:
- If the `assignee` is another actor, do not touch the bead — its claim wins.
- If the bead is assigned to another actor but its heartbeat is expired past the
  stall threshold, a recovery pass (cleanup or coordinator) may reclaim it:
  re-claim with `bd update <id> --claim` and reset the heartbeat.
- Never "win" a live foreign claim by writing a newer timestamp; the heartbeat
  does not arbitrate ownership.

## Orchestrator Wake Cadence (Prompt-Cache Economics)

This is a distinct mechanism from the Beads stall heartbeat above — it governs
the **token cost of resuming this session**, not bead mutual exclusion or
stall detection. Do not conflate the two.

- The coordinator session's prompt cache lives for the runtime's **TTL**,
  measured start-to-start between requests. Claude Code sessions default to a
  **1-hour** TTL (it falls back to 5 minutes in usage overage). Treat any
  runtime whose TTL you cannot confirm as **5 minutes**. A wake that lands
  after the TTL re-reads the whole context at write price: 12.5x-40x a cache
  hit, depending on model and TTL (see `../../../references/token-efficiency.md`).
- While there is near-term work to track (an active lane, a dispatchable
  `pr-review-task`, a PR cooldown about to expire), wake on events first.
  Behind them, set a fallback heartbeat that always lands inside the TTL:
  **20-30 minutes** on a 1-hour TTL, **4m50s** on a 5-minute TTL. On a 1-hour
  TTL, polling faster than every 20 minutes just to keep the cache warm is
  waste, because there is no cache cliff to beat. This is what "active mode"
  in `coordinator-loop.md` Step 8 means in practice.
- The miss cost scales with context size, so the projection and file-routing
  rules in `../../../references/token-efficiency.md` compound with this one: a
  lean context makes every wake cheaper in both modes.
- Never block the coordinator's own turn on a long call either: no in-line
  test suites, and no wait-on-agent call whose timeout exceeds the ceiling.

Runtime binding (use whichever wake primitive the session exposes):

| Runtime | Active-mode wake | Frontier wake |
|---|---|---|
| Claude Code | subagent completion wakes you; fallback `ScheduleWakeup` (`/loop` dynamic mode) with `delaySeconds: 1200`-`1800` on the default 1-hour TTL (`290` only if the session is on the 5-minute TTL), or a `Monitor` until-loop | `delaySeconds: 3600`, `noop: true` on a no-op wake, `stop: true` after the third |
| Codex | `wait_agent` with timeout ≤ 290s (a timeout means "still running", never a stall) | `wait_agent`/sleep capped at 60 min per wake |

### No-progress frontier

Once a poll finds genuinely nothing to do — `bd ready` empty, no dispatchable
`pr-review-task`, no active workers, no near-term PR cooldown, no decision-sweep
work — the cache-preserving fallback cadence stops paying for itself: there is no
real work to keep warm a cache *for*. At that frontier, switch modes instead of
continuing to burn cache-hit polls on nothing:

- Widen the wake interval to **60 minutes** and run the safety sweep on each
  wake.
- Track consecutive no-op wakes (a wake where the sweep still finds nothing
  dispatchable). After **3 consecutive no-op wakes** (3 hours of confirmed
  silence), stop the loop entirely — report the terminal state and hand back —
  rather than polling forever. Any wake that finds real work (even one
  dispatchable bead) resets the no-op counter to zero and returns to the
  near-term-work cadence above.
- This still respects the mandatory heartbeat-renewal checkpoints above: renew
  the stall heartbeat on any wake that performs a `bd` mutation.

Net shape: fast, cache-cheap polling while there is work to track; slow,
infrequent polling with a hard stop while there is confirmed nothing to do.
Neither mode is license to skip a mandatory heartbeat-renewal checkpoint or
override the stall-heartbeat/claim rules above — this section is cost-only and
never trades correctness for it.

## Model Selection Strategy

The coordinator has discretion on subagent model choice based on task type.

### Complexity Constants

Before revising this matrix, re-check the current
[OpenAI model-selection guidance](https://developers.openai.com/api/docs/guides/model-selection),
[Anthropic model-selection guidance](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model),
and [Anthropic effort guidance](https://platform.claude.com/docs/en/build-with-claude/effort),
then update this reference and its contract test in the same change.

| Strategy | Claude | Codex / ChatGPT | Gemini |
|---|---|---|---|
| `EPIC_COMPLEXITY_MODEL` | Opus 5.5 High | GPT-6.1 Sol XHigh | gemini-4-argon |
| `HIGH_COMPLEXITY_MODEL` | Opus 5.5 Medium | GPT-6.1 Sol High | gemini-4-argon |
| `MEDIUM_COMPLEXITY_MODEL` | Sonnet 5.5 High | GPT-6.1 Sol High | gemini-4-argon |
| `LOW_COMPLEXITY_MODEL` | Sonnet 5.5 Low | GPT-6.1 Sol Low | gemini-3.8-flash |
| `TRIVIAL_COMPLEXITY_MODEL` | Haiku 5.5 Low | GPT-6 Luna Max | gemini-3.8-flash |
| `DESIGN_AND_SPECIFICATION_MODEL` | Opus 5.5 High | GPT-6.1 Sol XHigh | gemini-4-argon |

These are deliberate local choices over the vendors' starting-point guidance.
On Codex, Sol 6.1 is the workhorse and Luna is reserved for trivial work.
Dispatch trivial work (see the TRIVIAL criteria below) with Luna Max; never
dispatch Luna for anything that needs judgment. Dispatch non-trivial work below
medium with Sol Low, medium and high work with Sol High, and epic work and
plan/scoping/design/specification drafting with Sol XHigh. Sol is the only
non-trivial Codex model: do not dispatch Astra or Terra. Escalation on Codex is
an effort step within Sol (Low -> High -> XHigh), never a model-family switch.

Anthropic recommends starting Opus 5.5 at its default `medium` effort and
tuning against task-specific evals. This policy mirrors the Codex split: Haiku
5.5 (priced like Luna) for trivial work only, Sonnet 5.5 (priced like Sol) for
low (`low` effort) and medium (`high` effort) work, and Opus 5.5 above them:
`medium` for high work, `high` for epic or plan/specification drafting.
Escalate after a shallow or failed attempt: low to medium tier first, then to
Opus.

Haiku 5.5 suits one-shot trivial dispatches, not long lanes: a prompt over
100K tokens is billed at 5x its base rate, and it has no server-side refusal
fallback. Never dispatch Haiku for anything that needs judgment. LOW stays on
Sonnet until Haiku 5.5 has a track record on trivial beads.

### Claude Dispatch Binding

Pass model and effort independently. Where the runtime accepts an effort
argument, set it explicitly: defaults differ (Opus and Haiku `medium`,
Sonnet `high`), so omitting it would violate the Haiku low, Sonnet low, and
Opus high rows.

| Policy choice | `model` | `effort` |
|---|---|---|
| Haiku 5.5 Low | `claude-haiku-5-5` | `low` |
| Sonnet 5.5 Low | `claude-sonnet-5-5` | `low` |
| Sonnet 5.5 High | `claude-sonnet-5-5` | `high` |
| Opus 5.5 Medium | `claude-opus-5-5` | `medium` |
| Opus 5.5 High | `claude-opus-5-5` | `high` |

Claude Code's `Agent` tool takes `model` as a family alias (`haiku`, `sonnet`, `opus`)
rather than the full ID, and has no per-dispatch effort argument: a subagent's
effort comes from its agent definition (`.claude/agents/*.md` frontmatter or
SDK `agents`). To hold a pairing there, dispatch an agent type whose
definition sets that effort. Where no such definition exists, the effort
column is a target, not a guarantee: dispatch the right model and say in the
final report that effort was not set per dispatch.

### Codex Dispatch Binding

Pass a base model and independent reasoning effort when using Codex's native
subagent mechanism:

| Policy choice | `model` | `reasoning_effort` |
|---|---|---|
| GPT-6 Luna Max | `gpt-6-luna` | `max` |
| GPT-6.1 Sol Low | `gpt-6.1-sol` | `low` |
| GPT-6.1 Sol High | `gpt-6.1-sol` | `high` |
| GPT-6.1 Sol XHigh | `gpt-6.1-sol` | `xhigh` |

### Assignment Rules

| Task Type | Model Complexity |
|---|---|
| Epic / team-coordinated work (the extreme tier) | `EPIC_COMPLEXITY_MODEL` |
| Reconciliation bead for a medium-or-higher epic | `EPIC_COMPLEXITY_MODEL` (floor — see below) |
| Plan drafting, architecting, design, or specification work whose primary deliverable is the plan/specification | `DESIGN_AND_SPECIFICATION_MODEL` |
| Research or analysis | Matching complexity model; use `EPIC_COMPLEXITY_MODEL` only when the work itself meets the epic/extreme criteria |
| Coding | `MEDIUM_COMPLEXITY_MODEL` unless trivial (see TRIVIAL criteria below) |
| Orchestration | `HIGH_COMPLEXITY_MODEL` |
| PR review (`pr-review-task`) | `MEDIUM_COMPLEXITY_MODEL`; escalate to `HIGH_COMPLEXITY_MODEL` only for large (>400 changed lines) or risk-flagged (security/auth/schema/public-API) diffs |
| Simple bugfixes | `MEDIUM_COMPLEXITY_MODEL` |
| Formatting, linting | `TRIVIAL_COMPLEXITY_MODEL` |
| Probes: bootstrap/status checks, recovery probes, read-only lookups (`Explore`-style) | `TRIVIAL_COMPLEXITY_MODEL`, read-only tools; prefer a script or one composite command over a subagent when the answer is mechanical |

Concrete `TRIVIAL_COMPLEXITY_MODEL` criteria — dispatch at TRIVIAL when **all** hold:
- docs-only, config/dotfile-only, test-only, or single-file mechanical change
- no API, schema, auth, or cross-module behavior change
- acceptance criteria are fully mechanical (no design judgment required)

Non-trivial work labeled `complexity:low` uses `LOW_COMPLEXITY_MODEL`.

**Plan/design/specification override.** If the bead's primary deliverable is a
plan, design, architecture, or specification artifact, select
`DESIGN_AND_SPECIFICATION_MODEL` before the complexity-label fast path. Do not
apply this override to research, implementation, review, or reconciliation work
merely because it informs or consumes such an artifact.

**Complexity-label fast path.** Otherwise, if the bead carries a
`complexity:<tier>` label (`low`/`medium`/`high`/`epic`, stamped by
`beads-writer` at creation), map it directly to the matching
`*_COMPLEXITY_MODEL` (a `low` bead that meets the TRIVIAL criteria goes to
`TRIVIAL_COMPLEXITY_MODEL`) and skip re-deriving complexity from the description.
Re-derive only when the label is obviously stale (e.g. scope grew via
follow-ups). Apply the Reconciliation Floor below regardless of label.

**Right-size deliberately.** Token spend scales with the dispatched model, and
most backlog beads are not the hard case. Default to the lowest tier the
criteria allow and escalate on evidence (a failed or shallow attempt), not on
vibes: one redispatch after a too-weak attempt costs less than habitually
over-provisioning every bead. Effort is part of each approved pairing, not a
second opportunity to cheapen it: in particular, Luna `max` is the only Codex
setting for TRIVIAL work. Right-size when a lane is **formed**, from the highest
tier in its planned chain. Never switch a live lane's model: that is a cold
start, so retire the lane instead.

### Reconciliation Floor (mandatory)

A reconciliation bead (label `reconciliation`, title `Reconcile spec-to-code
(gen-N) …`) is a deep spec-to-code audit, not ordinary coding. Its `task` type
would otherwise route it to `MEDIUM_COMPLEXITY_MODEL` (Sonnet), which is too weak to
catch coverage gaps across a large epic.

Rule: before dispatching a reconciliation bead, resolve its parent epic's
complexity tier (see `epic-coordination.md` → "Epic Complexity Tiers"). If the
epic is **medium or higher** (1+ positive classification signal, or it carries
the `team-coordination` label), dispatch the reconciliation bead at
`EPIC_COMPLEXITY_MODEL` — i.e. Opus on Claude. This is a floor, not a
target: never drop below it for a qualifying epic, regardless of the bead's
`task` type. Only a low/trivial epic (0 signals) may reconcile at
`MEDIUM_COMPLEXITY_MODEL`.

To find the parent epic and its tier:

```bash
EPIC_ID=$(bd show <recon-id> --json | jq -r '.parent // .epic // empty')
bd show "${EPIC_ID}" --json   # inspect scope + labels for the tier signals
```

## Review Risk Tiers

Every review verdict is bound to the **exact head SHA** inspected. If the head
moves, merge readiness expires until that SHA is reviewed.

| Tier | Examples | Review policy |
|---|---|---|
| High | auth/authorization, approvals, secrets, cross-schema boundaries, migrations/persisted contracts, concurrency/distributed state, replay/idempotence, data loss | Dedicated independent exact-head review. Any reviewer-authored semantic fix or material risk-changing correction requires a fresh independent reviewer. |
| Standard | Cohesive product/backend/UI behavior with bounded failure surface | Independent exact-head review; retain the same reviewer for correction rechecks when independence is intact. |
| Low | Tiny docs, tests, formatting, chore, or mechanical refactor with no observable contract/risk change | Schedule a sequential convoy of 3-4 same-domain review beads to one sticky reviewer identity. Process one PR/SHA and emit one verdict at a time; escalate on any semantic finding. |

**Reviewer lanes.** Standard- and low-tier reviews run on a sticky reviewer
lane: one reviewer session takes sequential reviews (and their correction
rechecks) so the repo context it loaded stays cached. A reviewer lane never
reviews a PR produced by an implementation lane it shares a session with.
High-tier reviews, and any review that needs a fresh reviewer, retire or
bypass the reviewer lane and start cold. Independence beats cache.

Risk tiers reduce repeated context loading, never the evidence required for the
actual change. Do not batch high-risk work or merge an unreviewed moved head.
"Sequential convoy" never means one multi-PR reviewer worker: the one-bead,
one-PR report contract remains intact, and the coordinator dispatches the next
low-risk bead only after the prior verdict returns.

## Central Mutation Authority

The coordinator owns canonical Beads lifecycle mutations. Worker agents do not.

Coordinator-only operations:
- `bd create`
- `bd update`
- `bd dep add`
- `bd close`

Workers may:
- read Beads state for context
- push code branches
- open or merge PRs when their worker contract allows it
- report structured follow-up items for the coordinator to materialize

Workers may not:
- create follow-up beads
- claim or release beads
- add or remove dependencies
- close original or review beads

## Runtime-Specific Dispatch Notes

- Use the runtime's native subagent mechanism.
- Never shell out to `codex` / `claude` / `opencode` / `gemini` binaries
  directly to create workers.
- Inside this package's docs, cross-reference bundled files by relative path.
  In dispatch prompts and commands, pass absolute paths (see "Discover
  subskills" in the package router).

### Runtime Table

| Runtime | Dispatch mechanism | Permission flag |
|---|---|---|
| Claude Code | `Agent` tool (formerly `Task`); pass `model` as the family alias for the tables' choice, run workers in the background and act on their completion notification | `--dangerously-skip-permissions` on the coordinator session; subagents inherit it |
| Codex | built-in subagent | `--yolo` |
| OpenCode | built-in subagent dispatch | use runtime's full-auto mode |

### Codex-Specific Dispatch Hardening

These rules are mandatory when the coordinator runtime is Codex:

1. Dispatch workers with `fork_context=false`.
2. Treat `wait_agent` timeout as "still running", not failure.
3. Require bootstrap evidence (`pwd`, branch) before a worker counts as active.
4. Apply the default 30-minute stall threshold above; a `wait_agent` timeout is
   not itself a stall.
5. Send one interrupt heartbeat requesting a 2-line status before classifying a
   worker as stalled.
6. Close completed probe/worker agents promptly so thread limits do not block
   new dispatches.

### Gemini CLI Note

If the runtime is Gemini CLI, do not use the skill markdown directly. Invoke
the matching custom subagent (`beads-worker` or `beads-pr-reviewer-worker`)
using this JSON shape:

```json
{"ISSUE_ID":"<BEADS-ISSUE-ID>","BASE":"<main or master branch>","WORKTREE_PATH":"<WORKTREE-PATH>","query":"<full selected worker skill prompt here>"}
```

## Bead Mutation Safety

Beads data lives in Dolt DB, not git-tracked files.

`bd create/update/close/dep` mutate the Dolt database directly. The sql-server
is a shared external service (`dolt_mode: server`, `--external` in
`.beads/config.yaml`): `bd` never starts or stops it. Worker worktrees share
the same DB via redirect files created by `bd worktree create`.

Rules:
1. Keep the Dolt server healthy. Run `bd dolt status` before heavy mutation
   loops. On `connection refused`, search `../../../references/known-errors.md`
   first; never start a local `dolt sql-server` as a workaround.
2. Never commit `.beads/` contents on code branches. The directory is
   gitignored; accidental `.beads/` diffs on code branches must be stripped.
3. Create beads sequentially. Never create multiple beads in parallel because
   the ID counter can race and overwrite existing beads.
4. Do not use `--deps discovered-from:` in `bd create`; it can panic. Create
   the bead first, then wire dependencies with `bd dep add`.
5. Use `bd dolt commit/push/pull` for Dolt version control, not manual file
   surgery. Use `bd vc status` to check for uncommitted changes.
6. The assignee-check-then-renew-then-mutate rule from "Claiming And The Stall
   Heartbeat" applies to every mutation here.

## Bead Closure Rule

A bead may only be closed by the coordinator when one of these conditions is
met:

1. Direct merge for trivial/simple changes:
   - worker pushes `agent/<id>`
   - worker reports `direct-merge-candidate`
   - coordinator fast-forward merges to `main`
   - coordinator closes the bead
2. GitHub PR merged:
   - worker pushes branch and opens PR, or coordinator opens it during
     reconciliation
   - coordinator blocks the original bead and stores `external_ref=gh-pr:<N>`
   - coordinator creates or reuses exactly one dedicated PR-review bead
   - reviewer worker may merge the PR (or enqueue it to the base branch's
     merge queue and report `merge-queued`), but coordinator confirms `MERGED`
     and then closes the review and original beads
3. Queued direct merge (base branch behind a merge queue):
   - worker pushes `agent/<id>` and reports `direct-merge-candidate`
   - coordinator opens a `queue-direct` PR and enqueues it with
     `gh pr merge --squash --auto`; no review bead is created
   - coordinator closes the bead only after `gh pr view` reports `MERGED`

Implementation workers and reviewer workers must never call `bd close`.

## Quality Gates

Workers must pass all quality gates defined in the repository's `AGENTS.md` /
`CLAUDE.md` before coordinator closure. The specific gate commands are
language/project-dependent. If a worker skips gates, the coordinator should
flag the issue and re-dispatch or escalate.

Test growth is governed, not free: workers and reviewers apply
`../../../references/test-growth-gate.md` (extend the nearest test, one gate
species per behavior, net delta stated, repo test budget respected). A PR that
fails a repo test-budget ratchet is an ordinary CI failure for the review lane;
the coordinator never raises a budget on a worker's behalf.
