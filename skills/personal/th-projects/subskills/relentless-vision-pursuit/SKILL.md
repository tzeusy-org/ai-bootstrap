---
name: relentless-vision-pursuit
description: >
  Use for a recurring, generative pursuit run that closes the gap between what
  a project is today and the ideal its doctrine describes: a ledger scoring
  each vision claim, an in-use audit against the null alternative,
  lane-sequenced audits of every surface against the applicable quality bar,
  vision-grounded ideation lenses, synthesis into a ranked move list with
  claim and tier-board movement across runs, a closure gate, and an
  immediately runnable work plan (no epic-level approval gate).
  Triggers: "run the vision
  pursuit", "what's the next best step toward the vision", "relentlessly
  improve this project", "audit the whole project against the ideal",
  "generate the next round of improvements", "have we done all we can
  toward the vision", "how close are we to the vision".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - Claude Fable 5.2
  status: active
  last_reviewed: "2026-11-21"
compatibility: >
  Needs repo-root access and doctrine (about/heart-and-soul/). The in-use
  audit wants a runnable product; without one, claims stay unmeasured. Audit and
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
found — and measures movement since the last run. A run is finished when it
can say how much of the vision the whole backlog, old and new, would deliver;
a list of fresh findings alone is not that answer.

The posture, which every dispatched prompt inherits:

- **Judge against the north star**, quoted from the prior dossier or
  re-derived from `about/heart-and-soul/vision.md`. Verdicts are
  gap-to-ideal, not pass/fail.
- **Propose the ideal.** Backward compatibility inside the project's own
  surface is waived by default — redesigns and removals are in scope; note
  migration cost, don't let it veto the move. External consumers' contracts
  are never waived silently.
- **Success is claims demonstrated, not findings counted.** Score the
  vision's own promises, not only the surfaces that exist. Fewer new findings
  is success only when the claim ledger shows the gap closing; never pad the
  move list to hit a count, and never let a short list imply a closed gap.
- **Judge the product in use**, beside what the persona would do without it.
  Source reading finds defects; only use shows whether the promise is kept.
- **Serve the user, not the apparatus.** Every move names its user-visible
  effect, and moves that add checks or gates account for their cost.

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
2. **Claim ledger.** Decompose the vision into 4–10 testable claims, each
   with its comparator, measure, status, and covering beads, including the
   implied claims doctrine rarely states (beats the null alternative, worth
   its cost, stays current). Carry the prior run's ledger forward. Format and
   rules: [`references/vision-closure.md`](references/vision-closure.md) §1.
3. **Surface inventory.** Enumerate from the project's source-of-truth
   artifact for its type — route table for frontends, command tree for CLIs,
   public API for libraries, module map from `about/lay-and-land/` otherwise
   — never a directory listing. Group routes/modules into cohesive surfaces
   and order them for locality (adjacent surfaces share code). That order is
   the audit lane's walk.
4. **Already-known ledger** — the dedup input injected verbatim into each
   lane's opening prompt: prior pursuit dossiers (move lists + tier boards), open/in-flight
   beads (`bd list --json`), recently merged work on the audited surfaces,
   and `about/legends-and-lore/ideas-ledger.md`. Compress to a bullet list:
   "known and in-flight — do not re-report". It dedups findings only: an
   open bead still counts as an open gap in the claim ledger.
5. **Tier-board and claim baseline** from the newest prior dossier so this
   run reports movement. First run: no baseline — say so, don't fabricate one.
6. If a prior pursuit epic shipped since the last run, start with a scoped
   `../project-review/SKILL.md` verification pass over the surfaces it
   touched; only verified landings count as movement.

## Phase 1 — In-use audit, then surface pursuit (audit lane)

Open with the **in-use audit**: run the product end to end as its primary
persona on a real input, run the null alternative on the same input, judge
the two artifacts, and record cost, time and yield
([`references/vision-closure.md`](references/vision-closure.md) §2). Its
findings update the claim ledger and go into the audit lane's opening prompt.
A product that cannot be run leaves its claims `unmeasured`; source reading
is never reported as use.

