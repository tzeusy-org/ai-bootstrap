# Commands

Use this file for quick lookup. It is not required reading before every run.

## Claim And Heartbeat Checklist

Repeat this during the run:

Before any `bd` mutation: confirm you are the bead's `assignee`, renew the stall
heartbeat if near expiry, then mutate.

Coordinator responsibilities:
- pass `--actor "$COORDINATOR_ID"` on every `bd` command and renew the
  registry entry with `coordinator_registry.py register` on every wake
  (`runtime-and-safety.md` → "Parallel Coordinators")
- claim atomically with `bd --actor "$COORDINATOR_ID" update <id> --claim`
  (sets `assignee` + `in_progress`)
- check `assignee` (not a lease token) to decide whether a bead is yours
- renew the stall heartbeat at the checkpoints in `runtime-and-safety.md`
  (TTL, cadence, and the skip-if-recently-renewed rule are canonical there)
- replace the canonical heartbeat block in notes; never append a second one
- keep every wake inside the prompt-cache window (`runtime-and-safety.md` →
  "Orchestrator Wake Cadence")

## Session Completion Checklist

```bash
# 1. Verify all workers finished or cleaned up
bd worktree list

# 2. Release stale claims only after verifying the holder is dead (no live
#    registration, expired heartbeat), with the guarded update in
#    runtime-and-safety.md → "Parallel Coordinators"
bd list --status=in_progress --json | jq -c '[.[] | {id, title, assignee}]'

# 3. Deregister this coordinator (from the package directory)
uv run scripts/coordinator_registry.py deregister --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID"

# 4. Verify Dolt DB is healthy
bd dolt status

# 5. Push everything
git pull --rebase && git push
git status
```

Codex add-on:
- close completed or unused subagents so thread limits do not block future
  dispatches

## bd Quick Reference

Pipe every listing through a `jq` field projection per
`../../../references/token-efficiency.md`; the projections below are the
default coordinator views.

| Action | Command |
|---|---|
| Ranked ready work | `uv run scripts/rank_ready_work.py --repo-root "$REPO_ROOT" --coordinator "$COORDINATOR_ID"` (from the package directory; Step 1 explains the envelope) |
| New coordinator id | `uv run scripts/coordinator_registry.py new-id --runtime claude` |
| Register / renew | `uv run scripts/coordinator_registry.py register --repo-root "$REPO_ROOT" --id "$COORDINATOR_ID" --wake-minutes <n> [--startup] [--extra-lanes <k>]` |
| Epic progress | `bd epic status --json \| jq -c '[.[] \| {id: .epic.id, closed: .closed_children, total: .total_children}]'` |
| Live coordinators | `bd kv list --json \| jq -c 'with_entries(select(.key \| startswith("beads.coordinator.")))'` |
| All open | `bd list --status=open --json \| jq -c '[.[] \| {id, title, priority, type, labels, assignee}]'` (prefix with `bd -C /path/to/rig` if outside target rig) |
| PR-review issues | `bd list --status=blocked --label pr-review --json \| jq -c '[.[] \| {id, title, labels, assignee, external_ref}]'` |
| PR-review-task issues | `bd list --status=blocked --label pr-review-task --json \| jq -c '[.[] \| {id, title, labels, assignee, external_ref}]'` |
| Issue detail | `bd show <id> --json \| jq '{id, title, status, assignee, labels, external_ref}'` (workers add `description`, `acceptance_criteria`, `notes`, `design`) |
| Check PR state | `gh pr view <number> --json state,mergedAt,createdAt` |
| Find PR by worker branch | `gh pr list --state open --head "agent/<id>" --json number,url,createdAt` |
| Create issue | `bd --actor "$COORDINATOR_ID" create "<title>" --description "<desc>" -t <type> -p <priority> [--parent <epic-id>] --json` |
| Claim issue | `bd --actor "$COORDINATOR_ID" update <id> --claim --json` (atomic; sets `assignee` + `in_progress`), then write the stall heartbeat note |
| Release issue | `bd --actor "$COORDINATOR_ID" unclaim <id> --reason "<why>"` (clears the assignee and reopens it) |
| Adopt a dead peer's bead | guarded `bd --actor "$COORDINATOR_ID" update <id> --if-assignee '<old>' --if-status <status> …`; exit 13 means a peer won (`runtime-and-safety.md` → "Adopt the dead, guarded") |
| Close finished epics | `bd --actor "$COORDINATOR_ID" epic close-eligible --json` (after Step 0 merges) |
| Stamp readiness rejection | `bd --actor "$COORDINATOR_ID" update <id> --add-label needs-shaping --append-notes "dispatch-readiness <date> <COORDINATOR_ID>: missing <fields>"` (see the readiness gate in `coordinator-loop.md`) |
| Shaped beads awaiting re-check | `bd list --label needs-shaping --json \| jq -c '[.[] \| {id, title, labels}]'` |
| Block review issue | `bd --actor "$COORDINATOR_ID" update <id> --status blocked --json` |
| Close issue | `bd --actor "$COORDINATOR_ID" close <id> --reason "<reason>"` |
| Add dependency | `bd --actor "$COORDINATOR_ID" dep add <issue> <depends-on>` |
| Children | `bd children <id> --json` |
| Blocked | `bd blocked --json` (cross-rig: `bd -C /path/to/rig list --status=blocked`) |
| Dolt server status | `bd dolt status` |
| Dolt version control | `bd vc status` |
