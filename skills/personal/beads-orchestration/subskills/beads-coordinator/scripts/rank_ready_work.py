#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Rank ready Beads work in finish-first order for one or more coordinators.

Report-only: emits one compact `beads-work-queue/v2` JSON envelope and never
mutates Beads. `queue` is the capacity-bounded admission plan: P0 emergency,
review, retained-owner correction/resume, then new implementation. Step-0
normalization via `--review-state-file` is required for admissions; without
it only diagnostic `candidates` are available. A supplied but incomplete,
invalid or stale normalization degrades the queue to unclaimed implementation
only, with the cause in `admission.reason`: review, correction and resume
actions stay fail-closed, but one bad PR record does not idle every lane. The coordinator still applies the dispatch-readiness and
cohesion gates and claims atomically; losing a claim race requires refreshing
the queue. Every coordinator reading the same tracker gets the same order, so
parallel coordinators converge on the same epics instead of each opening its
own.

Usage (from the beads-coordinator package directory):

    uv run scripts/rank_ready_work.py --repo-root "$REPO_ROOT" \
        --coordinator "$COORDINATOR_ID" [--lanes N] [--scope-epic ID ...]

Ranking contract (canonical; `coordinator-loop.md` Step 2 only points here):

1. Urgent first: a runnable priority-0 bead, a ready bead under a priority-0
   ancestor, or the runnable blocker frontier of any live priority-0 task or
   outcome. Urgency ignores `--scope-epic`; held, unknown, or foreign-owned
   prerequisites never become runnable merely by inheritance.
2. Then work grouped by outcome. An outcome is a bead's nearest open ancestor
   epic, or the bead itself when it has none (a standalone bead is a one-bead
   outcome). Outcomes sort by:
   a. `focus` label on the epic or any ancestor epic (operator pin) first;
   b. effective priority, lower first. An epic holding a lane (a bead
      `in_progress`) counts one level more urgent than its priority, so it
      keeps its lanes against idle epics up to one level above it, but not
      against bigger gaps;
   c. epics already moving (a lane, a closed child, or a bead blocked on
      review with `gh-pr:<N>`) before untouched ones;
   d. fewest remaining beads (`total_children - closed_children`), so at
      equal effective priority the epic nearest completion wins even over
      one that holds a lane (Step 2's lane-epic affinity keeps a lane on its
      own epic);
   e. oldest creation, then id.
3. Within an outcome: prerequisites first (ready beads outside the epic that
   form the runnable frontier of its transitive blocker chain; they count as in scope when the epic
   is), then priority, then most dependents, then oldest.
4. Each epic puts at most `--max-lanes-per-epic` beads, minus the lanes it
   already holds, in the primary queue; a bead another outcome already queued
   does not use up that share. Its other ready beads append after every
   outcome's primary share, so no lane idles while ready work exists.

Capacity: the lane budget is global across coordinators: `--lanes`, else the
`beads.max_lanes` kv key, else 4. An in-progress package anchor or unpackaged
bead, including a reviewer, occupies one execution slot. Blocked/open owners
retain claims and outcome WIP but park execution; member in-progress flags
never override a parked anchor. A closed anchor conservatively falls back to
its pending members. Live registrations' `extra_lanes` reserve execution.
New outcomes also require free owned-WIP capacity under the same budget;
reviews and corrections reuse existing WIP. Owned WIP counts coordinator
claims agents are building: a blocked anchor parked on an owner gate (a label
in `OWNER_GATE_LABELS`, or a same-head `held` canonical review receipt) and any
claim whose coordinator has no live registration keep their claims but leave
`owned`, listed under `capacity.owned_excluded` (`owner_gate`, `dead_owner`;
additive to v2). Lanes keep working while the human acts; dead claims are
adoptable, not held. Technical waits (CI, review, merge queue) still count.
A blocked item parks itself: anything this coordinator cannot run now (a
peer's or dead owner's review, correction, resume or missing review wiring,
an inconsistent review record) is listed in `admission.waiting` with its
reason and owner, and never suppresses unrelated runnable work. Tier order
and capacity alone decide what is admitted.
One emergency or runnable prerequisite of retained parked work may overflow
owned WIP by one when execution is free. At budget+1 no further overflow is
admitted, even if that extra package parks. Execution never exceeds budget.
Other `in_progress` assignees (humans) are counted under `other_claims` only.
In-progress claims whose assignee has no live registration are listed under
`unowned_claims` and reserve execution only until abandoned: once the newest
of the bead heartbeats and the owner's registration expiry (the bead's last
update when it never registered) is past the stall threshold, the claim stops
holding a lane and is listed in `recoverable_claims` for guarded adoption. A
missing heartbeat is not a veto; a malformed one is.

Excluded from diagnostic candidates, with counts or ids in `excluded`:
- epics themselves; claiming a programme does not hide its runnable
  prerequisites or authorize parallel implementation of the same substrate;
- `pr-review`, `pr-review-task`, and legacy `review-correction` beads (their
  review/correction phase comes from normalized PR state and the receipt);
- `needs-shaping` beads (listed per epic, standalone ones by priority);
- `no-dispatch` beads (tracking or meta beads no lane works; counted) and
  `scope:local-state` beads (work on a checkout's gitignored state, which no
  worktree reaches; listed for the owner);
- beads with an assignee. Exceptions: an open bead assigned to `--coordinator`
  is queued (a release that kept the assignee), and an open bead whose
  `coordinator:` assignee has no live registration is listed as `stranded`
  for guarded recovery.

Blocked beads whose `coordinator:` assignee (any format, including
pre-registry `coordinator:<uuid>` ids) has no live registration are listed as
`orphaned_blocked`, each with `kind`: `pr` (it carries `gh-pr:<N>` or a review
label, so the review lane adopts it) or `gate` (blocked by status alone).

Also reported: `held` epics (progress or a pin, no runnable work, with what
they wait on), `childless_epics` for decomposition, and `closable_epics`
(every child closed; a candidate hint, never acceptance or activation proof).

Fail-closed: a failed `bd` command or unexpected output yields `status: fatal`
and no queue. bd older than 1.3 rejects `--brief` and fails this way. The
coordinator then dispatches nothing that wake (`coordinator-loop.md` Step 1).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(PACKAGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SCRIPTS))
from delivery_package import ID as DELIVERY_ID, metadata as bead_metadata, package_index

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordinator_registry import claim_abandoned, lane_budget, parse_time, registrations  # noqa: E402
from review_relation_graph import normalized_finding_relation_error

