---
name: user-questionnaire
description: >
  Use to accumulate genuine owner decisions into a local asynchronous review
  packet, publish that packet as a local web form the owner answers in a
  browser, walk the owner through it one question at a time, ingest the
  recorded answers in a later session, or route recorded signoffs through
  canonical owning workflows. Every problem scope and recommendation receives
  a separate adversarial verdict from an independent subagent against the
  target project's th-projects shape and th-engineering bar before it is
  review-ready. Triggers: "prepare decisions for me to review", "review when
  I wake up", "publish the questionnaire", "give me a link to review", "walk
  me through the blocked decisions", "ingest my answers", "collect owner
  signoffs", "decision packet", "questionnaire".
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
    - Claude Fable 5.1
  status: active
  last_reviewed: "2026-10-21"
compatibility: Requires local file access; review-ready packets require a subagent facility. The validator and the web review surface run with uv and Python 3.11+, stdlib only; the web surface binds a local TCP port and prints a tailnet or FQDN URL.
---

# User Questionnaire

Cross-cutting human-gate transport for `th-projects`. It turns already
identified hard owner decisions into a durable, resumable review surface. It
does **not** become a sixth planning authority: doctrine, specs, topology,
engineering standards, and the project tracker remain canonical.

## Admit only genuine owner gates

Before drafting, apply the target repository's decision-autonomy policy. In a
Beads-backed project, use `/beads-orchestration` to distinguish hard human gates
from ordinary engineering judgment. Admit a question only when a human must:

- adopt or change doctrine, policy, product scope, or an observable contract;
- record a decision that an owning operational workflow may later use to seek
  separately bounded external, privileged, destructive, costly, or live action;
- choose among materially different user outcomes with no governing answer; or
- supply unavailable owner-only information.

Do not questionnaire implementation details an agent can resolve from doctrine,
specs, evidence, and the engineering bar. Do not infer approval from silence.
A run that produced no owner gates publishes nothing and says so.

## Build and review the packet

1. **Refresh the decision inventory.** Read the target project's local
   `th-projects` shape: heart-and-soul, governing RFC/spec, lay-and-land, and
   craft-and-care. Read the relevant tracker items and current implementation.
   Deduplicate by canonical decision or bead ID; record the evidence commit,
   timestamp, and anything live that was not inspected.
2. **Draft bounded items.** Copy
   [`assets/questionnaire-template.md`](assets/questionnaire-template.md). For
   each item, state one decision, 2-4 materially distinct options with pros and
   cons, a doctrine-grounded recommendation, and an exact authorization
   boundary. Follow [`references/packet-contract.md`](references/packet-contract.md).
3. **Dispatch an independent adversarial subagent per review batch.** Give the
   reviewer the draft items, source artifacts, and the target project's
   `th-projects` and `th-engineering` instructions—not the answer you hope to
   receive. The reviewer must issue a separate verdict for every item's
   **problem scope** and **recommendation**. Follow
   [`references/adversarial-review.md`](references/adversarial-review.md).
4. **Correct and re-review.** A material scope, option, recommendation, or
   authority-boundary correction returns that item to `Needs-rework`. It is not
   `Review-ready` until an independent subagent records `Pass` for both verdicts
   on the corrected artifact. If no subagent facility is available, leave it
   `Draft` and report the missing gate.
5. **Accumulate locally.** Write one local ignored `.md` packet unless the user
   names another location. The default home is outside any worktree:
   `~/.tmp/user-questionnaire/<repo>-<date>/packet.md`. Keep stable IDs and
   update in place so another agent can resume. Before writing under a Git
   worktree instead, verify the intended path with
   `git check-ignore --no-index --quiet -- <path>`; otherwise choose an
   ignored local directory or ask for one. Never place secrets, tokens, or
   owner-private values in it.
6. **Validate before handoff.** Run
   [`scripts/validate_questionnaire.py`](scripts/validate_questionnaire.py):

   ```bash
   QUESTIONNAIRE_SKILL_DIR="/absolute/path/to/loaded/th-projects/subskills/user-questionnaire"
   uv run "$QUESTIONNAIRE_SKILL_DIR/scripts/validate_questionnaire.py" \
     /absolute/path/to/packet.md --require-review-ready
   ```

   Resolve every error; the validator is structural evidence, not semantic
   review.
