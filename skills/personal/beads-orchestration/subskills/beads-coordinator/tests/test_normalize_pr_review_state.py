#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Behavioral tests for the coordinator's report-only PR-review normalizer."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "normalize_pr_review_state.py"
RELATION_GRAPH = SKILL_ROOT / "scripts" / "review_relation_graph.py"


def relation_error(relations: object) -> str | None:
    """Load the canonical pure seam directly without running external commands."""
    spec = importlib.util.spec_from_file_location("review_relation_graph", RELATION_GRAPH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.review_relation_error(relations)


class FakeBinDir:
    """Temporary fake command directory used only by subprocess tests."""

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


def run_normalizer(
    repo_root: Path,
    *,
    now: str,
    env: dict[str, str],
    resolver: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(SCRIPT), "--repo-root", str(repo_root), "--now", now]
    if resolver is not None:
        argv.extend(["--resolver", str(resolver)])
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
    )


def install_fakes(fake_bin: FakeBinDir) -> None:
    fake_bin.add(
        "bd",
        """
        import json
        import os
        import sys

        fixture = json.loads(open(os.environ["NORMALIZER_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["NORMALIZER_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "bd", "argv": sys.argv[1:]}) + "\\n")

        argv = sys.argv[1:]
        if argv[:1] == ["-C"]:
            argv = argv[2:]

        def fail(key):
            if key in fixture.get("failures", {}):
                print(fixture["failures"][key], file=sys.stderr)
                raise SystemExit(1)

        if argv == ["config", "get", "issue_prefix", "--json"]:
            fail("bd-config:issue_prefix")
            print(json.dumps(fixture.get("prefix_payload", {"key": "issue_prefix", "value": "aib"})))
            raise SystemExit(0)

        if "list" in argv:
            label = argv[argv.index("--label") + 1] if "--label" in argv else ""
            status = next((part.split("=", 1)[1] for part in argv if part.startswith("--status=")), "")
            key = f"bd-list:{status}:{label}"
            fail(key)
            if label == "pr-review" and status == "blocked":
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
            else:
                records = {}
                for row in fixture.get("blocked_pr_review", []) + fixture.get("pr_review_tasks_all", []):
                    if isinstance(row, dict) and row.get("id"):
                        records[row["id"]] = row
                result = fixture.get("active_associations", list(records.values()))
                sequence = fixture.get("association_sequence")
                if sequence:
                    previous = [json.loads(line) for line in open(os.environ["NORMALIZER_CALLS"])]
                    count = sum(call["tool"] == "bd" and call["argv"] == sys.argv[1:] for call in previous)
                    result = sequence[min(count - 1, len(sequence) - 1)]
                print(json.dumps(result))
            raise SystemExit(0)

        if "show" in argv:
            issue_id = argv[argv.index("show") + 1]
            key = f"bd-show:{issue_id}"
            fail(key)
            payload = fixture.get("shows", {}).get(issue_id)
            if payload is None:
                print("unmatched show " + issue_id, file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(payload))
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

        fixture = json.loads(open(os.environ["NORMALIZER_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["NORMALIZER_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "gh", "argv": sys.argv[1:]}) + "\\n")

        argv = sys.argv[1:]
        if argv[:1] == ["api"]:
            path = argv[1]
            failure = fixture.get("failures", {}).get(f"gh-api:{path}")
            payload = fixture.get("api_payloads", {}).get(path)
            if failure or payload is None:
                print("unavailable API fixture", file=sys.stderr)
                raise SystemExit(1)
            print(json.dumps(payload))
            raise SystemExit(0)
        if argv[:2] == ["repo", "view"]:
            failure = fixture.get("failures", {}).get("gh-repo-view")
            if failure:
                print(failure, file=sys.stderr)
                raise SystemExit(1)
            print("owner/repo")
            raise SystemExit(0)
        if argv[:2] == ["pr", "list"]:
            failure = fixture.get("failures", {}).get("gh-pr-list")
            if failure:
                print(failure, file=sys.stderr)
                raise SystemExit(1)
            rows = fixture.get("open_prs", [])
            sequence = fixture.get("open_pr_sequence")
            if sequence:
                previous = [json.loads(line) for line in open(os.environ["NORMALIZER_CALLS"])]
                count = sum(call["tool"] == "gh" and call["argv"][:2] == ["pr", "list"] for call in previous)
                rows = sequence[min(count - 1, len(sequence) - 1)]
            if fixture.get("simulate_pr_limit") and isinstance(rows, list):
                limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 30
                rows = rows[:limit]
            print(json.dumps(rows))
            raise SystemExit(0)
        if argv[:2] == ["pr", "view"]:
            number = argv[2]
            failure = fixture.get("failures", {}).get(f"gh-pr-view:{number}")
            if failure:
                print(failure, file=sys.stderr)
                raise SystemExit(1)
            payload = fixture.get("prs", {}).get(str(number))
            sequence = fixture.get("pr_view_sequence")
            if fixture.get("pr_view_sequences"):
                sequence = fixture["pr_view_sequences"].get(number)
            if sequence:
                previous = [json.loads(line) for line in open(os.environ["NORMALIZER_CALLS"])]
                count = sum(call["tool"] == "gh" and call["argv"][:3] == argv[:3] for call in previous)
                payload = sequence[min(count - 1, len(sequence) - 1)]
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

        fixture = json.loads(open(os.environ["NORMALIZER_FIXTURE"], encoding="utf-8").read())
        with open(os.environ["NORMALIZER_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"tool": "git", "argv": sys.argv[1:]}) + "\\n")
        if "ls-remote" in sys.argv:
            reads = fixture.get("merge_ref_reads", [""])
            previous = [json.loads(line) for line in open(os.environ["NORMALIZER_CALLS"])]
            count = sum(call["tool"] == "git" and "ls-remote" in call["argv"] for call in previous)
            print(reads[min(count - 1, len(reads) - 1)], end="")
        raise SystemExit(0)
        """,
    )


def review_task(
    issue_id: str,
    original_id: str,
    *,
    created_at: str = "2026-08-14T03:00:00Z",
    status: str = "blocked",
) -> dict[str, object]:
    return {
        "id": issue_id,
        "labels": ["pr-review", "pr-review-task"],
        "created_at": created_at,
        "status": status,
    }


def original_bead(issue_id: str, pr_number: int) -> dict[str, object]:
    return {
        "id": issue_id,
        "labels": ["pr-review"],
        "external_ref": f"gh-pr:{pr_number}",
        "status": "blocked",
    }


def review_show(issue_id: str, original_id: str) -> list[dict[str, object]]:
    return [
        {
            "id": issue_id,
            "description": (
                f"Original implementation bead: {original_id}\n"
                "https://github.com/owner/repo/pull/41"
            ),
            "dependencies": [],
        }
    ]


def pr_payload(state: str, *, created_at: str = "2026-08-14T03:00:00Z", merged_at: str | None = None) -> dict[str, object]:
    return {
        "number": 41,
        "url": "https://github.com/owner/repo/pull/41",
        "state": state,
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "headRefName": "agent/aib-swr.1",
        "baseRefName": "main",
        "headRefOid": "a" * 40,
        "createdAt": created_at,
        "mergedAt": merged_at,
    }


