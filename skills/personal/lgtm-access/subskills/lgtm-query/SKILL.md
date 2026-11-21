---
name: lgtm-query
description: Use to read homelab observability data — PromQL against Prometheus or long-range Thanos history, LogQL against Loki, Tempo trace search or lookup, firing Alertmanager alerts, scrape-target health, metric discovery, or Grafana dashboard lookup — with compact output.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-22"
---

# LGTM Query

Read with [`scripts/lgtm.py`](../../scripts/lgtm.py) first; its output is
already projected (one line per series, alert or log line, with
`--limit`). Fall back to raw API calls only for what it lacks, and project
those with `jq` too. `<pkg>` is the lgtm-access package root.

## Pick the backend

| Question | Command | Credential |
|---|---|---|
| Current or recent (≤15d) metric value | `lgtm.py prom '<promql>' [--range 6h --step 5m]` | no |
| Metric older than 15 days | add `--history --ago 40d` (Thanos via Grafana) | yes |
| Which metrics exist | `lgtm.py metrics '<regex>'` | no |
| Is something not being scraped | `lgtm.py targets` (down only; `--all`) | no |
| What is firing | `lgtm.py alerts` (`--all` adds silenced) | no |
| App logs | `lgtm.py loglabels`, then `lgtm.py logs '<logql>' --since 1h` | yes |
| Traces | `lgtm.py traces [--traceql '<q>'] --since 1h`; `--id <trace>` for spans | yes |
| Which dashboard shows it | `lgtm.py dashboards '<text>'` | yes |

Credentialed commands run under `bws run`, with the query passed in an env
var; see [lgtm-credentials](../lgtm-credentials/SKILL.md).

## What each backend holds

- Prometheus: every scrape job plus OTLP-pushed app metrics; list jobs with
  `lgtm.py prom 'count by (job) (up)' --limit 50`. Local retention 15 days.
- Thanos (`--history`): same series; raw 30 days, 5-minute downsample 90
  days, 1-hour forever. `--ago` moves an instant query or a range's end into
  the past (or use PromQL `offset`). Thanos only uses data at resolution
  ≤ step/5, so past 30 days use `--step 25m` or more, past 90 days `5h` or
  more, or the result is empty.
- Loki holds logs sent over OTLP plus Loki's own canary, not Kubernetes pod
  stdout; run `loglabels` for the live label set. For pod logs use
  `kubectl -n <ns> logs` (tze; orca only in its RBAC namespaces).
- Tempo: traces sent over OTLP. TraceQL `{ resource.service.name = "x" }`
  matches traces with any span from service `x`.

## PromQL discipline

- Enumerate a metric's real label sets before aggregating:
  `lgtm.py prom 'count by (job, instance) (<metric>)'`.
- Pin every label you are not summing over; a second value appearing later
  silently doubles a sum.
- Aggregate before reading: `topk(10, ...)`, `sum by (...)`. The helper
  caps output at `--limit` series and says how many it dropped.

## Raw API recipes

Only when the helper lacks the call. `P=https://prometheus.TAILNET`,
`A=https://alertmanager.TAILNET`.

```bash
curl -s "$P/api/v1/label/<label>/values" | jq -r '.data[]' | head -50
curl -s "$P/api/v1/rules" | jq -r '.data.groups[].rules[] | select(.state=="firing") | .name'
curl -s "$P/api/v1/status/tsdb" | jq '.data.seriesCountByMetricName[:10]'
curl -s "$A/api/v2/silences" | jq -r '.[] | select(.status.state=="active") | "\(.endsAt) \(.comment)"'
```

`lgtm.py dashboards '<text>'` prints each match's URL to hand to a human.

## Reporting

State the query, time window and backend with each number, so a reader
can rerun it. Absent data is "no series", not zero.
