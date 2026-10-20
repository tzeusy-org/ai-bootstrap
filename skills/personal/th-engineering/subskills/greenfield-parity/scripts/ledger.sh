#!/usr/bin/env bash
# Emit the already-known ledger for an idempotent audit run: open/in-progress
# beads, recently closed beads, prior review packets, and the last recorded
# convergence record. Output is compact and projected; paste it verbatim into
# reviewer prompts.
#
# Usage: ledger.sh [repo_root] [closed_limit]
#   repo_root     Beads-backed repo (default: cwd)
#   closed_limit  how many recently closed beads to list (default: 40)
set -euo pipefail
root="${1:-.}"; limit="${2:-40}"
command -v bd >/dev/null || { echo "ledger.sh: bd CLI not found; cannot build ledger" >&2; exit 2; }
command -v jq >/dev/null || { echo "ledger.sh: jq not found" >&2; exit 2; }
cd "$root"
echo "## Open / in-progress"
bd list --json | jq -r '.[] | select(.status != "closed") | "\(.id) \(.status) [\((.labels // []) | join(","))] \(.title)"'
echo; echo "## Recently closed (last $limit)"
bd list --status closed --json | jq -r '.[] | "\(.id) \(.closed_at // "") \(.title)"' | sort -k2 -r | head -n "$limit"
echo; echo "## Audit-keyed beads"
bd search "Audit-key: greenfield:" 2>/dev/null | sed '/^No issues/d' || true
echo; echo "## Prior review packets"
ls docs/reviews/ 2>/dev/null || echo "(none)"
echo; echo "## Last convergence record"
last="$(ls docs/reviews/*greenfield-delta.md 2>/dev/null | sort | tail -1 || true)"
[ -n "$last" ] && grep -E '^(Audited|Findings|Parity share):' "$last" || echo "(none — first run)"
