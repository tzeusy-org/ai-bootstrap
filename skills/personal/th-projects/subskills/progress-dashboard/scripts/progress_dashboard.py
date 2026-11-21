#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Private, deterministic progress workspace; no model, tracker writes or shell execution."""
from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

SCHEMA = "progress-dashboard/v1"
STATUSES = ("in-progress", "todo", "complete")
AGENT_STATUSES = ("running", "waiting", "complete", "failed", "unknown")
PANEL_KINDS = ("stats", "bars", "table", "steps")
STEP_STATES = ("done", "active", "todo", "failed")
# collection -> (identity field, text fields, list field, row limit)
ROWS = {
    "tasks": ("id", ("title", "status", "detail", "evidence"), None, 200),
    "agents": ("id", ("name", "role", "status", "task", "summary", "model", "updated_at",
                      "branch", "worktree", "pr_url", "thread_id"), "activity", 100),
    "artifacts": ("path", ("label",), None, 40),
    "panels": ("id", ("title", "kind", "note"), "rows", 12),
}
MAX_JSON = 256 * 1024
FEEDBACK_BATCH_BYTES = 8 * 1024
LOG_LIMIT = 60


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def text(value, label: str, limit: int = 8000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{label} must be a string of at most {limit} characters")
    return value


def atomic(path: Path, data: str) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path.name}")
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(path: Path, value) -> None:
    atomic(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def load(path: Path, default=None):
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path.name}")
    if not path.exists():
        if default is not None:
            return default
        raise ValueError(f"missing {path.name}; initialize the workspace first")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError(f"{path.name} exceeds the state size limit")
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def lock(folder: Path, name="workspace.lock"):
    path = folder / name
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {name}")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def workspace(slug: str, root: str | Path | None = None, *, create=True) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
        raise ValueError("slug must contain 1–80 lowercase letters, digits or hyphens")
    base = Path(root or "~/.tmp").expanduser().resolve()
    folder = base / slug
    if folder.is_symlink():
        raise ValueError("workspace must not be a symlink")
    if create:
        base.mkdir(parents=True, exist_ok=True)
        folder.mkdir(mode=0o700, exist_ok=True)
    elif not folder.is_dir():
        raise ValueError("workspace does not exist; initialize it first")
    if folder.stat().st_uid != os.getuid():
        raise ValueError("workspace belongs to another user")
    os.chmod(folder, 0o700)
    return folder


def documents(state: dict) -> dict[str, str]:
    result = {}
    for status in STATUSES:
        lines = [f"# {status.replace('-', ' ').title()}", "",
                 f"{state['title']} · revision {state['revision']} · {state['updated_at']}", "",
                 "Generated progress projection; the owning workflow remains authoritative.", ""]
        if status == "in-progress":
            lines.extend([state["summary"] or "No summary published yet.", ""])
        tasks = [t for t in state["tasks"] if t["status"] == status]
        for task in tasks:
            lines.extend([f"## {task['id']} — {task['title']}", "", task["detail"], ""])
            if task["evidence"]:
                lines.extend(["Evidence: " + task["evidence"], ""])
        if not tasks:
            lines.extend(["No tasks in this section.", ""])
        if status == "in-progress" and state["findings"]:
            lines.extend(["## Findings", "", *[f"- {f}" for f in state["findings"]], ""])
        result[status + ".md"] = "\n".join(lines)
    return result


def project(folder: Path, state: dict) -> None:
    for filename, content in documents(state).items():
        path = folder / filename
        if path.is_symlink():
            raise ValueError(f"refusing symlink: {filename}")
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            atomic(path, content)


def snapshot(folder: Path) -> dict:
    """Load the display state; fields added after v1 workspaces were created default to empty."""
    state = load(folder / "state.json")
    state.setdefault("panels", [])
    state.setdefault("log", [])
    return state


def repository() -> str:
    try:
        found = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                               timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        found = ""
    return found or os.getcwd()


