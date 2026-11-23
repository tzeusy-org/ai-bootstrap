#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Parallel-coordinator registry kept in the Beads kv store.

The pure readers (`registrations`, `live_coordinator_ids`, `lane_budget`) are
shared by `rank_ready_work.py` and `beads-cleanup/scripts/cleanup_scan.py`, so
both judge coordinator liveness and the global lane budget identically.

The command line is the only writer, run by the coordinator for its own key
(`references/runtime-and-safety.md` → "Parallel Coordinators"). It computes
timestamps itself, because a hand-written `expires_at` is how a dead
coordinator stays "live" for a day or a live one reads as dead:

    uv run scripts/coordinator_registry.py new-id --runtime claude
    uv run scripts/coordinator_registry.py register --repo-root "$REPO_ROOT" \
        --id "$COORDINATOR_ID" --wake-minutes 30 [--startup] [--extra-lanes N]
    uv run scripts/coordinator_registry.py deregister --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID"

`heartbeat` writes the one canonical stall-heartbeat block into a held bead's
notes (`references/runtime-and-safety.md` → "Stall heartbeat"):

    uv run scripts/coordinator_registry.py heartbeat --repo-root "$REPO_ROOT" \
        --id "$COORDINATOR_ID" --bead <issue-id> [--min-age-minutes 5]

It replaces an existing block (collapsing duplicates) or appends one, leaving
the rest of the notes untouched; skips the write while this id's block is
younger than `--min-age-minutes`; and writes only under `--if-assignee`, so a
bead this id does not hold is refused (`not-assignee`, exit 13). bd has no
compare-and-swap on notes: the helper re-reads just before writing and retries
if the notes changed, which narrows but cannot close the window in which a
concurrent append to the same notes is lost.

`register` writes `{"expires_at": now + wake + 15m}` (plus `extra_lanes`) only
when the stored entry would lapse before the next wake or its `extra_lanes`
changed, so routine wakes add no tracker history. `--startup` refuses an id
that is already live: two sessions sharing an id share every claim.

A failed `bd` call prints `{"action": "failed", "step": ..., "error": <code>,
"detail": <last stderr line>, "attempts": N}`. `classify_failure` names the
code: `server-timeout` (a transient Dolt server blip, retried up to three
attempts in all), `connection-refused` (never retried: on the shared server it
means the host override is missing, not a blip), or `command-failed`.
`cleanup_scan.py` reuses the classifier for its sanitized error codes.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REGISTRY_PREFIX = "beads.coordinator."
BUDGET_KEY = "beads.max_lanes"
DEFAULT_LANE_BUDGET = 4
GRACE = timedelta(minutes=15)
RENEW_MARGIN = timedelta(minutes=5)
# No legitimate registration reaches this far ahead (the longest wake is 60
# minutes plus grace), so a value beyond it is a bad write and is not live.
MAX_AHEAD = timedelta(hours=3)
# A dead owner's claim is adoptable once its newest liveness evidence is this
# old (`references/runtime-and-safety.md`, "Heartbeat and stall parameters").
STALL_THRESHOLD = timedelta(minutes=30)
RUNTIME_RE = re.compile(r"[a-z][a-z0-9-]{0,15}\Z")
# Seconds slept between attempts of a `bd` call that hit a transient server
# error; three attempts in all. Tests patch `sleep`.
RETRY_DELAYS = (1.0, 2.0)
DETAIL_LIMIT = 200
REFUSED_MARKERS = ("connection refused",)
TRANSIENT_MARKERS = (
    "i/o timeout",
    "deadline exceeded",
    "context deadline",
    "timed out",
    "connection reset",
    "broken pipe",
    "unexpected eof",
    "bad connection",
    "invalid connection",
)
sleep = time.sleep
# Only the exact canonical shape is a block; anything else carrying the tag is
# malformed and refused, so a stray tag never swallows neighbouring notes.
HEARTBEAT_BLOCK_RE = re.compile(
    r"\n*\[beads-heartbeat\][ \t]*\r?\nowner=\S+[ \t]*\r?\nlast_heartbeat_at=\S+[ \t]*\r?\n\[/beads-heartbeat\][ \t]*(?:\r?\n|$)"
)
HEARTBEAT_TAG_RE = re.compile(r"\[/?beads-heartbeat\]")
HEARTBEAT_FIELDS_RE = re.compile(
    r"\[beads-heartbeat\]\s*owner=(\S+)\s*last_heartbeat_at=(\S+)\s*\[/beads-heartbeat\]"
)
HEARTBEAT_WRITE_ATTEMPTS = 3


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
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def registrations(kv: dict[str, Any], now: datetime) -> dict[str, dict[str, Any]]:
    """Map each registered coordinator id to `{expires_at, live, extra_lanes}`.

    A value is either a JSON object carrying `expires_at` (and optionally
    `extra_lanes`, lanes the tracker cannot see, such as Team Lead subworkers)
    or a bare RFC 3339 timestamp. An unparseable value, or one further ahead
    than `MAX_AHEAD`, registers the id as not live, so a malformed write can
    never make a dead coordinator look alive.
    """
    found: dict[str, dict[str, Any]] = {}
    for key, raw in kv.items():
        if not isinstance(key, str) or not key.startswith(REGISTRY_PREFIX):
            continue
        coordinator_id = key[len(REGISTRY_PREFIX):]
        if not coordinator_id:
            continue
        value: object = raw
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = None
        expires_text = value.get("expires_at") if isinstance(value, dict) else value
        expires = parse_time(expires_text)
        extra = value.get("extra_lanes") if isinstance(value, dict) else 0
        found[coordinator_id] = {
            "expires_at": expires_text if expires is not None else None,
            "extra_lanes": extra if isinstance(extra, int) and not isinstance(extra, bool) and extra > 0 else 0,
            "live": expires is not None and now < expires <= now + MAX_AHEAD,
        }
    return found


