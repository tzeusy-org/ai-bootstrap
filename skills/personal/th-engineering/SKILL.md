---
name: th-engineering
description: >
  Use for engineering-quality work on any change or codebase — holding an
  implementation or review to the engineering quality bar, code readability,
  test-suite rigor, module boundaries and dependency chains, diagnosing hard
  bugs and flaky failures to root cause, post-refactor cruft cleanup,
  codebase documentation, scaffolding or reviewing skills against the skill
  quality bar, greenfield-parity complexity audits that re-run without
  duplicating beads, or Excalidraw diagrams. Triggers: "hold
  this to the engineering bar", "is this code readable", "review these
  tests", "untangle these dependencies", "diagnose this", "flaky test",
  "clean up this refactor", "would we build it this way from scratch",
  "is this over-engineered", "document this service", "audit the docs",
  "review this skill", "create a skill", "draw a diagram".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-20"
compatibility: skill-standards auditing and excalidraw rendering require uv and Python 3.11+; excalidraw-diagram additionally requires Playwright Chromium setup on first render.
---

# TH Engineering

Superskill router for the engineering quality bar. Eleven subskills live under
`subskills/`, each a complete skill package. **Not** in the global
catalog — discover lazily, load **at most one** subskill body per
subdomain, and apply several in sequence in your own session when a task
spans them ("Subagent dispatch" says when delegating pays).

`/th-projects` governs the project (doctrine, specs, topology, audits);
`/th-tooling` governs this machine's harness (installed skills, dotfiles,
snapshot state); this superskill governs the change: the bar code must clear
while written, reviewed, or cleaned up.

## Discover subskills

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
find "$PKG/subskills" -maxdepth 2 -name SKILL.md
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

## Routing table

| Task intent | Subskill | Typical trigger |
|---|---|---|
| Holistic quality bar: default engineering biases, definition of done; the source projects' craft-and-care pillars adopt by reference. | [subskills/engineering-bar/SKILL.md](subskills/engineering-bar/SKILL.md) | "hold this to the engineering bar", "is this change complete" |
| Readability and maintainability: naming, function shape, abstraction altitude, comments, simplicity over cleverness. | [subskills/code-readability/SKILL.md](subskills/code-readability/SKILL.md) | "is this readable", "simplify this code" |
| Test quality: behavior-focused assertions, edge/failure coverage, regression tests, tautology and flake elimination. | [subskills/test-rigor/SKILL.md](subskills/test-rigor/SKILL.md) | "review these tests", "what coverage is missing" |
| Module boundaries and dependency chains: direction, layering, cycles, public surface, third-party policy. | [subskills/dependency-hygiene/SKILL.md](subskills/dependency-hygiene/SKILL.md) | "untangle these dependencies", "should we add this library" |
| Codebase documentation: synthesis, diagrams, contract-level code-cited facts, interface semantics cards, human-readable doc sites. | [subskills/documentation/SKILL.md](subskills/documentation/SKILL.md) | "document this service", "audit docs for stale claims" |
| Evidence bar for diagnosing hard bugs, flaky failures, and perf regressions: feedback loop, ranked hypotheses, tagged instrumentation, regression test at the correct seam. | [subskills/diagnosis/SKILL.md](subskills/diagnosis/SKILL.md) | "diagnose this", "fails one run in twenty", "find the regression" |
| Finish same-repo refactors/renames/migrations: delete lingering aliases, wrappers, fallbacks, dead flags. | [subskills/cruft-cleanup/SKILL.md](subskills/cruft-cleanup/SKILL.md) | "clean up this refactor", "old path still works" |
| Complexity vs. spec: sketch the from-scratch build, classify every excess as justified or residue, rewrite-vs-incremental; audits that file beads re-run idempotently against the existing backlog and close out with an owner dossier questionnaire (web form URL) for their hard gates. | [subskills/greenfield-parity/SKILL.md](subskills/greenfield-parity/SKILL.md) | "would we build it this way from scratch", "is this over-engineered", "re-run the audit without duplicating beads" |
| Skill/superskill quality bar: triggers, grounding, metadata, routing, context efficiency, validation. | [subskills/skill-standards/SKILL.md](subskills/skill-standards/SKILL.md) | "review this skill", "should this be a superskill" |
| Scaffold or author a new skill end-to-end: init from template, bundle scripts/references/assets, package for distribution. | [subskills/skill-creator/SKILL.md](subskills/skill-creator/SKILL.md) | "create a new skill", "scaffold a skill", "package this skill" |
| Excalidraw diagrams into/out of workflows, architectures, protocols, Mermaid. | [subskills/excalidraw-diagram/SKILL.md](subskills/excalidraw-diagram/SKILL.md) | "draw a diagram", "convert this Mermaid" |