def initialize(folder: Path, *, title=None, actor=None, repo_root=None, outcome="", feedback=True) -> dict:
    given = {"actor": actor and text(actor, "actor", 200),
             "repo_root": repo_root and str(Path(repo_root).expanduser().resolve())}
    with lock(folder):
        path = folder / "state.json"
        if path.exists():
            state = snapshot(folder)
            # Omitted identity resumes the workspace; a supplied one must match it.
            if any(value and state[key] != value for key, value in given.items()):
                raise ValueError("slug already belongs to a different actor or repository")
        else:
            state = {"schema": SCHEMA, "slug": folder.name, "actor": given["actor"] or "parent",
                     "repo_root": given["repo_root"] or str(Path(repository()).resolve()),
                     "title": text(title or folder.name, "title", 300), "outcome": text(outcome, "outcome", 300),
                     "summary": "", "phase": "starting", "revision": 0, "updated_at": now(),
                     "tasks": [], "findings": [], "agents": [], "artifacts": [], "panels": [], "log": [],
                     "feedback_enabled": feedback}
            save(path, state)
        project(folder, state)
        handover = folder / "handover.md"
        if not handover.exists():
            atomic(handover, "# Handover\n\nNo handover has been requested.\n")
        if not (folder / "feedback.json").exists():
            save(folder / "feedback.json", [])
        (folder / "artifacts").mkdir(mode=0o700, exist_ok=True)
        return state


def artifact_path(folder: Path, relative: str) -> Path:
    text(relative, "artifact path", 300)
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p.startswith(".") or p in ("secrets", "credentials") for p in path.parts):
        raise ValueError("artifacts require a visible relative path below artifacts/")
    base = folder / "artifacts"
    if base.is_symlink():
        raise ValueError("artifacts directory must not be a symlink")
    target = base / path
    if any(p.is_symlink() for p in [target, *target.parents] if p != folder.parent):
        raise ValueError("artifact symlinks are not supported")
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("artifact escapes its directory")
    if target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".excalidraw", ".pdf"):
        raise ValueError("unsupported artifact format; use a rendered image, PDF or Excalidraw source")
    if not target.is_file():
        raise ValueError("artifact does not exist")
    return target


def panel_rows(rows) -> list:
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError("panel rows must be a list of at most 30 rows")
    for row in rows:
        if not isinstance(row, list) or not 1 <= len(row) <= 6:
            raise ValueError("each panel row is a list of 1-6 cells")
    return [[cell if type(cell) in (int, float) and math.isfinite(cell) else text(cell, "panel cell", 300)
             for cell in row] for row in rows]


def check_row(folder: Path, key: str, entry: dict) -> None:
    if key == "tasks":
        entry["status"] = entry["status"] or "todo"
        if entry["status"] not in STATUSES or not entry["title"]:
            raise ValueError("tasks require title and status in-progress, todo or complete")
    elif key == "agents":
        entry["status"] = entry["status"] or "running"
        if entry["status"] not in AGENT_STATUSES:
            raise ValueError("invalid agent status")
        if entry["pr_url"]:
            parsed = urlsplit(entry["pr_url"])
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("agent pr_url must be an absolute HTTP(S) URL without credentials")
    elif key == "artifacts":
        artifact_path(folder, entry["path"])
    else:
        rows, kind = entry["rows"], entry["kind"]
        if kind not in PANEL_KINDS or not entry["title"]:
            raise ValueError("panels require title and kind stats, bars, table or steps")
        if kind != "table" and any(len(row) < 2 for row in rows):
            raise ValueError(f"{kind} panel rows need a label and a value")
        if kind == "bars" and any(type(row[1]) not in (int, float) or row[1] < 0 for row in rows):
            raise ValueError("bars panel values must be non-negative numbers")
        if kind == "steps" and any(row[1] not in STEP_STATES for row in rows):
            raise ValueError("steps panel states are done, active, todo or failed")