SCHEMA = "beads-work-queue/v2"
COORDINATOR_PREFIX = "coordinator:"
FOCUS_LABEL = "focus"
SHAPING_LABEL = "needs-shaping"
# `review-correction` marks a looper PR original returned for corrections: only an
# implement looper may take it, in correction mode on the existing PR.
REVIEW_LABELS = frozenset({"pr-review", "pr-review-task", "review-correction"})
# Beads no lane takes: tracking/meta beads, and work on gitignored local state
# that only exists in one checkout (the owner does it, or names a lane for it).
NO_DISPATCH_LABEL = "no-dispatch"
LOCAL_STATE_LABEL = "scope:local-state"
DEFAULT_MAX_LANES_PER_EPIC = 2
DEFAULT_LIMIT = 10
LIST_LIMIT = 10
TITLE_LIMIT = 80


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def fatal(now: str | None, code: str, scope: str) -> dict[str, Any]:
    return {
        "errors": [{"code": code, "scope": scope}],
        "generated_at": now,
        "queue": [],
        "candidates": [],
        "schema": SCHEMA,
        "status": "fatal",
    }


def bd_json(argv: list[str], repo_root: Path) -> tuple[object | None, str | None]:
    try:
        result = subprocess.run(
            ["bd", "-C", str(repo_root), *argv],
            capture_output=True,
            cwd=repo_root,
            text=True,
            check=False,
        )
    except OSError:
        return None, "command-failed"
    if result.returncode != 0:
        return None, "command-failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "invalid-json"