## Routing rules

- **Bar vs. subdomain**: holistic "is this good/done" → engineering-bar; an
  ask naming one subdomain → that subskill directly.
- **Change-level vs. project-level**: changes, diffs, PRs, modules → here.
  Repo-wide audits, specs, prioritization, knowledge architecture →
  `/th-projects` (whose craft-and-care pillar adopts engineering-bar by
  reference, and whose project-review cites greenfield-parity as its
  complexity lens).
- **Refactor leftovers vs. architecture residue**: wrappers and fallbacks
  from one finished migration → cruft-cleanup; whole surfaces the current
  spec would never have produced → greenfield-parity.
- **Build quality vs. product feel**: how the code is built → here; how the
  product feels to use (UX contracts, perceived latency, copy,
  discoverability, accessibility) → `/th-design`. A review that surfaces a
  user-facing feel finding routes it there instead of absorbing it.
- **Docs craft vs. knowledge architecture**: README/docs/doc-site quality →
  documentation (consumes excalidraw-diagram for its diagrams — an
  expected pairing); five-pillar shape work → `/th-projects`.
- **Skill content vs. harness state**: authoring a skill from scratch →
  skill-creator (then skill-standards before shipping); reviewing or
  auditing an existing one → skill-standards; whether installed skills are used, linked, fresh on
  this machine → `/th-tooling` (audit-skill-hygiene, refresh-snapshots).
- **Fallback**: quality-adjacent but no row fits → answer from router-level
  context or ask; don't load a subskill to browse.

## Subagent dispatch

Default inline. A sweep across several subdomains reads the same diff, so
one warm session applying each subskill in turn beats N cold subagents that
each re-read it at full price (cache-first rules:
[`beads-orchestration/references/token-efficiency.md`](../beads-orchestration/references/token-efficiency.md)).
Delegate only when one of these holds:

- **Independence**: the reviewer must not be the implementer. A
  fresh-context verifier catches what self-critique misses on high-risk
  changes.
- **Context isolation**: the scope would flood your context with material
  the rest of the task never needs (repo-wide sweep, large generated
  surface).
- **Iteration-heavy single subdomain** (excalidraw render loop, skill
  audit-and-fix): delegate the whole loop, review the returned artifact.

At most 3 subagent sessions in flight; meanwhile keep working on anything
that does not move the head under review. Each review prompt carries (1) the
absolute path to its `subskills/<name>/SKILL.md` with instruction to read
and apply it, (2) exact scope (diff, files, directories), (3) what the
result feeds and what done looks like, (4) the output contract: every
evidenced finding with file:line, severity, confidence, and a proposed fix,
or a clean verdict citing what was checked. The parent filters, dedupes,
and synthesizes; conflicts resolve via engineering-bar's biases. A loop
delegation states the artifact and its done-condition instead of (4).

## Shared invariants (all subskills)

- Quality claims are reviewable expectations, not taste: every finding cites
  file:line (or skill-path) evidence and the expectation it violates.
- engineering-bar's default biases are the baseline all subskills assume; a
  project's `about/craft-and-care/` overrides them where present.
- Fix-it-now beats file-it-away: the current implementation/recovery owner fixes
  small in-scope findings. Independent reviewers retain verifier ownership;
  new architecture, trust-boundary, contract, or risk-class findings return to
  `/th-projects` allocation/spec gates instead of silently expanding the diff.
- The owner's instructions outrank skill text: a subskill's bar shapes how
  work is done, not whether a direct request from the human owner (not a
  dispatching agent) is honored. Safety hard stops stay. When a gate here
  makes you pause, name the rule and its file.
- Reports and subagent briefs are written for a cold reader: outcome first,
  complete sentences, terms defined. The terse prose of these skill files
  is a loading-cost choice, not a template for deliverables.
- Subskills reference each other by relative path (`../engineering-bar/…`);
  those paths are package-internal and stable.
