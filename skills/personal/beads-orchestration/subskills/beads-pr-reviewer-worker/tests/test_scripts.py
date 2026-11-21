# /// script
# requires-python = ">=3.11"
# ///
"""Behavioral tests for beads-pr-reviewer-worker scripts.

Tests use fake `gh`, `git`, and `bd` executables injected via PATH so the
scripts can be exercised without real GitHub / git / Beads access.  Each test
creates a temporary bin directory, writes small Python scripts that return
canned JSON, then runs the real script under test as a subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeBinDir:
    """Context manager that owns a temporary directory of fake executables."""

    def __init__(self):
        self._tmp = None
        self.path: Path | None = None

    def __enter__(self) -> "FakeBinDir":
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        return self

    def __exit__(self, *args) -> None:
        self._tmp.cleanup()

    def add(self, name: str, body: str) -> None:
        """Write a fake executable with the given Python body."""
        exe = self.path / name
        exe.write_text(f"#!/usr/bin/env python3\n{textwrap.dedent(body)}")
        exe.chmod(0o755)

    def env(self, **extra: str) -> dict:
        """Return an env dict with this dir prepended to PATH."""
        env = os.environ.copy()
        env["PATH"] = f"{self.path}:{env.get('PATH', '')}"
        env.update(extra)
        return env


def run_script(script_name: str, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    script = SCRIPTS / script_name
    return subprocess.run(
        [sys.executable, str(script)] + args,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# discover_quality_gates.py — no external tools, pure filesystem
# ---------------------------------------------------------------------------

class DiscoverQualityGatesTests(unittest.TestCase):
    def test_empty_directory_returns_no_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "discover_quality_gates.py")],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["commands"], {})

    def test_package_json_with_scripts_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = {"scripts": {"lint": "eslint .", "test": "jest", "build": "tsc"}}
            (Path(tmp) / "package.json").write_text(json.dumps(pkg))
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "discover_quality_gates.py")],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("lint", payload["commands"])
        self.assertIn("test", payload["commands"])
        # build is not a tracked gate
        self.assertNotIn("build", payload["commands"])

    def test_makefile_with_lint_target_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Makefile").write_text("lint:\n\tflake8 .\ntypecheck:\n\tmypy .\n")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "discover_quality_gates.py")],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("lint", payload["commands"])
        self.assertIn("typecheck", payload["commands"])


# ---------------------------------------------------------------------------
# evaluate_merge_readiness.py
# ---------------------------------------------------------------------------

def _make_gh_evaluate(bin_dir: FakeBinDir, *, pr_state: str = "OPEN",
                       is_draft: bool = False, merge_state: str = "CLEAN",
                       review_decision: str | None = None, unresolved: int = 0,
                       check_states: list[str] | None = None,
                       checks_error: str | None = None,
                       merge_queue_rules: int | None = None,
                       label_pages: list | None = None,
                       labels_error: bool = False,
                       pr_overrides: dict | None = None) -> None:
    """Configure a fake `gh` for evaluate_merge_readiness tests.

    ``merge_queue_rules`` is the number of ``merge_queue`` rules the branch
    rules endpoint reports; ``None`` makes that endpoint fail (unknown).

    ``gh pr view``/``gh pr checks`` model gh's repository resolution: with
    ``--repo OWNER/REPO`` they answer for that repository; without it they
    answer for the cwd's repository (``cwd/other``), whose PR 42 is an
    unrelated, long-merged PR with green checks (the aib-6m4 incident).
    ``pr_overrides`` replaces fields a scoped ``gh pr view`` reports; a
    ``None`` value drops that field.
    """
    thread_nodes = json.dumps([{"isResolved": False}] * unresolved +
                              [{"isResolved": True}] * 0)
    cwd_pr_json = json.dumps({
        "state": "MERGED", "isDraft": False, "mergeStateStatus": "UNKNOWN",
        "reviewDecision": "APPROVED", "mergedAt": "2026-02-13T00:00:00Z",
        "url": "https://github.com/cwd/other/pull/42", "number": 42,
        "baseRefName": "main", "headRefName": "unrelated",
    })
    pr_fields = {
        "state": pr_state,
        "isDraft": is_draft,
        "mergeStateStatus": merge_state,
        "reviewDecision": review_decision,
        "mergedAt": None,
        "number": 42,
        "baseRefName": "main",
        "headRefName": "agent/test-42",
    }
    pr_fields_json = json.dumps(pr_fields)
    if check_states is None:
        checks_json = json.dumps([{"state": "SUCCESS"}])
    else:
        checks_json = json.dumps([{"state": s} for s in check_states])
    checks_error_body = f'print({repr(checks_error)}, file=__import__("sys").stderr); raise SystemExit(1)' if checks_error else ""

    if label_pages is None:
        label_pages = [{"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}]
    labels_json = json.dumps(label_pages)

    bin_dir.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
cmd = ' '.join(argv)
scoped = argv[argv.index('--repo') + 1] if '--repo' in argv else None

if 'pr' in argv and 'view' in argv and '--json' in argv:
    if scoped is None:
        print({repr(cwd_pr_json)})
        sys.exit(0)
    pr = json.loads({repr(pr_fields_json)})
    pr["url"] = "https://github.com/" + scoped + "/pull/42"
    for key, value in json.loads({json.dumps(pr_overrides or {})!r}).items():
        if value is None:
            pr.pop(key, None)
        else:
            pr[key] = value
    print(json.dumps(pr))
    sys.exit(0)

if 'pr' in argv and 'checks' in argv:
    if scoped is None:
        print(json.dumps([{{"state": "SUCCESS"}}]))
        sys.exit(0)
    {checks_error_body or f'print({repr(checks_json)}); sys.exit(0)'}

if 'api' in argv and any(a.startswith('repos/') and '/rules/branches/' in a for a in argv):
    if {merge_queue_rules!r} is None:
        print('gh: Not Found (HTTP 404)', file=sys.stderr)
        sys.exit(1)
    print({merge_queue_rules!r})
    sys.exit(0)

if 'api' in argv and 'graphql' in argv and any('labels(first:100' in a for a in argv):
    if {labels_error!r}:
        print('label read failed', file=sys.stderr)
        sys.exit(1)
    pages = json.loads({labels_json!r})
    index = 1 if 'after=next' in argv else 0
    print(json.dumps({{"data": {{"repository": {{"pullRequest": {{"labels": pages[index]}}}}}}}}))
    sys.exit(0)

if 'api' in argv and 'graphql' in argv:
    # Return thread count query response
    nodes = {repr(thread_nodes)}
    payload = {{"data": {{"repository": {{"pullRequest": {{"reviewThreads": {{"nodes": json.loads(nodes), "pageInfo": {{"hasNextPage": False, "endCursor": None}}}}}}}}}}}}
    print(json.dumps(payload))
    sys.exit(0)

print(json.dumps({{"error": "unmatched: " + cmd}}), file=sys.stderr)
sys.exit(1)
""")


