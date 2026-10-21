---
name: relentless-vision-pursuit
description: >
  Use for a recurring, generative pursuit run that closes the gap between what
  a project is today and the ideal its doctrine describes: lane-sequenced audits of
  every surface against the applicable quality bar, vision-grounded ideation
  lenses, synthesis into a ranked move list with tier-board movement across
  runs, and a gated (never auto-released) work plan. Triggers: "run the vision
  pursuit", "what's the next best step toward the vision", "relentlessly
  improve this project", "audit the whole project against the ideal",
  "generate the next round of improvements".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-10-21"
compatibility: >
  Needs repo-root access and doctrine (about/heart-and-soul/). Audit and
  ideation lanes want a subagent facility with session continuation, plus bd
  (beads). Both degrade: small projects run everything inline, sequentially,
  and without bd the ranked move list stays in the dossier. The closeout
  questionnaire uses ../user-questionnaire/ scripts (uv, Python 3.11+).
---

# Relentless Vision Pursuit

A **recurring, generative** audit loop. The goal is not to verify correctness
(that is `../project-review/SKILL.md`) but to relentlessly close the gap
between what the project is today and the ideal its doctrine describes. Each
run produces *new* moves — never a re-filing of what a prior run already
found — and measures movement since the last run.

The posture, which every dispatched prompt inherits:

- **Judge against the north star**, quoted from the prior dossier or
  re-derived from `about/heart-and-soul/vision.md`. Verdicts are
  gap-to-ideal, not pass/fail.
- **Propose the ideal.** Backward compatibility inside the project's own
  surface is waived by default — redesigns and removals are in scope; note
  migration cost, don't let it veto the move. External consumers' contracts
  are never waived silently.
- **Fewer new findings after a release is success**, not failure. Report
  tier movement prominently; never pad the move list to hit a count.

## Do not use when

- Correctness or health question ("is it wired", "score this repo") →
  `../project-review/SKILL.md`.
- One concrete feature to specify → `../project-feature-request/SKILL.md`.
- Quick candidate brief with no orchestration → `../project-direction/SKILL.md`
  ideation focus mode.
- Sequencing already-approved work → `../project-direction/SKILL.md`.

## Phase 0 — Ground and scope (inline, before any lane)

1. **Doctrine.** Read `about/heart-and-soul/` (vision, non-negotiables,
   NOT-boundaries) plus any normative design-language or architecture spec
   the project keeps. Absent → stop and recommend `../project-shape/`
   bootstrap: a pursuit without a vision is a random walk.
2. **Surface inventory.** Enumerate from the project's source-of-truth
   artifact for its type — route table for frontends, command tree for CLIs,
   public API for libraries, module map from `about/lay-and-land/` otherwise
   — never a directory listing. Group routes/modules into cohesive surfaces
   and order them for locality (adjacent surfaces share code). That order is
   the audit lane's walk.
3. **Already-known ledger** — the dedup input injected verbatim into each
   lane's opening prompt: prior pursuit dossiers (move lists + tier boards), open/in-flight
   beads (`bd list --json`), recently merged work on the audited surfaces,
   and `about/legends-and-lore/ideas-ledger.md`. Compress to a bullet list:
   "known and in-flight — do not re-report".
4. **Tier-board baseline** from the newest prior dossier so this run reports
   movement. First run: no baseline — say so, don't fabricate one.
5. If a prior pursuit epic shipped since the last run, start with a scoped
   `../project-review/SKILL.md` verification pass over the surfaces it
   touched; only verified landings count as movement.

## Phase 1 — Surface pursuit (audit lane)

One long-lived audit lane walks every surface in locality order, one per turn,
then runs the cross-cutting sweeps chosen for the project:
discoverability/shell, visual language, interaction speed, accessibility for
human surfaces; architecture seams, test rigor, operability for code
surfaces. Non-negotiables baked into every prompt:

- Hold the surface to the **applicable bar**: `/th-design` (one relevant
  subskill) for human-facing surfaces, `/th-engineering` for code-facing
  ones.
- Judge against the quoted north star; every finding cites `file:line`.
- Every surface gets a verdict tier —
  `world-class | solid | functional | weak | broken` — a gap paragraph, and
  a ranked move list.
- The already-known ledger is in the lane's opening prompt; the lane drops
  duplicates, not the synthesizer.

## Phase 2 — Vision pursuit lenses (ideation lane, concurrent with Phase 1)

Ideation at scale. Lens definitions, fit-trace discipline, and the
mandate-grounded vs vision-extending classing all come from
[`../project-direction/references/ideation.md`](../project-direction/references/ideation.md)
— do not redefine them here. Beyond its base lenses, derive 3–6
project-specific lenses from topology and doctrine (e.g. integrations, the
core loop's latency/cost, data or knowledge growth, proactivity/automation,
cross-component collaboration). One ideation lane runs the lenses in turn;
each lens turn returns concrete,
integration-point-named proposals (which module, schema, or spec would
change), scored owner-value vs build-cost, deduped against the ledger.

## Phase 3 — Synthesis (orchestrator, barrier after Phases 1–2)

The strategic core — keep it on the session's model, inline. Read inputs
from the durable harvest file, never from live lane returns (see execution
discipline §3). Produce:

- Tier board with movement vs baseline (verified movement only; inferred
  movement labeled).
- Systemic themes — cross-surface defects with exemplars.
- One ranked move list (~10–15) mixing surface moves and vision extensions;
  each move: what, why (doctrine citation), evidence, rough slice plan.
  Vision-extending moves carry their doctrine-amendment prerequisite.

## Phase 4 — Deliverables

Dossier + data JSON, gated beads epic, the owner dossier questionnaire, and a
memory line — formats and the gate protocol in
[`references/pursuit-deliverables.md`](references/pursuit-deliverables.md).
A pursuit run is *planning, never execution*: nothing it files may be
runnable until the owner releases the gate.

## Phase 5 — Owner dossier questionnaire (closeout)

The run ends by handing the owner its hard gates as one
[`../user-questionnaire/SKILL.md`](../user-questionnaire/SKILL.md) packet,
published as a web form: gate-release scope, each vision-extending move's
doctrine amendment, any external-contract waiver, and nothing else. Items
are drafted, adversarially reviewed by a fresh subagent, validated, and
served by script; the final report ends with the printed `QUESTIONNAIRE_URL=`
line. Admission rules, item shapes, and what the next session does with the
answers are in
[`references/pursuit-deliverables.md`](references/pursuit-deliverables.md)
§ Owner dossier questionnaire. A run with no owner gates says so instead of
inventing items.

## Execution discipline

Load [`references/execution-discipline.md`](references/execution-discipline.md)
before dispatching any lane. Four hard rules: lanes not batches (≤3 sessions
in flight), model routing by task difficulty, checkpoint-to-disk per unit, and
cadence/resume hygiene. Violating one is a defect in the run, not a style choice.

## Handoffs

- **Released moves that need behavior specification** → the
  `../project-feature-request/SKILL.md` funnel as fuzzy input;
  vision-extending moves enter knowing Gate 2 requires doctrine amendment
  or rejection.
- **Gate release** → the owner's move, recorded through the closeout
  questionnaire; the applying session closes the gate only on an `Agreed`
  release answer with `apply_now`. Execution belongs to
  `/beads-orchestration` (beads-coordinator), never to this skill.
- **Owner answers** → the next session runs `questionnaire_web.py ingest`
  before anything else and routes per the user-questionnaire apply checklist.
- **Next run** verifies this run's shipped moves via a scoped
  `../project-review/SKILL.md` pass (Phase 0 step 5).
