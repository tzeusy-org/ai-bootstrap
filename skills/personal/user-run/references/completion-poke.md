# Completion poke

The owner runs `run.sh` in their own terminal, so the run is not a child of
the session that wrote it. The poke closes that gap without a model reading
the log: the harness leaves a completion marker, and a watcher the session
armed in the background turns it into one compact line.

## Marker

When the payload returns (success, failure, Ctrl-C, hangup), the harness
writes, in order: `<RUN_ID>.exit`, then `<RUN_ID>.done.json` (tmp file +
rename, so it is never half-written), then releases the slug lock:

```json
{"slug":"…","run":"20261121170755","exit":0,"started":true,"signal":"","start":"…","end":"…","secs":42,"log":"…/20261121170755.log","artifacts_dir":"…","artifacts":3}
```

`started:false` means `su` failed and the payload never ran. A SIGKILL of the
harness itself leaves neither file. The marker stays after delivery, so a
later session without a watcher finds the run through `status.sh` or
`watch.py --latest`.

## Watcher

`python3 <skill-dir>/scripts/watch.py <slug> [--timeout N] [--interval S] [--latest]`

It polls every 2 s (`--interval`), reads only the slug dir, and never
touches `run.sh` or a run, so it cannot block or break one; if no session is
watching, nothing happens. It delivers the newest run that:

- started at or after `run.sh` was last modified, so a run of an earlier
  payload is never delivered as the new one (arm after the final edit); and
- is newer than `.delivered`, the run it last reported, so re-arming without
  a new run waits instead of repeating the last poke.

`--latest` drops both gates and reports the newest finished run at once: use
it in a later session that missed the poke.

On delivery it records the run in `.delivered`, prints one line and exits 0:

```text
USER_RUN_DONE {"slug":"dev-rollout-20261121","run":"20261121170755","state":"done","exit":0,"secs":42,"log":"/home/orca/.tmp/dev-rollout-20261121/20261121170755.log","artifacts":1,"artifact_names":["deploys.txt"],"result":["deployed=e3f4ef75bba5"]}
```

- `state`: `done` (`exit` is the payload's code, 130 = Ctrl-C),
  `not-started` (su failed), or `died` (no exit code and the lock is free:
  killed or the terminal closed).
- `result`: the last 5 `RESULT` lines, prefix dropped. `artifact_names`: up
  to 8.
- `tail`: only when the run did not succeed: the last 10 non-empty log lines
  without harness markers or zsh `zle` noise, at most 1.5 KB, each line at
  most 300 characters. A success carries no log text.
- `log_unreadable: true` replaces `result` and `tail` if the log cannot be
  read.

With `--timeout N` it gives up after N seconds, printing
`USER_RUN_WAITING {"slug":…,"state":"running"|"waiting","latest":…}` and
exiting 3. Exit 2 means no `run.sh` under `<base>/<slug>`.

## Session protocol

1. After presenting the brief, run the watcher as a background command whose
   exit re-invokes the session (Claude Code: Bash `run_in_background`).
2. On `USER_RUN_DONE`, act on the line. Read the log (offset near the end)
   only when the tail is not enough to diagnose.
3. After fixing the payload and presenting the delta, re-arm.
4. Without background commands, do not poll in the foreground; wait for the
   owner and use `status.sh`.

## Ownership across users

The payload runs as the owner (tze) while the watcher runs as the session
user (orca). The slug dir is mode 2770 with the session user's group, and
the harness runs with umask 002, so the marker, `.exit`, log and artifacts
the owner writes are group-readable (`-rw-rw-r-- tze orca`, as seen on real
runs), and `.lock` opens read-only for the probe. The watcher writes only
`.delivered`, into a dir its own user owns; a failed write is ignored and at
worst lets a re-arm repeat the last poke. A payload that tightens
permissions on its own artifacts can hide their names, not the poke.

## Limits

- The lock probe (only while the newest run lacks an exit code) holds the
  lock for microseconds; a run started in exactly that instant is refused
  and the owner reruns it.
- A refusal before the run starts (no terminal, wrong group, another run in
  progress) leaves no marker, so the watcher keeps waiting.
- `run.sh` files scaffolded before `done.json` existed still work: the
  watcher falls back to `.exit` once the lock is free, without `secs`.
- No tmux poke: Claude Code's background-command exit is the wake-up path.
