---
name: project-feature-request
description: >
  Use when one concrete feature or project request is still fuzzy and needs a
  signed-off behavior specification before planning or implementation, or when
  implementation reveals its governing spec is wrong, ambiguous, or incomplete.
  Triggers: "I want to add X", "spec this feature", "turn this idea into
  requirements", "what would it take to build X", "write a spec for X", "the
  spec is wrong", "the spec doesn't match what I'm building".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - OpenAI Codex
  status: active
  last_reviewed: "2026-07-13"
---

# Project Feature Request

One request through the idea funnel: fuzzy in, precise out. Deliverable = a
**signed-off spec delta** (plus design sketch when warranted) and a handoff —
never implementation, never sequencing. Operationalizes project-shape's
"Translate Ideas into Requirements" funnel for one request.

Two governing rules:

1. **Specification before work.** Nothing leaves without testable WHEN/THEN
   scenarios and explicit out-of-scope boundaries — or an explicit kill/park
   decision.
2. **Proportionality.** Depth scales with blast radius, not ceremony. Gates are
   always *answered*; only *deep* when the answer is contested.

## Sizing the Request

Classify early; it sets every gate's depth:

| Size | Signals | Gate depth |
|------|---------|-----------|
| **Small** | One component, no new boundaries, no doctrine tension | All gates inline in one pass; minutes, not sessions |
| **Medium** | Several components, new external surface, or observable-behavior changes | Each gate answered explicitly in funnel summary; design sketch required |
| **Large** | New subsystem, doctrine implications, or cross-boundary contracts | One funnel owner; add a dedicated specialist only for an independent artifact or contested gate; independent spec review before sign-off |

## Interview Discipline

Funnel = decision tree; run it like a grilling, not a form. Walk each branch,
resolving dependencies one at a time — an answer at one gate constrains the
next, so never batch-ask across gates.

- **One question at a time.** No questionnaire dumps. Ask, absorb, then ask
  what the answer raises.
- **Carry a recommended answer.** Pose the question *and* your best default
  with reasoning — requester reacts to a concrete proposal, not a blank.
- **Explore before asking.** Answerable from codebase, `about/`, or
  `openspec/`? Go read it. Ask the human only what only the human knows
  (intent, priorities, tolerances).
- **Grill until shared understanding.** Stay on a branch until the answer is
  falsifiable, not just plausible. "Roughly"/"probably" → push, don't move on.

## The Funnel

Load [`references/funnel-gates.md`](references/funnel-gates.md) for per-gate
inputs, exit criteria, and kill conditions, plus the funnel summary template —
read it before running the gates. The sequence:

**Gate 0 — Baseline.** Check what shape exists (run
`../project-shape/scripts/shape-scan.sh` or read `about/` + `openspec/`). Use
whatever pillars exist as normative baseline. Shape absent → proceed in lite
mode with [Inferred] labels. Do **not** force a shape bootstrap on someone who
asked for a feature; note the gap, suggest `../project-shape/SKILL.md`
separately.

**Gate 1 — Concretize the motif.** Restate as: problem, who it serves,
observable success criteria, and the underlying motif (the recurring need this
instances). Challenge vagueness via
`../project-shape/references/consultative-bootstrapping.md` — accept a vague
answer only to push deeper, never to ship.

**Gate 2 — Doctrine.** Aligns with `about/heart-and-soul/`? Outcomes: aligned
(cite the principle); conflict (reject, or escalate a doctrine change — never
both silently); or no doctrine exists (record alignment judgment as [Inferred]).

**Gate 3 — Topology.** Where does it live? Name components touched, boundaries
crossed, integration points, against `about/lay-and-land/` when it exists. A
request that lives "everywhere" isn't concrete — return to Gate 1.

**Gate 4 — Design sketch.** Medium+ → draft the design delta: state machines,
wire contracts, trade-offs considered, in `about/legends-and-lore/` style (see
`../project-shape/references/pillar-legends-and-lore.md`). Small → one sentence
on why no sketch is needed. Human-facing surface → load `/th-design`, select
only the relevant subskill (design-bar by default; discoverability,
accessibility, interaction-speed, information-design, or visual-language when
specifically implicated), and make its conclusions part of the behavior
contract. Backend-only work does not load `/th-design`.

