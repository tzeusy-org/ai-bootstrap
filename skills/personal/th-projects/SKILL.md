---
name: th-projects
description: >
  Use for project-level engineering governance: bootstrap or audit the
  doctrine/spec/topology/standards shape; concretize or amend one feature
  spec; reconcile specs and implementation; audit repo health; prioritize
  work; synthesize milestones; ideate from doctrine; run a recurring
  vision-pursuit; or accumulate and walk through independently vetted owner
  decisions. Triggers: "project shape",
  "bootstrap docs", "what should we work on next", "should we build this",
  "spec this feature", "the spec is wrong", "review this project", "audit the
  codebase", "does code match the spec", "what's the next milestone",
  "brainstorm features", "run the vision pursuit", "relentlessly improve this
  project", "prepare decisions for me to review", "walk me through the blocked
  decisions", "collect owner signoffs".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-03"
compatibility: Subskill scripts require bash, git, grep, find, and uv; project-shape uses uv for fail-closed YAML validation. project-direction additionally assumes the bd (beads) CLI and an OpenSpec-capable environment for changeset synthesis.
---

# TH Projects

Superskill router for spec-driven project governance. Five lifecycle subskills
plus one cross-cutting human-gate transport live under `subskills/`; each is a
complete standard package. Subskills **not** in global catalog — discover lazily
here, load **at most one** subskill body per task. A subskill may route you to a
sibling (e.g. project-review runs project-shape's scanner); follow that link
from the subskill, not here.

Five subskills, one lifecycle:

1. **Shape** — set normative baseline: what project believes, how designed,
   what must be built, where things live, engineering bar for changing it.
2. **Feature request** — run one concrete proposal through idea funnel against
   baseline → signed-off spec delta.
3. **Review** — audit implementation vs baseline + generic health criteria
   (incl. exhaustive spec↔code reconciliation) → confirmed findings.
4. **Direction** — baseline + spec deltas + findings → prioritized, spec-linked
   work plan; hand execution to beads. Before a project's *first* spec, its
   launch gate judges whether goals and requirements are settled enough to
   specify from.
5. **Pursuit** — recurring generative loop against the baseline's ideal:
   sequential surface audits + ideation lenses → ranked moves, tier-board
   movement, gated work plan; pursued moves re-enter via feature request.

**Questionnaire** crosses that lifecycle only at hard human gates. It
accumulates, independently reviews, presents, and records owner decisions; it
does not create doctrine, specs, priorities, or implementation authority.

VISION is a **continuous constraint**, not a one-time gate: proposal, spec,
allocation, implementation discovery, reconciliation, and milestone closeout
must still serve cited doctrine. Implementation discoveries re-enter at the
earliest affected feature-request gate; milestone closeout returns uncovered
mandates to direction's **milestone synthesis**. The lifecycle stops only at a
hard human gate or when the approved spec is achieved and verified.

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

## Routing table

| Task intent | Subskill | Typical trigger |
|---|---|---|
| Bootstrap or audit the project's knowledge architecture (five pillars: heart-and-soul, legends-and-lore, openspec, lay-and-land, craft-and-care); decide where an idea should be documented; generate a layman overview. | [subskills/project-shape/SKILL.md](subskills/project-shape/SKILL.md) | "set up project structure", "bootstrap docs", "where should this go", "audit documentation health" |
| Concretize ONE fuzzy feature/project request into a signed-off spec delta: motif, doctrine gate, topology placement, design sketch, WHEN/THEN scenarios. Also amendment mode: fix a spec found wrong/ambiguous mid-implementation. | [subskills/project-feature-request/SKILL.md](subskills/project-feature-request/SKILL.md) | "I want to add X", "spec this feature", "turn this idea into requirements", "what would it take to build X", "the spec is wrong" |
| Decide what to work on next; evaluate competing priorities; check roadmap alignment; turn approved specs into a prioritized beads work plan. Also the vision-generative modes when nothing is proposed: milestone synthesis (derive work doctrine already implies) and ideation (brainstorm new feature candidates grounded in doctrine); and the launch gate, which judges whether goals and requirements are settled enough to author a project's first specs. | [subskills/project-direction/SKILL.md](subskills/project-direction/SKILL.md) | "what's highest leverage", "what should we work on next", "break this down", "is this roadmap aligned", "what's the next milestone", "brainstorm features for this project", "are we ready to write specs" |
| Repo-wide health audit: code quality, reliability, security, docs, maintainability — scored, evidence-based, with a planning handoff packet. Includes the exhaustive spec-reconciliation mode (bidirectional spec↔code gap audit + remediation). | [subskills/project-review/SKILL.md](subskills/project-review/SKILL.md) | "review this project", "audit the codebase", "assess project health", "reconcile spec vs implementation", "what's implemented but undocumented" |
| Recurring generative pursuit of the project's ideal: lane-sequenced audits of every surface against the applicable bar + vision-grounded ideation lenses → ranked move list, tier-board movement across runs, gated (never auto-released) work plan. | [subskills/relentless-vision-pursuit/SKILL.md](subskills/relentless-vision-pursuit/SKILL.md) | "run the vision pursuit", "what's the next best step toward the vision", "relentlessly improve this project", "audit the whole project against the ideal" |
| Accumulate genuine hard owner gates into a local asynchronous packet, independently vet each problem scope and recommendation, publish it as a local web form (scripted, prints a URL), ingest the owner's asynchronous answers, walk the owner through one decision at a time, or route recorded signoffs through the canonical owning workflow. Pursuit and greenfield-parity runs end here with their owner dossier. | [subskills/user-questionnaire/SKILL.md](subskills/user-questionnaire/SKILL.md) | "prepare decisions for me to review", "review when I wake up", "publish the questionnaire", "ingest my answers", "walk me through blocked decisions", "collect owner signoffs" |

## Routing rules

- **Baseline before judgment**: review and direction both consume the shape
  baseline. No shape artifacts at all + real ask is "make this project legible"
  → project-shape, even if user said "review". Doctrine adopted but no specs
  yet + the ask is "can we start specifying" → direction's **launch gate**;
  project-shape *builds* the pillars, the gate judges whether what was built is
  enough to specify from.
- **Audit vs. plan vs. pursue**: review *classifies* (scores, risks, confirmed
  findings); direction *decides* (sequencing, specs, beads); pursuit
  *generates* (gap-to-ideal moves, recurring, orchestrated). "What's wrong
  with this repo" → review. "What to do about it / next" → direction.
  "Relentlessly push this toward its vision" → pursuit. Full review hands off
  to direction inside the subskills.
- **Feature vs. direction**: one concrete proposal → feature-request; many
  competing priorities or "what next" → direction; no proposal at all and the
  ask is to *invent* candidates → direction (ideation), whose pursued picks
  feed the funnel. A feature request surviving its funnel hands its spec delta
  to direction for sequencing.
- **Questionnaire vs. governance**: questionnaire transports already-identified
  hard human gates; it does not decide what should be built or turn ordinary
  engineering judgment into owner work. One fuzzy feature → feature-request;
  competing priorities → direction; tracker mechanics → beads-orchestration.
  Use questionnaire only when asynchronous accumulation, adversarial review,
  walkthrough, or durable signoff recording is the actual task.
- **Scope guard**: single-PR/diff review → `/code-review`, not here.
  Change-level engineering-quality judgment (engineering bar, readability, test
  rigor, dependency hygiene, cruft cleanup, skill reviews, diagrams) →
  `/th-engineering` (project-shape's craft-and-care adopts that bar by
  reference). Backlog mechanics without direction analysis →
  `/beads-orchestration` (beads-writer). AI-tooling harness hygiene (installed
  skills, dotfiles, snapshot state) → `/th-tooling`.
- **Design and engineering bars**: user-facing behavior or interaction contracts
  → `/th-design`; implementation quality, tests, dependencies, diagnosis, docs,
  or diagrams → `/th-engineering`. Load the router only when the concern exists,
  then its one relevant subskill; carry conclusions into the spec or bead rather
  than copying whole skill bodies.
- **Fallback**: project-adjacent but no row fits → answer from router context or
  ask. Do not load a subskill to browse.

## Shared invariants (all subskills)

- Five-pillar shape model (`about/heart-and-soul`, `about/legends-and-lore`,
  `openspec/`, `about/lay-and-land`, `about/craft-and-care`) is the single
  normative vocabulary; subskills must not redefine it. A project navigates
  those pillars through one local `doctrine` superskill — router in
  `.claude/skills/doctrine/SKILL.md`, one navigator per pillar under its
  `subskills/` — never five top-level skills competing for the global catalog.
- Specs are source of truth for work planning; implementation without spec
  coverage is a finding, not a baseline. The OpenSpec file format subskills
  read/write is one shared contract,
  [`references/spec-format.md`](references/spec-format.md) at package root, not
  owned by any single subskill. Its mechanical validator,
  [`scripts/spec-trace-check.py`](scripts/spec-trace-check.py), runs before any
  subagent spends tokens on semantic spec review.
- Per-change spec sync: an implementation task that changes observable behavior
  amends its governing spec in the same task (feature-request amendment mode).
  Episodic reconciliation is the backstop, never the mechanism.
- VISION/doctrine remains binding after sign-off. Record the baseline commit in
  spec handoffs; if it changed, revalidate affected mandates before allocation
  or resumption.
- Gap, TODO, unknown, and adjacent idea discovery is proactive but never silent
  scope expansion. Classify it as current-spec correction, boundary/design
  change, evidence unknown, new scope, or local debt; route to the earliest
  governing gate and record it durably.
- Work graphs follow
  [`references/work-allocation.md`](references/work-allocation.md): one bead and
  primary agent per cohesive independently verifiable outcome, with enough work
  to amortize context/worktree/CI/review overhead and no overlapping ownership.
- **Cache-first orchestration.** Plan and run work as a few long-lived,
  cache-warm sessions that ride autocompaction, not as many fresh subagents.
  A subagent here is justified by independence (adversarial or fresh-context
  review, questionnaire vetting) or by context isolation the lead can't afford,
  never by "it's parallelizable". Canonical rules:
  [`beads-orchestration/references/token-efficiency.md`](../beads-orchestration/references/token-efficiency.md)
  → "Cache-first execution".
- Autonomy contract — what agents may do without the human:

  | Artifact | Agent may | Human must |
  |---|---|---|
  | Doctrine (`about/heart-and-soul/`) | draft, review, blast-radius sweep | adopt every amendment |
  | Spec delta (proposed behavior) | draft + reconcile changeset | sign off before implementation |
  | Main-spec bookkeeping of [Observed] behavior | edit directly | — |
  | Beads/planning graph | generate from approved changeset | approve the changeset it derives from |

- **Run to the gate, not to a plan.** A run ends at a hard human gate (table
  above; full contract in
  [`beads-orchestration/references/decision-autonomy.md`](../beads-orchestration/references/decision-autonomy.md))
  or at the requested deliverable, verified; never short of it on a plan to
  produce it, a promise, or "shall I proceed". Where a subskill's
  deliverable is itself a plan or report, delivering it is completion.
  Decide reversible engineering choices and record why; finish everything
  that does not depend on a pending owner answer before surfacing the
  question.
- **The owner's instructions outrank skill text.** A direct request from the
  human owner (not a dispatching agent) narrows, reorders, or skips a
  workflow here; it does not lift the safety gates in decision-autonomy.md.
  When a gate in this package makes you pause, name the rule and its file.
- **Deliverables are written for a cold reader.** Reports, handoff packets,
  subagent briefs, and owner questions lead with the outcome in complete
  sentences and define their terms. The terse prose of these skill files is
  a loading-cost choice, not a template for output.
- Every major claim cites evidence, labeled [Observed], [Inferred], or
  [Unknown].
- A questionnaire item is not owner-review-ready until an independent subagent
  has separately passed its problem scope and recommendation against the target
  project's shape and engineering bar. The packet records the decision; it does
  not broaden the authorization granted by it.
- Subskills cross-reference by relative path (`../project-shape/…`) inside this
  package; those paths are package-internal and stable.

## Package maintenance

After changing any subskill, script, or fixture in this package, run
[`scripts/validate-th-projects.sh`](scripts/validate-th-projects.sh) — shell
syntax, project-shape self-tests, fixture invariants, and the spec-trace
fixtures — and fix every FAIL before committing.
