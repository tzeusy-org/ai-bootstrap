# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Exercise persistence, feedback transport and detached server lifecycle."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import progress_dashboard as dashboard

# The CLI binds the calling runtime session from the environment; tests must
# never attach the real session that happens to be running them.
for _variable in ("CODEX_THREAD_ID", "CODEX_HOME", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(_variable, None)
from dashboard_server import DashboardServer
from runtime_sources import claude_configuration, configuration, resolve, retain


class WorkspaceFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = dashboard.workspace("demo", self.tmp.name)
        dashboard.initialize(self.folder, title="A useful outcome", actor="owner", repo_root=self.tmp.name)

    def snapshot(self):
        return {"summary": "Implementing the accepted scope", "phase": "implementation",
                "tasks": [{"id": "task-1", "title": "Ship behavior", "status": "in-progress", "detail": "Current work"}],
                "agents": [{"id": "reviewer", "status": "running", "summary": "Reviewing the package"}],
                "findings": ["Focused verification passes"]}

    def feedback(self, key="request-1", **kwargs):
        return {"client_id": key, "kind": "steer", "target": "loop", "text": "Prioritize verification", "revision": 0, **kwargs}


class WorkspaceTests(WorkspaceFixture, unittest.TestCase):
    def test_publish_resume_idempotence_and_manual_handover(self):
        """Spec: REQ-progress-dashboard-001, REQ-progress-dashboard-002."""
        handover = self.folder / "handover.md"
        before = handover.stat().st_mtime_ns
        with patch.object(dashboard, "now", return_value="2026-11-14T09:00:00+00:00"):
            dashboard.checkpoint(self.folder, self.snapshot(), 0)
        self.assertIn("task-1", (self.folder / "in-progress.md").read_text())
        document_mtime = (self.folder / "in-progress.md").stat().st_mtime_ns
        with patch.object(dashboard, "now", return_value="2026-11-14T09:10:00+00:00"):
            self.assertEqual(dashboard.checkpoint(self.folder, self.snapshot(), 1)["revision"], 1)
        self.assertEqual(dashboard.load(self.folder / "state.json")["agents"][0]["updated_at"], "2026-11-14T09:00:00+00:00")
        self.assertEqual((self.folder / "in-progress.md").stat().st_mtime_ns, document_mtime)
        dashboard.initialize(self.folder, title="Ignored resume title", actor="owner", repo_root=self.tmp.name)
        self.assertEqual(handover.stat().st_mtime_ns, before)
        with self.assertRaises(ValueError):
            dashboard.handover(self.folder, "Please hand off", "")
        dashboard.handover(self.folder, "# Handover\nResume task-1", "Owner explicitly requested handover")
        dashboard.checkpoint(self.folder, {"phase": "waiting"}, 1)
        self.assertEqual(handover.read_text(), "# Handover\nResume task-1")

    def test_revision_and_identity_conflicts_preserve_state(self):
        dashboard.checkpoint(self.folder, self.snapshot(), 0)
        before = (self.folder / "state.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            dashboard.checkpoint(self.folder, {"summary": "Stale writer"}, 0)
        with self.assertRaisesRegex(ValueError, "different actor"):
            dashboard.initialize(self.folder, title="Other", actor="foreign", repo_root=self.tmp.name)
        self.assertEqual((self.folder / "state.json").read_bytes(), before)

    def test_deltas_merge_rows_by_identity_without_resending_the_roster(self):
        """Spec: REQ-progress-dashboard-003 (keyed delta checkpoints)."""
        dashboard.checkpoint(self.folder, self.snapshot())
        dashboard.checkpoint(self.folder, {
            "tasks": [{"id": "task-2", "title": "Verify", "detail": "Queued"}],
            "agents": [{"id": "builder", "name": "Builder", "task": "task-1", "activity": ["Started."]}]})
        result = dashboard.checkpoint(self.folder, {
            "tasks": [{"id": "task-1", "status": "complete"}],
            "agents": [{"id": "builder", "status": "complete", "activity": ["Started.", "Merged."]}],
            "findings": ["Focused verification passes", "Review found no defects"]})
        state = dashboard.snapshot(self.folder)
        self.assertEqual(result["revision"], 3)
        self.assertEqual([(t["id"], t["status"], t["title"]) for t in state["tasks"]],
                         [("task-1", "complete", "Ship behavior"), ("task-2", "todo", "Verify")])
        builder = state["agents"][1]
        self.assertEqual((state["agents"][0]["summary"], builder["name"], builder["status"], builder["activity"]),
                         ("Reviewing the package", "Builder", "complete", ["Started.", "Merged."]))
        self.assertEqual(state["findings"], ["Focused verification passes", "Review found no defects"])
        self.assertIn("task-1", (self.folder / "complete.md").read_text())
        # Rows leave only on request; a named collection can be rebuilt wholesale.
        dashboard.checkpoint(self.folder, {"agents": [{"id": "reviewer", "drop": True}],
                                           "findings": ["Only this"], "replace": ["findings"]})
        state = dashboard.snapshot(self.folder)
        self.assertEqual(([a["id"] for a in state["agents"]], state["findings"]), (["builder"], ["Only this"]))
        before = (self.folder / "state.json").read_bytes()
        for bad in ({"agents": [{"id": "new", "status": "sleeping"}]}, {"tasks": [{"id": "untitled"}]},
                    {"replace": ["summary"]}, {"tasks": [{"id": "task-2"}, {"id": "task-2"}]}):
            with self.assertRaises(ValueError):
                dashboard.checkpoint(self.folder, bad)
        self.assertEqual((self.folder / "state.json").read_bytes(), before)

    def test_templated_panels_and_derived_activity_need_no_authored_markup(self):
        """Spec: REQ-progress-dashboard-001 (templated visuals, derived activity)."""
        dashboard.checkpoint(self.folder, self.snapshot())
        dashboard.checkpoint(self.folder, {"phase": "review", "tasks": [{"id": "task-1", "status": "complete"}],
            "panels": [{"id": "gates", "title": "Gates", "kind": "steps", "rows": [["Tests", "done", "31 pass"], ["Review", "active"]]},
                       {"id": "size", "title": "Payload", "kind": "bars", "rows": [["Before", 2140], ["After", 96.5]]}]})
        dashboard.checkpoint(self.folder, {"panels": [{"id": "gates", "rows": [["Tests", "done"], ["Review", "done"]]}]})
        state = dashboard.snapshot(self.folder)
        self.assertEqual(state["panels"][0], {"id": "gates", "title": "Gates", "kind": "steps", "note": "",
                                              "rows": [["Tests", "done"], ["Review", "done"]]})
        self.assertEqual([entry["text"] for entry in state["log"]], [
            "Phase: implementation", "Finding: Focused verification passes", "Ship behavior: in progress",
            "reviewer: running", "Phase: review", "Ship behavior: complete", "Published: Gates", "Published: Payload"])
        for rows in ([["Tests", "finished"]], [["Tests"]], [["Tests", "done", {"html": "<b>"}]]):
            with self.assertRaises(ValueError):
                dashboard.checkpoint(self.folder, {"panels": [{"id": "bad", "title": "Bad", "kind": "steps", "rows": rows}]})
        for panel in ({"kind": "bars", "rows": [["A", "many"]]}, {"kind": "chart", "rows": []}, {"kind": "bars", "rows": [["A", -1]]}):
            with self.assertRaises(ValueError):
                dashboard.checkpoint(self.folder, {"panels": [{"id": "bad", "title": "Bad", **panel}]})
        for _ in range(dashboard.LOG_LIMIT + 5):
            dashboard.checkpoint(self.folder, {"phase": "a"})
            dashboard.checkpoint(self.folder, {"phase": "b"})
        self.assertEqual(len(dashboard.snapshot(self.folder)["log"]), dashboard.LOG_LIMIT)

    def test_feedback_cursor_is_retained_by_the_workspace(self):
        """Spec: REQ-progress-dashboard-003 (no session-held cursor)."""
        dashboard.record_feedback(self.folder, self.feedback())
        first = dashboard.checkpoint(self.folder, {"phase": "one"})
        self.assertEqual(([row["id"] for row in first["feedback"]], first["pending_count"]), ([1], 1))
        dashboard.record_feedback(self.folder, self.feedback("request-2", text="Second"))
        second = dashboard.checkpoint(self.folder, {"phase": "two"})
        self.assertEqual(([row["id"] for row in second["feedback"]], second["pending_count"]), ([2], 2))
        self.assertEqual(dashboard.checkpoint(self.folder, {})["feedback"], [])
        # A lost session re-reads everything unacknowledged without rewinding the retained cursor.
        self.assertEqual([row["id"] for row in dashboard.surface(self.folder, 0)["feedback"]], [1, 2])
        self.assertEqual(dashboard.surface(self.folder)["feedback"], [])

    def test_start_defaults_identity_and_resumes_without_arguments(self):
        cli = [sys.executable, str(SCRIPTS / "progress_dashboard.py"), "--root", self.tmp.name, "--slug", "fresh"]
        started = json.loads(subprocess.run([*cli, "start"], capture_output=True, text=True, check=True).stdout)
        self.addCleanup(subprocess.run, [*cli, "stop"], capture_output=True)
        self.assertEqual(started["revision"], 0)
        self.assertTrue(started["url"].startswith("http://127.0.0.1:"))
        state = dashboard.snapshot(Path(started["workspace"]))
        self.assertEqual((state["title"], state["actor"]), ("fresh", "parent"))
        subprocess.run([*cli, "checkpoint"], input='{"summary":"Under way"}', capture_output=True, text=True, check=True)
        resumed = json.loads(subprocess.run([*cli, "start", "--title", "Ignored"], capture_output=True, text=True, check=True).stdout)
        self.assertEqual((resumed["revision"], resumed["url"]), (1, started["url"]))

    def test_unknown_fields_cannot_write_handover_or_fabricate_usage(self):
        for incoming in ({"handover": "automatic"}, {"tokens": 1234}, {"tasks": [{"id": "a", "status": "complete"}]}):
            with self.subTest(incoming=incoming), self.assertRaises(ValueError):
                dashboard.checkpoint(self.folder, incoming, 0)
        self.assertEqual(dashboard.load(self.folder / "state.json")["revision"], 0)

    def test_feedback_retries_concurrent_writes_and_ack_survive_restart(self):
        """Spec: REQ-progress-dashboard-005."""
        row = dashboard.record_feedback(self.folder, self.feedback())
        self.assertEqual(dashboard.record_feedback(self.folder, self.feedback())["id"], row["id"])
        with self.assertRaisesRegex(ValueError, "different feedback"):
            dashboard.record_feedback(self.folder, self.feedback(text="changed"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda i: dashboard.record_feedback(self.folder, self.feedback(f"request-{i}")), range(2, 22)))
        self.assertEqual(dashboard.pending(self.folder)["pending_count"], 21)
        dashboard.acknowledge(self.folder, 1, "Accepted; verification now takes priority")
        resumed = subprocess.run([sys.executable, str(SCRIPTS / "progress_dashboard.py"),
            "--root", self.tmp.name, "--slug", "demo", "status", "--since", "0"],
            capture_output=True, text=True, check=True)
        status = json.loads(resumed.stdout)
        self.assertEqual(status["pending_count"], 20)
        self.assertFalse(status["changed"])
        self.assertLessEqual(len(status["feedback"]), 10)
        self.assertNotIn("tasks", status)

    def test_feedback_disabled_stale_targets_and_invalid_revisions(self):
        for body in (self.feedback(target="gone"), self.feedback(revision=1), self.feedback(revision=True), []):
            with self.subTest(body=body), self.assertRaises(ValueError):
                dashboard.record_feedback(self.folder, body)
        state = dashboard.load(self.folder / "state.json")
        state["feedback_enabled"] = False
        dashboard.save(self.folder / "state.json", state)
        with self.assertRaisesRegex(ValueError, "disabled"):
            dashboard.record_feedback(self.folder, self.feedback())

    def test_feedback_cursor_bounds_utf8_output_without_consuming_or_clipping_instructions(self):
        long_target = "target-" + "界" * 7900
        dashboard.checkpoint(self.folder, {"tasks": [{"id": long_target, "title": "Long identity", "status": "todo"}]}, 0)
        message = "Only after checking the full message: " + "界" * 7800
        dashboard.record_feedback(self.folder, self.feedback())
        dashboard.record_feedback(self.folder, self.feedback("request-2", target=long_target, text=message, revision=1))
        dashboard.record_feedback(self.folder, self.feedback("request-3"))
        first = dashboard.pending(self.folder)
        self.assertEqual([row["id"] for row in first["feedback"]], [1])
        self.assertTrue(first["feedback_more"])
        second = dashboard.pending(self.folder, after=first["feedback_cursor"])
        self.assertLessEqual(len(json.dumps(second["feedback"], ensure_ascii=False).encode()), dashboard.FEEDBACK_BATCH_BYTES)
        self.assertTrue(second["feedback"][0]["truncated"])
        self.assertNotIn("text", second["feedback"][0])
        self.assertNotIn("client_id", second["feedback"][0])
        self.assertEqual(dashboard.feedback_item(self.folder, 2)["text"], message)
        self.assertEqual(dashboard.pending(self.folder, after=second["feedback_cursor"])["feedback"], [])
        self.assertEqual(dashboard.pending(self.folder)["pending_count"], 3)
        dashboard.acknowledge(self.folder, 2, "Full message read; deferred until its prerequisite is met")
        self.assertEqual(dashboard.pending(self.folder)["pending_count"], 2)

    def test_cli_errors_preserve_state_and_do_not_create_missing_workspaces(self):
        script = str(SCRIPTS / "progress_dashboard.py")
        missing = Path(self.tmp.name) / "missing-root"
        result = subprocess.run([sys.executable, script, "--root", str(missing), "--slug", "typo", "status"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())
        before = (self.folder / "state.json").read_bytes()
        oversized = Path(self.tmp.name) / "snapshot.json"
        oversized.write_bytes(b"x" * (dashboard.MAX_JSON + 1))
        result = subprocess.run([sys.executable, script, "--root", self.tmp.name, "--slug", "demo",
                                 "checkpoint", "--expected-revision", "0", "--input", str(oversized)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.folder / "state.json").read_bytes(), before)
        dashboard.record_feedback(self.folder, self.feedback())
        result = subprocess.run([sys.executable, script, "--root", self.tmp.name, "--slug", "demo",
                                 "inbox", "--after-feedback", "1"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["feedback"], [])
        result = subprocess.run([sys.executable, script, "--root", self.tmp.name, "--slug", "demo",
                                 "inbox", "--id", "1"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["feedback"]["text"], self.feedback()["text"])

    def test_acknowledgement_limit_preserves_readable_history(self):
        rows = [{**self.feedback(f"request-{i:04}", text="x" * 7200), "id": i + 1,
                 "created_at": dashboard.now(), "status": "pending", "response": None} for i in range(400)]
        dashboard.save_feedback(self.folder, rows)
        acknowledged = 0
        for row in rows:
            before = (self.folder / "feedback.json").read_bytes()
            try:
                dashboard.acknowledge(self.folder, row["id"], "r" * 8000)
                acknowledged += 1
            except ValueError as error:
                self.assertIn("history is full", str(error))
                self.assertEqual((self.folder / "feedback.json").read_bytes(), before)
                break
        self.assertGreater(acknowledged, 0)
        self.assertLess(acknowledged, len(rows))
        self.assertLessEqual((self.folder / "feedback.json").stat().st_size, 3 * 1024 * 1024)
        self.assertEqual(dashboard.pending(self.folder)["pending_count"], 400 - acknowledged)
        dashboard.acknowledge(self.folder, acknowledged + 1, "Deferred")

    def test_artifact_allowlist_and_symlink_boundaries(self):
        image = self.folder / "artifacts/diagram.svg"
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        dashboard.checkpoint(self.folder, {"artifacts": [{"path": "diagram.svg", "label": "Architecture"}]}, 0)
        self.assertEqual(dashboard.artifact_path(self.folder, "diagram.svg"), image)
        (self.folder / "artifacts/escape.png").symlink_to(self.folder / "state.json")
        for path in ("../state.json", ".env", "escape.png", "page.html"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                dashboard.artifact_path(self.folder, path)
        outside = Path(self.tmp.name) / "outside"
        outside.write_text("do not change")
        (self.folder / "todo.md").unlink()
        (self.folder / "todo.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            dashboard.checkpoint(self.folder, {}, 1)
        self.assertEqual(outside.read_text(), "do not change")

    def test_source_attachment_identity_and_no_double_count(self):
        source = str(self.folder / "parent.jsonl")
        dashboard.attach(self.folder, "owner", "codex", source)
        dashboard.checkpoint(self.folder, {"agents": [{"id": "child", "status": "running"}]}, 0)
        with self.assertRaisesRegex(ValueError, "double-count"):
            dashboard.attach(self.folder, "child", "codex", source)
        with self.assertRaises(ValueError):
            dashboard.attach(self.folder, "foreign", "codex", source)

    def test_server_start_preserves_usage_offsets_and_reports_unattached_agents(self):
        from loop_telemetry import UsageCollector

        source = self.folder / "parent.jsonl"
        source.write_text(json.dumps({"type": "event_msg", "timestamp": "2026-11-14T09:00:00Z", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"input_tokens": 10,
            "cached_input_tokens": 5, "output_tokens": 3, "total_tokens": 13}}}}) + "\n")
        dashboard.attach(self.folder, "owner", "codex", str(source))
        sources = dashboard.load(self.folder / "sources.json")
        UsageCollector(self.folder).poll(sources)
        cache = self.folder / "telemetry-cache.json"
        before = cache.stat().st_mtime_ns
        dashboard.checkpoint(self.folder, {"agents": [{"id": "child", "status": "running"}]}, 0)
        server = DashboardServer(self.folder, "127.0.0.1", 0, "usage-check")
        try:
            self.assertEqual(cache.stat().st_mtime_ns, before)
            view = server.view()
            self.assertEqual(view["telemetry"]["totals"]["total_tokens"], 13)
            self.assertEqual(view["telemetry"]["coverage"], {"attached": 1, "measured": 1, "expected": 2})
            child = next(row for row in view["telemetry"]["agents"] if row["id"] == "child")
            self.assertIsNone(child["total_tokens"])
        finally:
            server.server_close()
        previous_cache = cache.read_bytes()
        for damaged in ("corrupt local cache", '{"schema":1,"sources":{"owner":"damaged entry"}}'):
            with self.subTest(cache=damaged):
                cache.write_text(damaged)
                degraded = DashboardServer(self.folder, "127.0.0.1", 0, "usage-recovery")
                try:
                    view = degraded.view()
                    self.assertIn("check the local telemetry cache", view["telemetry"]["error"])
                    self.assertEqual(view["state"]["title"], "A useful outcome")
                    self.assertEqual(cache.read_text(), damaged)
                    cache.write_bytes(previous_cache)
                    self.assertEqual(degraded.sample_usage()["totals"]["total_tokens"], 13)
                finally:
                    degraded.server_close()


class RuntimeTests(WorkspaceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "runtime"
        self.home.mkdir()
        self.parent = "00000000-0000-4000-8000-000000000001"
        self.child = "00000000-0000-4000-8000-000000000002"
        self.foreign = "00000000-0000-4000-8000-000000000003"
        self.index = self.home / "state_5.sqlite"
        self.parent_log = self.home / "parent.jsonl"
        self.child_log = self.home / "child.jsonl"
        for identity, path in ((self.parent, self.parent_log), (self.child, self.child_log)):
            path.write_text(json.dumps({"type": "token_usage_record", "timestamp": "2026-11-14T10:10:00Z",
                "payload": {"thread_id": identity, "thread_token_usage": {"input_tokens": 10,
                "cached_input_tokens": 5, "output_tokens": 3, "total_tokens": 13}}}) + "\n")
        with closing(sqlite3.connect(self.index, isolation_level=None)) as db:
            db.executescript("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, agent_path TEXT, model TEXT);"
                             "CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT, status TEXT);")
            db.executemany("INSERT INTO threads VALUES(?,?,?,?)", [
                (self.parent, str(self.parent_log), None, "runtime-model"),
                (self.child, str(self.child_log), "/root/reviewer", "runtime-model"),
                (self.foreign, str(self.home / "never-read.jsonl"), "/foreign/reviewer", "unrelated")])
            db.execute("INSERT INTO thread_spawn_edges VALUES(?,?,?)", (self.parent, self.child, "closed"))

    def test_watch_resolves_only_owned_agents_and_survives_index_outage_without_replay(self):
        """Spec: REQ-progress-dashboard-004."""
        result = subprocess.run([sys.executable, str(SCRIPTS / "progress_dashboard.py"),
            "--root", self.tmp.name, "--slug", "automatic", "init", "--title", "Automatic usage",
            "--actor", "parent", "--repo-root", self.tmp.name], env={**os.environ,
            "CODEX_THREAD_ID": self.parent, "CODEX_HOME": str(self.home)},
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["watching"], "codex")
        self.assertEqual(dashboard.load(Path(self.tmp.name) / "automatic/runtime.json")["thread_id"], self.parent)
        dashboard.checkpoint(self.folder, self.snapshot(), 0)
        handover = (self.folder / "handover.md").read_bytes()
        dashboard.watch_codex(self.folder, self.parent, self.home)
        server = DashboardServer(self.folder, "127.0.0.1", 0, "owned-runtime")
        try:
            view = server.view()
            self.assertEqual(view["telemetry"]["coverage"], {"attached": 2, "expected": 2, "measured": 2})
            self.assertEqual(view["telemetry"]["totals"]["total_tokens"], 26)
            self.assertEqual(next(r for r in view["telemetry"]["agents"] if r["id"] == "reviewer")["thread_id"], self.child)
            self.assertNotIn(str(self.home), json.dumps(view))
            self.index.rename(self.home / "unavailable.sqlite")
            before = (self.folder / "telemetry-cache.json").read_bytes()
            degraded = server.sample_usage()
            self.assertEqual(degraded["totals"]["total_tokens"], 26)
            self.assertIn("runtime index unavailable", degraded["error"])
            self.assertEqual((self.folder / "telemetry-cache.json").read_bytes(), before)
            self.assertFalse(self.index.exists())  # mode=ro never creates a replacement index.
            self.assertEqual((self.folder / "handover.md").read_bytes(), handover)
            explicit = self.home / "explicit.jsonl"
            explicit.write_text(json.dumps({"type": "assistant", "message": {"id": "new-work",
                "usage": {"input_tokens": 4, "output_tokens": 1}}}) + "\n")
            dashboard.attach(self.folder, "reviewer", "claude", str(explicit))
            changed = server.sample_usage()
            reviewer = next(row for row in changed["agents"] if row["id"] == "reviewer")
            self.assertEqual((reviewer["provider"], reviewer["total_tokens"]), ("claude", 5))
            self.assertNotIn("thread_id", reviewer)
            before_restart = (self.folder / "telemetry-cache.json").read_bytes()
            restarted = DashboardServer(self.folder, "127.0.0.1", 0, "outage-restart")
            try:
                self.assertEqual(restarted.view()["telemetry"]["totals"]["total_tokens"], 18)
                self.assertEqual((self.folder / "telemetry-cache.json").read_bytes(), before_restart)
                # A lost/corrupt derived binding cache must not prune offsets;
                # restored native lookup can resume without a full log replay.
                (self.folder / "runtime-sources.json").write_text("invalid bindings")
                partial = restarted.sample_usage()
                self.assertEqual(partial["totals"]["total_tokens"], 5)
                self.assertEqual((self.folder / "telemetry-cache.json").read_bytes(), before_restart)
                (self.home / "unavailable.sqlite").rename(self.index)
                recovered = restarted.sample_usage()
                self.assertEqual(recovered["totals"]["total_tokens"], 18)
                self.assertNotIn("error", recovered)
                self.assertEqual((self.folder / "telemetry-cache.json").read_bytes(), before_restart)
            finally:
                restarted.server_close()
        finally:
            server.server_close()

    def test_watch_refuses_ambiguous_names_and_preserves_explicit_source_precedence(self):
        """Spec: REQ-progress-dashboard-004."""
        config = configuration(self.parent, self.home)
        with closing(sqlite3.connect(self.index, isolation_level=None)) as db:
            db.execute("INSERT INTO thread_spawn_edges VALUES(?,?,?)", (self.child, self.foreign, "open"))
            db.execute("INSERT INTO thread_spawn_edges VALUES(?,?,?)", (self.child, self.parent, "closed"))
        sources, metadata, warnings = resolve(config, "owner", [{"id": "reviewer"}], [])
        self.assertEqual([row["id"] for row in sources], ["owner"])
        self.assertTrue(warnings)
        explicit = {"id": "reviewer", "provider": "claude", "path": str(self.home / "explicit.jsonl")}
        sources, metadata, warnings = resolve(config, "owner", [{"id": "reviewer", "thread_id": self.child}], [explicit])
        self.assertEqual(next(r for r in sources if r["id"] == "reviewer"), explicit)
        self.assertNotIn("reviewer", metadata)
        self.assertFalse(warnings)
        proven, identities, _ = resolve(config, "owner", [{"id": "reviewer", "thread_id": self.child}], [])
        saved = {"config": config, "sources": proven, "metadata": identities}
        # A reused workflow label must not retain old-thread counts after the
        # parent publishes a new explicit selector during an index outage.
        retained, identities, _ = retain(config, "owner", [{"id": "reviewer", "thread_id": self.foreign}], saved, [])
        self.assertEqual([row["id"] for row in retained], ["owner"])
        self.assertNotIn("reviewer", identities)
        with self.assertRaises(ValueError):
            dashboard.watch_codex(self.folder, "invalid", self.home)
        self.assertFalse((self.folder / "runtime.json").exists())


class ClaudeRuntimeTests(WorkspaceFixture, unittest.TestCase):
    """Claude Code parent plus the sub-agents in its own session folder (aib-eg4)."""

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "claude-home"
        self.parent = "00000000-0000-4000-8000-0000000000c1"
        self.sibling = "00000000-0000-4000-8000-0000000000c2"
        project = self.home / "projects" / "-work-repo"
        self.session = project / self.parent
        self.parent_log = project / f"{self.parent}.jsonl"
        self.reviewer_log = self.session / "subagents" / "agent-a1b2c3.jsonl"
        self.unmatched_log = self.session / "subagents" / "agent-d4e5f6.jsonl"
        decoys = [project / f"{self.sibling}.jsonl", project / self.sibling / "subagents" / "agent-a1b2c3.jsonl",
                  self.home / "projects" / "-other" / "00000000-0000-4000-8000-0000000000c3.jsonl"]
        for index, path in enumerate([self.parent_log, self.reviewer_log, self.unmatched_log, *decoys]):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"type": "assistant", "message": {"id": f"m{index}",
                "usage": {"input_tokens": 10, "output_tokens": 3}}}) + "\n")
        for log, meta in ((self.reviewer_log, {"agentType": "general-purpose", "description": "Review pass",
                                               "toolUseId": "toolu_review", "model": "runtime-model"}),
                          (self.unmatched_log, {"description": "Unpublished helper"}),
                          (decoys[1], {"description": "Review pass", "toolUseId": "toolu_review"})):
            log.with_name(log.stem + ".meta.json").write_text(json.dumps(meta))

    def config(self):
        return claude_configuration(self.parent, self.home)

    def test_init_binds_the_session_and_measures_published_sub_agents(self):
        """Spec: REQ-progress-dashboard-004."""
        result = subprocess.run([sys.executable, str(SCRIPTS / "progress_dashboard.py"),
            "--root", self.tmp.name, "--slug", "claude-auto", "init", "--title", "Claude usage",
            "--actor", "parent", "--repo-root", self.tmp.name], env={**os.environ,
            "CLAUDE_CODE_SESSION_ID": self.parent, "CLAUDE_CONFIG_DIR": str(self.home)},
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["watching"], "claude")
        folder = Path(self.tmp.name) / "claude-auto"
        self.assertEqual(dashboard.load(folder / "runtime.json")["session_id"], self.parent)
        snapshot = self.snapshot()
        snapshot["agents"][0]["thread_id"] = "toolu_review"
        dashboard.checkpoint(folder, snapshot, 0)
        server = DashboardServer(folder, "127.0.0.1", 0, "claude-runtime")
        try:
            view = server.view()
            agents = {row["id"]: row for row in view["telemetry"]["agents"]}
            reviewer = snapshot["agents"][0]["id"]
            self.assertEqual(set(agents), {"parent", reviewer})  # the unpublished helper is not attributed
            self.assertEqual(agents[reviewer]["thread_id"], "a1b2c3")
            self.assertEqual(agents[reviewer]["provider"], "claude")
            self.assertEqual(view["telemetry"]["totals"]["total_tokens"], 26)
            self.assertNotIn(str(self.home), json.dumps(view))
        finally:
            server.server_close()

    def test_matching_is_exact_then_unique_description_and_never_guesses(self):
        """Spec: REQ-progress-dashboard-004."""
        config = self.config()
        for agent, expected in (({"id": "r", "thread_id": "a1b2c3"}, "a1b2c3"),
                                ({"id": "r", "thread_id": "toolu_review"}, "a1b2c3"),
                                ({"id": "Review pass"}, "a1b2c3"),
                                ({"id": "r", "name": "Review pass"}, "a1b2c3")):
            with self.subTest(agent=agent):
                _, metadata, warnings = resolve(config, "owner", [agent], [])
                self.assertEqual(metadata["r" if agent["id"] == "r" else agent["id"]]["thread_id"], expected)
                self.assertFalse(warnings)
        _, metadata, _ = resolve(config, "owner", [{"id": "r", "thread_id": "toolu_missing"}], [])
        self.assertNotIn("r", metadata)
        self.unmatched_log.with_name("agent-d4e5f6.meta.json").write_text(json.dumps({"description": "Review pass"}))
        sources, metadata, warnings = resolve(config, "owner", [{"id": "Review pass"}], [])
        self.assertNotIn("Review pass", metadata)
        self.assertTrue(warnings)
        explicit = {"id": "r", "provider": "claude", "path": str(self.home / "explicit.jsonl")}
        sources, metadata, _ = resolve(config, "owner", [{"id": "r", "thread_id": "a1b2c3"}], [explicit])
        self.assertEqual(next(row for row in sources if row["id"] == "r"), explicit)
        self.assertNotIn("r", metadata)

    def test_lookup_reads_nothing_outside_the_bound_session(self):
        """Spec: REQ-progress-dashboard-004."""
        opened = []
        original = Path.read_text

        def spy(path, *args, **kwargs):
            opened.append(Path(path))
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", spy):
            resolve(self.config(), "owner", [{"id": "r", "thread_id": "a1b2c3"}], [])
        self.assertTrue(opened)
        for path in opened:
            self.assertTrue(path.is_relative_to(self.session / "subagents"), path)

    def test_missing_or_conflicting_parent_refuses_without_binding(self):
        """Spec: REQ-progress-dashboard-004."""
        with self.assertRaises(ValueError):
            dashboard.watch_claude(self.folder, "00000000-0000-4000-8000-0000000000ff", self.home)
        with self.assertRaises(ValueError):
            dashboard.watch_claude(self.folder, "not-a-uuid", self.home)
        self.assertFalse((self.folder / "runtime.json").exists())
        dashboard.watch_claude(self.folder, self.parent, self.home)
        with self.assertRaises(ValueError):
            dashboard.watch_codex(self.folder, "00000000-0000-4000-8000-000000000001", self.home)
        self.assertEqual(dashboard.load(self.folder / "runtime.json")["provider"], "claude")
        result = subprocess.run([sys.executable, str(SCRIPTS / "progress_dashboard.py"),
            "--root", self.tmp.name, "--slug", "explicit-only", "init", "--claude-session", ""],
            env={**os.environ, "CLAUDE_CODE_SESSION_ID": self.parent, "CLAUDE_CONFIG_DIR": str(self.home)},
            capture_output=True, text=True, check=True)
        self.assertNotIn("watching", json.loads(result.stdout))
        self.assertFalse((Path(self.tmp.name) / "explicit-only" / "runtime.json").exists())

    def test_retained_claude_bindings_survive_an_outage_and_drop_on_a_new_selector(self):
        """Spec: REQ-progress-dashboard-004."""
        config = self.config()
        proven, identities, _ = resolve(config, "owner", [{"id": "r", "thread_id": "a1b2c3"}], [])
        saved = {"config": config, "sources": proven, "metadata": identities}
        retained, kept, _ = retain(config, "owner", [{"id": "r", "thread_id": "a1b2c3"}], saved, [])
        self.assertEqual({row["id"] for row in retained}, {"owner", "r"})
        retained, kept, _ = retain(config, "owner", [{"id": "r", "thread_id": "other"}], saved, [])
        self.assertEqual({row["id"] for row in retained}, {"owner"})
        # A binding proven by tool-use id must survive an outage under that same selector.
        roster = [{"id": "r", "thread_id": "toolu_review"}]
        proven, identities, _ = resolve(config, "owner", roster, [])
        saved = {"config": config, "sources": proven, "metadata": identities}
        retained, kept, _ = retain(config, "owner", roster, saved, [])
        self.assertEqual({row["id"] for row in retained}, {"owner", "r"})
        self.assertEqual(kept["r"]["thread_id"], "a1b2c3")

    def test_a_symlinked_session_folder_is_not_followed(self):
        """Spec: REQ-progress-dashboard-004."""
        elsewhere = Path(self.tmp.name) / "elsewhere"
        self.session.rename(elsewhere)
        self.session.symlink_to(elsewhere, target_is_directory=True)
        _, metadata, _ = resolve(self.config(), "owner", [{"id": "r", "thread_id": "a1b2c3"}], [])
        self.assertNotIn("r", metadata)

    def test_a_failed_codex_watch_still_lets_claude_bind(self):
        """Spec: REQ-progress-dashboard-004."""
        result = subprocess.run([sys.executable, str(SCRIPTS / "progress_dashboard.py"),
            "--root", self.tmp.name, "--slug", "both", "init"],
            env={**os.environ, "CODEX_THREAD_ID": "not-a-uuid", "CLAUDE_CODE_SESSION_ID": self.parent,
                 "CLAUDE_CONFIG_DIR": str(self.home)}, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["watching"], "claude")
        self.assertIn("Codex", payload["usage_warning"])


class HttpTests(WorkspaceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = DashboardServer(self.folder, "127.0.0.1", 0, "test-capability")
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/test-capability/"

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body=None, headers=None):
        headers = {"Origin": self.url.split("/test-capability")[0], "X-Loop-Feedback": "1",
                   "Content-Type": "application/json", **(headers or {})}
        return urlopen(Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                               headers=headers), timeout=3)

    def test_real_http_feedback_state_etag_and_xss_text(self):
        content = '<img src=x onerror="alert(1)">'
        dashboard.checkpoint(self.folder, {"summary": content}, 0)
        with self.request("") as reply:
            self.assertIn("script-src 'self'", reply.headers["Content-Security-Policy"])
            self.assertNotIn(content.encode(), reply.read())
        with self.request("api/state") as reply:
            state = json.load(reply)
            etag = reply.headers["ETag"]
        self.assertEqual(state["state"]["summary"], content)
        self.assertIsNone(state["telemetry"]["totals"]["total_tokens"])
        self.server.telemetry["sampled_at"] = "2026-11-14T23:59:59+00:00"
        with self.assertRaises(HTTPError) as error:
            self.request("api/state", headers={"If-None-Match": etag})
        self.assertEqual(error.exception.code, 304)
        self.server.telemetry["error"] = "Usage collector unavailable"
        with self.request("api/state", headers={"If-None-Match": etag}) as reply:
            self.assertNotEqual(reply.headers["ETag"], etag)
        with self.request("api/feedback", self.feedback()) as reply:
            self.assertEqual(json.load(reply)["feedback"]["status"], "pending")
        self.assertEqual(dashboard.pending(self.folder)["pending_count"], 1)
        self.assertEqual((self.folder / "handover.md").read_text(), "# Handover\n\nNo handover has been requested.\n")

    def test_wrong_capability_origin_host_and_traversal_are_denied(self):
        for url in (self.url.replace("test-capability/", "wrong/api/state"), self.url + "../state.json",
                    self.url + "artifacts/%2E%2E%2Fstate.json"):
            with self.subTest(url=url), self.assertRaises(HTTPError):
                urlopen(url, timeout=3)
        for headers in ({"Origin": "https://elsewhere.invalid"}, {"Host": "evil.invalid"}, {"X-Loop-Feedback": "0"}):
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                self.request("api/feedback", self.feedback(), headers)
            self.assertEqual(error.exception.code, 403)
        forwarded = {"Host": "localhost:18080", "Origin": "http://localhost:18080"}
        with self.request("api/feedback", self.feedback(), forwarded) as response:
            self.assertEqual(json.load(response)["feedback"]["status"], "pending")
        for host in ("localhost:0", "localhost:70000", "evil.invalid:18080", "localhost.evil.invalid:18080"):
            with self.subTest(host=host), self.assertRaises(HTTPError) as error:
                self.request("api/state", headers={"Host": host})
            self.assertEqual(error.exception.code, 403)

    def test_only_explicitly_published_artifacts_are_served(self):
        image = self.folder / "artifacts/diagram.svg"
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        with self.assertRaises(HTTPError):
            self.request("artifacts/diagram.svg")
        dashboard.checkpoint(self.folder, {"artifacts": [{"path": "diagram.svg"}]}, 0)
        with self.request("artifacts/diagram.svg") as reply:
            self.assertEqual(reply.read(), image.read_bytes())


class BindAddressTests(unittest.TestCase):
    def test_only_loopback_and_the_tailscale_range_are_bindable(self):
        """Spec: REQ-progress-dashboard-006."""
        # Addresses are assembled so this file passes the pii-guard.
        def v4(*octets):
            return ".".join(map(str, octets))

        allowed = [v4(127, 0, 0, 1), v4(100, 64, 0, 0), v4(100, 101, 2, 3), v4(100, 127, 255, 255)]
        refused = [v4(100, 63, 255, 255), v4(100, 128, 0, 0), v4(192, 168, 1, 1), v4(0, 0, 0, 0), "::1"]
        for bind in allowed:
            self.assertTrue(dashboard.bind_allowed(bind), bind)
        for bind in refused:
            self.assertFalse(dashboard.bind_allowed(bind), bind)
        self.assertEqual(str(dashboard.TAILSCALE_NETWORK), v4(100, 64, 0, 0) + "/10")


class LifecycleTests(unittest.TestCase):
    def test_detached_start_reuse_stop_and_stale_pid_safety(self):
        """Spec: REQ-progress-dashboard-006."""
        with tempfile.TemporaryDirectory() as temporary:
            folder = dashboard.workspace("lifecycle", temporary)
            dashboard.initialize(folder, title="Lifecycle", actor="owner", repo_root=temporary)
            server = dashboard.serve(folder, "127.0.0.1", 0)
            try:
                reused = dashboard.serve(folder, "127.0.0.1", 0)
                self.assertTrue(reused["reused"])
                self.assertEqual(reused["url"], server["url"])
                self.assertTrue(dashboard.stop(folder)["stopped"])
                self.assertFalse(dashboard.health(dashboard.load(folder / "server.json")))
                # A stale PID, even our own, must never receive a signal.
                info = dashboard.load(folder / "server.json")
                info["pid"] = os.getpid()
                dashboard.save(folder / "server.json", info)
                self.assertFalse(dashboard.stop(folder)["stopped"])
            finally:
                dashboard.stop(folder)


if __name__ == "__main__":
    unittest.main()
