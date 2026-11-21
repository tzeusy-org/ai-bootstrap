---
name: lgtm-credentials
description: Use when an LGTM read or write fails on auth or reachability (Grafana 401/403, endpoint unreachable), or before a Loki, Tempo or long-range metrics query, to find which identity (tze or orca) can reach what and how to inject the Grafana credential without exposing it.
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

`bws run` exports a project's secrets as env vars for one child process.
It takes **one shell string**: `bws run -- 'cmd arg "quoted"'`. Unquoted
multi-word commands are re-split and the first one silently breaks.

tze: the shell's `BWS_ACCESS_TOKEN` reads the prod project, which holds
`HOMEPAGE_VAR_GRAFANA_USER` / `HOMEPAGE_VAR_GRAFANA_PASSWORD`. That login is
Grafana **Admin**, so use it for reads; write only on an owner request.

```bash
bws run -- 'uv run <pkg>/scripts/lgtm.py logs "{service_name=\"opencode\"}"'
```

orca: `BWS_ACCESS_TOKEN` and `BWS_PROJECT_ID` come from
`~/.config/bws/agent.env` (loaded into the Orca service environment) and
read only the dev project. The helper picks up `GRAFANA_VIEWER_TOKEN` when
that project has one:

```bash
bws run --project-id "$BWS_PROJECT_ID" -- 'uv run <pkg>/scripts/lgtm.py doctor'
```

Confirm which credential the helper found: `doctor` prints its source name
(`grafana credential: ...`), never the value. Check that a key exists
without reading it:

```bash
bws secret list "$BWS_PROJECT_ID" -o json | jq -r '.[].key' | grep -i grafana
```

## No Grafana credential for orca yet

If `doctor` reports `grafana credential: none` under the dev project, the
owner has not provisioned one. Stop and tell the owner what the query needs;
do not borrow tze's credential or the prod project. Owner procedure:

1. Grafana → Administration → Service accounts → add `llm-agents`, role
   **Viewer** (queries datasources and reads dashboards; cannot edit).
2. Add a token and store it in the Bitwarden web app as secret
   `GRAFANA_VIEWER_TOKEN` in the dev project (the web app keeps it out of
   shell history and `ps`).

## Failure decoding

| Symptom | Cause | Action |
|---|---|---|
| `401` from Grafana | no or wrong credential in env | re-run under `bws run`; check `doctor` |
| `403` from Grafana | role too low for that API | Viewer can query and read; anything else is owner work |
| unreachable, tailnet host | Tailscale down, or orca egress guard | `tailscale status`; the guard list is `orca_server_blocked_tailnet_hosts` in the homelab Ansible role `orca_server` |
| `cannot resolve the tailnet suffix` | no `tailscale` CLI access | set `LGTM_TAILNET` |

## Hard stops

- Never `bws secret get` a credential into context, echo it, or pass it as
  a command-line argument (visible in `ps`). Env injection only.
- Never put a credential in a file, commit, script default or log.
- orca sessions never use a prod-project token.