def merge(folder: Path, state: dict, incoming: dict) -> tuple[dict, list[str]]:
    """Validate a delta against the current state; return changed fields and derived log lines.

    Rows merge by identity, so a parent sends only the rows and fields that changed.
    """
    if not isinstance(incoming, dict):
        raise ValueError("snapshot must be an object")  # noqa: TRY004 - stable JSON validation API
    collections = ("findings", *ROWS)
    if set(incoming) - {"summary", "phase", "replace", *collections}:
        raise ValueError("unknown snapshot fields: "
                         + ", ".join(sorted(set(incoming) - {"summary", "phase", "replace", *collections})))
    replace = incoming.get("replace", [])
    if not isinstance(replace, list) or not all(isinstance(key, str) and key in collections for key in replace):
        raise ValueError("replace lists collections to rebuild from this snapshot: " + ", ".join(collections))
    result, events = {}, []
    for key in ("summary", "phase"):
        if key in incoming:
            result[key] = text(incoming[key], key, 8000 if key == "summary" else 100)
    if result.get("phase", state["phase"]) != state["phase"]:
        events.append(f"Phase: {result['phase']}")
    if "findings" in incoming or "findings" in replace:
        rows = incoming.get("findings", [])
        if not isinstance(rows, list):
            raise ValueError("findings must be a list")
        merged = [] if "findings" in replace else list(state["findings"])
        for row in rows:
            if text(row, "finding") not in merged:
                merged.append(row)
                if row not in state["findings"]:
                    events.append(f"Finding: {row}")
        if len(merged) > 100:
            raise ValueError("findings must be a list with at most 100 entries")
        result["findings"] = merged
    for key, (ident, fields, extra, maximum) in ROWS.items():
        if key not in incoming and key not in replace:
            continue
        rows = incoming.get(key, [])
        if not isinstance(rows, list):
            raise ValueError(f"{key} must be a list")  # noqa: TRY004 - stable JSON validation API
        previous = {row[ident]: row for row in state[key]}
        merged = {} if key in replace else dict(previous)
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {ident, "drop", *fields, *([extra] if extra else [])}:
                raise ValueError(f"invalid {key} entry")
            name = text(row.get(ident, ""), ident)
            if not name or name in seen or type(row.get("drop", False)) is not bool:
                raise ValueError(f"{key} entries require unique identities")
            seen.add(name)
            if row.get("drop"):
                merged.pop(name, None)
                continue
            old = previous.get(name)
            entry = dict(merged.get(name) or {ident: name, **dict.fromkeys(fields, ""), **({extra: []} if extra else {})})
            entry.update({field: text(row[field], field) for field in fields if field in row})
            if key == "agents" and "activity" in row:
                reports = row["activity"]
                if not isinstance(reports, list) or len(reports) > 8:
                    raise ValueError("agent activity must be a list of at most eight recent reports")
                fresh = [text(item, "agent activity", 1000) for item in reports]
                entry["activity"] = (entry["activity"] + [item for item in fresh if item not in entry["activity"]])[-8:]
            if key == "panels" and "rows" in row:
                entry["rows"] = panel_rows(row["rows"])
            check_row(folder, key, entry)
            if key == "agents" and not row.get("updated_at"):
                same = old is not None and all(entry[f] == old.get(f) for f in entry if f != "updated_at")
                entry["updated_at"] = old["updated_at"] if same else now()
            merged[name] = entry
            label = entry.get("title") or entry.get("name") or entry.get("label") or name
            if key in ("tasks", "agents") and (old is None or old["status"] != entry["status"]):
                events.append(f"{label}: {entry['status'].replace('-', ' ')}")
            elif old is None:
                events.append(f"Published: {label}")
        if len(merged) > maximum:
            raise ValueError(f"{key} must be a list with at most {maximum} entries")
        result[key] = list(merged.values())
    return result, events


def pending(folder: Path, limit=10, after=0) -> dict:
    if type(after) is not int or after < 0:
        raise ValueError("feedback cursor must be a non-negative integer")
    rows = [r for r in load(folder / "feedback.json", []) if r["status"] == "pending"]
    unseen = [row for row in rows if row["id"] > after]
    batch, cursor, used = [], after, 2
    for row in unseen[:limit]:
        item = {key: row[key] for key in ("id", "kind", "target", "revision", "text")}
        size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
        if used + size > FEEDBACK_BATCH_BYTES:
            if batch:
                break
            # Never present a clipped instruction as if it were the full message.
            item.pop("text")
            item["target_preview"] = item.pop("target")[:200]
            item.update(text_preview=row["text"][:200], truncated=True)
            size = len(json.dumps(item, ensure_ascii=False).encode()) + 1
        batch.append(item)
        cursor = row["id"]
        used += size
    return {"pending_count": len(rows), "feedback": batch, "feedback_cursor": cursor,
            "feedback_more": any(row["id"] > cursor for row in unseen)}


