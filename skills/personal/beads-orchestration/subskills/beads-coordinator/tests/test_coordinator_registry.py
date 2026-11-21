#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Behavioral tests for the registry CLI's failure reporting and retries."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("coordinator_registry", SCRIPTS / "coordinator_registry.py")
assert spec is not None and spec.loader is not None
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)

TIMEOUT = "Error: failed to open database: dial tcp dolt.example.invalid:3307: i/o timeout\n"
REFUSED = "Error: dial tcp dolt.example.invalid:3307: connect: connection refused\n"
ID = "coordinator:claude-20261101-deadbeef"


def done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["bd"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeBd:
    """Replay one scripted result per `bd` call and record each call's argv."""

    def __init__(self, *results: subprocess.CompletedProcess[str]) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return self.results.pop(0)


class ClassifyFailureTests(unittest.TestCase):
    def test_codes(self) -> None:
        table = {
            TIMEOUT: "server-timeout",
            "Error: context deadline exceeded": "server-timeout",
            "read tcp: connection reset by peer": "server-timeout",
            "write: broken pipe": "server-timeout",
            "[mysql] packets.go: unexpected EOF": "server-timeout",
            "driver: bad connection": "server-timeout",
            "operation TIMED OUT": "server-timeout",
            REFUSED: "connection-refused",
            "Error: issue not found": "command-failed",
            "": "command-failed",
        }
        for stderr, code in table.items():
            with self.subTest(stderr=stderr):
                self.assertEqual(registry.classify_failure(1, stderr), code)

    def test_a_command_that_never_started_is_a_plain_failure(self) -> None:
        self.assertEqual(registry.classify_failure(None, TIMEOUT), "command-failed")

    def test_detail_is_the_last_line_truncated(self) -> None:
        self.assertEqual(registry.last_line("first\n\n  last line  \n\n"), "last line")
        self.assertEqual(len(registry.last_line("x" * 500)), registry.DETAIL_LIMIT)
        self.assertEqual(registry.last_line(None), "")


class RetryTests(unittest.TestCase):
    def run_bd(self, fake: FakeBd) -> tuple[subprocess.CompletedProcess[str], int, mock.MagicMock]:
        with mock.patch.object(registry.subprocess, "run", fake), mock.patch.object(registry, "sleep") as sleep:
            result, attempts = registry.bd(["kv", "list", "--json"], Path("/repo"), ID)
        return result, attempts, sleep

    def test_a_transient_timeout_is_retried_until_it_succeeds(self) -> None:
        fake = FakeBd(done(1, stderr=TIMEOUT), done(0, stdout="{}"))
        result, attempts, sleep = self.run_bd(fake)
        self.assertEqual((result.returncode, attempts), (0, 2))
        self.assertEqual([call.args for call in sleep.call_args_list], [(1.0,)])

    def test_retries_stop_after_three_attempts(self) -> None:
        fake = FakeBd(*(done(1, stderr=TIMEOUT) for _ in range(3)))
        result, attempts, sleep = self.run_bd(fake)
        self.assertEqual((result.returncode, attempts), (1, 3))
        self.assertEqual([call.args for call in sleep.call_args_list], [(1.0,), (2.0,)])
        self.assertEqual(fake.results, [])

    def test_connection_refused_and_plain_failures_are_never_retried(self) -> None:
        for stderr in (REFUSED, "Error: unknown key"):
            with self.subTest(stderr=stderr):
                fake = FakeBd(done(1, stderr=stderr), done(0))
                result, attempts, sleep = self.run_bd(fake)
                self.assertEqual((result.returncode, attempts), (1, 1))
                sleep.assert_not_called()


class CliFailureReportTests(unittest.TestCase):
    def run_cli(self, argv: list[str], fake: FakeBd) -> tuple[int, dict[str, object]]:
        out = io.StringIO()
        with (
            mock.patch.object(registry.subprocess, "run", fake),
            mock.patch.object(registry, "sleep"),
            mock.patch.object(sys, "argv", ["coordinator_registry.py", *argv]),
            contextlib.redirect_stdout(out),
        ):
            code = registry.main()
        return code, json.loads(out.getvalue())

    def test_deregister_failure_names_cause_detail_and_attempts(self) -> None:
        fake = FakeBd(*(done(1, stderr="warning: slow\n" + TIMEOUT) for _ in range(3)))
        code, payload = self.run_cli(["deregister", "--repo-root", "/repo", "--id", ID], fake)
        self.assertEqual(code, 1)
        self.assertEqual(
            payload,
            {
                "action": "failed",
                "attempts": 3,
                "detail": TIMEOUT.strip(),
                "error": "server-timeout",
                "id": ID,
                "step": "kv-clear",
            },
        )

    def test_deregister_success_output_is_unchanged(self) -> None:
        code, payload = self.run_cli(["deregister", "--repo-root", "/repo", "--id", ID], FakeBd(done(0)))
        self.assertEqual((code, payload), (0, {"action": "cleared", "id": ID}))

    def test_register_reports_a_refused_listing_without_retrying(self) -> None:
        fake = FakeBd(done(1, stderr=REFUSED))
        code, payload = self.run_cli(["register", "--repo-root", "/repo", "--id", ID, "--wake-minutes", "20"], fake)
        self.assertEqual(code, 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(
            {key: payload[key] for key in ("action", "attempts", "error", "step")},
            {"action": "failed", "attempts": 1, "error": "connection-refused", "step": "kv-list"},
        )
        self.assertEqual(payload["detail"], REFUSED.strip())

    def test_register_reports_a_failed_write_with_the_value_it_tried(self) -> None:
        fake = FakeBd(done(0, stdout="{}"), done(1, stdout="Error: permission denied\n"))
        code, payload = self.run_cli(["register", "--repo-root", "/repo", "--id", ID, "--wake-minutes", "20"], fake)
        self.assertEqual(code, 1)
        self.assertEqual(payload["step"], "kv-set")
        self.assertEqual(payload["error"], "command-failed")
        # bd sometimes reports on stdout only; the detail falls back to it.
        self.assertEqual(payload["detail"], "Error: permission denied")
        self.assertIn("expires_at", payload)

    def test_register_reports_unparseable_listing_as_invalid_json(self) -> None:
        fake = FakeBd(done(0, stdout="not json"))
        code, payload = self.run_cli(["register", "--repo-root", "/repo", "--id", ID, "--wake-minutes", "20"], fake)
        self.assertEqual(code, 1)
        self.assertEqual((payload["error"], payload["step"]), ("invalid-json", "kv-list"))


NOW = registry.datetime(2026, 11, 21, 12, 0, tzinfo=registry.timezone.utc)
FIVE = registry.timedelta(minutes=5)


def block(owner: str = ID, at: str = "2026-11-21T11:58:00Z") -> str:
    return f"[beads-heartbeat]\nowner={owner}\nlast_heartbeat_at={at}\n[/beads-heartbeat]"


def shown(notes: str | None, assignee: str = ID, bead: str = "aib-1") -> subprocess.CompletedProcess[str]:
    return done(0, stdout=json.dumps([{"id": bead, "assignee": assignee, "notes": notes}]))


class PlanHeartbeatTests(unittest.TestCase):
    def test_insert_keeps_other_notes(self) -> None:
        action, notes = registry.plan_heartbeat("Packet: see design.", ID, now=NOW, min_age=FIVE)
        self.assertEqual(action, "write")
        self.assertEqual(notes, "Packet: see design.\n\n" + block(at="2026-11-21T12:00:00Z"))
        self.assertEqual(registry.plan_heartbeat(None, ID, now=NOW, min_age=FIVE)[1],
                         block(at="2026-11-21T12:00:00Z"))

    def test_replace_in_place_and_collapse_duplicates(self) -> None:
        notes = "\n\n".join(["head", block(at="2026-11-21T11:00:00Z"), "middle",
                              block(owner="coordinator:other"), "tail"])
        action, updated = registry.plan_heartbeat(notes, ID, now=NOW, min_age=FIVE)
        self.assertEqual(action, "write")
        self.assertEqual(updated.count("[beads-heartbeat]"), 1)
        self.assertEqual(updated, "\n\n".join(["head", block(at="2026-11-21T12:00:00Z"), "middle", "tail"]))

    def test_fresh_own_block_is_not_rewritten(self) -> None:
        self.assertEqual(registry.plan_heartbeat(block(), ID, now=NOW, min_age=FIVE), ("fresh", None))
        stale = block(at="2026-11-21T11:50:00Z")
        foreign = block(owner="coordinator:other")
        future = block(at="2026-11-21T12:30:00Z")
        for notes in (stale, foreign, future):
            with self.subTest(notes=notes):
                self.assertEqual(registry.plan_heartbeat(notes, ID, now=NOW, min_age=FIVE)[0], "write")


class MalformedHeartbeatTests(unittest.TestCase):
    def test_stray_or_broken_tags_are_refused_without_a_write(self) -> None:
        cases = {
            "unclosed before a valid block": "intro\n[beads-heartbeat]\nowner=x\nIMPORTANT NOTE\n\n" + block() + "\nafter",
            "unclosed with its own timestamp": "[beads-heartbeat]\nowner=x\nlast_heartbeat_at=2026-11-21T11:59:00Z\n",
            "prose mention": "see [beads-heartbeat] ... [/beads-heartbeat] here",
            "stray closing tag": "done [/beads-heartbeat]",
        }
        for name, notes in cases.items():
            with self.subTest(name=name):
                self.assertEqual(registry.plan_heartbeat(notes, ID, now=NOW, min_age=FIVE), ("malformed", None))

    def test_crlf_canonical_block_is_replaced(self) -> None:
        notes = "head\r\n" + block(at="2026-11-21T11:00:00Z").replace("\n", "\r\n") + "\r\ntail"
        action, updated = registry.plan_heartbeat(notes, ID, now=NOW, min_age=FIVE)
        self.assertEqual(action, "write")
        self.assertEqual(updated.count("[beads-heartbeat]"), 1)
        self.assertIn("last_heartbeat_at=2026-11-21T12:00:00Z", updated)
        self.assertTrue(updated.startswith("head") and updated.endswith("tail"))

    def test_command_refuses_malformed_notes(self) -> None:
        fake = FakeBd(shown("[beads-heartbeat]\nowner=x\n"))
        with mock.patch.object(registry.subprocess, "run", fake), mock.patch.object(registry, "sleep"):
            payload, code = registry.heartbeat(Path("/repo"), ID, "aib-1", now=NOW, min_age=FIVE)
        self.assertEqual((code, payload["error"]), (1, "malformed-heartbeat"))
        self.assertEqual(len(fake.calls), 1)


class HeartbeatCommandTests(unittest.TestCase):
    def run_heartbeat(self, fake: FakeBd) -> tuple[dict[str, object], int]:
        with mock.patch.object(registry.subprocess, "run", fake), mock.patch.object(registry, "sleep"):
            return registry.heartbeat(Path("/repo"), ID, "aib-1", now=NOW, min_age=FIVE)

    def test_writes_under_the_assignee_guard(self) -> None:
        fake = FakeBd(shown("notes"), shown("notes"), done(0, stdout="[]"))
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["action"]), (0, "written"))
        update = fake.calls[2]
        self.assertEqual(update[update.index("--if-assignee") + 1], ID)
        self.assertIn("notes\n\n[beads-heartbeat]", update[update.index("--notes") + 1])

    def test_refuses_a_bead_held_by_someone_else(self) -> None:
        for assignee in ("coordinator:other", "", None):
            with self.subTest(assignee=assignee):
                fake = FakeBd(shown("notes", assignee=assignee))
                payload, code = self.run_heartbeat(fake)
                self.assertEqual((code, payload["error"]), (13, "not-assignee"))
                self.assertEqual(len(fake.calls), 1)

    def test_guard_mismatch_at_write_time_is_refused(self) -> None:
        fake = FakeBd(shown("n"), shown("n"), done(13, stderr="precondition no longer held"))
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["error"]), (13, "not-assignee"))

    def test_notes_changed_between_reads_is_retried(self) -> None:
        fake = FakeBd(shown("a"), shown("a\nb"), shown("a\nb"), shown("a\nb"), done(0, stdout="[]"))
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["action"]), (0, "written"))
        self.assertIn("a\nb\n\n[beads-heartbeat]", fake.calls[4][fake.calls[4].index("--notes") + 1])

    def test_notes_that_keep_changing_fail_without_writing(self) -> None:
        reads = [shown(str(i)) for i in range(2 * registry.HEARTBEAT_WRITE_ATTEMPTS)]
        fake = FakeBd(*reads)
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["error"]), (1, "notes-changing"))
        self.assertFalse(any("update" in call for call in fake.calls))

    def test_fresh_block_skips_the_write(self) -> None:
        fake = FakeBd(shown(block()))
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["action"]), (0, "fresh"))
        self.assertEqual(len(fake.calls), 1)

    def test_read_failure_is_reported(self) -> None:
        fake = FakeBd(*(done(1, stderr=TIMEOUT) for _ in range(3)))
        payload, code = self.run_heartbeat(fake)
        self.assertEqual((code, payload["error"], payload["step"]), (1, "server-timeout", "show"))


if __name__ == "__main__":
    unittest.main()
