from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
FIXTURE = Path(__file__).parent / "fixtures" / "review-ready.md"


def _load(name: str):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def web():
    return _load("questionnaire_web")


@pytest.fixture(scope="module")
def validator():
    return _load("validate_questionnaire")


@pytest.fixture
def packet(tmp_path: Path) -> Path:
    target = tmp_path / "packet.md"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


def _answers(web, packet: Path, kind: str, choice: str = "", note: str = "", completed: bool = True):
    answers = web.empty_answers(packet)
    answers["items"]["dec-auth"] = {
        "kind": kind,
        "choice": choice,
        "note": note,
        "recorded_at": "2026-10-21T10:00:00+00:00",
        "actor": "owner via questionnaire web form (test)",
    }
    answers["completed_at"] = "2026-10-21T10:05:00+00:00" if completed else None
    answers["apply_now"] = True
    return answers


# ── parsing and rendering ────────────────────────────────────────────────────


def test_parse_packet_exposes_items_options_and_recommendation(web, packet):
    model = web.parse_packet(packet.read_text(encoding="utf-8"))
    assert [i["id"] for i in model["items"]] == ["dec-auth"]
    item = model["items"][0]
    assert item["priority"] == "P1"
    assert item["state"] == "Review-ready"
    assert [o["label"] for o in item["options"]] == ["A", "B"]
    assert item["recommended"] == "A"
    assert "Choose the sole credential authority." in item["decision_needed"]


def test_render_is_self_contained_and_escapes_content(web, packet, state_dir):
    text = packet.read_text(encoding="utf-8").replace(
        "Choose the sole credential authority.", "Choose <script>alert(1)</script> authority."
    )
    page = web.render_html(web.parse_packet(text), web.empty_answers(packet), "/q/t/api", live=False)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'id="item-dec-auth"' in page
    assert 'data-label="A"' in page and 'data-label="B"' in page
    assert "http://" not in page.split("<body>")[0].replace("http://www.w3.org", "")
    assert "prefers-color-scheme:dark" in page


def test_md_to_html_handles_lists_tables_and_labels(web):
    out = web.md_to_html("- **[Observed]** fact `x`\n- second\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\npara **bold**")
    assert '<span class="lbl obs">Observed</span>' in out
    assert "<ul>" in out and "<li>second</li>" in out
    assert "<table>" in out and "<td>2</td>" in out
    assert "<p>para <strong>bold</strong></p>" in out


def test_state_dir_is_derived_deterministically_from_packet_path(web, packet, monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTIONNAIRE_STATE_ROOT", str(tmp_path / "root"))
    first = web.state_dir_for(packet)
    second = web.state_dir_for(Path(str(packet)))
    assert first == second
    assert first.parent == tmp_path / "root"
    assert first.name.startswith("packet-")


# ── ingest ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,choice,note,state,choice_prefix",
    [
        ("pick", "A", "", "Agreed", "A"),
        ("defer", "", "wait for the migration", "Held", "Deferred: wait for the migration"),
        ("reject", "", "", "Rejected", "Rejected: owner rejected"),
        ("edit", "A but without migration", "", "Needs-rework", "Edited: A but without migration"),
    ],
)
def test_ingest_writes_owner_record_and_state(web, validator, packet, kind, choice, note, state, choice_prefix):
    text = packet.read_text(encoding="utf-8")
    new_text, log = web.apply_answers_to_packet(text, _answers(web, packet, kind, choice, note))
    assert log and state in log[0]
    assert f"**Decision state:** {state}" in new_text
    assert f"| `dec-auth` | P1 | {state} |" in new_text
    assert f"- Status: {state}" in new_text
    assert f"- Choice: {choice_prefix}" in new_text
    assert "- Recorded at: 2026-10-21T10:00:00+00:00" in new_text
    errors = validator.validate_packet(new_text)
    assert errors == []
    if state == "Needs-rework":
        assert "- Post-answer review: Pending" in new_text
        assert validator.validate_packet(new_text, require_review_ready=True)
    else:
        assert validator.validate_packet(new_text, require_review_ready=True) == []


def test_ingest_cli_refuses_unsubmitted_and_changed_packets(web, packet, state_dir):
    web.save_answers(state_dir, _answers(web, packet, "pick", "A", completed=False))
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 3
    web.save_answers(state_dir, _answers(web, packet, "pick", "A"))
    packet.write_text(packet.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 4
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir), "--force"]) == 0
    assert "**Decision state:** Agreed" in packet.read_text(encoding="utf-8")
    saved = json.loads((state_dir / "answers.json").read_text())
    assert saved["packet_sha256"] == web.packet_digest(packet)
    assert saved["ingested_at"]


# ── server ───────────────────────────────────────────────────────────────────


def _call(url: str, body: dict | None = None):
    req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                  headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urlopen(req, timeout=5) as r:
        return r.status, r.read()


def test_server_records_answers_atomically_and_gates_on_token(web, packet, state_dir):
    state_dir.mkdir()
    httpd = web.make_server(packet, state_dir, "127.0.0.1", 0, token="tok")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, page = _call(f"{base}/q/tok/")
        assert status == 200 and b'id="item-dec-auth"' in page
        with pytest.raises(Exception):
            _call(f"{base}/q/wrong/")
        status, body = _call(f"{base}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "B", "note": "n"})
        assert status == 200
        saved = json.loads((state_dir / "answers.json").read_text())
        assert saved["items"]["dec-auth"]["choice"] == "B"
        assert saved["items"]["dec-auth"]["recorded_at"].startswith("20")
        with pytest.raises(Exception):
            _call(f"{base}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "Z"})
        status, body = _call(f"{base}/q/tok/api/complete", {"completed": True, "apply_now": False})
        assert json.loads(body)["completed_at"] and json.loads(body)["apply_now"] is False
        with pytest.raises(Exception):  # submitted packets are read-only until reopened
            _call(f"{base}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "A"})
        status, body = _call(f"{base}/q/tok/api/state")
        assert json.loads(body)["completed_at"]
        assert not list(state_dir.glob(".answers.json.*.tmp"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert web.progress(packet, state_dir)["answered"] == 1
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 0
    assert "- Choice: B — n" in packet.read_text(encoding="utf-8")


def test_daemon_serve_is_idempotent_and_stop_terminates_it(web, packet, state_dir, capsys):
    argv = ["serve", str(packet), "--state-dir", str(state_dir), "--bind", "127.0.0.1", "--host", "localhost"]
    assert web.main(argv) == 0
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first["url"].startswith("http://localhost:") and not first["reused"]
    assert web.main(argv) == 0
    second = json.loads(capsys.readouterr().out.splitlines()[0])
    assert second["pid"] == first["pid"] and second["reused"]
    status, _ = _call(first["url"].replace("localhost", "127.0.0.1"))
    assert status == 200
    assert web.main(["stop", str(packet), "--state-dir", str(state_dir)]) == 0
    assert not web._pid_alive(first["pid"])
    assert not (state_dir / "server.json").exists()
    assert web.main(["stop", str(packet), "--state-dir", str(state_dir)]) == 0  # no server: still exit 0
