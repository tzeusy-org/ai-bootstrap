# OpenSpec Spec File Format Reference

Shared contract for every subskill that reads or writes specs. Defines what a
spec file looks like inside — both kinds — plus the traceability fields
(`ID`/`Source`/`Scope`) and the test-tagging convention that make the
doctrine → RFC → spec → test chain mechanically checkable.

Validate mechanically before semantic review:

```bash
uv run <th-projects>/scripts/spec-trace-check.py <repo-root> [--tests-dir <dir>] [--authoring|--strict]
```

Use `--authoring` before signing off new/modified specs: required fields and
placement fail closed, while future requirements need not have implementation
tests yet. Use `--strict` at implementation/milestone closeout to require both
authoring integrity and test citations.

Not defined here:

- **Changeset scaffolding** — the `openspec` CLI owns it (`openspec new change
  "<name>"`); never hand-write `openspec/changes/` structure. Full authoring
  loop:
  [`../subskills/project-direction/references/openspec-changeset.md`](../subskills/project-direction/references/openspec-changeset.md).
- **Spec lifecycle** — explore → new → continue → apply → verify → sync →
  archive, and divergence resolution patterns:
  [`../subskills/project-shape/references/pillar-spec-and-spine.md`](../subskills/project-shape/references/pillar-spec-and-spine.md).

## Two File Kinds — Never Mix Their Headings

| Kind | Location | H2 headings | Written when |
|------|----------|-------------|--------------|
| **Main spec** | `openspec/specs/{spec-name}/spec.md` | `## Purpose`, `## Requirements` | Bookkeeping observed behavior (reconciliation R4); `openspec archive` syncing a finished change |
| **Delta spec** | `openspec/changes/{change}/specs/{spec-name}/spec.md` | `## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`, `## RENAMED Requirements`; optional `## Purpose` for a new capability without a canonical main spec | Proposing future behavior (feature funnel Gate 5, direction Phase 2) |

Authoritative routing rule:

| Intent | Artifact |
|---|---|
| Propose or change observable behavior | Delta spec in an active changeset; human sign-off before implementation. |
| Document behavior already observed in code | Main-spec bookkeeping edit, labeled `[Observed]`; no future-behavior claim. |
| Correct ambiguity without changing behavior | Amend the governing active delta, or the main spec when no active delta governs it; record why. |

Active (non-archived) changes override main specs — always check
`openspec/changes/*/specs/` before extending a main spec, and build on an
active delta rather than forking it. This table overrides any older guide that
says all spec edits require a changeset.

`MODIFIED`/`REMOVED`/`RENAMED` sections match requirements by their exact
`### Requirement:` title text — copy the title verbatim from the main spec.

## Structure

Main spec:

```markdown
# {Main Title}

## Purpose
{1-3 sentence description of scope and value}

## Requirements

### Requirement: {Requirement Title}
{1-2 sentence normative description containing SHALL or MUST}

ID: REQ-{spec-name}-001
Source: {RFC/design-doc §section, doctrine principle, or [Observed] code ref}
Scope: v1-mandatory

#### Scenario: {Scenario Name}
- **WHEN** {condition/trigger}
- **THEN** {expected outcome}
- **AND** {additional outcome if applicable}
```

Delta spec: identical requirement/scenario blocks under `## ADDED Requirements`,
`## MODIFIED Requirements`, `## REMOVED Requirements`, or `## RENAMED Requirements`
instead of `## Requirements`. A new capability without a canonical main spec may
include an authored `## Purpose` before its operation sections: OpenSpec 1.9
carries that prose into the new main spec on archive instead of writing a TBD
purpose. Requirements still belong under delta-operation headings. A delta does
not replace an existing canonical Purpose; edit the main spec directly to change it.

## Requirement Block Ordering

The canonical block order is: requirement heading → normative SHALL/MUST paragraph → contiguous `ID`, `Source`, `Scope` lines → scenarios. The normative paragraph must be the first non-empty content after the heading so OpenSpec 1.3.1 recognizes the requirement; metadata-first blocks are invalid. Keep the three metadata lines adjacent, and put the first scenario immediately after them without displaced prose.

## Traceability Fields

Three contiguous plain-text lines immediately after the normative paragraph,
before any `#### Scenario:` heading:

- **ID** — `REQ-{spec-name}-NNN`; `{spec-name}` is the spec directory name,
  `NNN` zero-padded and unique within the spec. IDs are permanent: `MODIFIED`
  keeps the ID, `REMOVED` retires it forever (never reuse), `ADDED` takes the
  next free number across main + active delta specs. A main requirement may
  pair with one matching active `MODIFIED` or `REMOVED` restatement; a second
  active delta restating that identity is a conflict and must be reconciled.
- **Source** — what justifies the requirement: RFC section
  (`RFC 0003 §2.1`), doctrine principle (`heart-and-soul/vision.md #3`), or
  observed behavior (`[Observed] src/config/loader.py`). One line, citable.
- **Scope** — `v1-mandatory` (must implement; generates tasks + tests) ·
  `v1-reserved` (schema defined, implementation may stub) · `post-v1`
  (documented for awareness only).

Normative language and `Scope` must agree. Every requirement paragraph uses
`SHALL` or `MUST`; qualify a `post-v1` obligation with its future scope so it
does not imply current delivery. A `v1-mandatory` requirement cannot be
softened into optional or deferred behavior without an approved spec amendment.

