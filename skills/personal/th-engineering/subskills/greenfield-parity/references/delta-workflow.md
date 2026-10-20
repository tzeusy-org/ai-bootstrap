# Delta Workflow

How to measure a codebase against the build its spec deserves. Output feeds
[`idempotent-audit.md`](./idempotent-audit.md) when findings become beads.

## Step 0 — Fix the spec statement

Write, in a few lines, what the project must do: goal, hard requirements,
real constraints (platform, data volume, integrations, team size). Sources in
order: `about/heart-and-soul/` + `openspec/` → README → manifests/tests →
inference. Label each line `[Observed]` / `[Inferred]`. An inferred spec is
still the baseline — say so; vagueness here becomes vagueness in every
finding.

Note the production state explicitly: no external consumers → compatibility
is never a justification below.

## Step 1 — Greenfield sketch (before reading implementation)

Read only the spec statement, manifests, and top-level layout. Then write:

- Module list, one line of responsibility each
- Interfaces between modules (what crosses each boundary)
- External dependencies a from-scratch build would take, and why each
- Order-of-magnitude size: files and LOC per module, test count shape
- Build/tooling surface: what commands exist and what they gate

Time-box it; this is a baseline, not a design doc. Record it verbatim in the
report — it is the evidence every delta finding cites.

## Step 2 — Inventory the actual

Measure, don't impress:

```bash
# size by top-level module (tokei/cloc if installed; wc otherwise)
tokei --output json <root> | jq '.Total.code' 2>/dev/null
find <root> -type f -name '*.<ext>' -not -path '*/node_modules/*' | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -20
# one-implementation abstractions, pass-through layers, single-value knobs
rg -n "class \w+\((ABC|Protocol)\)|interface \w+" <root> | wc -l
```

Capture: module map and per-module size, dependency count (direct, with
unused ones flagged), abstraction count (interfaces, base classes, generics,
config knobs — each with its implementation/value count), layers a request
passes through, tooling surface, test count and run time.

## Step 3 — Delta table

One row per actual component (module, layer, dependency, tool):

| Actual | Greenfield counterpart | Verdict | Residue class | Evidence |
|---|---|---|---|---|

Verdicts: `parity` (maps 1:1) · `justified` (sketch missed a real
constraint — name it) · `residue` (nothing in the spec needs it) · `gap`
(sketch has it, code doesn't — route to spec/direction, not filed here).

Residue classes, each with its tell:

| Class | Tell |
|---|---|
| history | shim, migration already complete, "legacy" comment, two code paths for one behavior |
| speculative generality | interface/base/generic/plugin point with one implementation; config knob with one value ever set |
| duplicated concept | two names or two modules for one domain idea; parallel helpers that drifted |
| layer inflation | pass-through layer that renames and forwards; service → manager → handler chains |
| defensive excess | validation or fallback for states the type system or caller already excludes |
| narration | comments restating code, docstrings on obvious functions, changelog-in-comments |
| tooling sprawl | build/CI/lint surface larger than the code it guards; scripts with one caller |
| test bloat | tests of mocks, snapshot tests nobody reads, fixtures duplicating fixtures |

## Step 4 — Justify or cut

For each `justified` row with no written constraint: a documentation finding
— the constraint goes into the spec/doctrine in the same change, or the row
becomes `residue`. For each `residue` row: the target state is the sketch's
counterpart (often "nothing").

Rewrite vs. incremental: when the residue rows for one surface together
outweigh the parity rows, or removing them one by one re-touches the same
files repeatedly, file one rewrite-to-target finding for the surface instead
of N removals. Say which rule triggered it; the bead inherits `complexity:high`
or `epic` and names the model tier it needs.

## Finding contract

Every finding, for a cold reader:

- `file:line` (or module path) and the residue class
- The greenfield counterpart it should become, quoted from the sketch
- Blast radius: callers, tests, docs that change
- Removal complexity: `low` (delete + fix callers) · `medium` (reshape a
  boundary) · `high` (rewrite a surface) — maps straight to the bead label
- Severity by cost to the reader and operator, not by LOC: a pass-through
  layer in the request path outranks a narrating comment

Deliver the delta report outcome-first: verdict on overall parity (what share
of the codebase is `parity`/`justified`/`residue`), the rewrite surfaces if
any, then the table. Then hand to
[`idempotent-audit.md`](./idempotent-audit.md).
