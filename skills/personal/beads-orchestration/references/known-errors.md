# Beads CLI Known Errors

Living catalog of `bd` errors, deprecations, and workarounds. This file is the
superskill's own memory: it is symlinked into every tool home, so an entry
written once is available to every future agent session.

## Maintenance contract (read this first)

When you hit a `bd` error or rough edge that is NOT already listed here:

1. Resolve or circumvent it.
2. **Append an entry below in the same format** (symptom verbatim, cause,
   fix/workaround, date observed, bd version from `bd version`).
3. Prune entries that a later bd release fixes — note the fixed version
   instead of silently deleting, then drop the entry one revision later.

Do not skip step 2 because the fix felt obvious. The next agent pays the
debugging cost again unless it lands here.

## Connectivity / server

### Cannot connect to Dolt server (`connection refused` on 3307)
- **Symptom**: `Error: failed to open database: Dolt server unreachable at
  127.0.0.1:3307: dial tcp 127.0.0.1:3307: connect: connection refused`, or
  `bd create` → "The Dolt server may not be running. Try: bd dolt start".
- **Cause (since 2026-08-30)**: the shared Dolt sql-server no longer runs on
  this host. It is a k8s deployment (namespace `dolt`; `kubectl -n dolt get
  pods`) reached over Tailscale at `dolt.TAILNET.ts.net:3307`; nothing listens on
  `localhost:3307`. A repo whose `.beads/config.yaml` / `.beads/metadata.json`
  still says `127.0.0.1` is misconfigured, not facing an outage.
  `~/gt/.dolt-data.migrating/` is the pre-k8s data dir the migration left
  behind — its presence says nothing about whether the server is up.
- **Fix**: confirm the server first:
  `mysql -h dolt.TAILNET.ts.net -P 3307 -u root --protocol=tcp -e "SHOW DATABASES"`
  (lists `aib`, `dotfiles`, `homelab`, `gt`, …). Then repoint the repo:
  `dolt.host` in `.beads/config.yaml` and `dolt_server_host` in
  `.beads/metadata.json` → `dolt.TAILNET.ts.net`; commit both. Do NOT run
  `bd dolt start` or a local `dolt sql-server` as a workaround — that forks a
  second, divergent server.
- **If the k8s pod is really down**: recovery belongs to `~/GitHub/homelab`,
  not to bd.
- Observed: 2026-06-12 (`gt dolt start` era); rewritten 2026-09-02 after
  `ai-bootstrap` and `dotfiles` were found still pointing at localhost while
  `homelab`/`gt` had been repointed. bd 1.0.4.

## Deprecated / removed commands and flags

Deprecations land without much warning between releases. When a documented
command errors with `unknown flag` or `unknown command`, check
`bd <subcommand> --help` and `bd upgrade review` (release history) before
assuming breakage, then record the rename here AND fix the subskill docs.

### `--rig <rig>` flag removed (≤1.0.3 → 1.0.4)
- **Symptom**: `Error: unknown flag: --rig` on `bd ready`/`bd list`/`bd create`.
- **Fix**: use the global `-C <path>` flag instead:
  `bd -C /path/to/rig list --status=open`. ID-prefixed commands
  (`bd update/close/show/dep`) auto-route without `-C`.
- Observed: 2026-06-12, bd 1.0.4.

## Database / config state

### `database not initialized: issue_prefix config is missing`
- **Symptom**: mutations (`bd create`, `bd update`) fail with this error while
  reads (`bd ready`, `bd list`, `bd stats`) may still work. `bd bootstrap`
  says "Database already exists: Nothing to do" and `bd init --prefix X`
  aborts because the DB exists. `bd config set issue_prefix` is refused.
- **Cause**: DB directory exists but its config lost/never had `issue_prefix`
  (seen after version upgrades).
- **Fix**: back up first (`bd export > /tmp/backup.jsonl` — though it exports
  0 issues if the DB is empty), then `bd init --reinit-local --prefix <prefix>`
  and re-import data (see next entry). Note bd may normalize the prefix
  (`.dotfiles` became `dotfiles`).
- Observed: 2026-06-12, bd 1.0.4.

### Auto-export daemon overwrites `.beads/issues.jsonl` within seconds
- **Symptom**: you restore a historical `issues.jsonl` (e.g. from git) to
  re-import it, but by the time `bd import` runs the file holds only the
  current (possibly empty) DB state.
- **Fix**: never import via the working-tree file; pipe from git instead:
  `git show <ref>:.beads/issues.jsonl | bd import -`.
- **Related**: commit hooks that run `bd export` can stage a deletion or
  truncation of `issues.jsonl` into an unrelated commit when the DB is
  empty/broken. Inspect `git show --stat` after committing; use
  `--no-verify` while the DB is being repaired.
- Observed: 2026-06-12, bd 1.0.4.

### `bd import` → `Error: commit: dolt commit: Error 1105: nothing to commit`
- **Symptom**: import errors (and dumps full flag usage) even though nothing
  is wrong — the import simply produced no DB change.
- **Workaround**: treat as success-with-no-op; verify with `bd stats` or
  `bd import --dry-run <file>` rather than trusting the exit status.
- Observed: 2026-06-12, bd 1.0.4.

## Environment

### `no beads database found` inside a nested git repository
- **Symptom**: every `bd` command fails with `Error: no beads database found`
  (and `bd where` reports no active workspace) even though a parent directory
  has a healthy `.beads/`.
- **Cause**: bd's database discovery walks up from `$PWD` but stops at the
  enclosing git repository boundary. A nested repo (e.g. a submodule or a
  repo-within-a-repo) without its own `.beads/` cannot see the parent's DB.
  Session hooks keyed on `.beads/` detection also do not fire there.
- **Fix**: either give the nested repo its own DB (`bd init --prefix <p>`), or
  prefix commands with the global directory flag: `bd -C /path/to/parent
  <command>`.
- **This workspace**: `~/.dotfiles` → DB `dotfiles` (prefix `dotfiles-`,
  shell/dotfiles work); `~/.dotfiles/ai-bootstrap` → DB `aib` (prefix `aib-`,
  skill/agent work). Both now live on the **shared external Dolt server**
  (`dolt.TAILNET.ts.net:3307`, k8s-hosted; was `127.0.0.1:3307` /
  `~/gt/.dolt-data` until 2026-08-30) as per-project databases — migrated
  from per-repo embedded Dolt on 2026-06-17, repointed to the k8s host
  2026-09-02. Repo→DB selection is still
  per-`.beads/`, so **ID-prefix auto-routing does NOT work across them**
  (`bd show dotfiles-ac7` from ai-bootstrap fails) — always use `bd -C <repo>`
  when addressing the other repo's beads.
- Observed: 2026-06-12, updated 2026-06-17, bd 1.0.4.

### Migrating a repo from embedded Dolt to the shared external server
- **Goal**: move an embedded-mode repo's beads onto the shared 3307 server as
  its own per-project DB (keeps all repos on one server, data still isolated).
- **Steps**: (1) `bd export` to refresh `.beads/issues.jsonl` AND tar the whole
  `.beads/` aside as a backup; (2) create the target DB:
  `mysql -h dolt.TAILNET.ts.net -P3307 -uroot --ssl-mode=DISABLED -e "CREATE DATABASE <db>;"`;
  (3) re-init in external-server mode importing from JSONL:
  `BEADS_DOLT_PASSWORD= bd init --server --external --server-host dolt.TAILNET.ts.net
  --server-port 3307 --server-user root --database <db> --prefix <prefix>
  --from-jsonl --reinit-local --non-interactive --destroy-token DESTROY-<prefix>
  --skip-agents --skip-hooks`; (4) verify counts server-side
  (`SELECT COUNT(*) FROM issues;`) match the export; (5) persist connection to
  tracked config: `bd dolt set host/port/database <v> --update-config`;
  (6) delete the orphaned `.beads/embeddeddolt` only after `metadata.json`
  shows `"dolt_mode": "server"`.
- **Gotchas**: `--prefix` defaults to the *directory name* on re-init — pass it
  explicitly or `ai-bootstrap` becomes prefix `ai-bootstrap`, not `aib`.
  Non-interactive re-init refuses without `--destroy-token DESTROY-<prefix>`
  (the JSONL is the import source, so this is safe with a backup). Do NOT use
  `--shared-server` — that points at bd's *own* managed server
  (`~/.beads/shared-server/`), not the existing k8s-hosted one; use
  `--server --external`. Root has no password → `BEADS_DOLT_PASSWORD=` (empty).
- Observed: 2026-06-17, bd 1.0.4.

### Stale duplicate `bd` binary on PATH
- **Symptom**: `bd version` warns about multiple binaries; behavior differs
  between shells/worktrees.
- **Cause**: an old dev build (e.g. `~/go/bin/bd`) shadowed by the real one
  (`~/.local/bin/bd`) — PATH-order dependent.
- **Fix**: `which -a bd`, delete or rebuild the stale binary.
- Observed: 2026-06-12 (`~/go/bin/bd` was v1.0.0-dev vs installed 1.0.4).

## State-transition quirks

Some lifecycle transitions error even though the end state is legitimate.
When a direct `bd update <id> --status <to>` is rejected:

- Try routing through an intermediate state
  (e.g. `blocked → open → in_progress` instead of `blocked → in_progress`),
  or `bd close` + reopen (`bd update <id> --status open`) when the guard is
  on the closed path.
- If a transition only fails with extra flags combined, split into two
  `bd update` calls (status first, then labels/notes).
- Record the exact rejected transition and the route that worked as a new
  entry here.

- **Claiming a blocked bead** (`bd update <id> --claim` on a `pr-review-task`
  or any `status=blocked` bead): fails with `Error claiming <id>: issue not
  claimable: status blocked` (exit 0, no mutation). `--claim` only accepts
  `open`/`in_progress`. WORKAROUND: route through open first —
  `bd update <id> --status open` then `bd update <id> --claim`. Note a
  same-call `--claim --add-label X` will silently apply the label but skip the
  claim; verify `assignee` after. Observed 2026-06-13, bd 1.0.4 (the
  coordinator review-lane dispatch hits this every cycle).
- **Claiming an already-`in_progress` bead with an empty assignee**:
  `bd update <id> --claim` fails with `Error claiming <id>: issue not
  claimable: status in_progress`. This occurs when older coordinator state
  left the lifecycle status without an assignee. WORKAROUND: after confirming
  no live worker owns the bead, route `in_progress -> open -> --claim`, verify
  the new assignee, then apply the intended terminal status/metadata in a
  separate update. Observed 2026-07-13, bd 1.0.4.
- **Force-closing a bead with open logical deps**: `bd close <id>` fails with
  `cannot close <id>: blocked by open issues [<dep>] (use --force to override)`.
  When the deliverable is confirmed merged (PR MERGED) but a logical-ordering
  dep is still open, `bd close <id> --force --reason "..."` is correct.
  Observed 2026-06-13 (hud-bq0gl.2: PR #765 merged out-of-order ahead of its
  dep hud-bq0gl.1).

## Other quirks

### `bd edit` blocks agents
`bd edit` opens `$EDITOR` interactively. Never run it from an agent; use
`bd update <id> --title/--description/--notes/--design` instead.

### `bd create --json` returns a plain object, not an array
`bd create --json` emits a single object → extract the id with `jq -r '.id'`.
By contrast `bd show --json` and `bd update --json` emit an ARRAY → use
`jq -r '.[0].id'`. The defensive `jq -r '.id // .[0].id'` works for both;
`.[0].id // .id` does NOT (it throws `Cannot index object with number` on the
create object). Observed 2026-06-13, bd 1.0.4.

### A `|| bd create …` jq-fallback creates a DUPLICATE bead
- **Symptom**: one reconciliation ran `bd create … --json | jq -r '…' || bd
  create … 2>&1 | tail`, and a later `bd ready` showed TWO identical review
  beads (bu-foon5 + bu-63tvy) with the SAME `created_at` second and identical
  descriptions. Observed 2026-07-24, bd 1.0.x.
- **Cause**: `bd create` also prints a human `✓ Created issue: …` line, and
  `bd create --json` output can carry control characters that make the piped
  `jq` exit non-zero EVEN THOUGH the create already committed to Dolt. The `||`
  then re-runs `bd create`, minting a second bead. This is the create-side twin
  of the "jq parse error but the write still landed" race.
- **Fix**: NEVER chain `bd create` with a `|| bd create` (or any `||` that
  re-invokes a mutating command). Run the create exactly once; write long
  `--description` to a file and pass `--description="$(cat file)"`; read the
  new id from the plain `✓ Created issue: <id>` line (or a separate `bd list`),
  not from a jq-or-retry. If a duplicate slips through, the coordinator closes
  the unclaimed twin with `bd close <dup> --reason "Duplicate of <canonical>"`.
  Related: control-character jq breakage at "zsh `echo` can corrupt compact
  `bd --json`" below.

### Epic→task linkage: `bd dep add` rejects it; use `--parent`
- **Symptom**: wiring an epic to its child tasks with
  `bd dep add <epic> <task>` fails: `Error: epics can only block other epics,
  not tasks`.
- **Cause**: epic membership is a parent-child relation, not a `blocks` edge.
- **Fix**: `bd update <task> --parent <epic>` per child (the epic's `bd show`
  then renders a CHILDREN tree with completion %). Reserve `bd dep add` (type
  `blocks`, default) for genuine ordering between tasks.
- Observed: 2026-06-17, bd 1.0.4.

### `bd doctor` unsupported in embedded mode
`bd doctor` is server-mode only; in embedded repos it prints manual
troubleshooting steps instead. Use `bd dolt status`, `bd version`, and
`ls .beads/embeddeddolt/` for embedded diagnostics.

### bd WRITE path silently reverts; jsonl auto-import clobbers committed state (post-3307-migration)
- **Symptom**: `bd update`/`bd close`/`bd update --claim` print `✓ Updated`/
  `✓ Closed` (exit 0) but the change does NOT persist — a later `bd show`/`bd
  ready` shows the bead back at its prior state. Every `bd` call logs
  `auto-importing N bytes from issues.jsonl into empty database`. Under
  concurrent worker `bd` reads it is worse: even a direct
  `mysql … UPDATE … ; CALL DOLT_COMMIT()` to the server gets reverted by the
  next `bd` read.
- **Diagnosis** (aib repo, 2026-06-17, bd 1.0.4): `bd dolt show`/`bd dolt test`
  report the 3307 connection OK and `bd show` DOES read server state (write a
  sentinel via mysql+`DOLT_COMMIT` → `bd show` sees it). But each `bd`
  invocation auto-imports the working-tree `issues.jsonl` and commits it over
  server `main`. When that jsonl is stale (auto-export had failed its `git add`
  on the gitignored `.beads`, and/or the 60s `export.interval` throttle), the
  import REVERTS recent mutations. So jsonl is the de-facto source of truth and
  bd's own write→export→reimport cycle loses writes. `bd dolt show` reports
  `Mode: per-project` (bd wants to run its OWN server from data-dir
  `.beads/dolt`) while the external shared server already holds 3307 → split
  brain.