class EvaluateMergeReadinessTests(unittest.TestCase):
    def test_wrong_cwd_with_colliding_pr_number_reads_requested_repo(self) -> None:
        # aib-6m4: run from another checkout whose PR 42 is long merged; the
        # requested PR must be read, not the cwd's colliding number.
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, check_states=["FAILURE"])
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "OPEN")
        self.assertIsNone(payload["merged_at"])
        self.assertEqual(payload["pr_url"], "https://github.com/owner/repo/pull/42")
        self.assertFalse(payload["merge_ok"])
        self.assertIn("required_non_green=1", payload["reasons"])

    def test_returned_pr_identity_mismatch_fails_closed(self) -> None:
        mismatches = [
            {"url": "https://github.com/cwd/other/pull/42"},
            {"url": "https://github.com/owner/repo/pull/43"},
            {"url": "https://github.com/owner/repo-fork/pull/42"},
            {"url": "https://ghe.example.com/owner/repo/pull/42"},
            {"url": "not a url"},
            {"url": None},
            {"number": 43},
            {"number": None},
        ]
        for overrides in mismatches:
            with self.subTest(overrides=overrides), FakeBinDir() as fbd:
                _make_gh_evaluate(fbd, review_decision="APPROVED", pr_overrides=overrides)
                result = run_script("evaluate_merge_readiness.py",
                                    ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                    env=fbd.env())
                self.assertNotEqual(result.returncode, 0, result.stdout)
                error = json.loads(result.stderr)
                self.assertFalse(error["ok"])
                self.assertEqual(error["error_code"], "pr-identity-mismatch")

    def test_repository_identity_match_is_case_insensitive(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, review_decision="APPROVED",
                              pr_overrides={"url": "https://github.com/Owner/Repo/pull/42"})
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["merge_ok"])

    def test_merge_queue_unknown_when_rules_endpoint_fails(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "o", "--repo", "r", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["merge_queue"])
        self.assertEqual(payload["merge_command"], "gh pr merge 42 --repo o/r --squash")

    def test_merge_queue_detected_selects_auto_merge_command(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, merge_queue_rules=1)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "o", "--repo", "r", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["merge_queue"])
        self.assertEqual(payload["merge_command"], "gh pr merge 42 --repo o/r --squash --auto")

    def test_no_merge_queue_rule_selects_direct_merge(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, merge_queue_rules=0)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "o", "--repo", "r", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_queue"])
        self.assertEqual(payload["merge_command"], "gh pr merge 42 --repo o/r --squash")

    def test_merge_ok_when_all_conditions_green(self) -> None:
        def page(names, more=False, cursor=None):
            return {"nodes": [{"name": name} for name in names],
                    "pageInfo": {"hasNextPage": more, "endCursor": cursor}}

        # Reconfigure the same PR between calls: removal must be visible on the
        # next evaluation, without a persistent hold latch. Queue detection
        # must never bypass the hold or fail-closed evidence handling.
        cases = [
            ([page([])], False, False),
            ([page(["owner-merge-soon", "Owner-Merge"])], False, False),
            ([page(["owner-merge"])], True, False),
            ([page([f"tag-{i}" for i in range(100)], True, "next"),
              page(["owner-merge"])], True, False),
            ([page([])], False, False),
            ([None], False, True),
            ([{"nodes": []}], False, True),
            ([page([None])], False, True),
            ([page([], True, None)], False, True),
            ([page([], True, "next"), page([], True, "next")], False, True),
        ]
        for queue in (0, 1):
            with FakeBinDir() as fbd:
                for pages, held, invalid in cases:
                    with self.subTest(queue=queue, pages=pages):
                        _make_gh_evaluate(fbd, review_decision="APPROVED",
                                          merge_queue_rules=queue, label_pages=pages)
                        result = run_script("evaluate_merge_readiness.py",
                                            ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                            env=fbd.env())
                        if invalid:
                            self.assertNotEqual(result.returncode, 0, result.stdout)
                            self.assertFalse(json.loads(result.stderr)["ok"])
                        else:
                            self.assertEqual(result.returncode, 0, result.stderr)
                            payload = json.loads(result.stdout)
                            self.assertEqual(payload["merge_ok"], not held)
                            self.assertEqual(payload["reasons"], ["held-for-owner"] if held else [])
                _make_gh_evaluate(fbd, labels_error=True)
                result = run_script("evaluate_merge_readiness.py",
                                    ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                    env=fbd.env())
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(json.loads(result.stderr)["ok"])

    def test_merge_blocked_by_unresolved_threads(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, unresolved=2)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertTrue(any("unresolved_threads" in r for r in payload["reasons"]))

    def test_merge_blocked_by_failed_required_checks(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, check_states=["FAILURE"])
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertTrue(any("required_non_green" in r for r in payload["reasons"]))

    def test_merge_blocked_for_draft_pr(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, is_draft=True)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertIn("draft", payload["reasons"])

    def test_merge_blocked_for_closed_pr(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, pr_state="CLOSED")
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertTrue(any("state=CLOSED" in r for r in payload["reasons"]))

    def test_merge_blocked_by_changes_requested(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, review_decision="CHANGES_REQUESTED")
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertTrue(any("CHANGES_REQUESTED" in r for r in payload["reasons"]))

    def test_pending_checks_block_merge(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, check_states=["PENDING"])
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        payload = json.loads(result.stdout)
        self.assertFalse(payload["merge_ok"])
        self.assertTrue(any("required_non_green" in r for r in payload["reasons"]))

    def test_skipped_checks_allow_merge(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, check_states=["SKIPPED"])
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        payload = json.loads(result.stdout)
        self.assertTrue(payload["merge_ok"])

    def test_required_checks_unavailable_fails_closed(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd, checks_error="fatal: unable to fetch checks")
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42"],
                                env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("error_code", payload)

    def test_dry_run_flag_accepted(self) -> None:
        with FakeBinDir() as fbd:
            _make_gh_evaluate(fbd)
            result = run_script("evaluate_merge_readiness.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "42", "--dry-run"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)


# ---------------------------------------------------------------------------
# resolve_review_context.py
# ---------------------------------------------------------------------------

class ResolveReviewContextTests(unittest.TestCase):
    def _make_bins(self, bin_dir: FakeBinDir, *,
                   description: str = "Original implementation bead: aib-abc\nhttps://github.com/owner/repo/pull/99",
                   bd_list: list | None = None,
                   review_dependencies: list | None = None,
                   original_id: str = "aib-abc",
                   pr_number: int = 99,
                   head_branch: str | None = None,
                   base_branch: str = "main",
                   show_payloads: dict[str, object] | None = None,
                   cwd_repo: str = "owner/repo",
                   pr_url: str | None = None) -> None:
        """``gh pr view`` answers for ``--repo`` when given; unscoped it answers
        for the cwd checkout, whose PR of the same number is unrelated."""
        review_record = {
            "id": "aib-xyz",
            "description": description,
            "dependencies": review_dependencies or [],
        }
        bd_list_json = json.dumps(bd_list or [])
        pr_json = json.dumps({
            "number": pr_number,
            "url": pr_url or f"https://github.com/owner/repo/pull/{pr_number}",
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "reviewDecision": None,
            "headRefName": head_branch or f"agent/{original_id}",
            "baseRefName": base_branch,
            "mergedAt": None,
            "headRefOid": "abc123",
        })
        original_record = {
            "id": original_id,
            "description": "Implement thing",
            "external_ref": f"gh-pr:{pr_number}",
        }
        show_payloads = {
            "aib-xyz": [review_record],
            original_id: [original_record],
            **(show_payloads or {}),
        }
        show_payloads_json = json.dumps(show_payloads)

        bin_dir.add("bd", f"""
import sys
import json

argv = sys.argv[1:]
if 'show' in argv:
    issue_id = argv[argv.index('show') + 1]
    payload = json.loads({repr(show_payloads_json)}).get(issue_id)
    if payload is not None:
        print(json.dumps(payload))
        sys.exit(0)

if 'list' in argv:
    print({repr(bd_list_json)})
    sys.exit(0)

print(json.dumps({{"error": "unmatched bd: " + ' '.join(argv)}}), file=sys.stderr)
sys.exit(1)
""")
        cwd_pr_json = json.dumps({
            "number": pr_number, "url": f"https://github.com/{cwd_repo}/pull/{pr_number}",
            "state": "MERGED", "isDraft": False, "mergeStateStatus": "UNKNOWN",
            "reviewDecision": "APPROVED", "headRefName": "unrelated", "baseRefName": "main",
            "mergedAt": "2026-02-13T00:00:00Z", "headRefOid": "fff000",
        })
        bin_dir.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
cmd = ' '.join(argv)

if argv[:2] == ['repo', 'view']:
    print({cwd_repo!r})
    sys.exit(0)

if 'pr' in argv and 'view' in argv:
    print({repr(pr_json)} if '--repo' in argv else {repr(cwd_pr_json)})
    sys.exit(0)

print(json.dumps({{"error": "unmatched gh: " + cmd}}), file=sys.stderr)
sys.exit(1)
""")

    def test_wrong_checkout_with_colliding_pr_number_fails(self) -> None:
        # aib-1r0: the bead records owner/repo#99, the cwd is another repo
        # whose PR 99 is an unrelated merged PR.
        with FakeBinDir() as fbd:
            self._make_bins(fbd, cwd_repo="butlers/butlers")
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertNotEqual(result.returncode, 0, result.stdout)
        error = json.loads(result.stderr)
        self.assertEqual(error["error_code"], "repo-mismatch")
        self.assertEqual(error["owner_repo"], "owner/repo")

    def test_pr_read_is_scoped_and_reports_repo_source(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd)
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "OPEN")
        self.assertEqual(payload["pr_url"], "https://github.com/owner/repo/pull/99")
        self.assertEqual(payload["repo_source"], "review-bead")

    def test_returned_pr_identity_mismatch_fails_closed(self) -> None:
        for url in ("https://github.com/other/repo/pull/99",
                    "https://github.com/owner/repo/pull/98", ""):
            with self.subTest(url=url), FakeBinDir() as fbd:
                self._make_bins(fbd, pr_url=url or "x")
                result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(json.loads(result.stderr)["error_code"], "pr-identity-mismatch")

    def test_repo_argument_overrides_and_must_match_checkout(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description="Original implementation bead: aib-abc")
            ok = run_script("resolve_review_context.py",
                            ["--issue-id", "aib-xyz", "--repo", "Owner/Repo"], env=fbd.env())
            wrong = run_script("resolve_review_context.py",
                               ["--issue-id", "aib-xyz", "--repo", "other/repo"], env=fbd.env())
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["repo_source"], "argument")
        self.assertEqual(json.loads(wrong.stderr)["error_code"], "repo-mismatch")

    def test_unrecorded_repository_falls_back_to_checkout(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description="Original implementation bead: aib-abc")
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["repo_source"], "cwd")
        self.assertEqual(payload["owner_repo"], "owner/repo")

    def test_ambiguous_recorded_repository_fails(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description=(
                "Original implementation bead: aib-abc\n"
                "https://github.com/owner/repo/pull/99\nhttps://github.com/fork/repo/pull/99"))
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertEqual(json.loads(result.stderr)["error_code"], "ambiguous-pr-repository")

    def test_success_resolves_context_from_description(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd)
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["original_id"], "aib-abc")
        self.assertEqual(payload["pr_number"], 99)
        self.assertEqual(payload["owner"], "owner")
        self.assertEqual(payload["repo"], "repo")
        self.assertEqual(payload["head_branch"], "agent/aib-abc")

    def test_rejects_non_singleton_bd_show_responses_at_every_context_hop(self) -> None:
        review_record = {
            "id": "aib-xyz",
            "description": "Original implementation bead: aib-abc\nhttps://github.com/owner/repo/pull/99",
            "dependencies": [],
        }
        original_record = {"id": "aib-abc", "external_ref": "gh-pr:99"}
        dependency_record = {"id": "aib-dependency", "external_ref": "gh-pr:99"}
        cases = {
            "review": ({"aib-xyz": [review_record, review_record]}, None),
            "original": ({"aib-abc": [original_record, original_record]}, None),
            "dependency": (
                {"aib-dependency": [dependency_record, dependency_record]},
                [{"depends_on_id": "aib-dependency", "type": "blocks"}],
            ),
        }
        for name, (show_payloads, dependencies) in cases.items():
            with self.subTest(name=name):
                with FakeBinDir() as fbd:
                    self._make_bins(
                        fbd,
                        description=(
                            "Review PR (no marker)\nhttps://github.com/owner/repo/pull/99"
                            if dependencies
                            else "Original implementation bead: aib-abc\nhttps://github.com/owner/repo/pull/99"
                        ),
                        review_dependencies=dependencies,
                        show_payloads=show_payloads,
                    )
                    result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stderr)["error_code"], "invalid-show-response")

    def test_rejects_mismatched_bd_show_identity_at_every_context_hop(self) -> None:
        review_record = {
            "id": "aib-other",
            "description": "Original implementation bead: aib-abc\nhttps://github.com/owner/repo/pull/99",
            "dependencies": [],
        }
        original_record = {"id": "aib-other", "external_ref": "gh-pr:99"}
        dependency_record = {"id": "aib-other", "external_ref": "gh-pr:99"}
        cases = {
            "review": ({"aib-xyz": [review_record]}, None),
            "original": ({"aib-abc": [original_record]}, None),
            "dependency": (
                {"aib-dependency": [dependency_record]},
                [{"depends_on_id": "aib-dependency", "type": "blocks"}],
            ),
        }
        for name, (show_payloads, dependencies) in cases.items():
            with self.subTest(name=name):
                with FakeBinDir() as fbd:
                    self._make_bins(
                        fbd,
                        description=(
                            "Review PR (no marker)\nhttps://github.com/owner/repo/pull/99"
                            if dependencies
                            else "Original implementation bead: aib-abc\nhttps://github.com/owner/repo/pull/99"
                        ),
                        review_dependencies=dependencies,
                        show_payloads=show_payloads,
                    )
                    result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stderr)["error_code"], "mismatched-record-id")

    def test_preserves_dotted_child_id_and_expected_branch(self) -> None:
        description = (
            "Original implementation bead: aib-swr.1\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr.1",
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["original_id"], "aib-swr.1")
        self.assertEqual(payload["head_branch"], "agent/aib-swr.1")

    def test_rejects_malformed_dotted_child_without_parent_fallback(self) -> None:
        description = (
            "Original implementation bead: aib-swr.invalid\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            # The parent exists to prove the resolver must not silently fall
            # back from the malformed child token to the parent prefix.
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr",
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "malformed-original-id")

    def test_rejects_malformed_dotted_marker_before_dependency_fallback(self) -> None:
        description = (
            "Original implementation bead: aib.swr.1\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            # The dependency target exists so a malformed marker must fail
            # before the resolver can silently select this different bead.
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr",
                review_dependencies=[{"depends_on_id": "aib-swr", "type": "blocks"}],
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "malformed-original-id")
        self.assertEqual(payload["candidates"], ["aib.swr.1"])

    def test_rejects_explicit_plain_marker_before_dependency_fallback(self) -> None:
        description = (
            "Original implementation bead: aib\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr",
                review_dependencies=[{"depends_on_id": "aib-swr", "type": "blocks"}],
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "malformed-original-id")
        self.assertEqual(payload["candidates"], ["aib"])

    def test_package_resolves_one_pr_and_all_member_acceptance_targets(self):
        owner = "coordinator:owner"
        manifest = {"schema": "beads-delivery/v1", "anchor": "aib-abc",
            "members": ["aib-abc", "aib-terminal"], "owner": owner,
            "outcome": "aib-runtime", "base_branch": "main", "packet": "Current adapter corrections"}
        anchor = {"id": "aib-abc", "assignee": owner, "status": "blocked",
                  "external_ref": "gh-pr:99", "metadata": {"delivery": manifest}}
        member = {"id": "aib-terminal", "assignee": owner, "status": "in_progress",
                  "metadata": {"delivery_anchor": "aib-abc"}}
        with FakeBinDir() as fbd:
            self._make_bins(fbd, show_payloads={"aib-abc": [anchor], "aib-terminal": [member]})
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["delivery_members"], manifest["members"])
        self.assertEqual(payload["implementation_owner"], owner)
        self.assertEqual(payload["pr_number"], 99)
        # A member belongs to the anchor PR; it cannot start its own review.
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description="Original implementation bead: aib-terminal",
                            show_payloads={"aib-terminal": [member]})
            result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stderr)["error_code"], "noncanonical-delivery-original")

    def test_accepted_retained_branch_resolves_exactly_with_package_guards(self):
        original_id = "bu-dhloi1.15"
        accepted = f"agent/{original_id}-vendors-20261109"
        owner = "coordinator:owner"
        delivery = {"schema": "beads-delivery/v1", "anchor": original_id, "members": [original_id, "bu-helper.1"], "owner": owner, "outcome": original_id, "base_branch": "main", "packet": "Current frozen packet", "branch": accepted}
        anchor = {"id": original_id, "assignee": owner, "status": "blocked", "external_ref": "gh-pr:5644", "metadata": {"delivery": delivery}}
        member = {"id": "bu-helper.1", "assignee": owner, "status": "blocked", "metadata": {"delivery_anchor": original_id}}
        cases = [("accepted", {}, 0, None), ("neighbor", {"head_branch": accepted + "-neighbor"}, 1, "invalid-delivery-branch"), ("foreign-head", {"head_branch": "agent/bu-other.15"}, 1, "invalid-delivery-branch"), ("wrong-base", {"base_branch": "other"}, 1, "invalid-delivery-branch"), ("released", {}, 1, "invalid-delivery-package"), ("foreign-owner", {}, 1, "invalid-delivery-package"), ("missing-member", {}, 1, "invalid-show-response"), ("protected", {}, 1, "invalid-delivery-package"), ("ambiguous", {}, 1, "ambiguous-original-id"), ("default-strict", {}, 1, "invalid-delivery-branch")]
        for name, options, expected_exit, error in cases:
            with self.subTest(case=name), FakeBinDir() as fbd:
                current_delivery = dict(delivery)
                current_member = {**member, "metadata": dict(member["metadata"])}
                description = f"Original implementation bead: {original_id}"
                if name == "released": current_delivery["state"] = "released"
                if name == "foreign-owner": current_member["assignee"] = "coordinator:foreign"
                if name == "protected": current_delivery["branch"] = "main"
                if name == "default-strict": current_delivery.pop("branch")
                if name == "ambiguous": description += "\nOriginal implementation bead: bu-other.15"
                current_anchor = {**anchor, "metadata": {"delivery": current_delivery}}
                payloads = {original_id: [current_anchor], member["id"]: [] if name == "missing-member" else [current_member]}
                self._make_bins(fbd, description=description, original_id=original_id, pr_number=5644, show_payloads=payloads, **{"head_branch": accepted, **options})
                result = run_script("resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env())
                self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
                if error:
                    self.assertEqual(json.loads(result.stderr)["error_code"], error)
                else:
                    result = json.loads(result.stdout)
                    self.assertEqual(result["head_branch"], accepted)
                    self.assertEqual(result["original_id"], original_id)
                    self.assertEqual(result["delivery_members"], delivery["members"])

    def test_repeated_original_marker_candidates_fail_as_ambiguous(self) -> None:
        description = (
            "Original implementation bead: aib-swr.1\n"
            "Original implementation bead: aib-swr.2\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            # The first child exists so the old re.search implementation would
            # succeed by silently selecting it instead of reporting ambiguity.
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr.1",
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "ambiguous-original-id")
        self.assertEqual(payload["candidates"], ["aib-swr.1", "aib-swr.2"])

    def test_repeated_original_marker_occurrences_fail_even_with_same_id(self) -> None:
        description = (
            "Original implementation bead: aib-swr.1\n"
            "Original implementation bead: aib-swr.1\n"
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description=description,
                original_id="aib-swr.1",
            )
            result = run_script(
                "resolve_review_context.py",
                ["--issue-id", "aib-xyz"],
                env=fbd.env(),
            )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "ambiguous-original-id")
        self.assertEqual(payload["candidates"], ["aib-swr.1", "aib-swr.1"])

    def test_success_resolves_context_from_review_target_marker(self) -> None:
        description = "REVIEW TARGET BEAD: aib-abc\nhttps://github.com/owner/repo/pull/99"
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description=description)
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["original_id"], "aib-abc")

    def test_success_resolves_context_from_coordinator_wording_without_colon(self) -> None:
        description = (
            "Review PR #1214 for ORIGINAL IMPLEMENTATION BEAD hud-1izx4. "
            "Confirm the Windows-only build script uses required-manifest semantics."
        )
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description=description,
                original_id="hud-1izx4",
                pr_number=1214,
            )
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["original_id"], "hud-1izx4")
        self.assertEqual(payload["pr_number"], 1214)

    def test_ignores_prose_without_a_bead_shaped_original_id(self) -> None:
        description = (
            "Review the original implementation bead before merging. "
            "https://github.com/owner/repo/pull/99"
        )
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description=description, bd_list=[])
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error_code"], "missing-original-id")

    def test_ambiguous_original_id_fails(self) -> None:
        # Different marker families name different candidates, which is ambiguous.
        description = ("Original implementation bead: aib-abc\n"
                       "Review target bead: aib-def\n"
                       "https://github.com/owner/repo/pull/99")
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description=description)
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("ambiguous", payload["error_code"])

    def test_missing_original_id_fails(self) -> None:
        with FakeBinDir() as fbd:
            self._make_bins(fbd, description="No original bead here", bd_list=[])
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertIn("missing", payload["error_code"])

    def test_resolves_from_review_bead_dependency(self) -> None:
        # Coordinator-style linkage: no description marker; the review bead's
        # own dependency points AT the original implementation bead.
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description="Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
                review_dependencies=[{"id": "aib-abc", "dependency_type": "blocks"}],
            )
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["original_id"], "aib-abc")
        self.assertEqual(payload["pr_number"], 99)

    def test_dependency_target_shapes_are_compatible_only_when_unambiguous(self) -> None:
        valid_rows = {
            "legacy": [{"depends_on_id": "aib-abc", "type": "blocks"}],
            "equal-dual": [
                {"id": "aib-abc", "depends_on_id": "aib-abc", "dependency_type": "blocks"}
            ],
        }
        invalid_rows = {
            "conflicting-dual": [{"id": "aib-abc", "depends_on_id": "aib-other"}],
            "missing": [{"dependency_type": "blocks"}],
            "malformed": [{"id": "not_a_bead"}],
            "self-link": [{"id": "aib-xyz"}],
            "duplicate": [{"id": "aib-abc"}, {"id": "aib-abc"}],
        }

        for name, rows in valid_rows.items():
            with self.subTest(name=name):
                with FakeBinDir() as fbd:
                    self._make_bins(
                        fbd,
                        description="Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
                        review_dependencies=rows,
                    )
                    result = run_script(
                        "resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env()
                    )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["original_id"], "aib-abc")

        for name, rows in invalid_rows.items():
            with self.subTest(name=name):
                with FakeBinDir() as fbd:
                    self._make_bins(
                        fbd,
                        description="Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
                        review_dependencies=rows,
                    )
                    result = run_script(
                        "resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env()
                    )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stderr)["error_code"], "invalid-dependency-row")

    def test_resolves_reverse_dependency_from_current_bd_shape_with_null_collections(self) -> None:
        review_record = {
            "id": "aib-xyz",
            "description": "Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
            "dependencies": None,
        }
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description="Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
                bd_list=[
                    {"id": "aib-unrelated", "dependencies": None},
                    {
                        "id": "aib-abc",
                        "dependencies": [{"id": "aib-xyz", "dependency_type": "blocks"}],
                    }
                ],
                show_payloads={"aib-xyz": [review_record]},
            )
            result = run_script(
                "resolve_review_context.py", ["--issue-id", "aib-xyz"], env=fbd.env()
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["original_id"], "aib-abc")

    def test_review_dep_excludes_parent_and_prefers_gh_pr(self) -> None:
        # Parent-child (epic) edges are excluded; among the rest the target
        # carrying a gh-pr external_ref (aib-abc) wins over a gate bead
        # (aib-gate, no gh-pr) so the gate does not masquerade as the original.
        with FakeBinDir() as fbd:
            self._make_bins(
                fbd,
                description="Review PR (no marker)\nhttps://github.com/owner/repo/pull/99",
                review_dependencies=[
                    {"id": "aib-epic", "dependency_type": "parent-child"},
                    {"id": "aib-gate", "dependency_type": "blocks"},
                    {"id": "aib-abc", "dependency_type": "blocks"},
                ],
            )
            result = run_script("resolve_review_context.py",
                                ["--issue-id", "aib-xyz"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["original_id"], "aib-abc")


# ---------------------------------------------------------------------------
# prepare_pr_branch.py
# ---------------------------------------------------------------------------

class PreparePrBranchTests(unittest.TestCase):
    """Review preparation never mutates the implementation branch."""

    @staticmethod
    def _fake_git(*, merge_tree_exit=0, beads_diff="", dirty=""):
        return f"""
import sys
argv = sys.argv[1:]
if argv[:1] in (['rebase'], ['push'], ['commit'], ['add']):
    print('forbidden implementation mutation', file=sys.stderr)
    sys.exit(97)
if argv[:1] == ['checkout'] and argv[1:3] != ['-B', 'review/bd-review']:
    print('author branch checkout attempted', file=sys.stderr)
    sys.exit(98)
if argv[:2] == ['merge-tree', '--write-tree']:
    sys.exit({merge_tree_exit})
if argv[:1] == ['branch']:
    print('origin/main')
elif argv[:1] == ['symbolic-ref']:
    print('review/bd-review')
elif argv[:1] == ['rev-parse']:
    print('same-head')
elif argv[:1] == ['diff']:
    print({beads_diff!r})
elif argv[:1] == ['status']:
    print({dirty!r})
sys.exit(0)
"""

    def _run(self, fake_git, extra_args=()):
        with FakeBinDir() as fbd:
            fbd.add("git", fake_git)
            return run_script(
                "prepare_pr_branch.py",
                ["--base-branch", "main", "--head-branch", "agent/test-1",
                 "--review-id", "bd-review", *extra_args], env=fbd.env(),
            )

    def test_clean_merge_uses_independent_snapshot_without_mutating_author(self):
        result = self._run(self._fake_git())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["review_branch"], "review/bd-review")
        self.assertFalse(payload["rebased"])
        self.assertFalse(payload["pushed_prepared_head"])
        self.assertEqual(payload["head_commit"], "same-head")

    def test_conflict_returns_correction_without_rewriting_author(self):
        result = self._run(self._fake_git(merge_tree_exit=1))
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "corrections-required")
        self.assertEqual(payload["error"], "conflict-with-base")

    def test_unavailable_probe_fails_closed_without_rebase(self):
        result = self._run(self._fake_git(merge_tree_exit=129))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"], "merge-tree-unavailable")

    def test_tracker_divergence_returns_to_owner(self):
        result = self._run(self._fake_git(beads_diff="tracker changes"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "corrections-required")

    def test_dirty_review_worktree_is_preserved(self):
        result = self._run(self._fake_git(dirty=" M file.py"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["error"], "review-worktree-is-dirty")

    def test_real_base_drift_preserves_author_worktree_and_unpublished_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, repo, author, reviewer = [root / name for name in ("remote", "repo", "author", "reviewer")]
            def git(*args, cwd=None):
                result = subprocess.run(["git", *map(str, args)], cwd=cwd, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            git("init", "--bare", remote)
            git("init", "-b", "main", repo)
            git("config", "user.name", "Test", cwd=repo)
            git("config", "user.email", "test@example.invalid", cwd=repo)
            (repo / "base").write_text("baseline")
            git("add", ".", cwd=repo); git("commit", "-m", "base", cwd=repo)
            git("remote", "add", "origin", remote, cwd=repo)
            git("push", "-u", "origin", "main", cwd=repo)
            git("worktree", "add", "-b", "agent/bd-package", author, cwd=repo)
            (author / "adapter").write_text("accepted correction")
            git("add", ".", cwd=author); git("commit", "-m", "correction", cwd=author)
            git("push", "-u", "origin", "agent/bd-package", cwd=author)
            published_head = git("rev-parse", "HEAD", cwd=author)
            # This optional unpublished tail cannot be made hostage to review.
            (author / "adapter").write_text("optional unfinished correction")
            (repo / "unrelated").write_text("concurrent main change")
            git("add", ".", cwd=repo); git("commit", "-m", "main advances", cwd=repo)
            git("push", "origin", "main", cwd=repo)
            git("worktree", "add", "-b", "review/bd-review", reviewer, cwd=repo)
            result = subprocess.run([sys.executable, str(SCRIPTS / "prepare_pr_branch.py"),
                "--base-branch", "main", "--head-branch", "agent/bd-package", "--review-id", "bd-review"],
                cwd=reviewer, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["head_commit"], published_head)
            self.assertEqual(git("rev-parse", "HEAD", cwd=author), published_head)
            self.assertEqual(git("branch", "--show-current", cwd=author), "agent/bd-package")
            self.assertEqual((author / "adapter").read_text(), "optional unfinished correction")
            self.assertEqual((reviewer / "adapter").read_text(), "accepted correction")
            self.assertEqual(git("rev-parse", "origin/agent/bd-package", cwd=repo), published_head)

    def test_clean_local_only_review_commit_is_preserved_before_fetch_or_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote, repo = root / "remote", root / "repo"
            def git(*args):
                result = subprocess.run(["git", *map(str, args)], cwd=repo if repo.exists() else root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            git("init", "--bare", remote)
            git("init", "-b", "review/bd-review", repo)
            git("config", "user.name", "Test"); git("config", "user.email", "test@example.invalid")
            (repo / "published").write_text("base")
            git("add", "."); git("commit", "-m", "published")
            git("remote", "add", "origin", remote)
            git("push", "origin", "HEAD:main")
            (repo / "recovery").write_text("valuable local-only committed work")
            git("add", "."); git("commit", "-m", "unpublished recovery")
            recovery_head = git("rev-parse", "HEAD")
            # A fetch would fail because the requested PR branch does not
            # exist. The provenance error proves refusal happened first.
            result = subprocess.run([sys.executable, str(SCRIPTS / "prepare_pr_branch.py"),
                "--base-branch", "main", "--head-branch", "agent/bd-package", "--review-id", "bd-review"],
                cwd=repo, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["error"], "review-head-has-no-published-provenance")
            self.assertEqual(git("rev-parse", "HEAD"), recovery_head)
            self.assertEqual(git("branch", "--show-current"), "review/bd-review")
            self.assertEqual((repo / "recovery").read_text(), "valuable local-only committed work")

    def test_dry_run_invokes_no_git(self):
        result = self._run("import sys; sys.exit(99)", ["--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "dry-run")


# ---------------------------------------------------------------------------
# list_review_threads.py
# ---------------------------------------------------------------------------

class ListReviewThreadsTests(unittest.TestCase):
    def test_success_returns_thread_list(self) -> None:
        threads_payload = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "T_1",
                                    "isResolved": False,
                                    "isOutdated": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "id": "C_1",
                                                "databaseId": 1,
                                                "body": "Please add tests",
                                                "path": "foo.py",
                                                "line": 10,
                                                "originalLine": 10,
                                                "url": "https://github.com/owner/repo/pull/1#comment",
                                                "createdAt": "2026-01-01T00:00:00Z",
                                                "author": {"login": "reviewer"},
                                            }
                                        ],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json
print({repr(json.dumps(threads_payload))})
sys.exit(0)
""")
            result = run_script("list_review_threads.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "1"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["thread_count"], 1)
        self.assertEqual(payload["unresolved_count"], 1)

    def test_command_failure_exits_nonzero(self) -> None:
        with FakeBinDir() as fbd:
            fbd.add("gh", """
import sys
print('gh: connection refused', file=sys.stderr)
sys.exit(1)
""")
            result = run_script("list_review_threads.py",
                                ["--owner", "owner", "--repo", "repo", "--pr-number", "1"],
                                env=fbd.env())
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stderr)
        self.assertFalse(payload["ok"])
        self.assertIn("error_code", payload)


