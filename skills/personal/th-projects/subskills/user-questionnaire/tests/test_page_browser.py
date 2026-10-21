"""Browser tier: page.js behavior against a real server in headless Chromium.

Skips unless Playwright and a Chromium build are available. Run it with:

    uv run --with pytest --with playwright pytest -q tests/test_page_browser.py

PLAYWRIGHT_CHROMIUM may point at a Chromium executable when the bundled
browser for the installed Playwright version is not downloaded.
"""

from __future__ import annotations

import glob
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

SCRIPTS = Path(__file__).parents[1] / "scripts"
FIXTURE = Path(__file__).parent / "fixtures" / "review-ready.md"


def _web():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("questionnaire_web")


def _two_item_packet() -> str:
    text = FIXTURE.read_text(encoding="utf-8")
    section = text[text.index("### `dec-auth`") :]
    row = "| 1 | `dec-auth` | P1 | Review-ready | Choose A: retain the single authority. |"
    text = text.replace(row, row + "\n" + row.replace("| 1 |", "| 2 |").replace("dec-auth", "dec-two"))
    return text.rstrip("\n") + "\n\n" + section.replace("dec-auth", "dec-two").replace("choose credential authority", "second decision")


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        candidates = [os.environ.get("PLAYWRIGHT_CHROMIUM"), None, *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True)]
        for executable in candidates:
            try:
                launched = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
            except playwright_api.Error:
                continue
            yield launched
            launched.close()
            return
        pytest.skip("no launchable Chromium for Playwright")


