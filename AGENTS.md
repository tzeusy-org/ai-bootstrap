# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd prime` for full workflow context.

> **Architecture in one line:** issues live on the shared Dolt server
> (`dolt.TAILNET.ts.net`, reached through `BEADS_DOLT_SERVER_HOST`), which is
> the only store. Git repos are NOT Dolt remotes: never `bd dolt push/pull`,
> never write `refs/dolt/*` to a git origin. `.beads/issues.jsonl` is a
> passive export, not a source of truth.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
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

**Architecture in one line:** issues live on the shared Dolt server, the only store; git repos are not Dolt remotes (no `bd dolt push/pull`, no `refs/dolt/*`); `.beads/issues.jsonl` is a passive export.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
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
- `beads-orchestration/subskills/beads-coordinator/references/runtime-and-safety.md` owns model tables, claim liveness, lane capacity and bounded event waits. Do not add cache-preserving polling or duplicate numerical policies in other references.
- Session efficiency: continue the same outcome owner and independent reviewer where appropriate; cache percentages are not measured savings. Canonical packet/evidence/session policy lives in `beads-orchestration/references/delivery-model.md`, output/gate discipline in `references/token-efficiency.md`.
- Delivery doctrine (2026-11-06): `beads-orchestration/references/delivery-model.md` is the shared contract. Loopers are persistent bounded-outcome owners; several existing tracking beads may share one anchor package, worktree, PR and independent review. Implementation claims/worktree stay with the owner through review/corrections; blocked anchors park execution. The shared review-first admission queue drains reviews/corrections before new work, with execution separate from retained WIP; reviewer uses `review/<review-id>`. Anchor alone holds the unique PR ref; closure is per-member acceptance plus actual delivery-base landing.
- Skill validation: canonical `skill-standards/scripts/audit_skill.py` checks package structure. Behavioral tests prove claims, isolation, evidence and recovery; do not pin obsolete orchestration prose with phrase assertions.
- Merge-flow doctrine (2026-11-06): `prepare_pr_branch.py` prepares an independent `review/<review-id>` snapshot without rewriting the implementation branch. Textual conflicts return to the existing owner; unsupported probes and unpublished review commits block preparation. A clean main advance alone does not trigger owner rebase or duplicate broad review. Required merge-queue/check/owner-hold rules remain binding; never `--admin`.
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
- CI placement doctrine lives in `th-engineering/subskills/ci-discipline` (2026-11-01): each verdict once per tree, in the tier the repo assigns it. Worker, reviewer, test-growth-gate, token-efficiency and finishing-a-development-branch defer to it; never reintroduce a blanket "run the full gate locally before handoff" rule, which made butlers compute the same broad verdict up to four times (local worker, PR CI, local reviewer, merge queue). A local full run is skipped only when a required hosted check covers the landing tree; direct-merge candidates always run it locally and report it as a `full:` entry (worker-report).
- Global session guidance lives in `generalized-AGENTS.md`; `.claude/CLAUDE.md` and `.codex/AGENTS.md` are relative symlinks to it. Keep repository knowledge here, not in the shared global file.
