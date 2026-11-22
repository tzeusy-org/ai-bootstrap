---
name: project-shape
description: >
  Use when analyzing or bootstrapping the five-pillar knowledge architecture of a software project:
  about/heart-and-soul (doctrine), about/legends-and-lore (RFCs/design contracts), about/lay-and-land
  (topology), about/craft-and-care (execution-quality standards), and openspec/ (capability specs
  at root). Use when: starting a new project's knowledge structure, auditing documentation health,
  onboarding, deciding where knowledge belongs, or mapping system topology. Triggers:
  "project shape", "bootstrap docs", "where should this go", "what's this project about",
  "project pillars", "heart and soul", "spec structure", "knowledge architecture", "system
  map", "topology", "lay of the land", "set up project structure".
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

# Project Shape

A project's **shape** = the knowledge architecture making it comprehensible to humans and LLMs. Not code — structured understanding of *what* a project is, *why* it exists, *how* it works, *where* it lives, *what* must be built, *who* you must be when you change it.

**Visualization**: prefer `/th-engineering` (excalidraw-diagram) + SVG render when supported and the diagram materially aids comprehension. Else fall back to Mermaid or prose. Never block shape work on diagram tooling.

## Sample triggers

- "Help me set up the docs structure for this project"
- "What's this project about / where should this idea be documented?"
- "Audit our documentation health — are the pillars coherent?"
- "Map this system's topology"

**Not for:** one concrete feature request (`../project-feature-request/` runs
the funnel); prioritization or "what next" (`../project-direction/`); scored
repo health audits (`../project-review/`); README/docs *craft* quality on a
single doc (`/th-engineering` documentation — this subskill owns where
knowledge lives, not how well one file reads).

## The Five-Pillar Model

Five distinct knowledge layers, each answering a different question:

| Pillar | Folder | Navigator (`doctrine` subskill) | Question | Content |
|--------|--------|-------------|----------|---------|
| **Doctrine** | `about/heart-and-soul/` | `heart-and-soul` | **WHY** does this exist? | Vision, principles, non-negotiables, scope boundaries, what it is NOT |
| **Design Contracts** | `about/legends-and-lore/` | `legends-and-lore` | **HOW** will it work? | RFCs, design docs, wire contracts, state machines, reviews, trade-offs |
| **Capability Specs** | `openspec/` | `spec-and-spine` | **WHAT** exactly must be built? | Normative requirements, WHEN/THEN scenarios, testable acceptance criteria |
| **Topology** | `about/lay-and-land/` | `lay-and-land` | **WHERE** does everything live and connect? | Component diagrams, dependency boundaries, data flow, deployment topology, integration maps |
| **Engineering Standards** | `about/craft-and-care/` | `craft-and-care` | **WHO ARE WE WHEN WE BUILD?** | Implementation quality bar, testing discipline, review expectations, observability, dependency hygiene, documentation, maintainability |

Four pillars live under `about/` (project self-knowledge, poetic names). `openspec/` stays at root — a product with its own structure/conventions.

**Optional `.syzygy` canon.** A governance-plane repo may instead keep its shape under `.syzygy/`: doctrine at `.syzygy/governance/doctrine/`, design contracts at `.syzygy/governance/contracts/`, engineering standards/policies at `.syzygy/governance/policies/`, topology at `.syzygy/map/`; `openspec/` is unchanged. `shape-scan.sh` detects either canon (labeling `[SYZYGY-CANON]`), warns `[DUAL-CANON]` if a pillar exists in both, and `shape-init.sh` refuses to scaffold an `about/` mirror of a pillar already living under `.syzygy/`. Only the doctrine path is fixed by that canon's own doctrine; the other three are detection candidates a project's schema RFCs may refine.

Pillars form a **traceability chain** — Doctrine principle → RFC decision → Spec requirement → Code → Test. Topology cross-cuts all: *where* doctrine is embodied, contracts apply, specs are implemented, work lands. `craft-and-care` is the engineering-character cross-cut: who an engineer must be when changing this repo — explicit, careful, reviewable, observable, maintainable.

