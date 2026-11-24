#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Behavioral tests for the finish-first ranking and the coordinator registry."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "rank_ready_work.py"
NOW = datetime(2026, 10, 27, 4, 0, tzinfo=timezone.utc)


def load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ranking = load("rank_ready_work")
registry = load("coordinator_registry")


def bead(bead_id, *, parent=None, status="open", priority=2, issue_type="task", assignee="",
         labels=None, blocks_on=None, external_ref=None, created="2026-10-01T00:00:00Z", dependents=0):
    return {
        "id": bead_id,
        "title": f"title {bead_id}",
        "parent": parent,
        "status": status,
        "priority": priority,
        "issue_type": issue_type,
        "assignee": assignee,
        "labels": labels or [],
        "external_ref": external_ref,
        "created_at": created,
        "dependent_count": dependents,
        "dependencies": [
            {"issue_id": bead_id, "depends_on_id": dep, "type": "blocks"} for dep in (blocks_on or [])
        ],
    }


def epic(epic_id, *, priority=2, labels=None, created="2026-09-01T00:00:00Z"):
    return bead(epic_id, priority=priority, issue_type="epic", labels=labels, created=created)


def status(epic_row, total, closed):
    return {"epic": epic_row, "total_children": total, "closed_children": closed}


def snapshot(live, epics=(), kv=None, ready=None):
    """Build a tracker snapshot; by default every unassigned open non-epic bead is ready."""
    if ready is None:
        ready = [
            row for row in live
            if row["status"] == "open" and row["issue_type"] != "epic" and not row["dependencies"]
        ]
    return {"live": list(live), "ready": list(ready), "epics": list(epics), "kv": dict(kv or {})}


def queue_ids(payload):
    return [item["id"] for item in payload["candidates"]]


class SnapshotDriftTests(unittest.TestCase):
    def test_collector_reloads_once_before_resolving_missing_dependencies(self):
        stable = bead("bd-stable")
        reopened = bead("bd-reopened")
        discarded = bead("bd-discarded", status="blocked", assignee="coordinator:a", blocks_on=["bd-old-gate"])
        discarded["metadata"] = {"delivery_anchor": "bd-old-anchor"}
        with patch.object(ranking, "bd_json", side_effect=[
            ([stable, discarded], None), ([stable, reopened], None), ([], None), ({}, None),
            ([stable, reopened], None), ([stable, reopened], None), ([], None), ({}, None),
        ]) as tracker:
            current, error = ranking.collect(Path.cwd(), with_dependencies=True)
        self.assertIsNone(error)
        queries = [
            ["list", "--status=open,blocked,in_progress", "--json", "--limit", "0"],
            ["ready", "--json", "--brief", "--limit", "0", "--exclude-type", "epic"],
            ["epic", "status", "--json"],
            ["kv", "list", "--json"],
        ]
        self.assertEqual([call.args[0] for call in tracker.call_args_list], queries * 2)
        result = ranking.rank(current, now=NOW, review_state=normalized())
        self.assertEqual([action["id"] for action in result["queue"]], ["bd-reopened", "bd-stable"])
        self.assertEqual(result, ranking.rank(snapshot([stable, reopened]), now=NOW, review_state=normalized()))

    def test_unresolved_collection_drift_fails_closed_with_specific_reason(self):
        stable = bead("bd-stable", assignee="coordinator:a", blocks_on=["bd-old-gate"])
        stable["metadata"] = {"delivery_anchor": "bd-old-anchor"}
        reopened = bead("bd-reopened")
        with patch.object(ranking, "bd_json", side_effect=[
            ([stable], None), ([stable, reopened], None), ([], None), ({}, None),
        ] * 2) as tracker:
            current, error = ranking.collect(Path.cwd(), with_dependencies=True)
        self.assertIsNone(current)
        self.assertEqual(tracker.call_count, 8)
        self.assertEqual(error, {"code": "ready-live-drift", "scope": "snapshot"})

        result = ranking.rank(
            snapshot([stable], ready=[stable, reopened]), now=NOW, review_state=normalized(),
        )
        self.assertEqual(result["status"], "fatal")
        self.assertEqual(result["errors"], [error])
        self.assertEqual(result["queue"], [])
        self.assertEqual(result["candidates"], [])


SHA = "a" * 40

def normalized(*pairs, head=SHA):
    return {"schema": "beads-pr-review-normalization/v1", "status": "success" if pairs else "empty",
            "generated_at": registry.format_time(NOW), "errors": [], "self_heal_candidates": [],
            "findings": [{"kind": "review-task", "review_id": review["id"], "original_id": anchor["id"],
                          "canonical_review_id": review["id"], "duplicate_of": None, "context_status": "resolved",
                          "pr_state": "OPEN", "pr_number": 1, "head_sha": head,
                          "recommendation": "dispatch-canonical-review", "cooldown_until": None}
                         for anchor, review in pairs]}


def live_registry(*actors):
    return {"beads.coordinator." + actor: {"expires_at": "2026-10-27T05:00:00Z"} for actor in actors}


def review_pair(name="a", *, owner="coordinator:a", members=1):
    anchor = bead("bd-" + name, assignee=owner, status="blocked", labels=["pr-review"], external_ref="gh-pr:1")
    review = bead("bd-review-" + name, status="blocked", labels=["pr-review", "pr-review-task"])
    rows = [anchor, review]
    if members > 1:
        more = [bead("bd-" + name + "." + str(i), assignee=owner, status="in_progress") for i in range(1, members)]
        anchor["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": anchor["id"],
                              "members": [anchor["id"], *[m["id"] for m in more]], "owner": owner,
                              "outcome": anchor["id"], "base_branch": "main", "packet": "Current packet"}}
        for member in more:
            member["metadata"] = {"delivery_anchor": anchor["id"]}
        rows.extend(more)
    return anchor, review, rows


