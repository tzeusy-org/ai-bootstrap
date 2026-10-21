# Beads Context (compact prime)

bd is the ONLY task tracker — no TodoWrite, TaskCreate, or markdown TODO
lists. Never `bd edit` (opens $EDITOR; hangs agents). Persistent insights:
`bd remember "..."`; search with `bd memories <kw>` (not MEMORY.md files).
Re-run `bd prime` after compaction.

## Commands

```bash
bd ready | bd show <id> | bd update <id> --claim | bd close <id>... [--reason=]
bd create --title="..." --type=task|bug|feature -p 0-4 [--description= --acceptance=]
bd dep add <issue> <depends-on> | bd blocked | bd stale | bd search <kw>
bd stats | bd doctor | bash scripts/beads-mirror-push.sh   # never bare `bd dolt push`
```

Priority is numeric (0=critical … 4=backlog), never "high/medium/low".
Create the bead BEFORE coding; claim before starting; batch closes.

## Session close (MANDATORY before saying "done")

1. File beads for follow-up work; close finished, update in-progress.
2. Quality gates if code changed (tests, linters).
3. `bash scripts/beads-mirror-push.sh` — mirrors the shared beads DB to
   origin `refs/dolt/data` (bare `bd dolt push` pushes a stale local dir).
4. `git add`/`commit`, `git pull --rebase`, `git push` — must end
   "up to date with origin"; if push fails, resolve and retry. Work is
   NOT complete until push succeeds.

This is the intentionally compact prime (full default trimmed for context
economy). More commands exist — check `bd <cmd> --help` before assuming
one doesn't.
