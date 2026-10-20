---
name: project-review
description: Use when auditing a repository's overall health, tech debt, maintainability, or architecture quality; processing a third-party repo-wide audit; or exhaustively reconciling OpenSpec specs against implementation. Triggers include "review this project", "audit the codebase", "assess project health", "does code match the spec", and "reconcile spec vs implementation" — not single-PR review or backlog prioritization.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - Claude Sonnet 4.6
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-03"
---

# Project Review

Blunt, evidence-based audit of a repo across 15 categories. Establish the normative baseline via sibling `project-shape` first; hand confirmed findings to sibling `project-direction` for scheduling. README is a claim to validate, not primary truth when shape artifacts exist.

**Sample triggers:** "review this project" · "audit the codebase" · "assess project health" · "process this third-party review" · "reconcile spec vs implementation".

**Not for:** single-PR/diff review (use `/code-review`; change-level quality bars live in `/th-engineering`), or deciding what to build next (sibling [`project-direction`](../project-direction/SKILL.md)).

Normative source order: 1. `about/heart-and-soul/` + `about/legends-and-lore/` → 2. `openspec/` → 3. `about/lay-and-land/` → 4. README/docs/issues → 5. inference from code+history.

Every major claim cites specific files/sections. Label assertions [Observed] / [Inferred] / [Unknown]. Every criticism carries a concrete remedy.

## Workflow

### Phase 0 — Normative baseline via `project-shape`

Run project-shape's scanner directly; load its maturity rubric only when
interpreting the score. Do not load the full sibling skill for this baseline:

```bash
bash ../project-shape/scripts/shape-scan.sh <repo_root>
```

Classify each pillar on project-shape's per-pillar scale: `absent` / `nascent` / `structured` / `mature`. The scan's Assessment line uses a separate five-level repo rubric (`Unshaped`/`Nascent`/`Structured`/`Shaped`/`Mature` — see [`../project-shape/references/maturity-rubric.md`](../project-shape/references/maturity-rubric.md)); report both, never conflate pillar ratings with repo assessment.

Baseline rules:
- Pillars `structured`/`mature` → primary normative baseline for their domains. Read the actual doctrine/design/spec/topology/craft-and-care files before reviewing code; extract non-negotiables, scope boundaries, architectural claims, normative requirements; note contradictions between pillars without silently resolving them.
- Some pillars present → use those normatively, fall back elsewhere.
- All `absent`/unusable, or shape weak/missing → fall back to README/docs/code as provisional truth, lower confidence on alignment + product-coherence + roadmap judgments, and flag the absence of normative artifacts as a project risk.

**Phase 0 output — baseline packet:** per-pillar maturity + missing pillars · source-of-truth order used · explicit doctrine/spec requirements code must satisfy + craft-and-care execution standards · unresolved contradictions across doctrine/lore/spec/README/code.

### Phase 1 — Automated repo scan

```bash
bash scripts/project-scan.sh <repo_root>
```

Then read [`references/project-type-adaptations.md`](references/project-type-adaptations.md) to calibrate scoring by project type and maturity.

Calibration rule: `project-shape` sets normative requirements; `project-type-adaptations.md` adjusts emphasis. On conflict, shaped project artifacts win.

Edge cases:

| Situation | Handling |
|-----------|----------|
| <50 source files | Skip subagent fan-out; single-agent review OK |
| >50k LOC | Focus entry points, churn hotspots, public API, normative requirements |
| Monorepo, 10+ packages | Sample 3-5 representative packages + shared/core |
| No README/docs | Infer goals from git history, tests, manifests, comments |
| Shallow clone | Note in report; commit/churn data unreliable |
| Generated code (protobuf, ORM, bundles) | Exclude from quality scoring; note presence |
| Shape absent/weak | Treat missing pillars as maintainability + planning risk, not just doc gap |

### Phase 2 — Parallel investigation

Read [`references/investigation-guides.md`](references/investigation-guides.md) (per-domain checklists) and [`references/subagent-template.md`](references/subagent-template.md) (dispatch format).

Dispatch plan:

| Agent | Domain | Scores | Key focus |
|-------|--------|--------|-----------|
| A | Mapping & baseline reconciliation | — | Repo map, goals, doc + shape-vs-code contradictions |
| B | Code quality & architecture | 1-4 | Modularity, clarity, correctness, normative violations |
| C | Reliability & tooling | 5-8 | Errors, observability, testing, hygiene |
| D | Security, perf & data | 9-12 | Dependencies, auth, performance, API/data design |
| E | Docs, ops & maintainability | 13-15 | Documentation, release, change safety, missing shape artifacts |
| F | Gaps, scale & planning constraints | — | Feature gaps, 10x/100x, risk register, sequencing constraints |