class AdmissionPriorityTests(unittest.TestCase):
    def plan(self, rows, review_state, *, lanes=4, actor="coordinator:a", limit=10, scope=None):
        actors = {"coordinator:a", "coordinator:b", "coordinator:r", actor}
        return ranking.rank(snapshot(rows, kv=live_registry(*actors)), now=NOW, lanes=lanes,
                            coordinator=actor, review_state=review_state, limit=limit, scope_epics=scope)

    def test_saturated_waiting_owners_admit_reviews_without_releasing_any_member(self):
        a, ar, rows_a = review_pair("a", members=3)
        b, br, rows_b = review_pair("b", owner="coordinator:b")
        before = json.dumps(rows_a + rows_b, sort_keys=True)
        payload = self.plan(rows_a + rows_b, normalized((a, ar), (b, br)), lanes=2)
        self.assertEqual(payload["capacity"]["occupied"], 0)
        self.assertEqual(payload["capacity"]["owned"], 2)
        self.assertEqual([item["kind"] for item in payload["queue"]], ["review", "review"])
        self.assertEqual(json.dumps(rows_a + rows_b, sort_keys=True), before)

    def test_one_lane_review_correction_and_re_review_keep_the_implementation_owner(self):
        anchor, review, rows = review_pair(members=2)
        new = bead("bd-new")
        first = self.plan(rows + [new], normalized((anchor, review)), lanes=1)
        self.assertEqual([a["kind"] for a in first["queue"]], ["review"])
        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "corrections"}}
        second = self.plan(rows + [new], normalized((anchor, review)), lanes=1)
        self.assertEqual([a["kind"] for a in second["queue"]], ["correction"])
        self.assertEqual(second["queue"][0]["owner"], "coordinator:a")
        anchor["status"] = "in_progress"
        running = self.plan(rows + [new], normalized((anchor, review)), lanes=1)
        self.assertEqual(running["capacity"]["occupied"], 1)
        self.assertEqual(running["queue"], [])
        anchor["status"] = "blocked"
        moved = self.plan(rows + [new], normalized((anchor, review), head="b" * 40), lanes=1)
        self.assertEqual([a["kind"] for a in moved["queue"]], ["review"])
        self.assertEqual(anchor["assignee"], "coordinator:a")

    def test_open_parked_package_has_one_execution_phase_even_with_spare_slots(self):
        anchor, review, rows = review_pair(members=2)
        anchor["status"] = "open"
        for disposition in (None, "corrections", "ready", "held", "queued"):
            with self.subTest(disposition=disposition):
                review["metadata"] = {} if disposition is None else {
                    "review_receipt": {"head": SHA, "disposition": disposition}}
                payload = self.plan(rows, normalized((anchor, review)), lanes=4)
                expected = ["review"] if disposition is None else ["correction"] if disposition == "corrections" else []
                self.assertEqual([action["kind"] for action in payload["queue"]], expected)
                self.assertTrue(all(action["anchor"] == anchor["id"] for action in payload["queue"]))
                self.assertEqual(anchor["assignee"], "coordinator:a")

    def test_review_is_global_and_precedes_candidate_cutoff_then_spare_slots_accept_new_work(self):
        anchor, review, rows = review_pair()
        new = [bead("bd-low-" + str(i), priority=1) for i in range(5)]
        first = self.plan(rows + new, normalized((anchor, review)), lanes=3, limit=1, scope=["bd-other"])
        self.assertEqual([item["id"] for item in first["queue"]], [review["id"]])
        full = self.plan(rows + new, normalized((anchor, review)), lanes=3, limit=10)
        self.assertEqual([item["kind"] for item in full["queue"]], ["review", "implementation", "implementation"])
        self.assertEqual(full["capacity"]["free_wip"], 2)

    def test_running_reviewer_does_not_idle_spare_execution_capacity(self):
        anchor, review, rows = review_pair()
        review.update(status="in_progress", assignee="coordinator:r")
        payload = self.plan(rows + [bead("bd-new")], normalized((anchor, review)), lanes=2)
        self.assertEqual(payload["capacity"]["occupied"], 1)
        self.assertEqual([item["kind"] for item in payload["queue"]], ["implementation"])

        for case in ("live", "bound-ref", "full", "dead-owner", "owner-changed", "parked", "handed-off",
                     "conflicting-ref", "unknown-head", "head-mismatch", "wrong-branch",
                     "malformed-proof", "actual-review", "held", "blocked", "ambiguous-binding"):
            with self.subTest(author_case=case):
                author = bead("bd-author", status="in_progress", assignee="coordinator:b")
                event = {"original_id": author["id"], "canonical_review_id": None, "pr_number": 1,
                         "context_status": "resolved", "recommendation": "self-heal-original-and-review-wiring",
                         "pr_state": "OPEN", "head_sha": SHA,
                         "author_activity": {"assignee": "coordinator:b", "head_sha": SHA,
                            "branch": "agent/bd-author"}}
                state = normalized()
                state.update(status="success", self_heal_candidates=[event])
                rows = [author, bead("bd-new")]
                lanes = 1 if case == "full" else 2
                if case == "bound-ref":
                    author["external_ref"] = "gh-pr:1"
                if case == "ambiguous-binding":
                    state["self_heal_candidates"].append({**event, "pr_number": 2})
                if case == "dead-owner":
                    author["assignee"] = event["author_activity"]["assignee"] = "coordinator:gone"
                if case == "owner-changed":
                    author["assignee"] = "coordinator:r"
                if case == "parked":
                    author["status"] = "blocked"
                if case == "handed-off":
                    author["labels"] = ["pr-review"]
                if case == "conflicting-ref":
                    author["external_ref"] = "gh-pr:2"
                if case == "unknown-head":
                    event["head_sha"] = None
                if case == "head-mismatch":
                    event["author_activity"]["head_sha"] = "b" * 40
                if case == "wrong-branch":
                    event["author_activity"]["branch"] = "agent/bd-other"
                if case == "malformed-proof":
                    event["author_activity"] = []
                if case == "held":
                    author["labels"] = ["owner-held"]
                if case == "blocked":
                    author["dependencies"] = [{"issue_id": author["id"], "depends_on_id": "bd-unknown", "type": "blocks"}]
                if case == "actual-review":
                    anchor, review, review_rows = review_pair("other", owner="coordinator:b")
                    review["assignee"] = "coordinator:r"
                    rows.extend(review_rows)
                    state["findings"] = normalized((anchor, review))["findings"]
                    lanes = 4
                before = json.dumps((rows, state), sort_keys=True)
                payload = self.plan(rows, state, lanes=lanes)
                # Unwired author work waits by anchor; it never idles a free lane.
                expected = [] if case == "full" else ["bd-new"]
                self.assertEqual([item["id"] for item in payload["queue"]], expected)
                self.assertEqual(payload["capacity"]["occupied"], 0 if case == "parked" else 1)
                self.assertIn({"id": author["id"], "reason": "review-wiring-required", "owner": author["assignee"]},
                              payload["admission"]["waiting"])
                self.assertLessEqual(len(payload["queue"]), payload["capacity"]["free"])
                self.assertEqual(json.dumps((rows, state), sort_keys=True), before)

    def test_review_record_hard_gates_block_only_its_review_execution(self):
        anchor, review, rows = review_pair()
        # The normal implementation -> review edge is not a review blocker.
        anchor["dependencies"] = [{"issue_id": anchor["id"], "depends_on_id": review["id"], "type": "blocks"}]
        gate = bead("bd-review-gate", status="blocked")
        for labels, dependencies in ((["no-dispatch"], []), ([], [gate["id"]])):
            with self.subTest(labels=labels, dependencies=dependencies):
                review["labels"] = ["pr-review", "pr-review-task", *labels]
                review["dependencies"] = [{"issue_id": review["id"], "depends_on_id": target, "type": "blocks"}
                                          for target in dependencies]
                held = self.plan(rows + [gate, bead("bd-independent")], normalized((anchor, review)), lanes=2)
                self.assertEqual([action["id"] for action in held["queue"]], ["bd-independent"])
        review["labels"] = ["pr-review", "pr-review-task", "owner-merge"]
        gate["status"] = "closed"
        clear = self.plan(rows + [gate], normalized((anchor, review)))
        self.assertEqual([action["kind"] for action in clear["queue"]], ["review"])

    def test_review_waits_for_same_package_execution_but_spare_slots_remain_useful(self):
        anchor, review, rows = review_pair(members=2)
        for anchor_status in ("in_progress", "closed"):
            with self.subTest(anchor_status=anchor_status):
                anchor["status"] = anchor_status
                payload = self.plan(rows + [bead("bd-independent")], normalized((anchor, review)), lanes=3)
                self.assertEqual([action["id"] for action in payload["queue"]], ["bd-independent"])
                self.assertIn({"id": anchor["id"], "reason": "implementation-running"}, payload["admission"]["waiting"])
                self.assertEqual(payload["capacity"]["occupied"], 1)

    def test_one_parked_package_allows_one_emergency_overflow_but_never_a_second(self):
        anchor, review, rows = review_pair()
        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "ready"}}
        fire, second = bead("bd-fire", priority=0), bead("bd-fire-again", priority=0)
        first = self.plan(rows + [fire, second], normalized((anchor, review)), lanes=1)
        self.assertEqual([action["id"] for action in first["queue"]], [fire["id"]])
        self.assertEqual(first["queue"][0]["wip_exception"], "emergency")
        fire.update(assignee="coordinator:b", status="blocked")
        next_cycle = self.plan(rows + [fire, second], normalized((anchor, review)), lanes=1)
        self.assertEqual(next_cycle["capacity"]["owned"], 2)
        self.assertEqual(next_cycle["queue"], [])
        fire["status"] = "in_progress"
        running = self.plan(rows + [fire, second], normalized((anchor, review)), lanes=1)
        self.assertEqual(running["queue"], [])
        self.assertEqual(running["capacity"]["occupied"], 1)

    def test_spare_execution_slots_cannot_create_multiple_wip_exceptions_in_one_plan(self):
        owners = [bead("bd-owned-" + str(i), assignee="coordinator:a", status="blocked") for i in range(2)]
        fires = [bead("bd-fire-" + str(i), priority=0) for i in range(3)]
        payload = self.plan(owners + fires, normalized(), lanes=2)
        self.assertEqual(payload["capacity"]["free"], 2)
        self.assertEqual([action["id"] for action in payload["queue"]], [fires[0]["id"]])
        self.assertEqual(payload["queue"][0]["wip_exception"], "emergency")

    def test_retained_standalone_and_epic_packages_can_admit_one_runnable_prerequisite(self):
        for packaged in (False, True):
            with self.subTest(packaged=packaged):
                substrate = bead("bd-z-substrate", priority=3)
                intermediate = bead("bd-intermediate", status="blocked", blocks_on=[substrate["id"]])
                anchor = bead("bd-owned", assignee="coordinator:a", status="blocked", blocks_on=[intermediate["id"]])
                rows, epics = [anchor, intermediate, substrate, bead("bd-a-unrelated", priority=1)], []
                if packaged:
                    outcome = epic("bd-accepted")
                    anchor["parent"] = outcome["id"]
                    anchor["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": anchor["id"],
                        "members": [anchor["id"]], "owner": "coordinator:a", "outcome": outcome["id"],
                        "base_branch": "main", "packet": "Accepted feature requires canonical substrate"}}
                    rows.append(outcome)
                    epics = [status(outcome, 1, 0)]
                payload = ranking.rank(snapshot(rows, epics, live_registry("coordinator:a")), now=NOW,
                                       coordinator="coordinator:a", lanes=1, review_state=normalized())
                self.assertEqual([action["id"] for action in payload["queue"]], [substrate["id"]])
                self.assertEqual(payload["queue"][0]["wip_exception"], "prerequisite")
                self.assertEqual(anchor["assignee"], "coordinator:a")
                substrate.update(assignee="coordinator:b", status="blocked")
                saturated = ranking.rank(snapshot(rows, epics, live_registry("coordinator:a", "coordinator:b")), now=NOW,
                                         coordinator="coordinator:a", lanes=1, review_state=normalized())
                self.assertEqual(saturated["queue"], [])

    def test_held_or_foreign_prerequisite_chain_cannot_convey_wip_overflow(self):
        substrate = bead("bd-substrate")
        intermediate = bead("bd-intermediate", status="blocked", blocks_on=[substrate["id"]])
        anchor = bead("bd-owned", assignee="coordinator:a", status="blocked", blocks_on=[intermediate["id"]])
        intermediate["labels"] = ["owner-held"]
        held = self.plan([anchor, intermediate, substrate], normalized(), lanes=1)
        self.assertEqual(held["queue"], [])
        intermediate["labels"] = []
        anchor["labels"] = ["owner-held"]
        held_owner = self.plan([anchor, intermediate, substrate], normalized(), lanes=1)
        # An owner-gated anchor releases WIP, so this is ordinary admission.
        self.assertEqual([(a["id"], a.get("wip_exception")) for a in held_owner["queue"]], [("bd-substrate", None)])
        anchor["labels"] = []
        intermediate.update(labels=[], assignee="coordinator:b")
        foreign = self.plan([anchor, intermediate, substrate], normalized(), lanes=1)
        self.assertEqual(foreign["queue"], [])

    def test_canonical_waiting_review_can_convey_its_external_runnable_prerequisite(self):
        anchor, review, rows = review_pair(members=2)
        prerequisite = bead("bd-review-prerequisite")
        anchor["dependencies"] = [{"issue_id": anchor["id"], "depends_on_id": review["id"], "type": "blocks"}]
        review["dependencies"] = [{"issue_id": review["id"], "depends_on_id": prerequisite["id"], "type": "blocks"}]
        for reviewer in ("", "coordinator:r"):
            with self.subTest(reviewer=reviewer):
                review["assignee"] = reviewer
                payload = self.plan(rows + [prerequisite], normalized((anchor, review)), lanes=1)
                self.assertEqual([action["id"] for action in payload["queue"]], [prerequisite["id"]])
                self.assertEqual(payload["queue"][0]["wip_exception"], "prerequisite")
                self.assertEqual(review["assignee"], reviewer)
                review["labels"].append("owner-held")
                held = self.plan(rows + [prerequisite], normalized((anchor, review)), lanes=1)
                self.assertEqual(held["queue"], [])
                review["labels"].remove("owner-held")
        prerequisite["assignee"] = "coordinator:b"
        foreign = self.plan(rows + [prerequisite], normalized((anchor, review)), lanes=1)
        self.assertEqual(foreign["queue"], [])

    def test_queued_peer_reviewer_waits_without_blocking_new_work_or_losing_its_claim(self):
        anchor, review, rows = review_pair()
        review["assignee"] = "coordinator:r"
        payload = self.plan(rows + [bead("bd-new")], normalized((anchor, review)), lanes=2)
        self.assertEqual([action["id"] for action in payload["queue"]], ["bd-new"])
        self.assertIn({"id": review["id"], "reason": "review-owned-by-peer", "owner": "coordinator:r"},
                      payload["admission"]["waiting"])
        self.assertEqual(review["assignee"], "coordinator:r")
        emergency = self.plan(rows + [bead("bd-fire", priority=0)], normalized((anchor, review)), lanes=2)
        self.assertEqual([action["id"] for action in emergency["queue"]], ["bd-fire"])

    def test_needed_prerequisites_are_admitted_past_peer_waits_but_not_past_their_own_gates(self):
        cases = (
            "healthy", "terminal-owner-gate", "human-backlog", "running-peer-wiring", "combined",
            "terminal-clear", "terminal-unknown-gate", "terminal-unknown-head", "terminal-wrong-association", "queued-peer-review", "queued-peer-correction",
            "queued-peer-resume", "dead-coordinator-resume", "running-peer-unknown-head",
            "running-peer-wrong-association", "running-peer-dead-owner", "running-peer-malformed-wiring", "queued-peer-wiring",
            "unknown-wiring", "partial-state", "own-held", "foreign-prerequisite",
            "held-prerequisite", "unknown-prerequisite", "capacity-full", "overflow-full",
            "local-review-first", "human-canonical-correction", "human-pr-resume", "human-formal-correction",
        )
        spillover = {"healthy", "terminal-owner-gate", "human-backlog", "running-peer-wiring", "combined",
                     "local-review-first"}
        # A peer's or dead owner's pending work parks itself. Only the
        # prerequisite's own gate, invalid state, or capacity refuses it.
        allowed = set(cases) - {"own-held", "foreign-prerequisite", "held-prerequisite",
                                "unknown-prerequisite", "capacity-full", "overflow-full"}
        for case in cases:
            with self.subTest(case=case):
                prerequisite = bead("bd-needed", priority=3)
                retained = bead("bd-retained", status="blocked", assignee="coordinator:a",
                                blocks_on=[prerequisite["id"]])
                rows = [retained, prerequisite]
                state = normalized()
                lanes = 3
                terminal = case.startswith("terminal-")
                running = case.startswith("running-peer-") or case in {"combined", "capacity-full"}
                if terminal or running or case in {"queued-peer-review", "queued-peer-correction",
                                                   "queued-peer-wiring", "local-review-first", "human-canonical-correction", "human-formal-correction"}:
                    anchor, review, pair_rows = review_pair(
                        "other", owner="human-owner" if case.startswith("human-") else "coordinator:b",
                        members=2 if case == "human-formal-correction" else 1)
                    rows.extend(pair_rows)
                    state = normalized((anchor, review))
                    if terminal:
                        state["findings"][0]["pr_state"] = "CLOSED"
                        if case != "terminal-clear":
                            review["dependencies"] = [{"issue_id": review["id"], "type": "blocks",
                                                       "depends_on_id": "bd-owner-gate"}]
                            if case != "terminal-unknown-gate":
                                rows.append(bead("bd-owner-gate", status="blocked", labels=["owner-held"]))
                        if case == "terminal-unknown-head":
                            state["findings"][0]["head_sha"] = None
                        if case == "terminal-wrong-association":
                            anchor["external_ref"] = "gh-pr:2"
                    if running or case == "queued-peer-wiring":
                        review.update(status="in_progress" if running else "blocked", assignee="coordinator:r")
                        state["self_heal_candidates"] = [{"original_id": anchor["id"],
                            "canonical_review_id": review["id"], "pr_number": 1,
                            "context_status": "resolved", "recommendation": "self-heal-original-wiring"}]
                        if case == "running-peer-malformed-wiring":
                            state["self_heal_candidates"][0]["pr_number"] = []
                        if case == "running-peer-unknown-head":
                            state["findings"][0]["head_sha"] = None
                        if case == "running-peer-wrong-association":
                            anchor["external_ref"] = "gh-pr:2"
                        if case == "running-peer-dead-owner":
                            review["assignee"] = "coordinator:gone"
                    if case == "queued-peer-review":
                        review["assignee"] = "coordinator:r"
                    if case in {"queued-peer-correction", "human-canonical-correction", "human-formal-correction"}:
                        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "corrections"}}
                    if case == "local-review-first":
                        anchor["assignee"] = "coordinator:a"
                if case == "combined":
                    terminal_anchor, terminal_review, terminal_rows = review_pair("terminal", owner="")
                    terminal_review["dependencies"] = [{"issue_id": terminal_review["id"],
                        "depends_on_id": "bd-owner-gate", "type": "blocks"}]
                    rows.extend([*terminal_rows, bead("bd-owner-gate", status="blocked", labels=["owner-held"])])
                    terminal_state = normalized((terminal_anchor, terminal_review))
                    terminal_state["findings"][0]["pr_state"] = "CLOSED"
                    state["findings"].extend(terminal_state["findings"])
                if case in {"human-backlog", "combined"}:
                    rows.append(bead("bd-personal", assignee="human-owner"))
                if case == "human-pr-resume":
                    rows.append(bead("bd-human-pr", assignee="human-owner", external_ref="gh-pr:2"))
                if case in {"queued-peer-resume", "dead-coordinator-resume"}:
                    rows.append(bead("bd-resume", assignee="coordinator:b" if case == "queued-peer-resume"
                                     else "coordinator:gone"))
                if case == "unknown-wiring":
                    state["self_heal_candidates"] = [{"recommendation": "manual-triage"}]
                if case == "partial-state":
                    state["status"] = "partial"
                if case in {"own-held", "foreign-prerequisite", "held-prerequisite", "unknown-prerequisite",
                            "overflow-full"}:
                    lanes = 1
                    if case == "own-held":
                        retained["labels"] = ["owner-held"]
                    if case == "foreign-prerequisite":
                        prerequisite["assignee"] = "coordinator:b"
                    if case == "held-prerequisite":
                        prerequisite["labels"] = ["owner-held"]
                    if case == "unknown-prerequisite":
                        rows.remove(prerequisite)
                    if case == "overflow-full":
                        rows.append(bead("bd-parked-overflow", status="blocked", assignee="coordinator:b"))
                if case == "capacity-full":
                    lanes = 1
                # Known waits must not newly admit ordinary tier5 spillover.
                if case in spillover - {"healthy", "local-review-first"}:
                    rows.append(bead("bd-unrelated", priority=1))
                before = json.dumps((rows, state), sort_keys=True)
                payload = self.plan(rows, state, lanes=lanes)
                expected = [prerequisite["id"]] if case in allowed else []
                if case == "local-review-first":
                    expected.insert(0, review["id"])
                if case == "human-backlog":
                    # A human's own backlog holds no WIP and gates nothing.
                    expected.append("bd-unrelated")
                if case == "own-held":
                    # An owner-gated retained package frees its WIP slot: the
                    # bead is ordinary admission, not a prerequisite exception.
                    expected = [prerequisite["id"]]
                    self.assertNotIn("wip_exception", payload["queue"][0])
                self.assertEqual([action["id"] for action in payload["queue"]], expected)
                if case in allowed:
                    action = next(action for action in payload["queue"] if action["id"] == prerequisite["id"])
                    self.assertEqual(action["session_policy"], "allocate-owner")
                self.assertLessEqual(len(payload["queue"]), payload["capacity"]["free"])
                self.assertEqual(json.dumps((rows, state), sort_keys=True), before,
                                 "ranking must preserve every gate and foreign/human claim")

    def test_historical_partial_source_does_not_require_an_active_review_inventory_entry(self):
        partial = bead("bd-partial", status="blocked", assignee="coordinator:b",
                       external_ref="gh-pr:53", blocks_on=["bd-owner-gate"])
        gate = bead("bd-owner-gate", status="blocked", labels=["owner-held"])
        payload = self.plan([partial, gate, bead("bd-new")], normalized(), lanes=2)
        self.assertEqual([action["id"] for action in payload["queue"]], ["bd-new"])
        self.assertEqual(payload["capacity"]["owned"], 1)

    def test_review_only_admission_reports_a_nonempty_top_level_status(self):
        anchor, review, rows = review_pair()
        payload = self.plan(rows, normalized((anchor, review)), lanes=1)
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(payload["status"], "ok")

    def test_unusable_review_evidence_degrades_to_unclaimed_implementation_only(self):
        anchor, review, rows = review_pair()
        missing = self.plan(rows + [bead("bd-new")], None)
        self.assertEqual(missing["queue"], [])
        self.assertEqual(missing["admission"]["reason"], "review-state-required")
        bad_states = {"review-state-incomplete": {**normalized(), "status": "partial", "errors": [{"code": "unavailable"}]},
                      "review-inventory-changed": normalized(),
                      "invalid-review-state": {**normalized(), "findings": [{"kind": "original"}]}}
        for reason, state in bad_states.items():
            with self.subTest(reason=reason):
                # The review is not dispatched on evidence that cannot be trusted,
                # and a bead already carrying a PR is not started a second time.
                payload = self.plan(rows + [bead("bd-new"), bead("bd-has-pr", external_ref="gh-pr:9")], state)
                self.assertEqual([action["id"] for action in payload["queue"]], ["bd-new"])
                self.assertEqual(payload["admission"]["status"], "ready")
                self.assertEqual(payload["admission"]["reason"], reason)
                self.assertIn({"id": None, "reason": reason}, payload["admission"]["waiting"])
                idle = self.plan(rows, state)
                self.assertEqual(idle["queue"], [])
                self.assertEqual(idle["admission"]["status"], "blocked")

    def test_unchanged_content_receipt_waits_without_new_review_and_changed_head_invalidates_it(self):
        anchor, review, rows = review_pair()
        for disposition in ("ready", "queued", "held"):
            with self.subTest(disposition=disposition):
                review["metadata"] = {"review_receipt": {"head": SHA, "disposition": disposition}}
                same = self.plan(rows + [bead("bd-new")], normalized((anchor, review)), lanes=2)
                self.assertEqual([a["kind"] for a in same["queue"]], ["implementation"])
                changed = self.plan(rows + [bead("bd-new")], normalized((anchor, review), head="b" * 40), lanes=2)
                self.assertEqual([a["kind"] for a in changed["queue"]], ["review", "implementation"])

    def test_hard_gate_blocks_its_package_only_and_wip_bounds_spillover(self):
        anchor, review, rows = review_pair()
        anchor["labels"].append("owner-held")
        payload = self.plan(rows + [bead("bd-independent")], normalized((anchor, review)), lanes=2)
        self.assertEqual([a["id"] for a in payload["queue"]], ["bd-independent"])
        one = self.plan(rows + [bead("bd-independent")], normalized((anchor, review)), lanes=1)
        self.assertEqual([a["id"] for a in one["queue"]], ["bd-independent"])
        anchor["labels"].remove("owner-held")
        full = self.plan(rows + [bead("bd-independent")], normalized((anchor, review)), lanes=1)
        self.assertEqual([a["id"] for a in full["queue"]], [review["id"]])
        self.assertEqual(anchor["assignee"], "coordinator:a")

    def test_foreign_live_correction_returns_to_existing_owner_without_claim_transfer(self):
        anchor, review, rows = review_pair(owner="coordinator:b")
        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "corrections"}}
        payload = self.plan(rows, normalized((anchor, review)), lanes=1)
        self.assertEqual(payload["queue"], [])
        self.assertIn({"id": anchor["id"], "reason": "resume-existing-owner", "owner": "coordinator:b"}, payload["admission"]["waiting"])
        self.assertEqual(anchor["assignee"], "coordinator:b")

    def test_review_cannot_ignore_an_external_member_dependency_or_malformed_receipt(self):
        anchor, review, rows = review_pair()
        anchor["dependencies"] = [{"issue_id": anchor["id"], "depends_on_id": "bd-hard-gate", "type": "blocks"}]
        held = self.plan(rows + [bead("bd-hard-gate", status="blocked", labels=["owner-held"])], normalized((anchor, review)))
        self.assertEqual(held["queue"], [])
        anchor["dependencies"] = []
        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "complete"}}
        # A malformed receipt parks that review; unrelated ready work still runs.
        invalid = self.plan(rows + [bead("bd-independent")], normalized((anchor, review)))
        self.assertIn({"id": review["id"], "reason": "invalid-review-receipt"}, invalid["admission"]["waiting"])
        self.assertEqual([a["id"] for a in invalid["queue"]], ["bd-independent"])

    def test_collector_supplies_review_prerequisites_without_weakening_gates(self):
        cases = (
            "closed", "omitted", "listed-open-shown-closed", "listed-open-shown-blocked",
            "shared-target", "shown-open", "shown-blocked", "unknown", "failed", "malformed",
            "multiple", "wrong-id", "invalid-target", "without-dependencies",
            "member", "known-closed-member", "member-live-blocker", "member-held",
            "member-owner-mismatch", "source-held", "inactive-source-owner",
            "peer-source-owner", "review-held", "review-owned-by-peer", "terminal-owner-gate",
        )
        for case in cases:
            with self.subTest(case=case):
                packaged = case in {"member", "known-closed-member", "member-live-blocker",
                                    "member-held", "member-owner-mismatch"}
                owner = "coordinator:a" if packaged else ""
                if case in {"inactive-source-owner", "peer-source-owner"}:
                    owner = "coordinator:b"
                anchor, review, rows = review_pair(owner=owner, members=2 if packaged else 1)
                gate = bead("bd-prerequisite", status="closed")
                anchor["dependencies"] = [
                    {"issue_id": anchor["id"], "depends_on_id": target, "type": "blocks"}
                    for target in (gate["id"], review["id"])
                ]
                # The primary regression must not be fetched via the review.
                review["dependencies"] = []
                if case == "shared-target":
                    review["dependencies"] = [
                        {"issue_id": review["id"], "depends_on_id": gate["id"], "type": "blocks"}
                    ]
                shows = {gate["id"]: [gate]}
                if packaged:
                    member = rows.pop()
                    member["dependencies"] = [
                        {"issue_id": member["id"], "depends_on_id": gate["id"], "type": "blocks"}
                    ]
                    anchor["dependencies"] = [
                        {"issue_id": anchor["id"], "depends_on_id": review["id"], "type": "blocks"}
                    ]
                    review["dependencies"] = []
                    shows[member["id"]] = [member]
                    if case == "known-closed-member":
                        member["status"] = "closed"
                        anchor["dependencies"].append(
                            {"issue_id": anchor["id"], "depends_on_id": member["id"], "type": "blocks"}
                        )
                    elif case == "member-held":
                        member["labels"].append("owner-held")
                    elif case == "member-owner-mismatch":
                        member["assignee"] = "coordinator:b"
                if case in {"shown-open", "shown-blocked", "member-live-blocker"}:
                    gate["status"] = "open" if case == "shown-open" else "blocked"
                elif case == "source-held":
                    anchor["labels"].append("owner-held")
                elif case == "review-held":
                    review["labels"].append("owner-held")
                elif case == "review-owned-by-peer":
                    review["assignee"] = "coordinator:r"
                elif case == "invalid-target":
                    anchor["dependencies"][0]["depends_on_id"] = "invalid target"
                state = normalized((anchor, review))
                if case == "terminal-owner-gate":
                    gate.update(status="blocked", assignee="human", labels=["owner-gated"])
                    rows.append(gate)
                    state["findings"][0].update(pr_state="CLOSED", recommendation="close-review-reopen-original")
                if case == "unknown":
                    shows[gate["id"]] = []
                elif case == "malformed":
                    shows[gate["id"]] = {}
                elif case == "multiple":
                    shows[gate["id"]] = [gate, gate]
                elif case == "wrong-id":
                    shows[gate["id"]] = [bead("bd-other", status="closed")]
                # An unrelated unassigned non-PR blocker must not cause archive reads.
                rows.append(bead("bd-unrelated", status="blocked", blocks_on=["bd-unread"]))
                actors = ("coordinator:a", "coordinator:r")
                if case != "inactive-source-owner":
                    actors += ("coordinator:b",)
                source = snapshot(rows, kv=live_registry(*actors), ready=[])
                original = json.dumps((source, shows, state), sort_keys=True)
                reads = []
                batch_reads = []

                def read(argv, repo_root):
                    queries = {
                        ("list", "--status=open,blocked,in_progress", "--json", "--limit", "0"): source["live"],
                        ("ready", "--json", "--brief", "--limit", "0", "--exclude-type", "epic"): source["ready"],
                        ("epic", "status", "--json"): source["epics"],
                        ("kv", "list", "--json"): source["kv"],
                    }
                    if tuple(argv) in queries:
                        return queries[tuple(argv)], None
                    if argv[:2] == ["list", "--id"]:
                        self.assertEqual(argv[2].split(","), [gate["id"]])
                        batch_reads.append(gate["id"])
                        if case in {"omitted", "failed"}:
                            return [], None
                        if case.startswith("listed-open-"):
                            return [{**gate, "status": "open"}], None
                        return shows[gate["id"]], None
                    self.assertEqual(argv[0], "show")
                    self.assertEqual(argv[2:], ["--json"])
                    reads.append(argv[1])
                    if case == "failed" and argv[1] == gate["id"]:
                        return None, "command-failed"
                    self.assertIn(argv[1], shows, "collector must read only missing direct identities")
                    if case == "listed-open-shown-blocked":
                        return [{**gate, "status": "blocked"}], None
                    return shows[argv[1]], None

                with patch.object(ranking, "bd_json", side_effect=read):
                    collected, error = ranking.collect(Path("/closed-fixture"), with_dependencies=case != "without-dependencies")
                expected_error = {
                    "unknown": "invalid-dependency", "failed": "command-failed",
                    "malformed": "invalid-dependency", "multiple": "invalid-dependency",
                    "wrong-id": "invalid-dependency", "invalid-target": "invalid-dependency",
                    "member-owner-mismatch": "invalid-delivery-package",
                }.get(case)
                if expected_error:
                    self.assertIsNone(collected)
                    self.assertEqual(error, {"code": expected_error,
                                             "scope": "packages" if case == "member-owner-mismatch" else "admission"})
                else:
                    self.assertIsNone(error)
                    payload = ranking.rank(collected, now=NOW, coordinator="coordinator:a", lanes=4, review_state=state)
                    allowed = case in {"closed", "omitted", "listed-open-shown-closed", "shared-target",
                                       "member", "known-closed-member", "peer-source-owner"}
                    self.assertEqual([action["id"] for action in payload["queue"]], [review["id"]] if allowed else [])
                    if allowed:
                        self.assertEqual(payload["queue"][0]["owner"], owner)
                    if case == "terminal-owner-gate":
                        self.assertIn({"id": review["id"], "reason": "reconcile-terminal-pr"}, payload["admission"]["waiting"])
                    if case in {"without-dependencies", "known-closed-member", "terminal-owner-gate"}:
                        self.assertNotIn(gate["id"], reads)
                        self.assertEqual(batch_reads, [])
                    else:
                        self.assertEqual(batch_reads, [gate["id"]])
                        fallback = case in {"omitted", "shown-open", "shown-blocked", "member-live-blocker",
                                            "listed-open-shown-closed", "listed-open-shown-blocked"}
                        self.assertEqual(reads.count(gate["id"]), int(fallback))
                self.assertEqual(len(reads), len(set(reads)), "duplicate dependency reads")
                self.assertEqual(json.dumps((source, shows, state), sort_keys=True), original)

    def test_known_closed_dependency_allows_review_and_p0_still_preempts_new_review(self):
        anchor, review, rows = review_pair()
        gate = bead("bd-gate", status="closed")
        anchor["dependencies"] = [{"issue_id": anchor["id"], "depends_on_id": gate["id"], "type": "blocks"}]
        snap = snapshot(rows + [bead("bd-fire", priority=0)], kv=live_registry("coordinator:a"))
        snap["dependency_records"] = [gate]
        payload = ranking.rank(snap, now=NOW, coordinator="coordinator:a", lanes=2, review_state=normalized((anchor, review)))
        self.assertEqual([a["id"] for a in payload["queue"]], ["bd-fire", review["id"]])