**Pillar docs are present-tense state, not history.** Every pillar file states what the project is and intends *now*; a changed decision is rewritten in place. Audit trails — `legends-and-lore/decisions/`, the ideas ledger, changeset proposals, git — record how intent changed and may be cited, but never interleave with the stateful text (`X decided A; rectify to B…`).

## Quick Start: Assess Shape

Run the scanner for a health report (which pillars exist, maturity, gaps):

```bash
bash <skill-path>/scripts/shape-scan.sh [project-root]
```

For scanner thresholds and conservative scoring rules, read [`references/maturity-rubric.md`](references/maturity-rubric.md) — load when interpreting a scan score or asked what counts as structured/shaped/mature.

If scanning is unavailable, check manually for each pillar:

1. **Doctrine?** — `about/heart-and-soul/`, `heart-and-soul/`, `vision.md`, `MANIFESTO.md`, `PHILOSOPHY.md`, or doctrine-like `README.md`
2. **Design contracts?** — `about/legends-and-lore/`, `docs/rfcs/`, `docs/adrs/`, numbered design docs, review rounds
3. **Specs?** — `openspec/`, `specs/`, `requirements/`, WHEN/THEN files, formal requirement IDs
4. **Topology?** — `about/lay-and-land/`, `maps/`, `architecture/`, component diagrams, deployment docs, `ARCHITECTURE.md`
5. **Engineering standards?** — `about/craft-and-care/`, `engineering-bar.md`, `testing-and-verification.md`, review/verification/observability standards, or quality doctrine scattered in contributor docs

Rate each: **absent** → **nascent** (scattered) → **structured** (dedicated folder, some coverage) → **mature** (comprehensive, traceable, maintained).

## Workflow 1: Bootstrap Shape (new project)

Bootstrapping is **consultative**, not template-filling. Extract shape from the human's head via structured dialogue, synthesis, adversarial review.

**Quality gates:**
- Most capable model available, at `high` reasoning effort where the
  harness exposes the setting (chosen at session or dispatch start, not
  mid-run).
- Keep one bootstrap owner, drafting pillars in sequence in its own warm
  session. The interview is the evidence, and a cold pillar worker would have
  to be re-briefed from it at full price. Hand a pillar cluster to a subagent
  only when its investigation (large-repo topology, RFC code archaeology)
  would flood the owner's context with material later pillars don't need.
  Review is different: it stays independent and fresh-context.
- Never self-review — independent review subagents when the environment supports them.
- Challenge the user — accept vague answers only to push deeper, never to ship.

**Fallback modes** (degrade presentation, not rigor):
- Full — highest model, independent review subagents, diagrams via `/th-engineering`.
- Lite — single agent + deliberate self-critique + user review when subagents unavailable.
- No-diagram — Mermaid or prose when diagram tooling unavailable.

**Process:**

1. **Interview** — Socratic extraction across five tracks (identity, boundaries, principles, architecture, contracts). Read [`references/consultative-bootstrapping.md`](references/consultative-bootstrapping.md) for question banks + challenge patterns.
2. **Synthesize** — distill answers into drafts. Use the human's own language.
   Make trade-offs explicit. Flag contradictions. Draft pillar by pillar in
   the owner's session; split to a pillar/doc-cluster worker only under the
   flooding exception above.
3. **Independent review** — fresh context reviews stable pillar/doc clusters;
   combine coherence + adversarial lenses unless risk warrants separate agents,
   then run cross-pillar review at convergence points. Read
   [`references/review-protocol.md`](references/review-protocol.md).
4. **Revise + present** — incorporate findings, present for validation. If "not quite right" → return to interview, don't patch.
5. **Scaffold + install** — run `shape-init.sh` for structure, populate with reviewed docs, install the `doctrine` superskill (router + five pillar subskills). Vet the generated package with `/th-engineering` (skill-standards) before relying on it.

