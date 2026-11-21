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
- decisions   -> `design`, non-empty; or, when there is no new decision to
                 record, a scope/repro section in `description` (a line
                 starting `## Scope`, `## Steps to reproduce`, `Scope:` ...).
- shaping     -> no `needs-shaping` label.

Usage:
    uv run scripts/check_packet.py --repo-root "$REPO_ROOT" [ID ...]

With no IDs it checks `bd ready`. Prints one JSON object:
`{"beads": [{"id", "packet_complete", "missing": [...]}, ...]}`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

SHAPING_LABEL = "needs-shaping"
SCOPE_SECTION = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:scope|steps to reproduce|reproduction|repro)\b\s*:?",
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
    if not _text(record.get("design")) and not SCOPE_SECTION.search(description):
        missing.append("decisions:design-or-scope-section")
    if SHAPING_LABEL in (record.get("labels") or []):
        missing.append("shaping:needs-shaping-label")
    return {"id": record.get("id"), "packet_complete": not missing, "missing": missing}


def _bd_json(repo_root: str, args: list[str]) -> Any:
    result = subprocess.run(
        ["bd", "-C", repo_root, *args, "--json"], capture_output=True, text=True
    )
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["bd failed"])[-1]
        print(json.dumps({"error": "bd-failed", "args": args, "detail": detail}))
        sys.exit(1)
    return json.loads(result.stdout or "null")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args(argv)

    ids = args.ids or [row["id"] for row in _bd_json(args.repo_root, ["ready"]) or []]
    beads = []
    for issue_id in ids:
        rows = _bd_json(args.repo_root, ["show", issue_id])
        record = rows[0] if isinstance(rows, list) and rows else rows
        if not isinstance(record, dict):
            print(json.dumps({"error": "bead-unavailable", "id": issue_id}))
            return 1
        beads.append(packet_status(record))
    print(json.dumps({"beads": beads}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
