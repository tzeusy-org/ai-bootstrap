---
name: lgtm-access
description: >
  Use to read from or write to the homelab LGTM observability stack (Grafana,
  Prometheus/Thanos, Loki, Tempo, Alertmanager, Pushgateway, Alloy OTLP) over
  Tailscale from a tze or orca session: query metrics, logs or traces, list
  firing alerts, send telemetry, or get the Grafana credential a query needs.
  Not for alert triage or tuning (alert-steward) or dashboard JSON
  (generate-grafana-dashboards). Triggers: "query this metric", "find the
  logs for", "look up this trace", "send OTLP to the cluster", "push a
  metric", "Grafana returns 401".
metadata:
  owner: tze
  authors:
    - tze
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-22"
compatibility: Needs Tailscale on the homelab tailnet, uv, and bws for credentialed reads.
---

# LGTM Access

Router for using the homelab LGTM stack as a client. The stack itself (Helm
chart `helm/lgtm/`, dashboards, alert rules) lives in the homelab repo; this
skill never changes it.

## Endpoints

Hostnames sit on the homelab tailnet. `TAILNET` below is its MagicDNS suffix:
`tailscale status --json | jq -r .MagicDNSSuffix`. The helper resolves it
itself (`LGTM_TAILNET` overrides).

| Service | URL | Auth | Use |
|---|---|---|---|
| Prometheus | `https://prometheus.TAILNET` | none | PromQL, last 15 days; targets; rules |
| Alertmanager | `https://alertmanager.TAILNET` | none | firing alerts, silences |
| Pushgateway | `https://prometheus-pushgateway.TAILNET` | none | batch-job metrics in |
| Alloy OTLP | `http://otel.TAILNET:4317` gRPC, `:4318` HTTP | none | traces, logs, metrics in |
| Grafana | `https://grafana.TAILNET` | Grafana credential | dashboards; proxy to Loki, Tempo, Thanos |

Loki, Tempo and Thanos (metrics older than 15 days) have no tailnet endpoint:
reach them through Grafana's datasource proxy, which needs a credential.
Unauthenticated endpoints accept writes too, so treat them as shared
production state.

## Helper

[`scripts/lgtm.py`](scripts/lgtm.py) runs every common read with projected
output (`uv run <pkg>/scripts/lgtm.py --help`). Start with `doctor`: it
prints the tailnet, reachability of each endpoint, and which credential the
environment carries (by name, never value).

## Subskills

Load one; a credentialed read also loads lgtm-credentials once. Kept as a
superskill at the owner's request, so each workflow can grow without
crowding the others. Discover with:

```bash
PKG="$(dirname "<absolute-path-to-this-SKILL.md>")"
rg -n "^name:|^description:" "$PKG"/subskills/*/SKILL.md
```

| Task | Subskill |
|---|---|
| Read metrics, logs, traces, alerts, targets, dashboards | [subskills/lgtm-query/SKILL.md](subskills/lgtm-query/SKILL.md) |
| Send telemetry: OTLP from an app, Pushgateway from a batch job, remote_write | [subskills/lgtm-ingest/SKILL.md](subskills/lgtm-ingest/SKILL.md) |
| Which identity can reach what; injecting a Grafana credential; 401/403/unreachable | [subskills/lgtm-credentials/SKILL.md](subskills/lgtm-credentials/SKILL.md) |

## Out of scope

In the homelab repo, when that checkout is available:

- Alert triage, noise tuning, creating silences: `alert-steward` skill.
- Building or fixing dashboard JSON: `generate-grafana-dashboards` skill.
- Changing the stack (retention, datasources, exposure): `helm/lgtm/` and
  its README.

## Hard stops

- Never print, echo or log a credential, and never read one into context:
  inject with `bws run`, as lgtm-credentials shows.
- Never put query text inside a `bws run` command string; pass it through
  an env var (lgtm-credentials), since that string is re-parsed by a shell.
- Never `kubectl get secret -o yaml/json` in namespace `lgtm`.
- No Alertmanager writes (silences, expiries) and no Grafana writes
  (dashboards, datasources, users) unless the owner asked for that change.
- Written series are permanent (Thanos keeps 1-hour data forever): send
  test data only under a throwaway name, per lgtm-ingest.
