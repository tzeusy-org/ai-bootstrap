#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Behavioral tests for the cleanup subskill's report-only scan."""

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
SCRIPT = SKILL_ROOT / "scripts" / "cleanup_scan.py"


class FakeBinDir:
    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None

    def __enter__(self) -> "FakeBinDir":
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        return self

    def __exit__(self, *args: object) -> None:
        assert self._tmp is not None
        self._tmp.cleanup()

    def add(self, name: str, body: str) -> None:
        assert self.path is not None
        executable = self.path / name
        executable.write_text(f"#!/usr/bin/env python3\n{textwrap.dedent(body)}", encoding="utf-8")
        executable.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        assert self.path is not None
        environment = os.environ.copy()
        environment["PATH"] = f"{self.path}:{environment.get('PATH', '')}"
        environment.update(extra)
        return environment


def install_fakes(fake_bin: FakeBinDir) -> None:
    fake_bin.add(
        "bd",
        """
        import json
        import os
        import sys

        fixture = json.loads(open(os.environ["CLEANUP_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["CLEANUP_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "bd", "argv": sys.argv[1:]}) + "\\n")
        argv = sys.argv[1:]
        if argv[:1] == ["-C"]:
            argv = argv[2:]

        def fail(key):
            message = fixture.get("failures", {}).get(key)
            if message:
                print(message, file=sys.stderr)
                raise SystemExit(1)

        if argv == ["config", "get", "issue_prefix", "--json"]:
            print(json.dumps({"key": "issue_prefix", "value": "aib"}))
            raise SystemExit(0)

        if argv[:2] == ["dep", "list"]:
            issue_id = argv[2]
            fail(f"bd-dep:{issue_id}")
            print(json.dumps(fixture.get("dependencies", {}).get(issue_id, [])))
            raise SystemExit(0)

        if argv[:2] == ["worktree", "list"]:
            fail("bd-worktree-list")
            print(json.dumps(fixture.get("worktree_entries", [])))
            raise SystemExit(0)

        if "list" in argv:
            label = argv[argv.index("--label") + 1] if "--label" in argv else ""
            status = next((part.split("=", 1)[1] for part in argv if part.startswith("--status=")), "")
            key = f"bd-list:{status}:{label}"
            fail(key)
            if status == "in_progress":
                print(json.dumps(fixture.get("in_progress", [])))
            elif label == "review-running":
                print(json.dumps(fixture.get("review_running", [])))
            elif label == "pr-review" and status == "blocked":
                print(json.dumps(fixture.get("blocked_pr_review", [])))
            elif label == "pr-review-task":
                print(
                    json.dumps(
                        fixture.get(
                            "pr_review_tasks_all",
                            fixture.get("blocked_pr_review_tasks", []),
                        )
                    )
                )
            elif label == "pr-review":
                print(json.dumps(fixture.get("pr_review_all", [])))
            elif status == "blocked":
                print(json.dumps(fixture.get("blocked", [])))
            else:
                print(json.dumps([]))
            raise SystemExit(0)

        if "show" in argv:
            issue_id = argv[argv.index("show") + 1]
            fail(f"bd-show:{issue_id}")
            payload = fixture.get("shows", {}).get(issue_id)
            if payload is None:
                print("unmatched show " + issue_id, file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(payload))
            raise SystemExit(0)

        if argv[:2] == ["dolt", "status"]:
            fail("bd-dolt-status")
            print("healthy")
            raise SystemExit(0)
        if argv[:1] == ["doctor"]:
            fail("bd-doctor")
            print("healthy")
            raise SystemExit(0)
        print("unmatched bd command", file=sys.stderr)
        raise SystemExit(1)
        """,
    )
    fake_bin.add(
        "gh",
        """
        import json
        import os
        import sys

        fixture = json.loads(open(os.environ["CLEANUP_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["CLEANUP_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "gh", "argv": sys.argv[1:]}) + "\\n")
        argv = sys.argv[1:]
        if argv[:2] == ["repo", "view"]:
            print("owner/repo")
            raise SystemExit(0)
        if argv[:2] == ["pr", "list"]:
            message = fixture.get("failures", {}).get("gh-pr-list")
            if message:
                print(message, file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(fixture.get("open_prs", [])))
            raise SystemExit(0)
        if argv[:2] == ["pr", "view"]:
            number = argv[2]
            message = fixture.get("failures", {}).get(f"gh-pr-view:{number}")
            if message:
                print(message, file=sys.stderr)
                raise SystemExit(1)
            payload = fixture.get("prs", {}).get(str(number))
            if payload is None:
                print("unmatched pr " + str(number), file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(payload))
            raise SystemExit(0)
        print("unmatched gh command", file=sys.stderr)
        raise SystemExit(1)
        """,
    )
    fake_bin.add(
        "git",
        """
        import json
        import os
        import sys

        fixture = json.loads(open(os.environ["CLEANUP_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["CLEANUP_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "git", "argv": sys.argv[1:]}) + "\\n")
        argv = sys.argv[1:]

        def fail(key):
            message = fixture.get("failures", {}).get(key)
            if message:
                print(message, file=sys.stderr)
                raise SystemExit(1)

        if "ls-remote" in argv:
            branch = argv[-1].removeprefix("agent/")
            fail(f"git-ls-remote:{branch}")
            if branch in fixture.get("remote_branches", []):
                print("deadbeef\\trefs/heads/agent/" + branch)
            raise SystemExit(0)
        if "log" in argv:
            worktree = argv[argv.index("-C") + 1] if "-C" in argv else ""
            worktree_id = worktree.rstrip("/").split("/")[-1]
            fail(f"git-log:{worktree_id}")
            if worktree_id in fixture.get("unpublished_worktrees", []):
                print("abc123 useful commit")
            raise SystemExit(0)
        raise SystemExit(0)
        """,
    )


def bead(issue_id: str, status: str, **extra: object) -> dict[str, object]:
    return {"id": issue_id, "status": status, **extra}


def heartbeat(timestamp: str) -> str:
    return f"[beads-heartbeat]\nowner=coordinator:test\nlast_heartbeat_at={timestamp}\n[/beads-heartbeat]"


def worktree_entry(path_id: str | None = None, branch_id: str | None = None) -> dict[str, str]:
    """Build a ``bd worktree list --json`` row, mirroring its real (untruncated) shape."""
    entry: dict[str, str] = {}
    if path_id is not None:
        entry["path"] = f"/safe/parallel-agents/{path_id}"
    if branch_id is not None:
        entry["branch"] = f"agent/{branch_id}"
    return entry


