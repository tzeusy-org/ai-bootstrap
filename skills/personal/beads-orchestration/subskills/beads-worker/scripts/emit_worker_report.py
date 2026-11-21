# /// script
# requires-python = ">=3.11"
# ///

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from delivery_package import delivery_branch, load_delivery, validate_results


VALID_STATUSES = {
    "completed-pr-opened",
    "completed-direct-merge-candidate",
    "blocked-awaiting-coordinator",
    "invalid-runtime-context",
}
VALID_HANDOFF_PATHS = {
    "pr-required",
    "direct-merge-candidate",
    "blocked-awaiting-coordinator",
    "invalid-runtime-context",
}
VALID_BRANCH_PUSHED = {"yes", "no"}
VALID_RECOVERY_STATES = {"branch-pushed", "local-only", "no-code-changes"}
# not-run: the repo defines the gate but it did not run (reads as skipped).
# not-applicable: the repo defines no such gate (e.g. no typecheck for shell).
VALID_GATE_RESULTS = {"pass", "fail", "not-run", "not-applicable"}
COMPLETED_STATUSES = {"completed-pr-opened", "completed-direct-merge-candidate"}
EXPECTED_GATES = ("lint", "typecheck", "tests")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a validated machine-readable Beads worker report.",
    )
    parser.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    parser.add_argument("--issue-id", required=True)
    parser.add_argument("--delivery-file", help="Current cohesive package JSON; ISSUE_ID is its anchor.")
    parser.add_argument("--branch")
    parser.add_argument("--member-results-json", help="One {id,status:satisfied|partial|blocked,evidence} result per package member.")
    parser.add_argument("--worktree-path", required=True)
    parser.add_argument("--head-commit", required=True)
    parser.add_argument("--branch-pushed", required=True, choices=sorted(VALID_BRANCH_PUSHED))
    parser.add_argument("--handoff-path", required=True, choices=sorted(VALID_HANDOFF_PATHS))
    parser.add_argument("--pr-url", default="n/a")
    parser.add_argument("--pr-number", default="n/a")
    parser.add_argument("--base-branch", default="n/a")
    parser.add_argument("--review-reason", default="n/a")
    parser.add_argument("--recovery-state", help="Default: derived from status, --branch-pushed and git state.")
    parser.add_argument("--resume-condition", default="n/a")
    parser.add_argument("--failing-command", default="n/a")
    parser.add_argument("--remote-branch", help="Default: the worktree's upstream, else n/a.")
    parser.add_argument("--dirty-worktree", choices=("yes", "no", "unknown"), help="Default: from git status.")
    parser.add_argument("--unpushed-commits", choices=("yes", "no", "unknown"), help="Default: commits ahead of upstream.")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--quality-gate", action="append", default=[])
    parser.add_argument("--changes", action="append", default=[])
    parser.add_argument("--tests", action="append", default=[])
    parser.add_argument("--discovered-follow-ups-json", default="[]")
    parser.add_argument("--blockers-json", default="[]")
    return parser.parse_args()


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def parse_json_array(raw: str, field_name: str) -> list[object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return value


def parse_quality_gates(raw_gates: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw_gates:
        if "=" not in item:
            raise ValueError(f"Quality gate '{item}' must use key=value format")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in EXPECTED_GATES:
            raise ValueError(
                f"Unknown quality gate '{key}'. Expected only: {', '.join(EXPECTED_GATES)}"
            )
        if value not in VALID_GATE_RESULTS:
            raise ValueError(
                f"Invalid result '{value}' for quality gate '{key}'. "
                f"Expected one of: {', '.join(sorted(VALID_GATE_RESULTS))}"
            )
        parsed[key] = value

    for gate in EXPECTED_GATES:
        parsed.setdefault(gate, "not-run")
    return parsed


def git(worktree: str, *argv: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", worktree, *argv], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_recovery_facts(worktree: str) -> dict[str, str]:
    """Read dirty/unpushed/upstream from the worktree; unknown when git can't say."""
    status = git(worktree, "status", "--porcelain")
    upstream = git(worktree, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") if status is not None else None
    ahead = git(worktree, "rev-list", "--count", "@{u}..HEAD") if upstream else None
    return {
        "dirty_worktree": "unknown" if status is None else ("yes" if status else "no"),
        "unpushed_commits": "unknown" if ahead is None or not ahead.isdigit() else ("yes" if int(ahead) else "no"),
        "remote_branch": upstream or "n/a",
    }


def default_recovery(args: argparse.Namespace) -> None:
    """Fill omitted recovery fields from git, so a completed report never claims no-code-changes."""
    facts = git_recovery_facts(args.worktree_path)
    for field in ("dirty_worktree", "unpushed_commits", "remote_branch"):
        if getattr(args, field) is None:
            setattr(args, field, facts[field])
    if args.recovery_state is None:
        if args.status == "invalid-runtime-context":
            args.recovery_state = "no-code-changes"
        elif args.branch_pushed == "yes":
            args.recovery_state = "branch-pushed"
        elif args.dirty_worktree == "no" and args.unpushed_commits == "no":
            args.recovery_state = "no-code-changes"
        else:
            # Unknown git state is treated as local work so recovery looks for it.
            args.recovery_state = "local-only"


def validate_args(args: argparse.Namespace, blockers: list[object]) -> None:
    expected_branch = f"agent/{args.issue_id}"
    args.delivery = None
    args.member_results = None
    args.delivery_error = None
    if args.delivery_file:
        try:
            args.delivery = load_delivery(args.delivery_file)
            if args.delivery["anchor"] != args.issue_id or args.delivery.get("state", "active") != "active":
                raise ValueError("Delivery anchor or state mismatch")
        except (OSError, ValueError):
            if args.status != "invalid-runtime-context":
                raise
            args.delivery = None
            args.delivery_error = "delivery-manifest-unverifiable"
        if args.delivery is not None:
            if args.member_results_json is None:
                if args.status != "invalid-runtime-context":
                    raise ValueError("Delivery report requires member-results-json")
                args.member_results = [{"id": member, "status": "blocked", "evidence": "Not evaluated: runtime bootstrap failed before implementation."} for member in args.delivery["members"]]
            else:
                args.member_results = validate_results(args.delivery, json.loads(args.member_results_json))
    elif args.member_results_json is not None:
        raise ValueError("member-results-json requires delivery-file")

    if args.delivery is not None:
        expected_branch = delivery_branch(args.delivery)
    if args.branch is None:
        args.branch = expected_branch
    if args.branch != expected_branch and args.status != "invalid-runtime-context":
        raise ValueError("Branch must match the accepted delivery branch")

    if args.recovery_state not in VALID_RECOVERY_STATES:
        raise ValueError(
            f"Recovery-State must be one of: {', '.join(sorted(VALID_RECOVERY_STATES))}"
        )
    if args.status in COMPLETED_STATUSES and args.recovery_state != "branch-pushed":
        raise ValueError(f"{args.status} requires Recovery-State: branch-pushed")

    if args.status == "completed-pr-opened":
        if args.handoff_path != "pr-required":
            raise ValueError("completed-pr-opened requires Handoff-Path: pr-required")
        if args.branch_pushed != "yes":
            raise ValueError("completed-pr-opened requires Branch-Pushed: yes")
        if args.pr_url == "n/a":
            raise ValueError("completed-pr-opened requires PR-URL")
        if args.pr_number == "n/a":
            raise ValueError("completed-pr-opened requires PR-Number")
    elif args.status == "completed-direct-merge-candidate":
        if args.handoff_path != "direct-merge-candidate":
            raise ValueError(
                "completed-direct-merge-candidate requires Handoff-Path: direct-merge-candidate"
            )
        if args.branch_pushed != "yes":
            raise ValueError("completed-direct-merge-candidate requires Branch-Pushed: yes")
        if args.pr_url != "n/a" or args.pr_number != "n/a":
            raise ValueError(
                "completed-direct-merge-candidate requires PR-URL and PR-Number to be n/a"
            )
    elif args.status == "blocked-awaiting-coordinator":
        if args.handoff_path != "blocked-awaiting-coordinator":
            raise ValueError(
                "blocked-awaiting-coordinator requires Handoff-Path: blocked-awaiting-coordinator"
            )
        if not blockers:
            raise ValueError("blocked-awaiting-coordinator requires at least one blocker")
        if args.failing_command == "n/a":
            raise ValueError("blocked-awaiting-coordinator requires Failing-Command")
    elif args.status == "invalid-runtime-context":
        if args.handoff_path != "invalid-runtime-context":
            raise ValueError(
                "invalid-runtime-context requires Handoff-Path: invalid-runtime-context"
            )


def main() -> int:
    args = parse_args()

    try:
        gates = parse_quality_gates(args.quality_gate)
        follow_ups = parse_json_array(args.discovered_follow_ups_json, "Discovered-Follow-Ups-JSON")
        blockers = parse_json_array(args.blockers_json, "Blockers-JSON")
        default_recovery(args)
        validate_args(args, blockers)
    except (OSError, ValueError) as exc:
        return fail(str(exc))

    changes = args.changes or ["- none recorded"]
    tests = args.tests or ["- none recorded"]

    report = "\n".join(
        [
            f"## Worker Report: {args.issue_id}",
            "",
            f"Status: {args.status}",
            f"Issue: {args.issue_id}",
            f"Branch: {args.branch}",
            f"Worktree: {args.worktree_path}",
            f"Head-Commit: {args.head_commit}",
            f"Branch-Pushed: {args.branch_pushed}",
            f"Handoff-Path: {args.handoff_path}",
            f"PR-URL: {args.pr_url}",
            f"PR-Number: {args.pr_number}",
            f"Base-Branch: {args.base_branch}",
            f"Review-Reason: {args.review_reason}",
            f"Recovery-State: {args.recovery_state}",
            f"Resume-Condition: {args.resume_condition}",
            f"Summary: {args.summary}",
            "",
            "Recovery-Details:",
            f"- Failing-Command: {args.failing_command}",
            f"- Remote-Branch: {args.remote_branch}",
            f"- Dirty-Worktree: {args.dirty_worktree}",
            f"- Unpushed-Commits: {args.unpushed_commits}",
            "",
            "Quality-Gates:",
            f"- lint: {gates['lint']}",
            f"- typecheck: {gates['typecheck']}",
            f"- tests: {gates['tests']}",
            "",
            "Changes:",
            *[f"- {item}" for item in changes],
            "",
            "Tests:",
            *[f"- {item}" for item in tests],
            "",
            "Discovered-Follow-Ups-JSON:",
            "```json",
            json.dumps(follow_ups, separators=(",", ":")),
            "```",
            "",
            "Blockers-JSON:",
            "```json",
            json.dumps(blockers, separators=(",", ":")),
            "```",
        ]
    )

    if args.delivery_error:
        report += "\n\nDelivery-Validation-Error: " + args.delivery_error
    if args.delivery is not None:
        report += "\n\nDelivery-JSON:\n```json\n" + json.dumps(args.delivery, separators=(",", ":")) + "\n```"
        report += "\n\nMember-Results-JSON:\n```json\n" + json.dumps(args.member_results, separators=(",", ":")) + "\n```"
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
