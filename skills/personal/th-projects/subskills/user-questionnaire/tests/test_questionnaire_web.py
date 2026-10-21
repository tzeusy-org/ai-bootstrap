from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
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


def test_state_dir_is_derived_deterministically_from_packet_path(web, packet, monkeypatch, tmp_path):
    monkeypatch.setenv("QUESTIONNAIRE_STATE_ROOT", str(tmp_path / "root"))
    first = web.state_dir_for(packet)
    assert first == web.state_dir_for(Path(str(packet)))
    assert first.parent == tmp_path / "root"
    assert first.name.startswith("packet-")


# ── answer model ─────────────────────────────────────────────────────────────


OPTIONS = {"dec-auth": {"A", "B"}}


def test_record_answer_validates_and_clears(web, packet):
    answers = web.empty_answers(packet)
    with pytest.raises(web.AnswerError, match="unknown item"):
        web.record_answer(answers, OPTIONS, {"id": "nope", "kind": "pick", "choice": "A"}, "t", "d")
    with pytest.raises(web.AnswerError, match="one of this item's options: A, B"):
        web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": "pick", "choice": "D"}, "t", "d")
    web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": "hold", "choice": "A", "note": " later "}, "t", "d")
    assert answers["items"]["dec-auth"]["choice"] == "" and answers["items"]["dec-auth"]["note"] == "later"
    assert answers["packet_sha256"] == "d", "the first answer pins the packet version being read"
    web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": None}, "t", "d")
    assert answers["items"] == {}


def test_completion_requires_every_answer_a_described_edit_and_the_apply_choice(web, packet):
    answers = web.empty_answers(packet)
    ids = set(OPTIONS)
    with pytest.raises(web.AnswerError, match="unanswered: dec-auth"):
        web.record_completion(answers, ids, {"completed": True}, "t")
    web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": "edit", "choice": ""}, "t", "d")
    with pytest.raises(web.AnswerError, match="describe the edited option"):
        web.record_completion(answers, ids, {"completed": True}, "t")
    web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": "edit", "choice": "A, minus the migration"}, "t", "d")
    with pytest.raises(web.AnswerError, match="apply question"):
        web.record_completion(answers, ids, {"completed": True}, "t")
    with pytest.raises(web.AnswerError, match="true or false"):
        web.record_completion(answers, ids, {"apply_now": "yes"}, "t")
    web.record_completion(answers, ids, {"apply_now": False, "completed": True}, "t")
    assert answers["completed_at"] and answers["apply_now"] is False
    with pytest.raises(web.AnswerError, match="reopen"):
        web.record_answer(answers, OPTIONS, {"id": "dec-auth", "kind": "pick", "choice": "A"}, "t", "d")
    web.record_completion(answers, ids, {"completed": False}, "t")
    assert answers["completed_at"] is None and "completed_actor" not in answers


