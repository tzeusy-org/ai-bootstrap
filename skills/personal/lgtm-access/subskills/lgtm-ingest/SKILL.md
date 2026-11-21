---
name: lgtm-ingest
description: Use to send telemetry into the homelab LGTM stack — wiring an app's OpenTelemetry exporter (traces, logs, metrics) to the Alloy OTLP gateway, pushing batch-job metrics to the Pushgateway, or configuring Prometheus remote_write — and to verify the data arrived and clean up test data.
metadata:
  owner: tze
  authors:
    - tze
    - Claude Opus 5.5
  status: active
  last_reviewed: "2026-11-22"
---

# LGTM Ingest

All write paths are unauthenticated on the tailnet, so a mistake lands in
shared production data immediately. Choose the path, send, verify with
[lgtm-query](../lgtm-query/SKILL.md), and delete what you only meant to test.

## Pick the path

| Source | Path |
|---|---|
| Long-running app or service | OTLP to Alloy (default) |
| Cron or batch job reporting a last result | Pushgateway |
| Existing Prometheus or agent forwarding series | remote_write |

## OTLP (preferred)

Alloy fans out: traces → Tempo, logs → Loki, metrics → Prometheus.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel.TAILNET:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf   # some SDKs default to gRPC
# or gRPC: endpoint http://otel.TAILNET:4317, protocol grpc (plaintext; insecure=true)
export OTEL_SERVICE_NAME=<service>
export OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=<dev|prod>
```

In-cluster pods use `http://lgtm-alloy.lgtm.svc.cluster.local:4318`.

- `service.name` becomes Loki's `service_name` label and Tempo's root
  service; unset gives `unknown_service`, so always set it.
- Metric names are converted to Prometheus form (dots to underscores, unit
  and `_total` suffixes). Find the real name with
  `lgtm.py metrics '<fragment>'` before writing queries or dashboards.
- OTLP metrics carry no staleness markers: a series stops updating rather
  than disappearing.

Smoke-test the endpoint without sending data (expects `200`):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"resourceMetrics":[]}' http://otel.TAILNET:4318/v1/metrics
```

## Pushgateway

For jobs that exit before a scrape. Groups persist on disk until deleted
and never go stale. Deleting a group stops new samples; what Prometheus
already scraped stays in Thanos.

```bash
G=https://prometheus-pushgateway.TAILNET
printf 'backup_last_success_timestamp_seconds %s\n' "$(date +%s)" \
  | curl -s --data-binary @- "$G/metrics/job/<job>/instance/<host>"
curl -s -X DELETE "$G/metrics/job/<job>/instance/<host>"     # remove a group
curl -s "$G/api/v1/metrics" | jq -r '.data[].labels.job' | sort -u   # list groups
```

Push timestamps and outcomes (`*_last_success_timestamp_seconds`,
`*_duration_seconds`), not counters that reset each run.

## remote_write

Receiver enabled at `https://prometheus.TAILNET/api/v1/write`; in-cluster
`http://lgtm-kube-prometheus-stack-prometheus.lgtm.svc.cluster.local:9090/api/v1/write`.

## Verify, then clean up

1. Metrics: `lgtm.py prom '<metric>{job="<job>"}'` within two minutes.
2. Logs and traces: `lgtm.py logs '{service_name="<service>"}' --since 10m`
   or `lgtm.py traces --traceql '{ resource.service.name = "<service>" }'`
   (credentialed).
3. Test data: delete Pushgateway groups now. Scraped, OTLP and
   remote-written samples cannot be deleted (Thanos keeps 1-hour data
   forever), so send tests only under a throwaway `job` / `service.name`
   (`test-<name>`) and say so in the report.

## Hard stops

- No secrets, tokens or personal data in log lines, span attributes or
  labels.
- No unbounded label values (request IDs, timestamps, user input, full
  URLs): each value is a new series, kept forever in Thanos.
- Do not reuse an existing `job` or `service.name` for test data.
