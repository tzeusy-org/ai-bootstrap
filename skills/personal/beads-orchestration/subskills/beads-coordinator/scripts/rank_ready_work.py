#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Rank ready Beads work in finish-first order for one or more coordinators.

Report-only: emits one compact `beads-work-queue/v1` JSON envelope and never
mutates Beads. The coordinator still applies the dispatch-readiness and
cohesion gates and claims atomically; losing a claim race means taking the
next row. Every coordinator reading the same tracker gets the same order, so
parallel coordinators converge on the same epics instead of each opening its
own.

Usage (from the beads-coordinator package directory):

    uv run scripts/rank_ready_work.py --repo-root "$REPO_ROOT" \
        --coordinator "$COORDINATOR_ID" [--lanes N] [--scope-epic ID ...]

Ranking contract (canonical; `coordinator-loop.md` Step 2 only points here):

1. Urgent first: a priority-0 bead, or any ready bead under a priority-0 epic.
   Urgent rows ignore `--scope-epic`.
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
   block one of its open beads, one hop; they count as in scope when the epic
   is), then priority, then most dependents, then oldest.
4. Each epic puts at most `--max-lanes-per-epic` beads, minus the lanes it
   already holds, in the primary queue; a bead another outcome already queued
   does not use up that share. Its other ready beads append after every
   outcome's primary share, so no lane idles while ready work exists.

Capacity: the lane budget is global across coordinators: `--lanes`, else the
`beads.max_lanes` kv key, else 4. Every `in_progress` bead whose assignee is a
coordinator (`coordinator:` prefix) occupies one lane, plus each live
registration's `extra_lanes` (Team Lead subworkers the tracker cannot see).
Other `in_progress` assignees (humans) are counted under `other_claims` only.
Coordinator claims whose assignee has no live registration are listed under
`unowned_claims` and still occupy a lane until recovered.

Excluded from the queue, with counts or ids in `excluded`:
- epics themselves, and every descendant of an `in_progress` epic labelled
  `team-coordination` (a Team Lead holds it; a plain epic claim held for
  shaping or decomposition hides nothing);
- `pr-review` and `pr-review-task` beads (Step 0 owns them);
- `needs-shaping` beads (listed per epic, standalone ones by priority);
- beads with an assignee. Exceptions: an open bead assigned to `--coordinator`
  is queued (a release that kept the assignee), and an open bead whose
  `coordinator:` assignee has no live registration is listed as `stranded`
  for guarded recovery.

Also reported: `held` epics (progress or a pin, no runnable work, with what
they wait on), `childless_epics` for decomposition, and `closable_epics`
(every child closed).

Fail-closed: a failed `bd` command or unexpected output yields `status: fatal`
and no queue. bd older than 1.3 rejects `--brief` and fails this way. The
coordinator then dispatches nothing that wake (`coordinator-loop.md` Step 1).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coordinator_registry import lane_budget, parse_time, registrations  # noqa: E402

SCHEMA = "beads-work-queue/v1"
COORDINATOR_PREFIX = "coordinator:"
FOCUS_LABEL = "focus"
SHAPING_LABEL = "needs-shaping"
TEAM_LEAD_LABEL = "team-coordination"
REVIEW_LABELS = frozenset({"pr-review", "pr-review-task"})
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


