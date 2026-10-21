"""Browser tier: page.js behavior against a real server in headless Chromium.

Skips unless Playwright and a Chromium build are available. The package gate
(th-projects/scripts/validate-th-projects.sh) runs it with a pinned Playwright
and reports a skip. Standalone:

    uv run --with pytest --with playwright==1.61.0 pytest -q tests/test_page_browser.py

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


def _packet(items: int = 2) -> str:
    """The fixture's one item, copied: ids dec-auth, dec-two, then dec-3, dec-4, ..."""
    text = FIXTURE.read_text(encoding="utf-8")
    section = text[text.index("### `dec-auth`") :]
    row = "| 1 | `dec-auth` | P1 | Review-ready | Choose A: retain the single authority. |"
    ids = ["dec-two", *(f"dec-{n}" for n in range(3, items + 1))][: items - 1]
    rows = [row.replace("| 1 |", f"| {n} |").replace("dec-auth", iid) for n, iid in enumerate(ids, 2)]
    text = text.replace(row, "\n".join([row, *rows]))
    copies = [section.replace("dec-auth", iid).replace("choose credential authority", f"decision {n}") for n, iid in enumerate(ids, 2)]
    return "\n\n".join([text.rstrip("\n"), *copies])


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
def serve(tmp_path):
    """Start a server on a packet of `items` decisions; stopped after the test."""
    web = _web()
    servers = []

    def start(items: int = 2) -> dict:
        root = tmp_path / f"packet-{len(servers)}"
        root.mkdir()
        packet = root / "packet.md"
        packet.write_text(_packet(items), encoding="utf-8")
        state_dir = root / "state"
        state_dir.mkdir()
        httpd = web.make_server(packet, state_dir, "127.0.0.1", 0, token="tok")
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return {"url": f"http://127.0.0.1:{httpd.server_address[1]}/q/tok/", "state": state_dir, "web": web, "packet": packet}

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def served(serve):
    return serve()


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


# ── phone ─────────────────────────────────────────────────────────────────────

# A small Android phone; is_mobile + has_touch make (pointer: coarse) match.
PHONE = {"viewport": {"width": 360, "height": 740}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}


@pytest.fixture
def open_phone(browser):
    """Open a URL in a phone context; the test fails on any page error."""
    contexts, errors = [], []

    def open_(url: str, width: int = 360, smooth: bool = False):
        context = browser.new_context(**{**PHONE, "viewport": {"width": width, "height": 740}}, reduced_motion="no-preference" if smooth else "reduce")
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        return page

    yield open_
    for context in contexts:
        context.close()
    assert errors == []


@pytest.fixture
def phone(open_phone, served):
    return open_phone(served["url"])


def _tap_bar(page, selector: str) -> None:
    """Tap a top-bar control where it is; locator.tap() would scroll the sticky bar's slot into view first."""
    box = page.locator(selector).bounding_box()
    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _at_top(page, element_id: str) -> None:
    """Wait until the element sits under the top bar, where a jump puts it."""
    page.wait_for_function(f"Math.abs(document.getElementById('{element_id}').getBoundingClientRect().top - 72) < 4")


def _answer_all(served, ids) -> None:
    answers = served["web"].empty_answers(served["packet"])
    for iid in ids:
        answers["items"][iid] = {"kind": "pick", "choice": "A", "note": "", "recorded_at": "2026-10-21T10:00:00+00:00", "actor": "t"}
    served["web"].save_answers(served["state"], answers)


def test_phone_layout_fits_the_screen_with_touch_sized_controls(phone):
    assert phone.evaluate("document.documentElement.scrollWidth") <= 360
    small = phone.evaluate(
        """[...document.querySelectorAll('#item-dec-auth .chip-btn:not([hidden]), #item-dec-auth summary, #next-btn, #submit, #todo a')]
             .filter((el) => el.getBoundingClientRect().height < 44).map((el) => el.textContent.trim())"""
    )
    assert small == []
    # Below 16px, iOS zooms the page when a field takes focus.
    assert phone.evaluate("parseFloat(getComputedStyle(document.querySelector('#note-dec-auth')).fontSize)") >= 16
    assert not phone.is_visible("#help-btn")  # keyboard shortcuts mean nothing on touch
    assert phone.is_visible("#next-btn")


def test_top_bar_fits_many_decisions_on_a_small_phone(serve, open_phone):
    served = serve(20)
    _answer_all(served, ["dec-auth", "dec-two", *(f"dec-{n}" for n in range(3, 21))])  # longest text: "20 of 20", "Submit"
    page = open_phone(served["url"], width=320)
    boxes = page.evaluate(
        """['.segments i:last-child', '#progress-text', '#next-btn'].map((sel) => document.querySelector(sel).getBoundingClientRect())
             .map((r) => [r.left, r.right])"""
    )
    (_, dots_end), (text_start, text_end), (button_start, button_end) = boxes
    assert dots_end <= text_start and text_end <= button_start and button_end <= 320


