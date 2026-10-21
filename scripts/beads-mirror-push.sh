#!/usr/bin/env bash

# Mirror this repo's shared-server Beads database into refs/dolt/data on the
# git origin, so `bd bootstrap` can rebuild it on another machine.
#
# Why a script: in external-server mode `bd dolt push` cannot read the server
# (no remotesapi), so it either errors or pushes a stale local Dolt directory
# while printing "Push complete.". This script instead keeps a local embedded
# Dolt workspace (cloned from refs/dolt/data) in step with the server through
# `bd export` / `bd import`, then pushes that workspace. Server history is not
# mirrored, only its current state; the workspace's own history grows by one
# commit per run.
#
# Owner-private values never reach the public ref: issue owners recorded as
# email addresses are rewritten to the creating actor, and the run refuses to
# push when the export matches a denylist term, a tailnet hostname, or a
# Tailscale CGNAT address.
#
# Usage: scripts/beads-mirror-push.sh [--dry-run]
#   BEADS_MIRROR_DIR   workspace (default ~/.tmp/beads-mirror/<database>)
#   PII_DENYLIST       one literal per line (default ~/.config/pii-denylist)

set -euo pipefail

dry_run=0
case "${1:-}" in
    --dry-run) dry_run=1 ;;
    "") ;;
    *) printf 'Usage: %s [--dry-run]\n' "$(basename "$0")" >&2; exit 2 ;;
esac

repo="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
database="$(bd -C "$repo" dolt show --json | jq -r '.database')"
mirror="${BEADS_MIRROR_DIR:-$HOME/.tmp/beads-mirror/$database}"
denylist="${PII_DENYLIST:-$HOME/.config/pii-denylist}"
origin_url="$(git -C "$repo" remote get-url origin)"
actor="$(git -C "$repo" config user.name || echo unknown)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Commands against the mirror workspace must not inherit the shared-server
# override, otherwise bd would talk to the server instead of the workspace.
mbd() { env -u BEADS_DOLT_SERVER_HOST -u BEADS_DOLT_SERVER_PORT bd -C "$mirror" "$@"; }

# 1. Current server state, with owner emails rewritten to the creating actor.
bd -C "$repo" export --include-memories -o "$tmp/raw.jsonl" >/dev/null
jq -c --arg actor "$actor" \
    'if ((.owner // "") | test("@")) then .owner = (.created_by // $actor) else . end' \
    "$tmp/raw.jsonl" > "$tmp/mirror.jsonl"

# 2. Refuse to publish owner-private values.
tailnet='[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.ts\.net'
cgnat='(^|[^0-9.])100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}'
hits="$tmp/hits.jsonl"
: > "$hits"
if [ -s "$denylist" ]; then
    grep -F -f "$denylist" "$tmp/mirror.jsonl" >> "$hits" || true
fi
# The tracked configs use the placeholder <name>.TAILNET.ts.net on purpose.
sed -E 's/[A-Za-z0-9-]+\.TAILNET\.ts\.net//g' "$tmp/mirror.jsonl" | grep -E "$tailnet|$cgnat" >> "$hits" || true
if [ -s "$hits" ]; then
    printf 'refusing to mirror: owner-private values in %s\n' "$(jq -r '.id // ._type' "$hits" | sort -u | tr '\n' ' ')" >&2
    printf 'scrub them in bd (bd update <id> --title/--description/--notes) and rerun\n' >&2
    exit 1
fi

issues="$(jq -c 'select(._type != "memory")' "$tmp/mirror.jsonl" | wc -l)"
memories="$(jq -c 'select(._type == "memory")' "$tmp/mirror.jsonl" | wc -l)"
printf 'export: %s issues, %s memories, no owner-private values\n' "$issues" "$memories"
if [ "$dry_run" -eq 1 ]; then
    printf 'dry run: workspace %s left untouched\n' "$mirror"
    exit 0
fi

# 3. Workspace: an embedded-mode clone of origin's refs/dolt/data (bootstrap
#    reads that ref from the remote itself), migrated to this bd's schema.
#    `bd migrate --force` is a no-op when the schema already matches.
if [ ! -d "$mirror/.beads/embeddeddolt" ]; then
    mkdir -p "$(dirname "$mirror")"
    [ -d "$mirror/.git" ] || git clone --quiet --no-checkout "$origin_url" "$mirror"
    chmod 700 "$mirror"
    (cd "$mirror" && env -u BEADS_DOLT_SERVER_HOST -u BEADS_DOLT_SERVER_PORT bd bootstrap --yes >/dev/null)
    chmod 700 "$mirror/.beads"
    mbd migrate --force >/dev/null
fi

# 4. Bring the workspace to the server's state. `bd import` only adds and
#    updates, so edges and issues the server dropped are removed first, and
#    --allow-stale makes the server's row win even when its updated_at is older.
mbd export -o "$tmp/mirror-before.jsonl" >/dev/null
edges() { jq -r 'select(._type != "memory") | .id as $i | (.dependencies // [])[] | "\($i) \(.depends_on_id)"' "$1" | sort -u; }
edges "$tmp/mirror-before.jsonl" > "$tmp/have-edges"
edges "$tmp/mirror.jsonl" > "$tmp/want-edges"
comm -23 "$tmp/have-edges" "$tmp/want-edges" > "$tmp/stale-edges"
if [ -s "$tmp/stale-edges" ]; then
    printf 'removing %s dependency edge(s) absent from the server\n' "$(wc -l < "$tmp/stale-edges")"
    while read -r issue dep; do mbd dep remove "$issue" "$dep" >/dev/null; done < "$tmp/stale-edges"
fi
jq -r 'select(._type != "memory") | .id' "$tmp/mirror-before.jsonl" | sort > "$tmp/have"
jq -r 'select(._type != "memory") | .id' "$tmp/mirror.jsonl" | sort > "$tmp/want"
comm -23 "$tmp/have" "$tmp/want" > "$tmp/stale"
if [ -s "$tmp/stale" ]; then
    printf 'removing %s issue(s) absent from the server: %s\n' "$(wc -l < "$tmp/stale")" "$(tr '\n' ' ' < "$tmp/stale")"
    mbd delete --from-file "$tmp/stale" --force >/dev/null
fi
mbd import --allow-stale "$tmp/mirror.jsonl" | tail -n 3

# 5. Push and confirm the ref moved.
before="$(git ls-remote "$origin_url" refs/dolt/data | cut -c1-8)"
mbd dolt push
after="$(git ls-remote "$origin_url" refs/dolt/data | cut -c1-8)"
printf 'refs/dolt/data: %s -> %s\n' "${before:-none}" "${after:-none}"
