# Vision Closure: Claim Ledger, In-Use Audit, Closure Gate

The surface audit grades what exists; nothing in it asks whether the product
keeps the vision's promises. A project can hold every surface at `solid` and
still sit halfway to its vision. These four mechanisms score the vision
itself, so a run ends knowing how much of the gap its backlog closes.

## 1. Claim ledger (Phase 0)

Decompose `about/heart-and-soul/vision.md` and the success criteria into
**4–10 claims**: each one promise a user could find true or false. Quote the
doctrine line each comes from.

Doctrine rarely states three claims that every vision implies. Add them,
labelled `[Implied]`:

- **Beats the null alternative** — what the persona would do without this
  project (a plain LLM session, a spreadsheet, the incumbent tool).
- **Worth its cost to use** — time, money, and failed attempts per result.
- **Stays true as the world moves** — the output does not rot when its inputs
  change.

Per claim, record:

| Field | Content |
|---|---|
| `claim` | The promise, with its doctrine citation or `[Implied]` |
| `comparator` | What it is better than, or `none` |
| `measure` | How a sceptical outsider would check it |
| `status` | `demonstrated \| measured-short \| unmeasured \| unaddressed` |
| `evidence` | The in-use artifact, eval result, or "none" |
| `covering_beads` | Open and in-flight beads that would move it |
| `projected` | Status if every covering bead lands as written |

Rules:

- `demonstrated` needs evidence from the product in use (§2) or an existing
  eval. Source reading supports `[Inferred]` at most.
- **An unmeasured claim's first move is its measure.** Rank that move above
  any polish on the same claim: improvements nobody can see land are the
  cause of repeated runs.
- A claim that only the owner's taste can operationalize ("beautiful",
  "feels right") is a **taste gate**: it goes to the closeout questionnaire
  with concrete samples or a request for a reference, and work on it is not
  ranked on the pursuit's guess.
- **Stalls.** A claim whose covering beads have stayed open across two runs
  with no status change is reported as stalled, with the cause (queue order,
  blocked, bead too vague to execute). Re-ranking or re-scoping that work is
  a legitimate move even though it is not new. The already-known ledger
  dedups *findings*; it never hides an open gap.

Carry the ledger forward: the next run starts from the prior ledger and
reports claim movement the way the tier board reports surface movement.

## 2. In-use audit (opens Phase 1)

Before the surface walk, use the product the way its primary persona would.

1. Run it end to end on a **real input**, never a fixture, and keep the
   resulting artifact or session transcript.
2. Run the null alternative on the same input when one exists and costs
   little.
3. Judge the artifacts, not the code. Where practical, give both to a
   fresh-context agent without labels and ask the persona's own questions;
   record which answered better and why.
4. Record guardrail figures for the run: cost, wall time, and yield
   (attempts that failed, were retried, or were rejected by the product's
   own checks).

These feed the claim ledger's `evidence` and the audit lane's opening
prompt, so every surface verdict is read against what the product actually
produced.

- Prefer an eval harness the project already has. The pursuit does not build
  one; a missing harness is a move.
- Cannot run it (no environment, destructive, real money)? Say so and leave
  the affected claims `unmeasured`. Never substitute source reading and call
  it use.
- Running the product is observation. It does not breach "this skill never
  executes moves itself".

## 3. Apparatus check (Phase 3)

Checks, schemas and gates are cheap to propose and every one taxes the
product's own yield.

- Every move states its **user-visible effect** in one line. A move with
  none is tagged `internal` and names the claim it enables.
- A move that adds a check, schema or gate states its expected effect on the
  guardrail figures.
- When `internal` moves outnumber user-visible ones, the synthesis re-ranks
  or justifies the balance in the dossier.

## 4. Closure gate (ends Phase 3)

The question the owner would otherwise ask by hand, asked before the dossier
ships. Dispatch one fresh-context agent (no lane history; it counts toward
the session ceiling) with the doctrine, the claim ledger, the whole backlog
(open beads plus this run's moves), and the in-use artifacts. Ask:

> Assume every planned bead lands exactly as written. Per claim, is it then
> demonstrated? Would the primary persona choose this over the comparator?
> What in the end product would you not be proud of, and what does no bead
> address?

It returns a projected status per claim, a shortfall list, and one overall
fraction. The fraction is a judgment and is labelled as one; the per-claim
table is the durable output.

Every shortfall is resolved one of three ways:

- a **move** added to the plan,
- an **owner gate** (taste, doctrine amendment, contract) sent to the
  questionnaire, or
- a **named deferral** with its reason, in the dossier's open-gaps list.

Re-synthesize and re-ask at most once more. Whatever is left ships named as
an open gap. A run may end with the gap open; it may not end with the gap
unstated.

**Precedent:** in November 2026, after five pursuit runs on one product, the
owner asked this question by hand and got "50–60% of the vision, and no bead
tests whether it beats a plain session". Five runs of surface audits had
never asked it.
