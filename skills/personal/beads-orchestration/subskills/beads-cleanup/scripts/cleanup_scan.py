#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Emit a report-only cleanup scan for a Beads repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CANONICAL_COORDINATOR_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "beads-coordinator" / "scripts"
)
if str(CANONICAL_COORDINATOR_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CANONICAL_COORDINATOR_SCRIPTS))

from review_relation_graph import normalized_finding_relation_error  # noqa: E402


SCHEMA = "beads-cleanup-scan/v1"
NORMALIZER_SCHEMA = "beads-pr-review-normalization/v1"
STALL_THRESHOLD = timedelta(minutes=30)
BEAD_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.[0-9]+)*\Z")
SAFE_CODE_RE = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
BEAD_STATUSES = frozenset({"open", "in_progress", "blocked", "closed"})
PR_STATES = frozenset({"OPEN", "CLOSED", "MERGED"})
FINDING_RECOMMENDATIONS = frozenset(
    {
        "close-original-and-reviews",
        "close-review-and-original",
        "close-review-reopen-original",
        "dedupe-review-task",
        "dispatch-canonical-review",
        "manual-triage",
        "reopen-original-for-retriage",
        "self-heal-review-wiring",
        "skip-command-failure",
        "wait-for-cooldown",
    }
)
SELF_HEAL_RECOMMENDATIONS = frozenset(
    {
        "manual-triage",
        "review-wiring-current",
        "self-heal-original-and-review-wiring",
        "self-heal-original-wiring",
        "self-heal-review-wiring",
    }
)
HEARTBEAT_RE = re.compile(r"\[beads-heartbeat\].*?last_heartbeat_at=([^\s]+)", re.DOTALL)
WORKTREE_RE = re.compile(r"(?:^|/)parallel-agents/([a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.[0-9]+)*)\b")
AGENT_BRANCH_RE = re.compile(r"\bagent/([a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.[0-9]+)*)\b")


def emit(payload: dict[str, Any]) -> None:
    """Write only one compact JSON envelope; never forward subprocess output."""
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


def run_command(argv: list[str], repo_root: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, cwd=repo_root, text=True, check=False)
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


def record_id(record: dict[str, Any]) -> str | None:
    candidate = record.get("id")
    return candidate if isinstance(candidate, str) and BEAD_ID_RE.fullmatch(candidate) else None


def dependency_target_id(record: object) -> str | None:
    """Read one unambiguous current or legacy dependency target."""
    if not isinstance(record, dict):
        return None
    has_current = "id" in record
    has_legacy = "depends_on_id" in record
    if not has_current and not has_legacy:
        return None
    current = record.get("id")
    legacy = record.get("depends_on_id")
    if has_current and has_legacy and current != legacy:
        return None
    candidate = current if has_current else legacy
    return candidate if isinstance(candidate, str) and BEAD_ID_RE.fullmatch(candidate) else None


