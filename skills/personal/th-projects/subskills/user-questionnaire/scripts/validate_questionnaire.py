#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fail-closed structural validation for owner-questionnaire packets."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SECTION_RE = re.compile(r"^### `([^`]+)`(?:\s+—\s+.+)?\s*$", re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|FIXME)\b|<[A-Z][A-Z0-9 _-]*>")
READY_STATES = {"Review-ready", "Agreed", "Held", "Rejected", "Superseded"}
REVIEWED_STATES = READY_STATES | {"Applied"}
ALL_STATES = REVIEWED_STATES | {"Draft", "Needs-rework"}
REQUIRED_SUBSECTIONS = (
    "Decision needed",
    "Background and freshness",
    "Options",
    "Recommendation",
    "Authorization boundary",
    "Adversarial review record",
    "Owner decision record",
    "Evidence",
)
OWNER_FIELDS = (
    "Status",
    "Actor/channel",
    "Recorded at",
    "Choice",
    "Owner edits",
    "Post-answer review",
    "Final authorization boundary",
    "Canonical destination",
    "Routing evidence",
)
RESERVED_REVIEWER_IDS = {"pending", "todo", "tbd", "unknown", "none", "n/a"}
# Owner-authored free text (with indented continuation lines) inside an owner
# decision record that carries an answer timestamp. It is the owner's words,
# not author scaffolding, so the placeholder scan skips it: "Held: TBD after
# the migration" is a valid answer. Unanswered records are still scanned.
OWNER_TEXT_RE = re.compile(r"^- (?:Choice|Owner edits):.*(?:\n[ \t]+\S.*)*", re.MULTILINE)


def _summary_rows(text: str) -> tuple[dict[str, str], list[str]]:
    """Return decision-id to state mappings from the questionnaire matrix."""
    errors: list[str] = []
    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("|") and "Decision ID" in line and "Decision state" in line
        ),
        None,
    )
    if header_index is None:
        return {}, ["missing questionnaire summary table"]

    rows: dict[str, str] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            errors.append(f"malformed questionnaire summary row: {line}")
            continue
        decision_id = cells[1].strip("`")
        state = cells[3]
        if decision_id in rows:
            errors.append(f"duplicate summary decision id `{decision_id}`")
        rows[decision_id] = state
    if not rows:
        errors.append("questionnaire summary table has no decision rows")
    return rows, errors