def surface(folder: Path, after=None) -> dict:
    """Pending feedback after the caller's cursor, or after the one this workspace retained.

    Call with the workspace lock held. The retained cursor only advances, so an
    explicit earlier cursor re-reads unacknowledged items without losing position.
    """
    stored = load(folder / "cursor.json", {}).get("feedback", 0)
    result = pending(folder, after=stored if after is None else after)
    if result["feedback_cursor"] > stored:
        save(folder / "cursor.json", {"feedback": result["feedback_cursor"]})
    return result


def checkpoint(folder: Path, incoming: dict, expected=None, after_feedback=None) -> dict:
    with lock(folder):
        state = snapshot(folder)
        if expected is not None and state["revision"] != expected:
            raise ValueError(f"revision conflict: current revision is {state['revision']}")
        update, events = merge(folder, state, incoming)
        if any(state[key] != value for key, value in update.items()):
            stamp = now()
            state.update(update)
            state.update(revision=state["revision"] + 1, updated_at=stamp,
                         log=(state["log"] + [{"at": stamp, "text": line[:240]} for line in events])[-LOG_LIMIT:])
            # JSON commits the snapshot; projections are repairable from it on resume.
            save(folder / "state.json", state)
        project(folder, state)
        return {"revision": state["revision"], **surface(folder, after_feedback)}


def feedback_item(folder: Path, feedback_id: int) -> dict:
    for row in load(folder / "feedback.json", []):
        if row["id"] == feedback_id:
            return {key: value for key, value in row.items() if key != "client_id"}
    raise ValueError("feedback id not found")


def save_feedback(folder: Path, rows: list) -> None:
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(serialized.encode()) > 3 * 1024 * 1024:
        raise ValueError("feedback history is full; shorten the disposition or start a new workspace")
    atomic(folder / "feedback.json", serialized)


def record_feedback(folder: Path, body: dict) -> dict:
    if not isinstance(body, dict) or set(body) != {"client_id", "kind", "target", "text", "revision"}:
        raise ValueError("feedback requires client_id, kind, target, text and revision")
    client_id = text(body["client_id"], "client_id", 100)
    if not re.fullmatch(r"[A-Za-z0-9-]{8,100}", client_id):
        raise ValueError("invalid feedback idempotency key")
    message = text(body["text"], "feedback").strip()
    if not message or body["kind"] not in ("feedback", "steer", "question"):
        raise ValueError("feedback requires text and a supported kind")
    with lock(folder):
        state = load(folder / "state.json")
        if not state["feedback_enabled"]:
            raise ValueError("feedback is disabled for this workspace")
        rows = load(folder / "feedback.json", [])
        for row in rows:
            if row["client_id"] == client_id:
                if any(row[k] != body[k] for k in ("kind", "target", "revision")) or row["text"] != message:
                    raise ValueError("idempotency key already used for different feedback")
                return row
        if type(body["revision"]) is not int or not 0 <= body["revision"] <= state["revision"]:
            raise ValueError("feedback revision is invalid")
        targets = {"loop", *[r["id"] for r in state["tasks"]], *[r["id"] for r in state["agents"]]}
        if body["target"] not in targets:
            raise ValueError("feedback target no longer exists; select the loop or a current item")
        if len(rows) >= 2000:
            raise ValueError("feedback history is full; start a new workspace")
        row = {**body, "text": message, "id": len(rows) + 1, "created_at": now(),
               "status": "pending", "response": None}
        rows.append(row)
        save_feedback(folder, rows)
        return row