def labels(record: dict[str, Any]) -> set[str]:
    raw = record.get("labels")
    return {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()


def requested_record(payload: object, issue_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Require direct ``bd show`` evidence to be one record for that exact id."""
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        return None, "invalid-show-response"
    record = payload[0]
    if record_id(record) != issue_id:
        return None, "mismatched-record-id"
    return record, None


def list_records(
    repo_root: Path,
    *,
    status: str | None = None,
    label: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    argv = ["bd", "-C", str(repo_root), "list"]
    if status:
        argv.append(f"--status={status}")
    if label:
        argv.extend(["--label", label])
    argv.extend(["--json", "--limit", "0"])
    payload, error = command_json(argv, repo_root)
    if error:
        return None, error
    if not isinstance(payload, list):
        return None, "invalid-json"
    records: list[dict[str, Any]] = []
    for item in payload:
        records.append(item if isinstance(item, dict) else {})
    return records, None


def heartbeat_state(record: dict[str, Any], now: datetime) -> str:
    notes = record.get("notes")
    if not isinstance(notes, str):
        return "missing"
    match = HEARTBEAT_RE.search(notes)
    if match is None:
        return "missing"
    timestamp = parse_time(match.group(1))
    if timestamp is None:
        return "invalid"
    return "stale" if now - timestamp >= STALL_THRESHOLD else "fresh"


def remote_branch_exists(issue_id: str, repo_root: Path) -> tuple[bool | None, str | None]:
    result = run_command(["git", "ls-remote", "--heads", "origin", f"agent/{issue_id}"], repo_root)
    if result is None or result.returncode != 0:
        return None, "command-failed"
    return bool(result.stdout.strip()), None


def unpublished_work(worktree: Path, repo_root: Path) -> tuple[bool | None, str | None]:
    if not worktree.is_dir():
        return None, "worktree-unavailable"
    result = run_command(
        ["git", "-C", str(worktree), "log", "--oneline", "origin/HEAD..HEAD"],
        repo_root,
    )
    if result is None or result.returncode != 0:
        return None, "command-failed"
    return bool(result.stdout.strip()), None


def sanitize_id(value: object) -> str | None:
    return value if isinstance(value, str) and BEAD_ID_RE.fullmatch(value) else None


def valid_pr_number(value: object) -> bool:
    return type(value) is int and value > 0


def sanitize_timestamp(value: object) -> str | None:
    timestamp = parse_time(value)
    return format_time(timestamp) if timestamp else None


def bead_status(record: dict[str, Any]) -> str | None:
    value = record.get("status")
    return value if isinstance(value, str) and value in BEAD_STATUSES else None


def valid_optional_id(value: object) -> bool:
    return value is None or sanitize_id(value) is not None


def valid_context_status(value: object) -> bool:
    return isinstance(value, str) and safe_code(value, "command-failed") == value


def finding_action_is_consistent(
    *,
    kind: str,
    context_status: str,
    original_id: str | None,
    pr_number: object,
    pr_state: str | None,
    canonical_review_id: str | None,
    duplicate_of: str | None,
    review_id: str | None,
    cooldown_until: str | None,
    recommendation: str,
) -> bool:
    if recommendation == "manual-triage":
        return True
    if recommendation == "skip-command-failure":
        return context_status == "command-failed" and pr_state is None
    if context_status != "resolved" or original_id is None or not valid_pr_number(pr_number):
        return False
    if kind == "review-task" and review_id is None:
        return False
    expected = {
        "close-original-and-reviews": ("original", "MERGED"),
        "close-review-and-original": ("review-task", "MERGED"),
        "close-review-reopen-original": ("review-task", "CLOSED"),
        "dedupe-review-task": ("review-task", "OPEN"),
        "dispatch-canonical-review": (None, "OPEN"),
        "reopen-original-for-retriage": ("original", "CLOSED"),
        "self-heal-review-wiring": ("original", "OPEN"),
        "wait-for-cooldown": (None, "OPEN"),
    }.get(recommendation)
    if expected is None or pr_state != expected[1] or (expected[0] is not None and kind != expected[0]):
        return False
    if recommendation in {"dispatch-canonical-review", "wait-for-cooldown"}:
        return canonical_review_id is not None and cooldown_until is not None
    if recommendation == "dedupe-review-task":
        return (
            canonical_review_id is not None
            and duplicate_of == canonical_review_id
            and review_id != canonical_review_id
            and duplicate_of != review_id
        )
    return recommendation != "self-heal-review-wiring" or canonical_review_id is None


def self_heal_action_is_consistent(
    *,
    context_status: str,
    original_id: str | None,
    pr_number: object,
    canonical_review_id: str | None,
    cooldown_until: str | None,
    recommendation: str,
) -> bool:
    if recommendation == "manual-triage":
        return True
    if context_status != "resolved" or original_id is None or not valid_pr_number(pr_number) or cooldown_until is None:
        return False
    if recommendation in {"review-wiring-current", "self-heal-original-wiring"}:
        return canonical_review_id is not None
    if recommendation in {"self-heal-original-and-review-wiring", "self-heal-review-wiring"}:
        return canonical_review_id is None
    return False


def finding_source_relations_are_unambiguous(
    *,
    kind: str,
    original_id: str | None,
    review_id: str | None,
    canonical_review_id: str | None,
    duplicate_of: str | None,
) -> bool:
    if kind == "original":
        return (
            original_id is not None
            and review_id is None
            and duplicate_of is None
            and canonical_review_id != original_id
        )
    if review_id is None:
        return False
    if original_id is not None and review_id == original_id:
        return False
    if canonical_review_id is not None and canonical_review_id == original_id:
        return False
    if duplicate_of is not None:
        if duplicate_of == review_id or duplicate_of == original_id:
            return False
        if duplicate_of != canonical_review_id:
            return False
    if canonical_review_id is None:
        return duplicate_of is None
    if canonical_review_id == review_id:
        return duplicate_of is None
    return duplicate_of == canonical_review_id


def self_heal_source_relations_are_unambiguous(
    *, original_id: str | None, canonical_review_id: str | None
) -> bool:
    return original_id is not None and canonical_review_id != original_id


def has_matching_canonical_review_finding(
    candidate: dict[str, Any], findings: list[dict[str, Any]]
) -> bool:
    """Require wiring actions to be backed by their resolved canonical task."""
    canonical_review_id = candidate["canonical_review_id"]
    return any(
        canonical_review_id != candidate["original_id"]
        and finding["kind"] == "review-task"
        and finding["context_status"] == "resolved"
        and finding["canonical_review_id"] == canonical_review_id
        and finding["review_id"] == canonical_review_id
        and finding["duplicate_of"] is None
        and finding["review_id"] != finding["original_id"]
        and finding["canonical_review_id"] != finding["original_id"]
        and finding["original_id"] == candidate["original_id"]
        and finding["pr_number"] == candidate["pr_number"]
        for finding in findings
    )


def collection_roles_are_unambiguous(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    relation_error: str | None,
) -> bool:
    """Reject actionable evidence that binds one identity to conflicting roles."""
    if relation_error is not None:
        return False
    original_scopes: dict[str, tuple[str, int]] = {}
    review_scopes: dict[str, tuple[str, int]] = {}
    canonical_scopes: dict[str, tuple[str, int]] = {}
    canonical_finding_scopes: dict[str, tuple[str, int]] = {}
    canonical_by_scope: dict[tuple[str, int], str] = {}
    candidate_scopes: set[tuple[str, int]] = set()

    def bind_original(original_id: str, scope: tuple[str, int]) -> bool:
        existing = original_scopes.get(original_id)
        if existing is not None and existing != scope:
            return False
        original_scopes[original_id] = scope
        return True

    for finding in findings:
        if finding["recommendation"] == "manual-triage" or finding["context_status"] != "resolved":
            continue
        original_id = finding["original_id"]
        pr_number = finding["pr_number"]
        if original_id is None or not valid_pr_number(pr_number):
            return False
        scope = (original_id, pr_number)
        if not bind_original(original_id, scope):
            return False
        if finding["kind"] != "review-task":
            continue

        review_id = finding["review_id"]
        if review_id is None:
            return False
        if review_id in review_scopes:
            return False
        review_scopes[review_id] = scope

        canonical_review_id = finding["canonical_review_id"]
        if canonical_review_id is None:
            continue
        existing_scope = canonical_scopes.get(canonical_review_id)
        if existing_scope is not None and existing_scope != scope:
            return False
        canonical_scopes[canonical_review_id] = scope
        existing_canonical = canonical_by_scope.get(scope)
        if existing_canonical is not None and existing_canonical != canonical_review_id:
            return False
        canonical_by_scope[scope] = canonical_review_id
        if review_id == canonical_review_id:
            canonical_finding_scopes[canonical_review_id] = scope

    for candidate in candidates:
        if candidate["recommendation"] == "manual-triage" or candidate["context_status"] != "resolved":
            continue
        original_id = candidate["original_id"]
        pr_number = candidate["pr_number"]
        if original_id is None or not valid_pr_number(pr_number):
            return False
        scope = (original_id, pr_number)
        if not bind_original(original_id, scope) or scope in candidate_scopes:
            return False
        candidate_scopes.add(scope)

        canonical_review_id = candidate["canonical_review_id"]
        if canonical_review_id is None:
            continue
        existing_scope = canonical_scopes.get(canonical_review_id)
        if existing_scope is not None and existing_scope != scope:
            return False
        canonical_scopes[canonical_review_id] = scope
        existing_canonical = canonical_by_scope.get(scope)
        if existing_canonical is not None and existing_canonical != canonical_review_id:
            return False
        canonical_by_scope[scope] = canonical_review_id

    for review_id, review_scope in review_scopes.items():
        canonical_scope = canonical_scopes.get(review_id)
        if canonical_scope is not None and canonical_scope != review_scope:
            return False
    if any(canonical_finding_scopes.get(review_id) != scope for review_id, scope in canonical_scopes.items()):
        return False
    review_roles = set(review_scopes) | set(canonical_scopes)
    return not (set(original_scopes) & review_roles)


def is_lone_pr_state_command_failure_skip(
    *,
    status: str,
    errors: list[dict[str, str]],
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> bool:
    """Allow only the established no-action singleton PR-state failure through."""
    if status != "partial" or not findings:
        return False
    if len(errors) != 1 or {(item["scope"], item["code"]) for item in errors} != {
        ("pr-state", "command-failed")
    }:
        return False
    scopes = {(item["original_id"], item["pr_number"]) for item in findings}
    if len(scopes) != 1 or sum(item["kind"] == "original" for item in findings) != 1:
        return False
    original_id, pr_number = next(iter(scopes))
    if original_id is None or not valid_pr_number(pr_number):
        return False
    return all(
        item["recommendation"] == "skip-command-failure"
        and item["context_status"] == "command-failed"
        and item["pr_state"] is None
        for item in findings
    ) and all(
        item["recommendation"] == "manual-triage"
        and item["original_id"] == original_id
        and item["pr_number"] == pr_number
        for item in candidates
    )


def sanitize_normalizer(payload: object) -> dict[str, Any]:
    """Keep only the canonical normalizer's safe, compact public fields."""
    if not isinstance(payload, dict) or payload.get("schema") != NORMALIZER_SCHEMA:
        return {
            "errors": [{"code": "normalizer-failed", "scope": "pr-review"}],
            "findings": [],
            "schema": NORMALIZER_SCHEMA,
            "self_heal_candidates": [],
            "status": "partial",
        }

    valid_statuses = {"success", "empty", "partial", "fatal"}
    supplied_status = payload.get("status")
    status_is_valid = isinstance(supplied_status, str) and supplied_status in valid_statuses
    status = supplied_status if status_is_valid else "partial"
    errors: list[dict[str, str]] = []
    collections: dict[str, list[Any]] = {}
    for field in ("errors", "findings", "self_heal_candidates"):
        value = payload.get(field)
        if isinstance(value, list):
            collections[field] = value
        else:
            collections[field] = []
            report_error(errors, "invalid-normalizer-envelope", "pr-review")
    if not status_is_valid:
        report_error(errors, "invalid-normalizer-envelope", "pr-review")

    for item in collections["errors"]:
        if isinstance(item, dict):
            errors.append(
                {
                    "code": safe_code(item.get("code"), "command-failed"),
                    "scope": safe_code(item.get("scope"), "pr-review"),
                }
            )
        else:
            report_error(errors, "invalid-normalizer-envelope", "pr-review")

    relation_error = normalized_finding_relation_error(collections["findings"])
    relation_graph_complete = relation_error is None
    if relation_error:
        report_error(errors, "invalid-normalizer-evidence", "pr-review")
        status = "partial"

    findings = []
    for item in collections["findings"]:
        if not isinstance(item, dict):
            report_error(errors, "invalid-normalizer-envelope", "pr-review")
            continue
        raw_kind = item.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind in {"original", "review-task"} else "review-task"
        raw_state = item.get("pr_state")
        state = raw_state if isinstance(raw_state, str) and raw_state in PR_STATES else None
        number = item.get("pr_number")
        cooldown = sanitize_timestamp(item.get("cooldown_until"))
        canonical_review_id = sanitize_id(item.get("canonical_review_id"))
        duplicate_of = sanitize_id(item.get("duplicate_of"))
        original_id = sanitize_id(item.get("original_id"))
        review_id = sanitize_id(item.get("review_id"))
        raw_recommendation = item.get("recommendation")
        recommendation = (
            raw_recommendation
            if isinstance(raw_recommendation, str) and raw_recommendation in FINDING_RECOMMENDATIONS
            else "manual-triage"
        )
        context_status = safe_code(item.get("context_status"), "command-failed")
        invalid_evidence = ""
        if not relation_graph_complete:
            invalid_evidence = "invalid-normalizer-evidence"
        elif number is None:
            invalid_evidence = "invalid-normalizer-evidence"
        elif not valid_pr_number(number):
            invalid_evidence = "invalid-pr-number"
        elif item.get("cooldown_until") is not None and cooldown is None:
            invalid_evidence = "invalid-cooldown-until"
        elif (
            raw_kind != kind
            or not valid_context_status(item.get("context_status"))
            or not valid_optional_id(item.get("canonical_review_id"))
            or not valid_optional_id(item.get("duplicate_of"))
            or not valid_optional_id(item.get("original_id"))
            or not valid_optional_id(item.get("review_id"))
            or (raw_state is not None and state is None)
            or raw_recommendation != recommendation
            or (kind == "original" and (original_id is None or review_id is not None or duplicate_of is not None))
            or (kind == "review-task" and review_id is None)
            or not finding_source_relations_are_unambiguous(
                kind=kind,
                original_id=original_id,
                review_id=review_id,
                canonical_review_id=canonical_review_id,
                duplicate_of=duplicate_of,
            )
            or not finding_action_is_consistent(
                kind=kind,
                context_status=context_status,
                original_id=original_id,
                pr_number=number,
                pr_state=state,
                canonical_review_id=canonical_review_id,
                duplicate_of=duplicate_of,
                review_id=review_id,
                cooldown_until=cooldown,
                recommendation=recommendation,
            )
        ):
            invalid_evidence = "invalid-normalizer-evidence"
        if invalid_evidence:
            report_error(errors, invalid_evidence, "pr-review")
            status = "partial"
        findings.append(
            {
                "canonical_review_id": canonical_review_id,
                "context_status": invalid_evidence or context_status,
                "cooldown_until": None if invalid_evidence else cooldown,
                "duplicate_of": duplicate_of,
                "kind": kind,
                "original_id": original_id,
                "pr_number": number if valid_pr_number(number) else None,
                "pr_state": state,
                "recommendation": "manual-triage" if invalid_evidence else recommendation,
                "review_id": review_id,
            }
        )

    self_heal = []
    for item in collections["self_heal_candidates"]:
        if not isinstance(item, dict):
            report_error(errors, "invalid-normalizer-envelope", "pr-review")
            continue
        number = item.get("pr_number")
        cooldown = sanitize_timestamp(item.get("cooldown_until"))
        canonical_review_id = sanitize_id(item.get("canonical_review_id"))
        original_id = sanitize_id(item.get("original_id"))
        raw_recommendation = item.get("recommendation")
        recommendation = (
            raw_recommendation
            if isinstance(raw_recommendation, str) and raw_recommendation in SELF_HEAL_RECOMMENDATIONS
            else "manual-triage"
        )
        context_status = safe_code(item.get("context_status"), "command-failed")
        invalid_evidence = ""
        if not relation_graph_complete:
            invalid_evidence = "invalid-normalizer-evidence"
        elif number is None:
            invalid_evidence = "invalid-normalizer-evidence"
        elif not valid_pr_number(number):
            invalid_evidence = "invalid-pr-number"
        elif item.get("cooldown_until") is not None and cooldown is None:
            invalid_evidence = "invalid-cooldown-until"
        elif (
            not valid_context_status(item.get("context_status"))
            or not valid_optional_id(item.get("canonical_review_id"))
            or not valid_optional_id(item.get("original_id"))
            or raw_recommendation != recommendation
            or original_id is None
            or not self_heal_source_relations_are_unambiguous(
                original_id=original_id,
                canonical_review_id=canonical_review_id,
            )
            or not self_heal_action_is_consistent(
                context_status=context_status,
                original_id=original_id,
                pr_number=number,
                canonical_review_id=canonical_review_id,
                cooldown_until=cooldown,
                recommendation=recommendation,
            )
        ):
            invalid_evidence = "invalid-normalizer-evidence"
        if invalid_evidence:
            report_error(errors, invalid_evidence, "pr-review")
            status = "partial"
        self_heal.append(
            {
                "canonical_review_id": canonical_review_id,
                "context_status": invalid_evidence or context_status,
                "cooldown_until": None if invalid_evidence else cooldown,
                "original_id": original_id,
                "pr_number": number if valid_pr_number(number) else None,
                "recommendation": "manual-triage" if invalid_evidence else recommendation,
            }
        )

    for candidate in self_heal:
        if candidate["recommendation"] not in {"review-wiring-current", "self-heal-original-wiring"}:
            continue
        if has_matching_canonical_review_finding(candidate, findings):
            continue
        report_error(errors, "invalid-normalizer-evidence", "pr-review")
        status = "partial"
        candidate["context_status"] = "invalid-normalizer-evidence"
        candidate["cooldown_until"] = None
        candidate["recommendation"] = "manual-triage"

    if not collection_roles_are_unambiguous(
        findings,
        self_heal,
        relation_error=relation_error,
    ):
        report_error(errors, "invalid-normalizer-evidence", "pr-review")
        status = "partial"

    if status == "success" and not errors and not findings and not self_heal:
        status = "empty"
    elif status == "empty" and (errors or findings or self_heal):
        report_error(errors, "inconsistent-normalizer-status", "pr-review")
    elif status == "success" and errors:
        report_error(errors, "inconsistent-normalizer-status", "pr-review")
    elif status in {"partial", "fatal"} and not errors:
        report_error(errors, "inconsistent-normalizer-status", "pr-review")
    elif status == "fatal" and (findings or self_heal):
        report_error(errors, "inconsistent-normalizer-status", "pr-review")

    preserve_lone_skip = is_lone_pr_state_command_failure_skip(
        status=status,
        errors=errors,
        findings=findings,
        candidates=self_heal,
    )
    if errors:
        status = "partial"
        for item in findings:
            if preserve_lone_skip and item["recommendation"] == "skip-command-failure":
                continue
            item["recommendation"] = "manual-triage"
        for item in self_heal:
            item["recommendation"] = "manual-triage"

    findings.sort(key=lambda item: (str(item["original_id"] or ""), str(item["review_id"] or "")))
    self_heal.sort(key=lambda item: (str(item["original_id"] or ""), int(item["pr_number"] or 0)))
    errors = sorted({(item["scope"], item["code"]) for item in errors})
    return {
        "errors": [{"code": code, "scope": scope} for scope, code in errors],
        "findings": findings,
        "schema": NORMALIZER_SCHEMA,
        "self_heal_candidates": self_heal,
        "status": status,
    }


def normalizer_report(args: argparse.Namespace, repo_root: Path) -> dict[str, Any]:
    result = run_command(
        [sys.executable, str(args.normalizer), "--repo-root", str(repo_root), "--now", args.now],
        repo_root,
    )
    if result is None or result.returncode != 0:
        return sanitize_normalizer(None)
    try:
        return sanitize_normalizer(json.loads(result.stdout))
    except json.JSONDecodeError:
        return sanitize_normalizer(None)


def worktree_entries(payload: object) -> list[dict[str, str | None]]:
    """Extract (path-derived id, branch-derived id) pairs from ``bd worktree
    list --json`` rows.

    The plain ``bd worktree list`` table truncates long paths and branch
    names to fit terminal width, which silently corrupts id extraction --
    ``--json`` returns the untruncated ``path``/``branch`` fields instead.
    """
    if not isinstance(payload, list):
        return []
    entries: list[dict[str, str | None]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        branch = item.get("branch")
        path_match = WORKTREE_RE.search(path) if isinstance(path, str) else None
        branch_match = AGENT_BRANCH_RE.search(branch) if isinstance(branch, str) else None
        path_id = path_match.group(1) if path_match else None
        branch_id = branch_match.group(1) if branch_match else None
        if path_id is None and branch_id is None:
            continue
        entries.append({"path_id": path_id, "branch_id": branch_id})
    return entries


def worktree_listing(
    entries: list[dict[str, str | None]],
) -> tuple[list[str], dict[str, str], set[str]]:
    """Flatten worktree entries into (all ids, branch-id -> path-id, ambiguous branch ids).

    Each JSON entry from ``bd worktree list --json`` already pairs one
    worktree's directory-derived id with the bead id of its checked-out
    branch, so no line-based text correlation is required. The coordinator's
    two-stage review topology transfers an existing PR worktree to the
    reviewer instead of creating a same-named one, so a worktree directory
    keyed by the review bead id can have the *original* bead's branch
    checked out inside it -- the conventional ``parallel-agents/<original-id>``
    path never exists, only ``parallel-agents/<review-id>`` does. Callers
    resolve the branch id to the directory id it actually lives under via
    the returned mapping. A branch id claimed by more than one distinct
    directory id is reported as ambiguous rather than resolved, since git's
    own branch-uniqueness guarantee makes that shape suspicious.
    """
    names: set[str] = set()
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in entries:
        path_id = entry["path_id"]
        branch_id = entry["branch_id"]
        if path_id is not None:
            names.add(path_id)
        if branch_id is not None:
            names.add(branch_id)
        if branch_id is not None and path_id is not None:
            existing = mapping.get(branch_id)
            if existing is not None and existing != path_id:
                ambiguous.add(branch_id)
            else:
                mapping[branch_id] = path_id
    for branch_id in ambiguous:
        mapping.pop(branch_id, None)
    return sorted(names), mapping, ambiguous


def resolve_claim_worktree(
    issue_id: str,
    repo_root: Path,
    *,
    branch_path_ids: dict[str, str],
    ambiguous_worktree_ids: set[str],
) -> tuple[Path | None, bool]:
    if issue_id in ambiguous_worktree_ids:
        return None, True
    path_id = branch_path_ids.get(issue_id, issue_id)
    return repo_root / ".worktrees" / "parallel-agents" / path_id, False


def scan_claims(
    records: list[dict[str, Any]] | None,
    *,
    repo_root: Path,
    worktree_names: set[str],
    branch_path_ids: dict[str, str],
    ambiguous_worktree_ids: set[str],
    now: datetime,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if records is None:
        return []
    findings = []
    for record in records:
        issue_id = record_id(record)
        if issue_id is None:
            report_error(errors, "invalid-record", "claims")
            continue
        worktree, ambiguous = resolve_claim_worktree(
            issue_id,
            repo_root,
            branch_path_ids=branch_path_ids,
            ambiguous_worktree_ids=ambiguous_worktree_ids,
        )
        exists = ambiguous or issue_id in worktree_names or (worktree is not None and worktree.is_dir())
        branch, branch_error = remote_branch_exists(issue_id, repo_root)
        if ambiguous:
            unpublished, unpublished_error = None, "ambiguous-worktree"
        else:
            unpublished, unpublished_error = unpublished_work(worktree, repo_root) if exists else (None, None)
        if branch_error:
            report_error(errors, branch_error, "claims")
        if unpublished_error:
            report_error(errors, unpublished_error, "claims")
        beat = heartbeat_state(record, now)
        if beat == "fresh":
            recommendation = "preserve-live-claim"
        elif branch_error or unpublished_error:
            recommendation = "manual-triage"
        elif unpublished is True:
            recommendation = "preserve-unpublished-work"
        elif beat == "stale":
            recommendation = "release-claim-candidate"
        else:
            recommendation = "manual-triage"
        findings.append(
            {
                "heartbeat_state": beat,
                "id": issue_id,
                "recommendation": recommendation,
                "remote_branch_exists": branch,
                "unpublished_work": unpublished,
                "worktree_exists": exists,
            }
        )
    return sorted(findings, key=lambda item: item["id"])


def scan_blockers(
    records: list[dict[str, Any]] | None,
    *,
    repo_root: Path,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if records is None:
        return []
    findings = []
    for record in records:
        if {"pr-review", "pr-review-task"} & labels(record):
            continue
        issue_id = record_id(record)
        if issue_id is None:
            report_error(errors, "invalid-record", "blockers")
            continue
        payload, error = command_json(["bd", "dep", "list", issue_id, "--json"], repo_root)
        if error or not isinstance(payload, list):
            report_error(errors, error or "invalid-json", "blockers")
            findings.append(
                {"all_blockers_closed": None, "dependencies": [], "id": issue_id, "recommendation": "manual-triage"}
            )
            continue
        dependencies = []
        seen_dependency_ids: set[str] = set()
        failed = False
        for item in payload:
            dep_id = dependency_target_id(item)
            if dep_id is None or dep_id == issue_id or dep_id in seen_dependency_ids:
                failed = True
                report_error(errors, "invalid-record", "blockers")
                continue
            seen_dependency_ids.add(dep_id)
            dep_payload, dep_error = command_json(["bd", "show", dep_id, "--json"], repo_root)
            if dep_error is None:
                dep, evidence_error = requested_record(dep_payload, dep_id)
            else:
                dep, evidence_error = None, dep_error
            status = bead_status(dep) if isinstance(dep, dict) else None
            if status is None:
                failed = True
                report_error(errors, evidence_error or "invalid-bead-status", "blockers")
            dependencies.append({"id": dep_id, "status": status})
        dependencies.sort(key=lambda item: item["id"])
        all_closed: bool | None = None if failed or not dependencies else all(item["status"] == "closed" for item in dependencies)
        recommendation = "manual-triage" if all_closed is None else "unblock-candidate" if all_closed else "remain-blocked"
        findings.append(
            {
                "all_blockers_closed": all_closed,
                "dependencies": dependencies,
                "id": issue_id,
                "recommendation": recommendation,
            }
        )
    return sorted(findings, key=lambda item: item["id"])


def scan_review_locks(
    records: list[dict[str, Any]] | None,
    *,
    repo_root: Path,
    worktree_names: set[str],
    now: datetime,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if records is None:
        return []
    findings = []
    for record in records:
        issue_id = record_id(record)
        if issue_id is None:
            report_error(errors, "invalid-record", "review-locks")
            continue
        worktree = repo_root / ".worktrees" / "parallel-agents" / issue_id
        exists = issue_id in worktree_names or worktree.is_dir()
        beat = heartbeat_state(record, now)
        status = bead_status(record)
        if status is None:
            report_error(errors, "invalid-bead-status", "review-locks")
            recommendation = "manual-triage"
        else:
            live = status == "in_progress" and exists and beat == "fresh"
            recommendation = "preserve-review-lock" if live else "release-review-lock-candidate"
        findings.append(
            {
                "heartbeat_state": beat,
                "id": issue_id,
                "recommendation": recommendation,
                "worktree_exists": exists,
            }
        )
    return sorted(findings, key=lambda item: item["id"])


def scan_worktrees(
    names: list[str] | None,
    *,
    repo_root: Path,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if names is None:
        return []
    findings = []
    for worktree_id in names:
        issue_id = worktree_id
        payload, error = command_json(["bd", "show", issue_id, "--json"], repo_root)
        if error is None:
            record, evidence_error = requested_record(payload, issue_id)
        else:
            record, evidence_error = None, error
        status = bead_status(record) if isinstance(record, dict) else None
        if status is None:
            report_error(errors, evidence_error or "invalid-bead-status", "worktrees")
        worktree = repo_root / ".worktrees" / "parallel-agents" / worktree_id
        exists = worktree.is_dir()
        branch, branch_error = remote_branch_exists(issue_id, repo_root)
        unpublished, unpublished_error = unpublished_work(worktree, repo_root) if exists else (None, None)
        if branch_error:
            report_error(errors, branch_error, "worktrees")
        if unpublished_error:
            report_error(errors, unpublished_error, "worktrees")
        if branch_error or unpublished_error:
            recommendation = "manual-triage"
        elif unpublished is True:
            recommendation = "preserve-unpublished-work"
        elif status == "closed":
            recommendation = "cleanup-eligible-after-verification"
        elif status == "open":
            recommendation = "preserve-branch-worktree"
        elif status in {"in_progress", "blocked"}:
            recommendation = "preserve-active-worktree"
        else:
            recommendation = "manual-triage"
        findings.append(
            {
                "issue_id": issue_id,
                "issue_status": status,
                "remote_branch_exists": branch,
                "recommendation": recommendation,
                "unpublished_work": unpublished,
                "worktree_id": worktree_id,
            }
        )
    return sorted(findings, key=lambda item: item["worktree_id"])


def disk_bytes(path: Path) -> int:
    """Allocated bytes under ``path`` without following symlinks or mounts."""
    total = 0
    device = path.lstat().st_dev
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            total += getattr(info, "st_blocks", 0) * 512 or info.st_size
            if entry.is_dir(follow_symlinks=False) and info.st_dev == device:
                pending.append(Path(entry.path))
    return total


def artifact_bead(name: str, repo_root: Path) -> dict[str, Any] | None:
    """Resolve the longest ``<bead-id>-...`` prefix of a directory name."""
    parts = name.split("-")
    for length in range(min(len(parts), 4), 1, -1):
        candidate = "-".join(parts[:length])
        if not BEAD_ID_RE.fullmatch(candidate):
            continue
        payload, error = command_json(["bd", "show", candidate, "--json"], repo_root)
        if error is None:
            record, _ = requested_record(payload, candidate)
            if record is not None:
                return record
    return None


def pr_closed(record: dict[str, Any], repo_root: Path, errors: list[dict[str, str]]) -> bool | None:
    ref = record.get("external_ref")
    match = re.fullmatch(r"gh-pr:([0-9]+)", ref) if isinstance(ref, str) else None
    if match is None:
        return None
    payload, error = command_json(["gh", "pr", "view", match.group(1), "--json", "state"], repo_root)
    state = payload.get("state") if isinstance(payload, dict) else None
    if error or state not in PR_STATES:
        report_error(errors, error or "invalid-pr-state", "build-artifacts")
        return None
    return state != "OPEN"


def scan_build_artifacts(
    roots: list[Path],
    *,
    repo_root: Path,
    errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Attribute each build-artifact directory to its bead and size it.

    Targets die with their bead's PR; receipts are copied out first by the
    coordinator. This pass only reports.
    """
    findings = []
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            report_error(errors, "invalid-artifact-root", "build-artifacts")
            continue
        for child in sorted(root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            record = artifact_bead(child.name, repo_root)
            status = bead_status(record) if record else None
            closed_pr = pr_closed(record, repo_root, errors) if record and status != "closed" else None
            if record is None or status is None:
                recommendation = "manual-triage"
            elif status == "closed" or closed_pr:
                recommendation = "delete-after-receipts"
            else:
                recommendation = "preserve-active-bead"
            findings.append(
                {
                    "bytes": disk_bytes(child),
                    "issue_id": record_id(record) if record else None,
                    "issue_status": status,
                    "name": child.name,
                    "recommendation": recommendation,
                    "root_index": roots.index(root),
                }
            )
    return findings


def fatal_envelope(now: str | None, errors: list[dict[str, str]], pr_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "blockers": [],
        "build_artifacts": [],
        "claims": [],
        "dolt": {"doctor": None, "status": None},
        "errors": sorted(errors, key=lambda item: (item["scope"], item["code"])),
        "generated_at": now,
        "pr_review": pr_review,
        "review_locks": [],
        "schema": SCHEMA,
        "status": "fatal",
        "worktrees": [],
    }


def scan(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    repo_root = args.repo_root.resolve()
    now = parse_time(args.now)
    if now is None:
        return fatal_envelope(None, [{"code": "invalid-now", "scope": "bootstrap"}], sanitize_normalizer(None)), 1
    args.now = format_time(now)
    if not repo_root.is_dir():
        return fatal_envelope(args.now, [{"code": "invalid-repo-root", "scope": "bootstrap"}], sanitize_normalizer(None)), 1

    errors: list[dict[str, str]] = []
    pr_review = normalizer_report(args, repo_root)
    if pr_review["status"] in {"partial", "fatal"}:
        report_error(errors, "normalizer-failed", "pr-review")

    claims_records, claims_error = list_records(repo_root, status="in_progress")
    blocked_records, blockers_error = list_records(repo_root, status="blocked")
    lock_records, locks_error = list_records(repo_root, label="review-running")
    for error, scope in ((claims_error, "claims"), (blockers_error, "blockers"), (locks_error, "review-locks")):
        if error:
            report_error(errors, error, scope)

    worktree_payload, worktree_error = command_json(
        ["bd", "-C", str(repo_root), "worktree", "list", "--json"], repo_root
    )
    if worktree_error:
        report_error(errors, worktree_error, "worktrees")
        names: list[str] | None = None
        branch_path_ids, ambiguous_worktree_ids = {}, set()
    else:
        names, branch_path_ids, ambiguous_worktree_ids = worktree_listing(worktree_entries(worktree_payload))

    dolt_result = run_command(["bd", "-C", str(repo_root), "dolt", "status"], repo_root)
    doctor_result = run_command(["bd", "-C", str(repo_root), "doctor"], repo_root)
    dolt_status = "healthy" if dolt_result is not None and dolt_result.returncode == 0 else "unhealthy"
    doctor_status = "healthy" if doctor_result is not None and doctor_result.returncode == 0 else "unhealthy"
    if dolt_status != "healthy":
        report_error(errors, "command-failed", "dolt-status")
    if doctor_status != "healthy":
        report_error(errors, "command-failed", "dolt-doctor")

    core_results = (claims_records, blocked_records, lock_records, names)
    if all(result is None for result in core_results) and dolt_status == "unhealthy" and doctor_status == "unhealthy":
        return fatal_envelope(args.now, errors, pr_review), 1

    worktree_names = set(names or [])
    claims = scan_claims(
        claims_records,
        repo_root=repo_root,
        worktree_names=worktree_names,
        branch_path_ids=branch_path_ids,
        ambiguous_worktree_ids=ambiguous_worktree_ids,
        now=now,
        errors=errors,
    )
    blockers = scan_blockers(blocked_records, repo_root=repo_root, errors=errors)
    review_locks = scan_review_locks(
        lock_records,
        repo_root=repo_root,
        worktree_names=worktree_names,
        now=now,
        errors=errors,
    )
    worktrees = scan_worktrees(names, repo_root=repo_root, errors=errors)
    build_artifacts = scan_build_artifacts(args.artifact_root, repo_root=repo_root, errors=errors)
    errors.sort(key=lambda item: (item["scope"], item["code"]))
    empty = not (claims or blockers or review_locks or worktrees or build_artifacts or pr_review["findings"] or pr_review["self_heal_candidates"])
    status = "partial" if errors else "empty" if empty else "success"
    return (
        {
            "blockers": blockers,
            "build_artifacts": build_artifacts,
            "claims": claims,
            "dolt": {"doctor": doctor_status, "status": dolt_status},
            "errors": errors,
            "generated_at": args.now,
            "pr_review": pr_review,
            "review_locks": review_locks,
            "schema": SCHEMA,
            "status": status,
            "worktrees": worktrees,
        },
        0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report cleanup findings without mutating Beads, Git, GitHub, or Dolt."
    )
    parser.add_argument("--repo-root", required=True, type=Path, help="Repository or rig root to inspect.")
    parser.add_argument(
        "--now",
        default=format_time(datetime.now(timezone.utc)),
        help="RFC 3339 UTC timestamp; supply it for byte-stable reports.",
    )
    parser.add_argument(
        "--normalizer",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "beads-coordinator"
        / "scripts"
        / "normalize_pr_review_state.py",
        help="Canonical report-only PR-review normalizer.",
    )
    parser.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        type=Path,
        help="Directory whose <bead-id>-* children are build artifacts (repeatable).",
    )
    args = parser.parse_args()
    try:
        payload, exit_code = scan(args)
    except Exception:
        timestamp = parse_time(args.now)
        payload = fatal_envelope(
            format_time(timestamp) if timestamp else None,
            [{"code": "unexpected-error", "scope": "scan"}],
            sanitize_normalizer(None),
        )
        exit_code = 1
    emit(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
