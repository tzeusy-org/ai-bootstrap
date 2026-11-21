## Purpose

Give each running workflow a reusable local progress view with agent and usage
observations plus durable steering, without model-driven refresh overhead or a
second task/authorization authority.

## ADDED Requirements

### Requirement: Reusable Session-Owned Progress Workspace

The shared progress-dashboard skill MUST own its implementation independently of any execution subskill and MUST maintain one identity-bound local workspace with current in-progress, todo and complete Markdown projections plus a separately controlled handover document.

ID: REQ-progress-dashboard-001
Source: owner request 2026-11-14; about/heart-and-soul/vision.md rules 1, 5 and 7
Scope: v1-mandatory

#### Scenario: A workflow launches or resumes its view

- **WHEN** a workflow starts a stable slug, with its actor and repository supplied or defaulted
- **THEN** it gets a private local workspace with four readable Markdown files, repeat initialization preserves existing content, and a conflicting actor/repository is refused

#### Scenario: A checkpoint updates current work

- **WHEN** a validated delta names only new rows and the changed fields of existing rows
- **THEN** the helper merges it by row identity, commits the display state, derives the activity log and regenerates the three progress projections without mutating the owning tracker, while a supplied stale revision refuses overwrite

#### Scenario: A workflow publishes a chart or table

- **WHEN** a checkpoint supplies panel rows for a fixed stats, bars, steps or table template
- **THEN** the page renders them with its own marks, colors and layout, and no authored markup is accepted

### Requirement: Explicit Manual Handover

The helper MUST update handover.md only through a separate command carrying an explicit manual owner request, and MUST NOT change it during ordinary progress, usage, feedback, resume, compaction or stop operations.

ID: REQ-progress-dashboard-002
Source: owner request 2026-11-14 item 1
Scope: v1-mandatory

#### Scenario: Automatic activity leaves handover untouched

- **WHEN** a session publishes progress, receives feedback, resumes or stops
- **THEN** the existing handover content remains unchanged

#### Scenario: The owner asks for a handover

- **WHEN** the parent invokes the dedicated handover command with the explicit request and prepared content
- **THEN** the helper updates the document and records the request provenance separately

### Requirement: Token-Efficient Background Visibility

The dashboard MUST use fixed scaffolded assets and scripts for rendering, lifecycle, telemetry and feedback, and MUST NOT require model-driven refresh cycles, per-run generated web infrastructure, or extra model calls to summarize child transcripts.

ID: REQ-progress-dashboard-003
Source: owner request 2026-11-14 item 3 and explicit token-efficiency emphasis
Scope: v1-mandatory

#### Scenario: The owner watches progress

- **WHEN** the browser refreshes while the parent or its agents work
- **THEN** the background server serves current work, findings, agent summaries, optional rendered visuals and available usage without waking a model or querying the workflow's tracker

#### Scenario: A parent reaches an existing checkpoint

- **WHEN** a substantive workflow event changes progress or the parent needs pending steering
- **THEN** the parent publishes only changed fields or requests compact status/inbox data, retaining its existing wake cadence

#### Scenario: Feedback has already been surfaced or exceeds the batch budget

- **WHEN** the workspace has already surfaced a message to the parent or a pending message exceeds the bounded ten-entry/8KiB response budget
- **THEN** routine responses omit previously surfaced messages without consuming them, label oversized previews explicitly, and provide a full-item lookup required before interpreting or acknowledging a preview

### Requirement: Honest Incremental Usage and Agent Overview

The dashboard MUST summarize parent and child-agent observations with source freshness, MUST collect supported attached usage logs incrementally, MUST resolve known Codex children through a read-only parent-scoped runtime relationship index and known Claude Code sub-agents through the configured session's own sub-agent folder when configured, and MUST distinguish measured, partial, unknown and errored counters without fabricating estimates or double-counting cached input or inherited fork history.

ID: REQ-progress-dashboard-004
Source: owner refinement 2026-11-14 progress, tokens and subagents in one visual layer
Scope: v1-mandatory