7. **Publish the web review surface.** Run
   [`scripts/questionnaire_web.py`](scripts/questionnaire_web.py) `serve`; it
   renders the validated packet as a self-contained HTML form, starts a
   background server on a random open port, persists answers under
   `~/.tmp/user-questionnaire/`, and prints the URL. Re-running it for the
   same packet reuses the running server, so the command is idempotent.

   ```bash
   uv run "$QUESTIONNAIRE_SKILL_DIR/scripts/questionnaire_web.py" serve \
     /absolute/path/to/packet.md            # prints JSON, then QUESTIONNAIRE_URL=…
   ```

   The handoff report ends with that `QUESTIONNAIRE_URL=` line, the packet
   path, item count, evidence snapshot, and unresolved unknowns. Do not
   paste the packet into chat. The page is the owner's walkthrough: one card
   per item with the recommendation, options, hold/reject/edit controls, and a
   separate final submit-and-apply question. No LLM tokens are spent after
   this command.

## Walk the owner through it

**Asynchronous answers arrive through the web form.** A later session starts
by ingesting them, then proceeds exactly as if it had recorded them in chat:

```bash
uv run "$QUESTIONNAIRE_SKILL_DIR/scripts/questionnaire_web.py" status /abs/packet.md   # exit 0 once submitted; --wait N polls
uv run "$QUESTIONNAIRE_SKILL_DIR/scripts/questionnaire_web.py" ingest /abs/packet.md   # writes owner records into the packet
```

`ingest` maps each answer into the item's owner decision record and state
(picked option → `Agreed`, hold → `Held`, reject → `Rejected`, edited option →
`Needs-rework`), refuses a packet that changed since it was served, re-runs
the validator, and reports `apply_now`. Edited items go back through steps 3-4
before routing. `apply_now: true` is the owner's explicit apply instruction
for exactly this packet's `Agreed` and `Held` items; `false` or `null` leaves
them recorded and unrouted. Details and the mapping table are in
[`references/interaction-and-application.md`](references/interaction-and-application.md).

**Live walkthrough in chat.** When the owner is present instead, load
[`references/interaction-and-application.md`](references/interaction-and-application.md).
Ask exactly one decision at a time in priority/dependency order. Lead with the
decision needed and recommendation, then give concise background and options
with pros/cons. Accept a letter, prose, an edited option, defer, or reject.
Refresh drift-prone evidence before presentation. Record actor/channel,
timestamp, exact or normalized answer, edits, post-answer review requirement,
authorization boundary, and destination workflow before advancing.

Never treat agreement to one item as approval of another. If fresh evidence
invalidates the framing, pause that item, revise it, and re-run adversarial
review instead of pressuring the owner to answer a stale question.

**Close every walkthrough with the apply offer.** After the last item is
recorded, ask once, as its own question, whether to apply the recorded
decisions now. A "yes" is the explicit apply instruction for exactly the
`Agreed` and `Held` items in this packet; a "no" or silence leaves them
recorded and unrouted. Never fold the offer into an item's question. The web
form asks it as its own final control for the same reason.

## Route recorded decisions

Route decisions only when the user explicitly asks. Re-read dependencies and
current heads first. The questionnaire itself never mutates specs, doctrine,
Beads, configuration, runtime, or external systems. Hand the exact decision
record to the canonical owning workflow—project-shape, feature-request/OpenSpec,
direction/Beads, or an operational protocol—and let that workflow enforce its
own review and mutation gates. No packet can release external/live action.

Use the apply checklist in
[`references/interaction-and-application.md`](references/interaction-and-application.md),
then mark each item `Applied`, `Held`, `Rejected`, or `Superseded` only from the
owning workflow's evidence. Keep implementation work in that normal workflow.

Applying is finished only when the tracker reflects every routed decision
(checklist § Update tracker artifacts): each `Agreed` item has a filed or
updated work item carrying its decision record and boundary, each `Held` item's
re-ask trigger is noted on the trigger item, affected existing items carry a
note, and the packet's routing evidence names those IDs. Then re-run the
validator without `--require-review-ready`, stop the web server
(`questionnaire_web.py stop`), and report what was routed.

## Do not use when

- One fuzzy feature still needs shaping into a spec → `../project-feature-request/`.
- Competing work needs prioritization or Bead decomposition → `../project-direction/`.
- The request is merely to inspect or mutate Beads → `/beads-orchestration`.
- The agent can make the decision under the repository's autonomy contract.
- The user is present and asks one isolated, already-bounded question; answer it
  directly unless durable asynchronous accumulation adds value.