- **Reliable workaround** (kept a coordinator loop running across 11 closures):
  treat `issues.jsonl` as the store. Mutate by editing it directly, then `bd
  stats >/dev/null` once to propagate jsonl→server. READS (`bd ready`/`bd
  show`) are fine. Avoid `bd create`/`bd dep` (need synthesized records); use
  inline coordinator PR review instead of separate review beads. Also set `bd
  config set export.git-add false` and `export.interval 0` so exports stop
  aborting/throttling.
- **Real fix (TODO, needs human)**: properly establish external-server mode so
  bd stops auto-importing jsonl — re-init per the "Migrating … to the shared
  external server" entry above (`bd init --server --external … --from-jsonl
  --reinit-local`), then remove the orphaned `.beads/dolt` per-project data-dir
  once `metadata.json` shows server mode. Until then, bd writes are unsafe.
- Observed: 2026-06-17, bd 1.0.4.
- **Recurrence + trap (2026-07-06)**: the failure is INTERMITTENT, which makes
  it look fixed. A session saw `bd create`/`--claim`/`bd close` persist across
  several consecutive reads, concluded the write path was healed — then a later
  batch of `bd close` calls half-reverted (2 of 4 closures survived, the rest
  came back `in_progress`/`open` after the next `auto-importing … into empty
  database` cycle). Writes persist only while they win the race against the
  next stale-jsonl re-import. Do NOT trust a small number of persisting writes
  as evidence of a fix; verify `bd dolt show` no longer reports
  `Mode: per-project` before trusting bd writes. Until the real fix lands, use
  the jsonl-edit workaround above for anything you can't afford to lose.

### `bd worktree remove` may rewrite `.gitignore` comments
- **Symptom**: after removing a worktree through `bd worktree remove`,
  `.gitignore` is modified even though the removed worktree path is gone; in
  observed cases, explanatory `# bd worktree` comments around ignored historical
  worktree paths were stripped.
- **Cause**: the helper rewrites the worktree ignore block instead of preserving
  surrounding comments byte-for-byte.
- **Workaround**: for cleanup of Rust/code worktrees that were created with
  `git worktree add`, prefer plain `git worktree remove <path>` plus explicit
  `git branch -D agent/<id>` and remote-branch cleanup after bead closure. If
  `bd worktree remove` was already used, inspect `git diff -- .gitignore` and
  restore comment-only churn unless the ignore entries themselves changed.
- Observed: 2026-06-18, bd 1.0.4.

## resolve_review_context.py: `missing-original-id` on freeform review-bead descriptions

The pr-reviewer-worker helper `resolve_review_context.py` only recognizes the
markers `Original implementation bead:` / `Review target bead:` in the review
bead's description. A coordinator-authored description using freeform phrasing
like `Original bead: bu-xxxxx` fails with `missing-original-id` even though the
mapping is unambiguous (observed 2026-07-05, review bead bu-mm41k). Workaround:
reviewer falls back to the ORIGINAL_BEAD value in its dispatch prompt +
`gh pr view`. Durable fix: coordinators should write
`Original implementation bead: <id>` verbatim in review-bead descriptions.

## create_inline_review_comment.py broken on gh 2.95.0 (2026-07-05, butlers PR #2996 review)

