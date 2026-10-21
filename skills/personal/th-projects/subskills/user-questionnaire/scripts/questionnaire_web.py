#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Serve an owner-questionnaire packet as a local web form and ingest answers.

Deterministic, stdlib-only. One session publishes the packet; the owner
answers in a browser; any later session ingests the answers back into the
packet markdown and continues the normal apply workflow.

Subcommands
  serve   <packet.md>  validate, start (or reuse) a background server; print the URL
  render  <packet.md>  write a read-only preview page to stdout or --out (no server)
  status  <packet.md>  print answer progress; exit 0 only when submitted
  ingest  <packet.md>  write recorded answers into the packet markdown
  stop    <packet.md>  stop the background server for this packet

State lives under $QUESTIONNAIRE_STATE_ROOT (default ~/.tmp/user-questionnaire)
in a directory derived from the packet's absolute path, so any session can
find it from the packet alone: answers.json, server.json, server.log.
answers.json on disk is the only copy of the answers. The server and `ingest`
each read-modify-write it under one advisory file lock (answers.lock), so a
running server and a concurrent ingest never lose each other's writes.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import questionnaire_page as page  # noqa: E402
import validate_questionnaire as vq  # noqa: E402

DEFAULT_STATE_ROOT = Path("~/.tmp/user-questionnaire")
STATE_FOR_KIND = {"pick": "Agreed", "edit": "Needs-rework", "hold": "Held", "reject": "Rejected"}
MAX_BODY_BYTES = 64 * 1024
PENDING = "Pending owner response"


# ── state ─────────────────────────────────────────────────────────────────────


def state_root() -> Path:
    return Path(os.environ.get("QUESTIONNAIRE_STATE_ROOT", str(DEFAULT_STATE_ROOT))).expanduser()