def claim_abandoned(regs: dict[str, dict[str, Any]], owner: object, records: list[dict[str, Any]], now: datetime) -> bool:
    """Whether a dead owner's claim is past the stall threshold, hence adoptable.

    Evidence is every canonical bead heartbeat plus the owner's registration
    expiry, or the records' `updated_at` when no usable expiry exists. A missing
    heartbeat is not a veto: a session that died before its first heartbeat
    must not hold a lane forever. A live owner, a malformed heartbeat, a
    non-coordinator claim without a heartbeat, or no parseable evidence is
    never abandoned.
    """
    if not isinstance(owner, str) or not owner:
        return False
    entry = regs.get(owner)
    if entry and entry["live"]:
        return False
    evidence: list[datetime | None] = []
    for record in records:
        notes = record.get("notes") if isinstance(record.get("notes"), str) else ""
        beats = HEARTBEAT_FIELDS_RE.findall(notes)
        if not beats and HEARTBEAT_TAG_RE.search(notes):
            return False
        evidence.extend(parse_time(stamp) for _, stamp in beats)
    if not owner.startswith("coordinator:") and not evidence:
        return False
    expiry = parse_time(entry["expires_at"]) if entry else None
    if expiry is not None and expiry <= now:
        evidence.append(expiry)
    else:
        evidence.extend(parse_time(record.get("updated_at")) for record in records)
    return bool(evidence) and all(stamp is not None and now - stamp >= STALL_THRESHOLD for stamp in evidence)


def live_coordinator_ids(kv: dict[str, Any], now: datetime) -> set[str]:
    return {cid for cid, entry in registrations(kv, now).items() if entry["live"]}


def lane_budget(kv: dict[str, Any], override: int | None = None) -> tuple[int, str]:
    """Return the global lane budget and where it came from."""
    if override is not None and override >= 1:
        return override, "flag"
    raw = kv.get(BUDGET_KEY)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_LANE_BUDGET, "default"
    if value < 1:
        return DEFAULT_LANE_BUDGET, "default"
    return value, "kv"


def new_id(runtime: str, now: datetime) -> str:
    return f"coordinator:{runtime}-{now.strftime('%Y%m%d')}-{secrets.token_hex(4)}"


def plan_registration(
    kv: dict[str, Any], coordinator_id: str, *, now: datetime, wake: timedelta, extra_lanes: int, startup: bool
) -> tuple[str, dict[str, Any] | None]:
    """Decide what `register` should do: `refuse`, `skip`, or `write` a value."""
    current = registrations(kv, now).get(coordinator_id)
    if startup and current and current["live"]:
        return "refuse", None
    value: dict[str, Any] = {"expires_at": format_time(now + wake + GRACE)}
    if extra_lanes > 0:
        value["extra_lanes"] = extra_lanes
    if current and current["live"] and current["extra_lanes"] == extra_lanes:
        stored = parse_time(current["expires_at"])
        if stored is not None and stored >= now + wake + RENEW_MARGIN:
            return "skip", None
    return "write", value