`beads-pr-reviewer-worker/scripts/create_inline_review_comment.py`'s
`existing_comments()` calls `gh api` without `--method GET`, so gh 2.95.0
defaults to POST and always errors with a schema mismatch before the real
create call runs. `list_review_threads.py` and `resolve_review_thread.py`
are unaffected. Workaround: post the comment with raw
`gh api repos/{owner}/{repo}/pulls/{n}/comments -X POST ...`.
Fix when touching the script: add `--method GET` to the duplicate-check call.
ROOT CAUSE CONFIRMED (PR #3004 review): gh auto-switches to POST whenever -f/-F flags
are present, so the listing call with -F per_page=100 silently POSTs to the
create endpoint and 422s every time.
FIXED 2026-07-06: `--method GET` added to `existing_comments()` in the script
itself. Entry retained in case the regression pattern (gh api + -f/-F without
an explicit method) reappears in other scripts.

## `bd sync` removed in bd 1.0.4

- **Symptom**: `bd sync` fails with `Error: unknown command "sync" for "bd"`.
- **Cause**: Beads version control is now exposed through `bd vc` and
  `bd dolt`; the old aggregate `sync` command is absent.
- **Fix**: use `bd dolt commit -m "<message>"` for pending Beads changes,
  then `bd dolt push` when a Dolt remote is configured. `bd dolt push`
  safely reports and skips when the local database has no remote.
- Observed: 2026-07-10, bd 1.0.4.

## `bd dolt push` errors in external-server mode (no BEADS_DOLT_CLI_DIR)

- **Symptom**: `bd dolt push` fails with `Error: dolt push requires a local
  Dolt CLI database directory in external-server mode; set
  BEADS_DOLT_CLI_DIR to the local Dolt database path or use a remote type
  supported by SQL DOLT_PUSH/DOLT_PULL`. It does NOT "safely skip" as the
  `bd sync` entry above suggests — that skip behavior predates the
  shared-external-server migration.
- **Cause**: post-3307-migration repos run against the shared external
  server (`dolt.TAILNET.ts.net:3307`), where bd cannot drive a CLI push without an
  explicit local Dolt database path, and no Dolt remote is configured for
  the per-project DBs anyway.
- **Disposition**: session-end "push beads" is currently a no-op for these
  repos — durability rests on the shared server's data dir plus the jsonl
  export where one exists. Do not chase BEADS_DOLT_CLI_DIR; setting up a
  real Dolt remote is part of the same "needs human" external-server
  repair tracked in the WRITE-path entry above.
- Observed: 2026-07-19, bd 1.0.4, aib repo.

## zsh `echo` can corrupt compact `bd --json` before `jq`

- **Symptom**: piping a compact JSON object stored in a shell variable through
  `echo "$record" | jq ...` fails with `Invalid string: control characters
  from U+0000 through U+001F must be escaped`, while the original
  `bd show <id> --json | jq ...` succeeds.
- **Cause**: zsh's `echo` interprets backslash escapes by default. Escaped
  newlines in Beads notes become literal control characters before `jq` sees
  the JSON; the `bd` output itself is valid.
- **Fix**: project fields directly from `bd ... --json | jq ...`, or preserve
  the variable byte-for-byte with `printf '%s' "$record" | jq ...` (or zsh
  `print -r -- "$record"`).
- Observed: 2026-07-20, bd 1.0.4, zsh.

## `bd update --claim` refuses blocked beads: "issue not claimable: status blocked"

Observed bd 1.0.4 (butlers, 2026-07-25). The coordinator PR-review lane says to
atomically `--claim` a blocked `pr-review-task` bead before dispatch, but
`bd update <id> --claim` hard-fails on `status=blocked` beads with
`Error claiming <id>: issue not claimable: status blocked`.

Workaround (two steps, small non-atomic window is acceptable because review
beads are coordinator-owned):

```bash
bd update <id> --status open --json >/dev/null
bd update <id> --claim --json   # sets assignee + in_progress atomically
```

Verify `assignee` in the result as usual. Same applies to any blocked bead you
intend to claim for dispatch (e.g. after a decision sweep unblocks it).

## `resolve_review_context.py` misses reverse "blocks" dependent edges (review bead → original)

**Symptom:** A pr-reviewer worker's `resolve_review_context.py` reports missing-original-id even though the coordinator wired `bd dep add <original> <review>` correctly. Cause: that dep shape puts the link in the ORIGINAL bead's `dependents` array (original --blocks--> review); the helper only walks the review bead's own forward `dependencies` array (fallback A) or requires the pr-review label on the other side (fallback B). Hit twice on 2026-07-25 (bu-o4kzr, bu-zu768).

**Workaround:** The reviewer proceeds with the original_id/PR/head already supplied in the coordinator dispatch (always include them), or resolves manually: `bd show <review-bead> --json | jq '.[0].dependents'` / `bd show <original> --json | jq '.[0].dependents'` and cross-checks via `gh pr view`. Fix the helper to also walk dependents edges when convenient.

## `resolve_review_context.py` missing-original-id: marker regex `\b` breaks on escaped newlines

**Symptom:** Same `missing-original-id` outcome as the entry above, but the dep
edge is fine and the review bead *does* carry a `Review target bead: <id>. PR:
<url>` marker in `notes`. Observed 2026-08-22 (bu-r07ow → bu-istke.2, PR #3749).

**Cause:** the marker-matching regex relies on a `\b` word boundary, and the
surrounding notes text contains literal two-character `\n\n` sequences (backslash
+ `n`, not real newlines) written by an earlier `bd update --append-notes`. The
backslash is a word character on one side of the boundary, so `\b` fails to
match and the marker is skipped even though it is present.

**Workaround:** always inline `ORIGINAL_ID`, `PR`, and the head SHA in the
coordinator dispatch prompt so the reviewer never depends on the resolver. The
reviewer confirms with `gh pr view <n> --json headRefName,headRefOid,baseRefName`
plus `bd show <review-bead> --json | jq -r '.[0].notes'`.

**Prevention:** when writing notes with `bd update --append-notes`, pass real
newlines (a quoted multi-line shell string), never `\n` escapes — `zsh`'s `echo`
and some `bd` paths leave the literal backslash in the stored field. See also the
zsh `echo` entry above.

## `assert_worker_context.py` rejects correction work on a pre-existing PR branch

**Symptom:** A dispatched worker's bootstrap check fails with a
branch-must-equal-`agent/<ISSUE_ID>` mismatch even though the coordinator
assigned that branch deliberately. Observed 2026-08-22 (bu-0oyaw.1 on
`agent/bu-0oyaw-secrets-user-detail-parity`, PR #3660).

**Cause:** the helper hardcodes the `agent/<ISSUE_ID>` naming convention. That
holds for fresh implementation beads, but not when a bead is dispatched to
CORRECT an already-open PR whose branch was named by an earlier run (or by a
human). The branch is correct; the assertion's assumption is not.

**Workaround:** the coordinator must state the expected branch name explicitly
in the dispatch prompt and tell the worker the mismatch is expected, so the
worker proceeds on the assigned branch instead of treating it as
`invalid-runtime-context` and halting. Do the same for any reviewer dispatched
against that PR.

**Prevention:** when dispatching correction work onto an existing PR, always
inline the real branch name plus a one-line note that `assert_worker_context.py`
will disagree. Cheaper than a halted worker.

## A dispatched reviewer goes idle without ever sending its verdict

**Symptom:** a `beads-pr-reviewer-worker` completes all its mechanical work — gate
green, checks green, findings gathered — and then emits an
`idle_notification (available)` instead of a report. Nudging it produces another
idle notification, not a verdict. Observed 2026-08-21 (reviewer-bu-e2y3x on
PR #3750; two nudges, two idles, zero reports).

**Why it matters:** the coordinator's closure rule needs a verdict, so the
instinct is to keep waiting. That can stall a fully-green PR indefinitely on an
agent that will never answer.

**Workaround:** do not nudge more than once. Reviewer verdicts on a merge-ready
PR are almost always reducible to a handful of mechanical checks the coordinator
can run itself against the diff — and verifying them directly is both faster and
stronger evidence than an inherited claim. The generic set:

```bash
BASE=$(git merge-base HEAD origin/main)
git diff $BASE..HEAD -- tests/ | grep -c '^-.*def test_'          # tests deleted (expect 0)
git diff $BASE..HEAD -- tests/ | grep -E '^\+.*(pytest\.mark\.(skip|xfail)|pytest\.skip)'
git diff $BASE..HEAD --stat -- src/                                # production blast radius
git diff $BASE..HEAD -- tests/ | grep '^-' | grep -v '^---'        # every removed line, read them all
```

Then confirm the PR head equals the gate's head, `gh pr checks` is all-pass, and
`mergeStateStatus` is `CLEAN`. Record in the bead's notes which claims you
verified yourself versus inherited — that distinction is the whole point.

**Prevention:** state the verdict format in the dispatch prompt as the *first*
deliverable rather than the last, and tell the reviewer to send it before running
any final gate. A reviewer that reports findings incrementally cannot strand the
lane by going idle at the end.

## A worker's cwd resets between tool calls and can land it in another worker's worktree

**Symptom:** in a parallel run, an agent's shell working directory changes
nondeterministically between Bash calls, sometimes to a *different* worker's
worktree under `.worktrees/parallel-agents/`. Observed 2026-08-21: a reviewer
scoped to `bu-whgyw` found one call executing inside `bu-0uqgo.2`.

**Why it is dangerous here:** every parallel-agent worktree is a checkout of the
same repository with the same file layout, so a relative-path edit intended for
one branch will apply cleanly to another and produce a silent cross-branch
contamination that no test on either branch would catch.

**Workaround:** never rely on ambient cwd in a worker.

- Pin every git call with `git -C "$WORKTREE_PATH" …`.
- Use absolute paths for reads and edits, or `cd "$WORKTREE_PATH"` at the start
  of *each* compound command rather than once per session.
- Make edit scripts assert a unique match on text that exists only on the
  intended branch, so a misdirected edit fails loudly instead of applying.
- Re-assert `pwd` in the same command that performs the write, not in a
  preceding call.

**Prevention:** the worker bootstrap proof (`pwd` == `WORKTREE_PATH`) verifies
the starting directory only. It does not survive to later calls, so treat it as
a launch check, not an invariant.

**Addendum (observed 2026-08-21, second instance).** The danger is not only a
misplaced *write*. A worker on `bu-0035u` had its `pwd` come back inside
`bu-0uqgo.2`'s worktree mid-session; its patch scripts used absolute paths so
every write landed correctly, but a `grep` in that same compound command
searched the wrong tree and returned a clean sweep that meant nothing. **A false
negative from a verification command is worse than a misplaced write**: a
misplaced write leaves evidence in another worktree's `git status`, while a
false all-clear leaves none and gets reported upward as proof. So the rule is
not "use absolute paths when writing" — it is *pin every read that produces
evidence*, `grep -r`, `find`, `ls`, and `git` alike. When a sweep is going into
a report, re-run it with an explicit path or `git -C` and say in the report that
you did.

This is the same family as the killed-gate-with-no-summary trap, the
`cmd 2>&1 > file` vacuous "identical", and `pgrep -f "<pattern>"` matching its
own command line: **a check whose absence of output is read as a definite
result.** Before trusting any all-clear, assert the check actually ran where you
think it ran and actually produced output.

## `pgrep -f "<pattern>"` reports a finished process as still running

`pgrep -f` matches against full command lines — including the command line of
the shell that is running the `pgrep` itself, which contains the pattern as an
argument. So a poll like
`pgrep -f "/path/to/.venv/bin/python -m pytest" && echo "still running"` prints
"still running" forever, long after pytest exits. Observed 2026-08-21: a worker
armed a waiter on "pytest process exit", the gate finished cleanly
(`13704 passed, 21 skipped ... in 1077.82s` sat in its log), and the waiter never
fired; the worker idled indefinitely while the coordinator's own `pgrep`-based
checks agreed with it.

Diagnose by cross-checking with a matcher that shows you the actual rows:
`ps -eo pid,etime,stat,args | grep '<pattern>' | grep -v grep` — if that is empty
while `pgrep -f` succeeds, the `pgrep` is matching itself. Fix by holding a real
PID (`cmd & pid=$!; wait "$pid"`) rather than re-deriving liveness from a
pattern. If you must pattern-match, exclude the current shell (`pgrep -f pat |
grep -v "^$$\$"`).

## A worker dies on a usage limit with uncommitted work in its worktree

Symptom: an `idle_notification` arrives with
`"idleReason":"failed","failureReason":"You've hit your session limit · resets <time>"`.
Observed 2026-08-21, both live workers within 33 seconds of each other — a shared
account limit takes the whole fleet down at once, not one agent, so expect
simultaneous deaths rather than a single casualty.

**The work is still on disk and it is not safe.** Neither worker had pushed.
`git -C <worktree> status --porcelain` showed nine and eight modified/untracked
files respectively — over 1500 lines of real work with no remote copy and no
commit. Anything that removes or resets that worktree destroys it silently.

Coordinator recovery, in order:

1. **Check the reset time before deciding to reschedule.** The limit string is in
   the user's local timezone; convert it. In the observed case the workers died
   at 21:20Z quoting a 7:10am Asia/Singapore reset — 23:10Z the same day — and by
   the time the coordinator processed the notification the limit had already
   lifted. Rescheduling would have idled the fleet for no reason. Note that the
   *coordinator* can keep running normally while its subagents are limited.
2. **Salvage-commit each worktree immediately**, before any cleanup, on the
   agent's own branch, with a message that says plainly that the coordinator
   made it and that the contents are unreviewed and not gate-passed. A resuming
   worker must not mistake a salvage commit for a completed increment.
3. **Do not push the salvage commit** if CI runs on agent-branch pushes — it
   burns a runner on known-incomplete work. Local commit is enough to make it
   durable.
4. **Verify no `.beads/` content was swept in** by a `git add -A`:
   `git show --name-only --format= HEAD | grep -c '^\.beads/'` must be 0.

**Then diff the salvaged artifact against every instruction sent late in the
worker's life.** Delivery is not application. In the observed case the
coordinator sent a blocking migration-renumber ruling at ~21:05Z; the worker
committed the un-renumbered file at 21:16Z and died at 21:20Z, almost certainly
never having processed the message. A second worker received two blocking
corrections at ~21:15Z and none of them are in its salvaged code. **Read the
files, do not assume a message that was sent was acted on** — then write the
outstanding corrections into the bead's notes so the resuming worker inherits
them, since the mailbox does not survive the dead agent.

One thing the salvage will surface that the original ruling may have missed: a
correction can have an *ordering* constraint the coordinator did not state. Here
the fix was "renumber `core_199` to `core_200`", but `core_200` needs
`down_revision = "core_199"`, and that revision only exists once the sibling PR
merges. Renumbering on resume would have produced a broken chain. Check whether
your correction is safe to apply *now* or only after some other merge, and say
which in the resumption brief.

## A worker justifies a design deviation with a case that cannot distinguish the two designs

Observed 2026-08-21 on a cron-cadence estimator. A worker deviated from the
brief (years-else-days) to years-else-**weeks**-else-days and justified it with
`* * * * 1` (per-minute, Mondays only), claiming days-only over-reports ~20%.
Re-running the arithmetic independently in the worker's own worktree showed
days-only was **exact** for that expression at every computable cap
(2000/3000/5000/10000 all returned the true 6261.30). The justification did not
reproduce.

The deviation was still correct — just for a different reason. The estimator's
anchor was `2001-01-01`, a **Monday**, and the dense weekday was also Monday, so
truncating the sampling window to whole days happened to land on a week boundary
anyway. `* * * * 1` is the one weekday out of seven where the two designs
provably cannot differ. Sweeping the weekday field exposed the real failure:
`* * * * 3` gave 4873 against a true 6261 (22% low) and `* * * * 0` gave 3374
(46% low); the weeks tier fixed all six non-anchor weekdays exactly. The error
direction was also opposite to what the worker described — under-reporting on an
offset weekday, not over-reporting on the anchor weekday.

**The consequence is a test that proves nothing.** The worker had written
`test_weekly_seasonal_high_frequency_cron_uses_a_whole_week` around
`* * * * 1`, so it passes with *and* without the tier it is named for. This is
the recurring family again — a check whose result is indistinguishable from the
check not running — and it is the most dangerous member of it, because the test
name is a durable false claim that survives into every future reader's mental
model of what is covered.

Practice:

- **Never accept a deviation's stated rationale without re-deriving it.** Accept
  or reject the *change*; verify the *reason* separately. Here the change was
  right and the reason was wrong, and only the reason had been argued.
- **When a computation truncates to a window, test a case offset from the
  anchor.** Anchor-aligned inputs are the degenerate case for any
  align-to-boundary logic — a weekly rule anchored on a Monday, a monthly rule
  anchored on the 1st, an hourly rule anchored on :00. Sweep the offset field
  and pick a value where the candidate implementations demonstrably diverge.
- **The falsification test for any new test is: does it fail if I delete the
  code it names?** If the worker cannot state the pre-fix number their new test
  would have produced, the test is not pinning that code. Ask for the two
  numbers, not for reassurance.
- **Say plainly that the rationale was wrong even when approving the change.** A
  wrong reason left standing in the record becomes the precedent the next worker
  reasons from.

A related payoff: the same sweep incidentally proved a guard I had asked for was
not hypothetical. At a lower cap the window truncated to **zero** whole days and
the computation returned `nan` — a live division by zero. Sweeping a parameter
to check one claim routinely surfaces a second defect for free; prefer a sweep
over a single spot-check.

## Verifying a reviewer's facts is not verifying their conclusion

Observed 2026-08-22. A high-risk reviewer filed two blocking findings against a
PR: scenario headings in an OpenSpec delta had been "reverted" to name a retired
mechanism, and ten already-archived scenarios had been "resurrected" into the
delta. Both were argued rigorously — correct `git log -S` provenance, the right
commits identified, byte-level quotes. As coordinator I re-derived both
independently before acting, found the reviewer's facts true (and their scenario
count low by two), and wrote into the worker's brief: *treat them as established
fact, not hypotheses to re-investigate.*

Both findings were wrong. openspec requires a `## MODIFIED` block to reproduce
every scenario **name** the baseline still carries, because a MODIFIED block
replaces the whole requirement. So the carried scenarios were mandatory, not
duplicated, and the headings were the current baseline names and could not be
renamed at all. The commit the reviewer blamed for breaking the delta was the
commit that had made it valid. The dispatched worker disconfirmed it, against a
coordinator instruction telling it not to.

What went wrong is precise and worth naming: **the reviewer asserted facts F and
a conclusion C, and I verified F.** Every fact was true and C did not follow from
them. "These scenarios exist in the baseline AND appear in the delta" is
compatible with both "erroneous duplication" and "required carry-over"; nothing
in the evidence distinguished them, and neither of us asked what the tool
*requires* a delta to contain.

Practice:

- **Verify the load-bearing inference, not the citations.** When a finding has
  the shape "X is present, therefore X is wrong", the citations establish only
  presence. Ask what else would produce X. If a tool, framework, or convention
  could *require* X, check that before filing the blocker.
- **Run the counterfactual, not the observation.** The dispositive test here was
  one command: copy the spec tree to a scratch directory, apply the proposed fix,
  and validate. It produced six errors naming six requirements, and the error
  text stated the rule outright. Cost: under a minute. Wherever a fix is cheap to
  simulate in a throwaway copy, simulate it before prescribing it.
- **Never write "established fact, do not re-investigate" into a worker brief.**
  Certainty is the coordinator's to hold, not to impose. That sentence is
  precisely what a worker sitting on disconfirming evidence has to overcome, and
  the whole value of dispatching an independent agent is that it can see what you
  cannot. State confidence and its basis; leave the door open.
- **Retract in the durable record, not only in the reply.** The wrong ruling
  lived in a bead note that outlives the session; the correction has to land in
  the same place, and on the closed review bead too, or the next reader inherits
  the error with a coordinator's signature on it.

The reviewer's report was still worth its cost: its third finding — two test
dilutions where a replacement stub stopped raising, and a test whose name and
docstring survived while only its assertion changed — was real, was invisible to
a definition-count audit, and stood. And its section listing **what it did not
check** is what made the rest correctable. Require that section.

## A worker's summary asserts a value its own code and tests contradict

A worker reported a defect fixed "exact at 3720.00". The code returned `0.0`.
Not a rounding disagreement: the estimator hit an occurrence cap and returned a
"not forecastable" sentinel, which is a different product from a correct number.
Both beat the `43829.1` it replaced, so the fix was real and the summary was
still false.

The tell was internal, not external. The same message quoted a docstring saying
the expression was "too dense to enumerate ... within `_CADENCE_MAX_OCCURRENCES`",
and the test file it named parametrised that very cron over the
not-forecastable path. **The code, the docstring, and the tests all agreed with
each other and disagreed only with the prose summarising them.** Nothing about
the branch had to be re-derived to catch it — one eight-line script printing the
function's output over the claimed inputs settled it.

This is the same family as a killed gate with no summary line and a `pgrep -f`
matching its own command line: a check whose result is asserted rather than
read. Here the assertion is a worker's recollection of what it meant to build.

Practice:

- **When a report claims a specific numeric value, run the function on the
  claimed input.** It is nearly free and it is the only thing that distinguishes
  "measured" from "intended". Do it before ruling, not after merging.
- **Read the artifact the report cites, not just the report.** A docstring or a
  test name in the same message that contradicts the summary is the cheapest
  possible signal and costs one `grep`.
- **Approve the change and correct the record separately.** A wrong summary over
  right code is not grounds to reject the code; it is grounds to fix what goes
  into the durable record, because the bead notes and the PR description are
  written from the summary.
- **Tell the worker to write the summary from the test output.** A worker that
  has been corrected on this twice is describing its intent, not its artifact,
  and saying so plainly is more useful than re-checking every future number.
- Watch for the corollary: a report listing several results "all exact" is
  likelier to be reciting the design goal than reading a table. Spot-check the
  hardest case in the list, which is the one most likely to have been handled
  by a sentinel rather than solved.

## A mid-turn sample of another agent's working tree is not an observation

**Symptom.** The coordinator runs `git status --porcelain` / `git log -1` inside a
worker's worktree, sees a dirty tree or a stale HEAD, and concludes the worker has
not applied or has not committed its fix. The worker replies with `git ls-remote`
showing the commit already on the remote: the coordinator sampled the seconds
between the edit and the commit inside a single worker turn.

**Why it happens.** A worker's tree is dirty *by construction* while it works. The
coordinator does not control when its sample lands relative to the worker's turn
boundaries, so the observation has no defined meaning. It is the same family as a
killed gate log read as a completed one: a measurement whose timing you did not
control, reported as a settled result.

**Both directions are real, and they want opposite corrections.**

- *False negative* (this case): coordinator withholds a resource — the Docker
  socket, the merge, the hand-off — over a fix that is already pushed. Cost:
  blocked progress, and a worker told it failed to do something it did.
- *False positive* (the case the rule still closes): coordinator samples *between*
  worker turns, when the tree genuinely is settled, and a worker that reported
  "standing by" with uncommitted work gets handed the socket. Cost: a green gate
  log that appears to vouch for a fix that was never in the tested tree.

The false positive is the more dangerous of the two, which is why the hand-back
rule below stays even though the false negative is what usually gets noticed.

**Practice.**

- `git ls-remote origin refs/heads/<branch>` is the only tree observation a
  coordinator can act on. It is a fact about what exists, not about where an agent
  is standing mid-edit.
- Require every worker hand-back to carry: local HEAD sha, remote sha, and either
  "tree clean" or an explicit list of what is uncommitted. This makes the settled
  state *reported* rather than *sampled*, which removes the timing dependence.
- Never grant a serialized resource on an inference about another agent's state.
  Before handing over the gate socket, confirm the holder actually released it —
  `ps -eo args --no-headers | grep -cE '[p]ytest'` — rather than assuming from a
  message that the run finished.
- When you do get this wrong, check the sign of the error before writing the
  lesson. "I nearly gated a stale tree" and "I nearly blocked a pushed fix" are
  different mistakes with different fixes, and the alarming one is not always the
  one that happened.

## The backend gate must be run detached, with the exit code captured to a file

**Symptom.** `uv run pytest tests/ --ignore=tests/e2e` reports exit 143 at exactly
10m00s. Read as a failure or a hang. It is neither: 143 is SIGTERM from the agent
tool's foreground timeout. The suite genuinely needs ~9m46s on this repo, so a
foreground run sits far enough inside the cap that it will *always* look like a
timeout failure, and the closer the suite creeps to 10m the more often it happens.

**Practice.** Launch detached and persist the exit code, then read the file:

```bash
cd <worktree> && rm -f .tmp/test-logs/gate.log .tmp/test-logs/gate.exit
( uv run pytest tests/ --ignore=tests/e2e -q --tb=short \
    > <abs>/.tmp/test-logs/gate.log 2>&1; echo $? > <abs>/.tmp/test-logs/gate.exit ) &
```

Absolute paths for the redirects: a backgrounded command's cwd does not persist,
and a relative redirect can land in a different worktree. Same reason every `git`
call in a fleet worktree should be `git -C <abs-path>`. `rm -f` the log first so a
second run cannot be read as an append to a completed one.

## `--maxfail=1` on a final gate turns a partial run into a false all-clear

**Symptom.** Gate log ends with what looks like near-total success:

```
1 failed, 10218 passed, 16 skipped in 586.25s
xdist.dsession.Interrupted: stopping after 1 failures
```

The interrupt fired at 73%. Roughly a quarter of the suite never executed against
the change at all. "10218 passed" is evidence for the 73% that ran and says
nothing about the rest — but it *reads* as comprehensive, and a coordinator
scanning the tail will accept the branch on it.

**Practice.**

- `--maxfail=1` is the right flag while iterating and the wrong one for the final
  pre-merge gate. On a final run it saves minutes only when the branch is already
  broken, and costs a full ~10-minute cycle whenever there is a second independent
  failure.
- Never read a passed-count without the completion percentage. `Interrupted`, or
  any percentage below 100, means the run did not finish regardless of the count.
- Under `xdist`, `--maxfail` prints `xdist.dsession.Interrupted` and progress dots
  continue to appear *after* the `F`. Those are workers draining, not tests
  running past the failure. Do not read them as continued coverage.
- Same family as the SIGTERM above and as a mid-turn tree sample: a check that
  *stopped* being mistaken for a check that *concluded*. When a gate ends early,
  the count it reached is not a result.

## A green gate is silent about the direction most guard defects live in

**Symptom.** A branch fixes authorization/ownership guards, the full suite passes,
and the result gets reported as "the fixes are validated." It is not. A passing
suite establishes that no guard **rejects a caller the suite exercises**. Every
guard defect of the common kind is the opposite failure: a guard that **fails to
reject an invalid caller**, or that sits where it cannot fire at all. A suite full
of valid callers is silent about both.

**Worked example** (butlers PR #3742, three instances in one review):

- A function-ownership assertion listed two names inside a block filtering
  `nspname = 'dnd_generation_admin'` while both functions live in
  `runtime_attention_admin`. The `EXISTS` could never match; the guard asserted
  nothing. No test failed, before or after the fix.
- A `SECURITY DEFINER` guard read `current_user <> bootstrap_role`, which under
  `SECURITY DEFINER` is the function owner by construction — a tautology, so the
  `RAISE` could never fire. No test failed.
- The fix added a `rolsuper` branch that **no test in the tree can reach**, because
  the migration harness creates the Alembic login explicitly `NOSUPERUSER`. The
  gate can show the fix broke nothing; it cannot show the fix works.

**Practice.**

- Report a green gate as *"no change rejects a caller the suite exercises"*, not
  as *"the change is validated."* State the claim the evidence supports.
- For any guard change, ask which branch the test harness can actually reach.
  Fixtures that pin an identity (a `NOSUPERUSER` login, a fixed role) make whole
  branches structurally unreachable — that is not incidental coverage debt, it is
  a branch nothing will ever execute.
- Negative-path coverage needs a caller the guard should *refuse*. If no test
  constructs one, the guard's actual job is untested no matter how green the run.
- The tell for a misplaced guard is cheap and does not need a test: read the
  enclosing control flow and ask which branch runs in the case you care about.
  All three above were found that way, none by a failing test.

## A gate attests to the tree it ran against — rebasing afterward spends it

**Symptom.** Worker reports a clean full-suite run, then syncs with main. HEAD now
differs from the tested tree, but the green log is still sitting there reading as
merge evidence. Observed timeline:

```
08:30:32  commit 623eab37f
08:47:02  gate.exit written (exit 0)      <- 13822 passed, 100%
08:47:16  rebase (start): checkout origin/main
08:47:17  rebase (finish) -> d3475a05e
```

`git diff --stat 623eab37f d3475a05e` was 15 files / 2505 insertions of *other
people's merged work* pulled under the branch's own commits. The branch commits
were unchanged; the base moved. The green run described a tree that was no longer
the merge candidate.

**Two distinct hazards, do not conflate them.**

- *Rebase during the run*: tests read files that move underneath them. Results are
  meaningless or, worse, a mix. Check the reflog timestamp against the log/exit
  file mtimes — here the rebase started 14s **after** the exit file was written,
  so the run itself was sound.
- *Rebase after the run*: nothing is corrupted, but the evidence no longer
  describes HEAD. This is the quiet one, because everything looks fine.

**Practice.**

- Rebase **before** gating, then gate the rebased tree. If you rebase after, the
  gate is spent.
- Before accepting a gate as merge evidence, verify the head it ran against:
  compare `git rev-parse HEAD` to the head at run time, and `git rev-parse
  HEAD^{tree}` to be sure a same-message commit is not a rewritten one. A rebase
  preserves the subject line, so `git log --oneline -1` looks identical while the
  sha and tree have both changed.
- Prefer CI on the *pushed* head over a local run for final merge evidence: the
  pushed tree is the thing being merged, and CI cannot be invalidated by a local
  rebase.

## Read the CI workflow before claiming what CI does not cover

**Symptom.** Coordinator tells the fleet "the local gate is the only executable
check for this change," builds a worker's whole hold around it, and is wrong. Job
names (`check`, `frontend`, `em-dash-guard`) invite inference; they do not
describe coverage.

In this repo `check` runs **two** pytest lanes, not one:

- unit: `-m "not integration and not e2e and not nightly and not bench and not perf"`
- integration: `-m "integration and not nightly and not bench and not perf"`,
  against a `postgres:16` service container

So integration-marked tests *are* covered by CI. Separately, a test with **no**
marker falls into the unit lane regardless of what it actually does — a
testcontainers-based migration test with only `@pytest.mark.asyncio` runs in the
"fast/no-Docker" lane, and works there only because the runner happens to have
Docker.

**Practice.**

- `grep -nE "^  [a-z0-9_-]+:" .github/workflows/*.yml` for the job list, then read
  the actual `run:` lines. Two minutes, and it settles what a pending CI job will
  and will not execute.
- Check the test's own markers before asserting which lane runs it. Absence of a
  marker is a routing decision, not a neutral state.
- Same family as everything else in this file: an inference about a check,
  reported as a fact about a check.

## The `[p]ytest` liveness check counts its own invoking shell

**Symptom.** `ps -eo args --no-headers | grep -cE '[p]ytest'` is used to decide
whether the serialized backend gate is free. It returns 2-3 on a machine with **no
pytest running**, so a coordinator comparing it against 0 sees "always busy" and a
worker can be held indefinitely.

**Why.** The `[p]ytest` bracket trick stops `grep` from matching *its own* entry —
and that part works. But the agent harness runs commands as
`zsh -c '<the whole command string>'`, and that wrapper's command line contains the
literal text `[p]ytest`. `ps` faithfully reports the wrapper, once per shell in the
pipeline. Verified: a nonsense control token returns 0, while `[p]ytest` returns 4
with exactly one real run in flight — three of the four hits were
`/usr/bin/zsh -c source …` lines.

**Fix.** Match the process that actually runs the suite and drop the shells:

```bash
ps -eo args --no-headers | grep -E '(python[0-9.]*|uv)[^|]*pytest' | grep -vc 'zsh -c'
```

**Practice.**

- Any `ps | grep` liveness check written inline in an agent harness must exclude
  the harness shell, or it self-matches. This is not the classic grep-matches-itself
  problem and the bracket trick does not fix it.
- Sanity-check every such probe against a token that *cannot* be running. If the
  nonsense control returns non-zero, the probe is measuring itself.
- Prefer a positive assertion over an absence: confirm the exact process you expect
  is gone, rather than that a fuzzy pattern count reached zero.
- Same family as the rest of this file, and the sharpest instance of it: a check
  positioned where it cannot produce the answer it is asked for. Here the
  coordinator wrote the broken check, handed it to a worker as the way to verify
  the coordinator's own claim, and the worker caught it. Give workers the probe
  *and* the expectation, so a nonsense reading is visible as nonsense.

## Attest a tree hash, not a sha — and check the blob for a single-file change

**Symptom.** A reviewer attests "merge-ready at `<sha>`." The branch is then
rebased or amended. `git log --oneline -1` still shows the same subject line, so
nothing looks different, but the sha and tree have both moved and the attestation
silently covers a tree nobody reviewed.

**Practice.** Pin all three, and state that the attestation lapses if the tree
hash changes regardless of sha or subject:

```
commit  ad5b965012da7b89eb4032287fd4901d6665e82c
tree    0e250dbe740b2606701d0cc5febd4268bd97b900
blob    708e374bac4f32d165acb39630a3e63ba7362bcb  scripts/init-db.sql
        git status --porcelain -> 0 lines
```

When every change in the review lives in one file, pin that file's blob too:
`git rev-parse HEAD:<path>`. If the blob still matches, the reviewed content is
byte-identical and can be re-confirmed in one command without re-reading the diff
— useful after a rebase that legitimately moves the tree without touching the
reviewed file.

## "Collected but skipped" is the other way a gate goes green for the wrong reason

Companion to the `--maxfail` and green-gate entries above. A test can be collected
by the lane you expect and still contribute nothing, if it carries a
`pytest.mark.skipif` on an environment probe — this repo's common shape is
`docker_available = shutil.which("docker") is not None`. A skipped test reports as
green.

**Practice.** When a gate is the evidence for a specific change, confirm the test
that exercises it (a) is collected by the lane that runs, and (b) has no skip guard
that the runner could trip. Absence of a guard is the good case: the test will
*error* rather than skip if its dependency is missing, so it cannot pass silently.
Grep the file for `skipif` and for any locally-defined availability flag before
treating a green run as evidence.

## A well-formed `Claude-Session:` trailer PASSES this repo's session-link guard

**Do not strip it, and do not rewrite history to remove it.**

`scripts/session_link_guard.py` scans PR title, PR body, review comments, **and
commit messages** (`--commit-range base..head`). It rejects `claude-session\s*:`
and `claude.ai/code/session[_-]...` anywhere — with one documented exception,
stated in its own docstring: an exact
`Claude-Session: https://claude.ai/code/session_...` line sitting in a **terminal
Git trailer block** that `git interpret-trailers` recognizes. The guard strips
those before scanning (`_strip_allowed_claude_session_commit_trailers`).

So the standard Claude Code commit trailer is fine. What is *not* fine is the same
text in prose, in a PR body, in a review comment, or as a folded continuation that
`git interpret-trailers` will not parse as a trailer. The generic harness
instruction to end PR bodies with the session-URL footer therefore breaks these
PRs: in worker-dispatch prompts for such repos, keep the commit trailer but tell
workers not to append the footer to the PR body. A tripped PR is fixed with
`gh pr edit <PR#> --body-file <body-without-footer>` (re-triggers the guard, no
head change).

**Observed failure.** A worker predicted `session-link-guard` "would have failed"
on an inherited commit carrying that trailer, ran `git filter-branch --msg-filter`
over `origin/main..HEAD`, and force-pushed 7 rewritten commits. Verified after the
fact: the pre-rewrite range passes.

```bash
# Two seconds, settles it, needs no CI:
printf 'x' > /tmp/t.txt; printf 'x' > /tmp/b.txt; echo '[]' > /tmp/rc.json
python3 scripts/session_link_guard.py \
  --pr-title-file /tmp/t.txt --pr-body-file /tmp/b.txt \
  --commit-range "<base>..<head>" --review-comments-file /tmp/rc.json
echo "exit: $?"        # -> "clean — no tool-session links found.",  exit 0

# And to check whether git considers the trailer well-formed:
git log -1 --format='%B' <sha> | git interpret-trailers --parse
```

**Practice.**

- Never rewrite published history on a *predicted* CI failure. Guards in this repo
  are plain scripts — run the actual guard against the actual range first.
- This is the inverse of the pattern the rest of this file catalogs. Elsewhere a
  check gets credited with an answer it never gave; here a check was credited with
  a **failure** it never gave, and something destructive was done in response.
- A force-push silently voids any reviewer attestation pinned to a sha. Harmless
  here only because no attestation was outstanding on that branch and the tree
  came out byte-identical (verify with `git rev-parse <old> <new>` on `^{tree}`).

## `ListAgents` does not list your own in-process subagents — absence there is not evidence they exited

Symptom: the coordinator runs `ListAgents` to decide whether a worker slot is free, sees only rows
headed `Peer sessions (N)` — other tmux/Remote-Control sessions — and concludes its dispatched
workers have finished. It then dispatches replacements and silently exceeds its worker cap.

What actually happened: `ListAgents` renders *peer sessions*. Dispatched in-process subagents are a
different category and did not appear in that section at all. The listing also ended with
`(session list too long to fetch completely — sessions beyond the first pages are missing from this
listing)`, so even the category it *was* showing was truncated. Two independent reasons the output
could not answer the question being asked of it.

A partial source is `TaskStop`. Calling it with any unknown id returns an error that enumerates
teammates — but see the correction below, it lists REGISTERED teammates, not live ones:

```
TaskStop(task_id="definitely-not-a-task")
# -> No task found with ID: ... Running teammates: worker-a@session-x, reviewer-b@session-x, ...
```

That list included three subagents `ListAgents` had shown nothing about. (Passing a real monitor id
that has already self-terminated produces the same enumeration, which is a harmless way to ask.)

Rules:
- Never infer "the slot is free" from an agent NOT appearing in a listing. Confirm completion
  positively — the agent's own report-back is the only reliable signal.

**CORRECTION (same session, a few hours later): `TaskStop`'s enumeration is NOT a liveness check.**
It lists every teammate ever registered in the session, including ones that have explicitly said
"Exiting" and ones whose work merged long ago. A worker that had gone unreachable — three grant
messages, zero response, zero processes in its worktree for thirty minutes — still appeared in that
list as a "running teammate." Reading it as proof of life is the *same* error as reading
`ListAgents` absence as proof of death, just with the sign flipped.

What actually establishes liveness, in descending order of trust:
1. The agent sends you a message, or acts (a commit, a pushed branch, a new log file).
2. A **worktree-scoped** process check shows work attributable to its branch (see the process
   attribution entry in this file — an unscoped `ps` cannot attribute a run to a worktree).
3. Nothing else. Neither listing answers the question.
- A monitor that watched a now-merged PR self-terminates when its command exits; `TaskStop` on it
  returning "No task found" means it ended normally, not that the id was wrong.
- To free a slot deliberately, message the worker to stand down and exit. An idle worker awaiting a
  coordinator verdict still holds its name and transcript.

This is the same failure this session kept producing in other forms: **a check credited with an
answer it was never positioned to give.** Here the tell was printed in the output itself — the
truncation notice — and read past anyway.

## A process-table watch cannot attribute a test run to a worktree unless you filter by path

Symptom: the coordinator grants the serialized full-gate socket to worker A, arms a monitor that
watches for pytest processes, and is told `[gate] STARTED` then `[gate] ENDED` six minutes later.
Six minutes is impossible for a ~16-minute suite. The run belonged to worker B in a different
worktree, doing its own permitted targeted pass. Worker A's gate had not started at all.

The broken watch — no worktree filter, so it reports *any* pytest anywhere on the box:

```bash
ps -eo args --no-headers | grep -E '(python[0-9.]*|uv)[^|]*pytest' | grep -vc 'zsh -c'
```

The fix is to scope by the worktree path, which appears in the interpreter/venv path of the
running process:

```bash
WT=/path/to/.worktrees/parallel-agents/<id>
ps -eo args --no-headers | grep -F "$WT" | grep -E 'pytest' | grep -vc 'zsh -c'
```

Two further traps in the same monitor:

- **Do not report "the newest file in the log dir" as the gate's log.** `ls -t | head -1` returned a
  26MB `frontend-full.log` written by an unrelated frontend run, which reads as though the backend
  gate produced it. Name the expected log path up front and report *that* file, or report that it
  does not exist.
- **Duration is a free correctness check.** A full suite that "finishes" in a third of its known
  wall-clock did not finish. Compare against the known runtime before believing an END event.

Corroborate an END with artifacts the run itself must produce — the named log, the captured exit
code file, and a `100%` line — never with the disappearance of a process you did not prove was the
right one.

Same root as the rest of this file: **a check credited with an answer it was never positioned to
give.** A global `ps` scan cannot answer "did *this branch's* gate run," and no amount of care in
reading its output recovers information the probe never captured.

## `ps -eo … -p <pid>` does not check one PID — `-e` silently overrides `-p`

Symptom: a liveness check written as

```bash
ps -eo pid,etime --no-headers -p 1189911     # WRONG
```

prints the entire process table. `-e` (select all) and `-p` (select these PIDs) are
mutually exclusive selectors, and `-e` wins without a warning. If the output is piped
into `grep -q .` or `head`, it looks like a successful "yes, alive" answer for whatever
PID you asked about — including after the process has exited.

Correct forms:

```bash
ps -p 1189911 -o pid=,etime=                            # one PID, no header
ps -eo pid,args --no-headers | grep -F "$WORKTREE_PATH" # all procs, then filter
```

Pick one selector. Use `-p` when you have a PID; use `-e` plus an explicit filter when
you are searching. Never both.

This is the same failure mode as the `ListAgents` and global-`ps` entries above, in a
third costume: **a check credited with an answer it was never positioned to give.** The
command ran, exited 0, and produced output — none of which was about the PID in question.
Before trusting a probe, ask what it would print if the thing you are testing for were
false. If the answer is "the same kind of output", it is not a probe.

## Killing another agent's test run: verify tree identity first, then it is correct

A worker relaunching a gate on a tree you have already proven red is a strictly dominated
run: it consumes a serialized resource for ~20 minutes to reproduce a result you hold.
Killing it is correct resource management, **but only after** establishing the trees are
genuinely identical. Cheap and conclusive:

```bash
git rev-parse HEAD; git rev-parse HEAD^{tree}; git status --porcelain | wc -l
grep -n '<the specific stale token>' <the file your diagnosis names>
```

Commit SHA alone is not enough (uncommitted edits), and `dirty=0` alone is not enough
(the worker may have committed a fix). Check HEAD, tree hash, dirty count, **and** that
the exact defect you diagnosed is still present. If the worker already fixed it, the run
is valid — let it finish.

When you do kill it: message the worker first with the PID, then kill, then message again
confirming you did it and why. A test run vanishing with no explanation reads as
infrastructure failure and the worker will waste a cycle re-running it.

Scope the kill to the worktree, never globally:

```bash
PIDS=$(ps -eo pid,args --no-headers | grep -F "$WT" | grep -E 'bin/pytest' | grep -v 'zsh -c' | awk '{print $1}')
```

## The serialized gate covers targeted runs of globally-locking tests

"One full gate at a time" is usually read as "targeted runs are small, run them whenever."
That is wrong for any test that contends on a cross-process lock — in this repo,
`test_core_chain_serializes_global_runtime_attention_{install,downgrade_and_reapply}_across_processes`.
Those tests exist precisely to exercise global serialization, so a concurrent run against
the same Postgres can fail them for reasons unrelated to the code under test, and the
failure lands in *someone else's* gate where it is maximally confusing to diagnose.

Grant the socket for targeted runs of such tests too, and tell the worker to report when
it releases it.

## Head-pinned assertions make every new migration look like a regression

When a gate fails only on migration-chain tests right after a bead adds a migration, check
for `AssertionError: assert 'core_NNN' == 'core_<older>'` before assuming the migration is
broken. Tests that run `command.upgrade(cfg, "core@head")` and compare the stamped
`alembic_version` against a hardcoded literal break on every head move, however correct the
new revision is.

Decisive check, and it is cheap: run the failing test files against clean `main`. Pass on
main plus a literal-mismatch assertion means the head moved and the fix is a literal bump
in the migration bead's own scope, not a logic defect. Note the standalone-vs-full-suite
caveat: a subset run is not the same condition as the full gate, so this is only conclusive
when the assertion is deterministic (a string compare), not for order- or concurrency-
sensitive failures.

Also warn the *next* migration bead in the queue: it will hit the identical failures at the
next number, and it will waste a full gate cycle discovering that independently.

## A process-pattern kill will kill your own monitor — bracket the pattern

A worktree-scoped monitor's argv contains both the worktree path and the literal it greps
for. So a cleanup loop like

```bash
ps -eo pid,args --no-headers | grep -F "$WT" | grep -E 'bin/pytest|zsh -c cd' | awk '{print $1}' | xargs kill
```

matches the monitor process itself and silently kills it. The symptom is a monitor task
reported as "script failed (exit 1)" at the exact moment you killed a test run, and then no
further gate notifications — so the next run finishes unobserved.

Two fixes, use both:
- In the **monitor**, bracket the pattern so its own argv cannot match it:
  `grep -c '[b]in/pytest'` instead of `grep -c 'bin/pytest'`.
- In the **kill loop**, match only real interpreter invocations and exclude shell wrappers:
  `grep -E '/(python[0-9.]*|pytest)$|bin/pytest ' | grep -v 'zsh -c'`.

Same root cause as the `[p]ytest` self-match note: the harness wraps commands in `zsh -c`,
so every pattern you search for is also present in some process's command line. Always ask
what else on the box contains the string you are matching before you pipe it into `kill`.

## A worker that keeps relaunching a doomed gate: take the edit, not the bead

If a worker relaunches the same failing gate more than twice while your fix instructions sit
unapplied, stop sending instructions. Each cycle costs a full serialized gate (~20 min) and
the queue behind it pays. Apply the minimal fix yourself in the worker's worktree, verify it
with the cheap targeted run, and hand back a green tree.

Keep the bead with the worker. Leave the change **uncommitted** so the worker reviews and
commits it under its own authorship, and say explicitly that it is not losing the bead or
the credit. Tell it exactly what you changed, which sites you deliberately did not change,
and why — a worker that cannot see your reasoning cannot catch your mistake.

Note the ordering trap when handing back a dirty tree: instruct **commit first, then gate.**
A gate log stamped against a dirty tree records a tree hash matching no commit, so it cannot
attest whatever eventually gets pushed.

## "I sent it" is not "they received it" — verify before judging a worker

Symptom: a worker repeatedly fails to act on instructions, relaunches doomed work, or claims
a resource you already granted. It looks like insubordination or a dead agent. It is very
often neither: coordinator<->worker messages can cross, batch, or arrive tens of minutes late.

Observed 2026-08-22: three gate grants (01:29Z, 01:47Z, 01:55Z) and three detailed fix
instructions (02:27Z, 02:29Z, 02:36Z) never reached the worker in time. The worker meanwhile
sent messages saying it had waited 40 minutes with no grant, had deliberately NOT jumped the
queue, and had watched "a second worker" hold the socket for 19 minutes -- that "second
worker" was the coordinator running the gate on the worker's own branch. Both sides were
behaving correctly and each had a coherent, wrong model of the other.

The failure mode is this file's recurring one in its most expensive costume: **a check
credited with an answer it was never positioned to give.** `SendMessage` returning
`{"success": true, "message": "Message sent to <worker>'s inbox"}` confirms enqueue, not
delivery, and certainly not that the worker read and acted on it. Do not build a judgement of
a worker's competence on top of it.

Before concluding a worker is ignoring you:
- Look for evidence it acted on ANY earlier message of yours (it may be running an older
  instruction correctly).
- Check the worktree for what it actually did, not for what it should have done.
- Consider that your own actions are visible to it only as unexplained side effects. A
  coordinator that runs a gate on a worker's branch, kills its processes, or edits its
  worktree is generating events the worker must explain with no information.

When you do intervene in a worker's worktree, assume it did NOT get your explanation, and
lead your next message with the destructive-reflex warning rather than the reasoning. A
worker returning to an unexplained `dirty=2` will reach for `git checkout .` and wipe the fix
you just made for it. Put "do not clean the tree" in the first line; put the diagnosis below.

And when the crossed wires resolve, withdraw the accusation explicitly. A worker told it was
ignoring instructions it never received will otherwise carry that into its next report.

## `git diff origin/main..HEAD` over-reports a worker branch — use three-dot

Symptom: a PR body or worker report quotes a diff stat far larger than the actual change,
with a huge deletion count and files nobody on the bead touched.

Cause: once `origin/main` advances past your branch point, two-dot `origin/main..HEAD`
compares the two *tips*, so everything merged into main since your branch point shows up
inverted, as deletions you appear to be making.

Measured 2026-08-22 on two live branches whose merge-base was `a6bdef571`:

| branch | `origin/main..HEAD` (two-dot) | `origin/main...HEAD` (three-dot) |
|---|---|---|
| bu-iph56 | 36 files, +2806/-4407 | 20 files, +1261/-366 |
| bu-6jv4m.3 | 44 files, +4286/-4117 | 29 files, +2741/-76 |

Use three-dot for diffs, which compares against the merge-base:

```bash
git diff --stat origin/main...HEAD     # the change this branch actually makes
git merge-base origin/main HEAD        # print the base if you want it explicit
```

Note the asymmetry that makes this easy to get wrong: for **commit ranges**, two-dot is the
correct form (`git log origin/main..HEAD`, and `session_link_guard.py --commit-range
origin/main..HEAD`) and lists exactly your commits. Only the **diff** needs three dots. So a
report can have a correct commit count sitting next to a wildly inflated diff stat, which is
why the inflation survives review.

Equivalent and arguably clearer: pin the base commit by name, `git diff --stat <base>..HEAD`.
That is immune to main advancing, and it makes the base auditable by a reader instead of
implicit.

## A worker's evidence claims are claims, not evidence

A worker reported a mutation check to the coordinator as completed fact ("reverting the
projection call sites makes 2 tests fail"), then on a later self-review could find no record
of ever having run it, and withdrew the sentence from its PR body.

Two lessons, and the second is the coordinator's:
- The worker did the right thing. Re-deriving your own claims at final HEAD, and deleting the
  ones you cannot source, is exactly the reviewer pass worth asking for. Ask for it explicitly:
  "re-read the diff as the reviewer who must justify shipping it, and check every claim is one
  you verified today at this HEAD, not one that was true earlier in the bead."
- But a coordinator that ruled on the strength of that report ruled on something unverified.
  For evidence that gates a merge - especially a security or privacy boundary - require the
  artifact (a log path, a captured count), not the assertion. "The test exists" and "the test
  fails when I break the thing it guards" are different claims, and only the second one is
  worth anything on a boundary change. Make the mutation check required, not optional.

## `ListAgents` does not tell you how many worker slots are free

A coordinator running "N workers, start a new one when one completes" needs to know how many
lanes are in flight. `ListAgents` looks like the way to check it. It is not.

What it actually answers is "which agents can I address right now." Completed in-process
subagents drop off that listing while the work they were doing is still very much open - an
unmerged branch, unpushed commits, a bead in `in_progress`. After a compaction the listing can
come back with no subagent rows at all, showing only peer sessions. Read as a slot count, that
says "0 workers, dispatch N more," and the coordinator double-dispatches beads that already
have a branch with work on them.

Track lanes by the durable artifact, not the agent roster:

```bash
bd list --status=in_progress --json | jq -r '.[] | "\(.id)\t\(.assignee // "-")"'
git worktree list                       # a worktree that exists is a lane that exists
git -C <worktree> log --oneline origin/main..HEAD | wc -l   # unmerged work in that lane
```

A lane is free when its bead is closed and its worktree is removed. Nothing else frees it.

Related trap in the same tool: `SendMessage` returning `success: true` to a worker name means
the name resolved and the message was enqueued. Because a send to a completed agent resumes it
from its transcript, a resolving name is not evidence the agent was alive when you sent it, and
`success: true` is never evidence it was read. If the instruction matters, wait for the worker
to say something that could only be true if it had read it.

This is the recurring shape behind most of the entries in this file: **a check credited with an
answer it was never positioned to give.** `ps -e … -p PID` credited as "is this PID alive."
"HEAD changed" credited as "the worker acted." `success: true` credited as "the worker read it."
`ListAgents` credited as "this many lanes are free." A green targeted test run credited as "the
gate is green." Before trusting a signal, say out loud what it literally measures, then check
that against the question you are asking it. They are different more often than they look.

## A green local suite is not a green CI job (gate ordering)

A worker reported the full frontend suite green locally, with a real log and real counts. CI's
`frontend` job then failed - and the suite it had run was not the thing that failed.

The job runs its steps in order, and a lint-class gate sat before the tests:

    lint -> em-dash gate -> query-coercion gate -> knip -> build -> test

knip failed, so build and test were marked **skipped**. The tests never executed on CI. The
worker's local green was not wrong, it was irrelevant: it measured a step CI never reached.

Two things follow, and the second is the general one:

- When a CI job fails, check WHICH STEP failed before believing any narrative about the tests.
  `gh api repos/:owner/:repo/actions/jobs/<id> --jq '.steps[] | "\(.number). \(.name): \(.conclusion // .status)"'`
  gives the per-step verdict immediately, and works while the run is still in progress - unlike
  `gh run view --log`, which refuses until the whole run completes.
- A verification list assembled from memory omits exactly the gate nobody remembers. Build the
  list by reading the job definition in the workflow file and running what IT runs. "I ran the
  tests and they passed" answers a different question from "will this job go green."

Same shape as everything else in this file: a check credited with an answer it was not positioned
to give. A passing test suite cannot speak for a gate that runs before it.

## `openspec validate --strict` cannot see two changes overwriting one requirement

Seen 2026-08-22 on bu-97nlt (butlers repo, openspec 1.9.0), where the bead's own premise was half
wrong in an instructive way.

`openspec archive` writes the **whole** requirement into the baseline spec. So two unarchived
changes that each carry a `## MODIFIED Requirements` block for the same `### Requirement: X`,
authored against different ancestors, will silently destroy each other: whichever archives second
deletes everything the first added. Neither change is malformed. Both are individually correct
against the ancestor they were written on.

The bead asserted that `openspec validate --strict` passed on both changes and could not detect the
collision. The second half was right; the first half was not, and the reason matters.

openspec 1.9.0 has a `findMissingCurrentScenarios` guard
(`dist/core/parsers/requirement-blocks.js:269`, shared by `validate` and `archive`) that refuses a
MODIFIED block dropping scenario **names** the baseline still has. One of the two changes had
*renamed* three scenarios, so it was already failing validation and already unarchivable. The other
had kept every baseline scenario name and rewritten the bodies inside them, so it validated clean
while overwriting.

That is the whole trap in one sentence: **the guard fires on the harmless case and stays silent on
the destructive one.** A rename is loud. A body rewrite under an unchanged name is invisible.

What to do, as coordinator, before letting any worker archive an OpenSpec change:

```bash
# who else holds a block on this requirement?
rg -l '^### Requirement: <Name>$' openspec/changes/*/specs/*/spec.md
```

If two or more hit, the order is: archive one, then **rebuild** the other's block against the
refreshed baseline before archiving it. Rebuilding means starting from the new baseline body and
re-applying only that change's own edits, then diffing the rebuilt block against the baseline to
prove nothing else moved. Prefer to rebuild whoever holds the smaller, newer delta.

Two second-order effects that cost real time here:

- **Archiving arms the bug for everyone else.** Writing a new body into the baseline makes every
  remaining unarchived block on that spec stale. Re-run the grep after each archive. On this bead
  the first archive armed a third instance in a change nobody was working on; the worker rebuilt
  that block (without archiving it) precisely because its own archive is what armed it. That scope
  extension was correct and should be approved, not fenced off: leaving it stale ships the very bug
  the work exists to fix.
- **Do not accept strict validation as the acceptance evidence.** It passed on the overwriting side
  of every instance. The real evidence is a marker grep on the post-archive baseline proving both
  content sets survived, plus the rebuilt-block diff. A good worker will say this itself; if the
  report cites `validate --strict` as proof the collision is resolved, send it back.

Also worth knowing: comparing repo-wide validation before and after must compare the failing-item
**sets**, not the counts. Counts move for boring reasons (archived changes leave the item list
entirely, so both `passed` and the total drop). `comm -13` on the sorted failing-item lists is what
answers "did I introduce a new failure."

Same shape as everything else in this file: **a check credited with an answer it was never
positioned to give.** Scenario-name equality was credited as requirement-body equality. The
validator was doing exactly its job; the job was just narrower than the question being asked of it.

## `bd search` matches TITLE ONLY — a zero-hit result is not evidence of absence

`bd search <term>` (bd 1.0.4, Dolt server mode) indexes the **title** field and nothing
else. Description, acceptance, design and notes are NOT searched.

Demonstrated on 2026-08-22:

```bash
bd search truthful   # -> bu-0uqgo.6, bu-27dxl.7, bu-kqnum.8.7   (title word: hits)
bd search reissue    # -> (empty)
```

...yet `reissue` appears **4 times** inside bu-0uqgo.6, in its description/acceptance/
design/notes. The empty result was wrong in the way that matters: it looked like proof
that no bead owned a deferred piece of work, and would have led to filing a duplicate
bead for work that was already assigned.

**Search the body fields explicitly instead:**

```bash
bd show <id> --json | jq -r '.[0] | [.title,.description,.acceptance,.design,.notes]
                                    | map(. // "") | join("\n")' | grep -ic '<term>'
```

For a corpus-wide sweep, iterate ids from `bd list --json` through that same jq
projection — there is no server-side body search to lean on.

**Two traps that compound this:**

1. `bd show --json` returns an **array**; you must index `[0]`. A bare `.description`
   yields `null`, which greps as empty and looks like another clean miss.
2. Piping into `grep ... | head -N` makes `||` fallbacks useless: `head` exits 0, so
   `grep -q ... || echo "NOT FOUND"` never fires and a genuine miss prints nothing at
   all — indistinguishable from a hit whose output scrolled. Use `grep -c` and read the
   number, or drop the `head`.

General rule this is an instance of: **a search is only evidence about the fields it
indexes.** Title-level absence was credited with an answer only a body-level scan could
give — the same shape as `openspec validate --strict` being credited with a
requirement-body comparison it never performs.

## `emit_worker_report.py` cannot express "complete, verified, deliberately unpushed"

A coordinator that forbids workers from pushing (sole-merge-authority setups) creates a
state the report schema has no status for. Verified against the script's validation:

| status | blocking requirement |
|---|---|
| `completed-pr-opened` | `branch_pushed == "yes"` (line ~112) |
| `completed-direct-merge-candidate` | `branch_pushed == "yes"` (line ~123) |
| `blocked-awaiting-coordinator` | `--failing-command` must not be `n/a` (line ~136) |

So a worker that finished cleanly, gated green, and was *instructed* not to push has no
truthful option: the two `completed-*` statuses require a push it was told not to do, and
`blocked-*` requires a failure that does not exist.

**Do not have the worker pick the least-wrong flag.** `--branch-pushed yes` for an unpushed
branch puts a false fact into the machine-readable record, which is worse than no record —
downstream tooling reads that field to decide whether a remote ref exists. The coordinator
already holds the full handoff in the message; accept the prose handoff as the report of
record and tell the worker explicitly not to emit. Then push and open the PR yourself.

Same shape as the rest of this file: *a check credited with an answer it was never
positioned to give.* Here the schema asks "was it pushed?" as a proxy for "is it done?",
and the two come apart the moment the coordinator owns the push.

## Read-modify-write on `--notes`/`--description` silently DESTROYS the field

Appending to a bead field by round-tripping it through a command substitution is a
data-loss pattern, not a convenience:

```bash
# DESTRUCTIVE — do not copy
bd update <id> --notes "$(bd show <id> --json | jq -r '.notes // ""')
my new note"
```

`bd show --json` returns an **array**, so `.notes` raises `jq: error (at <stdin>:N):
Cannot index array with string "notes"`. jq writes the error to stderr and **nothing to
stdout**. The `$(...)` therefore expands to just the new note, `bd update` succeeds, and
every pre-existing note is gone. Observed 2026-08-23 on `bu-e4r0h`: two accumulated notes
(one of them a worker's measured finding) were overwritten by a single-line addition, and
the only reason they were recoverable is that a `bd show` earlier in the same session had
already printed them.

The `// ""` fallback does **not** protect you — it only fires on `null`, and this is an
error, not a null. Neither does `set -e` in the usual case: the failure is inside a command
substitution whose exit status the outer command discards.

**Do this instead** — build the full new value in a file, prove it is non-empty, then write:

```bash
N=/tmp/.../notes.md
bd show <id> --json | jq -r '.[0].notes // ""' > "$N"   # note the [0]
test -s "$N" || { echo "REFUSING: existing notes read as empty"; exit 1; }
printf '\n\n%s\n' "$new_note" >> "$N"
bd update <id> --notes "$(cat "$N")"
bd show <id> --json | jq -r '.[0].notes' | head -c 200   # verify the old text survived
```

The `test -s` guard is the load-bearing line: it converts "the read silently failed" from a
destructive write into a refusal. Apply the same guard to `--description`, `--design`, and
`--acceptance`; all four are whole-field replacements with no append mode.

Same shape as the rest of this file: *a check credited with an answer it was never
positioned to give.* Here `bd update` returning `✓ Updated issue` was read as confirmation
the append worked, when it only ever confirmed that a write happened — it cannot know the
value it wrote was a truncation.

## `idle_notification (available)` from an IMPLEMENTATION worker does not mean the bead is done

**Symptom:** a dispatched `beads-worker` emits
`{"type":"idle_notification","idleReason":"available"}` while its worktree holds
uncommitted work and no PR exists. The obvious reading — "it finished, reclaim
the slot" — is wrong. Observed 2026-08-24 (worker-bu-zbybd-2 on bu-kww1r): it
idled, then wrote three more times over the next 20 minutes and added an entire
new test.

**Distinguish this from the two entries above.** The reviewer case is an agent
that will never answer; the usage-limit case is an agent that is dead. This one
is an agent that is *between turns* and resumes on the next message. Treating it
as done and committing its tree mid-flight captures a partial change and can race
its next write.

**Why the obvious probes fail.** Both of these are checks credited with an answer
they were never positioned to give:

- `find <worktree> -newermt '-90 minutes'` returning nothing does NOT mean stopped.
  A worker surveying a large corpus reads for a long time before it writes.
- `git status --porcelain | wc -l` holding steady does NOT mean idle — a worker
  editing files it already touched keeps the count constant.
- Absence from `ListAgents` proves nothing when the listing truncates
  ("sessions beyond the first pages are missing").

**What actually discriminates:** send the worker a message. A send both tests
liveness (it fails against a dead agent) and *resumes* an idle one, so the probe
and the fix are the same action. Word it so it cannot manufacture pressure —
"take the time the work needs; I'd rather wait than have you write early to look
busy" — because a worker that reads a liveness ping as a deadline will commit a
half-finished tree to look responsive.

**Then re-check mtimes AFTER the ping**, not before: `git status --porcelain |
awk '{print $2}' | xargs stat -c '%y %n' | sort -r | head`. Fresh timestamps
after the send are the positive evidence; the pre-send silence never was.

## `${PIPESTATUS[0]}` is empty in zsh — exit-code probes silently report nothing

**Symptom:** a gate one-liner ending
`cmd | tail -5; echo "EXIT=${PIPESTATUS[0]}"` prints a bare `EXIT=` and you read
the success-sounding text above it as green. zsh spells it `$pipestatus`
(lowercase, and `$pipestatus[1]` is 1-indexed); `PIPESTATUS` is a bash-ism that
expands to nothing. This session's shell is zsh.

**Why it bites here specifically:** several repo guards print advisory banners
that read like failures but exit 0 — `check_spec_overwrites.py` emits
"Ratchet can be tightened ... Run: --update-baseline" while succeeding, since
only `regressions` gates its exit. With no usable exit code you are left grading
the guard on its prose, which is exactly backwards.

**Do this instead** — redirect, capture `$?` directly, then read the log:

```bash
timeout 600 uv run python scripts/some_guard.py >/tmp/g.log 2>&1; echo "EXIT=$?"
tail -20 /tmp/g.log
```

Never infer a guard's verdict from its output text when you can have its status.

## `find -newermt '-15 minutes'` silently returns nothing (bfs, not GNU find)

**Symptom.** A worker-liveness probe like

    find <worktree> -newermt '-15 minutes' -not -path '*/.git/*' -type f 2>/dev/null | wc -l

prints `0`, and you conclude the worktree is quiet — worker done, or dead.

**Cause.** On this box `find` resolves to **bfs**, not GNU findutils. bfs's `-newermt`
accepts only ISO-8601-like timestamps and rejects every relative form:

    bfs: error: ... -newermt "-15 minutes" ...
    bfs: error: Invalid timestamp.

Both `'-15 minutes'` and `'15 minutes ago'` fail. With stderr sent to `/dev/null`, the
error is invisible and `wc -l` reports a perfectly clean `0`.

**Fix.** Pass an absolute timestamp:

    TS=$(date -d '15 minutes ago' -Iseconds)
    find <worktree> -newermt "$TS" -not -path '*/.git/*' -type f | wc -l

**Why it matters more than it looks.** This is a coordinator's primary liveness
instrument, and broken it always reports "idle". Measured 2026-08-24: two worktrees
reporting `0` under the broken form showed **77** and **804** files written in the same
window under the fixed form. Both workers were mid-run. Every "no writes in N minutes"
conclusion drawn from the broken form is void.

**The general rule.** Never let a probe's stderr go to `/dev/null` when you intend to
read its *empty output* as evidence. Absence of output and failure to run produce the
identical observation. If a zero would change your decision, prove the command ran.

**And even when it works, it does not answer the question.** A quiet worktree cannot
distinguish "finished" from "reading and thinking" from "died". The only probe that
discriminates is `SendMessage` — it tests liveness and resumes an idle agent in one
move. Use mtimes to corroborate a report, never to replace one.

## `git show HEAD:<path>` compares against a HEAD that can move under you

**Symptom.** You inspect a dispatched worker's tree, find a forbidden file staged,
compare it to HEAD to gauge the damage, and get `IDENTICAL-CONTENT` — so you record it
as benign. It was not benign: the file carried a real 32-line change.

**Cause.** The worker committed between your `git status` and your `git show HEAD:`.
HEAD is now the worker's new commit, so you compared the file against a snapshot that
already contains the change you were trying to detect. It matches itself.

**Fix.** In another agent's live worktree, pin the comparison to an immutable ref — the
dispatch base SHA you handed out, or an explicit `git rev-parse HEAD` captured up front —
never the symbolic `HEAD`:

    BASE=<dispatch-base-sha>
    git -C <worktree> diff $BASE..HEAD -- <path>

Same reason `git status --porcelain` going empty mid-inspection means "it just got
committed", not "the changes vanished".

## Two ways a grep silently fails toward "nothing here"

Both surfaced 2026-08-24 in a single worker report whose conclusion — "no test references
this, therefore I ran no tests" — was false. The file existed and its 15 tests passed. Two
independent filters each excluded exactly the file that mattered, and neither failure was
visible in its own output.

**1. `head` on a search that feeds a negative claim.** The broad grep DID find the file. It
returned 44 matches, piped through `head -20`, and the one that mattered was line 44 of 44.
`head` truncates silently, so a truncated result and a complete one are the same observation.

    # wrong, when the conclusion is "there is no such file"
    grep -rl "$pat" tests/ | head -20
    # right
    grep -rl "$pat" tests/ | wc -l        # count first, then look

Never bound a search whose *emptiness or completeness* is the evidence. Bounding output is
for reading convenience; the moment a negative conclusion rests on it, the bound is a lie.

**2. Hyphen/underscore mismatch on an identifier.** The narrowing grep searched
`spec-overwrite` (the spelling used in prose and filenames) while the code identifier is
`check_spec_overwrites`. A hyphenated pattern structurally cannot match the underscored
form — the search was incapable of succeeding regardless of what was in the tree. A
zero-match grep is indistinguishable from a genuine absence.

    grep -rl 'spec[-_]overwrite' tests/     # match both separators

**The rule both share.** Before writing "and therefore X does not exist", ask what this
command would have printed if X *did* exist. If the answer is "the same thing", the search
proved nothing. That clause is load-bearing in any completion report and deserves a search
built to fail loudly.

## `merge_pr_exact_base.py` reports `postmerge-patch-drift` on any branch that is behind main

The helper compares the **tree of the reviewed head commit** against the **tree of the
landed merge commit** (`scripts/merge_pr_exact_base.py:413-414`, equality at `:432`).
Those two trees are equal only when the branch was already rebased onto the exact
current `main`. If `main` moved at all between the PR's base and the merge — including
because *you* merged something else five minutes earlier — the trees differ by exactly
that other change, the outcome is `postmerge-patch-drift`, and
`source_bead_closure_allowed` comes back `false`. AGENTS.md:421 then forbids closing
the source bead.

GitHub reporting `mergeStateStatus: CLEAN` with every check `SUCCESS` does **not**
protect you. CLEAN means "no conflicts", not "no base movement". #3800 was CLEAN, all
six checks green, and still landed as drift because #3794 had merged 40 minutes earlier.

The damage is unrecoverable after the fact: tree equality is structurally unattainable
for a behind-main branch, so no post-merge audit can produce the specific proof the
doctrine asks for. You can prove the *content* is right —

```
git fetch -q origin main
git diff <prior-main>..<merge-sha> > /tmp/landed.diff
gh pr diff <n> > /tmp/reviewed.diff
diff <(grep -E '^[+-]' /tmp/landed.diff   | grep -v '^[+-][+-][+-]') \
     <(grep -E '^[+-]' /tmp/reviewed.diff | grep -v '^[+-][+-][+-]')
```

— and that audit is worth running and recording, but it does not restore the bead's
eligibility to close. The bead stays open on an owner call about doctrine.

**Always rebase onto current `origin/main` and let CI re-run before invoking the
helper.** The corollary is the expensive part: merges are strictly serial. Every merge
moves `main` and invalidates every other open branch's exact base, so a five-PR train
is five sequential rebase→CI→merge cycles, not one parallel batch. Backend `check`
alone is 20-40 minutes. Plan the train as serial from the start rather than discovering
it after the first drift.

A useful side effect of rebasing: it is also how you find out a required check has
never actually fired on a branch. Compare the check list before and after the push.

## `bd list --json` silently truncates to 50

`bd list --status=open --json` returned 50 rows on a repo with 141 open issues. There is
no warning, no `has_more`, no non-zero exit. A `jq select(...)` over that output for beads
created seconds earlier found nothing, and the obvious reading — "the creates failed" —
was wrong: all seven existed.

```
bd list --status=open --json | jq 'length'                 # 50
bd list --status=open --limit 5000 --json | jq 'length'     # 141
```

Two fixes, both cheap: pass `--limit 5000` when you intend to filter the output, or
confirm existence with `bd search "<distinctive title text>" --json`, which is unaffected
by the cap. Prefer `bd search` for "does this bead exist" questions and reserve `bd list`
for browsing.

The trap is not the cap, it is that the cap is invisible in the direction of "nothing
here". Before reading an empty `jq` result as absence, ask what the command would have
printed if the row DID exist but sat at position 51.

## An empty `statusCheckRollup` reads as "all checks passed"

Immediately after a force-push, GitHub detaches the previous run's checks from the
old head and has not yet created the new ones. For a window of roughly a minute
`gh pr view --json statusCheckRollup` returns an **empty array**, and
`mergeStateStatus` reports `UNKNOWN`.

A watcher that decides "terminal" by counting the checks that are still pending
gets zero, and zero pending is the same answer it would get if every check had
passed. Observed live: a watcher on a freshly force-pushed PR exited after one
poll with `TERMINAL #3804: OPEN UNKNOWN` and an empty check list, roughly 35
minutes before that branch's CI actually finished.

The fix is to require positive evidence rather than the absence of negative
evidence:

- assert a **minimum check count** (this repo runs 7: session-link-guard,
  em-dash-guard, spec-overwrite-guard, archived-requirements-guard, check,
  frontend, frontend-e2e),
- require every check to carry a non-empty `conclusion`,
- refuse to call it terminal while `mergeStateStatus` is `UNKNOWN`,
- and confirm the same verdict **twice, 60s apart**, so a gap between two runs
  cannot win.

Note `gh` returns `""` for a pending step's conclusion, not `null`, so
`select(.conclusion != null)` counts pending steps as finished. Compare against
`""` as well: `select((.conclusion // "") == "")`.

Generalisation, the same one that keeps recurring: before a probe's empty result
is allowed to mean "nothing left to do", ask what it would have printed if the
work had not started yet. If that is also empty, the probe cannot tell the two
apart.

## Mutation-test a boundary predicate rule by rule, not wholesale

A predicate assembled from several independent rules can contain a rule that nothing
pins. Deleting the whole predicate fails loudly, so wholesale mutation reports full
coverage — and the dead rule stays invisible.

Measured on bu-hlbj6, a security-boundary test with a four-rule predicate. Deleting the
predicate entirely: 11 failures. Deleting three of the four rules individually: 1, 3, and
3 failures respectively. Deleting the fourth: **27 passed, zero failures.** Nothing in the
suite pinned it, so any later cleanup could have removed it without a signal, silently
reopening the shapes only it rejected.

The fix is per-rule fixtures: for each rule, a case that ONLY that rule catches. After
adding two, the previously-dead rule's mutation failed 2 tests.

Two habits worth carrying: revert each mutation and confirm `git status --porcelain` is
empty before the next one, and when you narrow a ban, write down the shapes you no longer
catch. A narrowed boundary whose gaps are undocumented is worse than the blunt one it
replaced, because it reads as precise.

## `pkill -f <pattern>` kills the shell that invoked it

`pkill -f` matches against the **full command line** of every process. It excludes
its own PID, but not its parent shell — and the parent shell's command line
contains the pattern string, because the pattern was typed there.

Observed live: a background job was launched as

```bash
pkill -f "watch-pr.sh 3804"     # retire the previous watcher
until <condition>; do sleep 60; done   # then wait for CI
```

Both the old watcher **and the new background job** died instantly with exit 144.
The new job's own `/bin/bash -c '...'` command line contains the literal text
`watch-pr.sh 3804`, so `pkill -f` matched it and killed the process it was
running inside. The `until` loop never executed a single iteration.

Two fixes:

- Do not combine the kill and the replacement in one invocation. Retire the old
  watcher in its own foreground call, then start the replacement separately.
- Or make the pattern unmatchable by its own command line — a bracket class
  breaks the literal: `pkill -f "watch-pr[.]sh 3804"` still matches the target
  but not the shell that typed it (the same `[x]` trick used with `ps | grep`).

Prefer targeting by PID recorded at launch (`echo $!`) over pattern matching
whenever the launcher is under your control.

Generalisation: the pattern here is the same one this file keeps recording — a
command credited with a scope it was never positioned to have. `pkill -f` was
read as "kill the watcher"; what it actually does is "kill everything whose
command line contains this text", and the caller is one of those things.

## A monitor's exit condition is not proof the job finished

A worker armed a Monitor whose until-loop was

```bash
tail -n 5 "$LOG" | grep -qE "passed|failed|error"
```

and then printed `tail -n 4 "$LOG"` as the gate result. It reported
`3821 passed, 62 skipped, 4 xfailed in 464.05s` as a completed full-suite run.

The run had not finished. At the moment of the claim the log tail read `[ 49%]`,
`grep -aoE '[0-9]+ (passed|failed)' "$LOG"` returned **nothing**, and the
background task's own output file was **0 bytes** because the process had never
exited. The reported number could not be attributed to any run in that worktree.

Two independent failures, both worth guarding:

1. **The exit condition matched something that was not a summary.** pytest writes
   warnings, `ERROR` lines, and captured output long before it writes its summary
   line. Any grep for `passed|failed|error` over a live log can fire mid-run.
2. **A number was carried over from an earlier command.** The monitor's echo was
   treated as a gate result rather than re-read from the log the run was routed
   to.

The rule: **key the wait on process exit, not on log content.**

```bash
# wrong -- a live log can contain the words at any moment
until tail -n 5 "$LOG" | grep -qE "passed|failed"; do sleep 45; done

# STILL WRONG -- this until-loop never exits; see below
until ! pgrep -f "$WORKTREE/.venv/bin/pytest" >/dev/null 2>&1; do sleep 45; done

# right -- bracket class stops the waiter matching its own command line
until ! pgrep -f "$WORKTREE/.venv/bin/pytes[t]" >/dev/null 2>&1; do sleep 45; done
tail -n 15 "$LOG"
```

**The remedy above was itself defective when first recorded here, and the
correction is the more important lesson.** A waiter whose own command line
contains the pattern string is matched by its own `pgrep -f`, so the condition
`pgrep succeeds` is permanently true and the loop spins forever. Observed
directly: a waiter armed on `pgrep -f "bu-qdi56/.venv/bin/pytest"` was still
running long after its pytest had exited, because the only process `pgrep` could
still see **was the waiter itself**. The real run had finished with
`14830 passed, 21 skipped in 1404.96s`; the waiter reported nothing at all.

This is the exact mechanism already documented above under
"`pgrep -f "<pattern>"` reports a finished process as still running" and under
"`pkill -f <pattern>` kills the shell that invoked it" — the same self-match, in
its third costume. Writing the fix for one costume while reintroducing the bug in
another is the failure worth remembering.

Defences, in order of preference:
- Record the PID at launch (`cmd & echo $!`) and wait on `kill -0 "$PID"`. No
  pattern, so nothing can self-match.
- Otherwise use a bracket class (`pytes[t]`), which no longer matches the literal
  string sitting in the waiter's own argv.
- Never test a `pgrep -f` liveness probe only while the target is alive: it
  succeeds either way. Test it **after** the target exits — that is the only run
  in which a self-match is visible.
- A `pytest` pattern must match **both invocation forms**. The console script is
  `<venv>/bin/pytest`, but `uv run pytest` and worker scripts often produce
  `<venv>/bin/python -m pytest`. A pattern anchored on `bin/pytes[t]` sees the
  first and is blind to the second, so it reports "nothing running" over a live
  25-minute suite. Observed directly: `pgrep -af "\.venv/bin/pytes[t]"` returned
  nothing while PID 6915 (`\.venv/bin/python -m pytest tests/ roster/ ...`) had
  been running for 19 minutes. Match on `-m pytes[t]\|bin/pytes[t]`, or avoid the
  question entirely by keying on the PID.

This is why the PID form is listed first and not merely as a stylistic
preference: **every** pattern-based liveness probe has to be right about both
self-matching and the target's exact argv spelling, and being wrong about either
one fails toward the same silent, confident "not running".

Then read the summary **out of the log file**, and sanity-check the magnitude
against a known baseline before believing it. This repo's non-e2e suite is
~14,800 tests and takes 20+ minutes; a "full suite" reporting 3,821 tests in 7:43
is a subset wearing the full suite's name. Keep a recent baseline count to hand —
it is the cheapest possible check and it catches this instantly.

Note also that when several worktrees run the byte-identical command at once,
`pgrep -f "pytest tests/"` matches all of them. Scope the pattern to the
worktree's own venv path, as above, or the wait ends when *somebody else's* run
ends.

Same generalisation as the rest of this file: a probe credited with an answer it
was never positioned to give. Here the probe was "does the log contain the word
passed", and it was read as "did the suite pass".

## A conflicting PR gets ZERO CI, and zero CI reads as "no failures"

`.github/workflows/ci.yml` in this repo triggers on:

```yaml
on:
  push:
    branches: [main]
  pull_request:
```

The `pull_request` event runs against the **merge ref** (`refs/pull/N/merge`).
GitHub cannot create that ref while the PR has conflicts, so a `CONFLICTING` PR
runs **no workflows at all** — not a failing run, not a skipped run, *nothing*.

Observed on #3798 (`agent/bu-46ci7`), open for hours:

```
#3798 OPEN UNKNOWN total=0 fail=0 pend=0
gh api repos/OWNER/REPO/commits/$SHA/check-runs --jq .total_count  ->  0
gh api repos/OWNER/REPO/commits/$SHA/status     --jq .state        ->  pending
gh pr view 3798 --json mergeable --jq .mergeable                   ->  CONFLICTING
```

Every rollup-based readiness check reports this PR as having zero failures,
because it does. It also has zero successes, and the two are indistinguishable to
any predicate that only counts failures or only counts pending items.

This is the **permanent** form of the transient empty-rollup trap recorded
earlier in this file. The transient one clears in about a minute after a
force-push; this one never clears on its own, because nothing will ever run until
a human resolves the conflict.

Practical consequences for a merge train:

- Resolve the conflict **first**, then push, and only then expect CI. A
  conflicting branch cannot be "waiting for CI" — it is waiting for you.
- Treat `total_count == 0` as a distinct state from "all green". Assert a
  **minimum check count** (7 in this repo) before calling any PR ready.
- `mergeStateStatus: UNKNOWN` plus `total=0` is the signature. Check
  `mergeable == "CONFLICTING"` before concluding anything about readiness.

Related: `migration-chain-main.yml` triggers only on `push` to `main` under
`alembic/versions/**`, `src/butlers/modules/*/migrations/**`, and
`roster/*/migrations/**` — so a PR adding an Alembic revision gets that check
only **after** it lands, never before. Do not wait for it on the PR.

## The `check` job is not a test run, and a 40-minute watcher deadline is too short

`check` in `.github/workflows/ci.yml` is a multi-stage job, not "the test suite". Its steps
run in order: lint, format check, a SQL safety check, **unit tests**, smoke tests, a disk-space
free, an integration path-coverage guard, and only then **integration tests
(testcontainers)** — which is by far the longest stage and needs a Docker container fleet
provisioned inside the runner.

Observed on #3811: `check` was still in progress at **43 minutes**, with steps 1-15 green and
step 16 (`Integration tests (testcontainers)`) running. That is normal, not wedged. A watcher
armed with a 40-minute deadline reported `DEADLINE-EXCEEDED` on a perfectly healthy run.

Budget **75+ minutes** for `check`. The other six jobs are fast and finish in well under ten
(`session-link-guard`, `em-dash-guard`, `spec-overwrite-guard`,
`archived-requirements-guard` all in ~10-20 seconds; `frontend` ~8 min; `frontend-e2e`
~3 min), so a PR sitting at `pending=1` for half an hour is the normal shape of this repo's
CI, not a signal.

To see where an in-progress job actually is — `gh run view <id> --log` returns NOTHING for a
run still in progress, which reads as "no output" and tempts a re-run. Use the jobs API for
step-level state instead:

```bash
gh api repos/<owner>/<repo>/actions/jobs/<job-id> \
  | jq -r '"status=\(.status)",(.steps[]|"  \(.number). \(.name) -> \(.status)/\(.conclusion // "-")")'
```

The job id comes from the `detailsUrl` in `gh pr view --json statusCheckRollup`.

Same generalisation as the rest of this file, aimed at myself this time: the deadline encoded
a belief about what `check` *was* ("the ~20-minute full suite"), and when the belief was wrong
the timeout reported a fault in the run rather than a fault in the belief.

## `git rebase --continue` will happily commit conflict markers

A conflict resolution done by script has two failure modes, and the obvious guard catches neither
unless it is wired as a *gate*.

```bash
# WRONG -- three separate commands; nothing stops the third
python3 - <<'PY'
...
assert ours[0] == base[0] == theirs[0]     # <-- fires; script exits 1; file NEVER rewritten
...
PY
grep -c '^<<<<<<<\|^=======\|^>>>>>>>' AGENTS.md      # prints 4 -- the alarm, ignored
git add AGENTS.md && git -c core.editor=true rebase --continue
# result: "1 file changed, 10 insertions(+)" -- ten of those lines are conflict markers,
# now permanently in a commit that git considers a successful rebase.
```

Two independent defects compound:

1. **A heredoc script that aborts leaves the file untouched, and the shell moves on.** `python3 <<PY`
   exiting non-zero is not an error the *next* command knows about. Chain it: `python3 - <<'PY' ... PY`
   followed by `&&`, or `set -e` at the top of the block.
2. **`grep -c` printing a non-zero count is not a check.** It exits 0 when it finds matches, so
   `grep -c ... ; git add ...` proceeds, and `grep -c ... && git add ...` proceeds *because* markers
   were found. The count must be captured and asserted:

```bash
# right
N=$(grep -c '^<<<<<<<\|^=======\|^>>>>>>>\|^|||||||' AGENTS.md || true)
test "$N" -eq 0 || { echo "ABORT: $N conflict markers remain"; exit 1; }
git add AGENTS.md && git -c core.editor=true rebase --continue
```

Nothing downstream saves you. `git rebase --continue` does not scan for markers. Neither do this
repo's seven CI checks — `em-dash-guard`, `session-link-guard`, `spec-overwrite-guard` and
`archived-requirements-guard` all pass on a Markdown file full of `<<<<<<< HEAD`, and `check` never
reads `AGENTS.md`. The only reason this one was caught is that a human-shaped follow-up (`grep -n`
on the committed file) was run *after* the commit; had the branch been pushed first, seven green
checks would have certified a corrupted file.

**Also: do not assert a precondition you have not measured.** The assertion that fired above
(`ours[0] == base[0]`) was itself wrong — `ours[0]` was main's *superseding* rewrite of that bullet
(2814 chars vs the base's 1627), which is exactly what a conflict in a frequently-edited notes file
looks like. Diff the three sides by length/hash and *print* them before encoding any equality into a
guard; a resolution script's assertions should describe what you have observed, not what you assume.

Belt-and-braces after any scripted resolution, before pushing:

```bash
git grep -n -e '^<<<<<<< ' -e '^>>>>>>> ' -e '^||||||| ' -- .   # empty output required
git diff $(git merge-base origin/main HEAD)..HEAD | grep -c '^+<<<<<<<\|^+=======\|^+>>>>>>>'
```

## Never edit a shell script in place while instances of it are running

`bash` does not slurp a script; it reads it incrementally and remembers a **byte offset**. A
`cat > script.sh <<'EOF'` truncates and rewrites the *same inode*, so every already-running
instance resumes at an offset that now points into different text. A watcher looping for 75
minutes will read its trailing lines long after you "improved" the file, and will execute whatever
bytes happen to sit at that offset — silently, with no error.

Observed here while three `watch-ci.sh` watchers were live: the loop body itself was already
parsed, but the post-loop `echo "DEADLINE-EXCEEDED"; exit 2` had not been, so a deadline exit would
have run garbage. The success path (`exit 0` from *inside* the loop) would have been unaffected —
which is exactly what makes this hard to notice: the bug only manifests on the failure branch.

Rules:
- Write the improved version to a **new path** (`watch-ci2.sh`) and start new runs from that.
- If you have already clobbered it, restore the **exact original bytes** so the live offsets stay
  valid, then move on to the new path.
- Where a script is genuinely long-lived, make it self-contained per iteration or launch it as
  `bash -c "$(cat script.sh)"`, which reads once into memory up front.

## With an exact-base merge gate, parallel CI on N PRs wastes N-1 runs

`scripts/merge_pr_exact_base.py` requires `--expected-base` to equal the current `origin/main`.
Every merge moves `main`, so the instant one PR lands, every other open PR's base is stale and its
just-completed 40-minute `check` run is no longer usable as merge evidence. Running CI on four PRs
concurrently does not produce four merge-ready PRs; it produces one, plus three runs that have to be
repeated after a rebase.

What the parallel runs *do* buy is **content validation** — proof that the diff itself passes, which
survives a rebase as long as the rebase is conflict-free. That is worth something for a brand-new
change and worth almost nothing for a PR that was already green on an older base.

So:

- Keep exactly **one** PR at the train head with fresh CI against the current tip.
- Treat an older green run on a stale base as content evidence only, never as merge evidence. Check
  `baseRefOid` against `origin/main` explicitly:
  `gh pr view N --json baseRefOid --jq .baseRefOid` vs `git rev-parse origin/main`.
- Give a genuinely new, large change one speculative run for the content signal; skip it for
  docs/spec-only PRs whose guards have already passed, since their `check` outcome is near-certain.
- Rebase the next head **immediately** after a merge rather than waiting for the losers' runs to
  finish; those runs are already dead and finishing them changes nothing.

Corollary for watchers: a watcher armed before a merge keeps polling the same PR number and will
happily report `TERMINAL fail=0 MERGEABLE/CLEAN` for a PR whose base is now stale. Terminal-green is
not merge-eligible. Stop and re-arm the watcher after every rebase, both so the deadline restarts
and so the vote is not carried over from the dead run.

**Observed instance (2026-08-24).** Watcher on #3798 exited 0 with
`TERMINAL fail=0 mergeable=MERGEABLE state=CLEAN` and all seven checks SUCCESS, while
`baseRefOid=1d4773f27` and `origin/main=7283128e4`. Every signal GitHub exposes said "merge this";
`merge_pr_exact_base.py` would have refused. `mergeable`/`mergeStateStatus` answer "can git merge
this?", never "is this PR's base the current tip?" — the base has to be compared explicitly:

```bash
BASE=$(gh pr view "$PR" --json baseRefOid --jq .baseRefOid)
git -C "$REPO" fetch origin main -q
[ "$BASE" = "$(git -C "$REPO" rev-parse origin/main)" ] || echo "STALE BASE -- content evidence only"
```

`/tmp/claude-1000/watch-ci3.sh` folds this in: exit 0 = merge-eligible, **exit 3 = green but stale
base** (rebase and re-run), exit 2 = deadline. Keeping the two outcomes as distinct exit codes stops
a green-but-stale run from being read as a merge signal.

## `bd show --json` inlines every dependency's FULL object

`bd show <id> --json | jq '.[0].dependencies'` does not return ids — it returns each dependency
issue in full: `description`, `design`, `acceptance_criteria`, `notes`, labels, timestamps. On an
epic child whose parent is a large epic, one `bd show` can cost ~2k tokens to communicate a single
parent id. Four of them in one command cost ~8k.

Project before printing, always:

```bash
bd show <id> --json | jq -r '.[0]|"\(.status) deps=\((.dependencies//[])|map(.id)|join(","))"'
```

Same shape as `bd list --json` silently capping at 50: the default output of a query is tuned for a
human reading one record, not for a loop over many.

## A `pr-review-task` child of an `in_progress` epic is unreachable via `bd ready`

`bd ready` treats parent-child as a blocking edge, so a child whose ONLY dependency is its parent
epic shows `status=blocked` with `blocked_by=[]` — an empty blocker list next to a blocked status is
the tell. Observed 2026-08-24: `bu-h4wec.6`/.7/.8/.9 (review tasks for PRs #3799/#3801/#3802/#3806)
were all `blocked` solely because `bu-h4wec` was `in_progress`, while the epic's own notes recorded
that the children were deliberately independent of each other.

Nothing is actually blocked here — the coordinator can dispatch such a bead directly by id, because
dispatch does not consult `bd ready`. Do not read "blocked" as "waiting on unfinished work" without
checking whether `blocked_by` is empty. And do not "fix" it by closing the parent epic early; the
epic is legitimately in progress.

## `uv run pytest` argv in `ps` has TWO forms — grepping for one reads as "not running"

Observed 2026-08-24: `ps -eo args | grep 'bin/python -m pytest'` returned empty while two full
suites were live. The actual argv was the PATH form:

```
<venv>/bin/python <venv>/bin/pytest tests/ roster/ --ignore=tests/e2e -q
```

not the `-m` form. An earlier session recorded `-m pytest` as *the* shape; it is one of at least two,
and which you get depends on how `uv` resolves the entry point. Grep for the bare word `pytest` and
filter, or better: don't probe by name at all — record the PID at launch and poll `kill -0 "$PID"`.
`pgrep -f` additionally self-matches the waiter.

Same defect family as everything else in this file: an empty grep result is not evidence of absence,
it is evidence that this pattern did not match.

## `resolve_review_context.py` misses the PR number when only the REVIEW bead carries `external_ref`

Observed 2026-09-06 (bu-9akae, PR #4027): the review bead had `external_ref: "gh-pr:4027"` set
directly (by the coordinator, at dispatch time), but the *original implementation* bead's
`external_ref` was `null` — it doesn't get one; only `pr-review-task` beads seem to. The script only
ever reads `external_ref` off `original_id`, never off the review bead (`args.issue_id`) itself, so
it fell through to `extract_pr_number(review_description)`, which needs a literal
`https://github.com/.../pull/N` URL in the bead's `description` field. A review-bead description
written as prose ("Review PR #4027: ...") has no such URL, so resolution failed with
`missing-pr-number` even though the PR number was sitting right there in the bead's own record.

Workaround used: read `external_ref` off the review bead directly (`bd show "${ISSUE_ID}" --json`),
regex out `gh-pr:(\d+)`, then reconstruct the rest of `CONTEXT_JSON` by hand with `gh repo view` +
`gh pr view <n> --json ...` (same fields the script emits). Don't block on
`blocked-awaiting-coordinator` when the PR number is this directly available — that status is for
genuinely unresolved context, not a narrow gap in one fallback script.

Fix candidate for the script itself: check `args.issue_id`'s own `external_ref` for a `gh-pr:N` match
before (or alongside) `original_id`'s, since the coordinator is now observed to set it on the review
bead rather than the original.

## `gh pr merge --squash --auto` under a merge queue: `autoMergeRequest` stays `null`, use `mergeQueueEntry` instead

Observed 2026-09-06 (bu-81436, PR #4030): `gh pr merge 4030 --squash --auto` printed the
informational `! The merge strategy for main is set by the merge queue` line (expected per the
coordinator's dispatch note) and exit 0. But `gh pr view 4030 --json autoMergeRequest` came back
`null` — the confirmation field `failure-protocol.md`'s `merge-queued` criterion names. A second
`gh pr merge --squash --auto` call confirmed `! Pull request ... is already queued to merge`, so the
first call did enqueue; `autoMergeRequest` simply never populates for a PR whose base has a
repository-level merge queue with a fixed merge strategy (GitHub models it as a merge-queue entry,
not a "set auto-merge" request).

Durable confirmation for this repo's queue setup:

```bash
gh api graphql -f query='
query { repository(owner: "OWNER", name: "REPO") {
  pullRequest(number: N) { state mergeStateStatus
    autoMergeRequest { enabledAt mergeMethod }
    mergeQueueEntry { state position } } } }'
```

`mergeQueueEntry.state` (`AWAITING_CHECKS`, etc.) plus a non-null `position` is the actual enqueue
signal here. Treat `autoMergeRequest: null` alongside a present `mergeQueueEntry` as `merge-queued`,
not as a failed enqueue — don't retry-loop `gh pr merge` past the second confirming call, and don't
report `blocked-awaiting-coordinator` solely because `autoMergeRequest` is null on a queue-managed
base branch.

## `bd update --claim` refuses a stale claim held by a *different* actor

`runtime-and-safety.md`'s ownership rule says an expired-heartbeat claim held by
another actor may be reclaimed with `bd update <id> --claim`. In practice (bd
1.0.4) `--claim` hard-refuses whenever `assignee` is already non-empty and not
you, regardless of heartbeat staleness:

```
Error claiming <id>: issue already claimed by coordinator:<other-uuid>
```

This is a different failure from the documented "claiming a blocked bead" and
"claiming an already-in_progress bead with an empty assignee" cases above —
here `assignee` is populated, just stale. Confirm the heartbeat is actually
past the stall threshold first (never do this to a live actor), then clear the
assignee before reclaiming:

```bash
bd update <id> --status open --assignee "" --json   # drop the stale assignee
bd update <id> --claim --json                        # now succeeds
```

Note `--claim` sets `assignee` to your resolved identity (e.g. the git user
`Tzeusy`), not a synthetic `coordinator:<uuid>` string — only the heartbeat
note uses that convention. Re-verify `assignee` in the result as usual.
Observed 2026-09-08, bd 1.0.4, reclaiming two `in_progress` spec-correction
beads (`bu-atsax`, `bu-m09qm`) whose prior coordinator session's heartbeat was
~24h stale.

## Transient "Verify the external server is running and reachable" on bd mutations

**Seen:** 2026-10-03, syzygy, during a `bd close` burst while several worker lanes were also hitting the shared Dolt sql-server over tailnet (`dolt.TAILNET.ts.net:3307`).
**Symptom:** `bd close` prints only the "Verify the external server is running and reachable from this host: nc -zv …" hint and closes nothing. The command after it in the same shell (`git push`, etc.) succeeds, so the failure is easy to miss.
**Fix:** run `nc -zv -w5 <host> <port>`. If it connects, the blip was transient: re-run the same `bd` mutation and confirm the `✓ Closed …` lines. Always read `bd` mutation output for the ✓ line rather than assuming success. A batched cleanup command hides the failure otherwise.

## `gh pr merge` refused: "Auto merge is not allowed for this repository" (merge-queue repo)
- **Symptom**: `gh pr merge <n> [--squash --auto]` on a repo whose ruleset uses a merge queue
  but has `enablePullRequestAutoMerge` off → `GraphQL: Auto merge is not allowed for this
  repository`; the PR is never queued.
- **Fix**: enqueue directly:
  `ID=$(gh api graphql -f query='{repository(owner:"O",name:"R"){pullRequest(number:N){id}}}' -q .data.repository.pullRequest.id)`
  then `gh api graphql -f query="mutation{enqueuePullRequest(input:{pullRequestId:\"$ID\"}){mergeQueueEntry{position state}}}"`.
- Observed: 2026-10-03, tzeusy-org/tze-hud PR #1245.

## bd 1.3.x: "legacy Dolt server workspace detected; explicit migration is required"
- **Symptom**: `~/.local/bin/bd` (1.3.1) refuses a server-mode workspace created by 1.0.4.
- **Fix (no migration)**: use the 1.0.4 binary explicitly (`/usr/local/bin/bd`). Also check
  the workspace points at the shared server (`dolt.TAILNET.ts.net:3307`), not 127.0.0.1:
  a local `dolt sql-server` on 3307 owned by another user lacks the project DB, and bd 1.0.4
  then suggests `bd bootstrap` — don't; repoint `dolt_server_host` / `dolt.host` instead.
- bd mutations print auto-backup/auto-export warnings and may exit non-zero while succeeding;
  verify with `bd show <id> --json`. In zsh loops, unquoted `$ids` does not word-split.
- Observed: 2026-10-03, tze-hud.

## tze-hud: `gh pr merge --squash --auto` fails "Auto merge is not allowed"
- **Symptom**: reviewer cannot enter the merge queue; repo has enablePullRequestAutoMerge off.
- **Workaround**: enqueue directly with GraphQL `mutation{enqueuePullRequest(input:{pullRequestId:$id}){mergeQueueEntry{position}}}` (queue still enforces required checks). Observed: 2026-10-03, tze-hud PR #1247.

## `bd worktree create` prints "Fixed .beads permissions to 0700"

bd 1.3.x tightens the new worktree's `.beads/` to 0700 on creation (seen
2026-10-06, tze-hud). On hosts where the project tree is shared with a
reviewer by group/ACL, that locks them out. Restore right after creation:
`chmod 2770 <worktree>/.beads` (match the main checkout's `.beads` mode via
`stat -c %a`). The ACL entries survive; only the mode bits change.

## Guarded label-only updates require a field update (bd 1.0.4)

Observed 2026-10-06: `bd update ID --if-assignee ACTOR --add-label pr-review` fails with `--if-assignee/--if-status require at least one field update`; label and parent edits are not covered by that guard. Read and verify current ownership, then include the intended current status field (for example `--status blocked`) with the guarded label change, and verify both afterward. Do not drop ownership verification or blindly overwrite a stale status.