def section_blocks(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    matches = list(SECTION_RE.finditer(text))
    errors: list[str] = []
    seen: set[str] = set()
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        decision_id = match.group(1)
        if decision_id in seen:
            errors.append(f"duplicate decision section id `{decision_id}`")
        seen.add(decision_id)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((decision_id, text[match.start() : end]))
    if not blocks:
        errors.append("questionnaire has no decision sections")
    return blocks, errors


def labeled_field(block: str, label: str) -> str | None:
    match = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def subsection(block: str, heading: str) -> str | None:
    match = re.search(
        rf"^#### {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^#### |\Z)",
        block,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body").strip() if match else None


def _bullet_field(body: str, label: str) -> str | None:
    match = re.search(rf"^- {re.escape(label)}:\s*(\S.*)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def _validate_review(decision_id: str, block: str, errors: list[str]) -> None:
    review_header = labeled_field(block, "Adversarial review")
    header_match = re.fullmatch(
        r"Passed\s+—\s+reviewer\s+`([^`]+)`\s+on\s+\d{4}-\d{2}-\d{2}",
        review_header or "",
    )
    if not header_match:
        errors.append(
            f"`{decision_id}` must record Passed adversarial review with reviewer id and date"
        )
    elif header_match.group(1).strip().lower() in RESERVED_REVIEWER_IDS:
        errors.append(f"`{decision_id}` adversarial review header uses a reserved Reviewer id")

    review_body = subsection(block, "Adversarial review record") or ""
    reviewer = _bullet_field(review_body, "Reviewer")
    reviewer_match = re.fullmatch(r"`([^`]+)`", reviewer or "")
    if not reviewer_match:
        errors.append(f"`{decision_id}` adversarial review record needs a valid Reviewer id")
    elif reviewer_match.group(1).strip().lower() in RESERVED_REVIEWER_IDS:
        errors.append(f"`{decision_id}` adversarial review record uses a reserved Reviewer id")
    elif header_match and reviewer_match.group(1) != header_match.group(1):
        errors.append(f"`{decision_id}` reviewer identity does not match review header")

    for label in ("Scope verdict", "Recommendation verdict"):
        verdict = _bullet_field(review_body, label)
        if verdict is None:
            errors.append(f"`{decision_id}` adversarial review record is missing {label}")
        elif not re.fullmatch(r"Pass(?:\s+—\s+\S.*)?", verdict):
            errors.append(f"`{decision_id}` {label} must be exactly Pass before owner review")

    for label in ("Material corrections", "Evidence freshness"):
        value = _bullet_field(review_body, label)
        if value is None:
            errors.append(f"`{decision_id}` adversarial review record is missing {label}")
        elif value.lower().startswith("pending"):
            errors.append(f"`{decision_id}` adversarial review field {label} is still pending")


def _validate_options(decision_id: str, block: str, errors: list[str]) -> set[str]:
    body = subsection(block, "Options")
    if body is None:
        return set()
    labels: list[str] = []
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        match = re.fullmatch(r"([A-D])\.\s*(.*)", cells[0])
        if not match:
            continue
        label, outcome = match.groups()
        labels.append(label)
        if not outcome or not cells[1] or not cells[2]:
            errors.append(
                f"`{decision_id}` option {label} needs non-empty outcome, pros, and cons"
            )
    if not 2 <= len(labels) <= 4:
        errors.append(f"`{decision_id}` must present 2-4 concrete options; found {len(labels)}")
    if len(labels) != len(set(labels)):
        errors.append(f"`{decision_id}` has duplicate option labels")
    return set(labels)


def _validate_recommendation(
    decision_id: str, block: str, options: set[str], errors: list[str]
) -> None:
    body = subsection(block, "Recommendation") or ""
    match = re.search(r"\b(?:Choose|Recommend)\s+\*\*([A-D])\*\*", body)
    if not match:
        errors.append(f"`{decision_id}` recommendation must select an option using Choose **A**")
    elif match.group(1) not in options:
        errors.append(f"`{decision_id}` recommendation selects absent option {match.group(1)}")


def _validate_owner_record(decision_id: str, state: str | None, block: str, errors: list[str]) -> None:
    body = subsection(block, "Owner decision record") or ""
    values: dict[str, str] = {}
    for label in OWNER_FIELDS:
        value = _bullet_field(body, label)
        if value is None:
            errors.append(f"`{decision_id}` owner decision record is missing {label}")
        else:
            values[label] = value

    if state not in {"Agreed", "Held", "Rejected", "Superseded", "Applied"}:
        return
    if values.get("Status") != state:
        errors.append(f"`{decision_id}` owner decision Status must match item state {state}")
    for label in (
        "Actor/channel",
        "Recorded at",
        "Choice",
        "Owner edits",
        "Post-answer review",
        "Final authorization boundary",
        "Canonical destination",
    ):
        if values.get(label, "").lower().startswith("pending"):
            errors.append(f"`{decision_id}` owner decision field {label} is still pending")
    recorded_at = values.get("Recorded at", "")
    if recorded_at and not re.match(r"^\d{4}-\d{2}-\d{2}(?:T\S+)?$", recorded_at):
        errors.append(f"`{decision_id}` Recorded at must be an ISO date or timestamp")
    choice = values.get("Choice", "")
    if state in {"Agreed", "Applied"} and not re.match(r"^(?:[A-D]|Edited:\s+\S)", choice):
        errors.append(f"`{decision_id}` Choice must name option A-D or an Edited choice")
    edited = choice.startswith("Edited:") or values.get("Owner edits", "None") != "None"
    if edited and not re.fullmatch(
        r"Passed\s+—\s+reviewer\s+`[^`]+`\s+on\s+\d{4}-\d{2}-\d{2}",
        values.get("Post-answer review", ""),
    ):
        errors.append(f"`{decision_id}` edited owner choice requires a passed post-answer review")
    if state == "Applied" and values.get("Routing evidence", "").lower().startswith("pending"):
        errors.append(f"`{decision_id}` Applied item has pending Routing evidence")


def _without_owner_text(text: str) -> str:
    """``text`` with owner-authored values removed from every answered owner decision record."""

    def strip_answered(match: re.Match[str]) -> str:
        recorded_at = _bullet_field(match.group(2), "Recorded at") or ""
        if not recorded_at or recorded_at.lower().startswith("pending"):
            return match.group(0)
        return match.group(1) + OWNER_TEXT_RE.sub("", match.group(2))

    return re.sub(
        r"(^#### Owner decision record\s*$\n)(.*?)(?=^#### |^### |\Z)",
        strip_answered,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )


def validate_packet(text: str, require_review_ready: bool = False) -> list[str]:
    """Return every structural validation error in ``text``."""
    errors: list[str] = []
    snapshot = re.search(r"^\*\*Evidence snapshot:\*\*\s*(\S.*)$", text, re.MULTILINE)
    if not snapshot:
        errors.append("missing Evidence snapshot")
    elif "repository" not in snapshot.group(1).lower() or "`" not in snapshot.group(1):
        errors.append("Evidence snapshot must name repository and exact commit/ref")
    if not re.search(r"^\*\*Artifact boundary:\*\*\s*\S", text, re.MULTILINE):
        errors.append("missing Artifact boundary")

    for placeholder in PLACEHOLDER_RE.findall(_without_owner_text(text)):
        errors.append(f"unresolved placeholder `{placeholder}`")

    summary, summary_errors = _summary_rows(text)
    blocks, block_errors = section_blocks(text)
    errors.extend(summary_errors)
    errors.extend(block_errors)

    section_ids = {decision_id for decision_id, _ in blocks}
    for decision_id in sorted(set(summary) - section_ids):
        errors.append(f"summary decision `{decision_id}` has no matching section")
    for decision_id in sorted(section_ids - set(summary)):
        errors.append(f"decision section `{decision_id}` has no matching summary row")

    for decision_id, block in blocks:
        state = labeled_field(block, "Decision state")
        if state is None:
            errors.append(f"`{decision_id}` is missing Decision state")
        elif state not in ALL_STATES:
            errors.append(f"`{decision_id}` has unknown Decision state: {state}")
        elif decision_id in summary and summary[decision_id] != state:
            errors.append(
                f"`{decision_id}` state mismatch: summary={summary[decision_id]!r}, section={state!r}"
            )
        if require_review_ready and state not in READY_STATES:
            errors.append(f"`{decision_id}` is not ready for owner review (state: {state or 'missing'})")

        for heading in REQUIRED_SUBSECTIONS:
            body = subsection(block, heading)
            if body is None:
                errors.append(f"`{decision_id}` is missing section: {heading}")
            elif not body:
                errors.append(f"`{decision_id}` {heading} body is empty")

        if not re.search(r"\*\*\[(?:Observed|Inferred|Unknown)\]\*\*", block):
            errors.append(f"`{decision_id}` has no freshness-labeled evidence")

        options = _validate_options(decision_id, block, errors)
        _validate_recommendation(decision_id, block, options, errors)
        if state in REVIEWED_STATES:
            _validate_review(decision_id, block, errors)
        _validate_owner_record(decision_id, state, block, errors)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="Markdown questionnaire packet")
    parser.add_argument(
        "--require-review-ready",
        action="store_true",
        help="fail if any decision is still Draft or Needs-rework",
    )
    args = parser.parse_args()

    errors = validate_packet(
        args.packet.read_text(encoding="utf-8"),
        require_review_ready=args.require_review_ready,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"PASS: questionnaire packet is valid: {args.packet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