**Pillar order** (top-down, each grounds the next): heart-and-soul → craft-and-care → legends-and-lore → openspec → lay-and-land. Draft `craft-and-care` right after doctrine is coherent, before implementation planning — mandatory for all non-trivial work. Topology can start in parallel with design contracts once the architecture interview track is done.

Read [`references/bootstrapping.md`](references/bootstrapping.md) for the phase-by-phase guide, including the local-skill authoring and skill-standards review loop.

## Workflow 2: Translate Ideas into Requirements

The shape model funnels ideas into code. Idea → doctrine gate → topology placement → design sketch → WHEN/THEN spec scenarios → tasks. Ideas enter fuzzy, exit precise; each pillar sharpens them; bad ideas die early on doctrine misalignment.

To run this funnel end-to-end for one concrete feature request, use [`../project-feature-request/SKILL.md`](../project-feature-request/SKILL.md). This section is the model it implements.

**When ideas don't fit:**
- Contradicts doctrine → reject, or evolve doctrine via
  [`references/doctrine-amendment.md`](references/doctrine-amendment.md) —
  drafted by agents, adopted only by the human owner, downstream sweep in the
  same session.
- No technical path → park; write exploratory RFC when a path emerges.
- Sound but not specifiable → too vague; break down further.

## Workflow 3: Audit and Maintain Shape Health

For an existing project, assess cross-pillar coherence and keep docs current.

**Assessment dimensions:**
1. **Coverage** — specs trace to RFC sections? RFCs align with doctrine?
2. **Freshness** — specs current with code? RFCs updated after implementation reveals design flaws?
3. **Gaps** — code with no spec? Specs with no doctrine? Design docs that never became specs?
4. **Orphans** — doctrine principles no RFC references; RFC sections no spec covers.
5. **Execution drift** — testing/observability/review/compatibility/documentation/dependency/maintenance standards out of sync with how the project is actually changed?
6. **Tense** — stateful pillar docs carrying decision history (dated updates, "decided X, now Y", superseded wording kept inline)? Rewrite to present-tense intent; move the history to a decision record if it is worth keeping.

**Maintenance protocol** when code diverges from docs:
1. **Detect** — compare implementation against spec requirements, RFC contracts, doctrine.
2. **Update** — generate updated sections; group cohesive small edits and split
   only substantive, independently reviewable pillar clusters.
3. **Review the delta** — independent review agents on changed sections only.
4. **Cross-check** — cross-pillar review if changes affect multiple pillars.
5. **Present** — show diff + review summary before committing.

**Related:** [`../project-direction/SKILL.md`](../project-direction/SKILL.md) for full direction analysis with priority-weighted plans; [`../spec-concretization/SKILL.md`](../spec-concretization/SKILL.md) for exhaustive spec↔code reconciliation; [`../project-feature-request/SKILL.md`](../project-feature-request/SKILL.md) for a single idea through the funnel.

## Workflow 4: Generate Project Overview

Synthesize pillars into a visual, layman-friendly `about/README.md` with embedded Excalidraw SVG diagrams — the public face of the project's shape.

**Requirements:** ≥2 pillars exist (heart-and-soul + one other); use
`/th-design` (information-design or accessibility) for the overview's human
reading experience when needed, and `/th-engineering` (excalidraw-diagram) only
when a diagram materially aids comprehension. Fall back to Mermaid/prose;
independent review when available, else explicit accessibility/adversarial
self-check + user validation.

**Process:** extract layman-relevant essence per pillar → design 3-5 diagrams arguing the project's story → generate via excalidraw-diagram + render SVG (render-view-fix loop) → write structured markdown (thesis → what it's not → how it works → v1 delivers → principles → navigating docs) → review (accessibility + adversarial subagents) → commit to `about/README.md`, sources in `about/assets/`.

Read [`references/generate-overview.md`](references/generate-overview.md) for diagram specs, document skeleton, review-agent prompts, and writing guidelines.

## Reference Index

### Diagrams

