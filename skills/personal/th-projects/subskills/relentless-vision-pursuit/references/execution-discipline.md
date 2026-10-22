# Execution Discipline: Lanes · Routing · Checkpoint · Resume

Pursuit runs are the largest orchestrations in th-projects. A run that
violates any rule below is a defect, not a style choice — this is the
`/th-engineering` bar applied to the orchestration itself: deterministic
ordering, idempotent resume, fail-safe writes, no silent caps. Prefer a
boring, resumable run over a clever one.

The economics behind these rules are canonical in
[`beads-orchestration/references/token-efficiency.md`](../../../../beads-orchestration/references/token-efficiency.md)
→ "Cache-first execution". In short, every audit prompt carries the same big
prefix: doctrine, the applicable bar, the already-known ledger, and topology.
A fan-out pays that prefix at write price once per agent. A lane pays it once,
then re-reads it at a fraction of the price for every later surface.

## 1. Lanes, not batches — never more than 3 sessions in flight

Run Phases 1–2 as **two lanes**, each one long-lived session:

- **Audit lane.** Walks the surfaces one at a time in locality order, so
  adjacent routes and modules share loaded code, and ends with the
  cross-cutting sweeps. The sweeps get better from having seen every surface
  first, which a fresh sweeper never has.
- **Ideation lane.** Runs every lens in one session. All the lenses read the
  same doctrine and topology, and a single session also dedups its proposals
  across lenses as it goes.

Dispatch each lane once (`Agent`, background), then drive it one surface or
lens per turn by continuing that session (`SendMessage` to the lane's agent
id/name), each turn returning that unit's structured output. The two lanes may
run concurrently. The ceiling of **3** sessions in flight stays hard: a
larger token budget buys *depth over hours*, never concurrency.

- Lanes ride autocompaction. The harvest file (§3) is the record, so a lane
  that compacts loses nothing the synthesis needs.
- **Retire and restart** a lane cold, resuming from the state file, when it
  shows compaction drift: it re-reports ledger items, contradicts an earlier
  verdict, or loses the bar.
- **Fan out only for independence**, such as a fresh-context adversarial check
  on the top-ranked moves before the dossier ships. Fan out for size only
  when one surface is so large its audit would flood the lane with code
  nothing later needs. Such an auditor is one extra session and counts toward
  the ceiling of 3.
- **Pacing.** Continuous turns keep the cache warm on their own. When the
  owner's usage window calls for spreading a run over hours, pace lane turns
  with `ScheduleWakeup` at **1200–2700 s**, inside the 1-hour cache TTL. Never
  use 3600 s: the TTL runs from the start of the previous request, so an
  hourly tick always lands cold.
- A Workflow-tool run is allowed only when the owner explicitly asks for one.
  Even then, run each lane as one `agent()` over its full ordered unit list
  (never one `agent()` per surface), and still keep at most 3 in flight.

**Precedent:** the 2026-07-22 butlers pursuit run launched ~10 concurrent
agents and took the owner from 5%→80% of the usage window in ~15 minutes;
it was killed mid-flight and resumed as `[3,2,2,…]` hourly batches. Those
batches fixed the concurrency, but every agent still re-wrote the shared
prefix cold, and every hourly tick missed the cache. Lanes fix both.

## 2. Model routing by task difficulty

Caches are per model, so a lane keeps one model for its whole life. Tier each
lane or one-off task to its hardest recurring work:

| Role | `model` | `effort` |
|------|---------|----------|
| Orchestrator (this session): grounding, surface clustering, lane planning, synthesis, dossier authorship | session model | — |
| Ideation lane (whole-system reasoning, must name real integration points) | `opus` | `high` |
| Audit lane (scoped critique against a known bar, then cross-cutting sweeps) | `opus` | `medium` |
| One-shot mechanical passes (surface scoping, ledger-dedup extraction, wiring checks) | `haiku`, or inline in the orchestrator | `low` |

Sonnet 5.5 costs half of Opus 5.5 on every token class, warm cache reads
included, so the audit lane's `opus` at `medium` is a deliberate quality choice
for critique, not a free one; revisit it if audits prove routine. When genuinely unsure which tier a lane needs, round **up**: a weak
plan costs more than the model that would have made a good one.

## 3. Checkpoint every unit to disk

Harvested findings must never live only in conversation context or in a
lane's memory. A kill or a compaction loses at most the unit in flight.

- After each lane turn, **append** that surface's or lens's structured output
  to a durable harvest file
  (`<dossier-home>/<date>-vision-pursuit-harvest.json`, gitignored), keyed by
  lane and unit label.
- Write atomically: temp path in the same dir, then `rename()` over the
  target — a crash mid-write never leaves torn JSON.
- Maintain a state file
  `{lanes: {<lane>: {agent_ref, model, units_done, next_unit}}, unit_plan,
  harvest_path}` beside it, updated after every unit. Harvest + state together
  must be enough to resume, or to hand-synthesize from a cold start. Phase 3
  reads the harvest file, not live returns, so the dossier can be rebuilt from
  disk even if a lane or the session context is lost.

## 4. Cadence, scale, and resume hygiene

- Scale to the surface count. Small projects (≤6 surfaces) skip the lanes and
  run audits and lenses inline in the orchestrator, sequentially. The phases
  and posture still apply; only the orchestration shrinks.
- Resume: re-dispatch a lost lane cold with the ledger, the bar, and its
  `next_unit` from the state file. Never re-run finished units.
- Keep the state file current so wakeups survive context summarization.
- Re-run soon after a release → expect fewer NEW findings; that is success.
  Report movement, don't pad.
- If a fleet is mid-execution on a prior pursuit epic: audit the surfaces
  it has already landed (to measure) and ideate the lenses it is not
  touching; note the overlap in the report instead of filing colliding
  beads.
