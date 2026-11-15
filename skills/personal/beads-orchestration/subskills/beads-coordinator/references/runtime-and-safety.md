# Runtime And Safety

Load this file before dispatching workers or mutating Beads state.

## Claiming And The Stall Heartbeat

Mutual exclusion comes from two real mechanisms, **not** from notes-based
tokens:

1. **Atomic claim** — `bd --actor "$COORDINATOR_ID" update <id> --claim`
   atomically sets the bead's `assignee` to you and `status=in_progress` in one
   operation. It is idempotent if you already hold it. This is the cross-actor
   mutual-exclusion primitive: only one actor can win the `assignee`, and the
   actor is whatever `--actor` resolves to, so it only excludes peers that use
   distinct ids ("Parallel Coordinators" below). Verify success from the
   command result (`assignee` is this coordinator), not from a later reread.
2. **Worktree creation backstop** — `bd worktree create` fails when the
   `agent/<id>` branch already exists. This prevents a same-actor session from
   double-dispatching a bead it already has in flight. If worktree creation
   fails on an existing branch, do not dispatch a second worker.

Never model claiming as a read-then-write of a token in `notes`/`design`. There
is no atomic conditional update / compare-and-swap via the notes field; that
would be a read-then-write race. The `bd --actor "$COORDINATOR_ID" update --claim` flag is the atomic
operation.

### Stall heartbeat (stall detection only)

The coordinator writes a heartbeat timestamp so a crashed/abandoned claim can be
detected and reclaimed by a recovery pass (cleanup or another coordinator). This
heartbeat is **explicitly not a mutual-exclusion mechanism** — `assignee` is.

Store exactly one canonical heartbeat block in `notes` (replace it in place;
never append duplicates):

```text
[beads-heartbeat]
owner=<COORDINATOR_ID>
last_heartbeat_at=<iso8601>
[/beads-heartbeat]
```

Heartbeat and stall parameters (canonical; do not restate elsewhere):
- Heartbeat TTL: 20 minutes.
- Renewal target: every 5 minutes while active, and before every mutation.
- Worker stall threshold: at least 30 minutes without a progress signal before
  force-release, unless a runtime-specific note below states otherwise. A technical wait with a recorded resume event is not a stalled implementer.

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
- A bead held by another actor is adoptable only under "Parallel Coordinators"
  → "Adopt the dead, guarded": no live registration for its assignee, and a
  bead heartbeat past the stall threshold. Plain `--claim` refuses a populated
  assignee anyway (`../../../references/known-errors.md`).
- Never "win" a live foreign claim by writing a newer timestamp; the heartbeat
  does not arbitrate ownership.

## Parallel Coordinators

Several coordinators may run against one tracker at once (separate sessions,
runtimes, or machines). The tracker is their only shared state. Each rule
below stops one coordinator from duplicating or undoing another's work.

- **Identity.** Mint one `COORDINATOR_ID` per run with
  `uv run scripts/coordinator_registry.py new-id --runtime claude`
  (`coordinator:<runtime>-<YYYYMMDD>-<8 hex>`, e.g.
  `coordinator:claude-YYYYMMDD-xxxxxxxx`), and pass `--actor "$COORDINATOR_ID"`
  on every `bd` command. Why: `--claim` assigns the bead to the resolved actor,
  which otherwise falls back to `git user.name`; two coordinators sharing a git
  identity would each read every claim as their own, and the atomic claim would
  exclude nothing. Write the flag into each command: shell state, including an
  exported `BEADS_ACTOR`, does not survive between tool calls on most runtimes.
  The same id is the heartbeat `owner=` and the registry key.