The chain these fields close: doctrine principle → RFC §section → spec
requirement (`ID`) → WHEN/THEN scenario → test citing the ID → code.

## Test Tagging

Every `v1-mandatory` requirement is pinned by ≥1 gate that cites its ID: the
requirement ID in the test name
(`test_REQ_core_config_001_missing_field_blocks_startup`) or in a
comment/docstring adjacent to the test (`Spec: REQ-core-config-001`), or in a
CI guard script for structural invariants. `spec-trace-check.py` greps the
test tree for IDs and reports uncovered requirements and stale IDs (cited in
tests, absent from specs).

Coverage is a presence check, not a per-requirement quota. One requirement
gets one gate species (behavior test, scan guard, or type rule), and one seam
test may cite several adjacent requirement IDs. Do not create a test per
requirement when one behavior test at the seam pins them together, and do not
add a source-grep test beside a behavior test for the same requirement.

## Heading Hierarchy

- H1: Main spec title (one per file)
- H2: `Purpose` + `Requirements` (main) / delta-operation headings, with optional `Purpose` for a new capability only (delta)
- H3: Requirement (e.g., `### Requirement: Configuration Loading`)
- H4: Scenario (e.g., `#### Scenario: Valid config loads successfully`)

## WHEN/THEN/AND Rules

- Bullet list format (`- `)
- Bold keywords: `**WHEN**`, `**THEN**`, `**AND**`
- 1 WHEN, 1 THEN, 0-4 AND lines per scenario
- Describe *observable behavior*, not implementation details

## Semantic Quality Gate

A mechanically valid spec is not necessarily complete. Before sign-off, verify:

1. **Clear** — one normative behavior per requirement; actor, trigger, outcome,
   and externally visible side effects are unambiguous. Delete narrative and
   duplicated rationale that do not constrain behavior.
2. **Comprehensive** — every stated success criterion maps to a scenario. For
   each applicable risk dimension, cover it or record an explicit N/A reason:
   failure/recovery, authorization/privacy, concurrency/ordering/idempotency,
   lifecycle/retention, limits/performance, compatibility/migration, and human-
   interface accessibility/discoverability/loading/error behavior.
3. **Concise** — requirements state intent once; scenarios add distinct cases,
   not paraphrases. Design and implementation detail live in their own
   artifacts.
4. **Bounded** — proposed changes record in-scope and out-of-scope behavior in
   the changeset proposal; no `TODO`, `TBD`, or unresolved `[Unknown]` remains in
   a `v1-mandatory` requirement at sign-off.
5. **Traceable** — every requirement cites VISION/doctrine or a design contract,
   and every `v1-mandatory` requirement has an implementation and verification
   path before milestone closeout.

When a change has a human-facing surface, consult `/th-design` for the relevant
experience subdomain and translate its conclusions into observable scenarios.
For delivery-quality constraints, consult project `craft-and-care` first, then
the one relevant `/th-engineering` subskill when the project bar is silent.

## Artifact Placement

| Content | Home |
|---|---|
| Observable capability behavior | Main or delta spec requirement/scenario |
| Why, value, scope, explicit non-goals | Changeset `proposal.md` |
| State machines, wire/data contracts, technical trade-offs | Changeset `design.md` or project RFC |
| Tests, observability, migration, rollback, review steps | Changeset tasks/acceptance criteria, unless itself observable behavior |
| User-experience contract | Spec scenarios, informed by the relevant `/th-design` subskill |

## Formatting Conventions

| Pattern | Usage | Example |
|---------|-------|---------|
| Backticks | Code identifiers, config keys | `config.toml`, `load_config()` |
| Bold | Keywords and type names | **WHEN**, **AppConfig** |
| Quotes | String values, enum options | `"suggest"`, `"fail"` |

## Requirement Title Patterns

- State: "Requirement: Configuration Loading and Validation"
- Tools: "Requirement: Calendar Event CRUD Tools"
- Lifecycle: "Requirement: Startup Phase Sequence"
- Policy: "Requirement: Conflict Detection and Resolution"

## Scenario Naming

- Success: "Scenario: Valid config loads successfully"
- Error: "Scenario: Missing required field blocks startup"
- Edge: "Scenario: Unresolved env var blocks startup"

## Special Tags

`[TARGET-STATE]` marks aspirational/unimplemented requirements:

```markdown
### Requirement: [TARGET-STATE] Calendar Sync and Projection
```

## Spec Naming Conventions

Adopt a `{category}-{name}` pattern. Common categories:

- `core-{component}` — foundational infrastructure
- `module-{name}` — pluggable feature modules
- `api-{area}` — API surface areas
- `service-{name}` — standalone services
- `connector-{name}` — external integrations

Adapt categories to match the project's own architecture.

## Density Guidelines

- Purpose: 2-3 sentences
- Requirements per spec: 8-15 typical
- Scenarios per requirement: 2-5 typical
- Focus on *spirit and intent* — describe what the system does for the user,
  not internal architecture or data structures

## Legacy Specs Without IDs

Pre-existing specs missing `ID`/`Source`/`Scope` lines are warnings by default
and errors under `--authoring` or `--strict`. Invalid block ordering is always
an error because OpenSpec cannot parse metadata-first requirements. Backfill
opportunistically: any edit that touches a requirement restores the canonical
order and adds its fields in the same change. New requirements always use the
canonical order and carry all three fields.