def heartbeat_block(coordinator_id: str, now: datetime) -> str:
    return f"[beads-heartbeat]\nowner={coordinator_id}\nlast_heartbeat_at={format_time(now)}\n[/beads-heartbeat]"


def plan_heartbeat(notes: str | None, coordinator_id: str, *, now: datetime, min_age: timedelta) -> tuple[str, str | None]:
    """Return ("fresh", None), ("malformed", None) or ("write", new_notes).

    A write leaves exactly one canonical block. Other note content is preserved
    in order; the block goes where the first existing block was, or at the end.
    Any heartbeat tag outside a canonical block is "malformed": rewriting around
    it would either drop notes or leave a stale block that cleanup reads first.
    """
    notes = notes or ""
    blocks = list(HEARTBEAT_BLOCK_RE.finditer(notes))
    if len(HEARTBEAT_TAG_RE.findall(notes)) != 2 * len(blocks):
        return "malformed", None
    if len(blocks) == 1:
        fields = HEARTBEAT_FIELDS_RE.search(blocks[0].group(0))
        last = parse_time(fields.group(2)) if fields else None
        if fields and fields.group(1) == coordinator_id and last and timedelta(0) <= now - last < min_age:
            return "fresh", None
    block = heartbeat_block(coordinator_id, now)
    if not blocks:
        body = notes.rstrip("\n")
        return "write", f"{body}\n\n{block}" if body else block
    head = notes[: blocks[0].start()].rstrip("\n")
    tail = HEARTBEAT_BLOCK_RE.sub("\n", notes[blocks[0].start():]).strip("\n")
    parts = [part for part in (head, block, tail) if part]
    return "write", "\n\n".join(parts)


def classify_failure(returncode: int | None, stderr: str | None) -> str:
    """Name a failed command's cause with a sanitized code.

    `returncode` is the exit status, or None when the command never started.
    Only `server-timeout` is worth retrying.
    """
    message = (stderr or "").lower()
    if returncode is not None and any(marker in message for marker in REFUSED_MARKERS):
        return "connection-refused"
    if returncode is not None and any(marker in message for marker in TRANSIENT_MARKERS):
        return "server-timeout"
    return "command-failed"


def last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:DETAIL_LIMIT] if lines else ""


def failure_text(result: subprocess.CompletedProcess[str]) -> str:
    # bd reports most errors on stderr; fall back to stdout when it stays silent.
    return result.stderr if (result.stderr or "").strip() else (result.stdout or "")


def bd(argv: list[str], repo_root: Path, actor: str) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run one `bd` call, retrying transient server errors; return it and the attempt count."""
    attempts = 0
    while True:
        attempts += 1
        result = subprocess.run(
            ["bd", "-C", str(repo_root), "--actor", actor, *argv],
            capture_output=True,
            cwd=repo_root,
            text=True,
            check=False,
        )
        retryable = classify_failure(result.returncode, failure_text(result)) == "server-timeout"
        if result.returncode == 0 or not retryable or attempts > len(RETRY_DELAYS):
            return result, attempts
        sleep(RETRY_DELAYS[attempts - 1])


def failure(step: str, coordinator_id: str, result: subprocess.CompletedProcess[str], attempts: int) -> dict[str, Any]:
    text = failure_text(result)
    return {
        "action": "failed",
        "attempts": attempts,
        "detail": last_line(text),
        "error": classify_failure(result.returncode, text),
        "id": coordinator_id,
        "step": step,
    }


def read_notes(bead: str, repo_root: Path, actor: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (record, None) or (None, failure-payload) for `bd show <bead>`."""
    result, attempts = bd(["show", bead, "--json"], repo_root, actor)
    if result.returncode != 0:
        return None, failure("show", actor, result, attempts)
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        rows = None
    record = rows[0] if isinstance(rows, list) and rows else rows
    if not isinstance(record, dict) or record.get("id") != bead:
        return None, {"action": "failed", "attempts": attempts, "detail": "", "error": "invalid-json", "id": actor, "step": "show"}
    return record, None