Strategy:
- A-F are isolated from the orchestrator because each domain's evidence
  would flood it. Run them as at most 3 investigator lanes (inside the
  cache-first lane ceiling): when a lane's domain returns, continue that
  session on its next domain instead of spawning a cold agent, so the
  baseline and scan are paid for three times, not six. Merge returned
  reports meanwhile.
- Pass artifact paths plus a compact Phase 0 manifest and scoped scan excerpt;
  do not paste the same full baseline/scan into every prompt. Assign one primary
  evidence owner per concern so overlapping domains cite rather than rescan.
- Change-level quality bars live in `/th-engineering` subskills — B: code-readability + dependency-hygiene, plus greenfield-parity's delta workflow as the complexity lens (sketch the from-scratch build before reading code; every excess is justified or residue); C: test-rigor (+ diagnosis for flake/root-cause evidence); E: documentation. When craft-and-care is absent/silent on a domain, agents cite those bars rather than inventing criteria; `investigation-guides.md` stays the evidence checklist, not a second bar.
- Human-facing surfaces load `/th-design` only for implicated experience
  concerns (design-bar by default; accessibility, discoverability,
  information-design, interaction-speed, or visual-language when specific).
  Backend-only scopes do not pay this context cost.
- Agent F's roadmap draft is advisory only — no beads, no scheduling.

Each subagent receives: Phase 0 baseline packet · shape-scan output · project-scan output · project context (type/users/maturity/scope) · its domain section from `investigation-guides.md` · relevant rubric sections from [`references/scoring-rubric.md`](references/scoring-rubric.md) · relevant calibration from `project-type-adaptations.md`.

### Phase 3 — Synthesis

Collect subagent reports. Read [`references/report-template.md`](references/report-template.md) for output structure.