**Gate 5 — Specification.** Write the spec delta: WHEN/THEN scenarios,
behavioral completeness sweep, per
`../../references/spec-format.md` and existing `openspec/` conventions (active
changes override main specs — extend, don't fork). Put scope/non-goals in the
changeset proposal and design details in the design/RFC artifact; keep the
capability spec strictly observable.

**Gate 6 — Engineering bar.** Pull relevant standards from
`about/craft-and-care/` into the changeset tasks/acceptance contract. When the
project bar is absent or silent, load `/th-engineering` and its one relevant
subskill; do not copy the whole router. Quality constraints enter capability
scenarios only when externally observable.

## Amendment Mode (mid-implementation)

The fast path back into the funnel when an implementer discovers, mid-task,
that the governing spec is wrong, ambiguous, or incomplete. Spec drift is
created at exactly this moment — catching it here keeps spec-concretization's
exhaustive reconciliation a backstop, not routine maintenance.

Start with a Gate 2 VISION sanity check, then route by blast radius — not by the
word "amendment": behavior-only clarification → Gates 5–6; user-experience,
boundary, topology, or contract change → resume at Gate 3 or 4; doctrine impact
→ full funnel and possibly
`../project-shape/references/doctrine-amendment.md`.

- Amend the artifact that governs the work: the active changeset the task came
  from, or the main spec when the correction is bookkeeping of
  already-observed behavior (spec-format.md routing rule).
- Rewrite the requirement in place so it states the corrected intent in
  present tense; never append a correction note beside the old wording
  (spec-format § Present Tense, Not History).
- ID discipline per `../../references/spec-format.md`: a modified requirement
  keeps its ID; genuinely new behavior takes the next free ID.
- Record what changed and why in the driving bead/task notes, so the next
  direction pass sees the amendment without re-deriving it.
- Sign-off: still the user's for observable-behavior changes; a pure ambiguity
  clarification (no behavior change) may proceed with a note in the bead.

Kill condition: the "correction" is actually new scope → record it durably in
the ideas ledger or a separate spec-first bead, keep it out of the active bead,
and run it through the full funnel. Do not lose it and do not implement it
silently.

## Sign-off and Handoff

Present the funnel summary: sizing, each gate's outcome with evidence, the
doctrine/spec baseline commit, the spec delta, open questions, and the human
sign-off identity/date. A stale baseline is not approval: revalidate affected
VISION mandates before allocation. Then:

- **Approved** → record `Sign-off: <identity> <date>` in the changeset's
  `proposal.md` (spec-format § Sign-off marker), then hand the spec delta to `../project-direction/SKILL.md` for
  sequencing and decomposition; for a small single-task request, file directly
  via `/beads-orchestration` (beads-writer) with the spec reference.
- **Doctrine conflict** → record the rejection and its reasoning where the
  project keeps decisions; do not soften into a backlog item. Default when
  no established home:
  `about/legends-and-lore/decisions/YYYY-MM-DD-rejected-{slug}.md`. The
  requester may instead escalate a doctrine change —
  `../project-shape/references/doctrine-amendment.md`; never both silently.
- **Parked** (sound idea, no technical path yet) → write an exploratory RFC
  stub in legends-and-lore and stop. Default when no established home:
  `about/legends-and-lore/rfcs/YYYY-MM-DD-parked-{slug}.md`.

Every park or reject also appends one line to the ideas ledger
(`about/legends-and-lore/ideas-ledger.md`) in the same change — that index is
what makes the decision visible to project-direction's milestone synthesis.
Ledger template and maintenance contract:
[`references/decision-record-template.md`](references/decision-record-template.md).

Project ADR/RFC conventions take precedence over these defaults; see
[`references/decision-record-template.md`](references/decision-record-template.md)
for minimal file structure.

- **Not specifiable** (still too vague) → split into smaller motifs and
  re-enter at Gate 1, or return to the requester with the specific questions
  blocking specification.

Sign-off is the user's, not yours. "Not quite right" → return to the failing
gate; don't patch the spec text.

## Boundaries

- One request per run. A list of features is N runs (or sibling `project-direction`
  if the real ask is prioritization).
- No implementation, no estimates, no sequencing — those belong to
  project-direction and execution tooling.
- Evidence labeling matches sibling subskills: [Observed], [Inferred],
  [Unknown].
