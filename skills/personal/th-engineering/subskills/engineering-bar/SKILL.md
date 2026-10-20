---
name: engineering-bar
description: >
  Use when implementing or reviewing any non-trivial change and you need the canonical
  engineering quality bar — default biases, definition of done, and reviewable
  expectations — or when authoring a project's about/craft-and-care pillar that should
  reference this bar instead of restating it.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Fable 5
    - Claude Fable 5.1
    - OpenAI Codex
  status: active
  last_reviewed: "2026-10-20"
---

# Engineering Bar

Canonical default quality bar for implementation work. Projects adopt it by
reference from `about/craft-and-care/` and override individual biases there;
when a project pillar exists, the pillar wins. Everything here is a reviewable
expectation: a reviewer can reject a change for violating it.

## Use This Skill When

- Implementing a non-trivial change, deciding what "done" requires
- Reviewing a change holistically before merge
- Resolving a conflict between subdomain findings (e.g. readability vs.
  observability) — biases set the tiebreak
- Authoring or updating a project's `about/craft-and-care/` pillar (via
  `/th-projects` project-shape) — reference this bar, restate nothing

Example trigger phrasings: "hold this to the engineering bar", "is this
change done", "is this good enough to merge", "what does done require here",
"set the quality bar for this project".

## Do Not Use This Skill For

- Deep work in one subdomain — route to the sibling subskill
  ([code-readability](../code-readability/SKILL.md),
  [test-rigor](../test-rigor/SKILL.md),
  [dependency-hygiene](../dependency-hygiene/SKILL.md),
  [cruft-cleanup](../cruft-cleanup/SKILL.md),
  [greenfield-parity](../greenfield-parity/SKILL.md))
- Project-level governance, specs, or repo-wide audits — `/th-projects`

## Default Engineering Biases

Unless a project's `about/craft-and-care/` explicitly overrides them, these
biases apply:

1. **Prefer cleanup over same-repo compatibility cruft** — When a refactor,
   rename, or migration completes atomically inside the same repo, delete
   retired wrappers, aliases, fallback branches, dead flags, and unused paths
   rather than preserving them "just in case." Preserve backward compatibility
   only for a verified external consumer or real cross-repo migration
   constraint. ([cruft-cleanup](../cruft-cleanup/SKILL.md)
   operationalizes this.)
2. **Prefer readability and simplicity over cleverness** — When two approaches
   achieve equal correctness and reliability, take the simpler, more readable
   one. Dense, overly abstract, or surprising code needs strong
   justification. ([code-readability](../code-readability/SKILL.md)
   operationalizes this.)
3. **Bias toward observability** — Failure paths must be diagnosable. Logs on
   failure-prone paths carry enough structured context to narrow plausible
   causes, not merely report that something failed.
4. **Prefer durable fixes over expedient patches** — Don't optimize for
   "clear the error for now" when a correct, maintainable fix is tractable.
   Assume engineering time is available; optimize for correctness,
   reliability, long-term maintainability.
5. **Prefer explicitness over magic** — Visible control flow, explicit data
   movement, and obvious invariants beat hidden side effects, surprising
   framework behavior, and implicit coupling.
6. **Prefer fail-fast over silent fallback** — Unless graceful degradation is
   explicitly required by doctrine, specs, or design contracts, surface
   incorrect assumptions and invalid states loudly rather than masking them
   behind quiet fallback.
7. **Prefer same-change documentation and contract updates** — When behavior,
   assumptions, interfaces, or standards change, update the relevant docs,
   specs, RFCs, or standards in the same change. "We'll update the docs later"
   is a violation, not a plan. ([documentation](../documentation/SKILL.md)
   operationalizes this, including the code-citation discipline that makes
   doc claims re-verifiable.)
8. **Prefer verification depth over throughput** — Verification is deliberate
   and risk-scaled. Important changes get a second look before merge rather
   than assuming the first pass sufficed, and high-risk ones an independent
   one (fresh-context reviewer). The same agent does not re-run an identical
   passing check on an unchanged head for reassurance; independent
   verification and flake measurement are not that. Evidence (command
   output, test runs, rendered artifacts) beats assertion.
   ([test-rigor](../test-rigor/SKILL.md) operationalizes the testing half.)
9. **Take pride in the work, but evaluate feedback on merit** — Defend good
   work with rigor, not ego. Incorporate valid feedback quickly, stay humble
   about blind spots, push back clearly on incorrect, weak, or scope-distorting
   claims.
10. **Prefer greenfield parity over inherited shape** — The code should look
    as if written once, today, from the current spec. Complexity the spec
    does not need is residue regardless of how it arrived; a constraint that
    justifies it gets written down so it stops looking like residue. Without
    an external consumer, compatibility is not such a constraint.
    ([greenfield-parity](../greenfield-parity/SKILL.md) operationalizes
    this, including re-runnable audits.)

## Definition of Done

A non-trivial change is complete when all of these hold:

- **Behavior verified** — the change was exercised (tests, run, render),
  evidence stated, not implied.
- **No retired paths left behind** — every callsite, import, and test uses the
  new interface; the old one is gone (bias 1).
- **Failure paths covered** — new failure modes either fail fast or log with
  diagnostic context (biases 3, 6).
- **Docs and contracts current** — specs, READMEs, and standards the change
  touches are updated in the same change (bias 7).
- **Regression protected** — bugfixes ship with a test that fails on the old
  behavior (bias 8).
- **Test delta accounted** — added tests pass the worth-adding bar (each
  catches a bug no existing test catches) and net suite growth is stated;
  adds-only growth in a mature area is reviewed, not waved through (bias 8;
  [test-rigor](../test-rigor/SKILL.md) operationalizes).
- **A reviewer can follow it** — naming, structure, and commit message let a
  reviewer reconstruct intent without the author present (bias 2).

## Applying the Bar in Review

- Cite the bias or done-criterion a finding violates, with file:line evidence.
- Severity follows blast radius: silent fallback in a money path outranks a
  vague name in a test helper.
- When two biases tension (e.g. explicitness vs. simplicity), prefer the one
  protecting future readers and operators; say which you chose and why.
- Classify findings before changing scope:
  - Small, local violations clearly inside the approved outcome (naming,
    fixture clarity, missing focused assertion, dead local cruft) get fixed in
    the current change by its implementation owner, not filed separately.
  - Missing correctness for the accepted outcome remains in the active task/PR;
    repeated corrections rewrite the acceptance/failure matrix before more work.
  - A new subsystem, process-global state decision, trust boundary, migration,
    concurrency model, or other risk-class change becomes a linked prerequisite
    and returns to `/th-projects` at the earliest governing spec/allocation gate.
  - New behavior is spec-first; duplicates link to existing work.
- Preserve independent review. "Fix now" does not turn the reviewer into the
  implementation owner. If the reviewer authors semantic code, require fresh
  independent review of the exact resulting head.