def heartbeat(repo_root: Path, coordinator_id: str, bead: str, *, now: datetime, min_age: timedelta) -> tuple[dict[str, Any], int]:
    for _ in range(HEARTBEAT_WRITE_ATTEMPTS):
        record, error = read_notes(bead, repo_root, coordinator_id)
        if error:
            return {**error, "bead": bead}, 1
        if record.get("assignee") != coordinator_id:
            return {"action": "refused", "error": "not-assignee", "id": coordinator_id, "bead": bead}, 13
        action, notes = plan_heartbeat(record.get("notes"), coordinator_id, now=now, min_age=min_age)
        if action == "fresh":
            return {"action": "fresh", "id": coordinator_id, "bead": bead}, 0
        if action == "malformed":
            return {"action": "refused", "error": "malformed-heartbeat", "id": coordinator_id, "bead": bead}, 1
        latest, error = read_notes(bead, repo_root, coordinator_id)
        if error:
            return {**error, "bead": bead}, 1
        if (latest.get("notes") or "") != (record.get("notes") or ""):
            continue
        result, attempts = bd(
            ["update", bead, "--notes", notes, "--if-assignee", coordinator_id, "--json"], repo_root, coordinator_id
        )
        if result.returncode == 13:
            return {"action": "refused", "error": "not-assignee", "id": coordinator_id, "bead": bead}, 13
        if result.returncode != 0:
            return {**failure("update", coordinator_id, result, attempts), "bead": bead}, 1
        return {"action": "written", "id": coordinator_id, "bead": bead, "last_heartbeat_at": format_time(now)}, 0
    return {"action": "failed", "error": "notes-changing", "id": coordinator_id, "bead": bead, "step": "update"}, 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Write this coordinator's registry entry.")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("new-id", help="Print a fresh COORDINATOR_ID.")
    make.add_argument("--runtime", required=True, help="Short runtime name, e.g. claude or codex.")
    for name in ("register", "deregister", "heartbeat"):
        command = sub.add_parser(name)
        command.add_argument("--repo-root", required=True, type=Path)
        command.add_argument("--id", required=True, help="This coordinator's COORDINATOR_ID.")
        if name == "register":
            command.add_argument("--wake-minutes", required=True, type=int, help="Minutes until the next planned wake.")
            command.add_argument("--extra-lanes", type=int, default=0, help="Lanes the tracker cannot see (Team Lead subworkers).")
            command.add_argument("--startup", action="store_true", help="Refuse if this id is already live.")
        if name == "heartbeat":
            command.add_argument("--bead", required=True, help="Held bead whose notes carry the heartbeat.")
            command.add_argument("--min-age-minutes", type=int, default=5,
                                 help="Skip the write while this id's block is younger than this.")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    if args.command == "new-id":
        if not RUNTIME_RE.fullmatch(args.runtime):
            print(json.dumps({"error": "invalid-runtime"}))
            return 2
        print(new_id(args.runtime, now))
        return 0

    if not args.id.startswith("coordinator:"):
        print(json.dumps({"error": "id-must-start-with-coordinator"}))
        return 2
    key = REGISTRY_PREFIX + args.id
    repo_root = args.repo_root.resolve()
    if args.command == "heartbeat":
        payload, code = heartbeat(
            repo_root, args.id, args.bead, now=now, min_age=timedelta(minutes=max(args.min_age_minutes, 0))
        )
        print(json.dumps(payload))
        return code
    if args.command == "deregister":
        result, attempts = bd(["kv", "clear", key], repo_root, args.id)
        if result.returncode != 0:
            print(json.dumps(failure("kv-clear", args.id, result, attempts)))
            return 1
        print(json.dumps({"action": "cleared", "id": args.id}))
        return 0

    listing, attempts = bd(["kv", "list", "--json"], repo_root, args.id)
    if listing.returncode != 0:
        print(json.dumps(failure("kv-list", args.id, listing, attempts)))
        return 1
    try:
        kv = json.loads(listing.stdout)
    except json.JSONDecodeError:
        kv = None
    if not isinstance(kv, dict):
        print(json.dumps({"action": "failed", "attempts": attempts, "detail": "", "error": "invalid-json", "id": args.id, "step": "kv-list"}))
        return 1
    action, value = plan_registration(
        kv,
        args.id,
        now=now,
        wake=timedelta(minutes=max(args.wake_minutes, 0)),
        extra_lanes=max(args.extra_lanes, 0),
        startup=args.startup,
    )
    if action == "refuse":
        print(json.dumps({"action": "refused", "error": "id-already-live", "id": args.id}))
        return 3
    if action == "skip":
        print(json.dumps({"action": "skipped", "id": args.id}))
        return 0
    result, attempts = bd(["kv", "set", key, json.dumps(value, separators=(",", ":"))], repo_root, args.id)
    if result.returncode != 0:
        print(json.dumps({**failure("kv-set", args.id, result, attempts), **(value or {})}))
        return 1
    print(json.dumps({"action": "written", "id": args.id, **(value or {})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