Rendered visuals for the shape model (each `.svg` has an editable `.excalidraw` source). Regeneration specs live in [`references/diagram-specs.md`](references/diagram-specs.md) — load only when regenerating with /th-engineering's excalidraw-diagram:

| Diagram | Shows |
|---------|-------|
| [`assets/five-pillars-load-bearing.svg`](assets/five-pillars-load-bearing.svg) ([source](assets/five-pillars-load-bearing.excalidraw)) | How the five pillars bear load, and in what order |
| [`assets/five-pillars-health-risks.svg`](assets/five-pillars-health-risks.svg) ([source](assets/five-pillars-health-risks.excalidraw)) | Failure modes when a pillar is missing or weak |
| [`assets/pillar-traceability.svg`](assets/pillar-traceability.svg) ([source](assets/pillar-traceability.excalidraw)) | Traceability chain from doctrine down to execution |
| [`assets/idea-funnel.svg`](assets/idea-funnel.svg) ([source](assets/idea-funnel.excalidraw)) | Idea-to-spec funnel and the gate each pillar answers |
| [`assets/review-fanout.svg`](assets/review-fanout.svg) ([source](assets/review-fanout.excalidraw)) | Audit fan-out across pillars in Workflow 3 |
| [`assets/coordinator-workflow.svg`](assets/coordinator-workflow.svg) ([source](assets/coordinator-workflow.excalidraw)) | End-to-end coordinator workflow across shape phases |

### Pillar Guides

| Pillar | Reference | Read when... |
|--------|-----------|-------------|
| Doctrine | [`references/pillar-heart-and-soul.md`](references/pillar-heart-and-soul.md) | Bootstrapping vision, writing non-negotiables, scoping v1 |
| Design Contracts | [`references/pillar-legends-and-lore.md`](references/pillar-legends-and-lore.md) | Structuring RFCs, running reviews, capturing trade-offs |
| Capability Specs | [`references/pillar-spec-and-spine.md`](references/pillar-spec-and-spine.md) | Writing requirements, WHEN/THEN scenarios, spec lifecycle |
| Topology | [`references/pillar-lay-and-land.md`](references/pillar-lay-and-land.md) | Mapping components, boundaries, data flow, deployment |
| Engineering Standards | [`references/pillar-craft-and-care.md`](references/pillar-craft-and-care.md) | Defining the implementation quality bar, review standards, verification, observability, maintainability |

### Process Guides

| Guide | Reference | Read when... |
|-------|-----------|-------------|
| Doctrine Amendment | [`references/doctrine-amendment.md`](references/doctrine-amendment.md) | Changing `about/heart-and-soul/` — proposal delta, adversarial review, owner adoption, downstream sweep |
| Consultative Bootstrapping | [`references/consultative-bootstrapping.md`](references/consultative-bootstrapping.md) | Extracting shape from a human for a new project — interview tracks, challenge patterns, synthesis rules |
| Review Protocol | [`references/review-protocol.md`](references/review-protocol.md) | Reviewing generated docs with independent subagents — agent specs, iteration rules, anti-patterns |
| Bootstrapping Phases | [`references/bootstrapping.md`](references/bootstrapping.md) | Step-by-step phase guide for establishing shape from scratch |
| Local Skill Templates | [`references/local-skill-templates.md`](references/local-skill-templates.md) | Installing the `doctrine` superskill: router template, per-pillar subskill templates, migration from five top-level skills |
| Generate Project Overview | [`references/generate-overview.md`](references/generate-overview.md) | Creating a layman-friendly about/README.md with Excalidraw diagrams |
| Maturity Rubric | [`references/maturity-rubric.md`](references/maturity-rubric.md) | Understanding scanner thresholds and what qualifies as structured/shaped/mature |
| Evaluation Scenarios | [`references/evaluation-scenarios.md`](references/evaluation-scenarios.md) | Testing the skill package itself across strong/weak environments and legacy/scaffolded repos |

### Scripts

