#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Block until the owner finishes a user-run payload, then print one compact line.

Meant to run as a background command whose exit re-invokes the agent session
(Claude Code: Bash run_in_background). It idles silently and polls the slug
dir; it never touches run.sh or its run, so a session that is not watching
changes nothing. The line is computed here, deterministically:

  USER_RUN_DONE {"slug":…,"run":…,"state":"done","exit":0,"secs":42,"log":…,"artifacts":3,…}

`state` is done, not-started (su failed; the payload never ran) or died (no
exit code and the run's lock is free). RESULT lines come along (bounded);
a log `tail` only when the run did not succeed (<=10 lines, <=1.5 KB).

A run counts only if it started at or after run.sh was last modified (so a
stale run of an earlier payload is skipped) and after the run this command
last delivered (recorded in <slug>/.delivered). --latest drops both gates.

Exit codes: 0 delivered; 3 --timeout reached (prints USER_RUN_WAITING);
2 bad invocation or no such slug dir.

Usage: watch.py <slug> [--timeout N] [--interval S] [--latest]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path

TAIL_LINES = 10
TAIL_BYTES = 1500
LINE_CHARS = 300
RESULT_LINES = 5
ARTIFACT_NAMES = 8
READ_BYTES = 64 * 1024
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
NOISE = re.compile(r"^(== |\(eval\):\d*: ?can't change option: zle)")
RUN_ID = re.compile(r"^\d{14}$")


def latest_run(d: Path) -> str | None:
    try:
        rid = Path(os.readlink(d / "latest.log")).stem
    except OSError:
        return None
    return rid if RUN_ID.match(rid) else None


def lock_free(d: Path) -> bool:
    """True when no run holds the slug lock. Probes only when a run lacks an exit code."""
    try:
        fd = os.open(d / ".lock", os.O_RDONLY)
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def log_lines(log: Path) -> list[str] | None:
    try:
        with log.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - READ_BYTES))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [ANSI.sub("", ln).replace("\r", "").rstrip() for ln in raw.split("\n")]
    if size > READ_BYTES:
        lines = lines[1:]  # first line is likely cut
    return lines


def clip(s: str, n: int = LINE_CHARS) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def tail_of(lines: list[str]) -> list[str]:
    keep = [clip(ln) for ln in lines if ln.strip() and not NOISE.match(ln)][-TAIL_LINES:]
    while keep and len("\n".join(keep).encode()) > TAIL_BYTES:
        keep.pop(0)
    return keep


def report(d: Path, rid: str, state: str) -> dict:
    done = {}
    text = read_text(d / f"{rid}.done.json")
    if text:
        try:
            done = json.loads(text)
        except ValueError:
            done = {}
    out: dict = {"slug": d.name, "run": rid, "state": state}
    code = done.get("exit")
    if code is None:
        raw = read_text(d / f"{rid}.exit")
        code = int(raw) if raw and raw.lstrip("-").isdigit() else None
    if code is not None:
        out["exit"] = code
    for k in ("secs", "signal"):
        if done.get(k) not in (None, ""):
            out[k] = done[k]
    log = d / f"{rid}.log"
    out["log"] = str(log)
    art = d / "artifacts" / rid
    try:
        names = sorted(p.name for p in art.iterdir())
    except OSError:
        names = []
    out["artifacts"] = len(names)
    if names:
        out["artifact_names"] = names[:ARTIFACT_NAMES]
    lines = log_lines(log)
    if lines is None:
        out["log_unreadable"] = True
        return out
    results = [clip(ln[len("RESULT"):].strip()) for ln in lines if ln.startswith("RESULT")]
    if results:
        out["result"] = results[-RESULT_LINES:]
    if state != "done" or code != 0:
        out["tail"] = tail_of(lines)
    return out


def check(d: Path, floor: str, delivered: str, latest_only: bool) -> tuple[str, str] | None:
    """Return (run_id, state) once the newest eligible run has finished."""
    rid = latest_run(d)
    if not rid:
        return None
    if not latest_only and (rid < floor or rid <= delivered):
        return None
    if not (d / f"{rid}.done.json").exists():
        # The harness writes .exit, then done.json, then releases the lock.
        # Without done.json, wait for the lock: then .exit alone means a
        # harness scaffolded before done.json existed, and neither means the
        # run died. Re-check after the probe to close the race.
        if not lock_free(d):
            return None
        if not (d / f"{rid}.done.json").exists() and not (d / f"{rid}.exit").exists():
            return rid, "died"
    if not (d / f"{rid}.started").exists():
        return rid, "not-started"
    return rid, "done"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--timeout", type=int, default=0, help="give up after N seconds (exit 3); default: wait indefinitely")
    ap.add_argument("--interval", type=float, default=2.0, help="poll interval in seconds")
    ap.add_argument("--latest", action="store_true", help="deliver the newest finished run, ignoring run.sh mtime and .delivered")
    a = ap.parse_args(argv)
    base = Path(os.environ.get("USER_RUN_BASE") or Path.home() / ".tmp")
    d = base / a.slug
    if not (d / "run.sh").is_file():
        print(f"no user-run slug at {d}", file=sys.stderr)
        return 2
    deadline = time.time() + a.timeout if a.timeout else None
    while True:
        try:
            floor = time.strftime("%Y%m%d%H%M%S", time.localtime((d / "run.sh").stat().st_mtime))
        except OSError:
            floor = ""
        delivered = read_text(d / ".delivered") or ""
        hit = check(d, floor, delivered, a.latest)
        if hit:
            rid, state = hit
            try:
                tmp = d / f".delivered.{os.getpid()}"
                tmp.write_text(rid + "\n")
                os.replace(tmp, d / ".delivered")
            except OSError:
                pass  # best-effort: a re-arm may then re-deliver this run
            print("USER_RUN_DONE " + json.dumps(report(d, rid, state), ensure_ascii=False, separators=(",", ":")))
            return 0
        if deadline and time.time() >= deadline:
            rid = latest_run(d)
            state = "running" if rid and not lock_free(d) else "waiting"
            print("USER_RUN_WAITING " + json.dumps({"slug": a.slug, "state": state, "latest": rid}, separators=(",", ":")))
            return 3
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