class OwnedWipExclusionTests(unittest.TestCase):
    """Owned WIP bounds what agents are building, not what waits on a human."""

    def plan(self, rows, review_state=None, *, lanes=1, live=("coordinator:a",), kv=None):
        return ranking.rank(snapshot(rows, kv={**live_registry(*live), **(kv or {})}), now=NOW, lanes=lanes,
                            coordinator="coordinator:a", review_state=review_state or normalized())

    def test_needs_human_park_of_a_live_owner_frees_wip_for_ready_work(self):
        parked = bead("bd-parked", status="blocked", assignee="coordinator:a", labels=["needs-human"])
        payload = self.plan([parked, bead("bd-new")])
        self.assertEqual(payload["capacity"]["owned"], 0)
        self.assertEqual(payload["capacity"]["owned_excluded"], {"owner_gate": ["bd-parked"], "dead_owner": []})
        self.assertEqual([a["id"] for a in payload["queue"]], ["bd-new"])
        self.assertNotIn("wip_exception", payload["queue"][0])
        self.assertEqual(parked["assignee"], "coordinator:a")

    def test_only_a_same_head_held_review_receipt_releases_the_anchor_wip(self):
        for disposition, owned in (("held", 0), ("queued", 1), ("corrections", 1)):
            with self.subTest(disposition=disposition):
                anchor, review, rows = review_pair()
                review["metadata"] = {"review_receipt": {"head": SHA, "disposition": disposition}}
                payload = self.plan(rows, normalized((anchor, review)))
                self.assertEqual(payload["capacity"]["owned"], owned)
                self.assertEqual(payload["capacity"]["owned_excluded"]["owner_gate"], [anchor["id"]] if not owned else [])
        anchor, review, rows = review_pair()
        review["metadata"] = {"review_receipt": {"head": "b" * 40, "disposition": "held"}}
        self.assertEqual(self.plan(rows, normalized((anchor, review)))["capacity"]["owned"], 1)

    def test_blocked_claims_of_unregistered_or_expired_coordinators_are_adoptable_not_owned(self):
        rows = [bead("bd-gone", status="blocked", assignee="coordinator:gone"),
                bead("bd-expired", status="blocked", assignee="coordinator:expired"), bead("bd-new")]
        expired = {"beads.coordinator.coordinator:expired": {"expires_at": "2026-10-27T03:00:00Z"}}
        payload = self.plan(rows, kv=expired)
        self.assertEqual(payload["capacity"]["owned"], 0)
        self.assertEqual(payload["capacity"]["owned_excluded"]["dead_owner"], ["bd-expired", "bd-gone"])
        self.assertEqual([entry["id"] for entry in payload["orphaned_blocked"]], ["bd-expired", "bd-gone"])
        self.assertEqual([a["id"] for a in payload["queue"]], ["bd-new"])

    def test_technical_waits_and_running_anchors_of_live_owners_still_count(self):
        ci_wait = bead("bd-ci", status="blocked", assignee="coordinator:a", external_ref="gh-pr:7")
        running = bead("bd-running", status="in_progress", assignee="coordinator:b", labels=["needs-human"])
        payload = self.plan([ci_wait, running, bead("bd-new")], lanes=2, live=("coordinator:a", "coordinator:b"))
        self.assertEqual(payload["capacity"]["owned"], 2)
        self.assertEqual(payload["capacity"]["occupied"], 1)
        self.assertEqual(payload["capacity"]["owned_excluded"], {"owner_gate": [], "dead_owner": []})
        self.assertEqual(payload["queue"], [])
        self.assertEqual(payload["admission"]["waiting"], [{"id": "bd-new", "reason": "owned-wip-budget-full"}])

    def test_four_gate_parked_dead_claims_do_not_stall_a_four_lane_budget(self):
        owner = "coordinator:codex-20261114-"
        parks = [bead("hud-avr7s.4", status="blocked", assignee=owner + "1", labels=["needs-human"]),
                 bead("hud-h51u7.4.6", status="blocked", assignee=owner + "2", labels=["needs-human"]),
                 bead("hud-ljhku.5", status="blocked", assignee=owner + "3", labels=["human-gate"])]
        anchor, review, pair = review_pair("h51u7.8.3", owner=owner + "4")
        review["metadata"] = {"review_receipt": {"head": SHA, "disposition": "held"}}
        payload = self.plan([*parks, *pair, bead("hud-next")], normalized((anchor, review)), lanes=4, live=())
        excluded = payload["capacity"]["owned_excluded"]
        self.assertEqual(payload["capacity"]["owned"], 0)
        self.assertEqual(sorted(excluded["owner_gate"]), sorted(excluded["dead_owner"]))
        self.assertEqual(len(excluded["owner_gate"]), 4)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual([a["id"] for a in payload["queue"]], ["hud-next"])


