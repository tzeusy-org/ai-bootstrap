---
name: project-direction
description: Use when deciding what a project should work on next, comparing competing priorities, deriving a milestone from VISION, brainstorming new feature candidates that fit the project's doctrine, judging whether a project's goals and requirements are settled enough to begin authoring specifications, sequencing an approved specification into an execution graph, or deciding what to do about already-confirmed spec drift. Triggers on "what should we work on next", "prioritize features", "is this roadmap aligned", "what's highest leverage", "what's the next milestone", "should we build this or Y", "are we ready to write specs", "break this approved spec down", "brainstorm features", "what could we build".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-03"
---

# Project Direction

Decide what a project works on next. Ground every decision in specs, repo evidence, and implementation reality. Optimize for spec alignment, tractability, leverage, low-churn execution — not feature count.

Do not use when:
- One concrete new feature, idea → spec → `../project-feature-request/SKILL.md`.
- Score repo health / confirm findings → `../project-review/SKILL.md` (hands its packet here).
- Exhaustive spec↔code reconciliation, or "does code match spec?" before any drift is confirmed → `../spec-concretization/SKILL.md`.
- Backlog mechanics only, no direction analysis → `/beads-orchestration` (beads-writer).
- Recurring orchestrated pursuit loop (lane-sequenced surface audits + ideation at
  scale + ungated work plan) → `../relentless-vision-pursuit/SKILL.md`;
  ideation here is the inline single-brief mode it builds on.

## Core Rules

