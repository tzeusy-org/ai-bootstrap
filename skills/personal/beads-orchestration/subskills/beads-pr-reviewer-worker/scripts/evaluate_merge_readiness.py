#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
import argparse
import json
import re
import subprocess
import sys


THREAD_COUNT_QUERY = """
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$after) {
        nodes { isResolved }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
""".strip()

LABEL_QUERY = """
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      labels(first:100, after:$after) {
        nodes { name }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

ALLOWED_MERGE_STATES = {"CLEAN", "HAS_HOOKS", "UNSTABLE"}

PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/([0-9]+)$")


def fail(code, message, **extra):
    payload = {"ok": False, "error_code": code, "error": message}
    payload.update(extra)
    print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
    sys.exit(1)


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "command failed")
    return result.stdout


def run_json(cmd):
    return json.loads(run(cmd))


def unresolved_count(owner, repo, pr_number):
    total = 0
    after = None
    while True:
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={THREAD_COUNT_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"repo={repo}",
            "-F",
            f"number={pr_number}",
        ]
        if after:
            cmd.extend(["-F", f"after={after}"])
        payload = run_json(cmd)
        review_threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        total += sum(1 for node in review_threads["nodes"] if not node["isResolved"])
        if not review_threads["pageInfo"]["hasNextPage"]:
            return total
        after = review_threads["pageInfo"]["endCursor"]


def owner_merge_held(owner, repo, pr_number):
    """Read every label page; absent/partial/malformed evidence is not consent."""
    after = None
    seen = set()
    held = False
    while True:
        cmd = [
            "gh", "api", "graphql", "-f", f"query={LABEL_QUERY}",
            "-F", f"owner={owner}", "-F", f"repo={repo}",
            "-F", f"number={pr_number}",
        ]
        if after is not None:
            cmd.extend(["-F", f"after={after}"])
        payload = run_json(cmd)
        errors = payload.get("errors")
        if errors is not None and (not isinstance(errors, list) or errors):
            raise ValueError("label query returned GraphQL errors")
        labels = payload["data"]["repository"]["pullRequest"]["labels"]
        nodes = labels["nodes"]
        info = labels["pageInfo"]
        if (
            not isinstance(nodes, list)
            or not all(isinstance(node, dict) and isinstance(node.get("name"), str) for node in nodes)
            or type(info["hasNextPage"]) is not bool
            or "endCursor" not in info
            or (info["endCursor"] is not None and not isinstance(info["endCursor"], str))
        ):
            raise ValueError("invalid label page")
        held = held or any(node["name"] == "owner-merge" for node in nodes)
        if not info["hasNextPage"]:
            return held
        after = info["endCursor"]
        if not isinstance(after, str) or not after or after in seen:
            raise ValueError("incomplete or repeated label cursor")
        seen.add(after)


def verify_pr_identity(pr, owner, repo, pr_number):
    """Fail closed unless gh answered for exactly the requested PR.

    gh resolves an unscoped PR number against the cwd's repository, so a
    colliding number in another checkout once yielded an unrelated merged PR.
    """
    match = PR_URL.match(pr.get("url") or "")
    actual = (match.group(1), match.group(2), int(match.group(3))) if match else None
    if (
        actual is None
        or actual[0].lower() != owner.lower()
        or actual[1].lower() != repo.lower()
        or actual[2] != pr_number
        or pr.get("number") != pr_number
    ):
        fail(
            "pr-identity-mismatch",
            f"gh returned {pr.get('url')!r}, not {owner}/{repo}#{pr_number}",
            pr_number=pr_number,
        )


def fetch_required_checks(owner, repo, pr_number):
    result = subprocess.run(
        ["gh", "pr", "checks", str(pr_number), "--repo", f"{owner}/{repo}",
         "--required", "--json", "state"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Repos without branch protection rules report no required checks.
        # Treat this as zero required checks rather than a hard failure.
        if "no required checks" in stderr.lower():
            return []
        fail("required-checks-unavailable", stderr or "unable to fetch required checks", pr_number=pr_number)
    return json.loads(result.stdout)


def detect_merge_queue(owner, repo, base_branch):
    """Return True/False when the base branch's rulesets could be read, else None.

    None means "unknown": the reviewer should attempt a direct merge and fall
    back to ``gh pr merge --auto`` if GitHub rejects it in favour of the queue.
    """
    if not base_branch:
        return None
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo}/rules/branches/{base_branch}",
            "--jq",
            '[.[] | select(.type == "merge_queue")] | length',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip() or "0") > 0
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Evaluate whether a PR is merge-ready.")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true", help="Run evaluation without performing any mutations (evaluation is already read-only; flag provided for uniformity).")
    args = parser.parse_args()

    try:
        pr = run_json([
            "gh",
            "pr",
            "view",
            str(args.pr_number),
            "--repo",
            f"{args.owner}/{args.repo}",
            "--json",
            "number,state,isDraft,mergeStateStatus,reviewDecision,mergedAt,url,baseRefName,headRefName",
        ])
        verify_pr_identity(pr, args.owner, args.repo, args.pr_number)
        held = owner_merge_held(args.owner, args.repo, args.pr_number)
        unresolved = unresolved_count(args.owner, args.repo, args.pr_number)
        checks = fetch_required_checks(args.owner, args.repo, args.pr_number)
        merge_queue = detect_merge_queue(args.owner, args.repo, pr.get("baseRefName"))
    except RuntimeError as exc:
        fail("command-failed", str(exc), pr_number=args.pr_number)
    except Exception as exc:
        fail("unexpected-error", str(exc), pr_number=args.pr_number)

    required_non_green = sum(
        1
        for check in checks
        if check.get("state") not in {"SUCCESS", "SKIPPED", "NEUTRAL"}
    )

    reasons = []
    if pr.get("state") == "OPEN" and held:
        reasons.append("held-for-owner")
    if pr.get("state") != "OPEN":
        reasons.append(f"state={pr.get('state')}")
    if pr.get("isDraft"):
        reasons.append("draft")
    if unresolved:
        reasons.append(f"unresolved_threads={unresolved}")
    if required_non_green:
        reasons.append(f"required_non_green={required_non_green}")
    if (pr.get("reviewDecision") or "REVIEW_REQUIRED") == "CHANGES_REQUESTED":
        reasons.append("review_decision=CHANGES_REQUESTED")
    if (pr.get("mergeStateStatus") or "UNKNOWN") not in ALLOWED_MERGE_STATES:
        reasons.append(f"merge_state={pr.get('mergeStateStatus') or 'UNKNOWN'}")

    output = {
        "ok": True,
        "pr_url": pr.get("url"),
        "state": pr.get("state"),
        "is_draft": pr.get("isDraft"),
        "merge_state_status": pr.get("mergeStateStatus"),
        "review_decision": pr.get("reviewDecision") or "REVIEW_REQUIRED",
        "merged_at": pr.get("mergedAt"),
        "base_branch": pr.get("baseRefName"),
        "head_branch": pr.get("headRefName"),
        "unresolved_count": unresolved,
        "required_non_green": required_non_green,
        "merge_ok": len(reasons) == 0,
        "merge_queue": merge_queue,
        "merge_command": (
            "gh pr merge --squash --auto" if merge_queue else "gh pr merge --squash"
        ),
        "reasons": reasons,
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
