# Doctrine Amendment Procedure

How `about/heart-and-soul/` actually changes. Doctrine is constitutional —
slow to change by design — so this is the highest-blast-radius procedure in
the package: every RFC, spec, and code path grounded in the amended principle
is downstream. Load when a funnel Gate 2 conflict escalates, a direction
Phase 1 check finds doctrine contradicting reality, or a shape audit flags a
principle as wrong rather than merely stale.

**Hard rule: doctrine is never amended autonomously.** An agent drafts,
reviews, and sweeps; the human owner adopts. No exceptions, including
"obvious" fixes.

## Step 1 — Amendment proposal

Draft as a delta, not a rewrite:

- **Current text** — the principle verbatim, with file + rule number
- **Proposed text** — the amended principle
- **Motivation** — the concrete evidence that forced this: the feature
  request, finding, or observed reality contradicting the principle
  ([Observed]/[Inferred] labels as usual)
- **Blast radius** — every artifact citing the principle. Grep for the rule
  number and its key phrases across `about/legends-and-lore/`, `openspec/`
  (`Source:` lines cite doctrine — see
  [`../../../references/spec-format.md`](../../../references/spec-format.md)),
  and local skills. List each hit with a one-line impact judgment.

## Step 2 — Independent review

Change-tier discipline (see `../../project-direction/SKILL.md`
Reconciliation Protocol): fresh subagents that did not draft the proposal,
using the Coherence + Adversarial agent specs from
[`review-protocol.md`](review-protocol.md). The adversarial agent's brief:
argue the amendment is the tail wagging the dog — one feature bending the
constitution. If the strongest honest defense is "this one request needs it,"
the verdict is *reject the request*, not amend doctrine.

## Step 3 — Owner adoption

Present proposal + review findings + blast radius. The owner adopts, rejects,
or reworks. "Not quite right" → return to Step 1; never patch adopted text
directly.

## Step 4 — Downstream sweep

Immediately after adoption, in the same session:

1. Edit the doctrine file; keep numbered rules stable (amend text in place;
   retire numbers rather than renumbering — citations depend on them). The
   amended principle reads in present tense as current doctrine — no
   "previously…", "amended on…", or old-vs-new text in the file; that
   belongs in the step 4 decision record.
2. Walk the blast-radius list: re-align each RFC/design doc, and route spec
   impacts through a changeset (`openspec new change`), never silent spec
   edits.
3. Run `uv run <th-projects>/scripts/spec-trace-check.py <repo-root>` —
   `Source:` lines citing the amended principle must still resolve.
4. Write the decision record:
   `about/legends-and-lore/decisions/YYYY-MM-DD-doctrine-{slug}.md` with
   before/after text, motivation, review verdicts, and the sweep results.

An amendment with no completed sweep is an open wound, not a finished change
— if the sweep can't finish now, file the remaining artifacts as beads before
ending the session.

## Anti-patterns

- **Quiet exception** — implementing against doctrine "just this once"
  instead of amending or rejecting. The funnel's Gate 2 exists to prevent
  exactly this.
- **Amendment-as-appeasement** — softening a rejection into a doctrine change
  the owner never actually wanted. Rejection is the funnel working.
- **Doctrine-as-changelog** — appending the amendment beside the old
  principle ("Update: we now…") instead of rewriting it. Readers must
  reconstruct current doctrine from a history; the decision record already
  holds that history.
- **Sweep-later** — adopting the amendment and leaving downstream artifacts
  citing the old principle. Two truths, immediately.