1. **Ground work in its governing contract** — use the proportional
   [work-allocation contract](../../references/work-allocation.md#governing-contract).
   Routine settled fixes go directly to beads-writer; this project-direction
   lifecycle governs work that actually needs direction or specification.
2. **Honor actual signoff gates** — new or unresolved behavior follows its
   applicable spec/approval path; repository-mandated requirements remain binding.
3. **Evidence over assumption** — cite files, functions, spec sections. Label claims [Observed] / [Inferred] / [Unknown].
4. **Push back** — flag misaligned, premature, unrealistic, infeasible work directly.
5. **Minimize churn** — remove dead paths over back-compat shims (`/th-engineering` cruft-cleanup is the bar); serialize conflicting work.
6. **Rigor where truth changes** — deep reconciliation guards *changes to normative artifacts*; consuming them is one verification pass, not four.
7. **VISION is continuous** — every candidate, spec delta, bead, discovery, and
   closeout cites the doctrine mandate it advances. Revalidate when the recorded
   baseline commit changes; never treat a past doctrine check as permanent.
8. **Relentless, bounded progress** — resolve non-blocking gaps in scope, create
   explicit investigation/spec-first work for real unknowns, and keep moving to
   the next unblocked spec outcome. Never hide work in TODOs or smuggle adjacent
   ideas into the active graph.
9. **Readiness before authoring** — a project about to write its *first* specs
   passes the launch gate ([`references/launch-gate.md`](references/launch-gate.md))
   first. Readiness is a verdict with cited evidence, never a feeling; a
   non-empty reopen-list means shape work, not spec work.

## Workflow

### Preflight: Gather Context

Infer from the repo. Ask only for a parameter the repo cannot answer and whose
value changes the plan; otherwise state the assumption and proceed:

| Parameter | Values | Default |
|-----------|--------|---------|
| Project type | library, SDK, backend, SaaS, frontend, CLI, mobile, monorepo, ML/data, internal tool, IaC | Infer |
| Primary user | developers, end users, internal team, enterprises | Infer |
| Maturity | prototype, beta, production, mission-critical | Infer |
| Spec location | openspec/, spec/, docs/design/, or none | Infer from scan |
| Focus | full direction, launch gate, feature evaluation, spec-drift check, work decomposition | Full |
| Inputs on hand | project-review packet, feature-request spec delta, spec-concretization report, cold start | Cold start |

Then check for upstream inputs — they change how much of each phase runs (Receiver Protocol below).

### Receiver Protocol: Consume Upstream Packets, Don't Re-Derive

**From `../project-review/` (handoff packet).** Read the newest
`docs/reviews/*-packet.md` (the shared packet home — review writes it, this
subskill consumes it; ask before using anything older than the latest). Fresh
= repo HEAD matches the packet's recorded commit SHA, or user accepts
staleness. Fresh packet:
- Adopt its Phase 0 baseline (pillar maturity, source-of-truth order, normative requirements, contradictions) → Phase 1 = doctrine *check*, not re-derivation.
- Confirmed findings = Phase 2 evidence. Agent C skips review-scored dimensions (test confidence, observability, delivery readiness) → narrows to architectural fitness *for the proposed direction*.
- Feed sequencing constraints, dependency hints, deprioritized items to Agent D + Phase 3.
- Findings flagged as needing doctrine/spec updates → Phase 1/2 work items, not re-investigation.

**From `../project-feature-request/` (signed-off spec delta).** Funnel already
did doctrine, topology, design, spec work — do not re-run while its recorded
doctrine/spec commit still matches. If the baseline changed, revalidate only the
affected mandates. Phase 2 narrows to integrating the delta; Phase 3 sequences
it against existing work.

**From `../spec-concretization/` (run report).** Read the newest
`docs/reviews/*-spec-concretization.md`; fresh = its `Concretized:` SHA
matches HEAD. Its Escalations section is the input: architecture/trust/
migration gaps enter Phase 2 as design work; sequencing questions arrive with
their beads already filed — Phase 3 orders them, it does not re-create them.

**Cold start.** No packet, no delta → run phases as written.

### Reconciliation Protocol: Proportional to Risk

Reconciliation passes = subagent-driven deep-dive reviews of generated artifacts. Same brief every pass (fill only the braces); it states the stakes and the evidence contract instead of a persona:

```text
Reconcile the artifacts below against their sources of truth. They are
normative for this project: downstream agents implement whatever they say,
so an error that survives this review multiplies.

Phase: {phase, what it changed, its acceptance criteria}
Artifacts under review: {paths}
Sources of truth: {doctrine / lore / spec / code paths}

You have not seen how these artifacts were produced; treat them as neither
correct nor incorrect. Look for contradictions, omissions, requirement
drift, weak assumptions, and unverifiable claims. Report every finding you
can evidence, including minor and low-confidence ones. For each: the
file/section reference, the acceptance criterion it affects, severity,
confidence, and the minimum precise change that resolves it. The
orchestrator ranks and filters, so leave nothing out for seeming small.
Where an area is clean, say what you checked. End with a verdict:
acceptance criteria met or not met, naming the findings that block
acceptance. Read-only: propose changes, do not apply them.
```

Two tiers, chosen per phase by what the phase did; change-tier depth scales
with blast radius (same sizing vocabulary as
`../project-feature-request/SKILL.md`):

| Tier | When | Protocol |
|------|------|----------|
| **Change-tier, small** | Modified one spec's requirements; no doctrine/lore edits, no cross-boundary contracts | 1 pass, fixes applied, 1 confirming pass |
| **Change-tier, medium** | Several specs, new external surface, or lore edits | 2 passes + confirming pass |
| **Change-tier, large** | Doctrine edits, new subsystem, or cross-boundary contract changes | ≥4 dedicated passes (`R1`-`R4`), fresh subagent each, fixes applied between passes; continue while acceptance criteria unmet |
| **Verify-tier** | Phase only *consumed* normative artifacts: doctrine checks, drift analysis, graph from approved changeset | One verification pass; escalate to change-tier only if it finds the consumed artifacts need modification |

A pass converges when no acceptance-blocking finding remains; other findings
are fixed or recorded without spending another pass on them.

Every change-tier pass uses a fresh subagent regardless of size — size sets
the pass count, never self-review. Run
`uv run <th-projects>/scripts/spec-trace-check.py <repo-root> --authoring` before the
first pass; mechanical findings are fixed directly, not spent on subagents.

Mechanical validations (cycle checks, spec-link coverage, mandate coverage) = scripts-and-checklists — run directly; not passes, no subagents.

**Convergence ceiling**: artifact set still failing after 6 passes → stop, summarize unresolved findings with evidence, present disagreement to user, do not proceed on an unconverged artifact.

**Commit discipline**: commit normative changes when their phase converges (later phases build on a fixed base). Read-only runs (no normative artifact changed) make no commits, need no push gates. Push per repo convention, at end of run.

### Phase 1: Doctrine Alignment (project-shape Baseline)

Judge proposed work against the project's actual doctrine.

1. Obtain shape baseline: adopt a fresh review packet's baseline, or run `../project-shape/scripts/shape-scan.sh` and read `about/heart-and-soul/` + `about/legends-and-lore/`.
2. Map each proposed feature/initiative to baseline; flag doctrine conflicts.
3. Doctrine/lore docs need editing (contradictions, blocking gaps) → edit and run **change-tier** on them.
4. Docs sound, merely consumed → **verify-tier** (one pass over feature-to-doctrine mapping).

Acceptance: every proposed feature explicitly aligned/rejected/escalated against doctrine, with citations; any doctrine/lore edits reconciled (change-tier) and committed before Phase 2.

### Phase 2: Specification Scan + Fitness/Gap Synthesis

1. Run spec scan: `bash <skill_dir>/scripts/spec-scan.sh <repo_root>`
2. Parallel investigation (A, B, C) then synthesis (D) — roles + dispatch in `references/subagent-template.md`. Assign one primary evidence owner per concern; pass artifact paths plus a compact baseline manifest, not duplicated full documents:

| Agent | Role | Input | Output |
|-------|------|-------|--------|
| A | Doctrine/spec intent validation | Phase 1 baseline + scan + specs | Intent model, mandate checks, requirement fidelity |
| B | Spec adherence & workflows | Scan + specs | Spec drift inventory, workflow completeness |
| C | Implementation fitness | Scan + code (minus dimensions a fresh packet covered) | Test confidence, observability, delivery readiness, architectural fitness |
| D | Alignment & gap synthesis | A + B + C (+ packet constraints) | Alignment matrix, gaps, push-back list, spec deltas |

3. Synthesize into an OpenSpec changeset per `references/openspec-changeset.md` (tool-agnostic `openspec` CLI loop; `/opsx:ff` = Codex accelerator for the same procedure).
4. **Change-tier** reconciliation on the changeset (normative artifact). No changeset produced (pure analysis) → **verify-tier** on the analysis.

Acceptance: spec intent, implemented spine, gap analysis coherent and actionable; any changeset complete, internally consistent, traceable to doctrine + implementation evidence, reconciled, and committed before Phase 3.

### Phase 3: Beads Generation (Planning Graph Only)

Call `/beads-orchestration` (beads-writer) to build a full acyclic dependency
graph from the approved changeset — approval recorded as the
`Sign-off: <identity> <date>` line in its `proposal.md`
([spec-format § Sign-off marker](../../references/spec-format.md#sign-off-marker)). Load
[`references/work-allocation.md`](../../references/work-allocation.md) before
decomposing: preserve one finite accepted owner outcome and distinguish tracking
from coherent delivery packages. Existing related beads may share one owner,
branch, PR, and review cycle while retaining member acceptance.

1. Reuse or generate finite accepted outcomes and member tracking, with real
   dependencies and no cycles or overlapping implementation owners. Identify
   shared prerequisites once rather than cloning substrate work across epics.
2. Run the allocation contract's dedupe and cohesion scan across open/recently
   closed beads, active PRs, and the proposed graph. Propose cohesive packages
   among existing beads or serialize shared decisions; keep independent rollback
   paths separate. Do not split into sub-epics to satisfy child-count targets.
3. Populate every implementation bead's **Current Operative Packet** in its
   structured description/design/acceptance fields, using concise current
   instructions and exact authority/history references. Approved direction,
   adopted behavior, packet-complete scope, and runnable-now are distinct;
   settle a changed shared interface and source assumptions before releasing
   its dependents, once at that contract rather than once per child.
4. Map every in-scope `v1-mandatory` requirement to an implementation bead and
   verification path; one cohesive bead may cover several adjacent requirements.
5. Triage gaps, TODOs, unknowns, and expanded ideas through the allocation
   contract; keep corrections/local debt in scope, route boundary/spec changes
   back to feature-request, and capture adjacent scope separately.
6. Sequence tightened cross-cutting contracts as representation → propagation →
   enforcement when that creates independently safe rollout and rollback points.
7. Preserve the accepted outcome through foundation/dark merges and use one
   finite cross-child closeout path where needed: its checklist, an existing
   report, or one justified bead. Reuse it when gaps remain; do not create
   recursive reconciliation generations. Report beads may use
   `scripts/epic-report-scaffold.sh` (see `references/epic-report.md`).
8. Add a milestone-close callback that refreshes VISION mandate coverage and
   re-enters milestone synthesis when uncovered mandates remain.
9. Run mechanical graph validations (cycle check, complete mandatory-mandate
   coverage, spec-link coverage), then **verify-tier**.

Acceptance: graph acyclic, prioritized, spec-traceable, and honest about runnable
scope versus real waits. Accepted outcomes, proposed delivery packages, and
member criteria are explicit. Execution belongs to `/beads-orchestration`
(outcome-owning looper or coordinator), not this planning phase.

### Handoff Output (No Delivery Ownership)

Output the direction report per `references/work-plan-template.md`:
- Project's real direction?
- What to work on next?
- What to stop pretending it can do?
- Which beads graph was generated and why it's coherent with doctrine/lore/spec.

Do not execute/deliver the beads plan here — hand off explicitly to an outcome-owning looper or beads coordinator.

## Adapting to Focus

Focus modes change which phases do real work and which tier applies — the phase *questions* are always answered, even if one line backed by a citation.

- **Full direction analysis** (default): all phases as written; change-tier wherever normative artifacts change.
- **Launch gate** (pre-specification readiness: "are we ready to write specs", "is the vision settled enough to specify from", or any run that would author a project's *first* spec): administer the readiness question series to a **fresh-context reviewer subagent** — closed verdict vocabulary, cited evidence, falsification first. Phase 1 supplies the doctrine baseline and the parameter block; the gate replaces Phase 2 until it returns READY. `NOT READY` → a remediation brief routed by finding class (doctrine → project-shape, design contract → feature-request), no Phase 3. Read [`references/launch-gate.md`](references/launch-gate.md).
- **Milestone synthesis** (vision-generative: "what next" with no proposals on hand, or a milestone just closed): doctrine *produces* the candidates instead of judging proposed ones — mandate coverage matrix, ideas-ledger unparking, ranked milestone brief; user selects before anything enters Phases 2-3. Read [`references/milestone-synthesis.md`](references/milestone-synthesis.md).
- **Ideation** (vision-generative, divergent: "brainstorm what we could build", "what features would fit this project"): milestone synthesis's divergent twin — doctrine *grounds* new candidates instead of gating proposed ones. Mandate citation relaxes to a fit trace; candidates classed mandate-grounded vs vision-extending; user triages the brief (pursue → feature-request funnel, park → ledger, discard). No Phases 2-3 from this mode — pursued candidates re-enter via the funnel. Read [`references/ideation.md`](references/ideation.md).
- **Feature evaluation** ("should we build X?"): a *new, single* request routes to `../project-feature-request/SKILL.md` (its funnel returns a spec delta here). Run this mode only for portfolio questions (X vs competing priorities): Phase 1 verify-tier doctrine check on X, Phase 2 narrowed to the 8-dimension evaluation in `references/alignment-review.md`, Phase 3 only if X is approved. No commits unless specs changed.
- **Spec-drift check** (already-confirmed drift, e.g. from a review or spec-concretization packet; the inventory itself is `../spec-concretization/`): Phase 1 verify-tier (doctrine consumed). Phase 2 emphasizes B + C + D; produce a corrective changeset (change-tier) only for confirmed drift the user wants fixed — else deliver the drift inventory read-only. Phase 3 only when a changeset was produced.
- **Work decomposition** ("break this down"): assumes an approved spec. Phase 1 skipped unless the spec lacks a doctrine link. Phase 2 = one verify-tier pass confirming implementability. Phase 3 is the primary artifact (mechanical validations + verify-tier pass).

## Edge Cases

| Situation | Handling |
|-----------|----------|
| No specs exist | Launch gate first (Core Rule 9) — Phase 1 doctrine check, then `references/launch-gate.md`. READY → Phase 2 creates the initial changeset via `references/openspec-changeset.md`. NOT READY → remediation brief, no changeset. |
| No specs *and* no doctrine | The gate has nothing to administer against. Route to `../project-shape/` bootstrap; return here once doctrine is adopted. |
| `openspec` CLI unavailable | Stop changeset synthesis, report it; deliver analysis + drift inventory, recommend installing the CLI. Do NOT hand-write changeset scaffolds. |
| Specs outdated | Agent B catalogs drift; Phase 2 must produce a corrective changeset before Phase 3. |
| No clear direction | State it directly. Recommend a direction-setting workshop or `../project-shape/` bootstrap before feature work. |
| Conflicting specs | Flag each contradiction with evidence from both sides. Do not resolve — escalate to user. |
| Reconciliation won't converge | Convergence ceiling (6 passes): stop, present unresolved findings, await user judgment. |
| Massive spec surface (>50 sections) | Shard the inventory by non-overlapping capability slice. Never sample `v1-mandatory` requirements or active deltas; sample only explicitly post-v1/advisory material and label it. |

## Sample Triggers

- "What should we work on next in this repo?"
- "Are we ready to start writing specifications?"
- "Does the code actually match the spec?"
- "Should we build X, or is it a distraction?"
- "Break this approved spec into sequenced chunks."
- "Is our roadmap aligned with what the project really is?"

## References

| File | Read when | Content |
|------|-----------|---------|
| [`references/launch-gate.md`](references/launch-gate.md) | Launch-gate focus mode; before authoring a project's first spec | Pre-specification readiness question series, administration protocol, verdict rule, record + trend log, parameter block |
| [`references/milestone-synthesis.md`](references/milestone-synthesis.md) | Milestone-synthesis focus mode | Deriving candidates from doctrine: mandate coverage matrix, ledger unparking, milestone brief format |
| [`references/ideation.md`](references/ideation.md) | Ideation focus mode | Divergence lenses, fit-trace classing, mandate-grounded vs vision-extending routing, ideation brief format |
| [`references/direction-model.md`](references/direction-model.md) | Phase 2 (agents A, B, C) | Analysis dimensions: project spirit, requirement classification, current-state assessment |
| [`references/alignment-review.md`](references/alignment-review.md) | Phase 2 (agent D); feature evaluation | 8-dimension evaluation, classification buckets, push-back checklist, gap analysis |
| [`references/openspec-changeset.md`](references/openspec-changeset.md) | Phase 2 step 3 | How to synthesize an OpenSpec changeset via the `openspec` CLI |
| [`references/work-plan-template.md`](references/work-plan-template.md) | Handoff output | Output format, chunk/sequencing presentation, reconciliation reporting |
| [`../../references/work-allocation.md`](../../references/work-allocation.md) | Phase 3 decomposition and discovery triage | Cohesive bead boundaries, overhead test, ownership, mandatory coverage, gap/TODO/unknown routing |
| [`references/subagent-template.md`](references/subagent-template.md) | Phases 1-3 (reconciliation + dispatch) | Agent roles, dispatch template, depth limits, per-agent notes |
| [`references/epic-report.md`](references/epic-report.md) | Phase 3 (report bead structure) | Report bead template/execution, diagram integration, spec compliance matrix |

## Scripts

- [`scripts/spec-scan.sh`](scripts/spec-scan.sh) `<repo_root>` — discovers specs, design docs, roadmap, agent context, issue tracking, git activity by area. Run in Phase 2.
- [`scripts/epic-report-scaffold.sh`](scripts/epic-report-scaffold.sh) `<epic-id> [repo_root]` — used by Phase 3 report-bead executors; bootstraps report markdown from beads epic data, creates `docs/reports/` with metadata pre-filled.

## Diagrams

Workflow visuals (each `.svg` has an editable `.excalidraw` source alongside; regenerate with `/th-engineering` (excalidraw-diagram), `--format svg`):

| Diagram | Shows |
|---------|-------|
| [`references/diagrams/project-direction-workflow.svg`](references/diagrams/project-direction-workflow.svg) ([source](references/diagrams/project-direction-workflow.excalidraw)) | Phase-by-phase workflow, preflight through handoff |
| [`references/diagrams/project-direction-artifacts.svg`](references/diagrams/project-direction-artifacts.svg) ([source](references/diagrams/project-direction-artifacts.excalidraw)) | Analysis-agent and report-bead artifact flow |
