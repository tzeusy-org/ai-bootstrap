# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## Issue Tracking (beads)

bd is the ONLY task tracker — no TodoWrite, TaskCreate, or markdown TODOs;
`bd remember` for persistent knowledge, never MEMORY.md files. The
SessionStart hook injects the workflow context (`.beads/PRIME.md`); re-run
`bd prime` after compaction. Session close is mandatory: file beads for
follow-ups, run quality gates, `bash scripts/beads-mirror-push.sh`, then
commit, `git pull --rebase`, and `git push` until "up to date with origin" —
work is NOT done until push succeeds.

## Beads Database Topology

Beads live on a **shared external Dolt sql-server** hosted in the homelab k8s
cluster (namespace `dolt`; `kubectl -n dolt get pods`) and reached over
Tailscale at `dolt.TAILNET.ts.net:3307`. Nothing listens on
`localhost:3307` any more: the old `~/gt/.dolt-data` server was retired on
2026-08-30, and `~/gt/.dolt-data.migrating/` is its leftover, not live data.
Each repo maps to its own per-project database on that server: this repo →
DB `aib` (prefix `aib-`), the parent `~/.dotfiles` → DB `dotfiles` (prefix
`dotfiles-`). Connection details are recorded in each repo's
`.beads/config.yaml` + `.beads/metadata.json` (`dolt_mode: server`,
`--external`). Migrated from per-repo embedded Dolt on 2026-06-17; repointed
from localhost to the k8s host on 2026-09-02.

`--external` means **bd does not start/stop the server**. The real hostname
reaches bd only through `BEADS_DOLT_SERVER_HOST`, exported from the gitignored
`~/.dotfiles/to_source/machine_specific`; the tracked configs carry the
placeholder. On `connection refused`, check that the override is in the shell
and that no config still says `127.0.0.1`, verify with `bd dolt show`
(`Server connection OK`; `Mode: per-project` is the normal label here and
there is no `mysql` client on this host), and never start a local `dolt
sql-server` as a workaround. The server is the source of truth;
`scripts/beads-mirror-push.sh` mirrors it to origin `refs/dolt/data` at
session close, with owner emails rewritten and a PII gate, because bare `bd
dolt push` pushes a stale local directory instead. Recovery on a fresh clone:
`bd bootstrap --yes` run inside it with the override unset (it reads the
remote's `refs/dolt/data` itself). Repo→DB selection
is still per-`.beads/`, so a session here sees only `aib`; address the
parent's beads explicitly with `bd -C ~/.dotfiles <cmd>`, and cross-prefix
auto-routing still does NOT work. Known bd errors and workarounds are
cataloged in
`skills/personal/beads-orchestration/references/known-errors.md` — append new
rough edges there after resolving them.

## Build & Test

There is no repo-wide build. Skill packages with tests carry them in
`<skill>/tests/`; run them with:

```bash
python3 -m pytest skills/personal/beads-orchestration/subskills/*/tests/ --import-mode=importlib -q
```

Symlinks into tool homes (`~/.claude/skills` etc.) are managed by the parent
repo's `bootstrap.sh`, which prunes `subskills/` so superskills install as one
catalog entry.

## Project Shape Navigation

The five-pillar shape docs live under `about/` and `openspec/`;
`about/README.md` is the reading-order index and each pillar's `README.md`
routes within that pillar. There are no catalog skills for them — this repo's
`.claude/skills` doubles as the user's global `~/.claude/skills`, so
repo-specific skills would leak into every session. Before structural,
placement, or quality-bar decisions here, start from `about/README.md` (or
the relevant pillar README) and follow its routing. Reading
`about/craft-and-care/` is mandatory for non-trivial changes.

## Architecture Overview

Skills-first (see README.md). Canonical skills live in `skills/`;
`skills/personal/` is the user-maintained layer, everything else is
upstream-derived (submodules or vendored). Superskills (`beads-orchestration`
and every `th-*`) are router `SKILL.md` + `subskills/` packages; only the
router enters the global catalog. `agents/` is legacy/reference.

## Conventions & Patterns

- Follow `skills/personal/th-engineering/subskills/skill-standards/` when
  creating or updating skills (trigger quality, routing, context efficiency).
- Keep `SKILL.md` a thin router; detailed guidance goes in `references/`,
  helpers in `scripts/` (PEP 723 headers for Python).
- Update doctrine worth remembering in the parent repo's `AGENTS.md`
  "Notes to self" at session end.
