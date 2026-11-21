## Placement and data flow

`th-projects/subskills/progress-dashboard` owns all implementation. Fixed assets
and Python scripts serve any workflow. Beads integration carries only launch,
checkpoint and retirement instructions; it does not own UI or telemetry logic.

```mermaid
flowchart LR
    W[Owning workflow checkpoint] --> C[Validated partial snapshot]
    C --> J[Local state.json]
    J --> M[Three generated Markdown views]
    J --> S[Session background server]
    R[Configured Codex parent] --> X[Read-only indexed spawn relationships]
    X --> T[Matched parent / published child usage logs]
    E[Explicit attached logs] --> T
    T --> I[Bounded incremental collector]
    I --> S
    S --> B[Browser progress and agent overview]
    B --> F[Durable feedback inbox]
    F --> W
    O[Explicit manual owner request] --> H[handover.md]
```

## Efficiency and observations

The server polls attached usage sources at most once per three-second tick and
reads at most 1MiB of new bytes per source. Counters/offsets persist across restarts;
unchanged polls do not rewrite the cache. The browser independently refreshes.
Neither path calls a model, queries Beads/GitHub, or creates an agent. Parent
checkpoints publish changed fields and return cursor-based feedback batches capped
at ten entries and 8KiB. Surfaced items remain pending until disposition; an
oversized item is visibly a preview and requires an exact full-item read before
interpretation. Native
delegation/report events supply child summaries; no transcript summarizer runs.
Agent work details retain these reports, up to eight recent actions, and known
branch/worktree/PR context. Missing context remains unreported. Stable disclosure
shells keep those details open while runtime counters change.

Codex initialization binds `CODEX_THREAD_ID` once (or an explicit parent UUID).
Every server tick queries only that parent's indexed spawn relationships in the
local read-only `state_5.sqlite`, bounded at200 sessions. Published roster IDs,
runtime paths or explicit thread IDs identify children; ambiguous names refuse
attribution. No transcript directory is crawled. Explicit nonmatching attachments
retain precedence; duplicate log attribution refuses. A temporary index failure
keeps established sources/offsets across restart and displays a warning. Derived
`runtime-sources.json` bindings are configuration-pinned and written only when
changed; current explicit attachments still win during an outage. Failed lookup
never prunes unresolved parser offsets, even if the derived binding cache is
damaged. No unrelated session
content, index path or transcript path is returned to the browser.

Claude Code initialization binds `CLAUDE_CODE_SESSION_ID` the same way when no
binding exists. The parent is found by name only under `projects/*/`; each tick
lists only that session's own `subagents/` folder (bounded at 200) and reads its
agents' small descriptors to match published IDs (agent id, tool-use id, then a
unique description). Other sessions and projects are never read; the same
precedence, conflict, retention and outage rules apply.

Native Codex `token_usage_record` totals are filtered by thread identity and
supersede lagging legacy `event_msg/token_count` emission. Copied fork history is
excluded using native history ordinals; later samples never add both streams.

Conditional browser responses ignore collector heartbeat timestamps when all
meaningful content is unchanged. UI rows and disclosure controls retain identity
through unrelated polls, avoiding unnecessary payloads and lost keyboard focus.

Runtime-specific usage is an observation with source/freshness/coverage. Missing
or malformed counters stay unknown; partial reads do not contribute to measured
totals. Cached input is a subset of input, never another additive cost. Agent
status is separately reported by the parent and does not imply token freshness.
Optional telemetry errors remain visible without taking progress or feedback
offline. Partial coverage is identified alongside observed totals; a reachable
page does not attest that a stale parent or agent is executing.

## Persistence and recovery

Workspace identity pins actor and repository. Optional revision checks reject
stale concurrent writers. A lock serializes mutations; atomic replacements avoid torn files.
JSON commits the display snapshot; Markdown projections can be repaired on resume.
Feedback has a stable browser idempotency key, viewed revision, pending state and
explicit parent disposition. A lost HTTP acknowledgement does not duplicate it.
Pending feedback is never consumed just by reading. Manual handover is a separate
command and has no automatic caller, including stop or compaction.

## Trust and lifecycle

The server exposes fixed assets, the current projection, allowlisted artifacts
and one feedback POST. A capability URL, strict Host/Origin checks and CSP cover
the local browser transport. Default binding is loopback; an explicit Tailscale
IPv4 bind is available without modifying Tailscale configuration. There are no
commands, tracker writes, arbitrary file routes, or authority-granting APIs.
Transcript locators are explicit or proven through the configured parent's spawn
graph and remain private; only matched runtime identities and normalized counters
reach the UI. Agent summaries/actions are workflow reports rather than excerpts
from child transcripts.
Repeated launch reuses the verified instance. Stop authenticates instance/PID and
preserves the workspace and waits for its listener to stop; stale metadata never
authorizes signalling another PID. Read-only commands do not create a workspace
for a mistyped slug, and input reads are bounded before parsing.

## Verification

Behavioral storage/HTTP/lifecycle tests and incremental adapter tests run in the
package validator and CI. A real Chromium journey covers feedback, lost-response
retry, drafts, hostile text, usage, responsive layout and offline behavior; lack
of a browser is an explicit skip. Independent review checks the standalone
surface before thin looper wiring is accepted.
