# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright==1.61.0"]
# ///
"""Browser journeys against the real progress server, without model calls.

Run with ``uv run --script tests/test_dashboard_browser.py``. Missing Chromium
is an explicit skip; PLAYWRIGHT_CHROMIUM may name a local Chrome executable.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import progress_dashboard as model

# The CLI binds the calling runtime session from the environment; tests must
# never attach the real session that happens to be running them.
for _variable in ("CODEX_THREAD_ID", "CODEX_HOME", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(_variable, None)
from dashboard_server import DashboardServer


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class DashboardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        candidates = [os.environ.get("PLAYWRIGHT_CHROMIUM"), None,
                      shutil.which("google-chrome"), shutil.which("chromium"),
                      *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True)]
        launch_errors = []
        for executable in dict.fromkeys(candidates):
            try:
                cls.browser = cls.playwright.chromium.launch(executable_path=executable) if executable else cls.playwright.chromium.launch()
                return
            except PlaywrightError as error:
                launch_errors.append(str(error).splitlines()[0])
        cls.playwright.stop()
        raise unittest.SkipTest("no launchable Chromium for Playwright: " + "; ".join(dict.fromkeys(launch_errors)))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = model.workspace("browser-session", self.root)
        model.initialize(self.folder, title="A useful progress workspace", actor="parent", repo_root=str(self.root))
        self.snapshot = {
            "summary": "The parent owns the outcome; this page owns its readable projection.",
            "phase": "Implementation",
            "tasks": [{"id": "work", "title": "Implement the workspace", "status": "in-progress", "detail": "Persist progress and feedback.", "evidence": "Focused checks"},
                      {"id": "review", "title": "Independently verify", "status": "todo", "detail": "Review the complete behavior.", "evidence": ""}],
            "findings": ["Unknown counters stay unknown."],
            "agents": [{
                "id": "reviewer", "name": "Independent reviewer", "role": "Verifier",
                "status": "waiting", "task": "Review the workspace",
                "summary": "Waiting for a reviewable checkpoint.",
                "model": "example", "updated_at": model.now(),
                "branch": "agent/review-progress", "worktree": str(self.root / "review-worktree"),
                "pr_url": "https://github.com/example/progress/pull/123",
                "activity": ["Reviewed the storage contract.", "Checked feedback retry behavior."],
            }],
        }
        model.checkpoint(self.folder, self.snapshot, 0)
        self.server = DashboardServer(self.folder, "127.0.0.1", 0, "browser-fixture")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(f"http://127.0.0.1:{self.server.server_port}/{self.server.instance}/")
        self.page.get_by_role("heading", name="A useful progress workspace", exact=True).wait_for()

    def stop_server(self):
        self.server.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def saved(self):
        return model.load(self.folder / "feedback.json")

    def refresh(self):
        self.page.get_by_role("button", name="Refresh", exact=True).click()

    def saved_result(self):
        expect(self.page.locator("#feedback-result")).to_contain_text("Saved.")

    def test_progress_feedback_acknowledgement_and_safe_content(self):
        page = self.page
        self.assertEqual(page.locator("#token-total").inner_text(), "—")
        page.locator("#feedback-text").fill("Keep this bounded and token efficient.")
        model.checkpoint(self.folder, {"findings": ["<img src=x onerror=window.unsafe=true>"]})
        self.refresh()
        page.locator("#finding-list").get_by_text("<img src=x onerror=window.unsafe=true>", exact=True).wait_for()
        self.assertEqual(page.locator("#feedback-text").input_value(), "Keep this bounded and token efficient.")
        self.assertIsNone(page.evaluate("window.unsafe"))
        self.assertEqual(page.locator("#finding-list img").count(), 0)
        page.reload()
        page.get_by_role("heading", name="A useful progress workspace", exact=True).wait_for()
        self.assertEqual(page.locator("#feedback-text").input_value(), "Keep this bounded and token efficient.")
        page.locator("#feedback-target").select_option("reviewer")
        page.get_by_role("button", name="Send", exact=True).click()
        self.saved_result()
        rows = self.saved()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "reviewer")
        model.acknowledge(self.folder, rows[0]["id"], "Kept the scope bounded; background refresh does not wake the model.")
        self.refresh()
        page.get_by_text("Kept the scope bounded; background refresh does not wake the model.", exact=True).wait_for()
        self.assertEqual(page.locator("#feedback-text").input_value(), "")
        self.assertEqual((self.folder / "handover.md").read_text(), "# Handover\n\nNo handover has been requested.\n")

    def test_ambiguous_save_retry_is_idempotent_and_a_slow_save_preserves_new_input(self):
        page = self.page
        first = True

        def interrupt_confirmation(route):
            nonlocal first
            if first:
                first = False
                route.fetch()  # The real backend saves the message; its confirmation is lost.
                route.fulfill(status=503, json={"error": "Confirmation interrupted"})
            else:
                route.continue_()

        page.route("**/api/feedback", interrupt_confirmation)
        page.locator("#feedback-text").fill("One message even if confirmation is interrupted.")
        page.get_by_role("button", name="Send", exact=True).click()
        expect(page.locator("#feedback-result")).to_contain_text("Your draft is kept")
        self.assertEqual(len(self.saved()), 1)
        page.get_by_role("button", name="Send", exact=True).click()
        self.saved_result()
        self.assertEqual(len(self.saved()), 1)
        original = model.record_feedback

        def slow_record(*args, **kwargs):
            row = original(*args, **kwargs)
            time.sleep(0.8)
            return row

        with patch.object(model, "record_feedback", slow_record):
            page.locator("#feedback-text").fill("The message being saved.")
            page.get_by_role("button", name="Send", exact=True).click()
            self.assertFalse(page.locator("#feedback-text").is_disabled())
            page.locator("#feedback-text").fill("A new thought while saving.")
            self.saved_result()
        self.assertEqual(page.locator("#feedback-text").input_value(), "A new thought while saving.")
        self.assertEqual(len(self.saved()), 2)

    def test_refresh_preserves_agent_focus_disclosures_and_drafts(self):
        page = self.page
        agent = page.locator('.agent[data-agent-id="reviewer"]')
        summary = agent.locator("summary")
        summary.click()
        agent.evaluate("node => window.retainedAgent = node")
        button = page.get_by_role("button", name="Comment on Independent reviewer", exact=True)
        button.focus()
        # The collector samples every three seconds even when counters did not change.
        with self.server.telemetry_lock:
            self.server.telemetry = {**self.server.telemetry, "sampled_at": "2099-01-01T00:00:00Z"}
        with page.expect_response("**/api/state"):
            page.locator("#refresh").evaluate("node => node.click()")
        expect(button).to_be_focused()
        page.locator("#feedback-text").fill("Keep this draft while inspecting the agent.")
        expect(agent).to_contain_text("Waiting for a reviewable checkpoint.")
        expect(agent).to_contain_text("Reviewed the storage contract.")
        expect(agent).to_contain_text("agent/review-progress")
        expect(agent).to_contain_text("Not measured: no usage source attached")
        measured = {
            "id": "reviewer", "provider": "codex", "status": "measured",
            "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 23, "total_tokens": 123,
            "observed_at": model.now(), "thread_id": "11111111-1111-4111-8111-111111111111",
            "agent_path": "/root/reviewer", "model": "runtime-model",
        }
        link = agent.get_by_role("link")
        expect(link).to_have_attribute("href", "https://github.com/example/progress/pull/123")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        link.focus()
        link.evaluate("node => window.retainedLink = node")
        with self.server.telemetry_lock:
            self.server.telemetry = {"agents": [measured], "totals": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 23, "total_tokens": 123}, "coverage": {"attached": 1, "measured": 1}, "sampled_at": model.now()}
        with page.expect_response("**/api/state"):
            page.locator("#refresh").evaluate("node => node.click()")
        # Changing usage patches the row; the open report and its controls are the same nodes.
        expect(agent).to_contain_text("20 new input · 80 cached · 23 output")
        expect(agent).to_contain_text("example · codex · session 11111111")
        expect(agent).to_have_attribute("open", "")
        expect(link).to_be_focused()
        self.assertTrue(agent.evaluate("node => node === window.retainedAgent"))
        self.assertTrue(link.evaluate("node => node === window.retainedLink"))
        expect(page.locator("#feedback-text")).to_have_value("Keep this draft while inspecting the agent.")
        # A delta for another agent and a status change leave this row's state alone.
        summary.focus()
        model.checkpoint(self.folder, {"agents": [{"id": "builder", "name": "Builder"}, {"id": "reviewer", "status": "running"}]})
        with page.expect_response("**/api/state"):
            page.locator("#refresh").evaluate("node => node.click()")
        expect(summary).to_be_focused()
        expect(summary).to_contain_text("Running")
        expect(agent).to_have_attribute("open", "")
        builder = page.locator('.agent[data-agent-id="builder"]')
        builder.locator("summary").click()
        expect(builder).to_contain_text("Not measured")
        self.assertEqual(builder.get_by_role("link").count(), 0)
        button.click()
        expect(page.locator("#feedback-text")).to_be_focused()
        expect(page.locator("#feedback-target")).to_have_value("reviewer")
        page.locator("#feedback-text").fill("Keep the review independent.")
        page.locator("#feedback-text").press("Control+Enter")
        self.saved_result()
        expect(page.locator("#nav-feedback")).to_have_text("1")
        expect(page.locator("#feedback-total")).to_have_text("1 pending")
        expect(page.locator("#submit-feedback")).to_be_disabled()

    def test_templated_panels_task_links_and_derived_activity(self):
        page = self.page
        expect(page.locator("#visuals")).to_be_hidden()
        expect(page.locator('nav a[href="#visuals"]')).to_be_hidden()
        model.checkpoint(self.folder, {
            "agents": [{"id": "reviewer", "task": "review"}],
            "tasks": [{"id": "work", "status": "complete"}],
            "panels": [
                {"id": "gates", "title": "Delivery gates", "kind": "steps", "rows": [["Tests", "done", "31 pass"], ["<b>Review</b>", "active"]]},
                {"id": "size", "title": "Payload", "kind": "bars", "rows": [["Before", 2140, "bytes"], ["After", 96, "bytes"]]},
                {"id": "counts", "title": "Round", "kind": "stats", "rows": [["Findings", 7], ["Fixed", 6, "1 deferred"]]},
                {"id": "prs", "title": "Pull requests", "kind": "table", "rows": [["PR", "Files"], ["#65", 14]]}]})
        self.refresh()
        panels = page.locator("#panel-list .panel")
        expect(panels).to_have_count(4)
        expect(panels.nth(0)).to_contain_text("<b>Review</b>")
        self.assertEqual(page.locator("#panel-list b").count(), 0)
        widths = panels.nth(1).locator(".bar .fresh").evaluate_all("nodes => nodes.map(node => node.getBoundingClientRect().width)")
        self.assertGreater(widths[0], widths[1] * 10)
        expect(panels.nth(2)).to_contain_text("1 deferred")
        expect(panels.nth(3).get_by_role("columnheader", name="Files")).to_be_visible()
        expect(page.locator("#nav-visuals")).to_have_text("4")
        expect(page.locator("#task-total")).to_have_text("1/2")
        expect(page.locator("#activity-list li").first).to_contain_text("Published: Pull requests")
        expect(page.locator("#activity-list")).to_contain_text("Implement the workspace: complete")
        # A task names the agents working on it; the chip opens that agent's report.
        page.locator('.task[data-task-id="review"]').get_by_role("link", name="Independent reviewer").click()
        expect(page.locator('.agent[data-agent-id="reviewer"]')).to_have_attribute("open", "")

    def test_stale_checkpoint_offline_recovery_and_mobile_feedback_navigation(self):
        page = self.page
        state = model.load(self.folder / "state.json")
        state["updated_at"] = "2020-01-01T00:00:00Z"
        model.save(self.folder / "state.json", state)
        self.refresh()
        expect(page.locator("#connection-text")).to_have_text("Live")
        expect(page.locator("#checkpoint-time")).to_contain_text("may be stale")
        with self.server.telemetry_lock:
            self.server.telemetry = {**self.server.telemetry, "error": "Telemetry unavailable; check the attached sources."}
        self.refresh()
        expect(page.locator("#usage-warning")).to_have_text("Telemetry unavailable; check the attached sources.")
        expect(page.locator("#token-caption")).to_contain_text("Usage unavailable")
        mobile = self.browser.new_context(viewport={"width": 320, "height": 740}, is_mobile=True, has_touch=True, reduced_motion="reduce")
        self.addCleanup(mobile.close)
        page = mobile.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.page.url)
        page.get_by_role("heading", name="A useful progress workspace", exact=True).wait_for()
        self.page = page
        feedback_link = page.locator('nav a[href="#feedback"]')
        expect(page.locator("#top-title")).to_have_text("A useful progress workspace")
        expect(page.locator('nav a[href="#work"]')).to_have_attribute("aria-current", "true")
        agent_details = page.locator('.agent[data-agent-id="reviewer"]')
        agent_details.locator("summary").click()
        expect(agent_details).to_have_attribute("open", "")
        expect(agent_details).to_contain_text("agent/review-progress")
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        feedback_link.click()
        expect(page.locator("#feedback-heading")).to_be_in_viewport()
        expect(feedback_link).to_have_attribute("aria-current", "true")
        expect(feedback_link).to_be_in_viewport()
        page.locator("#feedback-text").fill("Keep this draft if the connection fails.")
        page.route("**/api/state", lambda route: route.abort())
        self.refresh()
        expect(page.locator("#load-error")).to_contain_text("Showing the last snapshot")
        expect(page.locator("#feedback-text")).to_have_value("Keep this draft if the connection fails.")
        page.unroute("**/api/state")
        self.refresh()
        expect(page.locator("#load-error")).to_be_hidden()
        expect(page.locator("#feedback-text")).to_have_value("Keep this draft if the connection fails.")

    def test_partial_usage_stays_visible_and_small_screens_fit(self):
        complete = {"id": "contract", "title": "Agree the bounded contract", "status": "complete", "detail": "Keep the owning workflow authoritative.", "evidence": "Reviewed contract."}
        builder = {**self.snapshot["agents"][0], "id": "builder", "name": "Workspace builder", "status": "running", "summary": "Storage and feedback checks pass; browser checks remain."}
        finished = {**builder, "id": "researcher", "name": "Runtime researcher", "status": "complete", "summary": "Missing counters must stay unknown."}
        image = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="160" viewBox="0 0 640 160"><rect width="640" height="160" fill="#faf9f5"/><text x="30" y="85" font-family="serif" font-size="24" fill="#24231f">Checkpoint → workspace → owner feedback</text></svg>'
        (self.folder / "artifacts" / "flow.svg").write_text(image)
        model.checkpoint(self.folder, {"tasks": [complete, *self.snapshot["tasks"]], "agents": [builder, *self.snapshot["agents"], finished], "findings": ["Unknown counters stay unknown.", "Feedback waits for a safe checkpoint."], "artifacts": [{"path": "flow.svg", "label": "Workspace feedback flow"}]})
        source = self.root / "usage.jsonl"
        source.write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 23, "total_tokens": 123}}}}) + "\n")
        model.attach(self.folder, "parent", "codex", str(source))
        model.attach(self.folder, "reviewer", "codex", str(self.root / "waiting.jsonl"))
        self.server.telemetry = self.server.collector.poll(model.load(self.folder / "sources.json"))
        self.refresh()
        expect(self.page.locator("#token-total")).to_have_text("123")
        expect(self.page.locator("#token-caption")).to_have_text("80% of input cached · 23 output")
        expect(self.page.locator("#token-coverage")).to_have_text("1 of 4 sessions measured")
        expect(self.page.locator("#task-total")).to_have_text("1/3")
        expect(self.page.locator("#agent-total")).to_have_text("1 running")
        expect(self.page.locator("#agent-caption")).to_have_text("1 waiting · 1 done")
        expect(self.page.locator("#nav-findings")).to_have_text("2")
        done = self.page.locator('.task-group[data-key="complete"]')
        expect(done.get_by_text("Reviewed contract.")).to_be_hidden()
        done.locator("summary").click()
        expect(done).to_contain_text("Reviewed contract.")
        preview = self.page.get_by_role("img", name="Workspace feedback flow", exact=True)
        preview.scroll_into_view_if_needed()
        expect(preview).to_be_visible()
        self.assertTrue(preview.evaluate("node => node.complete && node.naturalWidth > 0"))
        with self.page.expect_download() as transfer:
            self.page.get_by_role("link", name="Download Workspace feedback flow", exact=True).click()
        artifact = self.root / "download.svg"
        transfer.value.save_as(artifact)
        self.assertEqual(artifact.read_text(), image)
        expect(self.page.locator('.agent[data-agent-id="parent"] .agent-tokens')).to_contain_text("123")
        for disclosure in self.page.locator(".agent").all():
            disclosure.locator("summary").click()
        expect(self.page.locator('.agent[data-agent-id="reviewer"]')).to_contain_text("Not measured: unreadable source")
        expect(self.page.locator('.agent[data-agent-id="builder"]')).to_contain_text("Not measured: no usage source attached")
        self.page.emulate_media(color_scheme="dark")
        for width in (320, 390, 650, 768, 970, 1280):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"horizontal page overflow at {width}px")


if __name__ == "__main__":
    unittest.main()