- **Registry.** [`../scripts/coordinator_registry.py`](../scripts/coordinator_registry.py)
  is the only writer of your `beads.coordinator.<id>` kv entry; it computes
  `expires_at` itself, because a hand-typed timestamp is how a dead coordinator
  stays "live" for a day:

  ```bash
  REG="uv run scripts/coordinator_registry.py"
  $REG register --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID" --wake-minutes 30 --startup  # preflight
  $REG register --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID" --wake-minutes <planned delay> [--extra-lanes K]  # every wake
  $REG deregister --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID"  # on stop
  ```

  `--startup` exits 3 when the id is already live: a new launch mints a new id.
  A genuine continuation resumes the existing session/actor, without startup. `register`
  skips the write while the stored expiry still covers the next wake, so
  routine wakes add no tracker history. Pass `--extra-lanes K` while you run
  lanes the tracker cannot see (for example a separately running review session)
  so peers count them. A coordinator is live while its `expires_at` is in the
  future (and no more than 3 hours ahead). Liveness is per coordinator, not
  per bead, because a coordinator sleeping between event-driven wakes lets
  bead heartbeats lapse, and a bead blocked on review carries no fresh
  heartbeat at all. The same script's readers back `rank_ready_work.py` and
  `beads-cleanup`.
- **Execution capacity and retained WIP differ.** Global `beads.max_lanes`, else
  4; only the owner changes it. `capacity.occupied/free` counts currently executing
  implementation/review work and explicit active `extra_lanes`. A package anchor
  `in_progress` counts once, regardless of member count. A blocked/open anchor
  parks execution while retaining every member claim, worktree and owner session.
  Unpackaged one-bead work follows the same rule. A closed anchor cannot hide a
  still-running member; the ranker conservatively counts pending active work.
  Review records count execution while `in_progress`; finish the report and return
  them to `blocked` when waiting. Never mark work parked while its job still runs.
  Retained implementation packages count separately in `capacity.owned/free_wip`.
  Owned WIP counts only outcomes agents are building. Two kinds of claim keep
  their claims and worktree but leave `owned`, listed under
  `capacity.owned_excluded`: a blocked anchor parked on an owner gate (a gate
  label such as `needs-human`, `human-gate` or `owner-held`, the ranker's
  `OWNER_GATE_LABELS`, or a same-head `held` canonical review receipt), and any
  claim whose coordinator has no live registration. Lanes keep working on other
  ready work while the human acts; dead claims are adoptable under the guarded
  rule below, not held. Technical waits (CI, review, merge queue) and every
  `in_progress` anchor still count, and a dead `in_progress` claim still
  reserves execution until recovery.
  The same budget bounds new-outcome WIP; reviewers and corrections of an existing
  package need an execution slot, not a second implementation-WIP slot. This admits
  independent review even when every implementation owner is parked, including
  a one-lane budget. If retained parked work fills WIP, the queue may admit one
  overflow package for a genuine P0 emergency or the runnable prerequisite frontier
  of an owned outcome. Execution still fits the existing budget; retained WIP can
  exceed it by only this one package, never repeated spillover. The queue marks
  `wip_exception`; complete it or publish a concrete handoff before another such
  admission. A parked overflow is not permission for indefinite exclusive custody.
  Native agent/thread limits are separate: an idle retained thread may still occupy
  a runtime slot. Resume the existing session, check native
  availability, and publish an explicit recovery handoff if a real runtime ceiling
  requires retirement. Do not solve thread exhaustion by stealing claims or raising
  the Beads budget. Concurrent claim races can briefly overshoot; the next freed
  slot absorbs it rather than admitting more work.
- **One priority queue.** Every coordinator and looper uses `rank_ready_work.py`
  `beads-work-queue/v2`. After genuine P0 emergency work, drain runnable reviews,
  then existing-owner corrections/resumes, before admitting new implementation.
  Priority applies to queued work: once higher-class actions are admitted/running,
  spare execution and WIP capacity can admit disjoint work. One running review is
  not a fleet-wide idle barrier. Active jobs finish or reach a safe checkpoint;
  a newly queued review gets the next freed slot. Do not reserve an idle review
  lane or start another coordinator layer. The report's `queue` is admission order;
  `candidates` is diagnostic finish-first implementation order and never authorizes
  dispatch. Its `admission` result blocks dispatch on missing/partial/inconsistent
  review evidence. A lost atomic claim refreshes this queue; do not skip a failed
  high-class action and fill its slot with lower-class work from an old snapshot.