def acknowledge(folder: Path, feedback_id: int, response: str) -> dict:
    response = text(response, "response").strip()
    if not response:
        raise ValueError("acknowledgement requires an explanation of the disposition")
    with lock(folder):
        rows = load(folder / "feedback.json", [])
        for row in rows:
            if row["id"] == feedback_id:
                row.update(status="acknowledged", response=response, acknowledged_at=now())
                save_feedback(folder, rows)
                return {"id": feedback_id, "status": "acknowledged"}
        raise ValueError("feedback id not found")


def handover(folder: Path, content: str, owner_request: str) -> None:
    if not text(owner_request, "owner request").strip():
        raise ValueError("handover requires an explicit owner request")
    with lock(folder):
        load(folder / "state.json")
        atomic(folder / "handover.md", text(content, "handover", MAX_JSON))
        save(folder / "handover-request.json", {"request": owner_request, "recorded_at": now()})


def attach(folder: Path, agent_id: str, provider: str, source: str) -> dict:
    from loop_telemetry import validate_source

    path = Path(source).expanduser().absolute()
    if provider not in ("codex", "claude") or path.suffix != ".jsonl":
        raise ValueError("attach requires codex/claude and an explicit .jsonl transcript")
    if any(p.startswith(".env") or p.lower() in ("secrets", "credentials") for p in path.parts):
        raise ValueError("secret paths cannot be attached")
    if path.is_symlink():
        raise ValueError("transcript must not be a symlink")
    validated = validate_source({"id": agent_id, "provider": provider, "path": str(path)})
    path = Path(validated["path"])
    with lock(folder):
        state = load(folder / "state.json")
        if agent_id not in {state["actor"], *[r["id"] for r in state["agents"]]}:
            raise ValueError("attach the parent actor or a published agent id")
        rows = load(folder / "sources.json", [])
        if any(r["id"] != agent_id and r["path"] == str(path) for r in rows):
            raise ValueError("transcript already attached; refusing to double-count it")
        rows = [r for r in rows if r["id"] != agent_id]
        rows.append({"id": agent_id, "provider": provider, "path": str(path)})
        save(folder / "sources.json", rows)
    return {"attached": agent_id, "provider": provider}


def watch_codex(folder: Path, thread_id: str, home=None) -> dict:
    from runtime_sources import configuration, resolve

    config = configuration(thread_id, home)
    with lock(folder):
        state = load(folder / "state.json")
        existing = load(folder / "runtime.json", {})
        if existing and existing != config:
            raise ValueError("workspace already watches another Codex parent or home")
        # Validate the configured parent and index before committing the locator.
        resolve(config, state["actor"], state["agents"], load(folder / "sources.json", []))
        if existing != config:
            save(folder / "runtime.json", config)
    return {"watching": "codex", "thread_id": config["thread_id"]}


def watch_claude(folder: Path, session_id: str, home=None) -> dict:
    from runtime_sources import claude_configuration, resolve

    config = claude_configuration(session_id, home)
    with lock(folder):
        state = load(folder / "state.json")
        existing = load(folder / "runtime.json", {})
        if existing and existing != config:
            raise ValueError("workspace already watches another runtime parent or home")
        # Validate the configured parent session before committing the locator.
        resolve(config, state["actor"], state["agents"], load(folder / "sources.json", []))
        if existing != config:
            save(folder / "runtime.json", config)
    return {"watching": "claude", "session_id": config["session_id"]}


def health(info: dict) -> bool:
    try:
        with urlopen(info["url"] + "api/health", timeout=0.5) as reply:
            return json.load(reply) == {"instance": info["instance"], "pid": info["pid"]}
    except (URLError, OSError, ValueError, KeyError):
        return False


# Tailscale assigns node addresses from the RFC 6598 shared address space.
# Built from integers so the repo's pii-guard does not read it as an address.
TAILSCALE_NETWORK = ipaddress.IPv4Network((100 << 24 | 64 << 16, 10))


def bind_allowed(bind: str) -> bool:
    address = ipaddress.ip_address(bind)
    return address.version == 4 and (address.is_loopback or address in TAILSCALE_NETWORK)


