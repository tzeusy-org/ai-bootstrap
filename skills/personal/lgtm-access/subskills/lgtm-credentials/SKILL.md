---
name: lgtm-credentials
description: Use when an LGTM read or write fails on auth or reachability (Grafana 401/403, endpoint unreachable), or before a Loki, Tempo, dashboard or long-range metrics query, to find which identity (tze or orca) can reach what and how to inject the Grafana credential without exposing it.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-22"
---

# LGTM Credentials

Only Grafana needs a credential. Everything else on the tailnet answers
anonymously, so most failures are reachability, not auth.

## Who can reach what

Check identity with `id -un`.

| Need | tze session | orca session |
|---|---|---|
| Prometheus, Alertmanager, Pushgateway, OTLP | yes, no credential | yes, no credential |
| Grafana API, Loki, Tempo, Thanos history | yes: prod BWS Grafana login | only once a dev-project token exists (below) |
| `kubectl` in namespace `lgtm` | yes (cluster admin) | no: orca-agent RBAC excludes `lgtm` |

## Injecting the credential

`bws run` exports a project's secrets as env vars for one child process. It
joins its arguments into one string and hands that to a shell, so nested
quoting is lost and backticks or `$` inside it execute. Keep the command
string fixed and pass anything variable (queries, TraceQL) in an env var
set outside it:

```bash
Q='{service_name="opencode"} |~ `timeout`' \
  bws run --project-id "$BWS_TZEHOUSE_ID_PROD" -- 'uv run <pkg>/scripts/lgtm.py logs "$Q"'
```

tze: the shell's `BWS_ACCESS_TOKEN` reads the prod project, which holds
`HOMEPAGE_VAR_GRAFANA_USER` / `HOMEPAGE_VAR_GRAFANA_PASSWORD`. That login is
Grafana **Admin**, so use it for reads; write only on an owner request.
`--project-id` limits injection to that project; drop it if the variable is
unset.

orca: `BWS_ACCESS_TOKEN` and `BWS_PROJECT_ID` (dev project only) come from
`~/.config/bws/agent.env`, which the Orca service loads; agents it spawns
inherit them. In an interactive SSH shell as orca, `set -a; . ~/.config/bws/agent.env; set +a`
first. The helper picks up `GRAFANA_VIEWER_TOKEN` when that project has one:

```bash
bws run --project-id "$BWS_PROJECT_ID" -- 'uv run <pkg>/scripts/lgtm.py doctor'
```

`doctor` prints the credential's source name (`grafana credential: ...`),
never its value. To check that a key exists, this prints key names only,
which orca's "no `bws secret list` into context" guardrail permits:

```bash
bws secret list "$BWS_PROJECT_ID" -o json | jq -r '.[].key' | grep -i grafana
```

## No Grafana credential for orca yet

If `doctor` reports `grafana credential: none` under the dev project, the
owner has not provisioned one. Stop and tell the owner what the query needs;
do not borrow tze's credential or the prod project. Owner procedure:

1. Grafana → Administration → Service accounts → add `llm-agents`, role
   **Viewer** (queries datasources, reads dashboards; cannot edit). Set a
   token expiry.
2. Store the token in the Bitwarden web app as `GRAFANA_VIEWER_TOKEN` in
   the dev project (keeps it out of shell history and `ps`). This is a
   deliberate exception to dev/prod separation: every dev-project reader
   can then read prod telemetry.

## Failure decoding

| Symptom | Cause | Action |
|---|---|---|
| `401` from Grafana | no or wrong credential in env | re-run under `bws run`; check `doctor` |
| `403` from Grafana | role too low for that API | Viewer can query and read; anything else is owner work |
| `no loki datasource visible` | credential lacks datasource access | owner checks the service account's role |
| unreachable, tailnet host | Tailscale down, or orca egress guard | `tailscale status`; the guard list is `orca_server_blocked_tailnet_hosts` in the homelab Ansible role `orca_server` |
| `cannot resolve the tailnet suffix` | no `tailscale` CLI access | set `LGTM_TAILNET` |

## Hard stops

- Never `bws secret get` a credential into context, echo it, or pass it as
  a command-line argument (visible in `ps`). Env injection only.
- Never put a credential in a file, commit, script default or log.
- orca sessions never use a prod-project token.