- **Mutate only what you own.** Step 0 PR reconciliation, decision sweeps,
  shaping, review-bead creation, and closure act only on beads whose
  `assignee` is you. A bead held by a live registered coordinator is never
  touched, whatever its bead heartbeat says. Hold an epic's claim while
  creating its children: child ids come from a per-parent counter that two
  creators can race.
- **One implementation owner, separate reviewer.** Package members remain with
  the implementation lifecycle actor through review/corrections. The reviewer
  claims only its canonical review bead and uses an independent worktree. It
  never takes the package's implementation claims. Controller runs hold those
  claims for their named worker; loopers hold their own. Direct owner/reviewer
  communication needs no coordinator relay per finding.
- **Adopt the dead, guarded.** Only a `coordinator:` assignee with no live
  registration is adoptable; a human's or another tool's claim never is. A
  claim left by a coordinator from before this section (an assignee without
  the prefix) qualifies only when the bead's heartbeat block names that same
  assignee as `owner=`: only coordinators write that block. An
  `in_progress` bead also needs a bead heartbeat past the stall threshold;
  `open` and `blocked` beads qualify on the registration test alone.
  `rank_ready_work.py` lists the candidates: `unowned_claims`
  (`in_progress`, still spending a lane), `stranded` (ready but assigned),
  and `orphaned_blocked` (`blocked`, with `kind` `pr` or `gate`). It treats
  every `coordinator:` id format alike, including pre-registry
  `coordinator:<uuid>` ids, which never register and so are never live.
  The `--if-assignee` / `--if-status` guards make
  adoption a compare-and-swap, so two coordinators adopting at once cannot
  both win; exit code 13 means the precondition no longer held (a peer got
  there first), so skip the bead:

  ```bash
  # in_progress, abandoned: release it to the queue (cleanup's release-claim-candidate)
  bd --actor "$COORDINATOR_ID" update <id> --if-assignee '<old>' \
    --if-status in_progress --assignee '' --status open --json
  # open, stranded by a dead assignee: clear the assignee so it re-enters the queue
  bd --actor "$COORDINATOR_ID" update <id> --if-assignee '<old>' \
    --if-status open --assignee '' --json
  # blocked (on a PR, or a pr-review-task bead): take ownership in place, status unchanged
  bd --actor "$COORDINATOR_ID" update <id> --if-assignee '<old>' \
    --if-status blocked --assignee "$COORDINATOR_ID" --json
  ```

  Recover the package using its manifest and claim journal, not one arbitrary
  member at a time. Inspect its published/local work before changing claims.
  If any member is held by a live peer, stop and reconcile the partial recovery;
  do not dispatch split ownership. Reviewer claims recover separately after its
  liveness check. Preserve independent reviewer findings and implementation owner
  resume locator. Never restart a live fleet as a side effect of a harness update.

## Orchestrator Wake Cadence

Wake on actual implementation, review, CI, dependency or owner events. Cache
reuse is useful but does not justify repeated tracker scans or wakes solely to
keep a cache warm. Keep registry liveness renewed for the planned wait using
`coordinator_registry.py`; explicit waits carry a concrete resume event.

Use native runtime completion notifications/continuation (Claude agent/session,
Codex thread) when available. A bounded fallback checks only pending events and
liveness, not a full fleet audit. Choose it for expected CI/event latency within
registry expiry limits. Never interpret a wait timeout as implementation failure.

### No-progress frontier

At a genuine frontier, resolve engineering decisions under decision-autonomy,
name technical waits and owner gates, and wait for their event. If polling is
needed, use 60 minutes and stop after 3 consecutive no-op wakes. Renew registry
before waiting; apply the existing guarded adoption rules, not cache timing.
On stop, publish current packet/claims/recovery state and deregister. Do not keep
an outcome indefinitely exclusive without a runnable next step or agreed handoff.

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
| `TRIVIAL_COMPLEXITY_MODEL` | Haiku 5.5 Low | GPT-6.1 Terra Low | gemini-3.8-flash |
| `DESIGN_AND_SPECIFICATION_MODEL` | Opus 5.5 High | GPT-6.1 Sol XHigh | gemini-4-argon |

