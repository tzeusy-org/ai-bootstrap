# Token Efficiency

Applies to every subskill in this package, and to `th-projects` orchestration
(`../../th-projects/references/work-allocation.md` defers here). The
orchestration engine's cost is dominated by (a) fresh sessions re-reading the
same repo context at full price, (b) verbose command output landing in context,
(c) full test suites run repeatedly, and (d) oversized models on mechanical
work. These rules cap all four. They never override correctness rules — when a
safety rule and an efficiency rule conflict, safety wins.

## Cache-first execution: lanes over fan-out

**Economics** (verify against the live pricing page before quoting): a cache
read costs 0.05× base input on Opus 5.5 (a 20× discount), 0.025× on Fable 5.1,
and 0.1× on most other models. A cache write costs 1.25× on the 5-minute TTL
and 2× on the 1-hour TTL. Caches are **per model and per session prefix**, so a
fresh subagent starts cold: it pays the write premium on every token it has to
load (guidance, skills, spec slice, the code it explores) and then re-reads
them on every turn. A continuing session that already holds that context
re-reads it at 1/20th of the price. On Claude, Sonnet 5.5 costs half of Opus
5.5 on every token class, cache reads included ($0.10 vs $0.20/MTok), so
dropping a cache-read-dominated lane to Sonnet halves its cost. Haiku 5.5 is
about 1/20th of Sonnet 5.5 but bills prompts over 100K tokens at 5x, so keep it
to short one-shot dispatches.

Twenty parallel fresh sessions pay the context-loading cost twenty times.
A small number of long-lived sessions pay it once and then run on reads.
Default to the second shape:

- **A lane is the unit of dispatch; a bead is the unit of review.** A lane is
  one long-lived session (one model, one context) that runs an ordered chain of
  beads one at a time. Each bead keeps its own claim, worktree, branch, PR,
  report, and closure. Only the session, and so its cache, carries over.
  Continue the lane's session for the next bead (Claude Code: `SendMessage` to
  the worker's agent id/name; Codex: send input to the same thread) instead of
  spawning a fresh worker.
- **Default one lane.** Run a second lane only when a ready chain has none of
  the cohesion signals in `../../th-projects/references/work-allocation.md`
  with the active lane (a second lane on the same epic still needs that
  check), **and** either the owner asked for throughput or the active lane is
  idle, waiting on CI or review. The ceiling is one budget
  shared by every coordinator on the tracker (the `beads.max_lanes` kv key,
  else 4). Parallelism is how you buy wall-clock time. It is never the default
  way to cut cost.
- **Order for context locality.** Within a lane, run next the top ready bead
  of the epic the lane just worked: same epic usually means same module, spec
  area, fixtures, and review surface, and it finishes the epic sooner. The
  finish-first queue decides everything else, including which epic a new or
  emptied lane takes. Locality yields to a P0, a pinned epic, or work two or
  more priority levels more urgent: those take the lane at the next bead
  boundary or open their own.
- **Ride autocompaction; don't flee it.** Long lanes are expected to compact.
  Compaction keeps the stable prefix (system prompt, tools) warm and replaces
  history with a summary, which is far cheaper than a cold start. The safety net
  is that every bead stays cold-start self-contained (beads-writer) and every
  hand-off lives in a durable artifact (report, PR, notes). Never count on
  pre-compaction context to recover state.
- **Retire a lane** (end its session and start the next bead cold) when any of
  these holds: the next bead shares no context with the lane; the required
  model tier changes (the cache is per model, so a switch is a cold start
  anyway); the work needs independence (a reviewer must never share an
  implementer's lane); or the lane shows compaction drift (re-asking settled
  questions, contradicting its own earlier decisions, losing the acceptance
  criteria). On drift, retire the lane early instead of pushing it further.
- **Fan out only when independence is the point.** Adversarial review,
  exact-head review, and the questionnaire's independent vetting need fresh
  context for correctness, so keep them. Read-only helpers that only save the
  lead from reading are usually a loss: run the reading inline in the lane
  unless it would flood the lane's context with material the lane will never
  use again.
- **Keep the wake inside the TTL.** A wake that lands after the cache TTL
  re-pays the whole context at write price. The coordinator's cadence is keyed
  to the runtime's actual TTL (`../subskills/beads-coordinator/references/runtime-and-safety.md`
  → "Orchestrator Wake Cadence"). Don't assume 5 minutes.

## Command output discipline

- Never dump unfiltered `--json` output into context. Project to the fields you
  will actually use:

  ```bash
  bd ready --json | jq -c '[.[] | {id, title, priority, type, labels, assignee}]'
  bd list --status=blocked --label pr-review --json | jq -c '[.[] | {id, title, labels, assignee, external_ref}]'
  bd show <id> --json | jq '{id, title, status, assignee, labels, external_ref}'   # coordinator view
  ```

  Workers implementing a bead additionally need `description`,
  `acceptance_criteria`, and `notes` — project to those, not the full record.
- For `gh`, always pass an explicit `--json <fields>` list; never fetch default
  field sets.
- Route long stdout (test runs, builds, installs) to a file; read back only the
  exit status and the failure tail:

  ```bash
  <gate command> >"$TMPDIR/gate.log" 2>&1; status=$?
  [ $status -ne 0 ] && tail -40 "$TMPDIR/gate.log"
  ```

- Never re-run a command just to re-see output you already have in context.

## Verification discipline

- While iterating, run only the tests covering the changed area (test file,
  package, or `-k` selection). Run the repository's full required gate exactly
  once, immediately before handoff, with the runner's quiet flag (`-q`,
  `--quiet`, `--silent`).
- On a full-gate failure, iterate on the failing subset (`--lf`, named test
  ids), then re-run the full gate once more.
- This narrows iteration only — it never substitutes a lighter gate for one the
  repository defines. The final pre-handoff run is always the full defined gate.

## Model right-sizing

- Follow the assignment tables in
  `../subskills/beads-coordinator/references/runtime-and-safety.md`. Default
  down, escalate on evidence: a wrong-too-weak dispatch costs one redispatch; a
  habitually-too-strong dispatch taxes every bead.
- **Model continuity beats per-bead right-sizing inside a lane.** Pick a
  lane's model once, from the highest tier among its chain's beads, and keep
  it. Switching models mid-lane throws away the cache. Right-sizing applies
  when a lane is formed and to one-shot dispatches (probes, formatting, a
  lone bead).
- If the bead carries a `complexity:<tier>` label (stamped by `beads-writer`),
  use it directly instead of re-deriving complexity from the description,
  unless the design/specification override or Reconciliation Floor in
  `../subskills/beads-coordinator/references/runtime-and-safety.md` applies.

## Reference loading

- Load a reference file only when its owning step is actually reached; never
  preload the whole `references/` tree.
- Search `known-errors.md` with `rg -i -n '<error text>'` and read only the
  matching section — do not read the whole catalog on every error.
