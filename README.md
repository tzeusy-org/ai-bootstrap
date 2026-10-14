# ai-bootstrap

Portable AI assistant configs and shared skills for [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Codex](https://github.com/openai/codex), [Gemini CLI](https://github.com/google-gemini/gemini-cli), and [OpenCode](https://github.com/opencode-ai/opencode).

The current operating model is **skills-first**, not `agents/`-first. Shared workflows live in `skills/` and are mirrored into each tool's native skills directory. For day-to-day execution, the primary path is the Beads workflow under `skills/personal/`, especially `beads-coordinator`, `beads-worker`, and `beads-pr-reviewer-worker`.

## What This Repo Optimizes For

- One shared skill library that can be consumed by multiple CLI agents.
- Tool-native configs under `.claude/`, `.codex/`, and `.gemini/`.
- Beads-backed work management with explicit workflows instead of ad hoc TODOs.
- Isolated implementation via worktrees and PR-review follow-up via dedicated review workers.
- Five-pillar repository self-knowledge under `about/` plus `openspec/` so both humans and agents can navigate the source of truth.

## Structure

```text
.claude/           # Claude Code config and linked skill entrypoints
.codex/            # Codex config, prompts, rules, and linked skill entrypoints
.gemini/           # Gemini CLI config plus linked skills/antigravity skills
opencode/          # OpenCode config
skills/            # Canonical shared skill library
skills/personal/   # Primary home of Beads and project workflow skills
agents/            # Older agent prompts kept for reference / selective reuse
about/             # Five-pillar project-shape docs for doctrine, contracts, topology, and standards
openspec/          # Normative repository-shape requirements and change records
projects/          # Independently shaped product offerings with product-local authority
scripts/           # Helper scripts used by the ai-bootstrap setup
```

`agents/` still exists, but it is no longer the center of the system. Treat it as legacy/reference material unless you have a specific reason to use one of those prompts.

`projects/` is not part of the shared-skill mirror or installation flow. Its
[`README.md`](projects/README.md) lists each offering; start an offering from
its own `about/README.md`, whose product shape and child OpenSpec change govern
product behavior.

Global session instructions live in [`generalized-AGENTS.md`](generalized-AGENTS.md).
Both `.claude/CLAUDE.md` and `.codex/AGENTS.md` symlink to this shared,
tool-neutral file; the installed tool homes inherit these links. Keep repository
notes in the root `AGENTS.md`, not in global session guidance.

## Skills Layout And Provenance

The `skills/` tree is intentionally split by ownership:

- `skills/personal/` is the layer built and maintained by me.
- These are the primary place for custom workflow logic, project-shaping methods, and Beads execution patterns.
- Some personal skills are original; some are maintained forks adapted to my workflow. `skills/personal/th-engineering/subskills/excalidraw-diagram/` is an example of that pattern; `skills/personal/th-workflow/` (forked from `obra/superpowers`) and `th-engineering/subskills/skill-creator/` (forked from `anthropics/skills`) are others.

Everything else under top-level `skills/` should be treated as upstream-derived material:

- Some directories are checked out as git submodules from open source upstreams.
- The current submodules are `skills/archive/Skill_Seekers`,
  `skills/archive/anthropic-skills`, `skills/archive/notebooklm-skill`,
  `skills/archive/mattpocock-skills`, and
  `.claude/plugins/marketplaces/claude-plugins-official`.
- Every other non-`personal/` skill directory is a vendored copy of an open source skill or skill bundle.

In practice, that means:

- If the workflow is specific to my operating model, it belongs in `skills/personal/`.
- If a top-level skill came from upstream, prefer updating from upstream or forking intentionally instead of casually rewriting the vendored copy.
- Tool-specific skill directories under `.claude/skills`, `.codex/skills`, and `.gemini/skills` are generated runtime views of this source tree, not the source of truth. Claude and Gemini use direct directory links. Codex uses a shallow generated wrapper per root skill because it recursively discovers `SKILL.md` files through directory symlinks; each wrapper keeps the root frontmatter and points back to the canonical source before use.

## Primary Workflow

### 1. Shared skills are canonical

Each root skill lives in `skills/<name>/SKILL.md` or `skills/personal/<name>/SKILL.md`. The included `scripts/link-ai-skills.sh` installer discovers those roots while pruning `subskills/` and `archive/`; `~/.dotfiles/bootstrap.sh` calls that script on dotfiles-managed machines:

- `~/.claude/skills/`, `~/.gemini/skills/`, and `~/.gemini/antigravity/skills/` receive direct source-directory symlinks.
- `~/.codex/skills/` receives shallow, generated wrapper directories. The wrapper's `SKILL.md` is catalog metadata plus an instruction to read the canonical source; it deliberately contains no `subskills/` tree.

The generated namespace is flattened by root directory basename. Provenance and ownership should always be reasoned about from the canonical `skills/` source directory, not from a tool runtime view.

### 2. Beads drives execution

The main personal workflow is:

1. Use `beads-writer` or normal `bd` commands to create and shape issues.
2. Start `beads-coordinator` to pull ready work from `bd ready`.
3. `beads-coordinator` runs `beads-cleanup`, claims work, creates isolated worktrees, and dispatches workers.
4. `beads-worker` executes one implementation issue in its own worktree.
5. `beads-pr-reviewer-worker` handles PR review follow-up, review threads, and merge-or-retry decisions, then reports the outcome back to the coordinator for Beads closure.

This is the preferred model for sustained throughput. The coordinator coordinates; workers implement or review.

### 3. Planning lives in skills too

Related personal workflow skills include:

- `th-projects` — superskill routing to `project-shape` (doctrine/spec/topology setup), `project-feature-request` (feature request → spec delta), `project-review` (repo audits + spec reconciliation), and `project-direction` (spec-driven prioritization)
- `about/craft-and-care/` for this repository's own execution-quality bar (in-repo sessions are routed there by `CLAUDE.md`/`AGENTS.md`)

## Quick Start

When this repository is managed as `~/.dotfiles/ai-bootstrap`, use the dotfiles bootstrap script. It links the tool homes and regenerates their skill views:

```bash
cd ~/.dotfiles
./bootstrap.sh
```

To refresh only the skill views without the rest of bootstrap, run
`~/.dotfiles/ai-bootstrap/scripts/link-ai-skills.sh ~/.dotfiles/ai-bootstrap`.
Do not replace Codex's generated wrappers with direct directory symlinks: that
would reintroduce nested subskills into Codex's catalog.

For a standalone installation, clone this repository recursively, generate its
skill views, then link the tool homes:

```bash
git clone --recursive https://github.com/tzeusy-org/ai-bootstrap.git
cd ai-bootstrap
./scripts/link-ai-skills.sh "$(pwd)"
ln -sfn "$(pwd)/.claude" "$HOME/.claude"
ln -sfn "$(pwd)/.codex" "$HOME/.codex"
ln -sfn "$(pwd)/.gemini" "$HOME/.gemini"
mkdir -p "$HOME/.config"
ln -sfn "$(pwd)/opencode" "$HOME/.config/opencode"
```

This repo is intended to work standalone. Do not assume an enclosing parent repo or nested prefixes for local paths; the source of truth is this repository root, and the destination is the corresponding path under `$HOME`.

## Recommended Entry Points

- Want continuous issue execution: start with `beads-coordinator`
- Want to implement one already-assigned issue in a worktree: use `beads-worker`
- Want PR review follow-up and merge handling: use `beads-pr-reviewer-worker`
- Want to create or decompose backlog work: use `beads-writer`

## Customization

Machine-specific settings that should stay local:

- `.codex/config.toml` project entries and private absolute-path skill config
- `.gemini/settings.json` local telemetry or runtime settings
- `.gemini/installation_id`
- Any private skills you do not want in version control

## License

[MIT](LICENSE)