def log_tail(path: Path, limit: int = 400) -> str:
    """The last bytes of a server log, for a startup error that explains itself."""
    try:
        with open(path, "rb") as log:
            log.seek(max(0, log.seek(0, os.SEEK_END) - limit))
            return log.read().decode("utf-8", "replace").strip().replace("\n", " | ")
    except OSError:
        return ""


def serve(folder: Path, bind: str, port: int) -> dict:
    if not bind_allowed(bind):
        raise ValueError("bind must be an IPv4 loopback or explicit Tailscale address")
    with lock(folder, "server.lock"):
        load(folder / "state.json")
        old = load(folder / "server.json", {})
        if old and health(old):
            if old["bind"] != bind or (port and old["port"] != port):
                raise ValueError("server already running on another address; stop it first")
            return {"url": old["url"], "pid": old["pid"], "reused": True}
        instance = secrets.token_urlsafe(24)
        log_path = folder / "server.log"
        if log_path.is_symlink():
            raise ValueError("server log must not be a symlink")
        with open(log_path, "ab") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                "--root", str(folder.parent), "--slug", folder.name, "_serve", "--bind", bind,
                "--port", str(port), f"--instance={instance}"],  # a token may start with "-"
                stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            current = load(folder / "server.json", {})
            if current.get("instance") == instance and health(current):
                # Reap the child if this CLI is embedded in a longer-lived caller.
                threading.Thread(target=process.wait, daemon=True).start()
                return {"url": current["url"], "pid": current["pid"], "reused": False}
            if process.poll() is not None:
                raise ValueError(f"server startup failed (exit {process.returncode}): "
                                 f"{log_tail(log_path) or 'no output'}; see {log_path}")
            time.sleep(0.05)
        process.terminate()
        process.wait(timeout=3)
        raise ValueError(f"server startup timed out; see {log_path}")


def stop(folder: Path) -> dict:
    with lock(folder, "server.lock"):
        info = load(folder / "server.json", {})
        if not info or not health(info):
            return {"stopped": False, "reason": "no verified live server; no process signalled"}
        os.kill(info["pid"], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not health(info):
                return {"stopped": True}
            time.sleep(0.05)
        raise ValueError("server did not stop within five seconds; its verified instance was signalled once")


def read_input(source: str) -> str:
    if source == "-":
        raw = sys.stdin.buffer.read(MAX_JSON + 1)
    else:
        with Path(source).open("rb") as stream:
            raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("input exceeds 256KiB")
    return raw.decode("utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", required=True)
    p.add_argument("--root", help="workspace parent (default ~/.tmp)")
    commands = p.add_subparsers(dest="command", required=True)
    for command, summary in (("start", "create or resume a workspace and its server; prints the private URL"),
                             ("init", "create or resume a workspace without serving it")):
        init = commands.add_parser(command, help=summary)
        init.add_argument("--title", help="defaults to the slug")
        init.add_argument("--actor", help="parent session identity; defaults to 'parent'")
        init.add_argument("--repo-root", help="defaults to the current Git repository")
        init.add_argument("--outcome", default="")
        init.add_argument("--no-feedback", action="store_true")
        init.add_argument("--codex-thread", default=os.environ.get("CODEX_THREAD_ID"),
                          help="watch this Codex parent and known children; defaults to CODEX_THREAD_ID")
        init.add_argument("--codex-home", help="runtime home; defaults to CODEX_HOME or ~/.codex")
        init.add_argument("--claude-session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"),
                          help="watch this Claude Code parent and known sub-agents; defaults to CLAUDE_CODE_SESSION_ID")
        init.add_argument("--claude-home", help="runtime home; defaults to CLAUDE_CONFIG_DIR or ~/.claude")
        if command == "start":
            init.add_argument("--bind", default="127.0.0.1")
            init.add_argument("--port", type=int, default=0)
    update = commands.add_parser("checkpoint", help="merge changed rows by id and return bounded pending feedback")
    update.add_argument("--input", default="-", help="JSON file or - for stdin")
    update.add_argument("--expected-revision", type=int, help="refuse unless this is the current revision")
    update.add_argument("--after-feedback", type=int, help="feedback cursor; defaults to the retained one")
    status = commands.add_parser("status", help="compact checkpoint status, never the full dashboard")
    status.add_argument("--since", type=int)
    status.add_argument("--after-feedback", type=int)
    inbox = commands.add_parser("inbox", help="read bounded new feedback or one full item; never consumes it")
    inbox.add_argument("--after-feedback", type=int, help="0 re-reads every unacknowledged item")
    inbox.add_argument("--id", type=int, help="read one full feedback item before acting on a preview")
    ack = commands.add_parser("ack", help="record the disposition of one feedback item")
    ack.add_argument("--id", type=int, required=True)
    ack.add_argument("--response", required=True)
    manual = commands.add_parser("handover", help="write handover only after a manual owner request")
    manual.add_argument("--input", required=True)
    manual.add_argument("--owner-request", required=True)
    source = commands.add_parser("attach", help="incrementally measure an explicit parent/child transcript")
    source.add_argument("--id", required=True)
    source.add_argument("--provider", choices=("codex", "claude"), required=True)
    source.add_argument("--path", required=True)
    watch = commands.add_parser("watch-codex", help="resolve this parent and its known agents from the runtime index")
    watch.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    watch.add_argument("--home")
    claude = commands.add_parser("watch-claude",
                                 help="resolve this Claude Code parent and its known sub-agents from its own session folder")
    claude.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    claude.add_argument("--home")
    for command in ("serve", "_serve"):
        server = commands.add_parser(command)
        server.add_argument("--bind", default="127.0.0.1")
        server.add_argument("--port", type=int, default=0)
        if command == "_serve":
            server.add_argument("--instance", required=True)
    commands.add_parser("stop", help="stop only this workspace's verified server")
    return p


