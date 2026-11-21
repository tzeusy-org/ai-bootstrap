#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Structural packet check for Beads dispatch readiness (read-only).

Decides, from bead fields alone, whether a bead carries the packet slots that
`references/coordinator-loop.md` Step 2 requires, so two lanes rule the same
way on the same bead. It checks where each packet item lives, not whether the
content is right: semantic readiness (source-grounded scope, settled shared
interfaces, authority) stays the coordinator's judgment.

Slots and where they live:

- outcome     -> `description`, non-empty.
- acceptance  -> `acceptance_criteria`, non-empty. A checklist only inside
                 `description` does not count.
- decisions   -> required only for `feature` and `epic` beads, which introduce
                 new behavior: `design`, non-empty, or a scope section in
                 `description` (`## Scope`, `## Steps to reproduce`, or a line
                 starting `Scope:`, optionally bulleted or bold). Bugs, tasks
                 and chores change existing behavior under existing contracts,
                 so their description carries the surface.
- shaping     -> no `needs-shaping` label.

Usage:
    uv run scripts/check_packet.py --repo-root "$REPO_ROOT" [ID ...]

With no IDs it checks the whole `bd ready` queue. Prints one JSON object,
`{"beads": [{"id", "packet_complete", "missing": [...]}, ...]}`; a bead that
cannot be read is reported per bead as `{"id", "error"}`. Exit 1 only when the
tracker itself cannot be queried.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

SHAPING_LABEL = "needs-shaping"
DECISION_TYPES = frozenset({"feature", "epic"})
SCOPE_SECTION = re.compile(
    r"^[ \t]*(?:"
    r"#{1,6}[ \t]+(?:scope|steps to reproduce)\b"
    r"|(?:[-*][ \t]+)?(?:\*\*)?scope(?:\*\*)?[ \t]*:(?:\*\*)?"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def packet_status(record: dict[str, Any]) -> dict[str, Any]:
    """Return `{id, packet_complete, missing}` for one `bd show --json` record."""
    missing = []
    description = _text(record.get("description"))
    if not description:
        missing.append("outcome:description")
    if not _text(record.get("acceptance_criteria")):
        missing.append("acceptance:acceptance_criteria")
    if (
        record.get("issue_type") in DECISION_TYPES
        and not _text(record.get("design"))
        and not SCOPE_SECTION.search(description)
    ):
        missing.append("decisions:design-or-scope-section")
    if SHAPING_LABEL in (record.get("labels") or []):
        missing.append("shaping:needs-shaping-label")
    return {"id": record.get("id"), "packet_complete": not missing, "missing": missing}


class TrackerError(Exception):
    pass


def _bd_json(repo_root: str, args: list[str]) -> Any:
    try:
        result = subprocess.run(
            ["bd", "-C", repo_root, *args, "--json"], capture_output=True, text=True
        )
    except OSError as exc:
        raise TrackerError(f"bd unavailable: {exc}") from exc
    if result.returncode != 0:
        raise TrackerError((result.stderr.strip().splitlines() or ["bd failed"])[-1])
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise TrackerError(f"bd returned non-JSON output: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args(argv)

    try:
        ids = args.ids or [
            row["id"] for row in _bd_json(args.repo_root, ["ready", "--limit", "0"]) or []
        ]
    except (TrackerError, KeyError, TypeError) as exc:
        print(json.dumps({"error": "bd-failed", "detail": str(exc)}))
        return 1
    beads = []
    for issue_id in ids:
        try:
            rows = _bd_json(args.repo_root, ["show", issue_id])
        except TrackerError as exc:
            beads.append({"id": issue_id, "error": str(exc)})
            continue
        record = rows[0] if isinstance(rows, list) and rows else rows
        if not isinstance(record, dict):
            beads.append({"id": issue_id, "error": "bead-unavailable"})
            continue
        beads.append(packet_status(record))
    print(json.dumps({"beads": beads}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
