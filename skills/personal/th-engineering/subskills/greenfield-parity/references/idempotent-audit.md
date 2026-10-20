# Idempotent Audit

How an audit files beads so that running it twice files them once. Bead form
(fields, complexity labels, acceptance, work-allocation) is beads-writer's
contract — `beads-orchestration/subskills/beads-writer/SKILL.md`; this file
adds only what keeps re-runs convergent.

## Already-known ledger (before any finding is written)

Build the ledger, then inject it verbatim into every reviewer or lane prompt
as "known — do not re-report; do report if closed and still true":

```bash
# open + in-progress, projected
bd list --json | jq -r '.[] | select(.status != "closed") | "\(.id) \(.status) [\((.labels // []) | join(","))] \(.title)"'
# recently closed on the audited surfaces
bd list --status closed --json | jq -r '.[] | "\(.id) \(.closed_at // "") \(.title)"' | sort -k2 -r | head -40
# prior audit reports
ls docs/reviews/ 2>/dev/null
```

Also include prior reports' convergence records (below) so this run knows
the last head audited.

## Reconciliation states

Match each finding against the ledger by audit-key (below), then by surface
and title. One of five outcomes, in preference order:

| State | Condition | Action |
|---|---|---|
| attach | open/in-progress bead covers it | `bd update <id> --append-notes "<date> audit: still present at <file:line>"`; no new bead |
| supersede | open bead covers it partially or wrongly | new bead with `Supersedes: <id>` in design; close the old one with reason |
| reopen | closed bead, finding still true | `bd reopen <id>` (or new bead citing it) — a regression, say so |
| close | ledger bead whose finding is now absent | `bd close <id> --reason "verified absent by audit <date> @<sha>"` |
| new | nothing in the ledger | create, per beads-writer |

`new` is reached only after the four matches fail; the report lists which
state each finding landed in.

## Audit-key convention

Each audit-derived bead carries a mechanical match key in its design field:

```
Audit-key: greenfield:<residue-class>:<primary path>
```

e.g. `Audit-key: greenfield:layer-inflation:src/api/handlers/`. The next run
greps `bd search "Audit-key: greenfield:"` and matches on class + path before
falling back to title similarity. Keys name the surface, not the line, so
they survive small moves.

## Progressive ordering

File beads in the order a worker can land them without re-touching earlier
work, and wire `bd dep add` accordingly:

1. Leaf residue: delete-and-fix-callers (`complexity:low`) — narration,
   defensive excess, dead tooling, single-caller scripts
2. Boundary reshapes (`complexity:medium`) — collapse a pass-through layer,
   merge duplicated concepts, drop one-implementation abstractions
3. Surface rewrites (`complexity:high` / `epic`) — only where
   `delta-workflow.md`'s rewrite rule fired; the bead states why incremental
   lost, and names the model tier (reasoning-heavy work; the coordinator
   maps the label, the design field says if a stronger tier is required)
4. Constraint write-downs — spec/doctrine edits that make `justified` rows
   reproducible next sketch; these have no code dependency and can run first

An epic groups 3–7 of these and ends with a reconciliation bead per
beads-writer; the reconciliation bead's acceptance includes "re-run the
greenfield delta on the merged head: zero new findings for this surface".

## Convergence record

Append to the delta report, and to `docs/reviews/<date>-greenfield-delta.md`
when the project keeps review packets:

```
Audited: <sha> on <date>
Findings: <n> — new <a> · attached <b> · superseded <c> · reopened <d> · closed <e>
Parity share: <x>% parity · <y>% justified · <z>% residue
```

The next run cites the previous record's SHA, diffs against it, and reports
movement. A run on the remediated head with `new 0` and `reopened 0` is the
loop closing — report it as such, not as "nothing found".