class DeliveryPackageSchedulingTests(unittest.TestCase):
    def test_retained_one_bead_pr_owner_uses_the_same_capacity_with_or_without_manifest(self):
        owner = "coordinator:a"
        row = bead("bd-one", status="blocked", assignee=owner, external_ref="gh-pr:1")
        single = ranking.rank(snapshot([row]), now=NOW)
        row["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": row["id"],
            "members": [row["id"]], "owner": owner, "outcome": row["id"],
            "base_branch": "main", "packet": "Accepted scope"}}
        manifested = ranking.rank(snapshot([row]), now=NOW)
        self.assertEqual(single["capacity"]["occupied"], 0)
        self.assertEqual(single["capacity"], manifested["capacity"])
        self.assertEqual(single["capacity"]["free"], single["capacity"]["budget"])

    def test_scoped_run_keeps_the_runnable_frontier_of_an_outside_p0_epic(self):
        normal, emergency = epic("bd-normal"), epic("bd-emergency", priority=0)
        substrate = bead("bd-substrate", priority=2)
        host = bead("bd-host", status="blocked", blocks_on=[substrate["id"]])
        normal_child = bead("bd-normal.1", parent=normal["id"])
        emergency_child = bead("bd-emergency.1", parent=emergency["id"], status="blocked", blocks_on=[host["id"]])
        rows = [normal, emergency, normal_child, emergency_child, host, substrate]
        payload = ranking.rank(snapshot(rows, [status(normal, 1, 0), status(emergency, 1, 0)]),
                               now=NOW, scope_epics=[normal["id"]])
        self.assertEqual(queue_ids(payload), [substrate["id"], normal_child["id"]])
        self.assertEqual(payload["candidates"][0]["outcome"], emergency["id"])
        self.assertIn("prerequisite", payload["candidates"][0]["reason"])

    def test_scoped_run_keeps_p0_child_frontier_under_an_ordinary_epic(self):
        normal, outside = epic("bd-normal"), epic("bd-outside")
        normal_child = bead("bd-normal.1", parent=normal["id"])
        substrate = bead("bd-substrate")
        emergency = bead("bd-outside.1", parent=outside["id"], priority=0,
                         status="blocked", blocks_on=[substrate["id"]])
        payload = ranking.rank(snapshot([normal, outside, normal_child, substrate, emergency],
                               [status(normal, 1, 0), status(outside, 1, 0)]),
                               now=NOW, scope_epics=[normal["id"]])
        self.assertEqual(queue_ids(payload), [substrate["id"], normal_child["id"]])
        self.assertEqual(payload["candidates"][0]["outcome"], emergency["id"])
        self.assertIn("P0 task", payload["candidates"][0]["reason"])

    def test_scoped_run_keeps_standalone_p0_frontier_and_deduplicates_shared_substrate(self):
        normal, outside = epic("bd-normal"), epic("bd-outside")
        normal_child, substrate = bead("bd-normal.1", parent=normal["id"]), bead("bd-substrate")
        emergency = bead("bd-emergency", priority=0, status="blocked", blocks_on=[substrate["id"]])
        other_emergency = bead("bd-outside.1", parent=outside["id"], priority=0,
                               status="blocked", blocks_on=[substrate["id"]])
        payload = ranking.rank(snapshot([normal, outside, normal_child, substrate, emergency, other_emergency],
                               [status(normal, 1, 0), status(outside, 1, 0)]),
                               now=NOW, scope_epics=[normal["id"]])
        self.assertEqual(queue_ids(payload), [substrate["id"], normal_child["id"]])
        self.assertEqual(payload["candidates"][0]["outcome"], emergency["id"])
        self.assertIn("P0 task", payload["candidates"][0]["reason"])

    def test_p0_urgency_does_not_cross_unknown_held_or_foreign_owned_gates(self):
        normal, substrate = epic("bd-normal"), bead("bd-substrate")
        normal_child = bead("bd-normal.1", parent=normal["id"])
        for gate in (None,
                     bead("bd-gate", status="blocked", labels=["no-dispatch"], blocks_on=[substrate["id"]]),
                     bead("bd-gate", status="blocked", assignee="coordinator:foreign", blocks_on=[substrate["id"]])):
            with self.subTest(gate=gate):
                emergency = bead("bd-emergency", priority=0, status="blocked", blocks_on=["bd-gate"])
                rows = [normal, normal_child, substrate, emergency, *([gate] if gate else [])]
                payload = ranking.rank(snapshot(rows, [status(normal, 1, 0)]), now=NOW, scope_epics=[normal["id"]])
                self.assertEqual(queue_ids(payload), [normal_child["id"]])
        # A P0 record which is itself a hard-held gate cannot lend urgency.
        emergency = bead("bd-emergency", priority=0, status="blocked", labels=["no-dispatch"],
                         blocks_on=[substrate["id"]])
        payload = ranking.rank(snapshot([normal, normal_child, substrate, emergency], [status(normal, 1, 0)]),
                               now=NOW, scope_epics=[normal["id"]])
        self.assertEqual(queue_ids(payload), [normal_child["id"]])

    def test_three_members_use_one_lane_and_cannot_be_redispatched(self):
        owner = "coordinator:owner"
        anchor = bead("bd-adapter", status="blocked", assignee=owner)
        members = [anchor, bead("bd-terminal", status="in_progress", assignee=owner),
                   bead("bd-stderr", status="open", assignee=owner)]
        manifest = {"schema": "beads-delivery/v1", "anchor": anchor["id"],
                    "members": [item["id"] for item in members], "owner": owner,
                    "outcome": "bd-runtime", "base_branch": "main", "packet": "Current adapter corrections"}
        anchor["metadata"] = {"delivery": manifest}
        for member in members[1:]:
            member["metadata"] = {"delivery_anchor": anchor["id"]}
        unrelated = bead("bd-calendar")
        payload = ranking.rank(snapshot([*members, unrelated], kv=live_registry(owner)), now=NOW, coordinator=owner)
        self.assertEqual(payload["capacity"]["occupied"], 0)
        self.assertEqual(payload["capacity"]["owned"], 1)
        self.assertEqual(queue_ids(payload), ["bd-calendar"])

    def test_independent_packages_keep_separate_capacity_and_ownership(self):
        rows = []
        for name, owner in (("runtime", "coordinator:a"), ("calendar", "coordinator:b")):
            row = bead(f"bd-{name}", status="in_progress", assignee=owner)
            row["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": row["id"],
                "members": [row["id"]], "owner": owner, "outcome": row["id"],
                "base_branch": "main", "packet": "Accepted outcome"}}
            rows.append(row)
        payload = ranking.rank(snapshot(rows), now=NOW)
        self.assertEqual(payload["capacity"]["occupied"], 2)
        self.assertEqual(payload["capacity"]["by_assignee"], {"coordinator:a": 1, "coordinator:b": 1})

    def test_runnable_shared_substrate_advances_two_blocked_outcomes_once(self):
        one, two = epic("bd-one", labels=["focus"]), epic("bd-two")
        runtime = bead("bd-runner", status="blocked", blocks_on=["bd-session"])
        session = bead("bd-session")
        rows = [one, two, runtime, session,
                bead("bd-one.1", parent=one["id"], status="blocked", blocks_on=[runtime["id"]]),
                bead("bd-two.1", parent=two["id"], status="blocked", blocks_on=[runtime["id"]]),
                bead("bd-easy")]
        payload = ranking.rank(snapshot(rows, [status(one, 2, 1), status(two, 2, 1)]), now=NOW)
        self.assertEqual(queue_ids(payload)[0], session["id"])
        self.assertEqual(queue_ids(payload).count(session["id"]), 1)

    def test_held_prerequisite_does_not_authorize_dependent_dispatch(self):
        top = epic("bd-feature", labels=["focus"])
        hold = bead("bd-perception", status="blocked", labels=["no-dispatch"], blocks_on=["bd-probe"])
        rows = [top, hold, bead("bd-probe"),
                bead("bd-feature.1", parent=top["id"], status="blocked", blocks_on=[hold["id"]])]
        payload = ranking.rank(snapshot(rows, [status(top, 2, 1)]), now=NOW, scope_epics=[top["id"]])
        self.assertEqual(queue_ids(payload), [])
        self.assertEqual(payload["held"][0]["id"], top["id"])


