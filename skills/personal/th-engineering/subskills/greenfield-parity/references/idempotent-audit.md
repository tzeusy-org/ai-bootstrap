# Idempotent Audit

File beads so that running the audit twice files them once. Bead form is
beads-writer's contract
(`beads-orchestration/subskills/beads-writer/SKILL.md`); this adds only what
keeps re-runs convergent.

## Already-known ledger (before writing any finding)

```bash
# open + in-progress
bd list --json | jq -r '.[] | select(.status != "closed") | "\(.id) \(.status) [\((.labels // []) | join(","))] \(.title)"'
# recently closed
bd list --status closed --json | jq -r '.[] | "\(.id) \(.closed_at // "") \(.title)"' | sort -k2 -r | head -40
# prior reports and their convergence records
ls docs/reviews/ 2>/dev/null
```

Inject the ledger verbatim into every reviewer prompt as "known — report
still if present, tagged with the bead id". Reviewers report everything;
the orchestrator reconciles.

## Reconciliation states

Match each finding by audit-key, then surface and title. In preference
order:

| State | Condition | Action |
|---|---|---|
| attach | open/in-progress bead covers it | `bd update <id> --append-notes "<date> audit: still present at <file:line>"` |
| supersede | open bead covers it partially or wrongly | new bead with `Supersedes: <id>` in design; close the old one with reason |
| reopen | closed bead, finding still true | `bd reopen <id>` — a regression, say so |
| close | ledger bead whose finding is now absent | `bd close <id> --reason "verified absent by audit <date> @<sha>"` |
| new | no match | create, per beads-writer |

The report lists each finding's state.

## Audit-key

Design field of every audit-derived bead:

```
Audit-key: greenfield:<residue-class>:<primary path>
```

e.g. `greenfield:layer-inflation:src/api/handlers/`. Next run greps
`bd search "Audit-key: greenfield:"` and matches class + path before title
similarity. Keys name the surface, not the line, so they survive moves.

## Progressive ordering

File in an order workers land without re-touching earlier work; wire
`bd dep add` accordingly:

1. Constraint write-downs (spec/doctrine edits for `justified` rows) — no
   code dependency, can run first
2. Leaf residue, `complexity:low` — narration, defensive excess, dead
   tooling, single-caller scripts
3. Boundary reshapes, `complexity:medium` — collapse a pass-through, merge
   duplicated concepts, drop one-implementation abstractions
4. Surface rewrites, `complexity:high` / `epic` — only where the rewrite
   rule fired; the bead says why incremental lost and names the model tier

An epic groups 3–7 and ends with beads-writer's reconciliation bead, whose
acceptance includes "re-run the greenfield delta on the merged head: zero
new findings for this surface".

## Convergence record

Append to the delta report, and to `docs/reviews/<date>-greenfield-delta.md`
where the project keeps review packets:

```
Audited: <sha> on <date>
Findings: <n> — new <a> · attached <b> · superseded <c> · reopened <d> · closed <e>
Parity share: <x>% parity · <y>% justified · <z>% residue
```

Next run cites the previous SHA and reports movement. `new 0 · reopened 0`
on the remediated head is the loop closing — report it as such, not as
"nothing found".
