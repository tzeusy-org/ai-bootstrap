---
name: cruft-cleanup
description: >
  Use when reviewing or writing refactors, renames, or migrations where old interfaces may
  linger as aliases, re-exports, wrappers, fallback branches, deprecated flags, or other
  compatibility cruft. Especially relevant when changed code keeps both old and new paths
  alive inside the same repo.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
  status: active
  last_reviewed: "2026-06-12"
---

# Cruft Cleanup

LLMs preserve old interfaces "just in case." In same-repo refactors, that leaves dead wrappers, aliases, and fallback paths instead of a finished migration.

When refactoring, migrating, or renaming, leave **only the new code path** unless backward compatibility is explicitly required.

## When to Use

- A function, module, type, flag, config key, or CLI option was renamed, moved, or replaced
- A diff keeps both old and new interfaces alive in the same repo
- A refactor adds aliases, re-exports, wrappers, or fallback branches "for compatibility"
- LLM-generated code says some variant of "old path still works"
- Tests were updated incompletely and still exercise the retired interface

## Do Not Use This Skill When

- Published APIs with real downstream consumers
- Cross-repo migrations that cannot be completed atomically
- Temporary compatibility layers with a verified owner and removal date
- Residue at architecture scale (layers, abstractions, whole surfaces the
  spec never needed) rather than one migration's leftovers —
  [greenfield-parity](../greenfield-parity/SKILL.md)

## Core Rule

**If you changed it, finish the job.** Every callsite, import, reference, and test uses the new interface. Delete the old one — not deprecate, not re-export, not alias.

Catch yourself writing a compatibility alias, wrapper, or fallback? Stop and update the callers instead.

## Read Order

| File | Read when | Content |
|------|-----------|---------|
| [`references/audit-flow.md`](./references/audit-flow.md) | Every real use of this skill | Proactive and reactive workflow, grep strategy, and review sequence |
| [`references/patterns-to-delete.md`](./references/patterns-to-delete.md) | You need concrete examples of cruft | Common shim/fallback/tombstone patterns to remove |
| [`references/compat-boundary.md`](./references/compat-boundary.md) | You suspect compatibility might be legitimate | Hard boundary for when old interfaces may stay temporarily |

## Trigger Sanity Check

- Should trigger: "This rename left a wrapper and old re-export behind so callers still work."
- Should trigger: "Clean up this refactor — I think there are leftover aliases and fallback branches."
- Should not trigger: "We need a planned deprecation layer for an external API consumed by other repos."
