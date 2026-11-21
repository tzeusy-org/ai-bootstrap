# Pursuit Deliverables: Dossier, Scorecard, Epic, Memory

## Dossier

Home: the project's established pursuit home if one exists (check prior
dossiers); default `docs/pursuits/`. Two files per run, docs-only and safe
to commit directly where repo convention allows:

- `YYYY-MM-DD-vision-pursuit.md` — opens with the closure verdict (per-claim
  projection if the backlog lands, the judged overall fraction labelled as
  judgment, and the open-gaps list), then north star (quoted), claim
  scorecard and tier board with movement vs baseline, in-use audit summary
  with guardrail figures, systemic themes with exemplars, ranked move list
  (what / why with claim and doctrine citation / evidence / user-visible
  effect / rough slice plan), the per-claim closure plan naming later-wave
  beads, and the known-ledger snapshot the run deduped against.
- `YYYY-MM-DD-vision-pursuit-data.json` — full per-unit (surface or lens)
  structured output, plus the claim ledger under `.claims[]` (the next run's
  baseline) and the closure-gate return.
  Document the access pattern in the md, e.g.
  `jq '.audits[] | select(.surface=="<key>")'`.

The `-harvest.json` and state working files (execution discipline §3) stay
beside them, gitignored and never committed. They are the raw material the
dossier can be rebuilt from.

### Size and retention caps

Dossiers accrete every run; cap them so the tree does not become the archive.

- The md stays under **25 KB**, with at most **15 ranked moves** of one short
  paragraph each. Per-move detail lives in the bead's structured fields and
  the JSON, not the md.
- The JSON holds structured per-unit output only (no transcripts, prompts or
  raw tool output) and stays under **500 KB**.
- Only the **two newest runs** keep their `-data.json` in the tree. The change
  adding a new pair removes older JSONs and repoints each pruned run's md
  (links and `jq` examples) to `git show <sha>:<path> | jq …`, pinned to the
  last commit that held it. Git history is the archive.

## Tier board

One row per surface: verdict tier
(`world-class | solid | functional | weak | broken | absent`) and a movement
column vs the prior dossier's baseline. `absent` rows are capabilities the
claim ledger needs that no surface provides. Movement is only *claimed* for surfaces a
verification pass confirmed (Phase 0 step 6); movement inferred from source
reading alone is labeled [Inferred]. First run: tiers only, no movement
column.

## Claim scorecard

One row per claim from the ledger
([`vision-closure.md`](vision-closure.md) §1): status, movement vs the prior
ledger, evidence, covering beads, projected status. `demonstrated` is only
claimed on in-use or eval evidence. Stalled claims are flagged with their
cause. The scorecard sits above the tier board: surfaces serve claims.

## Epic (runnable on creation)

No epic-level gate: the owner does not sign off before the fleet may start.
The epic and every mandate-grounded move are ready as soon as they are filed.

1. Create the epic + one child bead per move, unassigned, so they appear in
   `bd ready`. That includes closure-plan moves beyond the dossier's ranked
   15, at lower priority; the plan covers the gap even when the md does not
   list every bead.
2. Each move bead names the claim it serves and carries **outcome
   acceptance**: an effect observable in the product or its measure ("the
   comparison probe prefers ours on N of M questions"), never "implemented".
   A bead whose effect cannot be observed yet depends on the bead that
   builds the measure.
3. The epic's last child is a **scorecard bead**, dependent on the others:
   re-run the in-use audit and re-score the claims the epic targeted. A
   claim that did not move reopens work inside the epic, not at the next run.
4. Hold only moves with a real hard gate: a vision-extending move needs its
   doctrine amendment, a contract-changing move needs its waiver, and a move
   on a taste-gated claim needs the owner's reference. For
   each, create a `[HOLD]` decision bead **assigned to the owner** and make
   only that move's child depend on it. Never hold the epic or its siblings.
5. Every bead description cites its evidence and points at the dossier
   JSON by pinned commit (`<sha>:<path>`), so retention pruning never breaks
   the pointer.
6. Bulk creation: `bd create` Dolt-commits per write (slow, serialized) —
   use `--dolt-auto-commit batch` and one `bd dolt commit` at the end.
   **Never** `bd create --graph` (its `--dry-run` actually creates beads
   and drops deps); add edges with `bd dep add`.
7. State in the final report which moves are runnable now and which single
   moves are held, and on what decision.

No bd in the repo → the ranked move list stays in the dossier and the run
stops there; do not improvise a tracker.

## Owner dossier questionnaire

The run's hard human gates leave as one owner packet, built and published
with [`../../user-questionnaire/SKILL.md`](../../user-questionnaire/SKILL.md)
(steps 2-7: draft, independent adversarial review, validate, serve). The
pursuit author never self-reviews the items.

Admit exactly these, each as one item with 2-4 options and the dossier's
recommendation:

| Gate | Item shape | Decision ID |
|---|---|---|
| Doctrine amendment behind a vision-extending move | Adopt the amendment as drafted / adopt with edit / reject (move drops) / defer | `pursuit-<date>-amend-<move-key>` |
| External consumer contract a move would change | Waive with migration plan / keep the contract (move narrows) / defer | `pursuit-<date>-contract-<move-key>` |
| Taste gate: a vision claim only the owner can operationalize | Pick among 2-4 concrete samples or named references / supply a reference / defer (claim stays an open gap) | `pursuit-<date>-taste-<claim-key>` |
| Anything Phase 3 classified as a hard human gate | Per decision-autonomy.md | `pursuit-<date>-<key>` |

Ordinary moves are not questions; their priority is the dossier's job and
their execution is the fleet's. If the run produced no such gates, write
"No owner gates this run" in the dossier and skip the packet.

Packet home: `~/.tmp/user-questionnaire/<repo>-<date>-pursuit/packet.md`.
Each item's evidence cites the dossier JSON by pinned commit
(`<sha>:<path>`), and the dossier md links the packet path.

```bash
Q="<th-projects>/subskills/user-questionnaire/scripts"
uv run "$Q/validate_questionnaire.py" "$PACKET" --require-review-ready
uv run "$Q/questionnaire_web.py" serve "$PACKET"      # last line: QUESTIONNAIRE_URL=…
```

The final report ends with that `QUESTIONNAIRE_URL=` line. The next session
(or the apply session) starts with `questionnaire_web.py ingest "$PACKET"`:
an `Agreed` item with `apply_now: true` lets the applying agent close that
move's hold bead, citing the decision record in the close reason; adopted
amendments route to `../../project-shape/`; a rejected amendment removes its
move from the epic; `Held` items keep that one hold open and note the re-ask
trigger on it.

## Memory

Write or update one durable memory (`bd remember` where available,
otherwise a `reference` memory) holding the dossier path, epic id, hold-bead ids,
questionnaire packet path and URL, and artifact URL if one was published;
link the prior pursuit's memory so runs form a chain.