def collect(repo_root: Path) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Run the four read-only `bd` queries the ranking needs."""
    queries = {
        "live": ["list", "--status=open,blocked,in_progress", "--json", "--brief", "--limit", "0"],
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


def rank(
    snapshot: dict[str, Any],
    *,
    now: datetime,
    coordinator: str | None = None,
    lanes: int | None = None,
    max_lanes_per_epic: int = DEFAULT_MAX_LANES_PER_EPIC,
    scope_epics: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    generated_at = format_time(now)
    problem = validate(snapshot)
    if problem:
        return fatal(generated_at, problem["code"], problem["scope"])

    rows = {row["id"]: row for row in snapshot["live"]}
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
        return next((ancestor for ancestor in ancestors(bead_id) if ancestor in epics), None)

    def pinned(epic_id: str) -> bool:
        return any(
            FOCUS_LABEL in labels_of(epics[eid]["row"]) for eid in [epic_id, *ancestors(epic_id)] if eid in epics
        )

    scope = set(scope_epics or [])

    def in_scope(bead_id: str) -> bool:
        return not scope or bool(({bead_id} | set(ancestors(bead_id))) & scope)

    def is_live_coordinator(assignee: str) -> bool:
        return bool(regs.get(assignee, {}).get("live"))

    team_led = {
        row["id"]
        for row in snapshot["live"]
        if row.get("issue_type") == "epic"
        and row.get("status") == "in_progress"
        and text(row.get("assignee"))
        and TEAM_LEAD_LABEL in labels_of(row)
    }

    # Capacity and epic activity come from the live rows.
    budget, budget_source = lane_budget(snapshot["kv"], lanes)
    by_assignee: dict[str, int] = {}
    other_claims = 0
    unowned: list[str] = []
    for row in snapshot["live"]:
        bead_id, assignee = row["id"], text(row.get("assignee"))
        epic_id = outcome_of(bead_id)
        if row.get("status") == "in_progress" and assignee:
            if assignee.startswith(COORDINATOR_PREFIX):
                by_assignee[assignee] = by_assignee.get(assignee, 0) + 1
                if not is_live_coordinator(assignee):
                    unowned.append(bead_id)
            else:
                other_claims += 1
            if epic_id and row.get("issue_type") != "epic":
                epics[epic_id]["lanes"] += 1
        if row.get("status") == "blocked" and text(row.get("external_ref")).startswith("gh-pr:") and epic_id:
            epics[epic_id]["in_review"] += 1
    for cid, entry in regs.items():
        if entry["live"] and entry["extra_lanes"]:
            by_assignee[cid] = by_assignee.get(cid, 0) + entry["extra_lanes"]
    occupied = sum(by_assignee.values())

    # Sort ready beads into urgent, epic-owned, and standalone outcomes.
    ready_ids = {row["id"] for row in ready_rows}
    urgent: list[dict[str, Any]] = []
    standalone: list[dict[str, Any]] = []
    shaping_standalone: list[dict[str, Any]] = []
    stranded: list[str] = []
    excluded = {"claimed": 0, "review": 0, "team_lead": 0}
    runnable: list[dict[str, Any]] = []
    for row in ready_rows:
        assignee = text(row.get("assignee"))
        if assignee and not (coordinator and assignee == coordinator):
            if assignee.startswith(COORDINATOR_PREFIX) and not is_live_coordinator(assignee):
                stranded.append(row["id"])
            else:
                excluded["claimed"] += 1
            continue
        if labels_of(row) & REVIEW_LABELS:
            excluded["review"] += 1
            continue
        if set(ancestors(row["id"])) & team_led:
            excluded["team_lead"] += 1
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
        if priority_of(row) == 0 or (epic_id and priority_of(epics[epic_id]["row"]) == 0):
            urgent.append(row)
        elif not in_scope(row["id"]):
            continue
        elif epic_id:
            epics[epic_id]["items"].append(row)
        else:
            standalone.append(row)

    # One-hop prerequisite inheritance: a ready bead outside an epic that
    # blocks one of the epic's open beads is ranked with that epic.
    runnable_by_id = {row["id"]: row for row in runnable}
    urgent_ids = {row["id"] for row in urgent}
    for row in snapshot["live"]:
        epic_id = outcome_of(row["id"])
        if not epic_id or row["id"] in ready_ids or row.get("status") not in {"open", "blocked"}:
            continue
        if row.get("issue_type") == "epic" or text(row.get("external_ref")).startswith("gh-pr:"):
            continue
        epics[epic_id]["blocked_open"].append(row["id"])
        for blocker_id in blockers_of(row):
            blocker = runnable_by_id.get(blocker_id)
            if blocker is None or blocker_id in urgent_ids or outcome_of(blocker_id) == epic_id:
                continue
            if SHAPING_LABEL in labels_of(blocker):
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
        epic_id = outcome_of(row["id"])
        cause = f"P{priority_of(row)} {text(row.get('issue_type'))}" if priority_of(row) == 0 else f"under P0 epic {epic_id}"
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
        if not in_scope(epic_id) or epic_id in team_led:
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
        if info["total"] == 0 and in_scope(epic_id) and epic_id not in team_led
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
        "capacity": {
            "budget": budget,
            "budget_source": budget_source,
            "by_assignee": dict(sorted(by_assignee.items())),
            "free": max(budget - occupied, 0),
            "occupied": occupied,
            "other_claims": other_claims,
        },
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
            "needs_shaping_standalone": [
                row["id"] for row in sorted(shaping_standalone, key=bead_key)[:LIST_LIMIT]
            ],
        },
        "focus": [epic_summary(epic_id) for epic_id in focus_ids],
        "generated_at": generated_at,
        "held": held[:LIST_LIMIT],
        "held_total": len(held),
        "queue": emitted,
        "queue_total": len(queue),
        "schema": SCHEMA,
        "scope": sorted(scope),
        "self": self_entry,
        "status": "ok" if queue else "empty",
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
    snapshot, error = collect(repo_root)
    if error:
        emit(fatal(format_time(now), error["code"], error["scope"]))
        return 1
    try:
        payload = rank(
            snapshot,
            now=now,
            coordinator=args.coordinator,
            lanes=args.lanes,
            max_lanes_per_epic=max(args.max_lanes_per_epic, 1),
            scope_epics=args.scope_epic,
            limit=args.limit,
        )
    except Exception:
        payload = fatal(format_time(now), "unexpected-error", "rank")
    emit(payload)
    return 0 if payload["status"] != "fatal" else 1


if __name__ == "__main__":
    raise SystemExit(main())