These are deliberate local choices over the vendors' starting-point guidance.
On Codex, Sol 6.1 is the workhorse and Terra 6.1 is reserved for trivial
work. Dispatch trivial work (see the TRIVIAL criteria below) with Terra Low;
never dispatch Terra for anything that needs judgment. Dispatch non-trivial
work below medium with Sol Low, medium and high work with Sol High, and epic
work and plan/scoping/design/specification drafting with Sol XHigh. Sol is the
only non-trivial Codex model: do not dispatch Astra, Luna, or GPT-5.6-generation
models. Escalation on Codex is an effort step within Sol (Low -> High ->
XHigh), never a model-family switch.

Anthropic recommends starting Opus 5.5 at its default `medium` effort and
tuning against task-specific evals. This policy mirrors the Codex split: Haiku
5.5 for trivial work only, Sonnet 5.5 (priced like Sol) for
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
| GPT-6.1 Terra Low | `gpt-6.1-terra` | `low` |
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
second opportunity to cheapen it: in particular, Terra `low` is the only Codex
setting for TRIVIAL work. Right-size when a lane is **formed**, from the highest
tier in its planned chain. Never switch a live lane's model: that is a cold
start, so retire the lane instead.

### Reconciliation Floor

Cross-package acceptance and shared runtime/trust-contract closeout requires the
model tier appropriate to those decisions, generally `EPIC_COMPLEXITY_MODEL`.
Mechanical evidence aggregation can use the ordinary complexity tier. Do not
create a new reconciliation generation or mini-coordinator to obtain this tier.

## Review Risk Tiers

The final readiness receipt names the **exact head SHA** to merge. Its substantive
review evidence may be reused when relevant content and base/contract assumptions
are proven unchanged; a changed SHA alone need not force another broad review.
New relevant content invalidates affected evidence. Current CI/queue/labels and
runtime permissions are separate time-sensitive facts.

| Tier | Examples | Review policy |
|---|---|---|
| High | auth/authorization, secrets, cross-schema boundaries, migration, concurrency, replay, data loss | Independent review of the cohesive package; new semantic content or reviewer-authored fixes requires fresh independent judgment as warranted by risk. |
| Standard | Bounded product/backend/UI behavior | Keep the same independent reviewer through corrections; inspect the changed content and affected assumptions. |
| Low | Docs, formatting, mechanical changes | Reuse one independent reviewer session across sequential packages; each has its own content evidence and current-head receipt. |

A reviewer never reviews content it authored. High-risk content can be cohesive
within one package; risk is a reason for appropriate review, not a mandatory
separate PR per tracking item. The shared delivery model owns the evidence contract.

## Central Mutation Authority

The controller owns lifecycle writes for dispatched workers. A looper is its own
controller for its held package; an independent review looper owns only its review
record. Dispatched implementation/reviewer workers report lifecycle changes.

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
6. Close completed one-shot probes and retired sessions promptly. A PR-opened
   handoff does not retire its implementation owner: retain that session for
   review/CI corrections and related packages until explicit handoff or completion.

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
   Heartbeat" applies to every mutation here, and every command carries
   `--actor "$COORDINATOR_ID"` ("Parallel Coordinators").

## Bead Closure Rule

Apply [Owner merge hold](coordinator-loop.md#owner-merge-hold) before every remote
merge/enqueue/close-PR/base-push action. Unknown hold/policy/check status fails
closed. A verified owner-performed MERGED/CLOSED state still reconciles truthfully.

The lifecycle holder closes a member only when its accepted criteria are proven
and its relevant source landed on the actual delivery base, normally main.
A package merge alone closes neither every member nor the original outcome.
Use the delivery helper's per-member closure plan, then freshly verify the
landing/evidence and guarded ownership before mutation. Runtime/activation criteria
remain open until proved; approved deferral/supersession is explicitly recorded
and never reported as delivered behavior.

PR routes retain exactly one anchor `external_ref` and one independent review
record. A queue receipt is waiting, not closure. A merge to a temporary integration
branch is accumulated source. Direct pushes require repository permission and an
independent verdict plus the repository-assigned full gate; queue-direct is no
review exemption. Dispatched workers do not close Beads; the controller or owning
looper reconciles members and outcome. Preserve unfinished recovery work.

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