class NormalizePrReviewStateTests(unittest.TestCase):
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
            fixture_path = root / "fixture.json"
            calls_path = root / "calls.jsonl"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            resolver = None
            if "resolver_payload" in fixture or "resolver_payloads" in fixture:
                resolver = root / "resolver.py"
                resolver.write_text(
                    "\n".join(
                        (
                            "import json",
                            "import os",
                            "import sys",
                            "fixture = json.loads(open(os.environ['NORMALIZER_FIXTURE'], encoding='utf-8').read())",
                            "issue_id = sys.argv[sys.argv.index('--issue-id') + 1]",
                            "raw_payload = fixture.get('resolver_payloads', {}).get(issue_id, fixture.get('resolver_payload', {}))",
                            "payload = dict(raw_payload)",
                            "payload.setdefault('issue_id', issue_id)",
                            "print(json.dumps(payload))",
                            "raise SystemExit(int(fixture.get('resolver_exit', 0)))",
                        )
                    ),
                    encoding="utf-8",
                )
            install_fakes(fake_bin)
            result = run_normalizer(
                repo_root,
                now=now,
                env=fake_bin.env(
                    NORMALIZER_FIXTURE=str(fixture_path),
                    NORMALIZER_CALLS=str(calls_path),
                ),
                resolver=resolver,
            )
            calls = [
                json.loads(line)
                for line in calls_path.read_text(encoding="utf-8").splitlines()
            ] if calls_path.exists() else []
        return result, calls, str(repo_root)

    def test_empty_scan_emits_a_compact_deterministic_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, FakeBinDir() as fake_bin:
            repo_root = Path(tmp)
            fake_bin.add(
                "bd",
                """
                import json
                import sys

                if "list" in sys.argv:
                    print(json.dumps([]))
                    raise SystemExit(0)
                if "config" in sys.argv:
                    print(json.dumps({"key": "issue_prefix", "value": "aib"}))
                    raise SystemExit(0)
                print("unexpected bd command", file=sys.stderr)
                raise SystemExit(1)
                """,
            )
            fake_bin.add(
                "gh",
                """
                import json
                import sys
                print("owner/repo" if sys.argv[1:3] == ["repo", "view"] else json.dumps([]))
                """,
            )
            fake_bin.add("git", "raise SystemExit(0)")

            result = run_normalizer(
                repo_root,
                now="2026-08-14T04:00:00Z",
                env=fake_bin.env(),
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "errors": [],
                "findings": [],
                "generated_at": "2026-08-14T04:00:00Z",
                "schema": "beads-pr-review-normalization/v1",
                "self_heal_candidates": [],
                "status": "empty",
            },
        )
        self.assertEqual(result.stderr, "")

    def test_unresolved_repository_scopes_no_pr_read_and_is_partial(self) -> None:
        # aib-3qa: every PR read is scoped to the scanned repo; without it none runs.
        original_id = "aib-swr.1"
        result, calls, _ = self.run_fixture(
            {
                "blocked_pr_review": [{"id": original_id, "labels": ["pr-review"], "status": "blocked"}],
                "blocked_pr_review_tasks": [],
                "shows": {original_id: [original_bead(original_id, 41)]},
                "prs": {"41": pr_payload("OPEN")},
                "open_prs": [],
                "failures": {"gh-repo-view": "not a git repository"},
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(any(error["code"] == "repo-unresolved" for error in payload["errors"]))
        pr_reads = [call for call in calls if call["tool"] == "gh" and call["argv"][0] == "pr"]
        self.assertEqual(pr_reads, [])

    def test_fake_commands_match_strict_read_only_argv_allowlists(self) -> None:
        original_id = "aib-swr.1"
        self_heal_id = "aib-selfheal.1"
        result, calls, repo_root = self.run_fixture(
            {
                "blocked_pr_review": [
                    {"id": original_id, "labels": ["pr-review"], "status": "blocked"},
                ],
                "blocked_pr_review_tasks": [],
                "shows": {
                    original_id: [original_bead(original_id, 41)],
                    self_heal_id: [{"id": self_heal_id, "status": "in_progress", "external_ref": "", "labels": [],
                                    "assignee": "coordinator:peer"}],
                },
                "prs": {"41": pr_payload("OPEN"),
                        "42": {**pr_payload("OPEN"), "number": 42, "headRefName": f"agent/{self_heal_id}"}},
                "open_prs": [
                    {
                        "number": 42,
                        "headRefName": f"agent/{self_heal_id}",
                        "createdAt": "2026-08-14T03:00:00Z",
                    }
                ],
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        allowed = {
            "bd": {
                (
                    "-C",
                    repo_root,
                    "list",
                    "--status=blocked",
                    "--label",
                    "pr-review",
                    "--json",
                    "--limit",
                    "0",
                ),
                ("-C", repo_root, "list", "--label", "pr-review-task", "--json", "--limit", "0"),
                ("-C", repo_root, "config", "get", "issue_prefix", "--json"),
                ("show", original_id, "--json"),
                ("show", self_heal_id, "--json"),
            },
            "gh": {
                ("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"),
                ("pr", "list", "--repo", "owner/repo", "--state", "open", "--limit", "1000", "--json",
                 "number,headRefName,createdAt,headRefOid,baseRefName,baseRefOid,isCrossRepository"),
                ("pr", "view", "41", "--repo", "owner/repo", "--json", "number,state,mergedAt,createdAt,headRefOid"),
                ("pr", "view", "42", "--repo", "owner/repo", "--json", "number,state,mergedAt,createdAt,headRefOid,headRefName"),
            },
            "git": set(),
        }
        self.assertTrue(calls)
        observed = {
            tool: {tuple(call["argv"]) for call in calls if call["tool"] == tool}
            for tool in allowed
        }
        self.assertEqual(observed, allowed)
        for call in calls:
            tool = call["tool"]
            argv = call["argv"]
            self.assertIsInstance(tool, str)
            self.assertIsInstance(argv, list)
            self.assertIn(tool, allowed, call)
            self.assertIn(tuple(argv), allowed[tool], call)

    def test_mismatched_original_show_identity_is_partial_manual_triage(self) -> None:
        original_id = "aib-swr.1"
        returned_id = "aib-other.1"
        fixture = {
            "blocked_pr_review": [
                {"id": original_id, "labels": ["pr-review"], "status": "blocked"},
            ],
            "shows": {original_id: [original_bead(returned_id, 41)]},
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        finding = payload["findings"][0]
        self.assertEqual(finding["context_status"], "mismatched-record-id")
        self.assertEqual(finding["recommendation"], "manual-triage")
        self.assertIn({"code": "mismatched-record-id", "scope": "original-context"}, payload["errors"])

    def test_open_branch_self_heal_rejects_mismatched_original_show_identity(self) -> None:
        original_id = "aib-swr.1"
        returned_id = "aib-other.1"
        fixture = {
            "blocked_pr_review": [],
            "shows": {
                original_id: [{"id": returned_id, "status": "open", "external_ref": "", "labels": []}],
            },
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        candidate = payload["self_heal_candidates"][0]
        self.assertEqual(candidate["context_status"], "mismatched-record-id")
        self.assertEqual(candidate["recommendation"], "manual-triage")
        self.assertIn({"code": "mismatched-record-id", "scope": "self-heal"}, payload["errors"])

    def test_self_referential_review_task_never_reaches_dispatch_or_wiring(self) -> None:
        review_id = "aib-review.1"
        cases = {
            "distinct-control": (
                "aib-swr.1",
                "success",
                "dispatch-canonical-review",
                "review-wiring-current",
            ),
            "self-referential": (review_id, "partial", "manual-triage", "manual-triage"),
        }
        for name, (original_id, expected_status, expected_recommendation, expected_self_heal) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [review_task(review_id, original_id)],
                    "resolver_payload": {"ok": True, "original_id": original_id, "pr_number": 41},
                    "prs": {"41": pr_payload("OPEN")},
                    "shows": {original_id: [original_bead(original_id, 41)]},
                    "open_prs": [
                        {
                            "number": 41,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        }
                    ],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                finding = payload["findings"][0]
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(finding["recommendation"], expected_recommendation)
                self.assertEqual(payload["self_heal_candidates"][0]["recommendation"], expected_self_heal)
                if name == "self-referential":
                    self.assertEqual(finding["context_status"], "self-referential-review-id")
                    self.assertIn(
                        {"code": "self-referential-review-id", "scope": "review-context"},
                        payload["errors"],
                    )

    def test_reciprocal_review_task_relations_are_partial_manual_triage(self) -> None:
        first_review_id = "aib-review.1"
        second_review_id = "aib-review.2"
        fixture = {
            "blocked_pr_review": [
                review_task(first_review_id, second_review_id),
                review_task(second_review_id, first_review_id),
            ],
            "resolver_payloads": {
                first_review_id: {
                    "ok": True,
                    "original_id": second_review_id,
                    "pr_number": 41,
                },
                second_review_id: {
                    "ok": True,
                    "original_id": first_review_id,
                    "pr_number": 41,
                },
            },
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "cyclic-review-relation", "scope": "review-context"}, payload["errors"])
        self.assertTrue(payload["findings"])
        self.assertTrue(all(item["recommendation"] == "manual-triage" for item in payload["findings"]))

    def test_relation_validator_is_total_and_enforces_global_role_binding(self) -> None:
        original_id = "aib-swr.1"
        first_review_id = "aib-review.1"
        second_review_id = "aib-review.2"
        third_review_id = "aib-review.3"
        cases = {
            "missing-collection": (None, "invalid-review-relation"),
            "string-collection": (first_review_id, "invalid-review-relation"),
            "malformed-tuple": ([(first_review_id, original_id, "extra")], "invalid-review-relation"),
            "non-tuple-item": ([{"review_id": first_review_id}], "invalid-review-relation"),
            "invalid-id": ([(first_review_id, None)], "invalid-review-relation"),
            "self-relation": ([(first_review_id, first_review_id)], "self-referential-review-id"),
            "duplicate-lhs": (
                [(first_review_id, original_id), (first_review_id, original_id)],
                "ambiguous-review-relation",
            ),
            "acyclic-cross-role-reuse": (
                [(first_review_id, original_id), (second_review_id, first_review_id)],
                "cross-role-review-relation",
            ),
            "reciprocal-cycle": (
                [(first_review_id, second_review_id), (second_review_id, first_review_id)],
                "cyclic-review-relation",
            ),
            "three-node-cycle": (
                [
                    (first_review_id, second_review_id),
                    (second_review_id, third_review_id),
                    (third_review_id, first_review_id),
                ],
                "cyclic-review-relation",
            ),
            "valid-distinct-dedupe": (
                [(first_review_id, original_id), (second_review_id, original_id)],
                None,
            ),
        }

        for name, (relations, expected_error) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(relation_error(relations), expected_error)

    def test_global_relation_validation_blocks_acyclic_cross_role_before_dispatch(self) -> None:
        original_id = "aib-swr.1"
        first_review_id = "aib-review.1"
        second_review_id = "aib-review.2"
        fixture = {
            "blocked_pr_review": [
                review_task(first_review_id, original_id),
                review_task(second_review_id, first_review_id),
            ],
            "resolver_payloads": {
                first_review_id: {
                    "ok": True,
                    "original_id": original_id,
                    "pr_number": 41,
                },
                second_review_id: {
                    "ok": True,
                    "original_id": first_review_id,
                    "pr_number": 41,
                },
            },
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn(
            {"code": "cross-role-review-relation", "scope": "review-context"},
            payload["errors"],
        )
        self.assertTrue(payload["findings"])
        self.assertTrue(all(item["recommendation"] == "manual-triage" for item in payload["findings"]))

    def test_resolver_identity_must_match_the_discovered_review_task(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        fixture = {
            "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
            "resolver_payload": {
                "ok": True,
                "issue_id": "aib-other.1",
                "original_id": original_id,
                "pr_number": 41,
            },
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertTrue(payload["findings"])
        self.assertTrue(all(item["recommendation"] == "manual-triage" for item in payload["findings"]))
        self.assertIn({"code": "mismatched-review-id", "scope": "review-context"}, payload["errors"])

    def test_malformed_pr_review_inventory_forces_partial_manual_triage(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        original = original_bead(original_id, 41)
        task = review_task(review_id, original_id)
        # Closed legacy and deliberately deferred reviews are not active work.
        for status in ("closed", "deferred"):
            with self.subTest(inactive_status=status):
                legacy = {"id": "aib-legacy", "status": status, "labels": ["pr-review-task"]}
                result, _, _ = self.run_fixture({"pr_review_tasks_all": [legacy], "open_prs": []})
                self.assertEqual(result.returncode, 0, result.stderr)
                terminal_payload = json.loads(result.stdout)
                self.assertEqual(terminal_payload["status"], "empty")
                self.assertEqual(terminal_payload["errors"], [])
                self.assertEqual(terminal_payload["findings"], [])
        cases = {
            "missing-original-label": (
                [{**original, "labels": []}],
                [task],
                "invalid-record",
                "blocked-pr-review",
            ),
            "wrong-original-status": (
                [{**original, "status": "open"}],
                [task],
                "invalid-record",
                "blocked-pr-review",
            ),
            "duplicate-original-id": (
                [original, dict(original)],
                [task],
                "duplicate-record",
                "blocked-pr-review",
            ),
            "missing-task-pr-review-label": (
                [original],
                [{**task, "labels": ["pr-review-task"]}],
                "invalid-record",
                "blocked-pr-review-task",
            ),
            "missing-task-status": (
                [original],
                [{**task, "status": None}],
                "invalid-record",
                "blocked-pr-review-task",
            ),
            "list-task-status": (
                [original],
                [{**task, "status": []}],
                "invalid-record",
                "blocked-pr-review-task",
            ),
            "dict-task-status": (
                [original],
                [{**task, "status": {}}],
                "invalid-record",
                "blocked-pr-review-task",
            ),
            "duplicate-task-id": (
                [original],
                [task, dict(task)],
                "duplicate-record",
                "blocked-pr-review-task",
            ),
            "missing-original-inventory": (
                None,
                [task],
                "invalid-json",
                "blocked-pr-review",
            ),
            "missing-task-inventory": (
                [original],
                None,
                "invalid-json",
                "blocked-pr-review-task",
            ),
            "invalid-task-inventory-item": (
                [original],
                ["not-a-record"],
                "invalid-record",
                "blocked-pr-review-task",
            ),
        }
        for name, (original_records, task_records, code, scope) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": original_records,
                    "blocked_pr_review_tasks": task_records,
                    "resolver_payload": {"ok": True, "original_id": original_id, "pr_number": 41},
                    "prs": {"41": pr_payload("OPEN")},
                    "open_prs": [],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertTrue(payload["findings"])
                self.assertTrue(all(item["recommendation"] == "manual-triage" for item in payload["findings"]))
                self.assertIn({"code": code, "scope": scope}, payload["errors"])

    def test_preserves_dotted_child_and_reports_open_pr_cooldown(self) -> None:
        # Custom IDs are valid when explicit tracker/review evidence names them.
        for original_id in ("aib-swr.1", "aib-owner-answers.1"):
            with self.subTest(original_id=original_id):
                review_id = "aib-review.1"
                fixture = {
                    "blocked_pr_review": [
                        original_bead(original_id, 41),
                        review_task(review_id, original_id),
                    ],
                    "shows": {
                        original_id: [original_bead(original_id, 41)],
                        review_id: review_show(review_id, original_id),
                    },
                    "prs": {"41": pr_payload("OPEN", created_at="2026-08-14T03:57:00Z")},
                    "open_prs": [{"number": 41, "headRefName": f"agent/{original_id}",
                                  "createdAt": "2026-08-14T03:57:00Z"}],
                }

                result, calls, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["errors"], [])
                self.assertEqual(payload["self_heal_candidates"][0]["original_id"], original_id)
                task = next(item for item in payload["findings"] if item["review_id"] == review_id)
                self.assertEqual(task["original_id"], original_id)
                self.assertEqual(task["canonical_review_id"], review_id)
                self.assertEqual(task["pr_number"], 41)
                self.assertEqual(task["pr_state"], "OPEN")
                self.assertEqual(task["cooldown_until"], "2026-08-14T04:02:00Z")
                self.assertEqual(task["recommendation"], "wait-for-cooldown")
                self.assertTrue(any(call["tool"] == "bd" for call in calls))
                self.assertTrue(any(call["tool"] == "gh" for call in calls))

    def test_duplicate_review_tasks_choose_the_chronological_oldest_then_id(self) -> None:
        original_id = "aib-swr.1"
        first_review = "aib-review.1"
        duplicate_review = "aib-review.2"
        fixture = {
            "blocked_pr_review": [
                original_bead(original_id, 41),
                review_task(first_review, original_id, created_at="2026-08-14T03:00:00+02:00"),
                review_task(duplicate_review, original_id, created_at="2026-08-14T02:30:00Z"),
            ],
            "shows": {
                original_id: [original_bead(original_id, 41)],
                first_review: review_show(first_review, original_id),
                duplicate_review: review_show(duplicate_review, original_id),
            },
            "prs": {"41": pr_payload("OPEN", created_at="2026-08-14T03:00:00Z")},
            "open_prs": [],
        }

        result, calls, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        duplicate = next(item for item in payload["findings"] if item["review_id"] == duplicate_review)
        self.assertEqual(duplicate["canonical_review_id"], first_review)
        self.assertEqual(duplicate["duplicate_of"], first_review)
        self.assertEqual(duplicate["recommendation"], "dedupe-review-task")

    def test_invalid_review_task_created_at_fails_closed_without_canonical_selection(self) -> None:
        original_id = "aib-swr.1"
        invalid_review = "aib-review.1"
        other_review = "aib-review.2"
        fixture = {
            "blocked_pr_review": [
                original_bead(original_id, 41),
                review_task(invalid_review, original_id, created_at="not-a-timestamp"),
                review_task(other_review, original_id, created_at="2026-08-14T03:00:00Z"),
            ],
            "shows": {
                original_id: [original_bead(original_id, 41)],
                invalid_review: review_show(invalid_review, original_id),
                other_review: review_show(other_review, original_id),
            },
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "invalid-created-at", "scope": "review-context"}, payload["errors"])
        for review_id in (invalid_review, other_review):
            finding = next(item for item in payload["findings"] if item["review_id"] == review_id)
            self.assertEqual(finding["context_status"], "invalid-created-at")
            self.assertIsNone(finding["canonical_review_id"])
            self.assertIsNone(finding["duplicate_of"])
            self.assertEqual(finding["recommendation"], "manual-triage")

    def test_invalid_chronology_in_one_scope_manualizes_the_complete_collection(self) -> None:
        first_original_id = "aib-swr.1"
        second_original_id = "aib-swr.2"
        invalid_review_id = "aib-review.1"
        valid_review_id = "aib-review.2"
        second_pr = {
            **pr_payload("OPEN"),
            "number": 42,
            "url": "https://github.com/owner/repo/pull/42",
            "headRefName": f"agent/{second_original_id}",
        }
        fixture = {
            "blocked_pr_review": [
                original_bead(first_original_id, 41),
                original_bead(second_original_id, 42),
                review_task(invalid_review_id, first_original_id, created_at="not-a-timestamp"),
                review_task(valid_review_id, second_original_id, created_at="2026-08-14T03:00:00Z"),
            ],
            "resolver_payloads": {
                invalid_review_id: {"ok": True, "original_id": first_original_id, "pr_number": 41},
                valid_review_id: {"ok": True, "original_id": second_original_id, "pr_number": 42},
            },
            "shows": {
                first_original_id: [original_bead(first_original_id, 41)],
                second_original_id: [original_bead(second_original_id, 42)],
            },
            "prs": {"41": pr_payload("OPEN"), "42": second_pr},
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{first_original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                },
                {
                    "number": 42,
                    "headRefName": f"agent/{second_original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                },
            ],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "invalid-created-at", "scope": "review-context"}, payload["errors"])
        self.assertTrue(payload["findings"])
        self.assertTrue(
            all(finding["recommendation"] == "manual-triage" for finding in payload["findings"])
        )
        self.assertTrue(
            all(finding["canonical_review_id"] is None for finding in payload["findings"])
        )
        self.assertTrue(payload["self_heal_candidates"])
        self.assertTrue(
            all(
                candidate["recommendation"] == "manual-triage"
                for candidate in payload["self_heal_candidates"]
            )
        )

    def test_mixed_resolver_command_failure_manualizes_the_complete_collection(self) -> None:
        original_id = "aib-swr.1"
        valid_review_id = "aib-review.1"
        failed_review_id = "aib-review.2"
        fixture = {
            "blocked_pr_review": [
                original_bead(original_id, 41),
                review_task(valid_review_id, original_id),
                review_task(failed_review_id, original_id),
            ],
            "resolver_payloads": {
                valid_review_id: {"ok": True, "original_id": original_id, "pr_number": 41},
                failed_review_id: {"ok": False, "error_code": "command-failed"},
            },
            "shows": {original_id: [original_bead(original_id, 41)]},
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "command-failed", "scope": "review-context"}, payload["errors"])
        self.assertTrue(payload["findings"])
        self.assertTrue(
            all(finding["recommendation"] == "manual-triage" for finding in payload["findings"])
        )
        self.assertTrue(
            all(finding["canonical_review_id"] is None for finding in payload["findings"])
        )
        self.assertTrue(payload["self_heal_candidates"])
        self.assertTrue(
            all(
                candidate["recommendation"] == "manual-triage"
                for candidate in payload["self_heal_candidates"]
            )
        )

    def test_malformed_original_reference_in_one_scope_manualizes_the_collection(self) -> None:
        malformed_original_id = "aib-swr.1"
        valid_original_id = "aib-swr.2"
        malformed_review_id = "aib-review.1"
        valid_review_id = "aib-review.2"
        valid_pr = {
            **pr_payload("OPEN"),
            "number": 42,
            "url": "https://github.com/owner/repo/pull/42",
            "headRefName": f"agent/{valid_original_id}",
        }
        fixture = {
            "blocked_pr_review": [
                {
                    "id": malformed_original_id,
                    "labels": ["pr-review"],
                    "external_ref": "gh-pr:0",
                    "status": "blocked",
                },
                original_bead(valid_original_id, 42),
                review_task(malformed_review_id, malformed_original_id),
                review_task(valid_review_id, valid_original_id),
            ],
            "resolver_payloads": {
                malformed_review_id: {
                    "ok": True,
                    "original_id": malformed_original_id,
                    "pr_number": 41,
                },
                valid_review_id: {"ok": True, "original_id": valid_original_id, "pr_number": 42},
            },
            "shows": {valid_original_id: [original_bead(valid_original_id, 42)]},
            "prs": {"42": valid_pr},
            "open_prs": [
                {
                    "number": 42,
                    "headRefName": f"agent/{valid_original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "invalid-pr-number", "scope": "original-context"}, payload["errors"])
        self.assertTrue(payload["findings"])
        self.assertTrue(
            all(finding["recommendation"] == "manual-triage" for finding in payload["findings"])
        )
        self.assertTrue(
            all(finding["canonical_review_id"] is None for finding in payload["findings"])
        )
        self.assertTrue(payload["self_heal_candidates"])
        self.assertTrue(
            all(
                candidate["recommendation"] == "manual-triage"
                for candidate in payload["self_heal_candidates"]
            )
        )
        self.assertTrue(
            all(
                candidate["canonical_review_id"] is None
                for candidate in payload["self_heal_candidates"]
            )
        )

    def test_original_lookup_failure_in_one_scope_manualizes_the_collection(self) -> None:
        failed_original_id = "aib-swr.1"
        valid_original_id = "aib-swr.2"
        failed_review_id = "aib-review.1"
        valid_review_id = "aib-review.2"
        valid_pr = {
            **pr_payload("OPEN"),
            "number": 42,
            "url": "https://github.com/owner/repo/pull/42",
            "headRefName": f"agent/{valid_original_id}",
        }
        fixture = {
            "blocked_pr_review": [
                {"id": failed_original_id, "labels": ["pr-review"], "status": "blocked"},
                original_bead(valid_original_id, 42),
                review_task(failed_review_id, failed_original_id),
                review_task(valid_review_id, valid_original_id),
            ],
            "resolver_payloads": {
                failed_review_id: {"ok": True, "original_id": failed_original_id, "pr_number": 41},
                valid_review_id: {"ok": True, "original_id": valid_original_id, "pr_number": 42},
            },
            "shows": {valid_original_id: [original_bead(valid_original_id, 42)]},
            "prs": {"42": valid_pr},
            "open_prs": [
                {
                    "number": 42,
                    "headRefName": f"agent/{valid_original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
            "failures": {"bd-show:aib-swr.1": "read failed"},
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "command-failed", "scope": "original-context"}, payload["errors"])
        self.assertTrue(payload["findings"])
        self.assertTrue(
            all(finding["recommendation"] == "manual-triage" for finding in payload["findings"])
        )
        self.assertTrue(
            all(finding["canonical_review_id"] is None for finding in payload["findings"])
        )
        self.assertTrue(payload["self_heal_candidates"])
        self.assertTrue(
            all(
                candidate["recommendation"] == "manual-triage"
                for candidate in payload["self_heal_candidates"]
            )
        )
        self.assertTrue(
            all(
                candidate["canonical_review_id"] is None
                for candidate in payload["self_heal_candidates"]
            )
        )

    def test_resolver_bool_pr_number_is_invalid_manual_triage(self) -> None:
        review_id = "aib-review.1"
        fixture = {
            "blocked_pr_review": [review_task(review_id, "aib-swr.1")],
            "resolver_payload": {"ok": True, "original_id": "aib-swr.1", "pr_number": True},
            "prs": {"True": pr_payload("OPEN")},
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        finding = next(item for item in payload["findings"] if item["review_id"] == review_id)
        self.assertEqual(finding["context_status"], "invalid-pr-number")
        self.assertIsNone(finding["pr_number"])
        self.assertEqual(finding["recommendation"], "manual-triage")
        self.assertIn({"code": "invalid-pr-number", "scope": "review-context"}, payload["errors"])

    def test_review_findings_bind_the_observed_head_and_invalid_heads_fail_closed(self) -> None:
        original_id, review_id = "aib-swr.1", "aib-review.1"
        fixture = {
            "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
            "shows": {original_id: [original_bead(original_id, 41)],
                      review_id: review_show(review_id, original_id)},
            "prs": {"41": pr_payload("OPEN")}, "open_prs": [],
        }
        result, _, _ = self.run_fixture(fixture)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "success")
        self.assertTrue(payload["findings"])
        self.assertTrue(all(finding["head_sha"] == "a" * 40 for finding in payload["findings"]))
        for invalid in (None, "", "abc123", True, "z" * 40, "a" * 41):
            with self.subTest(head=invalid):
                fixture["prs"]["41"]["headRefOid"] = invalid
                result, _, _ = self.run_fixture(fixture)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertIn({"code": "invalid-pr-head", "scope": "pr-state"}, payload["errors"])
                self.assertTrue(all(finding["head_sha"] is None for finding in payload["findings"]))

    def test_invalid_pr_created_at_is_partial_manual_triage_for_every_state(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        for state in ("OPEN", "CLOSED", "MERGED"):
            with self.subTest(state=state):
                fixture = {
                    "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
                    "shows": {
                        original_id: [original_bead(original_id, 41)],
                        review_id: review_show(review_id, original_id),
                    },
                    "prs": {"41": pr_payload(state, created_at="not-a-timestamp")},
                    "open_prs": [],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                for finding in payload["findings"]:
                    self.assertEqual(finding["context_status"], "invalid-pr-created-at")
                    self.assertIsNone(finding["pr_state"])
                    self.assertEqual(finding["recommendation"], "manual-triage")
                self.assertIn({"code": "invalid-pr-created-at", "scope": "pr-state"}, payload["errors"])

    def test_malformed_open_pr_candidates_are_partial_manual_triage_never_self_heal(self) -> None:
        original_id = "aib-swr.1"
        cases = {
            "bool-number": (
                {"number": True, "headRefName": f"agent/{original_id}", "createdAt": "2026-08-14T03:00:00Z"},
                "invalid-pr-number",
                None,
            ),
            "invalid-created-at": (
                {"number": 77, "headRefName": f"agent/{original_id}", "createdAt": "not-a-timestamp"},
                "invalid-pr-created-at",
                77,
            ),
        }
        for name, (open_pr, error_code, expected_number) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [],
                    "shows": {original_id: [{"id": original_id, "status": "open", "labels": [], "external_ref": ""}]},
                    "open_prs": [open_pr],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                candidate = payload["self_heal_candidates"][0]
                self.assertEqual(candidate["context_status"], error_code)
                self.assertEqual(candidate["pr_number"], expected_number)
                self.assertEqual(candidate["recommendation"], "manual-triage")
                self.assertIn({"code": error_code, "scope": "open-prs"}, payload["errors"])

    def test_active_review_task_is_canonical_and_blocks_self_heal(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        fixture = {
            "blocked_pr_review": [original_bead(original_id, 41)],
            "pr_review_tasks_all": [
                review_task(review_id, original_id, status="in_progress"),
            ],
            "shows": {
                original_id: [original_bead(original_id, 41)],
                review_id: review_show(review_id, original_id),
            },
            "prs": {"41": pr_payload("OPEN", created_at="2026-08-14T03:00:00Z")},
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
        }

        result, calls, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        original = next(item for item in payload["findings"] if item["kind"] == "original")
        self.assertEqual(original["canonical_review_id"], review_id)
        candidate = payload["self_heal_candidates"][0]
        self.assertEqual(candidate["canonical_review_id"], review_id)
        self.assertEqual(candidate["recommendation"], "review-wiring-current")
        task_lists = [
            call["argv"]
            for call in calls
            if call["tool"] == "bd" and "pr-review-task" in call["argv"]
        ]
        self.assertTrue(task_lists)
        self.assertTrue(all("--status=blocked" not in argv for argv in task_lists))

    def test_incomplete_active_task_lookup_never_recommends_self_heal(self) -> None:
        secret = "TOKEN=top-secret /private/absolute/path Traceback"
        original_id = "aib-swr.1"
        fixture = {
            "blocked_pr_review": [original_bead(original_id, 41)],
            "shows": {original_id: [original_bead(original_id, 41)]},
            "prs": {"41": pr_payload("OPEN", created_at="2026-08-14T03:00:00Z")},
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
            "failures": {"bd-list::pr-review-task": secret},
        }

        result, _, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        original = next(item for item in payload["findings"] if item["kind"] == "original")
        self.assertEqual(original["recommendation"], "manual-triage")
        self.assertEqual(payload["self_heal_candidates"][0]["recommendation"], "manual-triage")
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", repo_root):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_malformed_pr_review_task_inventory_blocks_self_heal(self) -> None:
        original_id = "aib-swr.1"
        malformed_inventories = {
            "non-record": ["malformed-task-inventory-record"],
            "missing-task-label": [
                {
                    "created_at": "2026-08-14T03:00:00Z",
                    "id": "aib-review.1",
                    "labels": ["pr-review"],
                    "status": "blocked",
                }
            ],
        }
        for name, inventory in malformed_inventories.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [],
                    "pr_review_tasks_all": inventory,
                    "shows": {original_id: [original_bead(original_id, 41)]},
                    "open_prs": [
                        {
                            "number": 41,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        }
                    ],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertEqual(payload["self_heal_candidates"][0]["recommendation"], "manual-triage")
                self.assertIn(
                    {"code": "invalid-record", "scope": "blocked-pr-review-task"},
                    payload["errors"],
                )

    def test_resolver_context_failures_are_safe_and_do_not_fall_back(self) -> None:
        cases = {
            "missing": ("No explicit original marker or dependency", "missing-original-id"),
            "malformed": ("Original implementation bead: aib-swr.invalid", "malformed-original-id"),
            "ambiguous": (
                "Original implementation bead: aib-swr.1\nOriginal implementation bead: aib-swr.2",
                "ambiguous-original-id",
            ),
        }
        for name, (description, error_code) in cases.items():
            with self.subTest(name=name):
                review_id = "aib-review.1"
                fixture = {
                    "blocked_pr_review": [review_task(review_id, "aib-swr.1")],
                    "shows": {
                        review_id: [{"id": review_id, "description": description, "dependencies": []}],
                    },
                    "prs": {},
                    "open_prs": [],
                }
                result, _, _ = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                finding = json.loads(result.stdout)["findings"][0]
                self.assertEqual(finding["context_status"], error_code)
                self.assertEqual(finding["original_id"], None)
                self.assertEqual(finding["recommendation"], "manual-triage")

    def test_active_review_context_collection_must_be_complete_before_actions(self) -> None:
        original_id = "aib-swr.1"
        first_review_id = "aib-review.1"
        second_review_id = "aib-review.2"
        cases = {
            "missing-original": (
                {"ok": False, "error_code": "missing-original-id"},
                "missing-original-id",
                False,
                {},
            ),
            "invalid-original": (
                {"ok": True, "original_id": "invalid", "pr_number": 41},
                "invalid-original-id",
                False,
                {},
            ),
            "missing-original-with-pr-read-failure": (
                {"ok": False, "error_code": "missing-original-id"},
                "missing-original-id",
                False,
                {"gh-pr-view:41": "read failed"},
            ),
            "full-valid-control": (
                {"ok": True, "original_id": original_id, "pr_number": 41},
                None,
                True,
                {},
            ),
        }
        for name, (second_context, error_code, expected_actionable, failures) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [
                        original_bead(original_id, 41),
                        review_task(first_review_id, original_id),
                        review_task(second_review_id, original_id),
                    ],
                    "resolver_payloads": {
                        first_review_id: {"ok": True, "original_id": original_id, "pr_number": 41},
                        second_review_id: second_context,
                    },
                    "shows": {original_id: [original_bead(original_id, 41)]},
                    "prs": {"41": pr_payload("OPEN")},
                    "open_prs": [
                        {
                            "number": 41,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        }
                    ],
                    "failures": failures,
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                if expected_actionable:
                    self.assertEqual(payload["status"], "success")
                    self.assertTrue(
                        any(
                            finding["recommendation"] == "dispatch-canonical-review"
                            for finding in payload["findings"]
                        )
                    )
                    self.assertTrue(
                        any(
                            candidate["recommendation"] == "review-wiring-current"
                            for candidate in payload["self_heal_candidates"]
                        )
                    )
                    continue

                self.assertEqual(payload["status"], "partial")
                self.assertIn({"code": error_code, "scope": "review-context"}, payload["errors"])
                self.assertTrue(payload["findings"])
                self.assertTrue(
                    all(finding["recommendation"] == "manual-triage" for finding in payload["findings"])
                )
                self.assertTrue(
                    all(finding["canonical_review_id"] is None for finding in payload["findings"])
                )
                self.assertTrue(payload["self_heal_candidates"])
                self.assertTrue(
                    all(
                        candidate["recommendation"] == "manual-triage"
                        for candidate in payload["self_heal_candidates"]
                    )
                )

    def test_merged_package_requires_member_acceptance_reconciliation(self):
        anchor_id, review_id = "aib-adapters", "aib-review"
        original = original_bead(anchor_id, 41)
        members = [anchor_id, "aib-terminal", "aib-stderr"]
        original["metadata"] = {"delivery": {"schema": "beads-delivery/v1", "anchor": anchor_id,
            "members": members, "owner": "coordinator:owner", "outcome": "aib-runtime",
            "base_branch": "main", "packet": "Adapter corrections"}}
        result, _, _ = self.run_fixture({
            "blocked_pr_review": [original, review_task(review_id, anchor_id)],
            "shows": {anchor_id: [original], review_id: review_show(review_id, anchor_id)},
            "prs": {"41": pr_payload("MERGED")}, "open_prs": [],
            "resolver_payload": {"ok": True, "original_id": anchor_id, "pr_number": 41,
                                 "delivery_members": members}})
        self.assertEqual(result.returncode, 0, result.stderr)
        findings = json.loads(result.stdout)["findings"]
        self.assertTrue(findings)
        self.assertTrue(all(item["recommendation"] == "reconcile-package-members" for item in findings))
        self.assertTrue(all(item["delivery_members"] == members for item in findings))
        self.assertEqual({item["review_id"] for item in findings if item["review_id"]}, {review_id})

    def test_pr_state_recommendations_cover_merged_closed_open_and_command_failure(self) -> None:
        cases = {
            "MERGED": ("MERGED", "close-review-and-original", None),
            "CLOSED": ("CLOSED", "close-review-reopen-original", None),
            "OPEN": ("OPEN", "dispatch-canonical-review", None),
            "command-failure": ("OPEN", "skip-command-failure", "TOKEN=secret /private/path Traceback"),
        }
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        for name, (state, recommendation, failure) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
                    "shows": {
                        original_id: [original_bead(original_id, 41)],
                        review_id: review_show(review_id, original_id),
                    },
                    "prs": {"41": pr_payload(state)},
                    "open_prs": [],
                    "failures": {"gh-pr-view:41": failure} if failure else {},
                }
                if failure:
                    fixture["resolver_payload"] = {
                        "ok": True,
                        "original_id": original_id,
                        "pr_number": 41,
                    }
                result, _, repo_root = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                finding = next(item for item in payload["findings"] if item["review_id"] == review_id)
                self.assertEqual(finding["recommendation"], recommendation)
                if failure:
                    self.assertEqual(finding["context_status"], "command-failed")
                    for forbidden in ("TOKEN=secret", "/private/path", "Traceback", repo_root):
                        self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_pr_metadata_failure_manualizes_self_heal_but_preserves_lone_skip(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        cases = {
            "review-wiring-current": (
                [original_bead(original_id, 41)],
                "review-wiring-current",
            ),
            "self-heal-original-wiring": (
                [{"id": original_id, "labels": [], "external_ref": "", "status": "open"}],
                "self-heal-original-wiring",
            ),
        }
        for name, (shown_original, prior_recommendation) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
                    "resolver_payload": {"ok": True, "original_id": original_id, "pr_number": 41},
                    "shows": {original_id: shown_original},
                    "prs": {"41": pr_payload("OPEN")},
                    "open_prs": [
                        {
                            "number": 41,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        }
                    ],
                    "failures": {"gh-pr-view:41": "read failed"},
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertIn({"code": "command-failed", "scope": "pr-state"}, payload["errors"])
                self.assertTrue(
                    all(
                        finding["recommendation"] == "skip-command-failure"
                        for finding in payload["findings"]
                    )
                )
                candidate = payload["self_heal_candidates"][0]
                self.assertEqual(candidate["recommendation"], "manual-triage")
                self.assertNotEqual(candidate["recommendation"], prior_recommendation)

    def test_pr_metadata_success_preserves_self_heal_wiring_recommendations(self) -> None:
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        cases = {
            "review-wiring-current": (
                [original_bead(original_id, 41)],
                "review-wiring-current",
            ),
            "self-heal-original-wiring": (
                [{"id": original_id, "labels": [], "external_ref": "", "status": "open"}],
                "self-heal-original-wiring",
            ),
        }
        for name, (shown_original, recommendation) in cases.items():
            with self.subTest(name=name):
                fixture = {
                    "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
                    "resolver_payload": {"ok": True, "original_id": original_id, "pr_number": 41},
                    "shows": {original_id: shown_original},
                    "prs": {"41": pr_payload("OPEN")},
                    "open_prs": [
                        {
                            "number": 41,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        }
                    ],
                }

                result, _, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(
                    payload["self_heal_candidates"][0]["recommendation"],
                    recommendation,
                )

        # An unhanded-off author may still be repairing the published PR.
        # Bind the discovered branch to a stable head independently of CI;
        # do not turn ordinary/unknown wiring into proof of running work.
        for case in ("ci-failed", "bound-ref", "ci-passed", "ci-pending", "ci-missing",
                     "transition", "closed", "wrong-branch", "conflicting-ref", "handed-off", "parked", "ambiguous-branch"):
            with self.subTest(author_case=case):
                author = {"id": original_id, "status": "in_progress", "labels": [],
                          "assignee": "coordinator:peer", "external_ref": ""}
                current = {**pr_payload("OPEN"), "headRefName": f"agent/{original_id}"}
                if case != "ci-missing":
                    current["statusCheckRollup"] = [{"name": "cargo test", "conclusion": {
                        "ci-passed": "SUCCESS", "ci-pending": None}.get(case, "FAILURE")}]
                fixture = {"shows": {original_id: [author]}, "prs": {"41": current},
                           "open_prs": [{"number": 41, "headRefName": f"agent/{original_id}",
                                         "createdAt": "2026-08-14T03:00:00Z"}]}
                if case == "bound-ref":
                    author["external_ref"] = "gh-pr:41"
                if case == "ambiguous-branch":
                    fixture["open_prs"].append({**fixture["open_prs"][0], "number": 42})
                if case == "transition":
                    fixture["pr_view_sequence"] = [current, {**current, "headRefOid": "b" * 40}]
                if case == "closed":
                    current["state"] = "CLOSED"
                if case == "wrong-branch":
                    current["headRefName"] = "agent/aib-other"
                if case == "conflicting-ref":
                    author["external_ref"] = "gh-pr:42"
                if case == "handed-off":
                    author["labels"] = ["pr-review"]
                if case == "parked":
                    author["status"] = "blocked"
                result, calls, _ = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                candidate = payload["self_heal_candidates"][0]
                if case in {"ci-failed", "bound-ref", "ci-passed", "ci-pending", "ci-missing"}:
                    self.assertEqual(payload["status"], "success")
                    self.assertEqual(candidate["recommendation"], "self-heal-original-and-review-wiring")
                    self.assertEqual(candidate["author_activity"], {
                        "assignee": "coordinator:peer", "head_sha": current["headRefOid"],
                        "branch": f"agent/{original_id}"})
                    views = [call for call in calls if call["tool"] == "gh" and call["argv"][:2] == ["pr", "view"]]
                    self.assertEqual(len(views), 2)
                    self.assertFalse(any(call["argv"][:2] == ["pr", "checks"]
                                         or any("statusCheckRollup" in arg for arg in call["argv"])
                                         for call in calls if call["tool"] == "gh"))
                else:
                    self.assertNotIn("author_activity", candidate)
                if case in {"transition", "closed", "wrong-branch"}:
                    self.assertEqual(payload["status"], "partial")
                    self.assertEqual(candidate["recommendation"], "manual-triage")

    def test_open_pr_discovery_finalizes_collection_actionability(self) -> None:
        original_id = "aib-swr.1"
        canonical_review_id = "aib-review.1"
        duplicate_review_id = "aib-review.2"
        base_fixture = {
            "blocked_pr_review": [
                original_bead(original_id, 41),
                review_task(canonical_review_id, original_id),
                review_task(duplicate_review_id, original_id),
            ],
            "resolver_payloads": {
                canonical_review_id: {
                    "ok": True,
                    "original_id": original_id,
                    "pr_number": 41,
                },
                duplicate_review_id: {
                    "ok": True,
                    "original_id": original_id,
                    "pr_number": 41,
                },
            },
            "shows": {original_id: [original_bead(original_id, 41)]},
            "prs": {"41": pr_payload("OPEN")},
            "open_prs": [
                {
                    "number": 41,
                    "headRefName": f"agent/{original_id}",
                    "createdAt": "2026-08-14T03:00:00Z",
                }
            ],
        }
        cases = {
            "open-pr-list-failure": ({"gh-pr-list": "read failed"}, False),
            "mixed-pr-state-and-open-pr-failure": (
                {"gh-pr-view:41": "read failed", "gh-pr-list": "read failed"},
                False,
            ),
            "valid-dedupe-control": ({}, True),
        }
        for name, (failures, actionable) in cases.items():
            with self.subTest(name=name):
                result, _, _ = self.run_fixture({**base_fixture, "failures": failures})

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                if not actionable:
                    self.assertEqual(payload["status"], "partial")
                    self.assertIn(
                        {"code": "command-failed", "scope": "open-prs"},
                        payload["errors"],
                    )
                    if name == "mixed-pr-state-and-open-pr-failure":
                        self.assertIn(
                            {"code": "command-failed", "scope": "pr-state"},
                            payload["errors"],
                        )
                    self.assertTrue(payload["findings"])
                    self.assertTrue(
                        all(
                            finding["recommendation"] == "manual-triage"
                            for finding in payload["findings"]
                        )
                    )
                    self.assertTrue(
                        all(
                            candidate["recommendation"] == "manual-triage"
                            for candidate in payload["self_heal_candidates"]
                        )
                    )
                    continue

                self.assertEqual(payload["status"], "success")
                duplicate = next(
                    finding
                    for finding in payload["findings"]
                    if finding["review_id"] == duplicate_review_id
                )
                self.assertEqual(duplicate["recommendation"], "dedupe-review-task")
                self.assertEqual(
                    payload["self_heal_candidates"][0]["recommendation"],
                    "review-wiring-current",
                )

    def test_unknown_pr_state_is_sanitized_to_a_partial_manual_triage_finding(self) -> None:
        secret_state = "TOKEN=top-secret /private/absolute/path Traceback"
        original_id = "aib-swr.1"
        review_id = "aib-review.1"
        fixture = {
            "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
            "shows": {
                original_id: [original_bead(original_id, 41)],
                review_id: review_show(review_id, original_id),
            },
            "prs": {"41": pr_payload(secret_state)},
            "open_prs": [],
        }

        result, _, repo_root = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        finding = next(item for item in payload["findings"] if item["review_id"] == review_id)
        self.assertEqual(finding["context_status"], "invalid-pr-state")
        self.assertIsNone(finding["pr_state"])
        self.assertEqual(finding["recommendation"], "manual-triage")
        self.assertIn({"code": "invalid-pr-state", "scope": "pr-state"}, payload["errors"])
        for forbidden in ("TOKEN=top-secret", "/private/absolute/path", "Traceback", repo_root):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def test_open_agent_branch_reports_only_project_prefix_self_heal_candidates(self) -> None:
        for prefix, original_id in (("aib", "aib-swr.1"), ("bu", "bu-abc"),
                                    ("syzygy", "syzygy-pdja.2"),
                                    ("multi-part", "multi-part-123.2.10")):
            with self.subTest(prefix=prefix):
                foreign_id = "custody-private-adopted-20260927"
                fixture = {
                    "prefix_payload": {"key": "issue_prefix", "value": prefix},
                    "shows": {original_id: [{"id": original_id, "status": "open", "labels": []}]},
                    "open_prs": [
                        {
                            "number": 77,
                            "headRefName": f"agent/{original_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        },
                        *[
                            {
                                "number": 80 + index,
                                "headRefName": f"agent/{prefix}-{suffix}",
                                "title": f"Owner answers [{original_id}]",
                                "createdAt": "2026-08-14T03:00:00Z",
                            }
                            for index, suffix in enumerate(("pdja-answers-4", "pdja.2-voice"))
                        ],
                        {
                            "number": 4239,
                            "headRefName": f"agent/{foreign_id}",
                            "createdAt": "2026-08-14T03:00:00Z",
                        },
                        {
                            "number": 78,
                            "headRefName": f"agent/{prefix}extra-abc",
                            "createdAt": "2026-08-14T03:00:00Z",
                        },
                    ],
                }

                result, calls, _ = self.run_fixture(fixture)

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["errors"], [])
                self.assertEqual(len(payload["self_heal_candidates"]), 1)
                candidate = payload["self_heal_candidates"][0]
                self.assertEqual(candidate["original_id"], original_id)
                self.assertEqual(candidate["pr_number"], 77)
                self.assertEqual(candidate["recommendation"], "self-heal-original-and-review-wiring")
                looked_up = [
                    call["argv"][call["argv"].index("show") + 1]
                    for call in calls if call["tool"] == "bd" and "show" in call["argv"]
                ]
                self.assertEqual(looked_up, [original_id])

    def test_legacy_branch_requires_unambiguous_canonical_pr_association(self) -> None:
        dated_cases = (
            "dotted-anchor", "plain-anchor", "named-package", "named-hyphen-package",
            "canonical-control", "no-review",
            "second-review-anchor", "second-original-ref", "changed-pr-ref",
            "same-anchor-other-pr-open", "same-anchor-other-pr-merged",
            "foreign-record", "lookup-failure", "wrong-pr", "unrelated-head",
            "invalid-date", "unknown-suffix",
        )
        attached_cases = (
            "dotted-anchor", "alphanumeric", "nested-child", "no-review",
            "second-review-anchor", "second-original-ref", "changed-pr-ref",
            "same-anchor-other-pr-open", "same-anchor-other-pr-merged",
            "foreign-record", "missing-record", "lookup-failure", "wrong-pr",
            "unrelated-head", "unrelated-descriptive", "invalid-created-at", "invalid-number", "bool-number",
            "incomplete-context", "invalid-review-created-at", "invalid-package",
            "uppercase-suffix", "hyphen-suffix", "plain-anchor-suffix",
        )
        for spelling, case in ([("dated", case) for case in dated_cases]
                               + [("attached", case) for case in attached_cases]):
            with self.subTest(spelling=spelling, case=case):
                original_id = "bu-abc" if case in {"plain-anchor", "plain-anchor-suffix"} else "bu-abc.1"
                if case == "nested-child":
                    original_id += ".2"
                review_id = "bu-review"
                original = original_bead(original_id, 41)
                fixture = {
                    "prefix_payload": {"key": "issue_prefix", "value": "bu"},
                    "blocked_pr_review": [original, review_task(review_id, original_id)],
                    "resolver_payloads": {
                        review_id: {"ok": True, "original_id": original_id, "pr_number": 41},
                    },
                    "shows": {original_id: [dict(original)]},
                    "prs": {"41": pr_payload("OPEN")},
                    "open_prs": [{
                        "number": 41,
                        "headRefName": f"agent/{original_id}-20261105",
                        "createdAt": "2026-08-14T03:00:00Z",
                    }],
                }
                if spelling == "attached":
                    fixture["open_prs"][0]["headRefName"] = f"agent/{original_id}b"
                if case == "alphanumeric":
                    fixture["open_prs"][0]["headRefName"] += "2"
                elif case == "canonical-control":
                    fixture["open_prs"][0]["headRefName"] = f"agent/{original_id}"
                elif case in {"named-package", "named-hyphen-package"}:
                    suffix = "chronicler" if case == "named-package" else "chronicler-public-2"
                    fixture["open_prs"][0]["headRefName"] = f"agent/{original_id}-{suffix}"
                elif case == "no-review":
                    fixture["blocked_pr_review"] = [original]
                elif case in {"same-anchor-other-pr-open", "same-anchor-other-pr-merged"}:
                    fixture["blocked_pr_review"][0] = original_bead(original_id, 42)
                    state = "MERGED" if case.endswith("merged") else "OPEN"
                    other_pr = pr_payload(state, merged_at="2026-08-14T03:30:00Z")
                    other_pr["number"] = 42
                    fixture["prs"]["42"] = other_pr
                elif case in {"second-review-anchor", "second-original-ref"}:
                    other_id = "bu-other.2"
                    fixture["blocked_pr_review"].append(original_bead(other_id, 41))
                    if case == "second-review-anchor":
                        other_review = "bu-other-review"
                        fixture["blocked_pr_review"].append(review_task(other_review, other_id))
                        fixture["resolver_payloads"][other_review] = {
                            "ok": True, "original_id": other_id, "pr_number": 41,
                        }
                elif case == "changed-pr-ref":
                    fixture["shows"][original_id][0]["external_ref"] = "gh-pr:42"
                elif case == "foreign-record":
                    fixture["shows"][original_id][0]["id"] = "bu-foreign"
                elif case == "missing-record":
                    fixture["shows"][original_id] = []
                elif case == "unrelated-descriptive":
                    fixture["open_prs"][0]["headRefName"] = "agent/bu-unrelated-notes"
                elif case == "invalid-created-at":
                    fixture["open_prs"][0]["createdAt"] = "not-a-time"
                elif case in {"invalid-number", "bool-number"}:
                    fixture["open_prs"][0]["number"] = 0 if case == "invalid-number" else True
                elif case == "incomplete-context":
                    fixture["resolver_payloads"][review_id].pop("original_id")
                elif case == "invalid-review-created-at":
                    fixture["blocked_pr_review"][1]["created_at"] = "not-a-time"
                elif case == "invalid-package":
                    fixture["resolver_payloads"][review_id]["delivery_members"] = ["bu-other"]
                elif case == "uppercase-suffix":
                    fixture["open_prs"][0]["headRefName"] = f"agent/{original_id}B"
                elif case == "hyphen-suffix":
                    fixture["open_prs"][0]["headRefName"] += "-extra"
                elif case == "plain-anchor-suffix":
                    fixture["open_prs"][0]["headRefName"] = "agent/bu-abcb"
                elif case == "lookup-failure":
                    fixture["failures"] = {f"bd-show:{original_id}": "lookup failed"}
                elif case == "wrong-pr":
                    fixture["open_prs"][0]["number"] = 42
                elif case == "unrelated-head":
                    suffix = "b" if spelling == "attached" else "-20261105"
                    fixture["open_prs"][0]["headRefName"] = f"agent/bu-unrelated.9{suffix}"
                elif case == "invalid-date":
                    fixture["open_prs"][0]["headRefName"] = f"agent/{original_id}-20261340"
                elif case == "unknown-suffix":
                    fixture["open_prs"][0]["headRefName"] += "-extra"

                result, calls, _ = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                if case in {"dotted-anchor", "plain-anchor", "named-package",
                            "named-hyphen-package", "canonical-control", "alphanumeric", "nested-child"}:
                    self.assertEqual(payload["status"], "success", payload["errors"])
                    self.assertEqual(payload["errors"], [])
                    self.assertEqual(len(payload["self_heal_candidates"]), 1)
                    candidate = payload["self_heal_candidates"][0]
                    self.assertEqual(candidate["original_id"], original_id)
                    self.assertEqual(candidate["pr_number"], 41)
                    self.assertTrue(all(item["original_id"] == original_id
                                        for item in payload["findings"]))
                    self.assertEqual(candidate["canonical_review_id"], review_id)
                    self.assertEqual(candidate["recommendation"], "review-wiring-current")
                    self.assertTrue(any(call["tool"] == "bd" and "show" in call["argv"]
                                        and original_id in call["argv"] for call in calls))
                    branch_id = fixture["open_prs"][0]["headRefName"].removeprefix("agent/")
                    if branch_id != original_id:
                        self.assertFalse(any(branch_id in call["argv"] for call in calls))
                else:
                    self.assertEqual(payload["status"], "partial")
                    self.assertTrue(payload["errors"])
                    self.assertTrue(payload["findings"])
                    self.assertTrue(all(item["recommendation"] == "manual-triage"
                                        for item in payload["findings"] + payload["self_heal_candidates"]))

    def test_supporting_slices_require_a_current_unambiguous_same_repo_stack(self) -> None:
        review_id = "bu-review"
        cases = (
            "verified", "retargeted", "cross-repo", "stale-parent-head", "stale-root-head",
            "missing-parent", "duplicate-head", "duplicate-number", "cycle",
            "malformed-metadata", "separately-bound", "no-review",
            "closed-root", "merged-root", "other-canonical-root", "non-dotted-anchor",
            "hidden-root", "hidden-active-owner", "hidden-root-owner", "review-reference",
            "deferred-owner", "missing-census-root", "census-failure", "malformed-census",
            "duplicate-census", "changed-custody", "root-ref-changed", "parent-ref-changed",
            "parent-number-changed", "parent-repo-changed", "changed-parent-on-recheck",
            "hidden-duplicate-parent", "saturated", "above-cap", "delivery-owner",
            "legacy-source-reference", "missing-labels", "unknown-source-reference",
            "legacy-root-reference", "changed-canonical-reference", "invalid-census-shape",
            "new-competing-parent", "recheck-saturated", "inventory-reordered",
            "merge-exact", "merge-stale-parent", "merge-stale-child", "merge-wrong-order",
            "merge-parent-moved", "merge-child-moved", "merge-ref-moved", "merge-regenerated",
            "merge-missing-ref", "merge-missing-potential", "merge-missing-commit",
            "merge-ambiguous-refs", "merge-wrong-pr-ref", "merge-malformed-parents",
            "merge-malformed-sha", "merge-malformed-base", "merge-parent-rollback",
            "merge-owned-child", "merge-custody-changed",
        )
        for case in cases:
            with self.subTest(case=case):
                original_id = "bu-abc" if case == "non-dotted-anchor" else "bu-abc.1"
                branch = f"agent/{original_id}"
                first_slice = f"{branch}-s2"
                second_slice = f"{branch}-s3b"
                anchor = original_bead(original_id, 41)
                root_pr = {**pr_payload("OPEN"), "headRefName": branch, "isCrossRepository": False}
                first = {**root_pr, "number": 42, "headRefName": first_slice, "headRefOid": "b" * 40,
                         "baseRefName": branch, "baseRefOid": "a" * 40}
                second = {**root_pr, "number": 43, "headRefName": second_slice, "headRefOid": "c" * 40,
                          "baseRefName": first_slice, "baseRefOid": "b" * 40}
                disjoint = {**root_pr, "number": 44, "headRefName": "agent/bu-disjoint"}
                rows = [second, disjoint, first, root_pr]
                fixture = {
                    "prefix_payload": {"key": "issue_prefix", "value": "bu"},
                    "blocked_pr_review": [anchor, review_task(review_id, original_id)],
                    "resolver_payloads": {
                        review_id: {"ok": True, "original_id": original_id, "pr_number": 41},
                    },
                    "shows": {original_id: [anchor], "bu-disjoint": [{"id": "bu-disjoint", "status": "open"}]},
                    "prs": {str(pr["number"]): dict(pr) for pr in rows},
                    "open_prs": rows,
                }
                if case.startswith("merge-"):
                    second["baseRefOid"] = "d" * 40
                    fixture["prs"]["43"] = {**second, "potentialMergeCommit": {"oid": "e" * 40}}
                    commit = {"sha": "e" * 40, "parents": ["b" * 40, "c" * 40]}
                    fixture["api_payloads"] = {f"repos/owner/repo/git/commits/{'e' * 40}": commit}
                    refs = {f"refs/heads/{first_slice}": "b" * 40,
                            f"refs/heads/{second_slice}": "c" * 40,
                            "refs/pull/43/merge": "e" * 40}
                    if case == "merge-stale-parent":
                        commit["parents"][0] = "d" * 40
                    elif case == "merge-stale-child":
                        commit["parents"][1] = "d" * 40
                    elif case == "merge-wrong-order":
                        commit["parents"].reverse()
                    elif case == "merge-parent-moved":
                        refs[f"refs/heads/{first_slice}"] = "f" * 40
                    elif case == "merge-child-moved":
                        refs[f"refs/heads/{second_slice}"] = "f" * 40
                    elif case == "merge-missing-ref":
                        refs.pop("refs/pull/43/merge")
                    elif case == "merge-missing-potential":
                        fixture["prs"]["43"]["potentialMergeCommit"] = None
                    elif case == "merge-missing-commit":
                        fixture["api_payloads"] = {}
                    elif case == "merge-wrong-pr-ref":
                        refs["refs/pull/42/merge"] = refs.pop("refs/pull/43/merge")
                    elif case == "merge-malformed-parents":
                        commit["parents"].append("d" * 40)
                    elif case == "merge-malformed-sha":
                        commit["sha"] = "f" * 40
                    elif case == "merge-malformed-base":
                        second["baseRefOid"] = None
                        fixture["prs"]["43"]["baseRefOid"] = None
                    elif case == "merge-parent-rollback":
                        refs[f"refs/heads/{first_slice}"] = "a" * 40
                        commit["parents"][0] = "a" * 40
                    elif case == "merge-owned-child":
                        fixture["active_associations"] = [anchor, {
                            "id": "bu-hidden", "status": "in_progress", "external_ref": "gh-pr:43",
                        }]
                    elif case == "merge-custody-changed":
                        fixture["association_sequence"] = [[anchor], [anchor, {
                            "id": "bu-hidden", "status": "deferred", "external_ref": "gh-pr:43",
                        }]]
                    ref_text = "".join(f"{oid}\t{ref}\n" for ref, oid in refs.items())
                    fixture["merge_ref_reads"] = [ref_text, ref_text]
                    if case == "merge-ambiguous-refs":
                        fixture["merge_ref_reads"] = [ref_text + f"{'e' * 40}\trefs/pull/43/merge\n"]
                    elif case == "merge-ref-moved":
                        fixture["merge_ref_reads"][1] = ref_text.replace("e" * 40, "f" * 40)
                    elif case == "merge-regenerated":
                        fixture["pr_view_sequences"] = {"43": [fixture["prs"]["43"], {
                            **fixture["prs"]["43"], "potentialMergeCommit": {"oid": "f" * 40},
                        }]}
                        fixture["merge_ref_reads"][1] = ref_text.replace("e" * 40, "f" * 40)
                        fixture["api_payloads"][f"repos/owner/repo/git/commits/{'f' * 40}"] = {
                            "sha": "f" * 40, "parents": ["b" * 40, "c" * 40],
                        }
                if case == "hidden-root":
                    rows[0:0] = [
                        {**disjoint, "number": 100 + i, "headRefName": f"docs/{i}"}
                        for i in range(30)
                    ]
                    fixture["simulate_pr_limit"] = True
                elif case in {"hidden-active-owner", "hidden-root-owner", "review-reference"}:
                    owner = {**original_bead("bu-hidden", 41 if case == "hidden-root-owner" else 42),
                             "status": "in_progress", "labels": []}
                    if case == "review-reference":
                        owner.update(labels=["pr-review", "pr-review-task"])
                        # A review PR reference does not claim implementation custody.
                        fixture["pr_review_tasks_all"] = [
                            review_task(review_id, original_id), owner,
                        ]
                        owner["created_at"] = "2026-08-14T03:30:00Z"
                        fixture["resolver_payloads"]["bu-hidden"] = {
                            "ok": True, "original_id": original_id, "pr_number": 41,
                        }
                        owner["external_ref"] = "gh-pr:41"
                    fixture["active_associations"] = [anchor, review_task(review_id, original_id), owner]
                elif case in {"deferred-owner", "delivery-owner", "legacy-source-reference", "unknown-source-reference"}:
                    owner = {"id": "bu-hidden", "status": "deferred", "labels": [], "external_ref": "gh-pr:42"}
                    if case == "legacy-source-reference":
                        owner["external_ref"] = "gh-42"
                    elif case == "unknown-source-reference":
                        owner["external_ref"] = "unknown:42"
                    if case == "delivery-owner":
                        owner["external_ref"] = ""
                        owner["metadata"] = {"delivery": {
                            "schema": "beads-delivery/v1", "anchor": "bu-hidden", "members": ["bu-hidden"],
                            "owner": "coordinator:peer", "outcome": "bu-outcome", "base_branch": "main",
                            "packet": "packet", "branch": first_slice,
                        }}
                    fixture["active_associations"] = [anchor, owner]
                elif case == "missing-labels":
                    fixture["active_associations"] = [anchor, {"id": "bu-unlabelled", "status": "open"}]
                elif case == "missing-census-root":
                    fixture["active_associations"] = []
                elif case == "legacy-root-reference":
                    fixture["active_associations"] = [{**anchor, "external_ref": "gh-41"}]
                elif case == "invalid-census-shape":
                    fixture["active_associations"] = {"records": [anchor]}
                elif case == "census-failure":
                    fixture["failures"] = {"bd-list:open,in_progress,blocked,deferred:": "read failed"}
                elif case == "malformed-census":
                    fixture["active_associations"] = [anchor, {"id": "bu-hidden", "status": "in_progress", "labels": "invalid"}]
                elif case == "duplicate-census":
                    fixture["active_associations"] = [anchor, anchor]
                elif case == "changed-custody":
                    fixture["association_sequence"] = [
                        [anchor], [anchor, {**original_bead("bu-hidden", 42), "status": "open"}],
                    ]
                elif case == "changed-canonical-reference":
                    fixture["association_sequence"] = [[anchor], [{**anchor, "external_ref": "gh-41"}]]
                elif case in {"root-ref-changed", "parent-ref-changed", "parent-number-changed", "parent-repo-changed"}:
                    target = fixture["prs"]["41" if case == "root-ref-changed" else "42"]
                    key, value = {
                        "root-ref-changed": ("headRefName", "agent/bu-other.1"),
                        "parent-ref-changed": ("headRefName", "agent/bu-other.1-s2"),
                        "parent-number-changed": ("number", 49),
                        "parent-repo-changed": ("isCrossRepository", True),
                    }[case]
                    target[key] = value
                elif case == "changed-parent-on-recheck":
                    fixture["pr_view_sequences"] = {"42": [dict(first), {**first, "headRefOid": "e" * 40}]}
                elif case in {"hidden-duplicate-parent", "saturated", "above-cap"}:
                    count = 996 if case == "saturated" else 997 if case == "above-cap" else 30
                    rows.extend([
                        {**disjoint, "number": 100 + i, "headRefName": f"docs/{i}"}
                        for i in range(count)
                    ])
                    if case == "hidden-duplicate-parent":
                        rows.append({**first, "number": 99})
                    fixture["simulate_pr_limit"] = case == "hidden-duplicate-parent"
                elif case in {"new-competing-parent", "recheck-saturated", "inventory-reordered"}:
                    after_rows = list(reversed(rows))
                    if case == "new-competing-parent":
                        after_rows.append({**first, "number": 99})
                    elif case == "recheck-saturated":
                        after_rows.extend({**disjoint, "number": 100 + i, "headRefName": f"docs/{i}"}
                                          for i in range(996))
                    fixture["open_pr_sequence"] = [list(rows), after_rows]
                if case == "retargeted":
                    first["baseRefName"] = "main"
                elif case == "cross-repo":
                    first["isCrossRepository"] = True
                elif case == "stale-parent-head":
                    second["baseRefOid"] = "d" * 40
                elif case == "stale-root-head":
                    fixture["prs"]["41"]["headRefOid"] = "d" * 40
                elif case == "missing-parent":
                    rows.remove(first)
                elif case == "duplicate-head":
                    rows.append({**first, "number": 45})
                elif case == "duplicate-number":
                    rows.append({**first, "headRefName": "docs/elsewhere"})
                elif case == "cycle":
                    first.update(baseRefName=second_slice, baseRefOid="c" * 40)
                elif case == "malformed-metadata":
                    second.pop("isCrossRepository")
                elif case == "separately-bound":
                    original = original_bead("bu-elsewhere", 42)
                    fixture["blocked_pr_review"].append(original)
                    fixture["shows"]["bu-elsewhere"] = [original]
                    fixture["prs"]["42"] = first
                elif case == "no-review":
                    fixture["blocked_pr_review"] = [anchor]
                elif case in {"closed-root", "merged-root"}:
                    fixture["prs"]["41"].update(
                        state=case.removesuffix("-root").upper(),
                        mergedAt="2026-08-14T03:30:00Z" if case == "merged-root" else None,
                    )
                elif case == "other-canonical-root":
                    other_id, other_review_id = "bu-other.2", "bu-other-review"
                    other = original_bead(other_id, 45)
                    other_pr = {**root_pr, "number": 45, "headRefName": f"agent/{other_id}",
                                "headRefOid": "d" * 40}
                    fixture["blocked_pr_review"].extend([other, review_task(other_review_id, other_id)])
                    fixture["shows"][other_id] = [other]
                    fixture["resolver_payloads"][other_review_id] = {
                        "ok": True, "original_id": other_id, "pr_number": 45,
                    }
                    fixture["prs"]["45"] = other_pr
                    rows.append(other_pr)
                    second.update(baseRefName=other_pr["headRefName"], baseRefOid=other_pr["headRefOid"])

                result, calls, repo_root = self.run_fixture(fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                candidates = payload["self_heal_candidates"]
                expected_numbers = {
                    "non-dotted-anchor": [41, 42, 43, 44],
                    "other-canonical-root": [41, 44, 45],
                }.get(case, [41, 44])
                self.assertEqual([item["pr_number"] for item in candidates], expected_numbers)
                if case in {"verified", "hidden-root", "review-reference", "missing-labels", "inventory-reordered", "merge-exact"}:
                    self.assertEqual(payload["status"], "success", payload["errors"])
                    self.assertEqual(payload["errors"], [])
                    self.assertEqual(candidates[0]["canonical_review_id"], review_id)
                    self.assertEqual(candidates[0]["recommendation"], "review-wiring-current")
                    self.assertEqual(candidates[1]["recommendation"], "self-heal-original-and-review-wiring")
                    census = [call["argv"] for call in calls if call["tool"] == "bd"
                              and "--include-ephemeral" in call["argv"]]
                    expected = ["-C", repo_root, "list", "--status=open,in_progress,blocked,deferred",
                                "--limit", "0", "--json", "--include-ephemeral", "--include-infra",
                                "--include-templates", "--include-gates"]
                    self.assertEqual(census, [expected, expected])
                    if case == "merge-exact":
                        ref_reads = [call["argv"] for call in calls if call["tool"] == "git"]
                        self.assertEqual(ref_reads, [["ls-remote", "--refs", "https://github.com/owner/repo.git",
                                                     f"refs/heads/{first_slice}", f"refs/heads/{second_slice}",
                                                     "refs/pull/43/merge"]] * 2)
                else:
                    self.assertEqual(payload["status"], "partial")
                    if case == "non-dotted-anchor":
                        self.assertTrue(any(error["scope"] == "self-heal" for error in payload["errors"]))
                    else:
                        self.assertIn({"code": "invalid-record", "scope": "open-prs"}, payload["errors"])
                    self.assertTrue(all(item["recommendation"] == "manual-triage"
                                        for item in payload["findings"] + candidates))
                    if case in {"saturated", "above-cap"}:
                        self.assertIn({"code": "open-pr-limit-reached", "scope": "open-prs"}, payload["errors"])
                    if case == "merge-owned-child":
                        self.assertFalse(any(call["tool"] == "git" for call in calls))

    def test_invalid_project_prefix_or_in_prefix_discovery_manualizes_collection(self) -> None:
        original_id = "bu-abc"
        review_id = "bu-review"
        base_fixture = {
            "prefix_payload": {"key": "issue_prefix", "value": "bu"},
            "blocked_pr_review": [original_bead(original_id, 41), review_task(review_id, original_id)],
            "resolver_payload": {"ok": True, "original_id": original_id, "pr_number": 41},
            "prs": {"41": pr_payload("OPEN")},
            "shows": {original_id: [original_bead(original_id, 41)]},
            "open_prs": [{"number": 77, "headRefName": "agent/bu-missing",
                          "createdAt": "2026-08-14T03:00:00Z"}],
        }
        cases = {
            "missing-prefix": ({"prefix_payload": {"key": "issue_prefix"}}, "invalid-issue-prefix", "issue-prefix"),
            "null-prefix": ({"prefix_payload": {"key": "issue_prefix", "value": None}}, "invalid-issue-prefix", "issue-prefix"),
            "empty-prefix": ({"prefix_payload": {"key": "issue_prefix", "value": ""}}, "invalid-issue-prefix", "issue-prefix"),
            "invalid-prefix": ({"prefix_payload": {"key": "issue_prefix", "value": "bu-*"}}, "invalid-issue-prefix", "issue-prefix"),
            "wrong-key": ({"prefix_payload": {"key": "other", "value": "bu"}}, "invalid-issue-prefix", "issue-prefix"),
            "invalid-shape": ({"prefix_payload": []}, "invalid-issue-prefix", "issue-prefix"),
            "prefix-read-failure": ({"failures": {"bd-config:issue_prefix": "read failed"}}, "command-failed", "issue-prefix"),
            "missing-issue": ({}, "command-failed", "self-heal"),
            "malformed-show": ({"shows": {**base_fixture["shows"], "bu-missing": {}}}, "invalid-show-response", "self-heal"),
            "malformed-branch": ({"open_prs": [{"number": 77, "headRefName": "agent/bu-INVALID"}]}, "invalid-record", "open-prs"),
        }
        for name, (overrides, code, scope) in cases.items():
            with self.subTest(name=name):
                result, calls, _ = self.run_fixture({**base_fixture, **overrides})

                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "partial")
                self.assertIn({"code": code, "scope": scope}, payload["errors"])
                self.assertTrue(payload["findings"])
                self.assertTrue(all(item["recommendation"] == "manual-triage"
                                    for item in payload["findings"] + payload["self_heal_candidates"]))
                if scope == "issue-prefix":
                    self.assertFalse(any("bu-missing" in call["argv"] for call in calls))

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
            "blocked_pr_review": ["TOKEN=top-secret /private/absolute/path"],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertIn({"code": "invalid-record", "scope": "blocked-pr-review"}, payload["errors"])
        self.assertNotIn("TOKEN=top-secret", result.stdout + result.stderr)
        self.assertNotIn("/private/absolute/path", result.stdout + result.stderr)

    def test_missing_canonical_pr_reference_is_an_unresolved_partial_finding(self) -> None:
        fixture = {
            "blocked_pr_review": [
                {
                    "id": "aib-swr.1",
                    "labels": ["pr-review"],
                    "external_ref": "",
                    "status": "blocked",
                },
            ],
            "open_prs": [],
        }

        result, _, _ = self.run_fixture(fixture)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        finding = payload["findings"][0]
        self.assertEqual(finding["context_status"], "missing-pr-number")
        self.assertEqual(finding["recommendation"], "manual-triage")
        self.assertIn({"code": "missing-pr-number", "scope": "original-context"}, payload["errors"])


if __name__ == "__main__":
    unittest.main()
