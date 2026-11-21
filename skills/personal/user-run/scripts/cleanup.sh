#!/usr/bin/env bash
# Prune user-run state older than N days (default 7) under <base>
# ($USER_RUN_BASE, default $HOME/.tmp):
#   - a slug dir with no file modified in N days is removed whole;
#   - in a live slug dir, runs (log, exit, payload copy, artifacts/<RUN_ID>)
#     older than N days are removed, except the latest run.
# Only dirs whose run.sh carries the "# user-run:" header are touched; slugs
# with a run in progress are skipped. scaffold.sh runs this automatically.
# Usage: cleanup.sh [--days N] [--dry-run] [--quiet] [--keep <slug>]...
set -uo pipefail
days=7; dry=0; quiet=0; keep=()
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="${2:?}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    --quiet) quiet=1; shift ;;
    --keep) keep+=("${2:?}"); shift 2 ;;
    *) echo "usage: cleanup.sh [--days N] [--dry-run] [--quiet] [--keep <slug>]..." >&2; exit 2 ;;
  esac
done
[[ "$days" =~ ^[0-9]+$ ]] && [ "$days" -ge 1 ] || { echo "bad --days: $days" >&2; exit 2; }
umask 002
base="${USER_RUN_BASE:-$HOME/.tmp}"
[ -d "$base" ] || exit 0
mins=$((days * 1440))
status=0

say() { [ "$quiet" = 1 ] || echo "$@"; }
remove() {
  if [ "$dry" = 1 ]; then echo "would remove: $*"; return 0; fi
  if rm -rf -- "$@"; then say "removed: $*"; else echo "failed to remove: $*" >&2; status=1; return 1; fi
}

for dir in "$base"/*/; do
  dir="${dir%/}"
  slug="${dir##*/}"
  [ -L "$dir" ] && continue
  [[ "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]] || continue
  head -n 2 "$dir/run.sh" 2>/dev/null | grep -q '^# user-run: ' || continue
  for k in "${keep[@]}"; do [ "$k" = "$slug" ] && continue 2; done

  # Hold the slug's lock while pruning it: skips a run in progress, and
  # refuses a new run until we are done.
  [ -e "$dir/.lock" ] || : >> "$dir/.lock" || continue
  exec 9< "$dir/.lock" || continue
  if ! flock -n 9; then say "skip (running): $slug"; exec 9<&-; continue; fi

  if [ -z "$(find "$dir" -mindepth 1 \( -type f -o -type l \) ! -name .lock -mmin -"$mins" -print -quit 2>/dev/null)" ]; then
    # Stale slug: run.sh (the user-run marker) goes last, so a partial
    # failure leaves a dir the next cleanup still recognises and retries.
    mapfile -d '' entries < <(find "$dir" -mindepth 1 -maxdepth 1 ! -name run.sh ! -name .lock -print0)
    if [ "${#entries[@]}" -eq 0 ] || remove "${entries[@]}"; then
      [ "$dry" = 1 ] && echo "would remove: $dir" || { rm -rf -- "$dir" && say "removed: $dir" || { echo "failed to remove: $dir" >&2; status=1; }; }
    fi
    exec 9<&-
    continue
  fi

  latest=""
  [ -L "$dir/latest.log" ] && latest="$(basename "$(readlink "$dir/latest.log")" .log)"
  while IFS= read -r log; do
    id="$(basename "$log" .log)"
    [ "$id" = "$latest" ] && continue
    remove "$log" "$dir/$id.exit" "$dir/$id.started" "$dir/$id.done.json" "$dir/payload.$id.sh" "$dir/artifacts/$id"
  done < <(find "$dir" -maxdepth 1 -type f -regextype posix-extended \
             -regex '.*/[0-9]{14}\.log' -mmin +"$mins" 2>/dev/null)
  exec 9<&-
done
exit "$status"
