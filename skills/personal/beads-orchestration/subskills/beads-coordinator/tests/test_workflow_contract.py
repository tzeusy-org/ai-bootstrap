# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
LOOP = SKILL_ROOT / "references" / "coordinator-loop.md"
SAFETY = SKILL_ROOT / "references" / "runtime-and-safety.md"
EPIC = SKILL_ROOT / "references" / "epic-coordination.md"
WRITER = SKILL_ROOT.parent / "beads-writer" / "SKILL.md"
TOKEN_EFFICIENCY = SKILL_ROOT.parents[1] / "references" / "token-efficiency.md"
NORMALIZER = SKILL_ROOT / "scripts" / "normalize_pr_review_state.py"
REVIEW_FAILURE_PROTOCOL = (
    SKILL_ROOT.parent / "beads-pr-reviewer-worker" / "references" / "failure-protocol.md"
)
SKILL_AUDIT_WORKFLOW = SKILL_ROOT.parents[4] / ".github" / "workflows" / "skill-audit.yml"


class BeadsCoordinatorWorkflowContractTests(unittest.TestCase):
    def test_dispatch_requires_structured_acceptance_and_cohesion_check(self) -> None:
        contents = LOOP.read_text(encoding="utf-8").lower()
        self.assertIn("dispatch readiness", contents)
        self.assertIn("cohesion", contents)
        self.assertIn("acceptance criteria", contents)

    def test_discoveries_are_classified_before_bead_creation(self) -> None:
        contents = LOOP.read_text(encoding="utf-8").lower()
        for phrase in (
            "current-pr correctness",
            "prerequisite blocker",
            "new behavior",
            "duplicate",
        ):
            self.assertIn(phrase, contents)

    def test_review_risk_tiers_and_exact_head_are_explicit(self) -> None:
        contents = SAFETY.read_text(encoding="utf-8").lower()
        self.assertIn("review risk tiers", contents)
        self.assertIn("exact head sha", contents)
        self.assertIn("auth", contents)
        self.assertIn("data loss", contents)

    def test_model_selection_matches_complexity_and_plan_policy(self) -> None:
        contents = SAFETY.read_text(encoding="utf-8")
        self.assertIn("| Strategy | Claude | Codex / ChatGPT | Gemini |", contents)
        for row in (
            "| `EPIC_COMPLEXITY_MODEL` | Opus 5.5 High | GPT-6.1 Sol XHigh | gemini-4-argon |",
            "| `HIGH_COMPLEXITY_MODEL` | Opus 5.5 Medium | GPT-6.1 Sol High | gemini-4-argon |",
            "| `MEDIUM_COMPLEXITY_MODEL` | Sonnet 5.5 High | GPT-6.1 Sol High | gemini-4-argon |",
            "| `LOW_COMPLEXITY_MODEL` | Sonnet 5.5 Low | GPT-6.1 Sol Low | gemini-3.8-flash |",
            "| `TRIVIAL_COMPLEXITY_MODEL` | Haiku 5.5 Low | GPT-6 Luna Max | gemini-3.8-flash |",
            "| `DESIGN_AND_SPECIFICATION_MODEL` | Opus 5.5 High | GPT-6.1 Sol XHigh | gemini-4-argon |",
        ):
            self.assertIn(row, contents)
        for binding in (
            "| GPT-6 Luna Max | `gpt-6-luna` | `max` |",
            "| GPT-6.1 Sol Low | `gpt-6.1-sol` | `low` |",
            "| GPT-6.1 Sol High | `gpt-6.1-sol` | `high` |",
            "| GPT-6.1 Sol XHigh | `gpt-6.1-sol` | `xhigh` |",
            "| Haiku 5.5 Low | `claude-haiku-5-5` | `low` |",
            "| Sonnet 5.5 Low | `claude-sonnet-5-5` | `low` |",
            "| Sonnet 5.5 High | `claude-sonnet-5-5` | `high` |",
            "| Opus 5.5 Medium | `claude-opus-5-5` | `medium` |",
            "| Opus 5.5 High | `claude-opus-5-5` | `high` |",
        ):
            self.assertIn(binding, contents)
        self.assertIn("Plan/design/specification override", contents)
        self.assertIn("before the complexity-label fast path", contents)
        self.assertIn("never\ndispatch Luna for anything that needs judgment", contents)
        self.assertIn("do not dispatch Astra or Terra", contents)
        self.assertIn("Never dispatch Haiku for anything that needs judgment", contents)
        for path in (WRITER, TOKEN_EFFICIENCY):
            self.assertIn("design/specification override", path.read_text(encoding="utf-8"))

    def test_refresh_is_event_driven_with_cache_bounded_wakes(self) -> None:
        loop = LOOP.read_text(encoding="utf-8").lower()
        self.assertIn("event-driven", loop)
        self.assertIn("no-progress frontier", loop)
        self.assertIn("safety sweep", loop)
        # Numbers are canonical in runtime-and-safety.md; the loop only points.
        safety = SAFETY.read_text(encoding="utf-8").lower()
        self.assertIn("orchestrator wake cadence", safety)
        self.assertIn("4m50s", safety)
        self.assertIn("1-hour", safety)
        self.assertIn("60 minutes", safety)
        self.assertIn("3 consecutive no-op wakes", safety)
        self.assertNotIn("30-minute safety sweep", loop)

    def test_dispatch_preserves_context_affinity_when_safe(self) -> None:
        contents = LOOP.read_text(encoding="utf-8").lower()
        self.assertIn("context affinity", contents)

    def test_dispatch_continues_warm_lanes_before_spawning(self) -> None:
        loop = LOOP.read_text(encoding="utf-8")
        self.assertIn("Lane continuation first", loop)
        self.assertIn("LANE-CONTINUATION", loop)
        self.assertIn("| Worker lanes | 1 by default", loop)
        self.assertIn("Cache-first execution", TOKEN_EFFICIENCY.read_text(encoding="utf-8"))

    def test_worktree_creation_is_bound_to_repo_root(self) -> None:
        command = '(cd "${REPO_ROOT}" && bd worktree create'
        for document in (LOOP, EPIC):
            with self.subTest(document=document.name):
                self.assertIn(command, document.read_text(encoding="utf-8"))

    def test_review_cycle_state_has_a_canonical_note(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("[review-cycle]", contents)
        self.assertIn("substantive_reopenings=", contents)
        self.assertIn("reviewed_head_sha=", contents)

    def test_correction_lane_temporarily_detaches_and_restores_review_gate(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("bd dep remove <original-id> <review-id>", contents)
        self.assertIn("REVIEW_CORRECTION_MODE=yes", contents)
        self.assertIn("bd dep add <original-id> <review-id>", contents)

    def test_queue_membership_uses_entry_and_never_infers_rebase_from_absence(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("mergeQueueEntry{position enqueuedAt} labels(first:20)", contents)
        self.assertIn("`merge_queue_entry` is non-null", contents)
        self.assertIn("queue metadata alone", contents)
        self.assertNotIn("the queue ejected it", contents)

        documents = (LOOP, REVIEW_FAILURE_PROTOCOL)
        valid_cases = {
            "queued": (
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": None,
                    "mergeQueueEntry": {
                        "position": 1,
                        "enqueuedAt": "2026-09-06T13:55:14Z",
                    },
                },
                {"auto_merge_armed": False, "queue_position": 1},
            ),
            "armed": (
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": {"enabledAt": None},
                    "mergeQueueEntry": None,
                },
                {"auto_merge_armed": True, "queue_position": None},
            ),
            "absent": (
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": None,
                    "mergeQueueEntry": None,
                },
                {"auto_merge_armed": False, "queue_position": None},
            ),
            "merged": (
                {
                    "state": "MERGED",
                    "mergedAt": "2026-09-06T14:05:09Z",
                    "autoMergeRequest": None,
                    "mergeQueueEntry": None,
                },
                {"auto_merge_armed": False, "queue_position": None},
            ),
        }
        invalid_cases = {
            "partial data with errors": (
                {"errors": [{"message": "partial failure"}]},
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": None,
                    "mergeQueueEntry": None,
                },
            ),
            "empty queue entry": (
                {},
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": None,
                    "mergeQueueEntry": {},
                },
            ),
            "null queue fields": (
                {},
                {
                    "state": "OPEN",
                    "mergedAt": None,
                    "autoMergeRequest": None,
                    "mergeQueueEntry": {"position": None, "enqueuedAt": None},
                },
            ),
        }

        for document in documents:
            match = re.search(
                r"\| jq -e '([^'\n]+)'", document.read_text(encoding="utf-8")
            )
            self.assertIsNotNone(match, document)
            projection = match.group(1)
            for name, (pull_request, expected) in valid_cases.items():
                pull_request["labels"] = {"nodes": []}
                result = subprocess.run(
                    ["jq", "-e", projection],
                    input=json.dumps(
                        {"data": {"repository": {"pullRequest": pull_request}}}
                    ),
                    capture_output=True,
                    text=True,
                )
                with self.subTest(document=document.name, case=name):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = json.loads(result.stdout)
                    self.assertEqual(output["state"], pull_request["state"])
                    self.assertEqual(output["mergedAt"], pull_request["mergedAt"])
                    self.assertEqual(
                        output["auto_merge_armed"], expected["auto_merge_armed"]
                    )
                    entry = output["merge_queue_entry"]
                    self.assertEqual(
                        None if entry is None else entry["position"],
                        expected["queue_position"],
                    )
            for name, (top_level, pull_request) in invalid_cases.items():
                pull_request["labels"] = {"nodes": []}
                payload = {
                    **top_level,
                    "data": {"repository": {"pullRequest": pull_request}},
                }
                result = subprocess.run(
                    ["jq", "-e", projection],
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                )
                with self.subTest(document=document.name, case=name):
                    self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_review_bead_template_is_packet_complete(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn('--acceptance="1. Record reviewer identity', contents)
        self.assertIn('--design="Independent exact-head review', contents)

    def test_correction_worktree_has_an_explicit_re_review_hand_back(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("Correction-to-re-review hand-back", contents)
        self.assertIn(
            "git -C <review-worktree> checkout -B agent/<original-id> origin/agent/<original-id>",
            contents,
        )
        self.assertIn("expected reviewer branch is `agent/<original-id>`", contents)

    def test_initial_review_bootstrap_uses_two_stage_branch_attestation(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("Stage 1 — dispatch bootstrap", contents)
        self.assertIn("`agent/<review-id>`", contents)
        self.assertIn("Stage 2 — prepared-head attestation", contents)
        self.assertIn("`agent/<original-id>`", contents)
        self.assertIn("Re-review hand-back starts at Stage 2", contents)

    def test_post_merge_cleanup_maps_original_and_review_ids(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        cleanup = contents.split(
            "### Post-closure branch and worktree cleanup (merged PRs)", 1
        )[1].split("Priority rule:", 1)[0]
        for command in (
            'git push origin --delete "agent/${ORIGINAL_ID}"',
            'bd worktree remove ".worktrees/parallel-agents/${REVIEW_ID}"',
            'git branch -D "agent/${ORIGINAL_ID}"',
            'git branch -D "agent/${REVIEW_ID}"',
        ):
            self.assertIn(command, cleanup)
        self.assertIn(
            "the transferred worktree path remains keyed by `REVIEW_ID`",
            cleanup,
        )
        self.assertNotIn("agent/<id>", cleanup)

    def test_step_zero_uses_the_report_only_normalizer_then_reverifies_before_mutation(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertTrue(NORMALIZER.exists(), NORMALIZER)
        self.assertIn("scripts/normalize_pr_review_state.py", contents)
        self.assertIn("beads-pr-review-normalization/v1", contents)
        self.assertIn("immediately before each actual mutation", contents)
        self.assertIn("sole PR-state mutator", contents)
        self.assertIn("manual-triage", contents)
        # The fail-closed contract is canonical in the script docstring, not the
        # loop doc, so the coordinator does not re-read it every cycle.
        self.assertIn("module docstring", contents)
        docstring = NORMALIZER.read_text(encoding="utf-8").split('"""', 2)[1]
        for phrase in (
            "Fail-closed contract",
            "manual-triage",
            "self-referential",
            "resolver `issue_id`",
            "required label, status, or uniqueness",
            "creation timestamp",
            "`gh-pr:<N>`",
            "skip-command-failure",
            "cyclic-review-relation",
            "duplicate review-ID",
            "same original",
        ):
            self.assertIn(phrase, docstring)

    def test_review_deduplication_uses_parsed_chronology_and_fails_closed(self) -> None:
        contents = LOOP.read_text(encoding="utf-8")
        self.assertIn("parsed creation chronology", contents)
        self.assertIn("partial manual triage", contents)
        self.assertIn("never choose by raw timestamp text", contents)

    def test_pr_ci_runs_only_the_report_only_fake_command_suites(self) -> None:
        contents = SKILL_AUDIT_WORKFLOW.read_text(encoding="utf-8")
        marker = "      - name: Run report-only Beads safety suites"
        self.assertIn(marker, contents)
        step = contents.split(marker, 1)[1].split(
            "      - name:", 1
        )[0]
        for command in (
            "uv run skills/personal/beads-orchestration/subskills/beads-coordinator/tests/test_normalize_pr_review_state.py",
            "uv run skills/personal/beads-orchestration/subskills/beads-coordinator/tests/test_workflow_contract.py",
            "uv run skills/personal/beads-orchestration/subskills/beads-cleanup/tests/test_cleanup_scan.py",
            "uv run skills/personal/beads-orchestration/subskills/beads-pr-reviewer-worker/tests/test_skill_package.py",
            "uv run skills/personal/beads-orchestration/subskills/beads-pr-reviewer-worker/tests/test_scripts.py",
        ):
            self.assertIn(command, step)
        self.assertNotIn("bd ", step)
        self.assertNotIn("gh ", step)


if __name__ == "__main__":
    unittest.main()