def test_next_moves_from_the_card_being_read_and_wraps(serve, open_phone):
    served = serve(4)
    page = open_phone(served["url"])
    page.tap(_opt("dec-auth", "A"))
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    page.evaluate("window.scrollTo(0, 0)")
    _tap_bar(page, "#next-btn")
    _at_top(page, "item-dec-two")  # skips the answered dec-auth
    assert page.evaluate("document.activeElement.id") == "title-dec-two"  # focus follows, for screen readers
    page.evaluate("document.getElementById('item-dec-3').scrollIntoView({block: 'start'})")
    page.wait_for_selector("#item-dec-3.is-current")
    _tap_bar(page, "#next-btn")
    _at_top(page, "item-dec-4")  # from the card being read, not from the top
    _tap_bar(page, "#next-btn")
    _at_top(page, "item-dec-two")  # wraps past the end to the first unanswered
    page.evaluate("document.getElementById('final').scrollIntoView({block: 'start'})")
    page.wait_for_function("!document.querySelector('.item.is-current')")
    _tap_bar(page, "#next-btn")
    _at_top(page, "item-dec-two")  # from the submit section, back to the first unanswered


def test_next_tapped_twice_quickly_goes_two_cards(serve, open_phone):
    served = serve(4)
    page = open_phone(served["url"], smooth=True)
    _tap_bar(page, "#next-btn")
    _tap_bar(page, "#next-btn")  # before the reading band has reported dec-auth
    _at_top(page, "item-dec-two")


def test_next_ends_at_submit_once_everything_is_answered(phone, served):
    phone.tap(_opt("dec-auth", "A"))
    phone.tap(_opt("dec-two", "B"))
    phone.wait_for_function("document.querySelector('#next-btn').textContent === 'Submit ↓'")
    _tap_bar(phone, "#next-btn")
    _at_top(phone, "final")


def test_next_button_is_only_for_screens_without_the_sidebar(page):
    assert page.is_visible(".toc")
    assert not page.is_visible("#next-btn")


def test_hold_on_touch_leaves_the_keyboard_closed_but_edit_opens_it(phone):
    phone.tap("#item-dec-auth [data-kind=hold]")
    assert phone.is_visible("#note-dec-auth")
    assert phone.evaluate("document.activeElement.tagName") != "TEXTAREA"
    phone.tap("#item-dec-auth [data-kind=edit]")
    assert phone.evaluate("document.activeElement.id") == "edit-dec-auth"  # edit text is required


def test_a_save_lost_offline_is_resent_when_the_connection_returns(phone, served):
    phone.context.set_offline(True)
    phone.tap(_opt("dec-auth", "B"))
    phone.wait_for_function("document.querySelector('#item-dec-auth .save').textContent === 'Not saved: no connection'")
    phone.context.set_offline(False)  # fires "online"
    phone.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["choice"] == "B"


def test_reconnecting_does_not_resend_over_a_newer_answer_from_another_device(browser, phone, served):
    phone.context.set_offline(True)
    phone.tap(_opt("dec-auth", "B"))
    phone.wait_for_function("document.querySelector('#item-dec-auth .save').textContent === 'Not saved: no connection'")
    desk = browser.new_context().new_page()
    desk.goto(served["url"])
    desk.click(_opt("dec-auth", "A"))  # decided again, later, on the desktop
    desk.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    phone.context.set_offline(False)
    phone.wait_for_function("document.querySelector('#item-dec-auth .opt[data-label=A]').getAttribute('aria-pressed') === 'true'")
    assert phone.text_content("#item-dec-auth .save").startswith("Saved")
    assert _saved(served)["items"]["dec-auth"]["choice"] == "A"
    desk.context.close()


def test_a_failed_save_is_resent_when_the_page_is_shown_again(phone, served):
    phone.route("**/api/answer", lambda route: route.abort())
    phone.tap(_opt("dec-auth", "B"))
    phone.wait_for_selector("#item-dec-auth .retry:not([hidden])")
    phone.unroute("**/api/answer")
    phone.evaluate("document.dispatchEvent(new Event('visibilitychange'))")  # still "visible": back from another app
    phone.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["choice"] == "B"


def test_answering_another_item_releases_a_focused_note(page, served):
    # Safari never focuses a clicked button, so the note would keep the typing.
    page.click("#item-dec-auth [data-kind=hold]")
    page.keyboard.type("later")
    page.evaluate("document.querySelector('#item-dec-two .opt[data-label=A]').click()")  # a click that moves no focus
    assert page.evaluate("document.activeElement.tagName") != "TEXTAREA"
    page.wait_for_function("document.querySelector('#item-dec-auth .save').textContent.startsWith('Saved')")
    assert _saved(served)["items"]["dec-auth"]["note"] == "later"
