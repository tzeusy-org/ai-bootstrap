#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Emit a read-only compact report of PR-review state for a coordinator.

Fail-closed contract (canonical; `coordinator-loop.md` Step 0 only points here).
Recommendations are never authorization: the coordinator re-verifies live state
before any mutation. The scan is reported `partial` and every finding and
self-heal candidate is held at `manual-triage` — suppressing dispatch, wiring,
dedupe, and self-heal for the *whole* active collection — when any of these
fails:

- inventory: a list-shaped but malformed active `pr-review-task` inventory, or a record
  missing a required label, status, or uniqueness guarantee
- resolver: resolver `issue_id` not matching the discovered review-task ID; a
  `bd show` record not echoing the requested original ID; a review task whose
  resolved original is itself (self-referential)
- context: any active review-task context missing a valid original identity or
  a parseable creation timestamp (canonical selection uses parsed chronology,
  never raw timestamp text)
- originals: any blocked original that does not resolve to the requested bead
  with a valid `gh-pr:<N>` reference
- PR metadata: any resolved PR number without a valid `gh pr view` record; the
  sole exception is a lone, otherwise complete `command-failed` lookup, reported
  only as a no-action `skip-command-failure` finding
- discovery: missing or invalid authoritative `bd config get issue_prefix`, or
  any `gh pr list` / in-prefix candidate-source lookup failure (open-PR
  discovery is collection evidence, not a post-selection best effort);
  only `agent/<issue_prefix>-...` branches infer issues. A legacy
  `agent/<original>-YYYYMMDD`, `agent/<original>-<package>` or an attached
  lowercase alphanumeric suffix on a dotted-decimal child (e.g. `.2b`) can
  reuse one unambiguous canonical review/PR association after checking the
  original's current PR reference. Association is checked before branch
  inference: descriptive-hyphen branches with neither a known PR binding nor
  a related known anchor infer no issue. A dotted child with an `-sN` slice
  suffix can be a supporting PR when its same-repository base chain reaches
  one current canonical review head and no slice has a separate binding. Open
  PR discovery is bounded at 1000 rows; saturation is incomplete, never a
  completeness proof. Stack custody uses an unlabelled, unlimited census of
  all active Beads (including deferred and normally hidden records), not just
  review originals. Review-task references are not implementation ownership.
  Census bindings and exact PR identities/links must survive a bounded recheck;
  this is observation consistency, not a cross-service lock or merge authority.
  Known conflicting bindings, ordinary canonical lookup failures and other
  malformed in-prefix identities remain failures
- relations: `review_relation_graph.review_relation_error` rejects malformed
  entries, self-links, duplicate review-ID left sides, a review ID reused in the
  original role, and reciprocal/longer cycles (`cyclic-review-relation`);
  distinct review tasks pointing at the same original remain valid
  canonical-plus-duplicate dedupe input
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from review_relation_graph import review_relation_error

PACKAGE_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(PACKAGE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SCRIPTS))
from delivery_package import metadata as bead_metadata, validate_delivery


SCHEMA = "beads-pr-review-normalization/v1"
COOLDOWN = timedelta(minutes=5)
OPEN_PR_LIMIT = 1000
STACK_PR_FIELDS = "number,headRefName,createdAt,headRefOid,baseRefName,baseRefOid,isCrossRepository"
BEAD_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.[0-9]+)*\Z")
PR_HEAD_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
STACK_SLICE_RE = re.compile(r"(agent/.+\.[0-9]+)-s[1-9][0-9]*[a-z]?\Z")
ISSUE_PREFIX_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
SAFE_CODE_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})
BEAD_STATUSES = frozenset({"open", "in_progress", "blocked", "closed"})


def emit(payload: dict[str, Any]) -> None:
    """Write exactly one compact JSON envelope and never expose command output."""
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def safe_code(value: object, fallback: str) -> str:
    candidate = str(value or "")
    return candidate if SAFE_CODE_RE.fullmatch(candidate) else fallback


def report_error(errors: list[dict[str, str]], code: str, scope: str) -> None:
    entry = {"code": safe_code(code, "command-failed"), "scope": safe_code(scope, "scan")}
    if entry not in errors:
        errors.append(entry)


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fatal_envelope(now: object, code: str, scope: str) -> dict[str, Any]:
    timestamp = parse_time(now)
    return {
        "errors": [{"code": safe_code(code, "unexpected-error"), "scope": safe_code(scope, "scan")}],
        "findings": [],
        "generated_at": format_time(timestamp) if timestamp else None,
        "schema": SCHEMA,
        "self_heal_candidates": [],
        "status": "fatal",
    }


def run_command(argv: list[str], repo_root: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            cwd=repo_root,
            text=True,
            check=False,
        )
    except OSError:
        return None


def command_json(argv: list[str], repo_root: Path) -> tuple[object | None, str | None]:
    result = run_command(argv, repo_root)
    if result is None or result.returncode != 0:
        return None, "command-failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "invalid-json"