@pytest.fixture
def served(tmp_path):
    web = _web()
    packet = tmp_path / "packet.md"
    packet.write_text(_two_item_packet(), encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    httpd = web.make_server(packet, state_dir, "127.0.0.1", 0, token="tok")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"url": f"http://127.0.0.1:{httpd.server_address[1]}/q/tok/", "state": state_dir, "web": web, "packet": packet}
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def page(browser, served):
    # Reduced motion makes scrolling instant, so scroll-dependent state is deterministic.
    context = browser.new_context(viewport={"width": 1200, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(served["url"])
    yield page
    context.close()
    assert errors == []


def _saved(served) -> dict:
    path = served["state"] / "answers.json"
    return json.loads(path.read_text()) if path.exists() else {"items": {}}


def _opt(item: str, label: str) -> str:
    return f"#item-{item} .opt[data-label={label}]"


def test_failed_save_shows_the_error_not_the_answer_and_retry_recovers(page, served):
    page.route("**/api/answer", lambda route: route.abort())
    page.route("**/api/state", lambda route: route.abort())
    page.click(_opt("dec-auth", "A"))
    page.wait_for_selector("#item-dec-auth .retry:not([hidden])")
    assert page.get_attribute(_opt("dec-auth", "A"), "aria-pressed") == "false"
    assert page.text_content("#item-dec-auth .save").startswith("Not saved")
    assert page.text_content("#progress-text") == "0 of 2 answered"
    assert page.is_disabled("#submit")
    page.unroute("**/api/answer")
    page.unroute("**/api/state")
    page.click("#item-dec-auth .retry")
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert page.get_attribute(_opt("dec-auth", "A"), "aria-pressed") == "true"
    assert not page.is_visible("#item-dec-auth .retry")
    assert _saved(served)["items"]["dec-auth"]["choice"] == "A"


def test_typing_stays_in_the_field_the_owner_chose_while_a_save_is_slow(page, served, monkeypatch):
    page.click(_opt("dec-two", "A"))
    page.wait_for_function("document.querySelector('#item-dec-two .save').textContent.startsWith('Saved')")
    web = served["web"]
    record = web.record_answer

    def slow_record(*args, **kwargs):
        time.sleep(0.6)
        record(*args, **kwargs)

    monkeypatch.setattr(web, "record_answer", slow_record)
    page.click("#item-dec-auth [data-kind=reject]")
    page.click("#note-dec-two")
    page.keyboard.type("two only")
    page.click("body", position={"x": 2, "y": 300})  # blur flushes the note
    page.wait_for_function(
        "['dec-auth', 'dec-two'].every((id) => document.querySelector(`#item-${id} .save`).textContent.startsWith('Saved'))",
        timeout=5000,
    )
    saved = _saved(served)["items"]
    assert saved["dec-two"]["note"] == "two only"
    assert saved["dec-auth"]["kind"] == "reject" and saved["dec-auth"]["note"] == ""


def test_toggling_an_answer_off_and_on_keeps_the_typed_note(page, served):
    page.click("#item-dec-auth [data-kind=hold]")
    page.keyboard.type("after the migration")
    page.click("body", position={"x": 2, "y": 300})
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    page.click("#item-dec-auth [data-kind=hold]")  # accidental second tap clears the answer
    page.click("#item-dec-auth [data-kind=hold]")
    assert page.input_value("#note-dec-auth") == "after the migration"
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["note"] == "after the migration"


def test_answer_keys_do_nothing_when_no_decision_is_in_view(page, served):
    page.set_viewport_size({"width": 1200, "height": 600})
    page.keyboard.press("s")
    page.wait_for_function("!document.querySelector('.item.is-current')")
    page.keyboard.press("a")
    page.wait_for_timeout(300)  # give a (wrong) save time to land before asserting none did
    assert _saved(served)["items"] == {}
    page.evaluate("document.getElementById('item-dec-auth').scrollIntoView({block: 'start'})")
    page.wait_for_selector("#item-dec-auth.is-current")
    page.keyboard.press("b")
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["choice"] == "B"


def test_owner_text_with_script_markup_cannot_disable_the_page(browser, served):
    answers = served["web"].empty_answers(served["packet"])
    answers["items"]["dec-auth"] = {"kind": "hold", "choice": "", "note": "<!--<script>", "recorded_at": "2026-10-21T10:00:00+00:00", "actor": "t"}
    served["web"].save_answers(served["state"], answers)
    context = browser.new_context()
    page = context.new_page()
    page.goto(served["url"])
    assert page.text_content("#progress-text") == "1 of 2 answered"
    assert page.input_value("#note-dec-auth") == "<!--<script>"
    context.close()


def test_submit_requires_every_answer_and_the_apply_choice_then_locks(page, served):
    page.click(_opt("dec-auth", "A"))
    page.click(_opt("dec-two", "B"))
    page.wait_for_function("document.querySelector('#progress-text').textContent === '2 of 2 answered'")
    page.wait_for_function("!document.querySelector('#item-dec-two .save').textContent.includes('Saving')")
    assert page.is_disabled("#submit")  # apply question unanswered
    assert not page.is_visible("#reopen")
    assert not page.is_visible("#item-dec-two .retry")
    page.click("label.choice:has(input[value=false])")
    page.wait_for_function("!document.querySelector('#submit').disabled")
    page.click("#submit")
    page.wait_for_selector("#final.is-done")
    assert page.is_disabled(_opt("dec-auth", "B"))
    saved = _saved(served)
    assert saved["completed_at"] and saved["apply_now"] is False


def test_j_and_k_move_between_decisions(page, served):
    page.evaluate("document.getElementById('item-dec-auth').scrollIntoView({block: 'start'})")
    page.wait_for_selector("#item-dec-auth.is-current")
    page.keyboard.press("j")
    page.wait_for_selector("#item-dec-two.is-current")
    page.keyboard.press("k")
    page.wait_for_selector("#item-dec-auth.is-current")


def test_submit_clicked_straight_from_a_note_still_submits(page, served):
    page.click(_opt("dec-auth", "A"))
    page.click("#item-dec-two [data-kind=hold]")
    page.click("label.choice:has(input[value=true])")
    page.wait_for_function("!document.querySelector('#submit').disabled")
    page.click("#note-dec-two")
    page.keyboard.type("after Q1")
    page.click("#submit")  # blur starts the note save; the submit must queue behind it
    page.wait_for_selector("#final.is-done")
    saved = _saved(served)
    assert saved["completed_at"] and saved["items"]["dec-two"]["note"] == "after Q1"


def test_another_tabs_saved_note_is_not_overwritten_by_a_stale_tab(browser, page, served):
    page.click("#item-dec-auth [data-kind=hold]")
    page.keyboard.type("from tab A")
    page.click("body", position={"x": 2, "y": 300})
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    other = browser.new_context().new_page()
    other.goto(served["url"])  # tab B loads "from tab A"
    page.fill("#note-dec-auth", "tab A revised")
    page.click("body", position={"x": 2, "y": 300})
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    other.click(_opt("dec-two", "A"))  # any save refreshes tab B's copy of other items' text
    other.wait_for_function("document.querySelector('#item-dec-two .save').textContent.startsWith('Saved')")
    other.click("#item-dec-auth [data-kind=reject]")
    other.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["note"] == "tab A revised"
    other.context.close()