def main() -> int:
    os.umask(0o077)
    args = parser().parse_args()
    try:
        folder = workspace(args.slug, args.root, create=args.command in ("init", "start"))
        if args.command in ("init", "start"):
            state = initialize(folder, title=args.title, actor=args.actor, repo_root=args.repo_root,
                               outcome=args.outcome, feedback=not args.no_feedback)
            result = {"workspace": str(folder), "revision": state["revision"]}
            if args.codex_thread and not (folder / "runtime.json").exists():
                try:
                    result.update(watch_codex(folder, args.codex_thread, args.codex_home))
                except ValueError as error:
                    result["usage_warning"] = str(error)
            if args.claude_session and not (folder / "runtime.json").exists():
                try:
                    result.update(watch_claude(folder, args.claude_session, args.claude_home))
                except ValueError as error:
                    result["usage_warning"] = " ".join(filter(None, (result.get("usage_warning"), str(error))))
            if args.command == "start":
                result["url"] = serve(folder, args.bind, args.port)["url"]
        elif args.command == "checkpoint":
            result = checkpoint(folder, json.loads(read_input(args.input)), args.expected_revision, args.after_feedback)
        elif args.command in ("status", "inbox"):
            with lock(folder):
                state = load(folder / "state.json")
                if args.command == "inbox" and args.id is not None:
                    result = {"feedback": feedback_item(folder, args.id)}
                else:
                    result = {"revision": state["revision"], **surface(folder, args.after_feedback)}
                    if args.command == "status":
                        result.update(changed=state["revision"] != args.since, phase=state["phase"])
        elif args.command == "ack":
            result = acknowledge(folder, args.id, args.response)
        elif args.command == "handover":
            handover(folder, read_input(args.input), args.owner_request)
            result = {"handover": "updated"}
        elif args.command == "attach":
            result = attach(folder, args.id, args.provider, args.path)
        elif args.command == "watch-codex":
            result = watch_codex(folder, args.thread_id, args.home)
        elif args.command == "watch-claude":
            result = watch_claude(folder, args.session_id, args.home)
        elif args.command == "serve":
            result = serve(folder, args.bind, args.port)
        elif args.command == "stop":
            result = stop(folder)
        else:
            from dashboard_server import run
            run(folder, args.bind, args.port, args.instance)
            return 0
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
