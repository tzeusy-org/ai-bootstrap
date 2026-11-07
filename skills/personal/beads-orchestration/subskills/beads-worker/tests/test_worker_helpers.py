# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSERT_CONTEXT = SKILL_ROOT / "scripts" / "assert_worker_context.py"
EMIT_REPORT = SKILL_ROOT / "scripts" / "emit_worker_report.py"
PACKAGE_PLACEHOLDER = "<loaded beads-worker package>"
HELPER_PATH = re.compile(r"scripts/(?:assert_worker_context|emit_worker_report)\.py")
BARE_HELPER_PATH = re.compile(r"(?<!" + re.escape(PACKAGE_PLACEHOLDER) + r"/)" + HELPER_PATH.pattern)


class AssertWorkerContextTests(unittest.TestCase):
    def _init_repo(self, path: Path, branch: str = "main") -> None:
        path.mkdir()
        subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)
        subprocess.run(
            ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        (path / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "fixture"], check=True)

    def _run_context(
        self,
        *,
        worktree: Path,
        repo_root: Path,
        current_path: Path | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(ASSERT_CONTEXT),
                "--worktree-path",
                str(worktree),
                "--repo-root",
                str(repo_root),
                "--issue-id",
                "bd-42",
                "--current-path",
                str(current_path or worktree),
            ],
            cwd=(cwd or worktree).resolve(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_context_passes_for_expected_worktree_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            worktree = Path(tmp) / "worker"
            self._init_repo(repo_root)
            subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "add", "-q", "-b", "agent/bd-42", str(worktree)],
                check=True,
            )
            repo_alias = Path(tmp) / "repo-alias"
            worktree_alias = Path(tmp) / "worker-alias"
            repo_alias.symlink_to(repo_root, target_is_directory=True)
            worktree_alias.symlink_to(worktree, target_is_directory=True)

            result = self._run_context(
                worktree=worktree_alias,
                repo_root=repo_alias,
                current_path=worktree,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["expected_branch"], "agent/bd-42")

    def test_delivery_bootstrap_uses_anchor_and_independent_review_uses_own_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            worktree = Path(tmp) / "worker"
            self._init_repo(repo_root)
            subprocess.run(["git", "-C", str(repo_root), "worktree", "add", "-q", "-b", "agent/bd-42", str(worktree)], check=True)
            manifest = Path(tmp) / "delivery.json"
            delivery = {"schema": "beads-delivery/v1", "anchor": "bd-42", "members": ["bd-42", "bd-43"], "owner": "coord-test", "outcome": "bd-feature", "base_branch": "main", "packet": "docs/current.md#adapter-corrections"}
            manifest.write_text(json.dumps(delivery))
            command = ["python3", str(ASSERT_CONTEXT), "--worktree-path", str(worktree), "--repo-root", str(repo_root), "--issue-id", "bd-42", "--current-path", str(worktree), "--delivery-file", str(manifest)]
            result = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["members"], ["bd-42", "bd-43"])
            delivery["anchor"] = "bd-43"
            manifest.write_text(json.dumps(delivery))
            result = subprocess.run(command, cwd=worktree, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            subprocess.run(["git", "-C", str(worktree), "switch", "-q", "-c", "review/bd-review"], check=True)
            review_command = command[:-2]
            review_command[review_command.index("--issue-id") + 1] = "bd-review"
            review_command += ["--review-id", "bd-review"]
            result = subprocess.run(review_command, cwd=worktree, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["expected_branch"], "review/bd-review")

    def test_context_fails_for_wrong_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            expected_worktree = Path(tmp) / "expected-worker"
            actual_worktree = Path(tmp) / "actual-worker"
            self._init_repo(repo_root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "worktree",
                    "add",
                    "-q",
                    "-b",
                    "agent/bd-42",
                    str(expected_worktree),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "worktree",
                    "add",
                    "-q",
                    "-b",
                    "agent/other",
                    str(actual_worktree),
                ],
                check=True,
            )
            expected_git_dir = subprocess.run(
                ["git", "-C", str(expected_worktree), "rev-parse", "--absolute-git-dir"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            redirected_env = os.environ.copy()
            redirected_env.update(
                {
                    "GIT_DIR": expected_git_dir,
                    "GIT_WORK_TREE": str(expected_worktree),
                }
            )
            result = self._run_context(
                worktree=actual_worktree,
                repo_root=repo_root,
                env=redirected_env,
            )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid-runtime-context")
        self.assertEqual(payload["branch"], "agent/other")
        self.assertIn("branch", " ".join(payload["reasons"]))

    def test_context_rejects_foreign_or_unverifiable_repository_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "expected"
            foreign = Path(tmp) / "matching-name-worker"
            self._init_repo(repo_root)
            self._init_repo(foreign, branch="agent/bd-42")
            subprocess.run(
                ["git", "-C", str(foreign), "remote", "add", "origin", "https://token@example.invalid/repo.git"],
                check=True,
            )
            redirected_env = os.environ.copy()
            redirected_env.update(
                {
                    "GIT_DIR": str(repo_root / ".git"),
                    "GIT_WORK_TREE": str(repo_root),
                    "GIT_COMMON_DIR": str(repo_root / ".git"),
                }
            )

            foreign_result = self._run_context(
                worktree=foreign,
                repo_root=repo_root,
                env=redirected_env,
            )
            missing_root = Path(tmp) / "missing-root"
            missing_root.mkdir()
            missing_result = self._run_context(worktree=foreign, repo_root=missing_root)
            misbound_result = self._run_context(
                worktree=foreign,
                repo_root=repo_root,
                cwd=repo_root,
            )

        self.assertNotEqual(foreign_result.returncode, 0)
        foreign_payload = json.loads(foreign_result.stdout)
        self.assertIn("worktree-is-not-owned-by-repo-root", foreign_payload["reasons"])
        self.assertNotIn("token", foreign_result.stdout + foreign_result.stderr)
        self.assertNotEqual(missing_result.returncode, 0)
        missing_payload = json.loads(missing_result.stdout)
        self.assertIn("repo-root-git-identity-unverifiable", missing_payload["reasons"])
        misbound_payload = json.loads(misbound_result.stdout)
        self.assertIn("process-cwd-does-not-match-current-path", misbound_payload["reasons"])

    def test_documented_helper_commands_run_from_script_free_consumer(self) -> None:
        # A dispatched worker's cwd is a consumer worktree that ships no
        # scripts/ of its own, so every documented helper command must reach
        # the loaded package rather than a repo-root path.
        blocks = []
        for doc in (SKILL_ROOT / "SKILL.md", *(SKILL_ROOT / "references").glob("*.md")):
            for block in re.findall(r"```(?:bash|sh)?\n(.*?)```", doc.read_text(encoding="utf-8"), re.S):
                if not HELPER_PATH.search(block):
                    continue
                self.assertNotRegex(block, BARE_HELPER_PATH, f"{doc.name} invokes a helper by a cwd-relative path")
                if not block.rstrip().endswith("..."):  # elided usage stubs are not runnable
                    blocks.append((doc.name, block.replace(PACKAGE_PLACEHOLDER, str(SKILL_ROOT))))
        self.assertGreaterEqual({name for name, _ in blocks}, {"SKILL.md", "runtime-contract.md", "worker-report.md"})

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "consumer"
            worktree = Path(tmp) / "worker"
            self._init_repo(repo_root)
            subprocess.run(
                ["git", "-C", str(repo_root), "worktree", "add", "-q", "-b", "agent/bd-42", str(worktree)],
                check=True,
            )
            self.assertFalse((worktree / "scripts").exists())
            env = {**os.environ, "WORKTREE_PATH": str(worktree), "REPO_ROOT": str(repo_root), "ISSUE_ID": "bd-42"}
            for name, block in blocks:
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", block],
                    cwd=worktree,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"{name}:\n{block}\n{result.stdout}{result.stderr}")


class EmitWorkerReportTests(unittest.TestCase):
    def test_package_report_preserves_partial_acceptance_and_rejects_missing_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "delivery.json"
            manifest.write_text(json.dumps({"schema": "beads-delivery/v1", "anchor": "bd-42", "members": ["bd-42", "bd-43"], "owner": "coord-test", "outcome": "bd-feature", "base_branch": "main", "packet": "docs/current.md#adapter-corrections"}))
            results = [{"id": "bd-42", "status": "satisfied", "evidence": "Adapter seam tests pass"}, {"id": "bd-43", "status": "partial", "evidence": "Activation remains owner-held"}]
            command = ["python3", str(EMIT_REPORT), "--status", "completed-pr-opened", "--issue-id", "bd-42", "--worktree-path", "/tmp/worker", "--head-commit", "abc123", "--handoff-path", "pr-required", "--branch-pushed", "yes", "--pr-url", "https://example.invalid/pr/1", "--pr-number", "1", "--summary", "Foundation ready for review; activation remains partial.", "--delivery-file", str(manifest), "--member-results-json", json.dumps(results)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status":"partial"', result.stdout)
            self.assertIn('"outcome":"bd-feature"', result.stdout)
            command[-1] = json.dumps(results[:1])
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("member-results-do-not-cover-package", result.stderr)

    def test_bootstrap_failure_reports_members_unverified_and_handles_unreadable_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "delivery.json"
            manifest.write_text(json.dumps({"schema": "beads-delivery/v1", "anchor": "bd-42", "members": ["bd-42", "bd-43"], "owner": "coord-test", "outcome": "bd-feature", "base_branch": "main", "packet": "docs/current.md"}))
            command = ["python3", str(EMIT_REPORT), "--status", "invalid-runtime-context", "--issue-id", "bd-42", "--branch", "unexpected-branch", "--worktree-path", "/tmp/worker", "--head-commit", "n/a", "--handoff-path", "invalid-runtime-context", "--branch-pushed", "no", "--summary", "Bootstrap failed before edits.", "--delivery-file", str(manifest)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"status":"blocked"', result.stdout)
            self.assertIn('Not evaluated: runtime bootstrap failed', result.stdout)
            self.assertNotIn('"status":"satisfied"', result.stdout)
            manifest.write_text("unreadable JSON")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Delivery-Validation-Error: delivery-manifest-unverifiable", result.stdout)
            self.assertNotIn("Member-Results-JSON", result.stdout)

    def test_completed_pr_opened_requires_pr_metadata(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(EMIT_REPORT),
                "--status",
                "completed-pr-opened",
                "--issue-id",
                "bd-42",
                "--worktree-path",
                "/tmp/worktree",
                "--head-commit",
                "abc123",
                "--handoff-path",
                "pr-required",
                "--branch-pushed",
                "yes",
                "--summary",
                "Implemented the change.",
                "--quality-gate",
                "lint=pass",
                "--quality-gate",
                "typecheck=pass",
                "--quality-gate",
                "tests=pass",
                "--changes",
                "skills/personal/beads-worker/SKILL.md: tightened worker contract",
                "--tests",
                "python3 -m unittest: pass",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PR-URL", result.stderr)

    def test_completed_direct_merge_candidate_emits_report(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(EMIT_REPORT),
                "--status",
                "completed-direct-merge-candidate",
                "--issue-id",
                "bd-42",
                "--worktree-path",
                "/tmp/worktree",
                "--head-commit",
                "abc123",
                "--handoff-path",
                "direct-merge-candidate",
                "--branch-pushed",
                "yes",
                "--summary",
                "Implemented the change.",
                "--quality-gate",
                "lint=pass",
                "--quality-gate",
                "typecheck=pass",
                "--quality-gate",
                "tests=pass",
                "--changes",
                "skills/personal/beads-worker/SKILL.md: tightened worker contract",
                "--tests",
                "python3 -m unittest: pass",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("## Worker Report: bd-42", result.stdout)
        self.assertIn("Status: completed-direct-merge-candidate", result.stdout)
        self.assertIn("PR-URL: n/a", result.stdout)

    def test_blocked_report_captures_recovery_details(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(EMIT_REPORT),
                "--status",
                "blocked-awaiting-coordinator",
                "--issue-id",
                "bd-42",
                "--worktree-path",
                "/tmp/worktree",
                "--head-commit",
                "abc123",
                "--handoff-path",
                "blocked-awaiting-coordinator",
                "--branch-pushed",
                "yes",
                "--recovery-state",
                "branch-pushed",
                "--resume-condition",
                "GitHub auth is restored",
                "--summary",
                "Push succeeded but PR creation is blocked on auth.",
                "--quality-gate",
                "lint=pass",
                "--quality-gate",
                "typecheck=pass",
                "--quality-gate",
                "tests=pass",
                "--changes",
                "skills/personal/beads-worker/SKILL.md: tightened worker contract",
                "--tests",
                "python3 -m unittest: pass",
                "--blockers-json",
                '[{"title":"Restore GitHub auth","type":"task","priority":1,"depends_on":"bd-42","rationale":"gh pr create failed due to auth","unblock_condition":"GitHub auth works again"}]',
                "--failing-command",
                "gh pr create --base main --head agent/bd-42",
                "--remote-branch",
                "origin/agent/bd-42",
                "--dirty-worktree",
                "no",
                "--unpushed-commits",
                "no",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Recovery-Details:", result.stdout)
        self.assertIn("Failing-Command: gh pr create --base main --head agent/bd-42", result.stdout)
        self.assertIn("Remote-Branch: origin/agent/bd-42", result.stdout)


if __name__ == "__main__":
    unittest.main()