#### Scenario: Supported logs append usage records

- **WHEN** explicitly attached Codex or Claude logs receive complete valid usage records
- **THEN** bounded background reads update per-agent counters, deduplicate cumulative/streamed records, persist offsets for restart, and aggregate measured coverage separately from reported agent status

#### Scenario: Usage is unavailable or still catching up

- **WHEN** a source is missing, malformed, unsupported or not fully consumed
- **THEN** the page displays its unknown/error/partial state and excludes unmeasured rows from aggregate coverage rather than representing them as measured zero, while optional usage failure leaves progress and feedback available

#### Scenario: A configured Codex parent launches known agents

- **WHEN** the runtime index links a published agent to the configured parent session
- **THEN** background metadata lookup resolves its log and runtime identity without per-child manual attachment, transcript-directory crawling, unrelated-session reads or model calls, and thread-filtered runtime counters supersede legacy cumulative events

#### Scenario: A configured Claude Code parent launches known sub-agents

- **WHEN** the configured parent session's own sub-agent folder holds a log whose agent id, tool-use id or unique description matches a published agent
- **THEN** background lookup attributes that log without per-child manual attachment, reads only that session's sub-agent folder and its descriptors (never other sessions or projects), leaves unmatched or ambiguous sub-agents unattributed, and keeps explicit attachments authoritative

#### Scenario: The owner inspects an agent's work

- **WHEN** an agent has reported a summary, recent actions and delivery branch/worktree/PR at an existing checkpoint
- **THEN** its expandable detail view shows those reports alongside runtime attribution and freshness, retains open state and keyboard focus through usage refreshes, and identifies missing reports without inventing work or delivery links

### Requirement: Durable Authority-Safe Steering

The browser MUST persist optional feedback with its viewed revision and stable retry identity, MUST preserve unsent drafts across refresh/failure, and MUST leave disposition and all operational authority with the parent workflow at its next safe checkpoint.

ID: REQ-progress-dashboard-005
Source: owner request 2026-11-14 item 2; existing user-questionnaire authority boundary
Scope: v1-mandatory

#### Scenario: Feedback is submitted or retried

- **WHEN** the owner submits steering through the session page, including a retry after a lost response
- **THEN** one durable pending item is recorded, reads do not consume it, and the parent's explicit acknowledgement records the disposition visible in the browser

#### Scenario: A message concerns a hard owner gate

- **WHEN** feedback asks for a gated merge, deployment, policy or destructive action
- **THEN** it remains input to the owning workflow's existing gate and does not itself execute commands, mutate the tracker or confer new authority

### Requirement: Bounded Local Server Lifecycle

The helper MUST isolate each session's server and content, default to a private loopback listener, constrain served artifacts to explicitly published files, and verify server identity before reusing or stopping a process.

ID: REQ-progress-dashboard-006
Source: owner request 2026-11-14 pre-scaffolded parallel server; about/heart-and-soul/vision.md runtime boundary
Scope: v1-mandatory

#### Scenario: An existing session launches again

- **WHEN** a verified server is already running for its workspace
- **THEN** the helper reuses it without a second process and preserves feedback, while a stale PID alone cannot authorize stopping another process

#### Scenario: Unrelated content or cross-site writes are requested

- **WHEN** a request lacks the private URL, has an invalid host/origin, traverses artifact paths or targets an unpublished file
- **THEN** the server refuses it without exposing unrelated local content or recording feedback

## Source References

- `openspec/changes/add-progress-dashboard/proposal.md`: the owner's bounded request and standalone placement refinement.
- `about/heart-and-soul/vision.md`: shared-skill ownership, local-only runtime state, and human/agent legibility.
- `skills/personal/th-projects/subskills/progress-dashboard/references/protocol.md`: snapshot, feedback, usage and lifecycle interfaces.
- `tests/progress-dashboard-test.sh`: repository entrypoint to the canonical behavior suites, without copied test logic.