Then one long-lived audit lane walks every surface in locality order, one per turn,
then runs the cross-cutting sweeps chosen for the project:
discoverability/shell, visual language, interaction speed, accessibility for
human surfaces; architecture seams, test rigor, operability for code
surfaces. Non-negotiables baked into every prompt:

- Hold the surface to the **applicable bar**: `/th-design` (one relevant
  subskill) for human-facing surfaces, `/th-engineering` for code-facing
  ones.
- Judge against the quoted north star; every finding cites `file:line`.
- Every surface gets a verdict tier —
  `world-class | solid | functional | weak | broken | absent` — a gap
  paragraph, and a ranked move list.
- **Absent surfaces are audited too.** After the walk, the lane lists what
  the claim ledger needs that no surface provides; each gets an `absent` row
  and a move. An inventory built only from what exists cannot see these.
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
a final **claim-gap lens** takes every claim that is not `demonstrated` and
has no covering bead and proposes what would close it (mandate-grounded by
construction). Each lens turn returns concrete,
integration-point-named proposals (which module, schema, or spec would
change), scored owner-value vs build-cost, deduped against the ledger.

## Phase 3 — Synthesis (orchestrator, barrier after Phases 1–2)

The strategic core — keep it on the session's model, inline. Read inputs
from the durable harvest file, never from live lane returns (see execution
discipline §3). Produce:

- Claim scorecard: status per claim, movement vs baseline, covering beads,
  projected status if the backlog lands, and stalled claims with their cause.
- Tier board with movement vs baseline (verified movement only; inferred
  movement labeled).
- Systemic themes — cross-surface defects with exemplars.
- A **closure plan**: enough moves that every claim is covered, sent to an
  owner gate, or deferred by name. Plan to closure, not to a count. The
  dossier ranks the top ~10–15, mixing surface moves and vision extensions;
  the rest are filed as later-wave beads and listed per claim. Each move:
  what, why (claim and doctrine citation), evidence, user-visible effect,
  observable acceptance, rough slice plan. An unmeasured claim's measure
  outranks polish on that claim. Vision-extending moves carry their
  doctrine-amendment prerequisite.
- The apparatus check on the plan
  ([`references/vision-closure.md`](references/vision-closure.md) §3).

Then run the **closure gate** (§4 of the same reference): a fresh-context
agent projects the end state assuming every bead lands and names what it
would not be proud of. Each shortfall becomes a move, an owner gate, or a
named deferral; re-synthesize at most once more. The dossier opens with the
resulting per-claim projection and open gaps.

## Phase 4 — Deliverables

Dossier + data JSON, beads epic, the owner dossier questionnaire, and a
memory line — formats and the hold rules in
[`references/pursuit-deliverables.md`](references/pursuit-deliverables.md).
Every move bead carries acceptance observable in the product, and the epic
ends with a bead that re-runs the claim scorecard, so rework surfaces inside
the epic instead of at the next run.
The epic and its mandate-grounded move beads are runnable immediately:
no epic-level `[HOLD]` gate and no owner sign-off to start. Only a
vision-extending move that needs a doctrine amendment, a move that
changes an external consumer contract, or a move on a taste-gated claim is
held — that single bead alone
— until its own decision is answered. This skill still never executes
moves itself; the fleet does.

## Phase 5 — Owner dossier questionnaire (closeout)

The run ends by handing the owner its hard gates as one
[`../user-questionnaire/SKILL.md`](../user-questionnaire/SKILL.md) packet,
published as a web form: each vision-extending move's
doctrine amendment, any external-contract waiver, each taste gate from the
claim ledger, and nothing else. Items
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
- **Execution** → ungated moves go straight to `/beads-orchestration`
  (beads-coordinator); never executed by this skill. A held move
  (amendment/contract) is released only by an `Agreed` answer with
  `apply_now` through the closeout questionnaire.
- **Owner answers** → the next session runs `questionnaire_web.py ingest`
  before anything else and routes per the user-questionnaire apply checklist.
- **Next run** verifies this run's shipped moves via a scoped
  `../project-review/SKILL.md` pass (Phase 0 step 6) and re-scores the
  carried claim ledger.