def collect(repo_root: Path, *, with_dependencies: bool = False) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Collect live inputs plus exact missing direct prerequisites for admission.

    Recovered explicit-PR sources can be unassigned. Their canonical review
    still evaluates source/member blockers, so collect those rows regardless
    of claim presence. Validated packages extend that scope to their members;
    held/live/unknown targets remain subject to the unchanged hard gate.
    """
    queries = {
        "live": ["list", "--status=open,blocked,in_progress", "--json", "--limit", "0"],
        "ready": ["ready", "--json", "--brief", "--limit", "0", "--exclude-type", "epic"],
        "epics": ["epic", "status", "--json"],
        "kv": ["kv", "list", "--json"],
    }
    snapshot: dict[str, Any] = {}
    for name, argv in queries.items():
        payload, error = bd_json(argv, repo_root)
        if error:
            return None, {"code": error, "scope": name}
        snapshot[name] = payload
    # Closed members and anchors remain traceability evidence for a partially
    # delivered package. Fetch only missing identities, never the closed archive.
    if isinstance(snapshot.get("live"), list):
        known = {row.get("id") for row in snapshot["live"] if isinstance(row, dict)}
        needed = set()
        for row in snapshot["live"]:
            if not isinstance(row, dict):
                continue
            try:
                metadata = bead_metadata(row)
            except ValueError:
                return None, {"code": "invalid-package-metadata", "scope": "packages"}
            anchor = metadata.get("delivery_anchor")
            if isinstance(anchor, str):
                needed.add(anchor)
            delivery = metadata.get("delivery")
            if isinstance(delivery, dict) and isinstance(delivery.get("members"), list):
                needed.update(item for item in delivery["members"] if isinstance(item, str))
        snapshot["package_records"] = []
        for member_id in sorted(needed - known):
            if not DELIVERY_ID.fullmatch(member_id):
                return None, {"code": "invalid-package-record", "scope": "packages"}
            records, error = bd_json(["show", member_id, "--json"], repo_root)
            if error or not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict) or records[0].get("id") != member_id:
                return None, {"code": error or "invalid-package-record", "scope": "packages"}
            snapshot["package_records"].extend(records)
    if with_dependencies and isinstance(snapshot.get("live"), list):
        records = [*snapshot["live"], *snapshot.get("package_records", [])]
        try:
            delivery_anchors = package_index(records)
        except ValueError:
            return None, {"code": "invalid-delivery-package", "scope": "packages"}
        known = {row["id"] for row in records}
        dependency_anchors = {
            delivery_anchors.get(row["id"], row["id"])
            for row in records
            if text(row.get("assignee")) or "pr-review-task" in labels_of(row)
            or re.fullmatch(r"gh-pr:[1-9][0-9]*", text(row.get("external_ref")))
        }
        needed = {
            target for row in records
            if row.get("status") != "closed"
            and delivery_anchors.get(row["id"], row["id"]) in dependency_anchors
            for target in blockers_of(row) if target not in known
        }
        snapshot["dependency_records"] = []
        for target in sorted(needed):
            if not DELIVERY_ID.fullmatch(target):
                return None, {"code": "invalid-dependency", "scope": "admission"}
            records, error = bd_json(["show", target, "--json"], repo_root)
            if error or not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict) or records[0].get("id") != target:
                return None, {"code": error or "invalid-dependency", "scope": "admission"}
            snapshot["dependency_records"].extend(records)
    return snapshot, None


def priority_of(row: dict[str, Any]) -> int:
    value = row.get("priority")
    return value if isinstance(value, int) and not isinstance(value, bool) else 4


def labels_of(row: dict[str, Any]) -> set[str]:
    labels = row.get("labels")
    return {label for label in labels if isinstance(label, str)} if isinstance(labels, list) else set()


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def blockers_of(row: dict[str, Any]) -> list[str]:
    deps = row.get("dependencies")
    if not isinstance(deps, list):
        return []
    return [
        dep["depends_on_id"]
        for dep in deps
        if isinstance(dep, dict)
        and dep.get("type") == "blocks"
        and dep.get("issue_id") == row.get("id")
        and isinstance(dep.get("depends_on_id"), str)
    ]


def validate(snapshot: dict[str, Any]) -> dict[str, str] | None:
    for name, kind in (("live", list), ("ready", list), ("epics", list), ("kv", dict)):
        if not isinstance(snapshot.get(name), kind):
            return {"code": "invalid-json", "scope": name}
    for name in ("live", "ready"):
        if not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in snapshot[name]):
            return {"code": "invalid-record", "scope": name}
    for entry in snapshot["epics"]:
        if not (isinstance(entry, dict) and isinstance(entry.get("epic"), dict) and isinstance(entry["epic"].get("id"), str)):
            return {"code": "invalid-record", "scope": "epics"}
    return None



HOLD_LABELS = frozenset({"no-dispatch", "needs-shaping", "scope:local-state", "owner-held", "human-gate", "held-for-owner"})
# A blocked claim carrying one of these waits on an action only the human can
# take, so it releases owned WIP while keeping its claims and worktree. `human`
# is the label `bd human` lists; the rest are this package's and trackers' gates.
OWNER_GATE_LABELS = frozenset({
    "human", "needs-human", "human-gate", "owner-held", "held-for-owner", "owner-session",
    "owner-signoff-pending", "physical-acceptance-pending", "live-acceptance-pending",
})
SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def package_running(anchor, rows, delivery_anchors):
    """The anchor controls execution; pending members backstop a closed anchor."""
    principal = rows[anchor]
    if principal.get("status") == "closed":
        return any(row.get("status") == "in_progress" for member, row in rows.items()
                   if delivery_anchors.get(member) == anchor and row.get("status") != "closed")
    return principal.get("status") == "in_progress"


def held_review_anchors(rows, review_state):
    """Anchors whose canonical review receipt is `held` at the reviewed head.

    Reads only the Step-0 findings already supplied; invalid or partial state
    yields nothing, so it can never release WIP on its own.
    """
    findings = review_state.get("findings") if isinstance(review_state, dict) else None
    held = set()
    for finding in findings if isinstance(findings, list) else []:
        if not (isinstance(finding, dict) and finding.get("kind") == "review-task"
                and not finding.get("duplicate_of") and finding.get("pr_state") == "OPEN"
                and finding.get("canonical_review_id") == finding.get("review_id")
                and isinstance(finding.get("head_sha"), str)):
            continue
        try:
            receipt = bead_metadata(rows.get(finding["review_id"], {})).get("review_receipt")
        except (TypeError, ValueError):
            continue
        if (isinstance(receipt, dict) and receipt.get("disposition") == "held"
                and receipt.get("head") == finding["head_sha"]):
            held.add(finding.get("original_id"))
    return held


def execution_capacity(rows, delivery_anchors, regs, budget, held_anchors=frozenset(), *, now):
    """Claims survive waits; only running anchors/reviewers consume execution.

    Owned WIP bounds outcomes agents are building. A blocked owner-gated park
    (gate label or held review receipt) and any claim of a dead coordinator are
    not being built, so they keep their claims but leave `owned`; both are
    reported under `owned_excluded`. A dead in-progress claim reserves
    execution only until it is abandoned (`claim_abandoned`); it is then
    returned as recoverable and stops holding a lane.
    """
    groups = {}
    for row in rows.values():
        if row.get("status") == "closed" or not text(row.get("assignee")):
            continue
        anchor = delivery_anchors.get(row["id"], row["id"])
        groups.setdefault((text(row.get("assignee")), anchor), []).append(row)
    active, owned, other, unowned, recoverable = {}, set(), 0, [], []
    excluded = {"owner_gate": set(), "dead_owner": set()}
    for (owner, anchor), members in groups.items():
        principal = rows.get(anchor, members[0])
        review = "pr-review-task" in labels_of(principal)
        running = package_running(anchor, rows, delivery_anchors)
        if not owner.startswith(COORDINATOR_PREFIX):
            other += 1
            continue
        if not review and principal.get("issue_type") != "epic":
            dead = not regs.get(owner, {}).get("live")
            gated = principal.get("status") == "blocked" and (
                anchor in held_anchors
                or any(labels_of(m) & OWNER_GATE_LABELS for m in members if m.get("status") != "closed"))
            if dead:
                excluded["dead_owner"].add(anchor)
            if gated:
                excluded["owner_gate"].add(anchor)
            if not (dead or gated):
                owned.add((owner, anchor))
        abandoned = False
        if not regs.get(owner, {}).get("live") and any(m.get("status") == "in_progress" for m in members):
            unowned.append(anchor)
            abandoned = claim_abandoned(regs, owner, members, now)
            if abandoned:
                recoverable.append({"assignee": owner, "id": anchor,
                                    "kind": "review" if review else "implementation",
                                    "pr": text(principal.get("external_ref")) or None})
        if running and not abandoned:
            active[owner] = active.get(owner, 0) + 1
    for owner, entry in regs.items():
        if entry["live"] and entry["extra_lanes"]:
            active[owner] = active.get(owner, 0) + entry["extra_lanes"]
    occupied = sum(active.values())
    return {"by_assignee": dict(sorted(active.items())), "occupied": occupied,
            "free": max(budget - occupied, 0), "owned": len(owned),
            "free_wip": max(budget - len(owned), 0), "other_claims": other,
            "owned_excluded": {key: sorted(ids)[:LIST_LIMIT] for key, ids in excluded.items()}}, unowned, recoverable


def admission_queue(candidates, rows, delivery_anchors, regs, capacity, review_state, *, coordinator, now, limit, ready_ids, budget):
    """Project one global priority queue; nothing here authorizes a mutation."""
    def blocked(reason):
        return {"status": "blocked", "reason": reason, "waiting": [], "queue": []}
    if not isinstance(review_state, dict) or review_state.get("schema") != "beads-pr-review-normalization/v1":
        return blocked("review-state-required")
    # Unusable review evidence fails closed for review, correction and resume
    # only. Unclaimed ready work needs none of it, so the queue degrades to
    # implementation-only rather than idling every lane on one bad record.
    def review_state_problem():
        if review_state.get("status") not in {"success", "empty"} or review_state.get("errors") != []:
            return "review-state-incomplete"
        findings, healing = review_state.get("findings"), review_state.get("self_heal_candidates")
        if not isinstance(findings, list) or not isinstance(healing, list) or any(not isinstance(f, dict) for f in findings + healing):
            return "invalid-review-state"
        for finding in findings:
            if (finding.get("kind") not in {"original", "review-task"}
                    or not isinstance(finding.get("original_id"), str)
                    or not DELIVERY_ID.fullmatch(finding["original_id"])
                    or finding.get("context_status") != "resolved"
                    or finding.get("pr_state") not in {"OPEN", "MERGED", "CLOSED"}
                    or not isinstance(finding.get("pr_number"), int)
                    or isinstance(finding["pr_number"], bool) or finding["pr_number"] <= 0):
                return "invalid-review-state"
        generated = parse_time(review_state.get("generated_at"))
        if generated is None or generated > now:
            return "invalid-review-state-time"
        if normalized_finding_relation_error(findings):
            return "invalid-review-relations"
        return None

    visible_reviews = {r["id"] for r in rows.values()
                       if r.get("status") != "closed" and "pr-review-task" in labels_of(r)}
    degraded = review_state_problem()
    findings, healing = ([], []) if degraded else (review_state["findings"], review_state["self_heal_candidates"])
    if not degraded:
        seen_reviews = {f.get("review_id") for f in findings if f.get("kind") == "review-task"}
        visible_originals = {r["id"] for r in rows.values() if r.get("status") == "blocked"
                             and "pr-review" in labels_of(r) and "pr-review-task" not in labels_of(r)
                             and text(r.get("external_ref")).startswith("gh-pr:")}
        seen_originals = {f.get("original_id") for f in findings}
        if not visible_reviews.issubset(seen_reviews) or not visible_originals.issubset(seen_originals):
            degraded, findings, healing = "review-inventory-changed", [], []
    # A blocked item parks itself in `waiting`; it never suppresses unrelated
    # runnable work. Tier order and capacity alone rank what is admitted.
    waiting, actions = [], []
    active_finishing = any(r.get("status") == "in_progress" and
                           ("pr-review-task" in labels_of(r) or "review-correction" in labels_of(r))
                           for r in rows.values())
    seen = set()

    # Only runnable blockers of retained parked work earn the WIP escape hatch.
    # Unknown, held, and already owned implementation nodes never convey it.
    # Only this anchor's validated canonical waiting review may convey its own
    # external blockers; reading that edge never transfers a reviewer claim.
    canonical_reviews = {finding["original_id"]: finding["review_id"] for finding in findings
                         if finding.get("kind") == "review-task" and not finding.get("duplicate_of")
                         and finding.get("canonical_review_id") == finding.get("review_id")
                         and finding.get("review_id") in visible_reviews and finding.get("pr_state") == "OPEN"
                         and isinstance(finding.get("head_sha"), str) and SHA_RE.fullmatch(finding["head_sha"])
                         and text(rows.get(finding["original_id"], {}).get("external_ref")) == f"gh-pr:{finding['pr_number']}"}
    parked = {delivery_anchors.get(row["id"], row["id"]) for row in rows.values()
              if row.get("status") != "closed" and row.get("issue_type") != "epic"
              and text(row.get("assignee")).startswith(COORDINATOR_PREFIX)
              and "pr-review-task" not in labels_of(row)
              and regs.get(row.get("assignee"), {}).get("live")
              and not package_running(delivery_anchors.get(row["id"], row["id"]), rows, delivery_anchors)}
    owned_frontier = {}
    for anchor in sorted(parked):
        members = {member for member, package in delivery_anchors.items() if package == anchor} or {anchor}
        if any(labels_of(rows[member]) & HOLD_LABELS for member in members if rows[member].get("status") != "closed"):
            continue
        pending = [target for member in members if not labels_of(rows[member]) & HOLD_LABELS
                   for target in blockers_of(rows[member]) if target not in members]
        visited = set()
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            row = rows.get(target)
            if row is None or row.get("status") == "closed" or labels_of(row) & HOLD_LABELS:
                continue
            if target == canonical_reviews.get(anchor) and row.get("status") in {"blocked", "open"}:
                pending.extend(blockers_of(row))
                continue
            if text(row.get("assignee")) or labels_of(row) & REVIEW_LABELS:
                continue
            if target in ready_ids:
                owned_frontier.setdefault(target, anchor)
            else:
                pending.extend(blockers_of(row))

    def hard_gate(anchor, review_id=None):
        members = [member for member, package in delivery_anchors.items() if package == anchor] or [anchor]
        owner = text(rows.get(anchor, {}).get("assignee"))
        for member in members:
            record = rows.get(member)
            if record is None:
                return "missing-member"
            if record.get("status") == "closed":
                continue
            if labels_of(record) & HOLD_LABELS:
                return "member-held"
            if text(record.get("assignee")) != owner:
                return "member-owner-mismatch"
            for target in blockers_of(record):
                if target in members or target == review_id:
                    continue
                if target not in rows and member in ready_ids:
                    continue
                if target not in rows or rows[target].get("status") != "closed":
                    return "external-blocker"
        return None

    def append_action(kind, anchor, review_id=None, candidate=None):
        record = rows.get(anchor)
        if record is None:
            waiting.append({"id": anchor, "reason": "anchor-unavailable"})
            return
        owner = text(record.get("assignee"))
        if kind == "review" and package_running(anchor, rows, delivery_anchors):
            waiting.append({"id": anchor, "reason": "implementation-running"})
            return
        gate = hard_gate(anchor, review_id)
        if gate:
            waiting.append({"id": anchor, "reason": gate})
            return
        if kind in {"correction", "resume"} and package_running(anchor, rows, delivery_anchors):
            return
        if kind in {"correction", "resume"} and (not owner or owner != coordinator):
            waiting.append({"id": anchor, "reason": "resume-existing-owner", "owner": owner})
            return
        if owner.startswith(COORDINATOR_PREFIX) and not regs.get(owner, {}).get("live"):
            waiting.append({"id": anchor, "reason": "owner-recovery-required", "owner": owner})
            return
        review = rows.get(review_id, {}) if review_id else {}
        if kind == "review":
            if labels_of(review) & HOLD_LABELS:
                waiting.append({"id": review_id, "reason": "review-held"})
                return
            if any(target not in rows or rows[target].get("status") != "closed" for target in blockers_of(review)):
                waiting.append({"id": review_id, "reason": "review-external-blocker"})
                return
        if kind == "review" and text(review.get("assignee")) not in {"", coordinator}:
            waiting.append({"id": review_id, "reason": "review-owned-by-peer", "owner": review.get("assignee")})
            return
        if kind == "review" and review.get("status") == "in_progress":
            return
        if anchor in seen:
            return
        seen.add(anchor)
        action = {"id": review_id if kind == "review" else anchor, "kind": kind,
                  "anchor": anchor, "owner": owner, "review_id": review_id,
                  "review_owner": text(review.get("assignee")) if review_id else None,
                  "priority": priority_of(record), "outcome": candidate.get("outcome") if candidate else None,
                  "reason": candidate.get("reason") if candidate else kind + " retained delivery",
                  "session_policy": "independent-reviewer" if kind == "review" else "resume-existing" if kind in {"correction", "resume"} else "allocate-owner"}
        action["_tier"] = 0 if action["priority"] == 0 or (candidate and candidate["reason"].startswith("urgent:")) else {"review": 1, "correction": 2, "resume": 3, "implementation": 5}[kind]
        if kind == "implementation" and anchor in owned_frontier and action["_tier"] != 0:
            action["_tier"] = 4
        action["_order"] = len(actions)
        actions.append(action)

    review_anchors = {finding.get("original_id") for finding in findings}
    for finding in findings:
        if finding.get("kind") != "review-task" or finding.get("duplicate_of"):
            continue
        review_id, anchor = finding.get("review_id"), finding.get("original_id")
        if review_id not in visible_reviews:
            # A terminal record already reconciled out of the live inventory.
            if finding.get("pr_state") not in {"MERGED", "CLOSED"}:
                waiting.append({"id": review_id, "reason": "review-inventory-changed"})
            continue
        # An inconsistent review parks that review alone; its anchor stays
        # out of implementation through `review_anchors`.
        if finding.get("canonical_review_id") != review_id or finding.get("context_status") != "resolved":
            waiting.append({"id": review_id, "reason": "unresolved-review-state"})
            continue
        if not isinstance(anchor, str) or anchor not in rows:
            waiting.append({"id": review_id, "reason": "review-anchor-unavailable"})
            continue
        if finding.get("pr_state") != "OPEN":
            waiting.append({"id": review_id, "reason": "reconcile-terminal-pr"})
            continue
        if rows[review_id].get("status") == "in_progress":
            active_finishing = True
            continue
        if text(rows[anchor].get("external_ref")) != f"gh-pr:{finding.get('pr_number')}":
            waiting.append({"id": review_id, "reason": "review-association-changed"})
            continue
        head = finding.get("head_sha")
        if not isinstance(head, str) or not SHA_RE.fullmatch(head):
            waiting.append({"id": review_id, "reason": "review-head-unavailable"})
            continue
        try:
            receipt = bead_metadata(rows[review_id]).get("review_receipt")
        except ValueError:
            receipt = False
        if receipt is not None and (not isinstance(receipt, dict) or
                not isinstance(receipt.get("head"), str) or not SHA_RE.fullmatch(receipt["head"]) or
                receipt.get("disposition") not in {"ready", "corrections", "queued", "held"}):
            waiting.append({"id": review_id, "reason": "invalid-review-receipt"})
            continue
        if receipt and receipt["head"] == head:
            if receipt["disposition"] == "corrections":
                append_action("correction", anchor, review_id)
            else:
                waiting.append({"id": review_id, "reason": "same-head-" + receipt["disposition"]})
            continue
        if finding.get("recommendation") == "wait-for-cooldown":
            waiting.append({"id": review_id, "reason": "review-cooldown"})
            continue
        if finding.get("recommendation") != "dispatch-canonical-review":
            waiting.append({"id": review_id, "reason": "review-reconciliation-required"})
            continue
        append_action("review", anchor, review_id)
    # Missing review wiring is repaired by the anchor's holder (or its adopter
    # once the holder is dead). It waits by anchor and gates nothing else.
    unwired = {finding["original_id"] for finding in findings
               if finding.get("kind") == "original" and finding.get("original_id") in rows
               and not finding.get("canonical_review_id")}
    unwired |= {event.get("original_id") for event in healing
                if event.get("recommendation") != "review-wiring-current"}
    for anchor in sorted(unwired, key=text):
        waiting.append({"id": anchor, "reason": "review-wiring-required",
                        "owner": text(rows.get(anchor, {}).get("assignee")) or None})
    for candidate in candidates:
        record = rows[candidate["id"]]
        if record["id"] in review_anchors:
            continue
        owner = text(record.get("assignee"))
        if owner:
            append_action("resume", record["id"], candidate=candidate)
        else:
            append_action("implementation", record["id"], candidate=candidate)
    # Active packages are excluded from diagnostic ready candidates. A parked,
    # open anchor with clear gates may resume without another WIP admission.
    resume_anchors = set(delivery_anchors.values()) | {
        row["id"] for row in rows.values() if text(row.get("assignee"))
        and row["id"] in ready_ids and not labels_of(row) & REVIEW_LABELS}
    for anchor in sorted(resume_anchors):
        if anchor not in review_anchors and rows[anchor].get("status") == "open":
            append_action("resume", anchor)
    if degraded:
        waiting.append({"id": None, "reason": degraded})
        actions = [action for action in actions if action["kind"] == "implementation"
                   and not text(rows[action["id"]].get("external_ref")).startswith("gh-pr:")]
    actions.sort(key=lambda a: (a["_tier"], a["_order"], a["id"]))
    free, wip, owned = capacity["free"], capacity["free_wip"], capacity["owned"]
    selected = []
    for action in actions:
        if len(selected) >= min(free, max(limit, 0)):
            break
        if action["kind"] == "implementation":
            if wip <= 0:
                exception = "emergency" if action["_tier"] == 0 else "prerequisite" if action["id"] in owned_frontier else None
                if not exception or not parked or owned >= budget + 1:
                    waiting.append({"id": action["id"], "reason": "owned-wip-budget-full"})
                    continue
                action["wip_exception"] = exception
            else:
                wip -= 1
            owned += 1
        selected.append({key: value for key, value in action.items() if not key.startswith("_")})
    status = "ready" if selected else "busy" if actions or active_finishing else "waiting" if waiting else "empty"
    if degraded and not selected:
        status = "blocked"
    return {"status": status, "reason": degraded, "waiting": waiting, "queue": selected}

def rank(
    snapshot: dict[str, Any],
    *,
    now: datetime,
    coordinator: str | None = None,
    lanes: int | None = None,
    max_lanes_per_epic: int = DEFAULT_MAX_LANES_PER_EPIC,
    scope_epics: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    review_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = format_time(now)
    problem = validate(snapshot)
    if problem:
        return fatal(generated_at, problem["code"], problem["scope"])

    rows = {row["id"]: row for row in [*snapshot["live"], *snapshot.get("package_records", []), *snapshot.get("dependency_records", [])]}
    try:
        delivery_anchors = package_index(list(rows.values()))
    except ValueError:
        return fatal(generated_at, "invalid-delivery-package", "packages")
    ready_rows = [row for row in snapshot["ready"] if row.get("issue_type") != "epic"]
    regs = registrations(snapshot["kv"], now)
    epics: dict[str, dict[str, Any]] = {}
    for entry in snapshot["epics"]:
        epic = entry["epic"]
        if epic.get("status") == "closed":
            continue
        total = entry.get("total_children") if isinstance(entry.get("total_children"), int) else 0
        closed = entry.get("closed_children") if isinstance(entry.get("closed_children"), int) else 0
        epics[epic["id"]] = {
            "row": rows.get(epic["id"], epic),
            "total": total,
            "closed": closed,
            "lanes": 0,
            "in_review": 0,
            "items": [],
            "prereqs": [],
            "needs_shaping": [],
            "blocked_open": [],
        }

    def ancestors(bead_id: str) -> list[str]:
        chain: list[str] = []
        current = rows.get(bead_id, {}).get("parent")
        while isinstance(current, str) and current not in chain:
            chain.append(current)
            current = rows.get(current, {}).get("parent")
        return chain

    def outcome_of(bead_id: str) -> str | None:
        anchor_id = delivery_anchors.get(bead_id)
        if anchor_id:
            outcome = bead_metadata(rows[anchor_id])["delivery"]["outcome"]
            if outcome in epics:
                return outcome
        return next((ancestor for ancestor in ancestors(bead_id) if ancestor in epics), None)

    def urgent_sponsor(row: dict[str, Any]) -> str | None:
        # A P0 task or ancestor owns the emergency even when its nearest epic
        # has ordinary priority or the task has no epic at all.
        candidates = [row["id"], *ancestors(row["id"])]
        outcome = outcome_of(row["id"])
        if outcome:
            candidates.append(outcome)
        return next((candidate for candidate in candidates
                     if candidate in rows and priority_of(rows[candidate]) == 0), None)

    def pinned(epic_id: str) -> bool:
        return any(
            FOCUS_LABEL in labels_of(epics[eid]["row"]) for eid in [epic_id, *ancestors(epic_id)] if eid in epics
        )

    scope = set(scope_epics or [])

    def in_scope(bead_id: str) -> bool:
        return not scope or bool(({bead_id} | set(ancestors(bead_id))) & scope)

    def is_live_coordinator(assignee: str) -> bool:
        return bool(regs.get(assignee, {}).get("live"))

    # Owned work remains visible while blocked/open anchors lend their execution
    # slot to independent review. Never infer session retirement from parking.
    budget, budget_source = lane_budget(snapshot["kv"], lanes)
    capacity, unowned, recoverable = execution_capacity(rows, delivery_anchors, regs, budget,
                                                        held_review_anchors(rows, review_state), now=now)
    by_assignee = capacity["by_assignee"]
    orphaned = []
    counted_epics = set()
    for row in snapshot["live"]:
        bead_id, assignee = row["id"], text(row.get("assignee"))
        epic_id = outcome_of(bead_id)
        anchor = delivery_anchors.get(bead_id, bead_id)
        if row.get("status") == "blocked" and assignee.startswith(COORDINATOR_PREFIX) and not is_live_coordinator(assignee):
            on_pr = text(row.get("external_ref")).startswith("gh-pr:") or bool(labels_of(row) & REVIEW_LABELS)
            orphaned.append({"assignee": assignee, "id": bead_id, "kind": "pr" if on_pr else "gate"})
        if epic_id and assignee and row.get("issue_type") != "epic" and (assignee, anchor) not in counted_epics:
            counted_epics.add((assignee, anchor))
            epics[epic_id]["lanes"] += 1
        if row.get("status") == "blocked" and text(row.get("external_ref")).startswith("gh-pr:") and epic_id:
            epics[epic_id]["in_review"] += 1

    # Sort ready beads into urgent, epic-owned, and standalone outcomes.
    ready_ids = {row["id"] for row in ready_rows}
    urgent: list[dict[str, Any]] = []
    standalone: list[dict[str, Any]] = []
    shaping_standalone: list[dict[str, Any]] = []
    stranded: list[str] = []
    local_state: list[dict[str, Any]] = []
    excluded = {"claimed": 0, "no_dispatch": 0, "review": 0}
    runnable: list[dict[str, Any]] = []
    for row in ready_rows:
        assignee = text(row.get("assignee"))
        if row["id"] in delivery_anchors:
            # An active owner resumes the package; member readiness is not a
            # second implementation allocation, even for its current actor.
            excluded["claimed"] += 1
            continue
        if assignee and not (coordinator and assignee == coordinator):
            if assignee.startswith(COORDINATOR_PREFIX) and not is_live_coordinator(assignee):
                stranded.append(row["id"])
            else:
                excluded["claimed"] += 1
            continue
        if labels_of(row) & REVIEW_LABELS:
            excluded["review"] += 1
            continue
        if NO_DISPATCH_LABEL in labels_of(row):
            excluded["no_dispatch"] += 1
            continue
        if LOCAL_STATE_LABEL in labels_of(row):
            local_state.append(row)
            continue
        runnable.append(row)
    runnable_ids = {row["id"] for row in runnable}

    for row in runnable:
        epic_id = outcome_of(row["id"])
        if SHAPING_LABEL in labels_of(row):
            if epic_id:
                epics[epic_id]["needs_shaping"].append(row["id"])
            elif in_scope(row["id"]):
                shaping_standalone.append(row)
            continue
        if urgent_sponsor(row):
            urgent.append(row)
        elif not in_scope(row["id"]):
            continue
        elif epic_id:
            epics[epic_id]["items"].append(row)
        else:
            standalone.append(row)

    # Follow prerequisite chains to their runnable frontier. A shared runtime
    # substrate keeps one queue identity even when it unlocks several epics.
    runnable_by_id = {row["id"]: row for row in runnable}
    urgent_ids = {row["id"] for row in urgent}
    urgent_prerequisites: dict[str, str] = {}
    def runnable_prerequisites(bead_id: str, seen: set[str]) -> list[dict[str, Any]]:
        if bead_id in seen:
            return []
        seen.add(bead_id)
        dependency = rows.get(bead_id)
        if dependency is None or dependency.get("status") == "closed":
            return []
        if bead_id in runnable_by_id:
            return [runnable_by_id[bead_id]]
        # A claimed or owner-held prerequisite belongs to that owner; never
        # dispatch through it or turn its hold into permission for dependents.
        if text(dependency.get("assignee")) or NO_DISPATCH_LABEL in labels_of(dependency):
            return []
        return [candidate for parent in blockers_of(dependency)
                for candidate in runnable_prerequisites(parent, seen)]

    for row in snapshot["live"]:
        epic_id = outcome_of(row["id"])
        sponsor = urgent_sponsor(row)
        eligible_states = {"open", "blocked", "in_progress"} if sponsor else {"open", "blocked"}
        if (not epic_id and not sponsor) or row["id"] in ready_ids or row.get("status") not in eligible_states:
            continue
        if row.get("issue_type") == "epic" or text(row.get("external_ref")).startswith("gh-pr:"):
            continue
        if epic_id:
            epics[epic_id]["blocked_open"].append(row["id"])
        if NO_DISPATCH_LABEL in labels_of(row):
            continue
        for blocker_id in blockers_of(row):
            for blocker in runnable_prerequisites(blocker_id, {row["id"]}):
                if SHAPING_LABEL in labels_of(blocker):
                    continue
                # Emergency scope exemption follows every P0 task/outcome's
                # runnable blocker frontier; ownership and hard gates above
                # still prohibit traversal through held prerequisites.
                if sponsor:
                    cause = urgent_prerequisites.get(blocker["id"], sponsor)
                    urgent_prerequisites[blocker["id"]] = min(cause, sponsor)
                    if blocker["id"] not in urgent_ids:
                        urgent.append(blocker)
                        urgent_ids.add(blocker["id"])
                if not epic_id or blocker["id"] in urgent_ids or outcome_of(blocker["id"]) == epic_id:
                    continue
                if blocker not in epics[epic_id]["prereqs"]:
                    epics[epic_id]["prereqs"].append(blocker)

    def bead_key(row: dict[str, Any]) -> tuple[Any, ...]:
        dependents = row.get("dependent_count") if isinstance(row.get("dependent_count"), int) else 0
        return (priority_of(row), -dependents, text(row.get("created_at")), row["id"])

    def epic_state(info: dict[str, Any]) -> str:
        if info["lanes"]:
            return "active"
        if info["in_review"]:
            return "in-review"
        return "started" if info["closed"] else "new"

    def outcome_key(*, pin: bool, priority: int, moving: bool, remaining: int, row: dict[str, Any]) -> tuple[Any, ...]:
        # One shape for epic and standalone outcomes, so they always compare.
        return (0 if pin else 1, priority, 0 if moving else 1, remaining, text(row.get("created_at")), row["id"])

    def epic_key(epic_id: str) -> tuple[Any, ...]:
        info = epics[epic_id]
        row, state = info["row"], epic_state(info)
        return outcome_key(
            pin=pinned(epic_id),
            priority=max(priority_of(row) - (1 if state == "active" else 0), 0),
            moving=state != "new",
            remaining=info["total"] - info["closed"],
            row=row,
        )

    outcomes: list[dict[str, Any]] = []
    for epic_id, info in epics.items():
        if not (info["items"] or info["prereqs"]) or not in_scope(epic_id):
            continue
        outcomes.append(
            {
                "id": epic_id,
                "epic": info,
                "key": epic_key(epic_id),
                "beads": sorted(info["prereqs"], key=bead_key) + sorted(info["items"], key=bead_key),
                "allowance": max(0, max_lanes_per_epic - info["lanes"]),
            }
        )
    for row in standalone:
        outcomes.append(
            {
                "id": row["id"],
                "epic": None,
                "key": outcome_key(pin=False, priority=priority_of(row), moving=False, remaining=1, row=row),
                "beads": [row],
                "allowance": 1,
            }
        )
    outcomes.sort(key=lambda item: item["key"])

    def epic_reason(epic_id: str) -> str:
        info = epics[epic_id]
        parts = [f"epic {epic_id}"]
        if pinned(epic_id):
            parts.append("pinned")
        parts.append(epic_state(info))
        if info["lanes"] or info["in_review"]:
            parts.append(f"{info['lanes']} lane(s), {info['in_review']} in review")
        parts.append(f"P{priority_of(info['row'])}")
        parts.append(f"{info['closed']}/{info['total']} closed")
        return ", ".join(parts)

    queue: list[dict[str, Any]] = []
    queued: set[str] = set()

    def push(row: dict[str, Any], outcome: str | None, reason: str) -> bool:
        if row["id"] in queued:
            return False
        queued.add(row["id"])
        queue.append(
            {
                "id": row["id"],
                "outcome": outcome,
                "priority": priority_of(row),
                "rank": len(queue) + 1,
                "reason": reason,
                "title": text(row.get("title"))[:TITLE_LIMIT],
                "type": text(row.get("issue_type")),
            }
        )
        return True

    for row in sorted(urgent, key=bead_key):
        prerequisite_sponsor = urgent_prerequisites.get(row["id"])
        sponsor = prerequisite_sponsor or urgent_sponsor(row)
        epic_id = sponsor if prerequisite_sponsor else outcome_of(row["id"])
        sponsor_type = text(rows.get(sponsor, {}).get("issue_type")) or "task"
        cause = (f"P{priority_of(row)} {text(row.get('issue_type'))}" if priority_of(row) == 0
                 else f"prerequisite for P0 {sponsor_type} {sponsor}" if prerequisite_sponsor
                 else f"under P0 {sponsor_type} {sponsor}")
        push(row, epic_id, f"urgent: {cause}")

    def reason_for(outcome: dict[str, Any], row: dict[str, Any]) -> str:
        if outcome["epic"] is None:
            return f"standalone P{priority_of(row)} {text(row.get('issue_type'))}"
        if row in outcome["epic"]["prereqs"]:
            return f"prerequisite for {epic_reason(outcome['id'])}"
        return epic_reason(outcome["id"])

    overflow: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for outcome in outcomes:
        share = outcome["allowance"]
        for row in outcome["beads"]:
            if row["id"] in queued:
                continue
            if share > 0:
                push(row, outcome["id"] if outcome["epic"] else None, reason_for(outcome, row))
                share -= 1
            else:
                overflow.append((outcome, row))
    for outcome, row in overflow:
        push(row, outcome["id"], reason_for(outcome, row) + "; over epic lane cap")

    emitted = queue[: max(limit, 0)]
    admission = admission_queue(queue, rows, delivery_anchors, regs, capacity, review_state,
                                coordinator=coordinator, now=now, limit=limit, ready_ids=ready_ids, budget=budget)
    focus_ids: list[str] = []
    for item in emitted:
        if item["outcome"] in epics and item["outcome"] not in focus_ids:
            focus_ids.append(item["outcome"])

    def epic_summary(epic_id: str) -> dict[str, Any]:
        info = epics[epic_id]
        return {
            "closed": info["closed"],
            "id": epic_id,
            "in_review": info["in_review"],
            "lanes": info["lanes"],
            "needs_shaping": sorted(info["needs_shaping"])[:LIST_LIMIT],
            "pinned": pinned(epic_id),
            "priority": priority_of(info["row"]),
            "ready": len(info["items"]) + len(info["prereqs"]),
            "state": epic_state(info),
            "title": text(info["row"].get("title"))[:TITLE_LIMIT],
            "total": info["total"],
        }

    def blocker_entry(bead_id: str) -> dict[str, Any] | None:
        blocker = rows.get(bead_id)
        if blocker is None:
            return None
        return {"assignee": text(blocker.get("assignee")) or None, "id": bead_id, "status": text(blocker.get("status"))}

    # Held: epics with progress or a pin whose remaining work cannot run.
    # Epics whose open beads are all claimed or in review are busy, not held.
    held = []
    for epic_id in sorted(epics, key=epic_key):
        info = epics[epic_id]
        if info["items"] or info["prereqs"] or not info["total"] or info["closed"] >= info["total"]:
            continue
        if epic_state(info) == "new" and not pinned(epic_id):
            continue
        if not in_scope(epic_id):
            continue
        waiting_on: list[dict[str, Any]] = []
        candidates = list(blockers_of(info["row"]))
        for child_id in info["blocked_open"]:
            child_blockers = blockers_of(rows[child_id])
            # A child blocked by status alone (an owner gate) waits on itself.
            candidates.extend(child_blockers or ([child_id] if rows[child_id].get("status") == "blocked" else []))
        for blocker_id in candidates:
            entry = blocker_entry(blocker_id)
            if entry and entry not in waiting_on:
                waiting_on.append(entry)
        if not (waiting_on or info["needs_shaping"]):
            continue
        summary = epic_summary(epic_id)
        summary["waiting_on"] = waiting_on[:3]
        summary["reason"] = "open beads are blocked" if waiting_on else "only needs-shaping beads are unblocked"
        held.append(summary)

    childless = sorted(
        (0 if pinned(epic_id) else 1, priority_of(info["row"]), text(info["row"].get("created_at")), epic_id)
        for epic_id, info in epics.items()
        if info["total"] == 0 and in_scope(epic_id)
    )
    closable = sorted(epic_id for epic_id, info in epics.items() if info["total"] and info["closed"] >= info["total"])

    self_entry = None
    if coordinator:
        registration = regs.get(coordinator)
        self_entry = {
            "id": coordinator,
            "lanes": by_assignee.get(coordinator, 0),
            "live": bool(registration and registration["live"]),
            "registered": registration is not None,
        }

    in_scope_ready = {row["id"] for row in runnable if in_scope(row["id"])}
    for outcome in outcomes:
        in_scope_ready.update(row["id"] for row in outcome["beads"])

    return {
        "capacity": {"budget": budget, "budget_source": budget_source, **capacity},
        "admission": {key: value for key, value in admission.items() if key != "queue"},
        "candidates": emitted,
        "candidate_total": len(queue),
        "childless_epics": [entry[-1] for entry in childless[:LIST_LIMIT]],
        "closable_epics": closable[:LIST_LIMIT],
        "coordinators": [
            {"expires_at": entry["expires_at"], "id": cid, "live": entry["live"]}
            for cid, entry in sorted(regs.items())
            if entry["live"]
        ],
        "errors": [],
        "excluded": {
            **excluded,
            "local_state": [row["id"] for row in sorted(local_state, key=bead_key)[:LIST_LIMIT]],
            "needs_shaping_standalone": [
                row["id"] for row in sorted(shaping_standalone, key=bead_key)[:LIST_LIMIT]
            ],
        },
        "focus": [epic_summary(epic_id) for epic_id in focus_ids],
        "generated_at": generated_at,
        "held": held[:LIST_LIMIT],
        "held_total": len(held),
        "orphaned_blocked": sorted(orphaned, key=lambda entry: entry["id"])[:LIST_LIMIT],
        "recoverable_claims": sorted(recoverable, key=lambda entry: entry["id"])[:LIST_LIMIT],
        "queue": admission["queue"],
        "queue_total": len(admission["queue"]),
        "schema": SCHEMA,
        "scope": sorted(scope),
        "self": self_entry,
        "status": "ok" if admission["queue"] else admission["status"],
        "stranded": sorted(stranded)[:LIST_LIMIT],
        "totals": {
            "epics_open": len(epics),
            "ready": len(ready_rows),
            "ready_in_scope": len(in_scope_ready & runnable_ids),
        },
        "unowned_claims": sorted(unowned)[:LIST_LIMIT],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank ready Beads work in finish-first order (read-only).")
    parser.add_argument("--repo-root", required=True, type=Path, help="Repository or rig root to inspect.")
    parser.add_argument("--coordinator", help="This coordinator's id (its bd --actor), for `self` and its own released beads.")
    parser.add_argument("--lanes", type=int, help="Global lane budget; overrides the beads.max_lanes kv key.")
    parser.add_argument(
        "--max-lanes-per-epic",
        type=int,
        default=DEFAULT_MAX_LANES_PER_EPIC,
        help="Primary-queue lanes one epic may hold before others are admitted (default 2).",
    )
    parser.add_argument("--scope-epic", action="append", default=[], help="Restrict non-urgent work to this epic's subtree; repeatable.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Queue rows to emit (default 10).")
    parser.add_argument("--review-state-file", type=Path, help="Complete Step-0 normalization; required for admission, otherwise diagnostics only.")
    parser.add_argument("--now", help="RFC 3339 UTC timestamp; supply it for byte-stable reports.")
    args = parser.parse_args()

    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        emit(fatal(None, "invalid-now", "bootstrap"))
        return 1
    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        emit(fatal(format_time(now), "invalid-repo-root", "bootstrap"))
        return 1
    snapshot, error = collect(repo_root, with_dependencies=args.review_state_file is not None)
    if error:
        emit(fatal(format_time(now), error["code"], error["scope"]))
        return 1
    try:
        review_state = json.loads(args.review_state_file.read_text()) if args.review_state_file else None
        payload = rank(
            snapshot,
            now=now,
            coordinator=args.coordinator,
            lanes=args.lanes,
            max_lanes_per_epic=max(args.max_lanes_per_epic, 1),
            scope_epics=args.scope_epic,
            limit=args.limit,
            review_state=review_state,
        )
    except Exception:
        payload = fatal(format_time(now), "unexpected-error", "rank")
    emit(payload)
    return 0 if payload["status"] != "fatal" else 1


if __name__ == "__main__":
    raise SystemExit(main())