# ── ingest ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,choice,note,state,choice_prefix",
    [
        ("pick", "A", "", "Agreed", "A"),
        ("hold", "", "wait for the migration", "Held", "Held: wait for the migration"),
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
    assert validator.validate_packet(new_text) == []
    if state == "Needs-rework":
        assert "- Post-answer review: Pending" in new_text
        assert validator.validate_packet(new_text, require_review_ready=True)
    else:
        assert validator.validate_packet(new_text, require_review_ready=True) == []


def test_ingest_keeps_an_author_named_canonical_destination(web, packet):
    text = packet.read_text(encoding="utf-8").replace(
        "- Canonical destination: Pending owner response", "- Canonical destination: `/beads-orchestration` beads-writer"
    )
    new_text, _ = web.apply_answers_to_packet(text, _answers(web, packet, "pick", "A"))
    assert "- Canonical destination: `/beads-orchestration` beads-writer" in new_text


def test_ingest_writes_owner_text_verbatim_even_with_regex_escapes(web, packet):
    new_text, _ = web.apply_answers_to_packet(packet.read_text(encoding="utf-8"), _answers(web, packet, "hold", "", r"after \1 and \g<0>"))
    assert r"- Choice: Held: after \1 and \g<0>" in new_text


def test_multiline_owner_text_stays_inside_its_field_across_reingest(web, validator, packet):
    note = "until Q1:\n- name the repo\n\n#### Evidence\n- move the beads"
    first, _ = web.apply_answers_to_packet(packet.read_text(encoding="utf-8"), _answers(web, packet, "hold", "", note))
    assert "- Choice: Held: until Q1:\n  - name the repo\n  #### Evidence\n  - move the beads\n" in first
    assert validator.validate_packet(first) == []
    second, _ = web.apply_answers_to_packet(first, _answers(web, packet, "hold", "", "later\nre-ask"))
    record = validator.subsection(second, "Owner decision record")
    assert "- Choice: Held: later\n  re-ask\n- Owner edits: None" in record
    assert "name the repo" not in second and second.count("#### Evidence") == 1


def test_owner_words_that_look_like_placeholders_are_valid(web, validator, packet):
    new_text, _ = web.apply_answers_to_packet(packet.read_text(encoding="utf-8"), _answers(web, packet, "hold", "", "TBD after <MIGRATION>"))
    assert validator.validate_packet(new_text) == []
    assert validator.validate_packet(new_text.replace("Choose the sole credential authority.", "TBD"))  # authors still can't
    unanswered = packet.read_text(encoding="utf-8").replace("- Choice: Pending owner response", "- Choice: TODO pick later")
    assert "unresolved placeholder `TODO`" in validator.validate_packet(unanswered)


def test_ingest_never_writes_a_packet_that_fails_validation(web, packet, state_dir, capsys):
    broken = packet.read_text(encoding="utf-8").replace("- `spec.md:42`\n", "")
    packet.write_text(broken, encoding="utf-8")
    web.save_answers(state_dir, _answers(web, packet, "pick", "A"))
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["validator_errors"] and result["written"] is False
    assert packet.read_text(encoding="utf-8") == broken


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
    try:
        with urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except HTTPError as error:
        return error.code, error.read()


@pytest.fixture
def server(web, packet, state_dir):
    state_dir.mkdir()
    httpd = web.make_server(packet, state_dir, "127.0.0.1", 0, token="tok")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_server_gates_on_token_and_persists_each_answer(web, packet, state_dir, server):
    status, page = _call(f"{server}/q/tok/")
    assert status == 200 and b'id="item-dec-auth"' in page
    assert _call(f"{server}/q/wrong/")[0] == 404
    assert _call(f"{server}/q/tokx/")[0] == 404, "a token prefix is not the token"
    assert _call(f"{server}/health")[0] == 404
    status, body = _call(f"{server}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "B", "note": "n"})
    assert status == 200 and json.loads(body)["packet_sha256"] == web.packet_digest(packet)
    assert json.loads((state_dir / "answers.json").read_text())["items"]["dec-auth"]["choice"] == "B"
    status, body = _call(f"{server}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "Z"})
    assert status == 409 and "A, B" in json.loads(body)["error"]
    assert _call(f"{server}/q/tok/api/complete", {"completed": True})[0] == 409, "apply question unanswered"
    status, body = _call(f"{server}/q/tok/api/complete", {"apply_now": False, "completed": True})
    assert status == 200 and json.loads(body)["answers"]["completed_at"]
    assert not list(state_dir.glob(".answers.json.*.tmp"))
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 0
    assert "- Choice: B — n" in packet.read_text(encoding="utf-8")


def test_server_reads_state_from_disk_so_ingest_and_server_agree(web, packet, state_dir, server):
    _call(f"{server}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "A"})
    _call(f"{server}/q/tok/api/complete", {"apply_now": True, "completed": True})
    assert web.main(["ingest", str(packet), "--state-dir", str(state_dir)]) == 0
    _, body = _call(f"{server}/q/tok/api/state")
    assert json.loads(body)["answers"]["ingested_at"], "server serves the ingested state, not a stale in-memory copy"
    _, page = _call(f"{server}/q/tok/")
    assert b"The packet changed" not in page


def test_server_writes_wait_for_the_shared_answers_lock(web, state_dir, server):
    done = threading.Event()
    with web.answers_lock(state_dir):  # what a concurrent `ingest` holds
        worker = threading.Thread(
            target=lambda: (_call(f"{server}/q/tok/api/answer", {"id": "dec-auth", "kind": "pick", "choice": "A"}), done.set())
        )
        worker.start()
        assert not done.wait(0.3), "the server wrote answers.json while another process held the lock"
    assert done.wait(5)
    assert json.loads((state_dir / "answers.json").read_text())["items"]["dec-auth"]["choice"] == "A"


def test_server_rejects_oversized_and_non_object_bodies(server):
    assert _call(f"{server}/q/tok/api/answer", {"id": "dec-auth", "note": "x" * 70_000})[0] == 413
    assert _call(f"{server}/q/tok/api/answer", ["not", "an", "object"])[0] == 400


def test_serve_refuses_a_packet_that_is_not_review_ready(web, packet, state_dir, capsys):
    packet.write_text(packet.read_text(encoding="utf-8").replace("Review-ready", "Draft"), encoding="utf-8")
    assert web.main(["serve", str(packet), "--state-dir", str(state_dir)]) == 2
    assert "not ready for owner review" in capsys.readouterr().err
    assert not (state_dir / "server.json").exists()


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
