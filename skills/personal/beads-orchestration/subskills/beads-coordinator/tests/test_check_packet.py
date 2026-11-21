#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Tests for scripts/check_packet.py (structural packet check)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("check_packet", SCRIPTS / "check_packet.py")
check_packet = importlib.util.module_from_spec(spec)
sys.modules["check_packet"] = check_packet
spec.loader.exec_module(check_packet)


def bead(**fields):
    base = {"id": "x-1", "issue_type": "feature", "description": "Outcome.",
            "acceptance_criteria": "Observable.", "design": "Decision.", "labels": []}
    base.update(fields)
    return base


class PacketStatusTests(unittest.TestCase):
    def test_complete_feature_with_design(self) -> None:
        self.assertEqual(check_packet.packet_status(bead()),
                         {"id": "x-1", "packet_complete": True, "missing": []})

    def test_canary_rulings(self) -> None:
        # Canary 2: aib-3gt (bug, design set) and aib-b2t (task, design blank,
        # surface named in a plain paragraph) passed; aib-435 (task, design
        # blank, needs-shaping) was rejected.
        cases = {
            "aib-3gt": (bead(issue_type="bug"), True),
            "aib-b2t": (bead(issue_type="task", design="",
                             description="Run scripts/x.py in CI; it is missing."), True),
            "aib-435": (bead(issue_type="task", design=None,
                             labels=["needs-shaping", "scope:local-state"]), False),
        }
        for name, (record, complete) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(check_packet.packet_status(record)["packet_complete"], complete)

    def test_feature_needs_design_or_scope_section(self) -> None:
        status = check_packet.packet_status(bead(design="", description="Add a thing."))
        self.assertEqual(status["missing"], ["decisions:design-or-scope-section"])
        for description in ("Add it.\n\n## Scope\nOnly the helper.",
                            "### Steps to reproduce\nRun it.",
                            "Scope: the ranker only.",
                            "- Scope: the ranker only.",
                            "**Scope:** the ranker only.",
                            "**Scope**: the ranker only."):
            with self.subTest(description=description):
                self.assertTrue(check_packet.packet_status(
                    bead(design="", description=description))["packet_complete"])

    def test_prose_is_not_a_scope_section(self) -> None:
        for description in ("Scope creep is the risk here.",
                            "scope of this change is unclear",
                            "This widens the scope: wider.",
                            "Reproduction is not yet known.",
                            "Repro unknown"):
            with self.subTest(description=description):
                self.assertFalse(check_packet.packet_status(
                    bead(design="", description=description))["packet_complete"])

    def test_acceptance_must_be_the_structured_field(self) -> None:
        status = check_packet.packet_status(bead(
            acceptance_criteria="  ", description="Do it.\n\nAcceptance:\n- passes"))
        self.assertEqual(status["missing"], ["acceptance:acceptance_criteria"])

    def test_missing_outcome_and_shaping_label(self) -> None:
        status = check_packet.packet_status(
            bead(description="", design="", labels=["needs-shaping"]))
        self.assertEqual(status["missing"], [
            "outcome:description",
            "decisions:design-or-scope-section",
            "shaping:needs-shaping-label",
        ])

    def test_null_fields_and_labels(self) -> None:
        status = check_packet.packet_status({"id": "x-2"})
        self.assertEqual(status["missing"],
                         ["outcome:description", "acceptance:acceptance_criteria"])


class CliTests(unittest.TestCase):
    def run_main(self, argv, responses):
        calls = []

        def fake_run(cmd, capture_output, text):
            calls.append(cmd)
            out = responses["ready"] if cmd[3] == "ready" else responses.get(cmd[4])
            if isinstance(out, BaseException):
                raise out
            if out is None:
                return mock.Mock(returncode=1, stdout="", stderr="Error: no issue found")
            return mock.Mock(returncode=0, stdout=out if isinstance(out, str) else json.dumps(out),
                             stderr="")

        stdout = io.StringIO()
        with mock.patch.object(check_packet.subprocess, "run", fake_run), \
                contextlib.redirect_stdout(stdout):
            code = check_packet.main(argv)
        return code, json.loads(stdout.getvalue()), calls

    def test_defaults_to_whole_ready_queue(self) -> None:
        code, payload, calls = self.run_main(["--repo-root", "/r"], {
            "ready": [{"id": "a-1"}, {"id": "a-2"}],
            "a-1": [bead(id="a-1")],
            "a-2": [bead(id="a-2", acceptance_criteria="")],
        })
        self.assertEqual(code, 0)
        self.assertEqual([b["packet_complete"] for b in payload["beads"]], [True, False])
        self.assertEqual(calls[0], ["bd", "-C", "/r", "ready", "--limit", "0", "--json"])

    def test_explicit_ids_skip_ready(self) -> None:
        code, payload, calls = self.run_main(["--repo-root", "/r", "a-3"],
                                             {"a-3": [bead(id="a-3")]})
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(payload["beads"][0]["id"], "a-3")

    def test_unreadable_bead_is_reported_per_bead(self) -> None:
        code, payload, _ = self.run_main(["--repo-root", "/r", "a-4", "a-5", "a-6"], {
            "a-4": None, "a-5": "not json", "a-6": [bead(id="a-6")]})
        self.assertEqual(code, 0)
        self.assertEqual(payload["beads"][0], {"id": "a-4", "error": "Error: no issue found"})
        self.assertIn("non-JSON", payload["beads"][1]["error"])
        self.assertTrue(payload["beads"][2]["packet_complete"])

    def test_tracker_failure_exits_nonzero(self) -> None:
        for ready in (FileNotFoundError("bd"), "not json"):
            with self.subTest(ready=ready):
                code, payload, _ = self.run_main(["--repo-root", "/r"], {"ready": ready})
                self.assertEqual(code, 1)
                self.assertEqual(payload["error"], "bd-failed")


if __name__ == "__main__":
    unittest.main()