| Script | Run when... |
|--------|-------------|
| [`scripts/shape-scan.sh`](scripts/shape-scan.sh) `[project-root]` | Assessing an existing project's shape — reports which pillars exist, their maturity, and gaps |
| [`scripts/shape-init.sh`](scripts/shape-init.sh) `[project-root] [--skills-only] [--tools=...]` | Scaffolding pillar directories and generating correctly-formatted local skills |
| [`scripts/validate_local_skill.py`](scripts/validate_local_skill.py) | Internal fail-closed YAML/frontmatter validator used by `shape-scan.sh`; run through `uv` |
| [`scripts/self-test.sh`](scripts/self-test.sh) | Verifying the scanner/scaffolder against fixtures after changing `SKILL.md`, `shape-scan.sh`, or `shape-init.sh` |
| [`scripts/eval-fallbacks.sh`](scripts/eval-fallbacks.sh) | Confirming constrained-environment fallback behavior is still documented after changing fallback guidance |

## Local Skill Installation

Each pillar gets a local navigation skill — an **index with selection guidance**, not a copy of pillar content. It says *which file to read* for a task, not *what the file says*. All five ship as subskills of **one `doctrine` superskill**, so the project spends a single entry in the agent's global catalog instead of five always-loaded descriptions:

```text
.claude/skills/doctrine/SKILL.md                    # router (the catalog entry)
.claude/skills/doctrine/subskills/<pillar>/SKILL.md # heart-and-soul, legends-and-lore,
                                                    # spec-and-spine, lay-and-land, craft-and-care
```

Preferred path generates the whole package, correctly formatted:

```bash
bash <skill-path>/scripts/shape-init.sh [project-root] --skills-only --tools=claude,codex
```

For the manual path — router and per-pillar templates, the mandatory `name`/`description`-only frontmatter (scanner rejects extra keys), required progressive-discovery structure, and the migration procedure for projects still on five top-level skills — read [`references/local-skill-templates.md`](references/local-skill-templates.md). Superskill package rules live in `/th-engineering` (skill-standards, `references/superskills.md`). After writing, validate with `scripts/shape-scan.sh` (reports `DOCTRINE_LAYOUT=SUPERSKILL|LEGACY|MIXED|NONE` and flags a router that fails to link an installed subskill) + `scripts/self-test.sh` + `scripts/eval-fallbacks.sh`, and fix every reported issue before committing.

## Maintenance Expectations

- Keep metadata, scripts, and references consistent. If the model says "five pillars," adapters and companion files must too.
- Treat `shape-scan.sh` as an auditor, not a brochure — prefer conservative assessments.
- Re-run `scripts/self-test.sh` after changing `SKILL.md`, `shape-scan.sh`, or `shape-init.sh`.
- Re-run `scripts/eval-fallbacks.sh` after changing fallback-mode guidance or references.
- Keep `tests/fixtures/` aligned with real scanner behavior — they are package contract, not throwaway data.

## Anti-Patterns

- **README-as-doctrine** — README describes the project to users; doctrine defines what it *believes*. Don't conflate.
- **Monolith docs** — one giant ARCHITECTURE.md mixing vision/design/specs. Split into pillars.
- **Specs without doctrine** — requirements with no grounding get challenged endlessly. Doctrine ends debates.
- **Doctrine without specs** — principles that never become testable requirements. Specs make doctrine actionable.
- **Stale middle** — doctrine and code current, RFCs six months old. Design contracts must evolve.
- **Pillar without skill** — knowledge exists but agents can't find it. Install the `doctrine` superskill.
- **Five top-level pillar skills** — one capability charged five always-loaded catalog descriptions. Consolidate under the `doctrine` router (`DOCTRINE_LAYOUT=LEGACY` in the scan).
- **Self-reviewed docs** — the LLM that wrote the doc reviews it in the same context. Use independent subagents.
- **Template-filling** — handing templates to the user instead of extracting shape through dialogue. Produces bureaucracy, not doctrine.