def list_records(
    repo_root: Path,
    label: str,
    *,
    status: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    argv = ["bd", "-C", str(repo_root), "list"]
    if status:
        argv.append(f"--status={status}")
    argv.extend(["--label", label, "--json", "--limit", "0"])
    payload, error = command_json(argv, repo_root)
    if error:
        return None, error
    if not isinstance(payload, list):
        return None, "invalid-json"
    records: list[dict[str, Any]] = []
    for item in payload:
        records.append(item if isinstance(item, dict) else {})
    return records, None


def labels(record: dict[str, Any]) -> set[str]:
    raw = record.get("labels")
    if not isinstance(raw, list):
        return set()
    return {value for value in raw if isinstance(value, str)}


def record_id(record: dict[str, Any]) -> str | None:
    candidate = record.get("id")
    return candidate if isinstance(candidate, str) and BEAD_ID_RE.fullmatch(candidate) else None


def requested_record(payload: object, issue_id: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        return None, "invalid-show-response"
    record = payload[0]
    if record_id(record) != issue_id:
        return None, "mismatched-record-id"
    return record, None


def validate_inventory_records(
    records: list[dict[str, Any]] | None,
    *,
    required_labels: frozenset[str],
    required_status: str | None,
    scope: str,
    errors: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], bool]:
    """Retain only uniquely identified records matching the queried inventory."""
    if records is None:
        return [], False
    valid_records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    complete = True
    for record in records:
        issue_id = record_id(record)
        status = record.get("status")
        if (
            issue_id is None
            or not required_labels.issubset(labels(record))
            or not isinstance(status, str)
            or status not in BEAD_STATUSES
            or (required_status is not None and status != required_status)
        ):
            report_error(errors, "invalid-record", scope)
            complete = False
            continue
        if issue_id in seen_ids:
            report_error(errors, "duplicate-record", scope)
            complete = False
            continue
        seen_ids.add(issue_id)
        valid_records.append(record)
    return valid_records, complete


def valid_pr_number(value: object) -> bool:
    return type(value) is int and value > 0


def pr_number_from_ref(value: object) -> int | None:
    match = re.fullmatch(r"gh-pr:([0-9]+)", value if isinstance(value, str) else "")
    return int(match.group(1)) if match else None


def load_original(record: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(record.get("external_ref"), str):
        return record, None
    issue_id = record_id(record)
    if issue_id is None:
        return None, "invalid-record"
    payload, error = command_json(["bd", "show", issue_id, "--json"], repo_root)
    if error:
        return None, error
    return requested_record(payload, issue_id)


REPO_SLUGS: dict[Path, tuple[str | None, str | None]] = {}


def repo_slug(repo_root: Path) -> tuple[str | None, str | None]:
    """The scanned repository's OWNER/REPO, read once per root; every PR read is scoped to it."""
    if repo_root not in REPO_SLUGS:
        result = run_command(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], repo_root)
        slug = result.stdout.strip() if result is not None and result.returncode == 0 else ""
        REPO_SLUGS[repo_root] = (slug, None) if re.fullmatch(r"[^/\s]+/[^/\s]+", slug) else (None, "repo-unresolved")
    return REPO_SLUGS[repo_root]


def resolver_error(result: subprocess.CompletedProcess[str] | None) -> str:
    if result is None:
        return "resolver-unavailable"
    for raw in (result.stdout, result.stderr):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return safe_code(payload.get("error_code"), "command-failed")
    return "command-failed"


def resolve_review_task(
    review_id: str,
    resolver: Path,
    repo_root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    if not resolver.is_file():
        return None, "resolver-unavailable"
    slug, error = repo_slug(repo_root)
    if error:
        return None, error
    # The scan targets repo_root's repository by construction, so name it; the
    # resolver still fails repo-mismatch if the review bead records another.
    result = run_command([sys.executable, str(resolver), "--issue-id", review_id, "--repo", slug], repo_root)
    if result is None or result.returncode != 0:
        return None, resolver_error(result)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "invalid-json"
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None, safe_code(payload.get("error_code") if isinstance(payload, dict) else None, "command-failed")
    if payload.get("issue_id") != review_id:
        return None, "mismatched-review-id"
    original_id = payload.get("original_id")
    pr_number = payload.get("pr_number")
    if not isinstance(original_id, str) or not BEAD_ID_RE.fullmatch(original_id):
        return None, "invalid-original-id"
    if not valid_pr_number(pr_number):
        return None, "invalid-pr-number"
    context = {"original_id": original_id, "pr_number": pr_number}
    members = payload.get("delivery_members")
    if members is not None:
        if not isinstance(members, list) or not members or any(not isinstance(member, str) or not BEAD_ID_RE.fullmatch(member) for member in members) or original_id not in members or len(set(members)) != len(members):
            return None, "invalid-delivery-package"
        context["delivery_members"] = members
    return context, None


def view_pr(
    pr_number: int,
    cache: dict[int, tuple[dict[str, Any] | None, str | None]],
    repo_root: Path,
    *,
    with_branch: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    if pr_number not in cache:
        fields = "number,state,mergedAt,createdAt,headRefOid" + (",headRefName" if with_branch else "")
        slug, error = repo_slug(repo_root)
        if not error:
            payload, error = command_json(
                ["gh", "pr", "view", str(pr_number), "--repo", slug, "--json", fields],
                repo_root,
            )
        if error:
            cache[pr_number] = (None, error)
        elif not isinstance(payload, dict) or not isinstance(payload.get("state"), str):
            cache[pr_number] = (None, "invalid-pr-payload")
        elif not valid_pr_number(payload.get("number")) or payload["number"] != pr_number:
            cache[pr_number] = (None, "invalid-pr-number")
        elif payload["state"] not in PR_STATES:
            cache[pr_number] = (None, "invalid-pr-state")
        elif not isinstance(payload.get("headRefOid"), str) or not PR_HEAD_RE.fullmatch(payload["headRefOid"]):
            cache[pr_number] = (None, "invalid-pr-head")
        else:
            created_at = parse_time(payload.get("createdAt"))
            if created_at is None:
                cache[pr_number] = (None, "invalid-pr-created-at")
            else:
                payload["createdAt"] = format_time(created_at)
                cache[pr_number] = (payload, None)
    return cache[pr_number]


def cooldown_until(pr: dict[str, Any], now: datetime) -> str | None:
    created_at = parse_time(pr.get("createdAt"))
    return format_time(created_at + COOLDOWN) if created_at else None


def task_finding(
    *,
    review_id: str,
    original_id: str | None,
    pr_number: int | None,
    context_status: str,
    canonical_review_id: str | None,
    duplicate_of: str | None,
    pr_state: str | None,
    cooldown: str | None,
    recommendation: str,
) -> dict[str, Any]:
    return {
        "canonical_review_id": canonical_review_id,
        "context_status": context_status,
        "cooldown_until": cooldown,
        "duplicate_of": duplicate_of,
        "kind": "review-task",
        "original_id": original_id,
        "pr_number": pr_number,
        "pr_state": pr_state,
        "recommendation": recommendation,
        "review_id": review_id,
    }


def original_finding(
    *,
    original_id: str,
    pr_number: int | None,
    context_status: str,
    canonical_review_id: str | None,
    pr_state: str | None,
    cooldown: str | None,
    recommendation: str,
) -> dict[str, Any]:
    return {
        "canonical_review_id": canonical_review_id,
        "context_status": context_status,
        "cooldown_until": cooldown,
        "duplicate_of": None,
        "kind": "original",
        "original_id": original_id,
        "pr_number": pr_number,
        "pr_state": pr_state,
        "recommendation": recommendation,
        "review_id": None,
    }


def recommendation_for_open(cooldown: str | None, now: datetime) -> str:
    end = parse_time(cooldown)
    if end is None:
        return "manual-triage"
    return "wait-for-cooldown" if now < end else "dispatch-canonical-review"


def recommendation_for_pr_error(error: str | None) -> str:
    return "skip-command-failure" if error == "command-failed" else "manual-triage"


def manualize_incomplete_collection(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    collection_complete: bool,
    preserve_lone_pr_metadata_skip: bool,
) -> None:
    """Ensure incomplete Step-0 evidence cannot leave an actionable recommendation."""
    if collection_complete:
        return
    for finding in findings:
        if (
            preserve_lone_pr_metadata_skip
            and finding.get("recommendation") == "skip-command-failure"
        ):
            continue
        finding["recommendation"] = "manual-triage"
    for candidate in candidates:
        candidate["recommendation"] = "manual-triage"


def finding_sort_key(finding: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(finding.get("original_id") or ""),
        str(finding.get("review_id") or ""),
        int(finding.get("pr_number") or 0),
        str(finding.get("kind") or ""),
    )


def legacy_branch_original(
    branch_id: str,
    number: object,
    canonical_by_key: dict[tuple[str, int], str],
    original_contexts: dict[str, tuple[dict[str, Any] | None, int | None, str | None]],
) -> str | None:
    """Recognize a legacy suffix only around an already established anchor."""
    if not valid_pr_number(number):
        return None
    anchors = {original_id for original_id, pr in canonical_by_key if pr == number}
    if len(anchors) != 1:
        return None
    original_id = next(iter(anchors))
    if any(
        error is None and (
            (other_id == original_id and pr != number)
            or (other_id != original_id and pr == number)
        )
        for other_id, (_, pr, error) in original_contexts.items()
    ):
        return None
    # Attached suffixes belong only to an explicitly bound dotted child.
    if re.search(r"\.[0-9]+\Z", original_id) and re.fullmatch(
        re.escape(original_id) + r"[a-z][a-z0-9]*", branch_id,
    ):
        return original_id
    prefix = f"{original_id}-"
    if not branch_id.startswith(prefix):
        return None
    suffix = branch_id[len(prefix):]
    if re.fullmatch(r"[0-9]{8}", suffix):
        try:
            datetime.strptime(suffix, "%Y%m%d")
        except ValueError:
            return None
    elif not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", suffix):
        return None
    return original_id


def author_activity_evidence(
    original: dict[str, Any], number: int, branch: str, repo_root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Bind an unhanded-off author to one stable current PR/branch/head."""
    before, error = view_pr(number, {}, repo_root, with_branch=True)
    if error:
        return None, error
    if before["state"] != "OPEN" or before.get("headRefName") != branch:
        return None, "author-pr-association-changed"
    # Never reuse a cached head to certify the live association. CI status is
    # diagnostic: it cannot turn an active author into an unstarted review job.
    after, error = view_pr(number, {}, repo_root, with_branch=True)
    if error:
        return None, error
    if (after["state"] != "OPEN" or after.get("headRefName") != branch
            or after["headRefOid"] != before["headRefOid"]):
        return None, "author-pr-head-changed"
    return {"assignee": original["assignee"], "branch": branch,
            "head_sha": before["headRefOid"]}, None


def verified_supporting_prs(
    prs: list[object],
    canonical_by_key: dict[tuple[str, int], str],
    original_contexts: dict[str, tuple[dict[str, Any] | None, int | None, str | None]],
    pr_cache: dict[int, tuple[dict[str, Any] | None, str | None]],
    repo_root: Path,
    errors: list[dict[str, str]],
) -> set[int]:
    """Find unbound slice PRs whose exact base links reach a canonical open head."""
    if not any(isinstance(pr, dict) and isinstance(pr.get("headRefName"), str)
               and STACK_SLICE_RE.fullmatch(pr["headRefName"]) for pr in prs):
        return set()
    custody, error = active_pr_associations(repo_root, prs)
    if error:
        report_error(errors, error, "stack-custody")
        return set()
    rows = [pr for pr in prs if isinstance(pr, dict)]
    number_count = Counter(pr["number"] for pr in rows if valid_pr_number(pr.get("number")))
    head_count = Counter(pr["headRefName"] for pr in rows if isinstance(pr.get("headRefName"), str))
    by_head = {
        pr["headRefName"]: pr for pr in rows
        if valid_pr_number(pr.get("number")) and number_count[pr["number"]] == 1
        and isinstance(pr.get("headRefName"), str) and head_count[pr["headRefName"]] == 1
        and isinstance(pr.get("headRefOid"), str) and PR_HEAD_RE.fullmatch(pr["headRefOid"])
        and pr.get("isCrossRepository") is False and parse_time(pr.get("createdAt"))
    }
    bindings = set(canonical_by_key) | {
        (original, pr) for original, (_, pr, error) in original_contexts.items()
        if error is None and pr is not None
    }
    bindings.update(custody)
    bound_numbers = {number for _, number in bindings}
    roots = {}
    for original, number in canonical_by_key:
        branch = f"agent/{original}"
        row = by_head.get(branch)
        live, error = pr_cache.get(number, (None, None))
        if (
            row and row["number"] == number and error is None and live
            and live["state"] == "OPEN" and live["headRefOid"] == row["headRefOid"]
            and live.get("headRefName", branch) == branch
            and custody.get((original, number)) is True
            and sum(bead == original for bead, _ in bindings) == 1
            and sum(pr == number for _, pr in bindings) == 1
        ):
            roots[branch] = row

    slices = {}
    for branch, row in by_head.items():
        match = STACK_SLICE_RE.fullmatch(branch)
        if match and match[1] in roots and row["number"] not in bound_numbers:
            slices[branch] = (match[1], row)

    # Re-read the actual PR identities, including parents and the root ref, on
    # both sides of the custody recheck. A cached OID or ancestry is insufficient.
    related = {**roots, **{branch: row for branch, (_, row) in slices.items()}}
    stable = set(related)
    slug, error = repo_slug(repo_root)
    for pass_number in range(2):
        for branch, row in related.items():
            live, error = command_json(
                ["gh", "pr", "view", str(row["number"]), "--repo", slug,
                 "--json", STACK_PR_FIELDS + ",state"], repo_root,
            )
            if (error or not isinstance(live, dict) or not valid_pr_number(live.get("number"))
                    or live.get("isCrossRepository") is not False or live.get("state") != "OPEN"
                    or any(live.get(key) != row.get(key) for key in STACK_PR_FIELDS.split(","))):
                stable.discard(branch)
                report_error(errors, error or "stack-pr-changed", "stack-identity")
        if pass_number == 0:
            after, error = active_pr_associations(repo_root, prs)
            if error or after != custody:
                report_error(errors, error or "stack-custody-changed", "stack-custody")
                return set()

    verified: set[int] = set()
    for branch, (root, row) in slices.items():
        if branch not in stable or root not in stable:
            continue
        visited = {branch}
        current = row
        while isinstance(base := current.get("baseRefName"), str) and base not in visited:
            parent = by_head.get(base)
            if parent is None or base not in stable or current.get("baseRefOid") != parent["headRefOid"]:
                break
            if base == root:
                verified.add(row["number"])
                break
            if base not in slices or slices[base][0] != root:
                break
            visited.add(base)
            current = parent
    return verified


def active_pr_associations(
    repo_root: Path, prs: list[object],
) -> tuple[dict[tuple[str, int], bool], str | None]:
    """Authoritative PR custody across every active status, without label filters.

    Include the bd list categories hidden by default. Unlimited list output (or
    its nonzero failure) is the completeness boundary; never infer absence from
    the review queue. Accepted active delivery branches also establish custody.
    True means an exact canonical gh-pr:N binding; other custody evidence can
    obstruct a stack but cannot establish its root.
    """
    records, error = command_json(
        ["bd", "-C", str(repo_root), "list", "--status=open,in_progress,blocked,deferred",
         "--limit", "0", "--json", "--include-ephemeral", "--include-infra",
         "--include-templates", "--include-gates"], repo_root,
    )
    if error:
        return {}, error
    if not isinstance(records, list):
        return {}, "invalid-association-inventory"
    bindings: dict[tuple[str, int], bool] = {}
    seen: set[str] = set()
    for row in records:
        if not isinstance(row, dict):
            return {}, "invalid-association-record"
        issue_id = record_id(row)
        if (issue_id is None or issue_id in seen
                or row.get("status") not in {"open", "in_progress", "blocked", "deferred"}
                or (row.get("labels") is not None and not isinstance(row["labels"], list))
                or any(not isinstance(label, str) for label in (row.get("labels") or []))):
            return {}, "invalid-association-record"
        seen.add(issue_id)
        if "pr-review-task" in labels(row):
            if "pr-review" not in labels(row):
                return {}, "invalid-association-record"
            continue  # A review's PR reference is not source ownership.
        reference = row.get("external_ref")
        number = pr_number_from_ref(reference)
        # Old gh-N references can obstruct custody, never establish canonical
        # review wiring or authorize historical branch-name inference.
        legacy_ref = re.fullmatch(r"gh-([0-9]+)", reference) if isinstance(reference, str) else None
        if number is None and legacy_ref:
            number = int(legacy_ref[1])
        if reference not in (None, "") and (number is None or not valid_pr_number(number)):
            return {}, "unproven-pr-association"
        if number is not None:
            bindings[(issue_id, number)] = reference == f"gh-pr:{number}"
        try:
            delivery = bead_metadata(row).get("delivery")
            if delivery is not None:
                delivery = validate_delivery(delivery)
                if delivery["anchor"] != issue_id:
                    return {}, "invalid-association-record"
                if delivery.get("state", "active") == "active":
                    branch = delivery.get("branch", f"agent/{issue_id}")
                    for pr in prs:
                        if isinstance(pr, dict) and pr.get("headRefName") == branch:
                            if not valid_pr_number(pr.get("number")):
                                return {}, "invalid-association-record"
                            bindings.setdefault((issue_id, pr["number"]), False)
        except ValueError:
            return {}, "invalid-association-record"
    return bindings, None


def self_heal_candidates(
    *,
    repo_root: Path,
    now: datetime,
    canonical_by_key: dict[tuple[str, int], str],
    original_contexts: dict[str, tuple[dict[str, Any] | None, int | None, str | None]],
    pr_cache: dict[int, tuple[dict[str, Any] | None, str | None]],
    task_lookup_complete: bool,
    errors: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], bool]:
    """Discover open-PR candidates and report whether their evidence is complete."""
    prefix_payload, prefix_error = command_json(
        ["bd", "-C", str(repo_root), "config", "get", "issue_prefix", "--json"],
        repo_root,
    )
    if prefix_error:
        report_error(errors, prefix_error, "issue-prefix")
        return [], False
    prefix = prefix_payload.get("value") if isinstance(prefix_payload, dict) else None
    if (
        not isinstance(prefix_payload, dict)
        or prefix_payload.get("key") != "issue_prefix"
        or not isinstance(prefix, str)
        or not ISSUE_PREFIX_RE.fullmatch(prefix)
    ):
        report_error(errors, "invalid-issue-prefix", "issue-prefix")
        return [], False

    slug, error = repo_slug(repo_root)
    if not error:
        payload, error = command_json(
            ["gh", "pr", "list", "--repo", slug, "--state", "open",
             "--limit", str(OPEN_PR_LIMIT), "--json", STACK_PR_FIELDS],
            repo_root,
        )
    if error:
        report_error(errors, error, "open-prs")
        return [], False
    if not isinstance(payload, list):
        report_error(errors, "invalid-json", "open-prs")
        return [], False

    candidates: list[dict[str, Any]] = []
    discovery_complete = len(payload) < OPEN_PR_LIMIT
    if not discovery_complete:
        report_error(errors, "open-pr-limit-reached", "open-prs")
    rows = [row for row in payload if isinstance(row, dict)]
    numbers = [row["number"] for row in rows if valid_pr_number(row.get("number"))]
    heads = [row["headRefName"] for row in rows if isinstance(row.get("headRefName"), str)]
    if len(set(numbers)) != len(numbers) or len(set(heads)) != len(heads):
        report_error(errors, "duplicate-pr-identity", "open-prs")
        discovery_complete = False
    supporting_prs = (
        verified_supporting_prs(payload, canonical_by_key, original_contexts, pr_cache, repo_root, errors)
        if task_lookup_complete and discovery_complete else set()
    )
    if any(error["scope"] in {"stack-custody", "stack-identity"} for error in errors):
        discovery_complete = False
    for item in payload:
        if not isinstance(item, dict):
            report_error(errors, "invalid-record", "open-prs")
            discovery_complete = False
            continue
        branch = item.get("headRefName")
        number = item.get("number")
        if not isinstance(branch, str) or not branch.startswith("agent/"):
            continue
        original_id = branch.removeprefix("agent/")
        if not original_id.startswith(f"{prefix}-"):
            continue
        legacy_original = legacy_branch_original(
            original_id, number, canonical_by_key, original_contexts,
        )
        if legacy_original is not None:
            original_id = legacy_original
        else:
            # Unassociated descriptive names are supporting branches, not IDs.
            # Never let that non-inference hide a known PR/anchor conflict.
            known_originals = set(original_contexts) | {key[0] for key in canonical_by_key}
            known_pr = valid_pr_number(number) and (
                any(pr == number for _, pr in canonical_by_key)
                or any(pr == number for _, pr, _ in original_contexts.values())
            )
            related_anchor = any(
                original_id == anchor or original_id.startswith(f"{anchor}-")
                for anchor in known_originals
            )
            if valid_pr_number(number) and number in supporting_prs:
                continue
            if not known_pr and not related_anchor and "-" in original_id[len(prefix) + 1:]:
                continue
        if not BEAD_ID_RE.fullmatch(original_id):
            report_error(errors, "invalid-record", "open-prs")
            discovery_complete = False
            continue
        if not valid_pr_number(number):
            report_error(errors, "invalid-pr-number", "open-prs")
            discovery_complete = False
            candidates.append(
                {
                    "canonical_review_id": None,
                    "context_status": "invalid-pr-number",
                    "cooldown_until": None,
                    "original_id": original_id,
                    "pr_number": None,
                    "recommendation": "manual-triage",
                }
            )
            continue
        created_at = parse_time(item.get("createdAt"))
        if created_at is None:
            report_error(errors, "invalid-pr-created-at", "open-prs")
            discovery_complete = False
            candidates.append(
                {
                    "canonical_review_id": None,
                    "context_status": "invalid-pr-created-at",
                    "cooldown_until": None,
                    "original_id": original_id,
                    "pr_number": number,
                    "recommendation": "manual-triage",
                }
            )
            continue
        original_payload, original_error = command_json(["bd", "show", original_id, "--json"], repo_root)
        if original_error is None:
            original, identity_error = requested_record(original_payload, original_id)
        else:
            original, identity_error = None, original_error
        if (
            original is not None
            and legacy_original is not None
            and original.get("external_ref") != f"gh-pr:{number}"
        ):
            original, identity_error = None, "conflicting-original-pr"
        if original is None:
            report_error(errors, identity_error or "invalid-json", "self-heal")
            discovery_complete = False
            candidates.append(
                {
                    "canonical_review_id": None,
                    "context_status": identity_error or "invalid-json",
                    "cooldown_until": None,
                    "original_id": original_id,
                    "pr_number": number,
                    "recommendation": "manual-triage",
                }
            )
            continue

        canonical = canonical_by_key.get((original_id, number))
        original_labels = labels(original)
        correct_original = (
            original.get("status") == "blocked"
            and original.get("external_ref") == f"gh-pr:{number}"
            and "pr-review" in original_labels
        )
        if not task_lookup_complete:
            recommendation = "manual-triage"
        elif correct_original and canonical:
            recommendation = "review-wiring-current"
        elif correct_original:
            recommendation = "self-heal-review-wiring"
        elif canonical:
            recommendation = "self-heal-original-wiring"
        else:
            recommendation = "self-heal-original-and-review-wiring"

        candidate = {
            "canonical_review_id": canonical,
            "context_status": "resolved",
            "cooldown_until": format_time(created_at + COOLDOWN),
            "original_id": original_id,
            "pr_number": number,
            "recommendation": recommendation,
        }
        # Publication alone is not an independent-review handoff. Keep the
        # wiring advisory, while allowing admission to recognize proven work.
        if (task_lookup_complete and canonical is None
                and original.get("status") == "in_progress"
                and isinstance(original.get("assignee"), str)
                and original["assignee"].startswith("coordinator:")
                and not original_labels & {"pr-review", "pr-review-task", "review-correction"}
                and original.get("external_ref") in (None, "", f"gh-pr:{number}")
                and branch == f"agent/{original_id}"
                and sum(isinstance(pr, dict) and pr.get("headRefName") == branch for pr in payload) == 1):
            evidence, error = author_activity_evidence(original, number, branch, repo_root)
            if error:
                report_error(errors, error, "author-activity")
                discovery_complete = False
            elif evidence:
                candidate.update(author_activity=evidence, head_sha=evidence["head_sha"], pr_state="OPEN")
        candidates.append(candidate)
    return (
        sorted(candidates, key=lambda item: (str(item["original_id"]), int(item["pr_number"] or 0))),
        discovery_complete,
    )


def normalize(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    repo_root = args.repo_root.resolve()
    now = parse_time(args.now)
    if now is None:
        return (
            {
                "errors": [{"code": "invalid-now", "scope": "bootstrap"}],
                "findings": [],
                "generated_at": None,
                "schema": SCHEMA,
                "self_heal_candidates": [],
                "status": "fatal",
            },
            1,
        )
    generated_at = format_time(now)
    if not repo_root.is_dir():
        return (
            {
                "errors": [{"code": "invalid-repo-root", "scope": "bootstrap"}],
                "findings": [],
                "generated_at": generated_at,
                "schema": SCHEMA,
                "self_heal_candidates": [],
                "status": "fatal",
            },
            1,
        )

    errors: list[dict[str, str]] = []
    pr_review_records, original_error = list_records(repo_root, "pr-review", status="blocked")
    task_records, task_error = list_records(repo_root, "pr-review-task")
    if original_error:
        report_error(errors, original_error, "blocked-pr-review")
    if task_error:
        report_error(errors, task_error, "blocked-pr-review-task")
    if pr_review_records is None and task_records is None:
        return (
            {
                "errors": sorted(errors, key=lambda item: (item["scope"], item["code"])),
                "findings": [],
                "generated_at": generated_at,
                "schema": SCHEMA,
                "self_heal_candidates": [],
                "status": "fatal",
            },
            1,
        )

    validated_pr_review_records, pr_review_inventory_complete = validate_inventory_records(
        pr_review_records,
        required_labels=frozenset({"pr-review"}),
        required_status="blocked",
        scope="blocked-pr-review",
        errors=errors,
    )
    # Closed/deferred review history is not part of active admission. Older parked
    # records may lack today's paired labels; retain validation of every
    # non-terminal or malformed record instead of retrofitting closed history.
    if task_records is not None:
        task_records = [
            record for record in task_records
            if not isinstance(record, dict) or record.get("status") not in ("closed", "deferred")
        ]
    validated_task_records, task_inventory_complete = validate_inventory_records(
        task_records,
        required_labels=frozenset({"pr-review", "pr-review-task"}),
        required_status=None,
        scope="blocked-pr-review-task",
        errors=errors,
    )
    inventory_complete = (
        original_error is None
        and task_error is None
        and pr_review_inventory_complete
        and task_inventory_complete
    )
    task_lookup_complete = inventory_complete
    records_by_id: dict[str, dict[str, Any]] = {}
    for record in validated_pr_review_records:
        issue_id = record_id(record)
        assert issue_id is not None
        records_by_id[issue_id] = record
    for record in validated_task_records:
        issue_id = record_id(record)
        assert issue_id is not None
        existing = records_by_id.get(issue_id)
        if existing is not None and existing != record:
            report_error(errors, "conflicting-record", "pr-review-inventory")
            inventory_complete = False
            task_lookup_complete = False
            continue
        records_by_id.setdefault(issue_id, record)

    originals: dict[str, dict[str, Any]] = {}
    tasks: dict[str, dict[str, Any]] = {}
    for issue_id, record in records_by_id.items():
        if "pr-review-task" in labels(record):
            status = record.get("status")
            if status == "closed":
                continue
            if not isinstance(status, str) or status not in {"open", "blocked", "in_progress"}:
                report_error(errors, "invalid-bead-status", "review-context")
                task_lookup_complete = False
                continue
            tasks[issue_id] = record
        else:
            originals[issue_id] = record

    original_contexts: dict[str, tuple[dict[str, Any] | None, int | None, str | None]] = {}
    original_contexts_complete = True
    for original_id in sorted(originals):
        original, error = load_original(originals[original_id], repo_root)
        if original is None:
            context_error = error or "invalid-json"
            report_error(errors, context_error, "original-context")
            original_contexts[original_id] = (None, None, context_error)
            original_contexts_complete = False
            continue
        pr_number = pr_number_from_ref(original.get("external_ref"))
        if pr_number is None:
            report_error(errors, "missing-pr-number", "original-context")
            original_contexts[original_id] = (original, None, "missing-pr-number")
            original_contexts_complete = False
            continue
        if not valid_pr_number(pr_number):
            report_error(errors, "invalid-pr-number", "original-context")
            original_contexts[original_id] = (original, None, "invalid-pr-number")
            original_contexts_complete = False
            continue
        original_contexts[original_id] = (original, pr_number, None)

    task_contexts: dict[str, dict[str, Any] | None] = {}
    task_context_errors: dict[str, str] = {}
    for review_id in sorted(tasks):
        context, error = resolve_review_task(review_id, args.resolver, repo_root)
        task_contexts[review_id] = context
        if error:
            task_context_errors[review_id] = error
            report_error(errors, error, "review-context")
            task_lookup_complete = False

    active_contexts_complete = not task_context_errors and all(
        context is not None for context in task_contexts.values()
    )
    relation_error: str | None = None
    relation_graph_complete = False
    if active_contexts_complete:
        relation_error = review_relation_error(
            [(review_id, context["original_id"]) for review_id, context in task_contexts.items()]
        )
        relation_graph_complete = relation_error is None
        if relation_error:
            report_error(errors, relation_error, "review-context")
            task_lookup_complete = False

    chronology_by_key: dict[tuple[str, int], list[tuple[datetime, str]]] = {}
    invalid_task_keys: set[tuple[str, int]] = set()
    tasks_by_key: dict[tuple[str, int], list[str]] = {}
    if active_contexts_complete and relation_graph_complete:
        for review_id, context in task_contexts.items():
            if context is None:
                continue
            key = (context["original_id"], context["pr_number"])
            tasks_by_key.setdefault(key, []).append(review_id)
        for key, review_ids in tasks_by_key.items():
            chronology: list[tuple[datetime, str]] = []
            for review_id in review_ids:
                created_at = parse_time(tasks[review_id].get("created_at"))
                if created_at is None:
                    invalid_task_keys.add(key)
                    report_error(errors, "invalid-created-at", "review-context")
                    task_lookup_complete = False
                    break
                chronology.append((created_at, review_id))
            if key not in invalid_task_keys:
                chronology_by_key[key] = chronology

    chronology_complete = (
        active_contexts_complete and relation_graph_complete and not invalid_task_keys
    )
    structural_collection_complete = (
        inventory_complete
        and task_lookup_complete
        and original_contexts_complete
        and active_contexts_complete
        and relation_graph_complete
        and chronology_complete
    )

    pr_cache: dict[int, tuple[dict[str, Any] | None, str | None]] = {}
    pr_numbers = {
        pr_number
        for _, pr_number, context_error in original_contexts.values()
        if pr_number is not None and context_error is None
    }
    pr_numbers.update(
        context["pr_number"] for context in task_contexts.values() if context is not None
    )
    pr_metadata_errors: dict[int, str] = {}
    for pr_number in sorted(pr_numbers):
        _, pr_error = view_pr(pr_number, pr_cache, repo_root)
        if pr_error:
            pr_metadata_errors[pr_number] = pr_error
            report_error(errors, pr_error, "pr-state")
    pr_metadata_complete = not pr_metadata_errors
    pre_discovery_collection_complete = structural_collection_complete and pr_metadata_complete
    # The explicitly allowed singleton command failure is a report-only skip, never an action.
    provisional_lone_pr_metadata_skip = (
        structural_collection_complete
        and len(pr_numbers) == 1
        and len(pr_metadata_errors) == 1
        and all(error == "command-failed" for error in pr_metadata_errors.values())
    )
    canonical_by_key = (
        {
            key: min(chronology, key=lambda item: (item[0], item[1]))[1]
            for key, chronology in chronology_by_key.items()
        }
        if pre_discovery_collection_complete
        else {}
    )

    findings: list[dict[str, Any]] = []

    for original_id in sorted(originals):
        original, pr_number, context_error = original_contexts[original_id]
        if original is None:
            findings.append(
                original_finding(
                    original_id=original_id,
                    pr_number=None,
                    context_status=context_error or "invalid-json",
                    canonical_review_id=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="manual-triage",
                )
            )
            continue
        if pr_number is None:
            findings.append(
                original_finding(
                    original_id=original_id,
                    pr_number=None,
                    context_status=context_error or "missing-pr-number",
                    canonical_review_id=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="manual-triage",
                )
            )
            continue
        canonical = canonical_by_key.get((original_id, pr_number))
        pr, pr_error = view_pr(pr_number, pr_cache, repo_root)
        if pr is None:
            report_error(errors, pr_error or "command-failed", "pr-state")
            findings.append(
                original_finding(
                    original_id=original_id,
                    pr_number=pr_number,
                    context_status=pr_error or "command-failed",
                    canonical_review_id=canonical,
                    pr_state=None,
                    cooldown=None,
                    recommendation=(
                        recommendation_for_pr_error(pr_error)
                        if provisional_lone_pr_metadata_skip
                        else "manual-triage"
                    ),
                )
            )
            continue
        state = pr["state"]
        cooldown = cooldown_until(pr, now)
        if not pre_discovery_collection_complete:
            recommendation = "manual-triage"
        elif state == "MERGED":
            recommendation = "close-original-and-reviews"
        elif state == "CLOSED":
            recommendation = "reopen-original-for-retriage"
        elif state == "OPEN" and canonical is None:
            recommendation = "manual-triage" if not task_lookup_complete else "self-heal-review-wiring"
        elif state == "OPEN":
            recommendation = recommendation_for_open(cooldown, now)
        else:
            recommendation = "manual-triage"
        findings.append(
            original_finding(
                original_id=original_id,
                pr_number=pr_number,
                context_status="resolved",
                canonical_review_id=canonical,
                pr_state=state,
                cooldown=cooldown,
                recommendation=recommendation,
            )
        )

    for review_id in sorted(tasks):
        context = task_contexts[review_id]
        if context is None:
            context_error = task_context_errors.get(review_id, "invalid-review-context")
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=None,
                    pr_number=None,
                    context_status=context_error,
                    canonical_review_id=None,
                    duplicate_of=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="skip-command-failure" if context_error == "command-failed" else "manual-triage",
                )
            )
            continue
        if not active_contexts_complete:
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=context["original_id"],
                    pr_number=context["pr_number"],
                    context_status="incomplete-review-context",
                    canonical_review_id=None,
                    duplicate_of=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="manual-triage",
                )
            )
            continue
        if not relation_graph_complete:
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=context["original_id"],
                    pr_number=context["pr_number"],
                    context_status=relation_error or "invalid-review-relation",
                    canonical_review_id=None,
                    duplicate_of=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="manual-triage",
                )
            )
            continue
        original_id = context["original_id"]
        pr_number = context["pr_number"]
        key = (original_id, pr_number)
        if pr_number in pr_metadata_errors:
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=original_id,
                    pr_number=pr_number,
                    context_status=pr_metadata_errors[pr_number],
                    canonical_review_id=None,
                    duplicate_of=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation=(
                        "skip-command-failure"
                        if provisional_lone_pr_metadata_skip
                        else "manual-triage"
                    ),
                )
            )
            continue
        canonical = canonical_by_key.get(key)
        if key in invalid_task_keys or not pre_discovery_collection_complete or canonical is None:
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=original_id,
                    pr_number=pr_number,
                    context_status=(
                        "invalid-created-at"
                        if key in invalid_task_keys
                        else "incomplete-review-collection"
                        if not pre_discovery_collection_complete
                        else "missing-canonical-review"
                    ),
                    canonical_review_id=None,
                    duplicate_of=None,
                    pr_state=None,
                    cooldown=None,
                    recommendation="manual-triage",
                )
            )
            continue
        duplicate_of = canonical if review_id != canonical else None
        pr, pr_error = view_pr(pr_number, pr_cache, repo_root)
        if pr is None:
            report_error(errors, pr_error or "command-failed", "pr-state")
            findings.append(
                task_finding(
                    review_id=review_id,
                    original_id=original_id,
                    pr_number=pr_number,
                    context_status=pr_error or "command-failed",
                    canonical_review_id=canonical,
                    duplicate_of=duplicate_of,
                    pr_state=None,
                    cooldown=None,
                    recommendation=recommendation_for_pr_error(pr_error),
                )
            )
            continue
        state = pr["state"]
        cooldown = cooldown_until(pr, now)
        if duplicate_of:
            recommendation = "dedupe-review-task"
        elif state == "MERGED":
            recommendation = "close-review-and-original"
        elif state == "CLOSED":
            recommendation = "close-review-reopen-original"
        elif state == "OPEN":
            recommendation = recommendation_for_open(cooldown, now)
        else:
            recommendation = "manual-triage"
        findings.append(
            task_finding(
                review_id=review_id,
                original_id=original_id,
                pr_number=pr_number,
                context_status="resolved",
                canonical_review_id=canonical,
                duplicate_of=duplicate_of,
                pr_state=state,
                cooldown=cooldown,
                recommendation=recommendation,
            )
        )

    for finding in findings:
        pr, _ = pr_cache.get(finding.get("pr_number"), (None, None))
        finding["head_sha"] = pr.get("headRefOid") if pr else None

    # A merged package is landing evidence, not blanket member acceptance.
    # Keep one canonical review task while the lifecycle owner reconciles the
    # package's per-member closure plan.
    packages_complete = True
    for finding in findings:
        original_id = finding.get("original_id")
        original = original_contexts.get(original_id, (None, None, None))[0]
        try:
            metadata = bead_metadata(original) if isinstance(original, dict) else {}
            delivery = metadata.get("delivery")
            if delivery is not None:
                delivery = validate_delivery(delivery)
        except ValueError:
            report_error(errors, "invalid-delivery-package", "packages")
            packages_complete = False
            continue
        context = task_contexts.get(finding.get("review_id")) or {}
        members = delivery.get("members") if isinstance(delivery, dict) else context.get("delivery_members")
        if isinstance(members, list) and (isinstance(delivery, dict) or len(members) > 1):
            finding["delivery_members"] = members
            if finding.get("pr_state") == "MERGED" and finding.get("recommendation") in {
                "close-original-and-reviews", "close-review-and-original"
            }:
                finding["recommendation"] = "reconcile-package-members"

    self_heal, open_pr_discovery_complete = self_heal_candidates(
        repo_root=repo_root,
        now=now,
        canonical_by_key=canonical_by_key,
        original_contexts=original_contexts,
        pr_cache=pr_cache,
        task_lookup_complete=pre_discovery_collection_complete,
        errors=errors,
    )
    collection_complete = pre_discovery_collection_complete and open_pr_discovery_complete and packages_complete
    preserve_lone_pr_metadata_skip = (
        provisional_lone_pr_metadata_skip and open_pr_discovery_complete
    )
    manualize_incomplete_collection(
        findings,
        self_heal,
        collection_complete=collection_complete,
        preserve_lone_pr_metadata_skip=preserve_lone_pr_metadata_skip,
    )
    findings.sort(key=finding_sort_key)
    errors.sort(key=lambda item: (item["scope"], item["code"]))
    status = "partial" if errors else "empty" if not findings and not self_heal else "success"
    return (
        {
            "errors": errors,
            "findings": findings,
            "generated_at": generated_at,
            "schema": SCHEMA,
            "self_heal_candidates": self_heal,
            "status": status,
        },
        0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report blocked PR-review state without mutating Beads or GitHub."
    )
    parser.add_argument("--repo-root", required=True, type=Path, help="Repository or rig root to inspect.")
    parser.add_argument(
        "--now",
        default=format_time(datetime.now(timezone.utc)),
        help="RFC 3339 UTC timestamp; supply it for byte-stable reports.",
    )
    parser.add_argument(
        "--resolver",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "beads-pr-reviewer-worker"
        / "scripts"
        / "resolve_review_context.py",
        help="Canonical read-only review-context resolver.",
    )
    args = parser.parse_args()
    try:
        payload, exit_code = normalize(args)
    except Exception:
        payload, exit_code = fatal_envelope(args.now, "unexpected-error", "scan"), 1
    emit(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
