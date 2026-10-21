# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** Issues live in a local Dolt database
> (`.beads/dolt/`); cross-machine sync uses `bd dolt push/pull` (a
> git-compatible protocol), stored under `refs/dolt/data` on your git
> remote — separate from `refs/heads/*` where your code lives.
> `.beads/issues.jsonl` is a passive export, not the wire protocol.
>
> See [SYNC_CONCEPTS.md](https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md)
> for the one-screen overview and anti-patterns (don't treat JSONL as the
> source of truth; don't `bd import` during normal operation; don't
> reach for third-party Dolt hosting before trying the default).

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   bash scripts/beads-mirror-push.sh   # beads DB -> origin refs/dolt/data (never bare `bd dolt push`)
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Project Shape Navigation

The five-pillar shape docs live under `about/` and `openspec/`;
`about/README.md` is the reading-order index and each pillar's `README.md`
routes within that pillar. They are deliberately not exposed as catalog
skills — the tool skill surfaces double as the user's global catalogs on
installed machines. Before structural, placement, or quality-bar decisions
here, start from `about/README.md` (or the relevant pillar README) and follow
its routing. Reading `about/craft-and-care/` is mandatory for non-trivial
changes.

# Notes to self

- Before dispatching a baseline-pinned ready bead, verify its governing paths still exist on current `main` and reconcile prior merged PRs plus later history; a stale ready record may describe work that was completed and then deliberately removed, and must not resurrect that surface.
- Global skill catalog is 8 routers (`beads-orchestration`, `bws-cli-skill`, six `th-*`); every description loads into every session, so a new capability joins an existing superskill as a subskill before it earns a top-level entry. `skills/personal/th-workflow/` (fork of `obra/superpowers`) and `th-engineering/subskills/skill-creator/` (fork of `anthropics/skills`) replaced seven standalone entries on 2026-09-02; unused `doc-coauthoring` sits in `skills/archive/`.
- `skills/.system/` is a Codex-managed system-skill mirror; `scripts/link-ai-skills.sh` and `~/.dotfiles/bootstrap.sh` prune it from discovery. Without that prune, deleting a same-named top-level skill lets the `.system` copy leak into the Claude/Gemini catalogs.
- `beads-orchestration` keeps every numeric policy (stall TTL, TTL-keyed wake cadence 20-30 min on 1h-TTL / 4m50s on 5m-TTL / 60 min frontier / 3 no-op wakes, model tables) canonical in `beads-coordinator/references/runtime-and-safety.md`; other files point, never restate. Its `subskills/*/tests/test_*contract*.py` pin doc *wording* (`assertIn` on phrases), so moving doctrine between files (e.g. the normalizer's fail-closed rules now live in the script docstring) means updating those tests in the same change, not loosening the docs.
- Cache-first doctrine (2026-09-27): orchestration runs on long-lived **lanes** (one session walking a context-local chain of beads, continued via `SendMessage`, riding autocompaction), not per-bead fresh workers or per-surface fan-outs. Opus 5.5 cache reads are 0.05x input, so a warm session is ~20x cheaper to re-read than a cold one is to load. Canonical: `beads-orchestration/references/token-efficiency.md` → "Cache-first execution"; th-projects work-allocation, the pursuit's execution discipline, and project-shape drafting all point there. Subagents stay justified by independence (review, vetting), not parallelism.
- Oversized subskills (`beads-pr-reviewer-worker` 499 lines against the audit's hard 500 cap, counted as newlines+1, `beads-worker` 323, `blogpost-editor` 259, `project-direction`/`project-shape` ~250) exceed the 150-line guideline but run linearly, so fanning them into `references/` saves little; revisit only if a conditional slice (e.g. beads-worker handoff paths) grows.
- Merge-flow doctrine (2026-09-04): `prepare_pr_branch.py` rebases only on a `git merge-tree --write-tree` conflict (or `--force-rebase`); a clean PR head is reviewed and merged as-is. When the base has a GitHub merge-queue ruleset (`MERGE_QUEUE=yes` in coordinator preflight) merges go through `gh pr merge --squash --auto`, the reviewer reports `merge-queued`, and the coordinator opens `queue-direct` PRs instead of fast-forwarding `main`. Never `--admin`.
- Test-growth governance for the autonomous lane lives in `beads-orchestration/references/test-growth-gate.md` (one gate species per behavior, `Tests: +a ~b -c` delta line, repo test-budget ratchet); beads-writer/th-projects/th-workflow all point at it rather than restating.

- `skills/` is the canonical local mirror source for shared workflows; mirrored tool skill names are flattened by basename, so provenance must be reasoned from the source tree rather than the installed name.
- `opencode/` installs under `$HOME/.config/opencode`, unlike `.claude`, `.codex`, and `.gemini`, which map directly under `$HOME`.
- `.claude/`, `.codex/`, and `.gemini` mix tracked baseline config with ignored runtime state; any topology or doctrine docs should distinguish canonical source, mirror surfaces, installed targets, and local-only state explicitly.

- `skills/personal/th-engineering/subskills/excalidraw-diagram/tests/output/` is a checked-in artifact directory for end-to-end fixtures and should contain the source `.excalidraw`, generated Mermaid `.mmd`, and themed SVG renders.
- `skills/personal/th-engineering/subskills/excalidraw-diagram/scripts/render_excalidraw.py` emits non-fatal layout warnings for bound text that exceeds roughly 75% width, 65% height, or minimum padding inside a container; keep fixtures and examples lint-clean.
- The project-shape design-contract pillar now uses `legends-and-lore` as the canonical skill and folder name; scaffolding, scans, and fixtures should use `about/legends-and-lore/` and `.claude/skills/legends-and-lore/`.
- Skill packaging quality gate: `uv run skills/personal/th-engineering/subskills/skill-standards/scripts/audit_skill.py <skill-dir>` checks frontmatter/spec limits, link integrity, orphaned support files, superskill layout, and PEP 723 compliance; ERRORs block shipping a skill change.
- All Python helper scripts in repo-owned skills must carry PEP 723 inline metadata (`# /// script ... # ///`) so they run via `uv run` env-agnostically; vendored skills (docx, pptx, pdf, slack-gif-creator, `.system/`, submodules) are exempt until next touched.
- The engineering quality-bar skills were consolidated (2026-06-12) into a superskill at `skills/personal/th-engineering/` — router `SKILL.md` plus `subskills/{engineering-bar,code-readability,test-rigor,dependency-hygiene,diagnosis,documentation,cruft-cleanup,skill-standards,excalidraw-diagram}/`. Old `skills/personal/{skill-standards,cruft-cleanup,excalidraw-diagram}` paths map to `skills/personal/th-engineering/subskills/<same-name>`; the divergent top-level `skills/excalidraw-diagram/` fork was deleted (the personal fork is canonical). The router encourages one subagent per subskill for multi-subdomain quality work.
- `th-engineering/subskills/engineering-bar` is the canonical home of the nine default engineering biases and the definition of done. project-shape's `pillar-craft-and-care.md` and the craft-and-care local-skill template adopt it **by reference** — project pillars record only deviations; do not reintroduce restated bias lists there.
- Global session guidance lives in `generalized-AGENTS.md`; `.claude/CLAUDE.md` and `.codex/AGENTS.md` are relative symlinks to it. Keep repository knowledge here, not in the shared global file.
