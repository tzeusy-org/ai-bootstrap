# Planning: llm_usage_exporter

Status: **not yet implemented — planning notes only.** Captured from a design conversation
in `homelab/mayor/rig` on 2026-08-10. To be picked up as a new subproject inside this
(`ai-bootstrap`) repo. This doc is scratch input for that work, not a pillar doc — once
implementation starts, fold the durable parts of this into a proper `about/` shape (or a
dedicated `openspec` change) via the `th-projects` bootstrap flow, and this file can retire.
The earlier `projects/ai-usage-telemetry` v1 offering was removed from `main` on 2026-08-15
and its backlog retired on 2026-10-21; this note is the only surviving successor record.

## Overarching goal

Track personal usage (tokens, cost proxy, session/turn counts) across every local LLM CLI
tool in daily use — Claude Code, Codex, OpenCode, and whatever gets adopted next — normalized
into a single Prometheus-queryable time series, labeled by `tool`, `model`, and `project`, so
it supports ad-hoc time-series analysis (trends over time, spend/usage by project, by model,
by tool) in the existing homelab Grafana/Prometheus stack.

Explicit non-goals: this is not a billing-accuracy system, not a security/audit trail, not a
live dashboard-in-itself (that's a follow-on, once metrics exist). It's a personal-analytics
exporter.

## Requirements

- **Reliable**: must not lose data if a session is killed mid-run, must not double-count on
  repeated/overlapping runs, must not silently break when a tool updates its on-disk format
  (should fail loud/observable, not fail silent).
- **Safe**: must never ingest or forward prompt/response *content* — token counts, model
  names, and project paths only. No new credentials — reads only local files/state each tool
  already writes for itself; no telemetry flags need to be enabled anywhere.
- **Simple**: one generic exporter core + a small per-tool adapter, not N bespoke
  integrations per tool. Adding a future tool should be a small, isolated, low-risk addition.
- **Uniform**: same metric shape and same delivery mechanism (Prometheus Pushgateway) for
  every tool, even though each tool's on-disk format differs.
- **No changes needed on the Prometheus/Grafana side**: the homelab LGTM stack's Pushgateway
  is already deployed and already scraped (confirmed live, job `lgtm-prometheus-pushgateway`)
  — this exporter only needs to push to it periodically.

## Architecture (as designed, not yet built)

1. **Per-tool adapter functions** — each walks a glob of local session files and yields
   normalized records: `(tool, model, project, input_tokens, output_tokens,
   cache_read_tokens, cache_write_tokens, timestamp)`.
2. **A local SQLite state DB** — tracks, per source file, how far it's already been read
   (line offset for line-delimited formats; seen-filenames for one-file-per-message formats),
   plus running cumulative totals per `(tool, model, project)` label combination. SQLite over
   a bespoke JSON file for crash-safety (atomic commits) given a systemd timer could in theory
   overlap a still-running previous invocation.
3. **Render + push** — each run renders *current cumulative totals* (not just the delta) as
   Prometheus text format and pushes to the Pushgateway. Pushgateway replaces-on-push rather
   than adds, so the exporter — not Pushgateway — must own the running sum. Prometheus's
   normal scrape cadence turns the pushed absolute values into a real time series that
   supports `rate()`/`increase()`.
4. **Deployment**: a systemd **user** timer (no sudo), following the exact pattern already
   established in `homelab/mayor/rig`'s `ansible/roles/host_maintenance` (added 2026-07-26,
   commit `0c42170`) — periodic job on the Tzeusy workstation, optional push-heartbeat URL
   defaulting empty so no tokens land in any repo. That role is the natural place to add a new
   task + timer unit once this exporter exists, OR this repo's own tooling can ship the
   systemd units directly if `llm_usage_exporter` should be self-contained/portable rather
   than homelab-repo-coupled. **Open question, not yet decided** — see below.

## Confirmed on-disk formats (verified directly on this machine, 2026-08-10)

All three tools already persist structured, non-content usage data locally — no RPC, no
telemetry opt-in, no native OTel needed for any of them:

| Tool | Location | Per-record shape |
|---|---|---|
| Claude Code | `~/.claude/projects/<enc-cwd>/<session>.jsonl` (one JSON object per line) | `type=="assistant"` lines: `.message.usage.{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}`, `.message.model`, `.cwd` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` | `payload.type=="token_count"` entries: `.info.last_token_usage.{input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens}` — **already a delta, not cumulative**, plus `.info.total_token_usage` (cumulative) and bonus `rate_limits` (plan type, primary window used%, credits) for free |
| OpenCode | `~/.local/share/opencode/storage/message/<session>/<msg>.json` (one JSON file per message) | `.tokens.{input,output,reasoning,cache.{read,write}}`, `.modelID`, `.providerID`, `.path.cwd` — fully self-contained per file. Sibling `~/.local/share/opencode/storage/session/<project-hash>/<session>.json` gives `.directory`/`.projectID` if session-level grouping is wanted instead of per-message `.path.cwd` |

Note: `~/.codex/` also has a `logs_2.sqlite` (1.4GB+) generic structured-log table
(`ts, level, target, feedback_log_body, thread_id, ...`) — **do not use this**, it's an
internal log dump with no stable schema contract, unlike the `token_count` JSONL payload
which is a proper session-log entry type.

## Known soft spots / open questions

1. **Codex model attribution**: the `token_count` payload doesn't carry the model name
   directly. Need to pull it from a nearby `turn_context` (or similar) payload in the same
   session file, or fall back to a coarser `tool="codex"` label with no per-model breakdown
   until that's checked. Not investigated yet.
2. **"Other future apps" caveat**: this design rides on a pattern (local structured
   per-turn/per-message usage logs) that Claude Code, Codex, and OpenCode all happen to follow
   today. A future tool that only reports usage via a cloud dashboard (no local file) would
   need its own bespoke adapter outside this pattern — not a cost today, just not guaranteed
   to hold forever.
3. **Where does this project actually live, and how does it connect to `homelab/mayor/rig`?**
   Originally scoped as a brand-new standalone GitHub repo (`llm_usage_exporter`) added to
   `rig` as a git submodule. Redirected mid-session to live inside `ai-bootstrap` instead
   (this repo) since it's already checked out on the workstation via the `~/.dotfiles`
   submodule. Not yet resolved:
   - Does `rig` need any submodule/reference back to this project at all, or does
     `ansible/roles/host_maintenance` just invoke a script at a known local path
     (`~/.dotfiles/ai-bootstrap/llm_usage_exporter/...` or `~/GitHub/ai-bootstrap/...`)  with
     no new submodule?
   - Where inside `ai-bootstrap` should the code actually live — top-level
     `llm_usage_exporter/`, under an existing pillar, or elsewhere?
   - A stray empty public GitHub repo (`Tzeusy/llm_usage_exporter`) was created during the
     original (abandoned) direction and is still sitting there unused — worth deleting
     whenever convenient.
4. **State DB location**: not yet decided where the SQLite state file should live on disk
   (e.g. `~/.cache/llm_usage_exporter/state.db` vs inside this repo's own data dir) — should
   be outside any git-tracked path regardless, it's pure local runtime state.
5. **Poll interval**: not yet decided; something in the 5-15 minute range is almost certainly
   sufficient for usage-trend analysis (this is not a low-latency use case), but pick based on
   how chatty the busiest of the three tools' session files get.

## Effort estimate (from the original conversation)

Small — core loop ~100 lines, three adapters ~30-50 lines each, SQLite schema ~20 lines, plus
whatever the deployment glue (systemd unit + timer, or an Ansible task) ends up being. A few
hours, most of the schema-discovery risk already retired by the investigation above.
