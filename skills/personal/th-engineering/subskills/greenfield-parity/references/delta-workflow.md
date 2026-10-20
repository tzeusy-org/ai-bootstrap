# Delta Workflow

Measure a codebase against the build its spec deserves. Findings feed
[`idempotent-audit.md`](./idempotent-audit.md) when they become beads.

## Step 0 — Spec statement

A few lines: goal, hard requirements, real constraints (platform, data
volume, integrations, team size). Sources in order: `about/heart-and-soul/`
+ `openspec/` → README → manifests/tests → inference. Label lines
`[Observed]` / `[Inferred]`; an inferred spec is still the baseline — say so,
since vagueness here becomes vagueness in every finding. State the
production status: no external consumer → compatibility never justifies
excess below.

## Step 1 — Greenfield sketch (before reading implementation)

Inputs: spec statement, manifests, top-level layout only. Write:

- Modules, one responsibility line each
- Interfaces between modules: what crosses each boundary
- External dependencies a from-scratch build takes, and why
- Order-of-magnitude size: files/LOC per module, test shape
- Build/tooling surface: commands and what each gates

Time-box it. Record verbatim in the report — every delta finding cites it.

Delegation: the sketch may go to a fresh-context subagent (independence);
a repo-wide inventory may go out when it would flood the session (context
isolation). Classification and the delta table stay inline — one warm
session reading the sketch beats N cold ones re-deriving it.

## Step 2 — Inventory the actual

```bash
# files per directory (top 20) and total LOC
find <root> -type f -name '*.<ext>' -not -path '*/node_modules/*' | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -20
tokei <root> 2>/dev/null | tail -3
```

Then by reading: dependency count (unused flagged), abstractions with their
implementation/value count (interfaces, base classes, generics, config
knobs), layers one request crosses, tooling surface, test count and runtime.

## Step 3 — Delta table

One row per actual component (module, layer, dependency, tool):

| Actual | Greenfield counterpart | Verdict | Residue class | Evidence |
|---|---|---|---|---|

Verdicts: `parity` (1:1) · `justified` (sketch missed a real constraint —
name it) · `residue` (nothing in the spec needs it) · `gap` (sketch has it,
code doesn't — route to spec/direction, not filed here).

| Residue class | Tell |
|---|---|
| history | shim, completed migration, "legacy" comment, two paths for one behavior |
| speculative generality | interface/base/generic/plugin point with one implementation; knob with one value ever set |
| duplicated concept | two names or modules for one domain idea; parallel helpers that drifted |
| layer inflation | pass-through that renames and forwards; service → manager → handler chains |
| defensive excess | validation or fallback for states the types or caller already exclude |
| narration | comments restating code, docstrings on obvious functions, changelog-in-comments |
| tooling sprawl | build/CI/lint surface larger than the code it guards; single-caller scripts |
| test bloat | tests of mocks, unread snapshots, fixtures duplicating fixtures |

## Step 4 — Justify or cut

`justified` with no written constraint → documentation finding: constraint
into spec/doctrine in the same change, or the row becomes `residue`.
`residue` → target state is the sketch's counterpart.

Rewrite vs. incremental: when one surface's residue rows outweigh its parity
rows, or removing them one by one re-touches the same files, file one
rewrite-to-target finding instead of N removals. Name the rule that fired;
the bead carries `complexity:high` or `epic` and the model tier it needs.

## Finding contract

Each finding, for a cold reader:

- `file:line` or module path, residue class
- Greenfield counterpart it becomes, quoted from the sketch
- Blast radius: callers, tests, docs touched
- Removal complexity `low` (delete + fix callers) · `medium` (reshape a
  boundary) · `high` (rewrite a surface) — maps straight to the bead label
- Severity by reader and operator cost, not LOC: a pass-through in the
  request path outranks a narrating comment

Report outcome-first: overall parity share (`parity`/`justified`/`residue`),
rewrite surfaces if any, then the table. Then
[`idempotent-audit.md`](./idempotent-audit.md).