def state_dir_for(packet: Path, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    resolved = packet.expanduser().resolve()
    digest = hashlib.sha1(str(resolved).encode()).hexdigest()[:8]
    return state_root() / f"{resolved.stem}-{digest}"


def packet_digest(packet: Path) -> str:
    return hashlib.sha256(packet.read_bytes()).hexdigest()


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def empty_answers(packet: Path) -> dict[str, Any]:
    return {
        "schema": 2,
        "packet": str(packet.expanduser().resolve()),
        "packet_sha256": packet_digest(packet),
        "items": {},
        "completed_at": None,
        "apply_now": None,
    }


def load_answers(state_dir: Path, packet: Path) -> dict[str, Any]:
    path = state_dir / "answers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return empty_answers(packet)


def save_answers(state_dir: Path, answers: dict[str, Any]) -> None:
    atomic_write(state_dir / "answers.json", json.dumps(answers, indent=2, sort_keys=True) + "\n")


@contextmanager
def answers_lock(state_dir: Path) -> Iterator[None]:
    """Exclusive lock for a read-modify-write of answers.json, across threads and processes."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "answers.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AnswerError(ValueError):
    """A request the answer model rejects; the message is shown to the owner."""


def record_answer(
    answers: dict[str, Any], options: dict[str, set[str]], body: dict[str, Any], actor: str, digest: str
) -> None:
    """Apply one ``/answer`` request: set an item's answer, or clear it with ``kind: null``.

    ``options`` maps each item id to the option labels the packet offers for it.
    """
    if answers.get("completed_at"):
        raise AnswerError("review already submitted; reopen it first")
    item_id = body.get("id")
    if item_id not in options:
        raise AnswerError(f"unknown item {item_id!r}")
    kind = body.get("kind")
    if kind is None:
        answers["items"].pop(item_id, None)
        return
    if kind not in STATE_FOR_KIND:
        raise AnswerError(f"kind must be one of {sorted(STATE_FOR_KIND)} or null")
    choice = str(body.get("choice") or "").strip()
    if kind == "pick" and choice not in options[item_id]:
        raise AnswerError(f"a pick needs one of this item's options: {', '.join(sorted(options[item_id]))}")
    if not answers["items"]:
        # The first answer pins the packet version the owner is reading.
        answers["packet_sha256"] = digest
    answers["items"][item_id] = {
        "kind": kind,
        "choice": choice if kind in ("pick", "edit") else "",
        "note": str(body.get("note") or "").strip(),
        "recorded_at": now_iso(),
        "actor": actor,
    }


def record_completion(answers: dict[str, Any], item_ids: set[str], body: dict[str, Any], actor: str) -> None:
    """Apply one ``/complete`` request: the apply-offer answer and/or submit/reopen."""
    if "apply_now" in body:
        if answers.get("completed_at"):
            raise AnswerError("review already submitted; reopen it first")
        if not isinstance(body["apply_now"], bool):
            raise AnswerError("apply_now must be true or false")
        answers["apply_now"] = body["apply_now"]
    if "completed" in body:
        if body["completed"]:
            missing = sorted(item_ids - set(answers["items"]))
            empty_edits = sorted(i for i, a in answers["items"].items() if a["kind"] == "edit" and not a["choice"])
            if missing:
                raise AnswerError(f"answer every item first; unanswered: {', '.join(missing)}")
            if empty_edits:
                raise AnswerError(f"describe the edited option for: {', '.join(empty_edits)}")
            if not isinstance(answers.get("apply_now"), bool):
                raise AnswerError("answer the apply question first")
            answers["completed_at"] = now_iso()
            answers["completed_actor"] = actor
        else:
            answers["completed_at"] = None
            answers.pop("completed_actor", None)


# ── server ────────────────────────────────────────────────────────────────────


class QuestionnaireServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, bind: tuple[str, int], packet: Path, state_dir: Path, token: str):
        super().__init__(bind, Handler)
        self.packet = packet
        self.state_dir = state_dir
        self.token = token
        self.prefix = f"/q/{token}"
        self._model: tuple[str, page.Packet] | None = None

    def model(self) -> tuple[str, page.Packet]:
        """Current packet digest and view model, re-parsed when the file changes."""
        digest = packet_digest(self.packet)
        if self._model is None or self._model[0] != digest:
            self._model = (digest, page.parse_packet(self.packet.read_text(encoding="utf-8")))
        return self._model


class Handler(BaseHTTPRequestHandler):
    server: QuestionnaireServer

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _route(self) -> str | None:
        """Path below the token prefix, or None when the token does not match."""
        path = self.path.split("?", 1)[0]
        prefix = self.server.prefix
        if path != prefix and not path.startswith(prefix + "/"):
            return None
        return path[len(prefix) :] or "/"

    def do_GET(self) -> None:  # noqa: N802
        route = self._route()
        if route is None:
            return self._send(404, b"not found", "text/plain")
        with answers_lock(self.server.state_dir):
            answers = load_answers(self.server.state_dir, self.server.packet)
            digest, model = self.server.model()
        if route == "/":
            html = page.render_page(
                model,
                answers,
                api=self.server.prefix + "/api",
                live=True,
                packet_sha256=digest,
                images=page.ImageResolver(self.server.packet.parent),
            )
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if route == "/api/state":
            return self._json(200, {"answers": answers, "packet_sha256": digest})
        return self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        route = self._route()
        if route not in ("/api/answer", "/api/complete"):
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            return self._json(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "expected a json object"})
        actor = f"owner via questionnaire web form (token-gated, from {self.client_address[0]})"
        with answers_lock(self.server.state_dir):
            answers = load_answers(self.server.state_dir, self.server.packet)
            digest, model = self.server.model()
            options = {item.id: {option.label for option in item.options} for item in model.items}
            try:
                if route == "/api/answer":
                    record_answer(answers, options, body, actor, digest)
                else:
                    record_completion(answers, set(options), body, actor)
            except AnswerError as error:
                return self._json(409, {"error": str(error)})
            save_answers(self.server.state_dir, answers)
        return self._json(200, {"answers": answers, "packet_sha256": digest})


def make_server(packet: Path, state_dir: Path, bind: str, port: int, token: str | None = None) -> QuestionnaireServer:
    return QuestionnaireServer((bind, port), packet, state_dir, token or secrets.token_urlsafe(12))


def public_host(override: str | None = None) -> str:
    if override:
        return override
    if os.environ.get("QUESTIONNAIRE_HOST"):
        return os.environ["QUESTIONNAIRE_HOST"]
    try:
        out = subprocess.run(["tailscale", "status", "--self", "--json"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            name = json.loads(out.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
            if name:
                return name
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    fqdn = socket.getfqdn()
    return fqdn if "." in fqdn else socket.gethostname()


def _pid_alive(pid: int) -> bool:
    try:  # reap if it is our own exited child, otherwise a zombie still answers kill(0)
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int, timeout: float = 5.0) -> bool:
    """SIGTERM, wait, then SIGKILL; True when the process is gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    time.sleep(0.1)
    return not _pid_alive(pid)


def read_server_info(state_dir: Path) -> dict[str, Any] | None:
    """server.json when its process is alive and answers on its own token, else None."""
    path = state_dir / "server.json"
    if not path.exists():
        return None
    info = json.loads(path.read_text(encoding="utf-8"))
    if not _pid_alive(int(info.get("pid", -1))):
        return None
    host = "127.0.0.1" if info.get("bind") in ("0.0.0.0", "", None) else info["bind"]
    try:
        with urlopen(f"http://{host}:{info['port']}/q/{info['token']}/api/state", timeout=2) as response:
            json.load(response)  # any JSON reply on its own token identifies our server, of any version
    except (OSError, ValueError):
        return None
    return info


# ── commands ──────────────────────────────────────────────────────────────────


def cmd_render(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    images = page.ImageResolver(packet.parent)
    html = page.render_page(
        page.parse_packet(packet.read_text(encoding="utf-8")),
        load_answers(state_dir, packet),
        api="",
        live=False,
        packet_sha256=packet_digest(packet),
        images=images,
    )
    for ref in images.missing:
        print(f"WARNING: diagram not inlined (missing, outside the packet directory, or unsupported): {ref}", file=sys.stderr)
    if args.out:
        atomic_write(Path(args.out), html)
        print(args.out)
    else:
        sys.stdout.write(html)
    return 0


def _run_foreground(args: argparse.Namespace, packet: Path, state_dir: Path) -> int:
    httpd = make_server(packet, state_dir, args.bind, args.port, args.token)
    port = httpd.server_address[1]
    host = public_host(args.host)
    info = {
        "pid": os.getpid(),
        "port": port,
        "bind": args.bind,
        "host": host,
        "token": httpd.token,
        "url": f"http://{host}:{port}{httpd.prefix}/",
        "packet": str(packet),
        "state_dir": str(state_dir),
        "started_at": now_iso(),
    }
    atomic_write(state_dir / "server.json", json.dumps(info, indent=2) + "\n")
    print(info["url"], flush=True)
    # shutdown() blocks until serve_forever() returns, so it must not run on
    # the thread that is inside serve_forever(); hand it to a helper thread.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    if not packet.exists():
        print(f"ERROR: packet not found: {packet}", file=sys.stderr)
        return 2
    errors = vq.validate_packet(packet.read_text(encoding="utf-8"), require_review_ready=True)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print("ERROR: only a review-ready packet is published; preview drafts with `render --out`", file=sys.stderr)
        return 2
    state_dir = state_dir_for(packet, args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if not (state_dir / "answers.json").exists():
        save_answers(state_dir, empty_answers(packet))
    if args.foreground:
        return _run_foreground(args, packet, state_dir)

    existing = read_server_info(state_dir)
    if existing and not args.restart:
        _print_serve_result(existing, state_dir, reused=True)
        return 0
    if existing and not _terminate(int(existing["pid"])):
        print(f"ERROR: could not stop running server pid {existing['pid']}", file=sys.stderr)
        return 1
    (state_dir / "server.json").unlink(missing_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "serve", str(packet), "--foreground",
           "--state-dir", str(state_dir), "--bind", args.bind, "--port", str(args.port)]
    if args.host:
        cmd += ["--host", args.host]
    if args.token:
        cmd += ["--token", args.token]
    with open(state_dir / "server.log", "ab") as log:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    deadline = time.time() + 15
    while time.time() < deadline:
        info = read_server_info(state_dir)
        if info:
            _print_serve_result(info, state_dir, reused=False)
            return 0
        time.sleep(0.2)
    print(f"ERROR: server did not start; see {state_dir / 'server.log'}", file=sys.stderr)
    return 1


def _print_serve_result(info: dict[str, Any], state_dir: Path, reused: bool) -> None:
    print(json.dumps({"url": info["url"], "port": info["port"], "pid": info["pid"], "state_dir": str(state_dir),
                      "answers": str(state_dir / "answers.json"), "reused": reused}))
    print(f"QUESTIONNAIRE_URL={info['url']}")


def progress(packet: Path, state_dir: Path) -> dict[str, Any]:
    model = page.parse_packet(packet.read_text(encoding="utf-8"))
    answers = load_answers(state_dir, packet)
    ids = [item.id for item in model.items]
    answered = [i for i in ids if i in answers["items"]]
    return {
        "packet": str(packet), "state_dir": str(state_dir), "total": len(ids), "answered": len(answered),
        "unanswered": [i for i in ids if i not in answers["items"]],
        "completed_at": answers.get("completed_at"), "apply_now": answers.get("apply_now"),
        "packet_changed": answers.get("packet_sha256") != packet_digest(packet),
        "server": read_server_info(state_dir) is not None,
        "kinds": {k: sum(1 for i in answered if answers["items"][i]["kind"] == k) for k in sorted(STATE_FOR_KIND)},
    }


def cmd_status(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    deadline = time.time() + (args.wait or 0)
    while True:
        p = progress(packet, state_dir)
        if p["completed_at"] or time.time() >= deadline:
            break
        time.sleep(min(30, max(1, deadline - time.time())))
    print(json.dumps(p, indent=2))
    return 0 if p["completed_at"] else 3


# ── ingest ────────────────────────────────────────────────────────────────────


def _record_value(text: str) -> str:
    """Owner free text as one bullet value: later lines indented as continuations.

    Indentation keeps owner lines (even ``- x`` or ``#### y``) inside the field,
    never parsed as sibling fields or new sections.
    """
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    return "\n  ".join(lines)


def _owner_record(answer: dict[str, Any], existing: dict[str, str]) -> dict[str, str]:
    """Owner-decision-record fields for one answer; keeps an author-named destination."""
    kind = answer["kind"]
    choice, note = _record_value(answer.get("choice") or ""), _record_value(answer.get("note") or "")
    if kind == "pick":
        recorded = choice + (f" — {note}" if note else "")
        review = "Not required — plain selection of a presented option"
    elif kind == "edit":
        recorded = f"Edited: {choice or 'owner-specified option'}" + (f" — {note}" if note else "")
        review = "Pending — fresh scope and recommendation review required before routing"
    elif kind == "hold":
        recorded = f"Held: {note or 'owner held without a stated re-ask trigger'}"
        review = "Not required — hold"
    else:
        recorded = f"Rejected: {note or 'owner rejected every presented option'}"
        review = "Not required — rejection"
    destination = existing.get("Canonical destination", "")
    if not destination or destination.startswith(PENDING):
        destination = "Unrouted — the applying agent names the owning workflow at apply time"
    return {
        "Status": STATE_FOR_KIND[kind],
        "Actor/channel": answer.get("actor", "owner via questionnaire web form"),
        "Recorded at": answer["recorded_at"],
        "Choice": recorded,
        "Owner edits": choice if kind == "edit" and choice else "None",
        "Post-answer review": review,
        "Final authorization boundary": (
            "Pending re-review of the owner's edit" if kind == "edit"
            else "As stated in this item's Authorization boundary; unchanged by the owner"
        ),
        "Canonical destination": destination,
        "Routing evidence": "Not routed",
    }


def _existing_record(block: str) -> dict[str, str]:
    body = vq.subsection(block, "Owner decision record") or ""
    return dict(re.findall(r"^- ([^:\n]+):\s*(.*)$", body, re.MULTILINE))


def apply_answers_to_packet(text: str, answers: dict[str, Any]) -> tuple[str, list[str]]:
    """Return packet text with owner records written in, plus a change log."""
    blocks, _ = vq.section_blocks(text)
    log: list[str] = []
    for decision_id, block in blocks:
        answer = answers["items"].get(decision_id)
        if not answer:
            continue
        new_state = STATE_FOR_KIND[answer["kind"]]
        record = _owner_record(answer, _existing_record(block))
        new_block = re.sub(r"^(\*\*Decision state:\*\*)\s*.+$", rf"\1 {new_state}", block, count=1, flags=re.MULTILINE)

        def _rewrite_record(match: re.Match[str]) -> str:
            body = match.group("body")
            for label, value in record.items():
                line = f"- {label}: {value}"
                body, n = re.subn(
                    rf"^- {re.escape(label)}:.*(?:\n[ \t]+\S.*)*", lambda _: line, body, count=1, flags=re.MULTILINE
                )
                if n == 0:
                    body = body.rstrip("\n") + f"\n{line}\n"
            return match.group("head") + body

        new_block, n = re.subn(
            r"(?P<head>^#### Owner decision record\s*$\n)(?P<body>.*?)(?=^#### |\Z)",
            _rewrite_record, new_block, count=1, flags=re.MULTILINE | re.DOTALL,
        )
        if n == 0:
            log.append(f"{decision_id}: no Owner decision record section; skipped")
            continue
        text = text.replace(block, new_block, 1)
        text = re.sub(
            rf"^(\|\s*\d+\s*\|\s*`{re.escape(decision_id)}`\s*\|[^|]*\|)\s*[^|]*?\s*(\|)",
            rf"\1 {new_state} \2", text, count=1, flags=re.MULTILINE,
        )
        log.append(f"{decision_id}: {answer['kind']} -> {new_state} ({record['Choice'][:60]})")
    return text, log


def cmd_ingest(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    with answers_lock(state_dir):
        return _ingest_locked(args, packet, state_dir)


def _ingest_locked(args: argparse.Namespace, packet: Path, state_dir: Path) -> int:
    answers = load_answers(state_dir, packet)
    if not answers["items"]:
        print("ERROR: no answers recorded yet", file=sys.stderr)
        return 3
    if not answers.get("completed_at") and not args.partial:
        print("ERROR: review not submitted; pass --partial to ingest answered items anyway", file=sys.stderr)
        return 3
    if answers.get("packet_sha256") != packet_digest(packet) and not args.force:
        print("ERROR: packet changed since the owner started answering; re-check the framing or pass --force", file=sys.stderr)
        return 4
    text = packet.read_text(encoding="utf-8")
    new_text, log = apply_answers_to_packet(text, answers)
    errors = vq.validate_packet(new_text)
    if new_text != text and not args.dry_run and not errors:
        atomic_write(packet, new_text)
        answers["packet_sha256"] = packet_digest(packet)
        answers["ingested_at"] = now_iso()
        save_answers(state_dir, answers)
    result = {
        "written": bool(new_text != text and not args.dry_run and not errors),
        "packet": str(packet), "dry_run": args.dry_run, "changes": log, "apply_now": answers.get("apply_now"),
        "completed_at": answers.get("completed_at"),
        "needs_rework": [i for i, a in answers["items"].items() if a["kind"] == "edit"],
        "validator_errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


def cmd_stop(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    info = read_server_info(state_dir)
    if not info:
        print("no running server")
        return 0
    if not _terminate(int(info["pid"])):
        print(f"ERROR: pid {info['pid']} did not exit", file=sys.stderr)
        return 1
    (state_dir / "server.json").unlink(missing_ok=True)
    print(f"stopped pid {info['pid']} on port {info['port']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("packet", help="Markdown questionnaire packet")
        p.add_argument("--state-dir", help="override the derived state directory")

    p = sub.add_parser("serve", help="validate, start or reuse a background server, and print its URL")
    common(p)
    p.add_argument("--bind", default="0.0.0.0", help="interface to bind (default all)")
    p.add_argument("--port", type=int, default=0, help="port (default: random open port)")
    p.add_argument("--host", help="hostname for the printed URL (default: tailscale DNS name, else FQDN)")
    p.add_argument("--token", help="fixed URL token (default: random)")
    p.add_argument("--restart", action="store_true", help="replace a running server for this packet")
    p.add_argument("--foreground", action="store_true", help="run in this process (used by the daemon child)")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("render", help="write a read-only preview page without serving")
    common(p)
    p.add_argument("--out", help="output file (default stdout)")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("status", help="print progress; exit 0 when submitted, 3 otherwise")
    common(p)
    p.add_argument("--wait", type=int, default=0, help="poll up to N seconds for submission")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("ingest", help="write answers into the packet's owner decision records")
    common(p)
    p.add_argument("--partial", action="store_true", help="ingest even if the owner has not submitted")
    p.add_argument("--force", action="store_true", help="ingest even if the packet changed after answering began")
    p.add_argument("--dry-run", action="store_true", help="report changes without writing")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("stop", help="stop the background server for this packet")
    common(p)
    p.set_defaults(fn=cmd_stop)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
