# Packet Contract

Use this reference while drafting or refreshing an asynchronous owner packet.

## Authority model

The packet is a review aid and audit trail. It never supersedes the target
project's heart-and-soul, RFCs, specs, topology, engineering standards, tracker,
or runtime. Put this boundary near the top of every packet.

Admit only a decision whose answer changes an owner-controlled outcome. For each
candidate ask: "What would an agent be unauthorized or unable to decide?" If the
answer is only code structure, test scope, migration mechanics, or other normal
engineering judgment governed by existing artifacts, resolve it autonomously.

## Packet header and matrix

Start with:

- **Evidence snapshot:** date, repository, exact commit/ref, and live sources
  checked or deliberately not checked.
- **Artifact boundary:** what the document records and what it cannot authorize.
- **Decision matrix:** stable ID, priority, state, and one-line recommendation.

Order by dependency first, then user impact/risk. Do not use order to imply a
bulk approval.

Before writing a packet inside a Git worktree, require
`git check-ignore --no-index --quiet -- <packet-path>` to succeed. If it does
not, choose a known ignored local directory or ask the user; never modify
`.gitignore` merely to hide a packet without explicit scope.

## Required item schema

Each `### \`stable-id\`` section contains:

1. **Decision state** — one state from the lifecycle below.
2. **Adversarial review** — reviewer ID, date, and pass status.
3. **Decision needed** — one sentence naming the owner act.
4. **Background and freshness** — the minimum context needed to choose; label
   material claims `[Observed]`, `[Inferred]`, or `[Unknown]` and identify stale
   or uninspected evidence.
5. **Options** — 2-4 genuine choices. Each row has a short outcome, pros, and
   cons. Do not manufacture a false option or hide the status quo.
6. **Recommendation** — choose one option and trace it to doctrine/spec,
   user value, tractability, dependencies, risk, and churn where material.
7. **Recorded decision boundary** — the exact scope chosen by the owner and
   explicit exclusions. The packet releases no artifact mutation or
   live/external/destructive action; the destination workflow obtains any
   separate authorization it requires.
8. **Adversarial review record** — reviewer, separate scope and recommendation
   verdicts, material corrections, and evidence freshness.
9. **Owner decision record** — state, authenticated actor/channel, ISO timestamp,
   chosen option or normalized edited choice, owner edits, post-answer review,
   final authorization boundary, canonical destination workflow, and routing
   evidence. Keep safe provenance, never secrets or owner-private values.
10. **Evidence** — concise `path:line`, canonical ID, URL, query, or command
   references sufficient for another agent to refresh the item.

Optional, after Recommendation: **Diagram** — one image link,
`![caption](relative/path.svg)`, when the options differ in architecture,
data flow, or topology and a before/after picture decides faster than prose.
Draw it with `/th-engineering` excalidraw-diagram (light theme, SVG) and keep
the file beside the packet; the web page inlines it and refuses paths outside
the packet's directory. Skip it for policy or wording decisions.

Aim for 150-350 words per ordinary decision. Spend more only when options have
materially different security, data, or architecture consequences. Put shared
background once in a preface and link to it from items.

## Lifecycle

| State | Meaning |
|---|---|
| `Draft` | Framing or evidence is incomplete; do not present for signoff. |
| `Needs-rework` | Adversarial review found a material defect. |
| `Review-ready` | Scope and recommendation each passed independent subagent review. |
| `Agreed` | Owner chose the recorded option and boundary. |
| `Held` | Owner deliberately deferred or an external gate remains. |
| `Rejected` | Owner explicitly declined every presented option/current scope. |
| `Superseded` | A newer canonical decision replaces this item. |
| `Applied` | The owning workflow received/applied the signoff and evidence was recorded. |

The structural validator accepts `Review-ready`, `Agreed`, `Held`, `Rejected`,
and `Superseded` in owner-handoff mode. `Applied` is an after-signoff state and
belongs in the decision record, not a pending questionnaire matrix.

## Freshness and deduplication

- Refresh drift-prone evidence before drafting and again before applying.
- Refresh drift-prone evidence before every walkthrough session and before
  presenting an item that may have changed during the asynchronous delay.
- Link one packet item to every affected tracker ID; never create parallel
  questions for the same owner act.
- If the canonical contract changed, set the item to `Draft`, explain the drift,
  and re-run review.
- Preserve prior answers. Amend with a dated correction or mark `Superseded`;
  do not silently rewrite what the owner approved.

## Concision checks

Delete history that does not change the choice. Keep decisive evidence,
constraints, and failure modes. Every option must be understandable without
opening the repository, while every claim must remain refreshable from its
evidence reference.
