# Interaction and Application

Use this reference when the owner starts answering or asks agents to apply the
recorded decisions.

## Asynchronous handoff

At handoff, report the packet path, item count, evidence snapshot, unresolved
unknowns, and validation result. Do not paste the whole packet into chat. The
document must be sufficient for a fresh agent to resume without author-only
context.

## Web form channel

`scripts/questionnaire_web.py serve <packet>` publishes the packet as a local
web form and prints its URL; the owner answers whenever they like. `serve`
publishes only a packet that passes `--require-review-ready` validation;
`render <packet> --out <file>` writes a read-only preview of any packet,
drafts included, flagged as not ready for signoff.

The page presents the same five elements per item as the chat walkthrough,
keeps every item independent, and asks the apply offer as its own final
control, never inside an item. It is one self-contained file (styles, script,
and diagrams inlined), so it works offline. Layout and behavior:

- an "at a glance" list of every decision with its one-line recommended
  direction and live answer status, plus any `## ` preface sections;
- per item: decision, recommendation (the option's name first), an optional
  `#### Diagram` figure, background with an Observed/Inferred/Unknown count,
  then option cards with pros (+) and cons (−) and hold/reject/edit controls;
  boundary, review record, and evidence stay one click away;
- answers save as they are given; a save that fails stays visible on its
  item with a retry, and the page never shows an answer the server lacks;
  clicking the active choice clears it without losing typed text; keys
  `j`/`k` move, `a`-`d` choose, `e`/`h`/`r` edit/hold/reject, `n` notes, `s`
  jumps to submit, `?` lists them; answer keys act only on the highlighted
  card;
- submit stays disabled until every item is answered, every edit is
  described, and the apply question is answered; the server enforces the same.

The page is rendered by [`scripts/questionnaire_page.py`](../scripts/questionnaire_page.py)
from [`assets/web/`](../assets/web/) (`page.html`, `page.css`, `page.js`);
change the look there, not in the server. `tests/test_page_browser.py`
drives `page.js` in headless Chromium; it skips unless Playwright is present
(`uv run --with pytest --with playwright pytest -q tests`).

Answers persist as JSON under `~/.tmp/user-questionnaire/` (override with
`QUESTIONNAIRE_STATE_ROOT`), atomically written, keyed by the packet's
absolute path so any session can find them. The file on disk is the only
copy; the server and `ingest` each update it under one file lock, so a
running server and a concurrent ingest never lose each other's writes. The
first answer pins the packet version being read; if the packet changes
afterward the open page tells the owner to reload and `ingest` refuses. The
URL carries a random token; the page is reachable to anyone holding the link
on the bound interfaces, which is why packets never carry secrets.

`status <packet>` reports progress (exit 0 only after the owner submitted;
`--wait N` polls). `ingest <packet>` writes the answers into the packet:

| Owner action on the page | Item state | Owner decision record |
|---|---|---|
| Picks option A-D | `Agreed` | Choice `A` (+ note); post-answer review not required |
| Edits the choice | `Needs-rework` | Choice `Edited: …`; fresh scope and recommendation passes required before routing |
| Hold | `Held` | Choice `Held: <reason>`; the reason is the re-ask trigger |
| Reject all | `Rejected` | Choice `Rejected: <reason>` |

Multi-line owner text is kept as indented continuation lines of its field.
`ingest` validates the result before writing and leaves the packet untouched
on any error; the validator's placeholder check skips owner-written `Choice`
and `Owner edits` text, which is the owner's words, not author scaffolding.
Every record carries the actor/channel (`owner via questionnaire web form`,
token-gated, source address), the ISO timestamp the page recorded, the
unchanged authorization boundary, the canonical destination the author
pre-filled (else `Unrouted` until the applying agent names the owning
workflow), and `Routing evidence: Not routed`.
`ingest` refuses when the packet changed after the owner began answering
(`--force` to override after re-validating the framing) or when the owner has not
submitted (`--partial` ingests the answered items only). Its output's
`apply_now` field is the owner's answer to the apply offer: `true` authorizes
the routing checklist below for exactly this packet's `Agreed` and `Held`
items; `false` or `null` leaves them recorded.

## One-question walkthrough

For each item, present:

1. the decision needed and why now;
2. the recommendation in one sentence;
3. 2-5 bullets of decisive background;
4. options labeled A-D, each with concise pros and cons; and
5. a direct request for the letter, an edited choice, defer, or reject.

Ask exactly one decision per turn. Record the answer in the packet immediately,
including authenticated channel, timestamp, normalized choice, any owner edit,
final authorization boundary, and canonical destination. Then restate the
recorded decision in one sentence and advance.

At the start of each walkthrough session, and before presenting an item whose
drift-prone evidence may have changed, refresh its tracker/dependency/current-head
facts. If framing changed, return it to `Draft` and re-review it.

An ambiguous answer is not approval. Ask one narrow clarification if multiple
interpretations would cause materially different changes. A request for more
evidence keeps the item `Held`; refresh it before asking again.

A materially edited option or broadened boundary is a new decision artifact:
record it, set `Needs-rework`, and obtain fresh scope and recommendation passes
before routing it. A plain selection of an existing option needs no post-answer
review. An explicit rejection becomes `Rejected`.

## Apply offer at the end of the walkthrough

When the last pending item has been answered, ask one closing question: apply
the recorded decisions now, or leave them recorded for later. Summarize first
which items are `Agreed`, `Held`, or `Rejected` and where each would be routed.
A "yes" authorizes routing of exactly those records through the checklist
below; it is not approval of any external, live, deployment, or merge action,
and it never extends to items answered in another packet. A "no" leaves every
item recorded with routing evidence `Not routed`.

## Applying decisions

Explicit permission to "apply", "update the beads/specs", or equivalent is a
separate routing gate. The questionnaire records and transports the signoff; it
does not mutate canonical project or external/live state itself. Before handing
off to the owning workflow:

- re-read every `Agreed` item and its exact authorization boundary;
- refresh tracker status, dependencies, current commit/base, active PRs, and
  overlapping ownership;
- stop if the governing artifact changed enough to invalidate the choice;
- identify and invoke the canonical owning protocol—project-shape doctrine
  amendment, project-feature-request/OpenSpec, project-direction/Beads, or the
  relevant operational workflow—and provide the exact decision record;
- require that workflow to remove only blockers actually satisfied by the
  recorded decision;
- retain every external, runtime, privileged, destructive, data-policy,
  deployment, and merge gate; the owning workflow must obtain any required
  explicit authorization separately from the questionnaire packet;
- validate the resulting tracker/spec graph; and
- record exact routing and downstream evidence in the packet.

### Update tracker artifacts

In a Beads-backed project the direction/Beads workflow performs these updates
(`/beads-orchestration`); elsewhere use the project's tracker equivalent:

1. Search open and blocked items for each decision first; update a matching
   item instead of filing a duplicate.
2. For each `Agreed` item, file or update one work item per cohesive outcome
   (see `../../references/work-allocation.md`). Each carries the decision ID,
   the owner's choice, the recorded timestamp, the packet path, and the final
   authorization boundary verbatim, plus the tracker's readiness fields.
   Group several decisions from one packet under one parent when that keeps
   them reviewable together.
3. Keep every separate gate as an explicit step or dependency: a doctrine
   change lands as a PR the owner merges; an environment, deployment, or live
   step names the separate approval it still needs.
4. For each `Held` item, note the re-ask trigger and the status-quo default on
   the item whose completion triggers it.
5. Append a short note to existing items the decision affects (parents,
   superseded or re-scoped work). Do not force-close an item assigned to the
   owner; note that it is ready to close instead.
6. Lint or validate the new and changed items, then write their IDs into each
   packet item's routing evidence and set the item `Applied` (or leave it
   `Held`).

For Beads, owner input can satisfy an owner-decision dependency without making
an implementation leaf runnable. A leaf is runnable only when its packet is
complete, all dependencies clear, no ownership overlap exists, and no other
gate remains.

## State transitions

```text
Draft -------------> Review-ready -> Agreed -> Applied
  |                       |            |
  +-> Needs-rework <------+            +-> Held
Review-ready -> Held | Rejected | Superseded
```

Never collapse `Review-ready` into `Agreed`, or `Agreed` into `Applied`.

## Final reconciliation

Report separately:

- canonical owning workflows invoked and artifacts they report updated;
- items unblocked and why;
- items still blocked and the remaining exact gate;
- items held/superseded;
- validation commands and outcomes; and
- external actions intentionally not taken.

If applying many decisions, use a table keyed by stable ID. Avoid claiming the
whole packet is complete when even one authorization boundary remains open.