class FinishFirstOrderingTests(unittest.TestCase):
    def test_nearly_done_epic_outranks_untouched_epic_of_same_priority(self) -> None:
        near, fresh = epic("bd-near"), epic("bd-fresh", created="2026-08-01T00:00:00Z")
        live = [near, fresh, bead("bd-near.9", parent="bd-near"), bead("bd-fresh.1", parent="bd-fresh")]
        epics = [status(near, total=10, closed=9), status(fresh, total=3, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-near.9", "bd-fresh.1"])
        self.assertEqual(payload["focus"][0]["state"], "started")

    def test_fewer_remaining_beads_wins_between_started_epics(self) -> None:
        big, small = epic("bd-big"), epic("bd-small")
        live = [big, small, bead("bd-big.1", parent="bd-big"), bead("bd-small.1", parent="bd-small")]
        epics = [status(big, total=20, closed=15), status(small, total=4, closed=2)]

        self.assertEqual(queue_ids(ranking.rank(snapshot(live, epics), now=NOW)), ["bd-small.1", "bd-big.1"])

    def test_epic_holding_a_lane_keeps_it_against_an_idle_epic_one_level_higher(self) -> None:
        held, urgent_new = epic("bd-held", priority=2), epic("bd-new", priority=1)
        live = [
            held, urgent_new,
            bead("bd-held.1", parent="bd-held", status="in_progress", assignee="coordinator:a"),
            bead("bd-held.2", parent="bd-held"),
            bead("bd-new.1", parent="bd-new"),
        ]
        epics = [status(held, total=4, closed=1), status(urgent_new, total=2, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-held.2", "bd-new.1"])

    def test_nearer_completion_beats_holding_a_lane_at_equal_effective_priority(self) -> None:
        # Butlers 2026-10-27: an active P2 epic at 0/5 sat above a P1 epic one
        # bead from done. A free lane should finish the P1 epic first.
        held, near = epic("bd-held", priority=2), epic("bd-near", priority=1)
        live = [
            held, near,
            bead("bd-held.1", parent="bd-held", status="in_progress", assignee="coordinator:a"),
            bead("bd-held.2", parent="bd-held"),
            bead("bd-near.5", parent="bd-near"),
        ]
        epics = [status(held, total=5, closed=0), status(near, total=5, closed=4)]

        self.assertEqual(queue_ids(ranking.rank(snapshot(live, epics), now=NOW)), ["bd-near.5", "bd-held.2"])

    def test_idle_epic_two_levels_higher_outranks_an_epic_holding_a_lane(self) -> None:
        low, high = epic("bd-low", priority=3), epic("bd-high", priority=1)
        live = [
            low, high,
            bead("bd-low.1", parent="bd-low", status="in_progress", assignee="coordinator:a"),
            bead("bd-low.2", parent="bd-low"),
            bead("bd-high.1", parent="bd-high"),
        ]
        epics = [status(low, total=3, closed=1), status(high, total=4, closed=0)]

        self.assertEqual(queue_ids(ranking.rank(snapshot(live, epics), now=NOW)), ["bd-high.1", "bd-low.2"])

    def test_children_of_a_p0_epic_are_urgent(self) -> None:
        busy, fire = epic("bd-busy", priority=3), epic("bd-fire", priority=0)
        live = [
            busy, fire,
            bead("bd-busy.1", parent="bd-busy", status="in_progress", assignee="coordinator:a"),
            bead("bd-busy.2", parent="bd-busy"),
            bead("bd-fire.1", parent="bd-fire", priority=2),
        ]
        epics = [status(busy, total=3, closed=1), status(fire, total=1, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW, scope_epics=["bd-busy"])

        self.assertEqual(queue_ids(payload), ["bd-fire.1", "bd-busy.2"])
        self.assertEqual(payload["candidates"][0]["reason"], "urgent: under P0 epic bd-fire")

    def test_review_in_flight_counts_as_progress(self) -> None:
        reviewing, fresh = epic("bd-rev"), epic("bd-fresh")
        live = [
            reviewing, fresh,
            bead("bd-rev.1", parent="bd-rev", status="blocked", external_ref="gh-pr:4", blocks_on=["bd-rev-task"]),
            bead("bd-rev.2", parent="bd-rev"),
            bead("bd-fresh.1", parent="bd-fresh"),
        ]
        epics = [status(reviewing, total=3, closed=0), status(fresh, total=2, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-rev.2", "bd-fresh.1"])
        self.assertEqual(payload["focus"][0]["state"], "in-review")

    def test_focus_pin_reaches_sub_epics(self) -> None:
        parent = epic("bd-p", priority=3, labels=["focus"])
        child = bead("bd-p.1", parent="bd-p", issue_type="epic", priority=3)
        other = epic("bd-o", priority=1)
        live = [parent, child, other, bead("bd-p.1.1", parent="bd-p.1"), bead("bd-o.1", parent="bd-o")]
        epics = [status(parent, 1, 0), status(child, 1, 0), status(other, 1, 0)]

        self.assertEqual(queue_ids(ranking.rank(snapshot(live, epics), now=NOW)), ["bd-p.1.1", "bd-o.1"])

    def test_priority_beats_progress_among_idle_epics(self) -> None:
        low, high = epic("bd-low", priority=3), epic("bd-high", priority=1)
        live = [low, high, bead("bd-low.1", parent="bd-low"), bead("bd-high.1", parent="bd-high")]
        epics = [status(low, total=5, closed=4), status(high, total=6, closed=0)]

        self.assertEqual(queue_ids(ranking.rank(snapshot(live, epics), now=NOW)), ["bd-high.1", "bd-low.1"])

    def test_focus_label_pins_an_epic_ahead_of_everything_but_urgent(self) -> None:
        pinned, active = epic("bd-pin", priority=3, labels=["focus"]), epic("bd-act", priority=1)
        live = [
            pinned, active,
            bead("bd-act.1", parent="bd-act", status="in_progress", assignee="coordinator:a"),
            bead("bd-act.2", parent="bd-act"),
            bead("bd-pin.1", parent="bd-pin"),
            bead("bd-fire", priority=0),
        ]
        epics = [status(pinned, total=3, closed=0), status(active, total=3, closed=1)]

        payload = ranking.rank(snapshot(live, epics), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-fire", "bd-pin.1", "bd-act.2"])

    def test_standalone_bead_ranks_as_a_one_bead_outcome_by_priority(self) -> None:
        idle = epic("bd-idle", priority=2)
        live = [idle, bead("bd-idle.1", parent="bd-idle"), bead("bd-solo-p1", priority=1), bead("bd-solo-p3", priority=3)]
        epics = [status(idle, total=4, closed=1)]

        self.assertEqual(
            queue_ids(ranking.rank(snapshot(live, epics), now=NOW)),
            ["bd-solo-p1", "bd-idle.1", "bd-solo-p3"],
        )

    def test_standalone_bead_and_untouched_epic_with_one_bead_left_compare(self) -> None:
        # Butlers 2026-10-27: mismatched key shapes raised TypeError on this tie.
        solo = bead("bd-solo", created="2026-08-01T00:00:00Z")
        fresh = epic("bd-fresh")
        live = [solo, fresh, bead("bd-fresh.1", parent="bd-fresh")]

        payload = ranking.rank(snapshot(live, [status(fresh, total=1, closed=0)]), now=NOW)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(queue_ids(payload), ["bd-solo", "bd-fresh.1"])

    def test_ready_prerequisite_outside_the_epic_inherits_the_epic_rank(self) -> None:
        near, other = epic("bd-near", priority=1), epic("bd-other", priority=3)
        live = [
            near, other,
            bead("bd-near.5", parent="bd-near", blocks_on=["bd-other.2"]),
            bead("bd-other.1", parent="bd-other"),
            bead("bd-other.2", parent="bd-other"),
        ]
        epics = [status(near, total=5, closed=4), status(other, total=8, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW)

        self.assertEqual(queue_ids(payload)[0], "bd-other.2")
        self.assertIn("prerequisite for epic bd-near", payload["candidates"][0]["reason"])

    def test_within_an_epic_unblocking_beads_go_first(self) -> None:
        parent = epic("bd-e")
        live = [parent, bead("bd-e.1", parent="bd-e"), bead("bd-e.2", parent="bd-e", dependents=3)]

        payload = ranking.rank(snapshot(live, [status(parent, total=2, closed=0)]), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-e.2", "bd-e.1"])


class LaneCapTests(unittest.TestCase):
    def test_epic_lane_cap_admits_next_epic_before_overflow(self) -> None:
        wide, second = epic("bd-wide", priority=1), epic("bd-second", priority=2)
        live = [wide, second] + [bead(f"bd-wide.{n}", parent="bd-wide") for n in range(1, 5)]
        live.append(bead("bd-second.1", parent="bd-second"))
        epics = [status(wide, total=4, closed=0), status(second, total=1, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW, max_lanes_per_epic=2)

        self.assertEqual(
            queue_ids(payload),
            ["bd-wide.1", "bd-wide.2", "bd-second.1", "bd-wide.3", "bd-wide.4"],
        )
        self.assertTrue(payload["candidates"][3]["reason"].endswith("over epic lane cap"))

    def test_bead_queued_by_another_outcome_does_not_use_up_the_epic_share(self) -> None:
        first, second = epic("bd-a", priority=1), epic("bd-b", priority=2)
        live = [
            first, second,
            bead("bd-a.1", parent="bd-a", blocks_on=["bd-b.1"]),
            bead("bd-b.1", parent="bd-b"),
            bead("bd-b.2", parent="bd-b"),
        ]
        epics = [status(first, total=2, closed=1), status(second, total=3, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW, max_lanes_per_epic=1)

        self.assertEqual(queue_ids(payload), ["bd-b.1", "bd-b.2"])
        self.assertNotIn("over epic lane cap", payload["candidates"][1]["reason"])

    def test_lanes_already_held_count_against_the_epic_cap(self) -> None:
        wide, second = epic("bd-wide"), epic("bd-second")
        live = [
            wide, second,
            bead("bd-wide.1", parent="bd-wide", status="in_progress", assignee="coordinator:a"),
            bead("bd-wide.2", parent="bd-wide", status="in_progress", assignee="coordinator:b"),
            bead("bd-wide.3", parent="bd-wide"),
            bead("bd-second.1", parent="bd-second"),
        ]
        epics = [status(wide, total=3, closed=0), status(second, total=1, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW, max_lanes_per_epic=2)

        self.assertEqual(queue_ids(payload), ["bd-second.1", "bd-wide.3"])


class ParallelCoordinatorTests(unittest.TestCase):
    def live_with_three_claims(self):
        parent = epic("bd-e")
        return [
            parent,
            bead("bd-e.1", parent="bd-e", status="in_progress", assignee="coordinator:a"),
            bead("bd-e.2", parent="bd-e", status="in_progress", assignee="coordinator:b"),
            bead("bd-x", status="in_progress", assignee="coordinator:dead"),
            bead("bd-e.3", parent="bd-e"),
            bead("bd-y"),
        ], [status(parent, total=3, closed=0)]

    def test_capacity_is_global_across_coordinators_and_reads_the_kv_budget(self) -> None:
        live, epics = self.live_with_three_claims()
        kv = {
            "beads.max_lanes": "5",
            "beads.coordinator.coordinator:a": json.dumps({"expires_at": "2026-10-27T05:00:00Z"}),
            "beads.coordinator.coordinator:b": "2026-10-27T04:30:00Z",
            "beads.coordinator.coordinator:dead": json.dumps({"expires_at": "2026-10-27T03:00:00Z"}),
        }

        payload = ranking.rank(snapshot(live, epics, kv), now=NOW, coordinator="coordinator:a")

        self.assertEqual(payload["capacity"]["budget"], 5)
        self.assertEqual(payload["capacity"]["budget_source"], "kv")
        # The dead claim is an hour past expiry: recoverable, no longer a lane.
        self.assertEqual(payload["capacity"]["occupied"], 2)
        self.assertEqual(payload["capacity"]["free"], 3)
        self.assertEqual(payload["unowned_claims"], ["bd-x"])
        self.assertEqual(payload["recoverable_claims"],
                         [{"assignee": "coordinator:dead", "id": "bd-x", "kind": "implementation", "pr": None}])
        self.assertEqual([entry["id"] for entry in payload["coordinators"]], ["coordinator:a", "coordinator:b"])
        self.assertEqual(payload["self"], {"id": "coordinator:a", "lanes": 1, "live": True, "registered": True})

    def test_dead_claim_reserves_its_lane_only_until_the_stall_threshold(self) -> None:
        live, epics = self.live_with_three_claims()
        dead = next(row for row in live if row["id"] == "bd-x")
        beat = "[beads-heartbeat]\nowner=coordinator:dead\nlast_heartbeat_at={}\n[/beads-heartbeat]"

        def plan(expiry=None):
            kv = {"beads.max_lanes": "5"}
            if expiry:
                kv["beads.coordinator.coordinator:dead"] = json.dumps({"expires_at": expiry})
            payload = ranking.rank(snapshot(live, epics, kv), now=NOW)
            return payload["capacity"]["occupied"], [entry["id"] for entry in payload["recoverable_claims"]]

        # Expired ten minutes ago: still inside the threshold.
        self.assertEqual(plan("2026-10-27T03:50:00Z"), (3, []))
        # Long expired with no heartbeat at all: a missing heartbeat is no veto.
        self.assertEqual(plan("2026-10-27T03:00:00Z"), (2, ["bd-x"]))
        # A fresh or malformed heartbeat keeps the claim.
        dead["notes"] = beat.format("2026-10-27T03:55:00Z")
        self.assertEqual(plan("2026-10-27T03:00:00Z"), (3, []))
        dead["notes"] = "[beads-heartbeat] garbled"
        self.assertEqual(plan("2026-10-27T03:00:00Z"), (3, []))
        # Never registered: the bead's last update is the evidence.
        dead["notes"] = None
        self.assertEqual(plan(), (3, []))
        dead["updated_at"] = "2026-10-27T03:45:00Z"
        self.assertEqual(plan(), (3, []))
        dead["updated_at"] = "2026-10-27T03:00:00Z"
        self.assertEqual(plan(), (2, ["bd-x"]))

    def test_live_registration_extra_lanes_count_as_occupied(self) -> None:
        live, epics = self.live_with_three_claims()
        kv = {
            "beads.coordinator.coordinator:a": json.dumps({"expires_at": "2026-10-27T05:00:00Z", "extra_lanes": 2}),
            "beads.coordinator.coordinator:dead": json.dumps({"expires_at": "2026-10-27T03:00:00Z", "extra_lanes": 5}),
        }

        payload = ranking.rank(snapshot(live, epics, kv), now=NOW, lanes=6)

        self.assertEqual(payload["capacity"]["occupied"], 4)
        self.assertEqual(payload["capacity"]["by_assignee"]["coordinator:a"], 3)
        self.assertEqual(payload["capacity"]["free"], 2)

    def test_flag_overrides_kv_budget_and_free_never_goes_negative(self) -> None:
        live, epics = self.live_with_three_claims()

        payload = ranking.rank(snapshot(live, epics, {"beads.max_lanes": "9"}), now=NOW, lanes=2)

        self.assertEqual(payload["capacity"]["budget_source"], "flag")
        self.assertEqual(payload["capacity"]["free"], 0)

    def test_queue_does_not_depend_on_tracker_row_order(self) -> None:
        live, epics = self.live_with_three_claims()
        live += [bead("bd-z", priority=1), bead("bd-w", priority=3)]

        forward = ranking.rank(snapshot(live, epics), now=NOW, coordinator="coordinator:a")
        backward = ranking.rank(snapshot(list(reversed(live)), epics), now=NOW, coordinator="coordinator:b")

        self.assertEqual(forward["candidates"], backward["candidates"])

    def test_human_claims_do_not_use_agent_lanes(self) -> None:
        live = [bead("bd-h", status="in_progress", assignee="tze"), bead("bd-c", status="in_progress", assignee="coordinator:a")]

        capacity = ranking.rank(snapshot(live), now=NOW, lanes=2)["capacity"]

        self.assertEqual((capacity["occupied"], capacity["other_claims"], capacity["free"]), (1, 1, 1))

    def test_open_beads_keeping_an_assignee_are_queued_stranded_or_excluded(self) -> None:
        kv = {"beads.coordinator.coordinator:peer": "2026-10-27T05:00:00Z"}
        live = [
            bead("bd-mine", assignee="coordinator:me"),
            bead("bd-dead", assignee="coordinator:gone"),
            bead("bd-peer", assignee="coordinator:peer"),
            bead("bd-human", assignee="tze"),
        ]

        payload = ranking.rank(snapshot(live, kv=kv, ready=live), now=NOW, coordinator="coordinator:me")

        self.assertEqual(queue_ids(payload), ["bd-mine"])
        self.assertEqual(payload["stranded"], ["bd-dead"])
        self.assertEqual(payload["excluded"]["claimed"], 2)

    def test_blocked_beads_of_dead_coordinators_are_orphaned_by_kind(self) -> None:
        kv = {"beads.coordinator.coordinator:peer": "2026-10-27T05:00:00Z"}
        live = [
            bead("bd-gate", status="blocked", assignee="coordinator:01a13679-0000-4000-8000-000000000000"),
            bead("bd-pr", status="blocked", assignee="coordinator:gone", external_ref="gh-pr:7"),
            bead("bd-rev", status="blocked", assignee="coordinator:gone", labels=["pr-review-task"]),
            bead("bd-live", status="blocked", assignee="coordinator:peer"),
            bead("bd-human", status="blocked", assignee="tze"),
            bead("bd-free", status="blocked"),
        ]

        payload = ranking.rank(snapshot(live, kv=kv, ready=[]), now=NOW)

        self.assertEqual(
            [(entry["id"], entry["kind"]) for entry in payload["orphaned_blocked"]],
            [("bd-gate", "gate"), ("bd-pr", "pr"), ("bd-rev", "pr")],
        )
        self.assertEqual(payload["orphaned_blocked"][0]["assignee"], live[0]["assignee"])

    def test_unregistered_coordinator_is_reported_in_self(self) -> None:
        live, epics = self.live_with_three_claims()

        payload = ranking.rank(snapshot(live, epics), now=NOW, coordinator="coordinator:new")

        self.assertEqual(payload["self"]["registered"], False)
        self.assertEqual(payload["self"]["live"], False)

    def test_scope_limits_non_urgent_work_and_distinguishes_scope_from_repository(self) -> None:
        mine, theirs = epic("bd-mine"), epic("bd-theirs")
        live = [
            mine, theirs,
            bead("bd-mine.1", parent="bd-mine"),
            bead("bd-theirs.1", parent="bd-theirs"),
            bead("bd-fire", priority=0),
        ]
        epics = [status(mine, total=1, closed=0), status(theirs, total=1, closed=0)]

        payload = ranking.rank(snapshot(live, epics), now=NOW, scope_epics=["bd-mine"])

        self.assertEqual(queue_ids(payload), ["bd-fire", "bd-mine.1"])
        self.assertEqual(payload["totals"]["ready"], 3)
        self.assertEqual(payload["totals"]["ready_in_scope"], 1)


class ExclusionTests(unittest.TestCase):
    def test_epics_claimed_and_needs_shaping_beads_never_enter_the_queue(self) -> None:
        parent, childless = epic("bd-e"), epic("bd-empty", priority=1)
        shaping = bead("bd-e.2", parent="bd-e", labels=["needs-shaping"])
        claimed = bead("bd-e.3", parent="bd-e", assignee="coordinator:a")
        live = [parent, childless, bead("bd-e.1", parent="bd-e"), shaping, claimed]
        ready = [parent, childless, live[2], shaping, claimed]
        epics = [status(parent, total=3, closed=0), status(childless, total=0, closed=0)]

        kv = {"beads.coordinator.coordinator:a": "2026-10-27T05:00:00Z"}

        payload = ranking.rank(snapshot(live, epics, kv, ready=ready), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-e.1"])
        self.assertEqual(payload["focus"][0]["needs_shaping"], ["bd-e.2"])
        self.assertEqual(payload["excluded"]["claimed"], 1)
        self.assertEqual(payload["childless_epics"], ["bd-empty"])

    def test_a_lane_filed_follow_up_awaiting_shaping_is_held_from_its_author(self) -> None:
        # aib-fn1: a lane files a standalone follow-up without a complete packet
        # and labels it needs-shaping; neither it nor another lane may claim it.
        follow_up = bead("bd-f", labels=["needs-shaping"])
        ordinary = bead("bd-g")
        payload = ranking.rank(snapshot([follow_up, ordinary], [], {}, ready=[follow_up, ordinary]),
                               now=NOW, coordinator="coordinator:a")
        self.assertEqual(queue_ids(payload), ["bd-g"])
        self.assertEqual(payload["excluded"]["needs_shaping_standalone"], ["bd-f"])

    def test_review_beads_stay_separate_without_hiding_a_claimed_epics_children(self) -> None:
        led = epic("bd-led", labels=["team-coordination"])
        led["status"], led["assignee"] = "in_progress", "coordinator:a"
        live = [
            led,
            bead("bd-led.1", parent="bd-led"),
            bead("bd-rev", labels=["pr-review-task"]),
            bead("bd-fix", labels=["review-correction"]),
            bead("bd-solo"),
        ]

        payload = ranking.rank(snapshot(live, [status(led, total=1, closed=0)]), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-led.1", "bd-solo"])
        self.assertEqual(payload["excluded"]["review"], 2)
        self.assertNotIn("team_lead", payload["excluded"])

    def test_no_dispatch_and_local_state_beads_stay_out_of_the_queue(self) -> None:
        live = [
            bead("bd-trial", priority=1, labels=["no-dispatch"]),
            bead("bd-local", priority=1, labels=["scope:local-state"]),
            bead("bd-solo"),
        ]

        payload = ranking.rank(snapshot(live), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-solo"])
        self.assertEqual(payload["excluded"]["no_dispatch"], 1)
        self.assertEqual(payload["excluded"]["local_state"], ["bd-local"])

    def test_epic_claimed_for_shaping_keeps_its_ready_children_queued(self) -> None:
        held = epic("bd-shape")
        held["status"], held["assignee"] = "in_progress", "coordinator:a"
        live = [held, bead("bd-shape.1", parent="bd-shape")]

        payload = ranking.rank(snapshot(live, [status(held, total=1, closed=0)]), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-shape.1"])
        self.assertNotIn("team_lead", payload["excluded"])

    def test_epic_with_every_child_closed_is_closable_not_held(self) -> None:
        done = epic("bd-done")

        payload = ranking.rank(snapshot([done], [status(done, total=3, closed=3)]), now=NOW)

        self.assertEqual(payload["closable_epics"], ["bd-done"])
        self.assertEqual(payload["held_total"], 0)

    def test_owner_gated_child_without_dependency_is_held_on_itself(self) -> None:
        gated = epic("bd-gate")
        live = [gated, bead("bd-gate.2", parent="bd-gate", status="blocked", assignee="tze")]

        payload = ranking.rank(snapshot(live, [status(gated, total=2, closed=1)]), now=NOW)

        self.assertEqual(payload["held"][0]["waiting_on"], [{"assignee": "tze", "id": "bd-gate.2", "status": "blocked"}])

    def test_epic_whose_only_ready_beads_need_shaping_is_held(self) -> None:
        parent = epic("bd-e")
        shaping = bead("bd-e.2", parent="bd-e", labels=["needs-shaping"])
        live = [parent, shaping]

        payload = ranking.rank(snapshot(live, [status(parent, total=2, closed=1)]), now=NOW)

        self.assertEqual(payload["held"][0]["reason"], "only needs-shaping beads are unblocked")
        self.assertEqual(payload["held"][0]["needs_shaping"], ["bd-e.2"])

    def test_started_epic_with_only_blocked_work_is_held_with_its_blockers(self) -> None:
        stuck = epic("bd-stuck", priority=1)
        live = [
            stuck,
            bead("bd-stuck.3", parent="bd-stuck", status="blocked", blocks_on=["bd-ext"]),
            bead("bd-ext", status="in_progress", assignee="coordinator:b"),
        ]

        payload = ranking.rank(snapshot(live, [status(stuck, total=3, closed=2)]), now=NOW)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["held_total"], 1)
        self.assertEqual(
            payload["held"][0]["waiting_on"],
            [{"assignee": "coordinator:b", "id": "bd-ext", "status": "in_progress"}],
        )

    def test_epic_waiting_only_on_review_is_busy_not_held(self) -> None:
        busy = epic("bd-busy")
        live = [
            busy,
            bead("bd-busy.1", parent="bd-busy", status="blocked", external_ref="gh-pr:7", blocks_on=["bd-review"]),
            bead("bd-review", status="in_progress", assignee="coordinator:a", labels=["pr-review-task"]),
        ]

        payload = ranking.rank(snapshot(live, [status(busy, total=2, closed=1)]), now=NOW)

        self.assertEqual(payload["held_total"], 0)


class FailClosedTests(unittest.TestCase):
    def test_parentless_rows_are_standalone_not_fatal(self) -> None:
        # bd 1.3 omits `parent` on beads that have none.
        row = bead("bd-1")
        del row["parent"]

        self.assertEqual(queue_ids(ranking.rank(snapshot([row]), now=NOW)), ["bd-1"])

    def test_record_without_an_id_is_fatal(self) -> None:
        for scope in ("live", "ready", "epics"):
            with self.subTest(scope=scope):
                snap = snapshot([bead("bd-1")], ready=[bead("bd-ready-only")])
                snap[scope].append({"title": "no id"})
                error = {"code": "invalid-record", "scope": scope}
                payload = ranking.rank(snap, now=NOW)
                self.assertEqual(payload["errors"], [error])
                self.assertEqual(payload["queue"], [])
                self.assertEqual(payload["candidates"], [])
                with patch.object(ranking, "bd_json", side_effect=[(value, None) for value in snap.values()]) as tracker:
                    self.assertEqual(ranking.collect(Path.cwd(), with_dependencies=True), (None, error))
                self.assertEqual(tracker.call_count, 4)

    def test_wrong_payload_shape_is_fatal(self) -> None:
        snap = snapshot([bead("bd-1")])
        snap["kv"] = []

        error = {"code": "invalid-json", "scope": "kv"}
        self.assertEqual(ranking.rank(snap, now=NOW)["errors"], [error])
        with patch.object(ranking, "bd_json", side_effect=[(value, None) for value in snap.values()]) as tracker:
            self.assertEqual(ranking.collect(Path.cwd(), with_dependencies=True), (None, error))
        self.assertEqual(tracker.call_count, 4)


class RegistryTests(unittest.TestCase):
    def test_malformed_or_expired_registration_is_never_live(self) -> None:
        kv = {
            "beads.coordinator.ok": json.dumps({"expires_at": "2026-10-27T05:00:00Z"}),
            "beads.coordinator.expired": "2026-10-27T03:59:59Z",
            "beads.coordinator.garbage": "{not json",
            "beads.coordinator.naive": "2026-10-27T05:00:00",
            "memory.unrelated": "x",
        }

        self.assertEqual(registry.live_coordinator_ids(kv, NOW), {"ok"})
        self.assertEqual(set(registry.registrations(kv, NOW)), {"ok", "expired", "garbage", "naive"})

    def test_registration_too_far_ahead_is_not_live(self) -> None:
        kv = {"beads.coordinator.hog": "2026-10-28T04:00:00Z"}

        self.assertEqual(registry.live_coordinator_ids(kv, NOW), set())

    def test_writer_refuses_a_live_id_at_startup_and_skips_fresh_renewals(self) -> None:
        from datetime import timedelta

        wake = timedelta(minutes=30)
        fresh = {"beads.coordinator.coordinator:a": json.dumps({"expires_at": "2026-10-27T04:45:00Z"})}
        lapsing = {"beads.coordinator.coordinator:a": json.dumps({"expires_at": "2026-10-27T04:20:00Z"})}

        self.assertEqual(registry.plan_registration(fresh, "coordinator:a", now=NOW, wake=wake, extra_lanes=0, startup=True), ("refuse", None))
        self.assertEqual(registry.plan_registration(fresh, "coordinator:a", now=NOW, wake=wake, extra_lanes=0, startup=False), ("skip", None))
        self.assertEqual(
            registry.plan_registration(lapsing, "coordinator:a", now=NOW, wake=wake, extra_lanes=0, startup=False),
            ("write", {"expires_at": "2026-10-27T04:45:00Z"}),
        )
        self.assertEqual(
            registry.plan_registration(fresh, "coordinator:a", now=NOW, wake=wake, extra_lanes=2, startup=False),
            ("write", {"expires_at": "2026-10-27T04:45:00Z", "extra_lanes": 2}),
        )

    def test_new_ids_are_prefixed_dated_and_distinct(self) -> None:
        first, second = registry.new_id("claude", NOW), registry.new_id("claude", NOW)

        self.assertRegex(first, r"^coordinator:claude-20261027-[0-9a-f]{8}$")
        self.assertNotEqual(first, second)

    def test_budget_falls_back_to_default_on_bad_values(self) -> None:
        for raw in ("0", "-2", "many", None):
            with self.subTest(raw=raw):
                kv = {} if raw is None else {"beads.max_lanes": raw}
                self.assertEqual(registry.lane_budget(kv), (registry.DEFAULT_LANE_BUDGET, "default"))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, fixture: dict[str, object], *, review_state=None) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "repo").mkdir()
            (root / "bin").mkdir()
            fixture_path, calls_path = root / "fixture.json", root / "calls.jsonl"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            fake = root / "bin" / "bd"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent(
                    """
                    import json, os, sys
                    fixture = json.load(open(os.environ["RANK_FIXTURE"], encoding="utf-8"))
                    argv = sys.argv[3:]
                    with open(os.environ["RANK_CALLS"], "a", encoding="utf-8") as calls:
                        calls.write(json.dumps(argv) + "\\n")
                    key = argv[0]
                    if key == "list" and "--id" in argv:
                        key = "dependency-list"
                    if key in fixture.get("fail", []):
                        raise SystemExit(1)
                    if key == "dependency-list":
                        if fixture.get("invalid_dependency_json"):
                            print("{invalid json")
                            raise SystemExit(0)
                        requested = argv[argv.index("--id") + 1].split(",")
                        rows = fixture.get("dependency-list", [row for issue_id in reversed(requested)
                                    for row in fixture.get("show", {}).get(issue_id, [])])
                    elif key == "show":
                        rows = fixture.get("show", {}).get(argv[1], [])
                    else:
                        rows = fixture[key]
                    print(json.dumps(rows))
                    """
                ),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env = os.environ.copy()
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", RANK_FIXTURE=str(fixture_path), RANK_CALLS=str(calls_path))
            argv = [sys.executable, str(SCRIPT), "--repo-root", str(root / "repo"),
                    "--now", "2026-10-27T04:00:00Z", "--coordinator", "coordinator:a"]
            if review_state is not None:
                review_path = root / "review-state.json"
                review_path.write_text(json.dumps(review_state), encoding="utf-8")
                argv.extend(["--review-state-file", str(review_path)])
            result = subprocess.run(
                argv,
                capture_output=True, text=True, env=env,
            )
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()] if calls_path.exists() else []
        return result, calls

    def fixture(self) -> dict[str, object]:
        parent = epic("bd-e")
        child = bead("bd-e.1", parent="bd-e")
        return {"list": [parent, child], "ready": [child], "epic": [status(parent, 2, 1)], "kv": {}}

    def test_cli_recovers_closed_anchor_for_an_incomplete_package(self):
        owner, anchor_id, member_id = "coordinator:owner", "bd-adapter", "bd-tail"
        anchor = bead(anchor_id, status="closed", assignee=owner)
        anchor["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": anchor_id,
            "members": [anchor_id, member_id], "owner": owner, "outcome": "bd-runtime",
            "base_branch": "main", "packet": "Accepted corrections"}}
        pending = bead(member_id, status="in_progress", assignee=owner)
        pending["metadata"] = {"delivery_anchor": anchor_id}
        result, calls = self.run_cli({"list": [pending], "ready": [], "epic": [], "kv": {},
                                      "show": {anchor_id: [anchor]}})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["capacity"]["occupied"], 1)
        self.assertEqual(payload["candidates"], [])
        self.assertIn(["show", anchor_id, "--json"], calls)

    def test_cli_runs_read_only_queries_and_emits_one_envelope(self) -> None:
        result, calls = self.run_cli(self.fixture())

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "beads-work-queue/v2")
        self.assertEqual(queue_ids(payload), ["bd-e.1"])
        self.assertEqual(
            calls,
            [
                ["list", "--status=open,blocked,in_progress", "--json", "--limit", "0"],
                ["ready", "--json", "--brief", "--limit", "0", "--exclude-type", "epic"],
                ["epic", "status", "--json"],
                ["kv", "list", "--json"],
            ],
        )

    def test_cli_reuses_step_zero_inventory_and_hydrates_only_missing_owner_dependencies(self):
        anchor, review, rows = review_pair()
        gate = bead("bd-old-gate", status="closed")
        review_gate = bead("bd-review-gate", status="closed")
        anchor["dependencies"] = [{"issue_id": anchor["id"], "depends_on_id": gate["id"], "type": "blocks"}]
        review["dependencies"] = [{"issue_id": review["id"], "depends_on_id": review_gate["id"], "type": "blocks"}]
        fixture = {"list": rows, "ready": [], "epic": [], "kv": live_registry("coordinator:a"),
                   "show": {gate["id"]: [gate], review_gate["id"]: [review_gate]}}
        result, calls = self.run_cli(fixture, review_state=normalized((anchor, review)))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([action["kind"] for action in payload["queue"]], ["review"])
        self.assertEqual(calls[-1], [
            "list", "--id", f'{gate["id"]},{review_gate["id"]}', "--all", "--include-gates",
            "--include-ephemeral", "--include-infra", "--include-templates", "--limit", "0", "--json",
        ])
        self.assertEqual(len(calls), 5)

        # Cross two batch boundaries and compare the whole decision with the
        # same frozen records supplied by the former individual-read collector.
        gates = [gate, review_gate, *[bead(f"bd-old-{i:03}", status="closed") for i in range(79)]]
        anchor["dependencies"] = [
            {"issue_id": anchor["id"], "depends_on_id": row["id"], "type": "blocks"} for row in gates
        ]
        fixture["show"] = {row["id"]: [row] for row in gates}
        result, calls = self.run_cli(fixture, review_state=normalized((anchor, review)))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        serial = snapshot(rows, kv=fixture["kv"], ready=[])
        serial["dependency_records"] = sorted(gates, key=lambda row: row["id"])
        self.assertEqual(payload, ranking.rank(serial, now=NOW, coordinator="coordinator:a",
                                               review_state=normalized((anchor, review))))
        batches = [call[2].split(",") for call in calls if call[:2] == ["list", "--id"]]
        self.assertEqual([len(batch) for batch in batches], [40, 40, 1])
        self.assertEqual([item for batch in batches for item in batch], sorted(fixture["show"]))
        self.assertEqual(len(calls), 7)  # Previously four inventory reads plus 81 shows.

    def test_cli_fails_closed_on_queries_and_snapshot_drift(self) -> None:
        inventory = self.fixture()
        inventory["fail"] = ["epic"]
        package = self.fixture()
        package["list"][1]["metadata"] = {"delivery_anchor": "bd-closed-anchor"}
        package["fail"] = ["show"]
        dependency = self.fixture()
        child = dependency["list"][1]
        child["assignee"] = "coordinator:a"
        child["dependencies"] = [{"issue_id": child["id"], "depends_on_id": "bd-old-gate", "type": "blocks"}]
        dependency["fail"] = ["show"]
        dependency["dependency-list"] = []
        drift = self.fixture()
        drift["list"][1]["assignee"] = "coordinator:a"
        drift["list"][1]["dependencies"] = child["dependencies"]
        drift["list"][1]["metadata"] = {"delivery_anchor": "bd-closed-anchor"}
        drift["ready"].append(bead("bd-ready-only"))
        for fixture, code, scope, count in (
            (inventory, "command-failed", "epics", 3),
            (package, "command-failed", "packages", 5),
            (dependency, "command-failed", "admission", 6),
            (drift, "ready-live-drift", "snapshot", 8),
        ):
            with self.subTest(scope=scope):
                result, calls = self.run_cli(fixture, review_state=normalized())
                self.assertEqual(result.returncode, 1, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "fatal")
                self.assertEqual(payload["errors"], [{"code": code, "scope": scope}])
                self.assertEqual(payload["queue"], [])
                self.assertEqual(payload["candidates"], [])
                self.assertEqual(len(calls), count)

        # Process success alone proves neither exact identity nor completeness.
        gate = bead("bd-old-gate", status="closed")
        for batch, shown, failure, code, count in (
            (None, [gate], None, "invalid-dependency", 5),
            ({}, [gate], None, "invalid-dependency", 5),
            ([None], [gate], None, "invalid-dependency", 5),
            ([{"id": None}], [gate], None, "invalid-dependency", 5),
            ([{"id": gate["id"]}], [gate], None, "invalid-dependency", 5),
            ([{**gate, "status": []}], [gate], None, "invalid-dependency", 5),
            ([gate, gate], [gate], None, "invalid-dependency", 5),
            ([bead("bd-foreign", status="closed")], [gate], None, "invalid-dependency", 5),
            ([], [], None, "invalid-dependency", 6),
            ([], {}, None, "invalid-dependency", 6),
            ([], [None], None, "invalid-dependency", 6),
            ([], [{"id": None}], None, "invalid-dependency", 6),
            ([], [{"id": gate["id"]}], None, "invalid-dependency", 6),
            ([], [gate, gate], None, "invalid-dependency", 6),
            ([], [bead("bd-foreign", status="closed")], None, "invalid-dependency", 6),
            ([], [gate], "dependency-list", "command-failed", 5),
            ([], [gate], "invalid-json", "invalid-json", 5),
        ):
            with self.subTest(batch=batch, shown=shown, failure=failure):
                fixture = {**dependency, "dependency-list": batch, "show": {gate["id"]: shown},
                           "fail": [failure], "invalid_dependency_json": failure == "invalid-json"}
                result, calls = self.run_cli(fixture, review_state=normalized())
                self.assertEqual(result.returncode, 1, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["errors"], [{"code": code, "scope": "admission"}])
                self.assertEqual(payload["queue"], [])
                self.assertEqual(payload["candidates"], [])
                self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()