class CleanupScanTests(unittest.TestCase):
    maxDiff = None

    def run_fixture(
        self,
        fixture: dict[str, object],
        *,
        now: str = "2026-08-14T04:00:00Z",
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str]:
        with tempfile.TemporaryDirectory() as tmp, FakeBinDir() as fake_bin:
            root = Path(tmp)
            repo_root = root / "repo"
            repo_root.mkdir()
            for worktree_id in fixture.get("worktree_dirs", []):
                (repo_root / ".worktrees" / "parallel-agents" / str(worktree_id)).mkdir(parents=True)
            fixture_path = root / "fixture.json"
            calls_path = root / "calls.jsonl"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            normalizer = None
            if "normalizer_stdout" in fixture:
                normalizer = root / "normalizer.py"
                normalizer.write_text(
                    "\n".join(
                        (
                            "import json",
                            "import os",
                            "import sys",
                            "fixture = json.loads(open(os.environ['CLEANUP_FIXTURE'], encoding='utf-8').read())",
                            "print(fixture['normalizer_stdout'])",
                            "raise SystemExit(int(fixture.get('normalizer_exit', 0)))",
                        )
                    ),
                    encoding="utf-8",
                )
            install_fakes(fake_bin)
            argv = [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--now", now]
            if normalizer is not None:
                argv.extend(["--normalizer", str(normalizer)])
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                env=fake_bin.env(
                    CLEANUP_FIXTURE=str(fixture_path),
                    CLEANUP_CALLS=str(calls_path),
                ),
            )
            calls = [
                json.loads(line)
                for line in calls_path.read_text(encoding="utf-8").splitlines()
            ] if calls_path.exists() else []
        return result, calls, str(repo_root)

    def test_empty_scan_emits_the_compact_versioned_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeBinDir() as fake_bin:
            repo_root = Path(tmp)
            fake_bin.add(
                "bd",
                """
                import json
                import sys

                if "list" in sys.argv:
                    print(json.dumps([]))
                elif "config" in sys.argv:
                    print(json.dumps({"key": "issue_prefix", "value": "aib"}))
                elif "worktree" in sys.argv:
                    print("")
                else:
                    print("ok")
                """,
            )
            fake_bin.add("gh", "import json\nprint(json.dumps([]))")
            fake_bin.add("git", "raise SystemExit(0)")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(repo_root),
                    "--now",
                    "2026-08-14T04:00:00Z",
                ],
                capture_output=True,
                text=True,
                env=fake_bin.env(),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "blockers": [],
                "claims": [],
                "dolt": {"doctor": "healthy", "status": "healthy"},
                "errors": [],
                "generated_at": "2026-08-14T04:00:00Z",
                "pr_review": {
                    "errors": [],
                    "findings": [],
                    "schema": "beads-pr-review-normalization/v1",
                    "self_heal_candidates": [],
                    "status": "empty",
                },
                "review_locks": [],
                "schema": "beads-cleanup-scan/v1",
                "status": "empty",
                "worktrees": [],
            },
        )
        self.assertEqual(result.stderr, "")

    def test_reports_stale_claims_dependencies_worktrees_locks_and_unpublished_work(self) -> None:
        fixture = {
            "in_progress": [
                bead("aib-stale", "in_progress", assignee="other", notes=heartbeat("2026-08-14T03:00:00Z")),
                bead("aib-live", "in_progress", assignee="other", notes=heartbeat("2026-08-14T03:50:00Z")),
            ],
            "blocked": [bead("aib-blocked", "blocked"), bead("aib-unblock", "blocked")],
            "review_running": [bead("aib-lock", "blocked", notes=heartbeat("2026-08-14T03:00:00Z"))],
            "dependencies": {
                "aib-blocked": [{"id": "aib-dep-open"}, {"id": "aib-dep-closed"}],
                "aib-unblock": [{"id": "aib-dep-closed"}],
            },
            "shows": {
                "aib-dep-open": [bead("aib-dep-open", "open")],
                "aib-dep-closed": [bead("aib-dep-closed", "closed")],
                "aib-stale": [bead("aib-stale", "in_progress")],
                "aib-closed": [bead("aib-closed", "closed")],
            },
            "worktree_entries": [worktree_entry(path_id="aib-stale"), worktree_entry(path_id="aib-closed")],
            "worktree_dirs": ["aib-stale", "aib-closed"],
            "remote_branches": ["aib-stale", "aib-closed"],
            "unpublished_worktrees": ["aib-stale"],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, calls, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        stale = next(item for item in payload["claims"] if item["id"] == "aib-stale")
        self.assertEqual(stale["heartbeat_state"], "stale")
        self.assertTrue(stale["worktree_exists"])
        self.assertTrue(stale["remote_branch_exists"])
        self.assertTrue(stale["unpublished_work"])
        self.assertEqual(stale["recommendation"], "preserve-unpublished-work")
        live = next(item for item in payload["claims"] if item["id"] == "aib-live")
        self.assertEqual(live["heartbeat_state"], "fresh")
        self.assertEqual(live["recommendation"], "preserve-live-claim")
        blocked = next(item for item in payload["blockers"] if item["id"] == "aib-blocked")
        self.assertFalse(blocked["all_blockers_closed"])
        self.assertEqual(blocked["recommendation"], "remain-blocked")
        unblocked = next(item for item in payload["blockers"] if item["id"] == "aib-unblock")
        self.assertTrue(unblocked["all_blockers_closed"])
        self.assertEqual(unblocked["recommendation"], "unblock-candidate")
        lock = payload["review_locks"][0]
        self.assertEqual(lock["id"], "aib-lock")
        self.assertEqual(lock["recommendation"], "release-review-lock-candidate")
        worktree = next(item for item in payload["worktrees"] if item["worktree_id"] == "aib-closed")
        self.assertEqual(worktree["recommendation"], "cleanup-eligible-after-verification")
        self.assertNotIn(repo_root, result.stdout + result.stderr)
        self.assertTrue(any(call["tool"] == "git" for call in calls))

    def test_claims_resolve_transferred_reviewer_worktree_by_branch_not_directory_name(self) -> None:
        # The coordinator's two-stage review topology transfers an existing PR
        # worktree to the reviewer instead of creating a same-named one: the
        # directory is keyed by the review bead id (aib-review) but has the
        # *original* bead's branch (agent/aib-original) checked out inside it.
        fixture = {
            "in_progress": [bead("aib-original", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z"))],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [worktree_entry(path_id="aib-review", branch_id="aib-original")],
            "worktree_dirs": ["aib-review"],
            "remote_branches": ["aib-original"],
            "unpublished_worktrees": ["aib-review"],
            "shows": {
                "aib-review": [bead("aib-review", "closed")],
                "aib-original": [bead("aib-original", "in_progress")],
            },
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        claim = next(item for item in payload["claims"] if item["id"] == "aib-original")
        self.assertTrue(claim["worktree_exists"])
        self.assertTrue(claim["remote_branch_exists"])
        self.assertTrue(claim["unpublished_work"])
        self.assertEqual(claim["recommendation"], "preserve-unpublished-work")
        self.assertNotIn({"code": "worktree-unavailable", "scope": "claims"}, payload["errors"])
        self.assertEqual(payload["status"], "success")

    def test_claims_ordinary_same_name_worktree_topology_still_resolves(self) -> None:
        fixture = {
            "in_progress": [bead("aib-plain", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z"))],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [worktree_entry(path_id="aib-plain", branch_id="aib-plain")],
            "worktree_dirs": ["aib-plain"],
            "remote_branches": ["aib-plain"],
            "unpublished_worktrees": [],
            "shows": {"aib-plain": [bead("aib-plain", "closed")]},
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        claim = next(item for item in payload["claims"] if item["id"] == "aib-plain")
        self.assertTrue(claim["worktree_exists"])
        self.assertFalse(claim["unpublished_work"])
        self.assertEqual(claim["recommendation"], "release-claim-candidate")
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["status"], "success")

    def test_claims_missing_worktree_directory_remains_manual_triage(self) -> None:
        # "aib-missing" is registered as a bare branch reference (no
        # associated directory/path id to correlate against) and no
        # directory for it -- under any name -- actually exists on disk.
        # This must stay a fail-closed manual-triage, never silently resolved.
        fixture = {
            "in_progress": [bead("aib-missing", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z"))],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [worktree_entry(branch_id="aib-missing")],
            "worktree_dirs": [],
            "remote_branches": ["aib-missing"],
            "unpublished_worktrees": [],
            "shows": {"aib-missing": [bead("aib-missing", "in_progress")]},
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        claim = next(item for item in payload["claims"] if item["id"] == "aib-missing")
        self.assertTrue(claim["worktree_exists"])
        self.assertIsNone(claim["unpublished_work"])
        self.assertEqual(claim["recommendation"], "manual-triage")
        self.assertEqual(payload["errors"], [{"code": "worktree-unavailable", "scope": "claims"}])
        self.assertEqual(payload["status"], "partial")

    def test_claims_ambiguous_worktree_branch_registration_remains_manual_triage(self) -> None:
        # Two distinct worktree directories both claim to host agent/aib-ambig
        # -- a shape git's own branch-uniqueness guarantee should forbid.
        # Refuse to guess which one is real rather than picking either.
        fixture = {
            "in_progress": [bead("aib-ambig", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z"))],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [
                worktree_entry(path_id="aib-dupe-a", branch_id="aib-ambig"),
                worktree_entry(path_id="aib-dupe-b", branch_id="aib-ambig"),
            ],
            "worktree_dirs": ["aib-dupe-a", "aib-dupe-b"],
            "remote_branches": ["aib-ambig"],
            "unpublished_worktrees": [],
            "shows": {
                "aib-dupe-a": [bead("aib-dupe-a", "closed")],
                "aib-dupe-b": [bead("aib-dupe-b", "closed")],
                "aib-ambig": [bead("aib-ambig", "in_progress")],
            },
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        claim = next(item for item in payload["claims"] if item["id"] == "aib-ambig")
        self.assertTrue(claim["worktree_exists"])
        self.assertIsNone(claim["unpublished_work"])
        self.assertEqual(claim["recommendation"], "manual-triage")
        self.assertEqual(payload["errors"], [{"code": "ambiguous-worktree", "scope": "claims"}])
        self.assertEqual(payload["status"], "partial")

    def test_claims_fresh_heartbeat_preserved_across_transferred_topology(self) -> None:
        fixture = {
            "in_progress": [bead("aib-fresh-original", "in_progress", notes=heartbeat("2026-08-14T03:50:00Z"))],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [worktree_entry(path_id="aib-fresh-review", branch_id="aib-fresh-original")],
            "worktree_dirs": ["aib-fresh-review"],
            "remote_branches": ["aib-fresh-original"],
            "unpublished_worktrees": [],
            "shows": {
                "aib-fresh-review": [bead("aib-fresh-review", "closed")],
                "aib-fresh-original": [bead("aib-fresh-original", "in_progress")],
            },
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        claim = next(item for item in payload["claims"] if item["id"] == "aib-fresh-original")
        self.assertEqual(claim["heartbeat_state"], "fresh")
        self.assertEqual(claim["recommendation"], "preserve-live-claim")
        self.assertTrue(claim["worktree_exists"])
        self.assertFalse(claim["unpublished_work"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["status"], "success")

    def test_dependency_target_shapes_are_compatible_only_when_unambiguous(self) -> None:
        valid_rows = {
            "current": [{"id": "aib-dependency"}],
            "legacy": [{"depends_on_id": "aib-dependency"}],
            "equal-dual": [{"id": "aib-dependency", "depends_on_id": "aib-dependency"}],
        }
        invalid_rows = {
            "conflicting-dual": [{"id": "aib-dependency", "depends_on_id": "aib-other"}],
            "missing": [{"type": "blocks"}],
            "malformed": [{"id": "not_a_bead"}],
            "self-link": [{"id": "aib-blocked"}],
            "duplicate": [{"id": "aib-dependency"}, {"id": "aib-dependency"}],
        }
        base_fixture = {
            "in_progress": [],
            "blocked": [bead("aib-blocked", "blocked")],
            "review_running": [],
            "shows": {"aib-dependency": [bead("aib-dependency", "closed")]},
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        for name, rows in valid_rows.items():
            with self.subTest(name=name):
                result, _, _ = self.run_fixture(
                    {**base_fixture, "dependencies": {"aib-blocked": rows}}
                )
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["blockers"][0]["recommendation"], "unblock-candidate")
                self.assertNotIn({"code": "invalid-record", "scope": "blockers"}, payload["errors"])

        for name, rows in invalid_rows.items():
            with self.subTest(name=name):
                result, _, _ = self.run_fixture(
                    {**base_fixture, "dependencies": {"aib-blocked": rows}}
                )
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(payload["blockers"][0]["recommendation"], "manual-triage")
                self.assertIn({"code": "invalid-record", "scope": "blockers"}, payload["errors"])

    def test_cleanup_embeds_canonical_dotted_review_findings_without_reparsing(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        fixture = {
            "in_progress": [],
            "blocked": [bead(original_id, "blocked", labels=["pr-review"], external_ref="gh-pr:41")],
            "review_running": [],
            "blocked_pr_review": [
                bead(original_id, "blocked", labels=["pr-review"], external_ref="gh-pr:41"),
                bead(review_id, "blocked", labels=["pr-review", "pr-review-task"], created_at="2026-08-14T03:00:00Z"),
            ],
            "blocked_pr_review_tasks": [],
            "shows": {
                original_id: [bead(original_id, "blocked", external_ref="gh-pr:41")],
                review_id: [
                    {
                        "id": review_id,
                        "description": f"Original implementation bead: {original_id}\nhttps://github.com/owner/repo/pull/41",
                        "dependencies": [],
                    }
                ],
            },
            "prs": {
                "41": {
                    "number": 41,
                    "url": "https://github.com/owner/repo/pull/41",
                    "state": "OPEN",
                    "isDraft": False,
                    "mergeStateStatus": "CLEAN",
                    "reviewDecision": None,
                    "headRefName": f"agent/{original_id}",
                    "baseRefName": "main",
                    "headRefOid": "abc123",
                    "createdAt": "2026-08-14T03:00:00Z",
                    "mergedAt": None,
                }
            },
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        review = next(item for item in json.loads(result.stdout)["pr_review"]["findings"] if item["review_id"] == review_id)
        self.assertEqual(review["original_id"], original_id)
        self.assertEqual(review["recommendation"], "dispatch-canonical-review")

    def test_nonzero_normalizer_discards_even_valid_stdout_as_partial_manual_triage(self) -> None:
        normalizer_payload = {
            "errors": [],
            "findings": [],
            "generated_at": "2026-08-14T04:00:00Z",
            "schema": "beads-pr-review-normalization/v1",
            "self_heal_candidates": [
                {
                    "canonical_review_id": None,
                    "context_status": "resolved",
                    "cooldown_until": "2026-08-14T04:05:00Z",
                    "original_id": "aib-swr.1",
                    "pr_number": 41,
                    "recommendation": "self-heal-original-and-review-wiring",
                }
            ],
            "status": "success",
        }
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(normalizer_payload),
            "normalizer_exit": 9,
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        self.assertEqual(payload["pr_review"]["findings"], [])
        self.assertEqual(payload["pr_review"]["self_heal_candidates"], [])
        self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_cleanup_rejects_bool_pr_number_from_normalizer_evidence(self) -> None:
        normalizer_payload = {
            "errors": [],
            "findings": [],
            "schema": "beads-pr-review-normalization/v1",
            "self_heal_candidates": [
                {
                    "canonical_review_id": None,
                    "context_status": "resolved",
                    "cooldown_until": "2026-08-14T04:05:00Z",
                    "original_id": "aib-swr.1",
                    "pr_number": True,
                    "recommendation": "self-heal-original-and-review-wiring",
                }
            ],
            "status": "success",
        }
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(normalizer_payload),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        candidate = payload["pr_review"]["self_heal_candidates"][0]
        self.assertEqual(candidate["context_status"], "invalid-pr-number")
        self.assertIsNone(candidate["pr_number"])
        self.assertEqual(candidate["recommendation"], "manual-triage")
        self.assertIn({"code": "invalid-pr-number", "scope": "pr-review"}, payload["pr_review"]["errors"])

    def test_error_bearing_success_normalizer_is_partial_manual_triage(self) -> None:
        normalizer_payload = {
            "errors": [{"code": "invalid-pr-created-at", "scope": "pr-state"}],
            "findings": [],
            "schema": "beads-pr-review-normalization/v1",
            "self_heal_candidates": [
                {
                    "canonical_review_id": None,
                    "context_status": "resolved",
                    "cooldown_until": "2026-08-14T04:05:00Z",
                    "original_id": "aib-swr.1",
                    "pr_number": 41,
                    "recommendation": "self-heal-original-and-review-wiring",
                }
            ],
            "status": "success",
        }
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(normalizer_payload),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        self.assertIn({"code": "invalid-pr-created-at", "scope": "pr-state"}, payload["pr_review"]["errors"])
        self.assertEqual(payload["pr_review"]["self_heal_candidates"][0]["recommendation"], "manual-triage")
        self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_cleanup_preserves_only_a_lone_pr_state_command_failure_skip(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        skipped_findings = [
            {
                "canonical_review_id": None,
                "context_status": "command-failed",
                "cooldown_until": None,
                "duplicate_of": None,
                "kind": "original",
                "original_id": original_id,
                "pr_number": 41,
                "pr_state": None,
                "recommendation": "skip-command-failure",
                "review_id": None,
            },
            {
                "canonical_review_id": None,
                "context_status": "command-failed",
                "cooldown_until": None,
                "duplicate_of": None,
                "kind": "review-task",
                "original_id": original_id,
                "pr_number": 41,
                "pr_state": None,
                "recommendation": "skip-command-failure",
                "review_id": review_id,
            },
        ]
        cases = {
            "lone-pr-state-failure": (
                [{"code": "command-failed", "scope": "pr-state"}],
                "skip-command-failure",
            ),
            "mixed-open-pr-failure": (
                [
                    {"code": "command-failed", "scope": "pr-state"},
                    {"code": "command-failed", "scope": "open-prs"},
                ],
                "manual-triage",
            ),
        }
        for name, (errors, expected_recommendation) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": errors,
                            "findings": skipped_findings,
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": [],
                            "status": "partial",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["pr_review"]["status"], "partial")
                self.assertTrue(
                    all(
                        finding["recommendation"] == expected_recommendation
                        for finding in payload["pr_review"]["findings"]
                    )
                )

    def test_empty_normalizer_with_actionable_candidate_is_partial_manual_triage(self) -> None:
        normalizer_payload = {
            "errors": [],
            "findings": [],
            "schema": "beads-pr-review-normalization/v1",
            "self_heal_candidates": [
                {
                    "canonical_review_id": None,
                    "context_status": "resolved",
                    "cooldown_until": "2026-08-14T04:05:00Z",
                    "original_id": "aib-swr.1",
                    "pr_number": 41,
                    "recommendation": "self-heal-original-and-review-wiring",
                }
            ],
            "status": "empty",
        }
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(normalizer_payload),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        candidate = payload["pr_review"]["self_heal_candidates"][0]
        self.assertEqual(candidate["recommendation"], "manual-triage")
        self.assertIn({"code": "inconsistent-normalizer-status", "scope": "pr-review"}, payload["pr_review"]["errors"])
        self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_malformed_or_missing_normalizer_collections_fail_closed(self) -> None:
        cases = {
            "missing-errors": {
                "findings": [],
                "schema": "beads-pr-review-normalization/v1",
                "self_heal_candidates": [],
                "status": "success",
            },
            "malformed-findings": {
                "errors": [],
                "findings": {},
                "schema": "beads-pr-review-normalization/v1",
                "self_heal_candidates": [],
                "status": "success",
            },
            "missing-self-heal-candidates": {
                "errors": [],
                "findings": [],
                "schema": "beads-pr-review-normalization/v1",
                "status": "empty",
            },
            "malformed-status": {
                "errors": [],
                "findings": [],
                "schema": "beads-pr-review-normalization/v1",
                "self_heal_candidates": [],
                "status": [],
            },
        }
        for name, normalizer_payload in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(normalizer_payload),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["pr_review"]["status"], "partial")
                self.assertIn({"code": "invalid-normalizer-envelope", "scope": "pr-review"}, payload["pr_review"]["errors"])
                self.assertEqual(payload["pr_review"]["findings"], [])
                self.assertEqual(payload["pr_review"]["self_heal_candidates"], [])
                self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_invalid_required_finding_evidence_is_partial_manual_triage(self) -> None:
        valid_finding = {
            "canonical_review_id": None,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "duplicate_of": None,
            "kind": "original",
            "original_id": "aib-swr.1",
            "pr_number": 41,
            "pr_state": "OPEN",
            "recommendation": "self-heal-review-wiring",
            "review_id": None,
        }
        cases = {
            "invalid-original-id": {**valid_finding, "original_id": "not_a_bead"},
            "missing-pr-number": {**valid_finding, "pr_number": None},
            "state-action-mismatch": {**valid_finding, "pr_state": "CLOSED"},
            "unapproved-action": {**valid_finding, "recommendation": "archive-review"},
            "missing-review-id": {
                **valid_finding,
                "canonical_review_id": "aib-review.1",
                "kind": "review-task",
                "recommendation": "dispatch-canonical-review",
                "review_id": None,
            },
        }
        for name, finding in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": [],
                            "findings": [finding],
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": [],
                            "status": "success",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["pr_review"]["status"], "partial")
                self.assertEqual(payload["pr_review"]["findings"][0]["recommendation"], "manual-triage")
                self.assertIn(
                    {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
                    payload["pr_review"]["errors"],
                )
                self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_invalid_required_self_heal_evidence_is_partial_manual_triage(self) -> None:
        valid_candidate = {
            "canonical_review_id": None,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "original_id": "aib-swr.1",
            "pr_number": 41,
            "recommendation": "self-heal-original-and-review-wiring",
        }
        cases = {
            "invalid-original-id": {**valid_candidate, "original_id": "not_a_bead"},
            "missing-pr-number": {**valid_candidate, "pr_number": None},
            "invalid-canonical-review-id": {
                **valid_candidate,
                "canonical_review_id": "not_a_bead",
                "recommendation": "review-wiring-current",
            },
            "unapproved-action": {**valid_candidate, "recommendation": "archive-review"},
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": [],
                            "findings": [],
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": [candidate],
                            "status": "success",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["pr_review"]["status"], "partial")
                self.assertEqual(
                    payload["pr_review"]["self_heal_candidates"][0]["recommendation"],
                    "manual-triage",
                )
                self.assertIn(
                    {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
                    payload["pr_review"]["errors"],
                )
                self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_cleanup_rejects_self_or_equivocal_source_relations(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        canonical_finding = {
            "canonical_review_id": review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "duplicate_of": None,
            "kind": "review-task",
            "original_id": original_id,
            "pr_number": 41,
            "pr_state": "OPEN",
            "recommendation": "dispatch-canonical-review",
            "review_id": review_id,
        }
        canonical_candidate = {
            "canonical_review_id": review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "original_id": original_id,
            "pr_number": 41,
            "recommendation": "review-wiring-current",
        }
        cases = {
            "valid-control": ([canonical_finding], [canonical_candidate], "success"),
            "original-canonical-self": (
                [
                    {
                        **canonical_finding,
                        "canonical_review_id": original_id,
                        "kind": "original",
                        "review_id": None,
                    }
                ],
                [],
                "partial",
            ),
            "review-id-self-and-actionable-candidate": (
                [
                    {
                        **canonical_finding,
                        "canonical_review_id": original_id,
                        "review_id": original_id,
                    }
                ],
                [{**canonical_candidate, "canonical_review_id": original_id}],
                "partial",
            ),
            "review-canonical-equals-original": (
                [{**canonical_finding, "canonical_review_id": original_id}],
                [],
                "partial",
            ),
            "duplicate-target-equals-original": (
                [
                    {
                        **canonical_finding,
                        "duplicate_of": original_id,
                        "recommendation": "manual-triage",
                    }
                ],
                [],
                "partial",
            ),
        }
        for name, (findings, candidates, expected_status) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": [],
                            "findings": findings,
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": candidates,
                            "status": "success",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(payload["pr_review"]["status"], expected_status)
                if name == "valid-control":
                    self.assertEqual(
                        payload["pr_review"]["self_heal_candidates"][0]["recommendation"],
                        "review-wiring-current",
                    )
                    self.assertEqual(payload["pr_review"]["errors"], [])
                    continue
                for finding in payload["pr_review"]["findings"]:
                    self.assertEqual(finding["recommendation"], "manual-triage")
                for candidate in payload["pr_review"]["self_heal_candidates"]:
                    self.assertEqual(candidate["recommendation"], "manual-triage")
                self.assertIn(
                    {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
                    payload["pr_review"]["errors"],
                )
                self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_empty_success_normalizer_derives_an_empty_nested_report(self) -> None:
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(
                {
                    "errors": [],
                    "findings": [],
                    "schema": "beads-pr-review-normalization/v1",
                    "self_heal_candidates": [],
                    "status": "success",
                }
            ),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["pr_review"]["status"], "empty")
        self.assertEqual(payload["errors"], [])

    def test_dedupe_review_task_rejects_self_linked_canonical_evidence(self) -> None:
        review_id = "aib-review.1"
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(
                {
                    "errors": [],
                    "findings": [
                        {
                            "canonical_review_id": review_id,
                            "context_status": "resolved",
                            "cooldown_until": "2026-08-14T04:05:00Z",
                            "duplicate_of": review_id,
                            "kind": "review-task",
                            "original_id": "aib-swr.1",
                            "pr_number": 41,
                            "pr_state": "OPEN",
                            "recommendation": "dedupe-review-task",
                            "review_id": review_id,
                        }
                    ],
                    "schema": "beads-pr-review-normalization/v1",
                    "self_heal_candidates": [],
                    "status": "success",
                }
            ),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        self.assertEqual(payload["pr_review"]["findings"][0]["recommendation"], "manual-triage")
        self.assertIn(
            {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
            payload["pr_review"]["errors"],
        )
        self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_canonical_candidate_actions_require_matching_resolved_review_findings(self) -> None:
        canonical_review_id = "aib-review.1"
        candidate = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "original_id": "aib-swr.1",
            "pr_number": 41,
        }
        canonical_finding = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "duplicate_of": None,
            "kind": "review-task",
            "original_id": "aib-swr.1",
            "pr_number": 41,
            "pr_state": "OPEN",
            "recommendation": "dispatch-canonical-review",
            "review_id": canonical_review_id,
        }
        mismatches = {
            "missing-finding": [],
            "different-original": [{**canonical_finding, "original_id": "aib-other.1"}],
            "different-pr": [{**canonical_finding, "pr_number": 42}],
            "noncanonical-finding": [{**canonical_finding, "canonical_review_id": "aib-review.2"}],
        }
        for recommendation in ("review-wiring-current", "self-heal-original-wiring"):
            for name, findings in mismatches.items():
                with self.subTest(recommendation=recommendation, name=name):
                    fixture = {
                        "in_progress": [],
                        "blocked": [],
                        "review_running": [],
                        "normalizer_stdout": json.dumps(
                            {
                                "errors": [],
                                "findings": findings,
                                "schema": "beads-pr-review-normalization/v1",
                                "self_heal_candidates": [{**candidate, "recommendation": recommendation}],
                                "status": "success",
                            }
                        ),
                    }

                    result, _, _ = self.run_fixture(fixture)

                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload["status"], "partial")
                    self.assertEqual(payload["pr_review"]["status"], "partial")
                    self.assertEqual(
                        payload["pr_review"]["self_heal_candidates"][0]["recommendation"],
                        "manual-triage",
                    )
                    self.assertIn(
                        {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
                        payload["pr_review"]["errors"],
                    )
                    self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_canonical_candidate_actions_preserve_matching_resolved_review_findings(self) -> None:
        canonical_review_id = "aib-review.1"
        finding = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "duplicate_of": None,
            "kind": "review-task",
            "original_id": "aib-swr.1",
            "pr_number": 41,
            "pr_state": "OPEN",
            "recommendation": "dispatch-canonical-review",
            "review_id": canonical_review_id,
        }
        candidate = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "original_id": "aib-swr.1",
            "pr_number": 41,
        }
        for recommendation in ("review-wiring-current", "self-heal-original-wiring"):
            with self.subTest(recommendation=recommendation):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": [],
                            "findings": [finding],
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": [{**candidate, "recommendation": recommendation}],
                            "status": "success",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["pr_review"]["status"], "success")
                self.assertEqual(
                    payload["pr_review"]["self_heal_candidates"][0]["recommendation"],
                    recommendation,
                )
                self.assertEqual(payload["pr_review"]["errors"], [])

    def test_collection_sanitizer_rejects_cross_record_role_collisions(self) -> None:
        original_id = "aib-swr.1"
        canonical_review_id = "aib-review.1"
        duplicate_review_id = "aib-review.2"
        canonical_finding = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "duplicate_of": None,
            "kind": "review-task",
            "original_id": original_id,
            "pr_number": 41,
            "pr_state": "OPEN",
            "recommendation": "dispatch-canonical-review",
            "review_id": canonical_review_id,
        }
        duplicate_finding = {
            **canonical_finding,
            "duplicate_of": canonical_review_id,
            "recommendation": "dedupe-review-task",
            "review_id": duplicate_review_id,
        }
        canonical_candidate = {
            "canonical_review_id": canonical_review_id,
            "context_status": "resolved",
            "cooldown_until": "2026-08-14T04:05:00Z",
            "original_id": original_id,
            "pr_number": 41,
            "recommendation": "review-wiring-current",
        }
        foreign_canonical = {
            **canonical_finding,
            "canonical_review_id": original_id,
            "original_id": "aib-other.1",
            "review_id": original_id,
        }
        foreign_candidate = {
            **canonical_candidate,
            "canonical_review_id": original_id,
            "original_id": "aib-other.1",
        }
        cases = {
            "valid-distinct-dedupe": (
                [canonical_finding, duplicate_finding],
                [canonical_candidate],
                "success",
            ),
            "review-id-reused-for-another-original": (
                [canonical_finding, {**canonical_finding, "original_id": "aib-other.1"}],
                [],
                "partial",
            ),
            "acyclic-cross-role-sibling-reuse": (
                [
                    canonical_finding,
                    {
                        **canonical_finding,
                        "canonical_review_id": duplicate_review_id,
                        "original_id": canonical_review_id,
                        "review_id": duplicate_review_id,
                    },
                ],
                [],
                "partial",
            ),
            "duplicate-relation-lhs": (
                [canonical_finding, dict(canonical_finding)],
                [],
                "partial",
            ),
            "two-canonicals-for-one-original": (
                [
                    canonical_finding,
                    {
                        **canonical_finding,
                        "canonical_review_id": "aib-review.3",
                        "review_id": "aib-review.3",
                    },
                ],
                [canonical_candidate],
                "partial",
            ),
            "foreign-original-reused-as-review-and-candidate-canonical": (
                [canonical_finding, foreign_canonical],
                [foreign_candidate],
                "partial",
            ),
            "review-id-is-a-foreign-canonical-role": (
                [
                    {
                        **canonical_finding,
                        "canonical_review_id": "aib-review.3",
                        "review_id": "aib-review.3",
                    },
                    {
                        **canonical_finding,
                        "canonical_review_id": "aib-review.3",
                        "duplicate_of": "aib-review.3",
                        "recommendation": "dedupe-review-task",
                    },
                    {
                        **canonical_finding,
                        "canonical_review_id": canonical_review_id,
                        "duplicate_of": canonical_review_id,
                        "original_id": "aib-other.1",
                        "recommendation": "dedupe-review-task",
                        "review_id": "aib-review.2",
                    },
                ],
                [],
                "partial",
            ),
        }
        for name, (findings, candidates, expected_status) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "in_progress": [],
                    "blocked": [],
                    "review_running": [],
                    "normalizer_stdout": json.dumps(
                        {
                            "errors": [],
                            "findings": findings,
                            "schema": "beads-pr-review-normalization/v1",
                            "self_heal_candidates": candidates,
                            "status": "success",
                        }
                    ),
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(payload["pr_review"]["status"], expected_status)
                if name == "valid-distinct-dedupe":
                    self.assertEqual(
                        payload["pr_review"]["findings"][1]["recommendation"],
                        "dedupe-review-task",
                    )
                    self.assertEqual(
                        payload["pr_review"]["self_heal_candidates"][0]["recommendation"],
                        "review-wiring-current",
                    )
                    self.assertEqual(payload["pr_review"]["errors"], [])
                    continue
                self.assertTrue(
                    all(item["recommendation"] == "manual-triage" for item in payload["pr_review"]["findings"])
                )
                self.assertTrue(
                    all(
                        item["recommendation"] == "manual-triage"
                        for item in payload["pr_review"]["self_heal_candidates"]
                    )
                )
                self.assertIn(
                    {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
                    payload["pr_review"]["errors"],
                )

    def test_delegated_cleanup_preserves_reciprocal_review_relations_as_manual_triage(self) -> None:
        first_review_id = "aib-review.1"
        second_review_id = "aib-review.2"
        canonical_findings = [
            {
                "canonical_review_id": first_review_id,
                "context_status": "resolved",
                "cooldown_until": "2026-08-14T04:05:00Z",
                "duplicate_of": None,
                "kind": "review-task",
                "original_id": second_review_id,
                "pr_number": 41,
                "pr_state": "OPEN",
                "recommendation": "dispatch-canonical-review",
                "review_id": first_review_id,
            },
            {
                "canonical_review_id": second_review_id,
                "context_status": "resolved",
                "cooldown_until": "2026-08-14T04:05:00Z",
                "duplicate_of": None,
                "kind": "review-task",
                "original_id": first_review_id,
                "pr_number": 41,
                "pr_state": "OPEN",
                "recommendation": "dispatch-canonical-review",
                "review_id": second_review_id,
            },
        ]
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "normalizer_stdout": json.dumps(
                {
                    "errors": [],
                    "findings": canonical_findings,
                    "schema": "beads-pr-review-normalization/v1",
                    "self_heal_candidates": [],
                    "status": "success",
                }
            ),
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["pr_review"]["status"], "partial")
        self.assertTrue(
            all(item["recommendation"] == "manual-triage" for item in payload["pr_review"]["findings"])
        )
        self.assertIn(
            {"code": "invalid-normalizer-evidence", "scope": "pr-review"},
            payload["pr_review"]["errors"],
        )
        self.assertIn({"code": "normalizer-failed", "scope": "pr-review"}, payload["errors"])

    def test_cleanup_direct_show_requires_singleton_requested_identity(self) -> None:
        blocker_id = "aib-blocker"
        dependency_id = "aib-dependency"
        worktree_id = "aib-worktree"
        normalizer_empty = json.dumps(
            {
                "errors": [],
                "findings": [],
                "schema": "beads-pr-review-normalization/v1",
                "self_heal_candidates": [],
                "status": "empty",
            }
        )
        base_fixture = {
            "in_progress": [],
            "blocked": [bead(blocker_id, "blocked")],
            "review_running": [],
            "dependencies": {blocker_id: [{"depends_on_id": dependency_id}]},
            "normalizer_stdout": normalizer_empty,
            "remote_branches": [],
            "worktree_dirs": [worktree_id],
            "worktree_entries": [worktree_entry(path_id=worktree_id)],
        }
        cases = {
            "valid-control": (
                {
                    dependency_id: [bead(dependency_id, "closed")],
                    worktree_id: [bead(worktree_id, "closed")],
                },
                "success",
                None,
                None,
            ),
            "dependency-non-singleton": (
                {
                    dependency_id: [bead(dependency_id, "closed"), bead(dependency_id, "closed")],
                    worktree_id: [bead(worktree_id, "closed")],
                },
                "partial",
                "blockers",
                "invalid-show-response",
            ),
            "dependency-mismatched-id": (
                {
                    dependency_id: [bead("aib-other.1", "closed")],
                    worktree_id: [bead(worktree_id, "closed")],
                },
                "partial",
                "blockers",
                "mismatched-record-id",
            ),
            "worktree-non-singleton": (
                {
                    dependency_id: [bead(dependency_id, "closed")],
                    worktree_id: [bead(worktree_id, "closed"), bead(worktree_id, "closed")],
                },
                "partial",
                "worktrees",
                "invalid-show-response",
            ),
            "worktree-mismatched-id": (
                {
                    dependency_id: [bead(dependency_id, "closed")],
                    worktree_id: [bead("aib-other.1", "closed")],
                },
                "partial",
                "worktrees",
                "mismatched-record-id",
            ),
        }
        for name, (shows, expected_status, scope, code) in cases.items():
            with self.subTest(name=name):
                result, _, _ = self.run_fixture({**base_fixture, "shows": shows})

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], expected_status)
                if name == "valid-control":
                    self.assertEqual(payload["blockers"][0]["recommendation"], "unblock-candidate")
                    self.assertEqual(payload["worktrees"][0]["recommendation"], "cleanup-eligible-after-verification")
                    continue
                self.assertIn({"code": code, "scope": scope}, payload["errors"])
                if scope == "blockers":
                    self.assertEqual(payload["blockers"][0]["recommendation"], "manual-triage")
                else:
                    self.assertEqual(payload["worktrees"][0]["recommendation"], "manual-triage")

    def test_delegated_cleanup_normalizer_resolver_reads_have_exact_order_and_count(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        claim_id = "aib-claim"
        blocker_id = "aib-blocker"
        dependency_id = "aib-dependency"
        worktree_id = "aib-worktree"
        original = {
            "id": original_id,
            "labels": ["pr-review"],
            "status": "blocked",
            "external_ref": "gh-pr:41",
        }
        review_task = {
            "id": review_id,
            "labels": ["pr-review", "pr-review-task"],
            "status": "blocked",
            "created_at": "2026-08-14T03:00:00Z",
        }
        review_show = {
            "id": review_id,
            "description": (
                f"Original implementation bead: {original_id}\n"
                "https://github.com/owner/repo/pull/41"
            ),
            "dependencies": [],
        }
        pr = {
            "number": 41,
            "url": "https://github.com/owner/repo/pull/41",
            "state": "OPEN",
            "isDraft": False,
            "mergeStateStatus": "CLEAN",
            "reviewDecision": None,
            "headRefName": f"agent/{original_id}",
            "baseRefName": "main",
            "mergedAt": None,
            "headRefOid": "abc123",
            "createdAt": "2026-08-14T03:00:00Z",
        }
        fixture = {
            "in_progress": [
                bead(claim_id, "in_progress", notes=heartbeat("2026-08-14T03:00:00Z")),
            ],
            "blocked": [bead(blocker_id, "blocked")],
            "review_running": [],
            "dependencies": {blocker_id: [{"depends_on_id": dependency_id}]},
            "blocked_pr_review": [original],
            "blocked_pr_review_tasks": [review_task],
            "shows": {
                review_id: [review_show],
                original_id: [original],
                dependency_id: [bead(dependency_id, "closed")],
                worktree_id: [bead(worktree_id, "closed")],
            },
            "prs": {"41": pr},
            "open_prs": [],
            "remote_branches": [claim_id, worktree_id],
            "worktree_dirs": [claim_id, worktree_id],
            "worktree_entries": [worktree_entry(branch_id=worktree_id)],
        }

        result, calls, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "success")
        claim_worktree = f"{repo_root}/.worktrees/parallel-agents/{claim_id}"
        listed_worktree = f"{repo_root}/.worktrees/parallel-agents/{worktree_id}"
        expected = [
            {
                "tool": "bd",
                "argv": [
                    "-C",
                    repo_root,
                    "list",
                    "--status=blocked",
                    "--label",
                    "pr-review",
                    "--json",
                    "--limit",
                    "0",
                ],
            },
            {
                "tool": "bd",
                "argv": ["-C", repo_root, "list", "--label", "pr-review-task", "--json", "--limit", "0"],
            },
            {"tool": "bd", "argv": ["show", review_id, "--json"]},
            {"tool": "bd", "argv": ["show", original_id, "--json"]},
            {"tool": "gh", "argv": ["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]},
            {
                "tool": "gh",
                "argv": [
                    "pr",
                    "view",
                    "41",
                    "--json",
                    "number,url,state,isDraft,mergeStateStatus,reviewDecision,headRefName,baseRefName,mergedAt,headRefOid",
                ],
            },
            {"tool": "gh", "argv": ["pr", "view", "41", "--json", "number,state,mergedAt,createdAt"]},
            {"tool": "bd", "argv": ["-C", repo_root, "config", "get", "issue_prefix", "--json"]},
            {"tool": "gh", "argv": ["pr", "list", "--state", "open", "--json", "number,headRefName,createdAt"]},
            {"tool": "bd", "argv": ["-C", repo_root, "list", "--status=in_progress", "--json", "--limit", "0"]},
            {"tool": "bd", "argv": ["-C", repo_root, "list", "--status=blocked", "--json", "--limit", "0"]},
            {"tool": "bd", "argv": ["-C", repo_root, "list", "--label", "review-running", "--json", "--limit", "0"]},
            {"tool": "bd", "argv": ["-C", repo_root, "worktree", "list", "--json"]},
            {"tool": "bd", "argv": ["-C", repo_root, "dolt", "status"]},
            {"tool": "bd", "argv": ["-C", repo_root, "doctor"]},
            {"tool": "git", "argv": ["ls-remote", "--heads", "origin", f"agent/{claim_id}"]},
            {"tool": "git", "argv": ["-C", claim_worktree, "log", "--oneline", "origin/HEAD..HEAD"]},
            {"tool": "bd", "argv": ["dep", "list", blocker_id, "--json"]},
            {"tool": "bd", "argv": ["show", dependency_id, "--json"]},
            {"tool": "bd", "argv": ["show", worktree_id, "--json"]},
            {"tool": "git", "argv": ["ls-remote", "--heads", "origin", f"agent/{worktree_id}"]},
            {
                "tool": "git",
                "argv": ["-C", listed_worktree, "log", "--oneline", "origin/HEAD..HEAD"],
            },
        ]
        self.assertEqual(calls, expected)

    def test_worktree_correlation_preserves_opaque_ids_containing_review(self) -> None:
        issue_id = "aib-release-review-preserve"
        fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "shows": {issue_id: [bead(issue_id, "closed")]},
            "worktree_entries": [worktree_entry(path_id=issue_id)],
            "worktree_dirs": [issue_id],
            "remote_branches": [issue_id],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, calls, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = json.loads(result.stdout)["worktrees"][0]
        self.assertEqual(worktree["worktree_id"], issue_id)
        self.assertEqual(worktree["issue_id"], issue_id)
        self.assertEqual(worktree["issue_status"], "closed")
        self.assertEqual(worktree["recommendation"], "cleanup-eligible-after-verification")
        self.assertIn(["show", issue_id, "--json"], [call["argv"] for call in calls if call["tool"] == "bd"])

    def test_failed_git_evidence_never_recommends_releasing_a_stale_claim(self) -> None:
        secret = "TOKEN=top-secret /private/absolute/path Traceback"
        fixture = {
            "in_progress": [
                bead("aib-remote-failed", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z")),
                bead("aib-log-failed", "in_progress", notes=heartbeat("2026-08-14T03:00:00Z")),
            ],
            "blocked": [],
            "review_running": [],
            "worktree_entries": [worktree_entry(path_id="aib-log-failed")],
            "worktree_dirs": ["aib-log-failed"],
            "remote_branches": ["aib-log-failed"],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
            "failures": {
                "git-ls-remote:aib-remote-failed": secret,
                "git-log:aib-log-failed": secret,
            },
        }

        result, _, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        for issue_id in ("aib-remote-failed", "aib-log-failed"):
            finding = next(item for item in payload["claims"] if item["id"] == issue_id)
            self.assertEqual(finding["recommendation"], "manual-triage")
        self.assertIn({"code": "command-failed", "scope": "claims"}, payload["errors"])
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", repo_root):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_unknown_beads_states_are_null_and_force_manual_triage(self) -> None:
        secret_status = "TOKEN=top-secret /private/absolute/path Traceback"
        fixture = {
            "in_progress": [],
            "blocked": [bead("aib-blocked", "blocked")],
            "review_running": [bead("aib-lock", secret_status, notes=heartbeat("2026-08-14T03:00:00Z"))],
            "dependencies": {"aib-blocked": [{"depends_on_id": "aib-dependency"}]},
            "shows": {
                "aib-dependency": [bead("aib-dependency", secret_status)],
                "aib-worktree": [bead("aib-worktree", secret_status)],
            },
            "worktree_entries": [worktree_entry(path_id="aib-worktree")],
            "worktree_dirs": ["aib-worktree"],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        blocker = payload["blockers"][0]
        self.assertIsNone(blocker["dependencies"][0]["status"])
        self.assertEqual(blocker["recommendation"], "manual-triage")
        lock = payload["review_locks"][0]
        self.assertEqual(lock["recommendation"], "manual-triage")
        worktree = payload["worktrees"][0]
        self.assertIsNone(worktree["issue_status"])
        self.assertEqual(worktree["recommendation"], "manual-triage")
        for scope in ("blockers", "review-locks", "worktrees"):
            self.assertIn({"code": "invalid-bead-status", "scope": scope}, payload["errors"])
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", repo_root):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_partial_and_fatal_errors_are_sanitized(self) -> None:
        secret = "TOKEN=top-secret /private/absolute/path Traceback"
        partial_fixture = {
            "in_progress": [],
            "blocked": [],
            "review_running": [],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
            "failures": {"bd-dolt-status": secret, "bd-doctor": secret},
        }
        result, _, repo_root = self.run_fixture(partial_fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["dolt"], {"doctor": "unhealthy", "status": "unhealthy"})
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", repo_root):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

        fatal_fixture = {
            "failures": {
                "bd-list:in_progress:": secret,
                "bd-list:blocked:": secret,
                "bd-list::review-running": secret,
                "bd-list:blocked:pr-review": secret,
                "bd-list:blocked:pr-review-task": secret,
                "gh-pr-list": secret,
                "bd-dolt-status": secret,
                "bd-doctor": secret,
                "bd-worktree-list": secret,
            }
        }
        fatal, _, _ = self.run_fixture(fatal_fixture)
        self.assertNotEqual(fatal.returncode, 0)
        self.assertEqual(json.loads(fatal.stdout)["status"], "fatal")
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback"):
            self.assertNotIn(forbidden, fatal.stdout + fatal.stderr)

    def test_invalid_bootstrap_never_echoes_a_secret_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret_now = "TOKEN=top-secret /private/absolute/path Traceback"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(Path(tmp) / "missing"),
                    "--now",
                    secret_now,
                ],
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "fatal")
        self.assertIsNone(payload["generated_at"])
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", tmp):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_unexpected_bootstrap_failure_never_emits_a_traceback_or_raw_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.symlink_to(loop)
            secret_now = "TOKEN=top-secret /private/absolute/path Traceback"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(loop),
                    "--now",
                    secret_now,
                ],
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "fatal")
        self.assertIsNone(payload["generated_at"])
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", tmp):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_malformed_list_data_is_a_sanitized_partial_report(self) -> None:
        fixture = {
            "in_progress": ["TOKEN=top-secret /private/absolute/path"],
            "blocked": [],
            "review_running": [],
            "blocked_pr_review": [],
            "blocked_pr_review_tasks": [],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "invalid-record", "scope": "claims"}, payload["errors"])
        self.assertNotIn("TOKEN=top-secret", result.stdout + result.stderr)
        self.assertNotIn("/private/absolute/path", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
