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
    base = {"id": "x-1", "description": "Outcome.", "acceptance_criteria": "Observable.",
            "design": "Decision.", "labels": []}
    base.update(fields)
    return base


class PacketStatusTests(unittest.TestCase):
    def test_complete_with_design(self) -> None:
        self.assertEqual(check_packet.packet_status(bead()),
                         {"id": "x-1", "packet_complete": True, "missing": []})

    def test_scope_section_stands_in_for_blank_design(self) -> None:
        # Canary 2: aib-b2t (design blank, scope in description) passed.
        for description in ("Fix it.\n\n## Scope\nOnly the helper.",
                            "## Steps to reproduce\nRun it.",
                            "Scope: the ranker only."):
            with self.subTest(description=description):
                status = check_packet.packet_status(bead(design="", description=description))
                self.assertTrue(status["packet_complete"], status)

    def test_blank_design_without_scope_section_is_incomplete(self) -> None:
        # Canary 2: aib-435 (design blank, no scope section) was rejected.
        status = check_packet.packet_status(
            bead(design=None, description="Move it aside once a second cycle runs clean."))
        self.assertEqual(status["missing"], ["decisions:design-or-scope-section"])
        # "scope" inside prose is not a section.
        status = check_packet.packet_status(
            bead(design="", description="This widens the scope of the helper."))
        self.assertFalse(status["packet_complete"])

    def test_acceptance_must_be_the_structured_field(self) -> None:
        status = check_packet.packet_status(bead(
            acceptance_criteria="  ",
            description="Do it.\n\nAcceptance:\n- passes"))
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
        self.assertFalse(status["packet_complete"])
        self.assertIn("acceptance:acceptance_criteria", status["missing"])


class CliTests(unittest.TestCase):
    def run_main(self, argv, responses):
        calls = []

        def fake_run(cmd, capture_output, text):
            calls.append(cmd)
            key = cmd[3]
            out = responses[key] if key == "ready" else responses[cmd[4]]
            return mock.Mock(returncode=0, stdout=json.dumps(out), stderr="")

        stdout = io.StringIO()
        with mock.patch.object(check_packet.subprocess, "run", fake_run), \
                contextlib.redirect_stdout(stdout):
            code = check_packet.main(argv)
        return code, json.loads(stdout.getvalue()), calls

    def test_defaults_to_ready_queue(self) -> None:
        code, payload, calls = self.run_main(["--repo-root", "/r"], {
            "ready": [{"id": "a-1"}, {"id": "a-2"}],
            "a-1": [bead(id="a-1")],
            "a-2": [bead(id="a-2", acceptance_criteria="")],
        })
        self.assertEqual(code, 0)
        self.assertEqual([b["packet_complete"] for b in payload["beads"]], [True, False])
        self.assertEqual(calls[0], ["bd", "-C", "/r", "ready", "--json"])

    def test_explicit_ids_skip_ready(self) -> None:
        code, payload, calls = self.run_main(["--repo-root", "/r", "a-3"],
                                             {"a-3": [bead(id="a-3")]})
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(payload["beads"][0]["id"], "a-3")

    def test_bd_failure_exits_nonzero(self) -> None:
        failed = mock.Mock(returncode=1, stdout="", stderr="Error: unreachable")
        stdout = io.StringIO()
        with mock.patch.object(check_packet.subprocess, "run", return_value=failed), \
                contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as exit_:
            check_packet.main(["--repo-root", "/r", "a-4"])
        self.assertEqual(exit_.exception.code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["error"], "bd-failed")


if __name__ == "__main__":
    unittest.main()