1. Merge scores conservatively — on disagreement take the lower score, record the disagreement.
2. Mark genuinely inapplicable categories `N/A`; exclude from average.
3. Bucket every reported finding (lower-severity ones that skip Phase 3.5 keep their stated confidence, are marked ungated, and stay out of the packet): **Normative violations** (doctrine/lore/spec/topology contradicted by code) · **Generic health risks** (quality/reliability/tooling/security/perf/DX) · **Shape gaps** (missing/stale pillars weakening confidence) · **Deprioritized** (good ideas that don't fit context).
4. Build risk register by severity × likelihood.
5. Generate advisory roadmap (quick wins / medium / strategic) — synthesis only, creates no execution artifacts.
6. Assign verdict:

| Verdict | Criteria |
|---------|----------|
| **Healthy** | No category <3; no critical risks; avg ≥3.5 |
| **Healthy but fragile** | No category <2; ≤1 critical risk; avg ≥3.0; but missing safety nets |
| **Functional but accumulating debt** | 1-3 categories <3; avg 2.5-3.5; growing risk register |
| **At risk** | 3+ categories <3 OR any at 1 OR 2+ critical risks; avg <3.0 |
| **Severely at risk** | 5+ categories <3 OR multiple at 1 OR critical security/data risks; avg <2.0 |

7. Prepare the `project-direction` handoff packet: confirmed findings only · Phase 0 baseline packet · required doctrine/lore/spec updates before planning · sequencing constraints + dependency hints · deprioritized items with reasons · evidence index. Write it to `docs/reviews/YYYY-MM-DD-{scope}-packet.md` with the reviewed commit SHA on the first line — that file is the cross-session handoff contract project-direction's receiver protocol reads; a packet living only in conversation dies with the session.

### Phase 3.5 — Veracity Gate

**Before delivering the report**, challenge every Critical/High/P0/P1 finding and the top roadmap items. Read [`references/review-veracity-gate.md`](references/review-veracity-gate.md) for the full procedure.

For each claim:
1. Re-open the named file/path directly.
2. Search for contradictory evidence using exact terms from the claim.
3. Verify referenced paths exist.
4. Verify process claims against actual SKILL/docs/scripts/config.
5. Classify: `[Confirmed]` · `[Overstated]` · `[Incorrect]` · `[Unverifiable]`.
6. Delete or demote anything not `[Confirmed]`.
7. Append invalidated claims to the Veracity Ledger (Appendix D in `report-template.md`); exclude them from the risk register and roadmap.

**Special rule:** Formatting, line-length, and script-invocation claims require local-checkout evidence or GitHub-blob evidence. Raw/parser rendering alone is not sufficient — mark such claims `[Unverifiable]` unless you have blob-level proof.

Any P0/P1 recommendation that survives must cite both supporting evidence **and** the contradictory evidence checked.

### Phase 4 — Deliver

Output the report per `report-template.md`. Make the boundary explicit: `project-review` audits and classifies; `project-direction` decides sequencing, specs, and beads.

## Adapting to scope

- **Full review** (default): all 6 domains, all 15 categories, complete report + handoff packet.
- **Focused review** (user names categories): dispatch only relevant subagents. Still include normative baseline, scorecard for scoped categories, risk register, handoff packet.
- **Quick health check** (fast answer): shape scan + project scan + Agent A, then a brief orchestrator sweep of obvious high-risk areas (tests, CI, auth/secrets, docs). Output: exec summary, provisional scorecard, top 5 risks, explicit low-confidence markers. Don't pass this off as a full review.
- **Third-party deep-dive** ("process this review/audit"): fact-check external findings, filter through actual context, convert confirmed findings into a handoff packet, route planning to `project-direction`. Read [`references/third-party-review.md`](references/third-party-review.md) for the five-step protocol.
- **Spec reconciliation** ("reconcile spec vs implementation", "what's implemented but undocumented", "what's specified but missing"): exhaustive bidirectional spec↔code mapping, report-only by default. Remediation requires explicit authorization; then observed behavior gets spec bookkeeping, cohesive unimplemented outcomes get beads, and strategic gaps escalate to `project-direction`. Read [`references/spec-reconciliation.md`](references/spec-reconciliation.md). Samples nothing.

## Anti-patterns

- Skipping the `project-shape` baseline, treating README as sufficient when doctrine/spec artifacts exist
- Accepting external-review severities at face value
- Treating all recommendations as equally important
- Enterprise-framing a personal project
- Creating beads/execution artifacts from `project-review` (scoped exception: spec-reconciliation remediation)
- Preserving the review as canon instead of extracting durable doctrine/spec/planning updates

## References

| File | Read when | Answers |
|------|-----------|---------|
| [`../project-shape/references/maturity-rubric.md`](../project-shape/references/maturity-rubric.md) | Phase 0 score interpretation | Conservative per-pillar/repo maturity rules; load only when the scanner result needs interpretation |
| [`../project-direction/SKILL.md`](../project-direction/SKILL.md) | Phase 3-4 | Planning contract, sequencing expectations, handoff target |
| [`references/scoring-rubric.md`](references/scoring-rubric.md) | Phase 2 | 1-5 criteria per category with evidence guidance |
| [`references/investigation-guides.md`](references/investigation-guides.md) | Phase 2 | Per-domain checklists, search patterns, deliverables |
| [`references/subagent-template.md`](references/subagent-template.md) | Phase 2 | Dispatch template and required prompt fields |
| [`references/report-template.md`](references/report-template.md) | Phase 3-4 | Output structure, scorecard, handoff packet layout |
| [`references/project-type-adaptations.md`](references/project-type-adaptations.md) | Phase 1-3 | Category weighting by project type, maturity expectations, hybrid handling |
| [`references/spec-reconciliation.md`](references/spec-reconciliation.md) | Spec reconciliation mode | Bidirectional spec↔code inventory, coverage table, gap analysis, remediation |
| [`references/third-party-review.md`](references/third-party-review.md) | Processing an external review/audit | Fact-check, context-filter, ROI tiers, planning-input prep, episodic-artifact handling |
| [`references/review-veracity-gate.md`](references/review-veracity-gate.md) | Phase 3.5 | Full gate procedure: per-claim verification steps, classification rules, Veracity Ledger format, special evidence rules |
| [`../../references/spec-format.md`](../../references/spec-format.md) | Writing/extending specs during reconciliation | OpenSpec file format: heading hierarchy, WHEN/THEN rules, naming (shared contract) |

## Scripts

- [`../project-shape/scripts/shape-scan.sh`](../project-shape/scripts/shape-scan.sh) `<repo_root>` — establishes normative baseline + shape maturity. Run first (Phase 0).
- [`scripts/project-scan.sh`](scripts/project-scan.sh) `<repo_root>` — structural scan: languages, deps, tests, CI, infra, governance, git signals, size (Phase 1).

## After review: schedule with `project-direction`

Once findings are confirmed, load the sibling [`../project-direction/SKILL.md`](../project-direction/SKILL.md) (a subskill, not a slash command) and run it with the Phase 3 handoff packet at `docs/reviews/` (Phase 0 baseline packet, not just the scorecard). Ownership stays separate: `project-review` audits, `project-direction` plans.
