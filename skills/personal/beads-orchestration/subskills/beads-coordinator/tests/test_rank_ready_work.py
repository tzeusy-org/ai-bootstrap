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
    return [item["id"] for item in payload["queue"]]


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
        self.assertEqual(payload["queue"][0]["reason"], "urgent: under P0 epic bd-fire")

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

        self.assertEqual(payload["status"], "ok")
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
        self.assertIn("prerequisite for epic bd-near", payload["queue"][0]["reason"])

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
        self.assertTrue(payload["queue"][3]["reason"].endswith("over epic lane cap"))

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
        self.assertNotIn("over epic lane cap", payload["queue"][1]["reason"])

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
        self.assertEqual(payload["capacity"]["occupied"], 3)
        self.assertEqual(payload["capacity"]["free"], 2)
        self.assertEqual(payload["unowned_claims"], ["bd-x"])
        self.assertEqual([entry["id"] for entry in payload["coordinators"]], ["coordinator:a", "coordinator:b"])
        self.assertEqual(payload["self"], {"id": "coordinator:a", "lanes": 1, "live": True, "registered": True})

    def test_live_registration_extra_lanes_count_as_occupied(self) -> None:
        live, epics = self.live_with_three_claims()
        kv = {
            "beads.coordinator.coordinator:a": json.dumps({"expires_at": "2026-10-27T05:00:00Z", "extra_lanes": 2}),
            "beads.coordinator.coordinator:dead": json.dumps({"expires_at": "2026-10-27T03:00:00Z", "extra_lanes": 5}),
        }

        payload = ranking.rank(snapshot(live, epics, kv), now=NOW, lanes=6)

        self.assertEqual(payload["capacity"]["occupied"], 5)
        self.assertEqual(payload["capacity"]["by_assignee"]["coordinator:a"], 3)
        self.assertEqual(payload["capacity"]["free"], 1)

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

        self.assertEqual(forward["queue"], backward["queue"])

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

    def test_review_beads_and_team_led_epic_descendants_stay_out_of_the_queue(self) -> None:
        led = epic("bd-led", labels=["team-coordination"])
        led["status"], led["assignee"] = "in_progress", "coordinator:a"
        live = [
            led,
            bead("bd-led.1", parent="bd-led"),
            bead("bd-rev", labels=["pr-review-task"]),
            bead("bd-solo"),
        ]

        payload = ranking.rank(snapshot(live, [status(led, total=1, closed=0)]), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-solo"])
        self.assertEqual(payload["excluded"]["review"], 1)
        self.assertEqual(payload["excluded"]["team_lead"], 1)

    def test_epic_claimed_for_shaping_keeps_its_ready_children_queued(self) -> None:
        held = epic("bd-shape")
        held["status"], held["assignee"] = "in_progress", "coordinator:a"
        live = [held, bead("bd-shape.1", parent="bd-shape")]

        payload = ranking.rank(snapshot(live, [status(held, total=1, closed=0)]), now=NOW)

        self.assertEqual(queue_ids(payload), ["bd-shape.1"])
        self.assertEqual(payload["excluded"]["team_lead"], 0)

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

        self.assertEqual(payload["status"], "empty")
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
        snap = snapshot([bead("bd-1")])
        snap["ready"].append({"title": "no id"})

        payload = ranking.rank(snap, now=NOW)

        self.assertEqual(payload["errors"], [{"code": "invalid-record", "scope": "ready"}])
        self.assertEqual(payload["queue"], [])

    def test_wrong_payload_shape_is_fatal(self) -> None:
        snap = snapshot([bead("bd-1")])
        snap["kv"] = []

        self.assertEqual(ranking.rank(snap, now=NOW)["errors"], [{"code": "invalid-json", "scope": "kv"}])


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
    def run_cli(self, fixture: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
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
                    if key in fixture.get("fail", []):
                        raise SystemExit(1)
                    print(json.dumps(fixture[key]))
                    """
                ),
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env = os.environ.copy()
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", RANK_FIXTURE=str(fixture_path), RANK_CALLS=str(calls_path))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--repo-root", str(root / "repo"), "--now", "2026-10-27T04:00:00Z"],
                capture_output=True, text=True, env=env,
            )
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()] if calls_path.exists() else []
        return result, calls

    def fixture(self) -> dict[str, object]:
        parent = epic("bd-e")
        child = bead("bd-e.1", parent="bd-e")
        return {"list": [parent, child], "ready": [child], "epic": [status(parent, 2, 1)], "kv": {}}

    def test_cli_runs_read_only_queries_and_emits_one_envelope(self) -> None:
        result, calls = self.run_cli(self.fixture())

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "beads-work-queue/v1")
        self.assertEqual(queue_ids(payload), ["bd-e.1"])
        self.assertEqual(
            calls,
            [
                ["list", "--status=open,blocked,in_progress", "--json", "--brief", "--limit", "0"],
                ["ready", "--json", "--brief", "--limit", "0", "--exclude-type", "epic"],
                ["epic", "status", "--json"],
                ["kv", "list", "--json"],
            ],
        )

    def test_cli_fails_closed_on_a_failed_query(self) -> None:
        fixture = self.fixture()
        fixture["fail"] = ["epic"]

        result, _ = self.run_cli(fixture)

        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "fatal")
        self.assertEqual(payload["errors"], [{"code": "command-failed", "scope": "epics"}])


if __name__ == "__main__":
    unittest.main()
