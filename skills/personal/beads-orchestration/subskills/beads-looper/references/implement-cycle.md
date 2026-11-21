# Implementation owner cycle

The [delivery model](../../../references/delivery-model.md) owns package, branch,
packet and evidence policy. This cycle uses existing worker phases; it does not
introduce an alternate batch framework.

## Select and claim

Run the shared ranker with Step 0's current `--review-state-file` and select from
its admitted `queue`, retaining the existing owner for correction/resume actions.
An implement-mode session cannot jump over queued review work simply because it
cannot review its own code: wake/notify an independent reviewer, park and wait if
needed. Mode is a capability filter, not a separate priority queue. Missing or
partial review state admits nothing. Prefer existing-owner corrections/resume and
runnable prerequisites before a new outcome. Inspect governing adoption, current
source assumptions, claims and rollback cohesion. Missing authority blocks its
dependent slice; missing packet content goes to bounded shaping, not silent scope
invention. For `scope:local-state`, require the owner's explicit request and keep
it out of ordinary isolated tracked-code delivery.

An owner naming a `scope:local-state` bead satisfies the readiness gate for its
local-state action only; it waives nothing else. The owner's request is that
action's packet. This is the one exception to never touching the shared root
checkout: the action may act on gitignored state in `REPO_ROOT` when it is
reversible (move aside, never delete, never tracked files), recording what
moved where on the bead. In a mixed bead, the local-state
part may proceed alone while the tracked part still lacks a complete packet: split
the tracked part into its own bead per the
[beads-writer split rule](../../beads-writer/SKILL.md) (the local-state bead keeps
`scope:local-state`), label that tracked bead `needs-shaping`, and close the
local-state bead on its own evidence. Without an owner request, a `scope:local-state` bead is never claimed.

One member uses native atomic claim. Multiple existing beads use the shared
`delivery_package.py` claim command with a manifest and durable journal. Associate
the anchor/member metadata and persist packet/session/journal locator on anchor.
No second owner starts unless the complete package claim succeeds. Ordered internal
member dependencies are allowed; external/hard gates are not bypassed. Claim only
current bounded work, retaining outcome affinity without claiming future backlog.

## Work

Create/recover one `agent/<anchor>` worktree with plain `git worktree add` from the
actual repository ([coordinator Step 3](../../beads-coordinator/references/coordinator-loop.md#step-3-claim-publish-current-packet-and-isolate)
shows the command; never `bd worktree create`, which edits the tracked `.gitignore`). Never move/edit the shared root checkout (sole exception: an owner-requested
`scope:local-state` action, per Select and claim). Use
[worker phases](../../beads-worker/SKILL.md) with `ISSUE_ID=<anchor>` and optional
`DELIVERY_FILE`; bootstrap verifies actual cwd, Git identity and membership.
Read the concise current packet and referenced contracts, not the archive.

As implementation session, own code, focused tests, failure diagnosis and ordinary
corrections. As lifecycle holder, apply the resulting updates yourself at checkpoints;
dispatched workers instead report to their controller. Renew registry/liveness
without gratuitous note traffic. Publish useful commits and current recovery state.
File discovered follow-ups as holder with the per-class dependency wiring in
[coordinator Step 6](../../beads-coordinator/references/coordinator-loop.md#step-6-land-or-wait-with-explicit-state):
only a prerequisite blocker blocks this outcome; new behavior links `discovered-from`.
Check each new follow-up with
[`check_packet.py`](../../beads-coordinator/scripts/check_packet.py)
`--repo-root "$REPO_ROOT" <new-id>`; without `packet_complete` it gets
`needs-shaping`, so the ranker holds it for beads-writer rather than this or
another lane claiming it on the next wake; a packet-complete one is ordinary
ready work for any lane. A shaping-held prerequisite blocker is handled as in
Step 6: shape it now, or report the hold on this outcome.

## Independent review, once per content

Create or reuse one independent review record for the package anchor/PR. Only anchor
gets `external_ref=gh-pr:N`; members point to anchor, never duplicate unique refs.
The original→review dependency is only on anchor. A reviewer gets its own
`review/<review-id>` worktree at the published head and sends findings directly here.
An early verifier can be that same independent reviewer, not an additional compulsory
review layer. Same-content findings and evidence remain in the canonical record.

Keep implementation claims and worktree throughout review/CI. On corrections,
remove anchor→review dependency for correction work, leave review blocked and
resume this same session with existing PR/review IDs. Hard member gates remain.
Correct and push the same branch; restore the dependency and send changed head/content
and relevant test evidence to the same reviewer. No release to the global ready queue,
new correction bead, new owner or worktree transfer. A new trust/architecture/scope
choice still goes through its actual authority gate.

## Land and reconcile

Use the repository's review/check/merge queue policy and current owner-hold guard.
An independent reviewer may perform authorized merge; this owner confirms terminal
GitHub state and actual delivery base. Queue/CI waits retain owner affinity; don't
launch unbounded unrelated work. Disjoint bounded spillover is permitted when the
shared queue admits it with execution/WIP capacity and higher-class work drained;
keep the original resume event and checkpoint when its review/correction returns.

Park the anchor as blocked when implementation hands off or waits; this releases
execution capacity without releasing claims, worktree or session. An owner-gate
park (gate label or `held` receipt) also releases WIP; a technical wait keeps it. Resume in_progress
only after queue admission. Independent review persists one typed review_receipt
on its canonical review bead; same-head ready/held/queued verdicts are waits or
fresh merge actions, not another source-review job. Corrections keep their existing
owner and get admission priority before ordinary new packages.

Use structured member results and the helper's `closure-plan`, verify evidence and
landing then close only met members. A foundation merge leaves the original accepted
feature open. Unmet runtime/activation proof remains explicit in place. Review record
completion is distinct from every member being done.

If an optional tail blocks useful accepted source, publish and land a safe subset
through real gates; keep incomplete member/outcome state. Do not silently shrink
accepted scope or build another reconciliation hierarchy. On an external/hard gate,
name dependent scope and resume event, continue unaffected scope, then wait.

## Recovery or next package

Useful published branches remain recoverable; dirty/unpushed work is preserved or
quarantined, never removed merely because a PR opened. Follow helper journal recovery
for interrupted multi-claim attempts and guarded dead-owner adoption for session loss.
Clean worktrees/branches only after completed or explicit published handoff proof.
Retain this session for the next related package. On preemption/drift/stop publish
actor/session/packet/claim/review state and release or retain claims explicitly.