# ---------------------------------------------------------------------------
# reply_to_review_thread.py
# ---------------------------------------------------------------------------

class ReplyToReviewThreadTests(unittest.TestCase):
    def _base_args(self):
        return [
            "--owner", "owner",
            "--repo", "repo",
            "--pr-number", "42",
            "--comment-id", "1001",
            "--thread-id", "T_kwDOAbc123",
            "--body", "Fixed now.",
            "--dedupe-key", "aib-test:T_kwDOAbc123:reply",
        ]

    def test_creates_reply_when_no_duplicate(self) -> None:
        # Thread has comments but none with the dedupe key
        empty_thread_payload = json.dumps({
            "data": {
                "node": {
                    "comments": {
                        "nodes": [{"id": "C_1", "databaseId": 1, "body": "other comment",
                                   "author": {"login": "reviewer"}, "url": "https://example.com/c1"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        })
        new_comment_payload = json.dumps({
            "id": 9999,
            "html_url": "https://github.com/owner/repo/pull/42#comment-9999",
        })

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
if 'graphql' in argv:
    print({repr(empty_thread_payload)})
else:
    # POST request to create comment
    print({repr(new_comment_payload)})
sys.exit(0)
""")
            result = run_script("reply_to_review_thread.py", self._base_args(), env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "created")

    def test_skips_duplicate_when_dedupe_key_present(self) -> None:
        dedupe_key = "aib-test:T_kwDOAbc123:reply"
        thread_payload = json.dumps({
            "data": {
                "node": {
                    "comments": {
                        "nodes": [
                            {"id": "C_1", "databaseId": 1,
                             "body": f"Fixed now.\n\ndedupe-key: {dedupe_key}",
                             "author": {"login": "bot"},
                             "url": "https://github.com/owner/repo/pull/42#comment-1"},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        })

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json
print({repr(thread_payload)})
sys.exit(0)
""")
            result = run_script("reply_to_review_thread.py", self._base_args(), env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "duplicate-skipped")

    def test_dry_run_does_not_post_comment(self) -> None:
        """In --dry-run, the POST to create the reply must not be called."""
        empty_thread_payload = json.dumps({
            "data": {
                "node": {
                    "comments": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        })

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
if 'graphql' in argv:
    print({repr(empty_thread_payload)})
    sys.exit(0)

# Any non-graphql call (i.e. the POST mutation) should fail the test
print('POST called in dry-run!', file=sys.stderr)
sys.exit(99)
""")
            result = run_script("reply_to_review_thread.py",
                                self._base_args() + ["--dry-run"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "dry-run")


# ---------------------------------------------------------------------------
# resolve_review_thread.py
# ---------------------------------------------------------------------------

class ResolveReviewThreadTests(unittest.TestCase):
    def _status_payload(self, is_resolved: bool) -> str:
        return json.dumps({
            "data": {
                "node": {"id": "T_kwDOAbc123", "isResolved": is_resolved}
            }
        })

    def _resolve_payload(self) -> str:
        return json.dumps({
            "data": {
                "resolveReviewThread": {
                    "thread": {"id": "T_kwDOAbc123", "isResolved": True}
                }
            }
        })

    def test_resolves_unresolved_thread(self) -> None:
        status_json = self._status_payload(False)
        resolve_json = self._resolve_payload()
        calls: list[str] = []

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
args_str = ' '.join(argv)
# Identify by query content passed via -f query=...
query = ''
for i, a in enumerate(argv):
    if a == '-f':
        kv = argv[i+1] if i+1 < len(argv) else ''
        if kv.startswith('query='):
            query = kv[len('query='):]

if 'resolveReviewThread' in query:
    print({repr(resolve_json)})
else:
    print({repr(status_json)})
sys.exit(0)
""")
            result = run_script("resolve_review_thread.py",
                                ["--thread-id", "T_kwDOAbc123"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "resolved")

    def test_already_resolved_thread_is_skipped(self) -> None:
        status_json = self._status_payload(True)

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json
# Only the status query matters here; mutation should not be called
argv = sys.argv[1:]
query = ''
for i, a in enumerate(argv):
    if a == '-f':
        kv = argv[i+1] if i+1 < len(argv) else ''
        if kv.startswith('query='):
            query = kv[len('query='):]
if 'resolveReviewThread' in query:
    print('mutation called on already-resolved thread!', file=sys.stderr)
    sys.exit(99)
print({repr(status_json)})
sys.exit(0)
""")
            result = run_script("resolve_review_thread.py",
                                ["--thread-id", "T_kwDOAbc123"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "already-resolved")

    def test_dry_run_skips_mutation_for_unresolved_thread(self) -> None:
        status_json = self._status_payload(False)

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
query = ''
for i, a in enumerate(argv):
    if a == '-f':
        kv = argv[i+1] if i+1 < len(argv) else ''
        if kv.startswith('query='):
            query = kv[len('query='):]
if 'resolveReviewThread' in query:
    print('mutation called in dry-run!', file=sys.stderr)
    sys.exit(99)
print({repr(status_json)})
sys.exit(0)
""")
            result = run_script("resolve_review_thread.py",
                                ["--thread-id", "T_kwDOAbc123", "--dry-run"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "dry-run")


# ---------------------------------------------------------------------------
# create_inline_review_comment.py
# ---------------------------------------------------------------------------

class CreateInlineReviewCommentTests(unittest.TestCase):
    def _base_args(self):
        return [
            "--owner", "owner",
            "--repo", "repo",
            "--pr-number", "42",
            "--commit-id", "abc123",
            "--path", "foo.py",
            "--line", "10",
            "--body", "This looks problematic.",
            "--dedupe-key", "aib-test:foo.py:10",
        ]

    def test_creates_comment_when_no_duplicate(self) -> None:
        list_payload = json.dumps([])  # no existing comments
        create_payload = json.dumps({
            "id": 7777,
            "html_url": "https://github.com/owner/repo/pull/42#comment-7777",
        })

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
# Distinguish list vs create by presence of mutation params
if '-F' in argv and 'per_page=100' in ' '.join(argv):
    print({repr(list_payload)})
else:
    print({repr(create_payload)})
sys.exit(0)
""")
            result = run_script("create_inline_review_comment.py",
                                self._base_args(), env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "created")

    def test_skips_duplicate_comment(self) -> None:
        dedupe_key = "aib-test:foo.py:10"
        list_payload = json.dumps([{
            "id": 1234,
            "path": "foo.py",
            "line": 10,
            "original_line": 10,
            "body": f"This looks problematic.\n\ndedupe-key: {dedupe_key}",
            "html_url": "https://github.com/owner/repo/pull/42#comment-1234",
        }])

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json
print({repr(list_payload)})
sys.exit(0)
""")
            result = run_script("create_inline_review_comment.py",
                                self._base_args(), env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "duplicate-skipped")

    def test_dry_run_does_not_create_comment(self) -> None:
        list_payload = json.dumps([])  # no existing comments

        with FakeBinDir() as fbd:
            fbd.add("gh", f"""
import sys
import json

argv = sys.argv[1:]
# List call is allowed; create call (no per_page) should not happen
if '-F' in argv and 'per_page=100' in ' '.join(argv):
    print({repr(list_payload)})
    sys.exit(0)

# Any non-list call = mutation = should not happen in dry-run
print('POST called in dry-run!', file=sys.stderr)
sys.exit(99)
""")
            result = run_script("create_inline_review_comment.py",
                                self._base_args() + ["--dry-run"],
                                env=fbd.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "dry-run")


class DocumentedBootstrapProjectionTests(unittest.TestCase):
    def test_native_show_array_projects_requested_record_and_rejects_bad_identity(self):
        text = (SKILL_ROOT / "SKILL.md").read_text()
        program = text.split('| jq -e --arg id "$ISSUE_ID" ', 1)[1].split("')", 1)[0][1:]
        record = {"id": "probe-a", "title": "Review", "notes": "current", "unneeded": "omit"}
        for payload in ([record], [], [record, record], [{"id": "probe-b"}], record):
            with self.subTest(payload=payload):
                result = subprocess.run(["jq", "-e", "--arg", "id", "probe-a", program],
                                        input=json.dumps(payload), capture_output=True, text=True)
                if payload == [record]:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)["id"], "probe-a")
                    self.assertNotIn("unneeded", json.loads(result.stdout))
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
