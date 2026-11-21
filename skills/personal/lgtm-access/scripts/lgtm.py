#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Query the homelab LGTM stack over Tailscale with compact, projected output.

Prometheus, Alertmanager and the Pushgateway answer on the tailnet without
credentials. Loki, Tempo and long-range (Thanos) metrics are reachable only
through Grafana's datasource proxy, which needs a credential in the
environment (never on the command line, never printed):

  GRAFANA_TOKEN or GRAFANA_VIEWER_TOKEN        service-account bearer token
  GRAFANA_USER + GRAFANA_PASSWORD               basic auth
  HOMEPAGE_VAR_GRAFANA_USER + ..._PASSWORD      basic auth (prod BWS names)

Inject them with `bws run -- 'uv run lgtm.py ...'` (bws runs ONE shell string;
unquoted multi-word commands are re-split); see the lgtm-credentials subskill. The tailnet suffix comes from LGTM_TAILNET, else
`tailscale status --json`, else the `search` line of /etc/resolv.conf.

Usage:
  lgtm.py doctor
  lgtm.py prom 'sum by (namespace) (kube_pod_info)' [--history] [--range 6h --step 5m]
  lgtm.py metrics '^node_filesystem'
  lgtm.py targets [--all]
  lgtm.py alerts [--all]
  lgtm.py dashboards 'storage'
  lgtm.py loglabels
  lgtm.py logs '{service_name="opencode"} |= "error"' [--since 1h] [--limit 50]
  lgtm.py traces [--traceql '{ resource.service.name = "x" }'] [--id TRACE_ID]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 20
LINE_WIDTH = 220


class LgtmError(Exception):
    """Expected failure with a message fit to show the agent."""


def tailnet_suffix() -> str:
    env = os.environ.get("LGTM_TAILNET", "").strip().strip(".")
    if env:
        return env
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        suffix = (json.loads(out).get("MagicDNSSuffix") or "").strip(".")
        if suffix:
            return suffix
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("search"):
                    for domain in line.split()[1:]:
                        if domain.endswith(".ts.net"):
                            return domain.strip(".")
    except OSError:
        pass
    raise LgtmError(
        "cannot resolve the tailnet suffix: set LGTM_TAILNET=<name>.ts.net "
        "or make `tailscale status --json` work"
    )


def endpoints() -> dict[str, str]:
    net = tailnet_suffix()
    return {
        "grafana": f"https://grafana.{net}",
        "prometheus": f"https://prometheus.{net}",
        "alertmanager": f"https://alertmanager.{net}",
        "pushgateway": f"https://prometheus-pushgateway.{net}",
        "otlp_http": f"http://otel.{net}:4318",
        "otlp_grpc": f"http://otel.{net}:4317",
    }


def grafana_auth_header() -> str | None:
    """Build an Authorization header from env; the value is never printed."""
    for name in ("GRAFANA_TOKEN", "GRAFANA_VIEWER_TOKEN"):
        token = os.environ.get(name)
        if token:
            return f"Bearer {token}"
    for prefix in ("GRAFANA_", "HOMEPAGE_VAR_GRAFANA_"):
        user = os.environ.get(f"{prefix}USER")
        password = os.environ.get(f"{prefix}PASSWORD")
        if user and password:
            raw = f"{user}:{password}".encode()
            return "Basic " + base64.b64encode(raw).decode()
    return None


def credential_source() -> str:
    for name in ("GRAFANA_TOKEN", "GRAFANA_VIEWER_TOKEN"):
        if os.environ.get(name):
            return name
    for prefix in ("GRAFANA_", "HOMEPAGE_VAR_GRAFANA_"):
        if os.environ.get(f"{prefix}USER") and os.environ.get(f"{prefix}PASSWORD"):
            return f"{prefix}USER/PASSWORD"
    return "none"


def get_json(url: str, params: dict | None = None, auth: str | None = None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if auth:
        request.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")[:300].replace("\n", " ")
        raise LgtmError(f"HTTP {err.code} from {url.split('?')[0]}: {body}") from None
    except urllib.error.URLError as err:
        raise LgtmError(f"cannot reach {url.split('?')[0]}: {err.reason}") from None


def status_code(url: str, auth: str | None = None) -> str:
    request = urllib.request.Request(url)
    if auth:
        request.add_header("Authorization", auth)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return str(response.status)
    except urllib.error.HTTPError as err:
        return str(err.code)
    except (urllib.error.URLError, OSError) as err:
        return f"unreachable ({getattr(err, 'reason', err)})"


def require_grafana() -> str:
    auth = grafana_auth_header()
    if not auth:
        raise LgtmError(
            "no Grafana credential in env; Loki, Tempo and --history need one. "
            "Run under `bws run -- ...` (see the lgtm-credentials subskill)"
        )
    return auth


def datasource_uid(kind: str, auth: str) -> str:
    """Look up a datasource UID by type: Loki's UID is auto-generated."""
    grafana = endpoints()["grafana"]
    for source in get_json(f"{grafana}/api/datasources", auth=auth):
        if source.get("type") == kind:
            return source["uid"]
    raise LgtmError(f"no {kind} datasource in Grafana")


def parse_duration(text: str) -> int:
    match = re.fullmatch(r"(\d+)([smhdw])", text)
    if not match:
        raise LgtmError(f"bad duration {text!r}; use e.g. 30s, 15m, 6h, 7d")
    scale = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]
    return int(match[1]) * scale


def fmt_labels(metric: dict) -> str:
    name = metric.get("__name__", "")
    rest = ",".join(f'{k}="{v}"' for k, v in sorted(metric.items()) if k != "__name__")
    return f"{name}{{{rest}}}"


def clip(text: str) -> str:
    return text if len(text) <= LINE_WIDTH else text[: LINE_WIDTH - 1] + "…"


def emit_vector(data: dict, limit: int) -> None:
    result = data.get("result", [])
    kind = data.get("resultType")
    for series in result[:limit]:
        labels = fmt_labels(series.get("metric", {}))
        if kind == "matrix":
            values = series.get("values", [])
            first, last = values[0][1], values[-1][1]
            print(clip(f"{labels}  points={len(values)} first={first} last={last}"))
        elif kind == "vector":
            print(clip(f"{labels}  {series['value'][1]}"))
        else:
            print(clip(json.dumps(series)))
    if kind == "scalar":
        print(data["result"][1])
    if len(result) > limit:
        print(f"... {len(result) - limit} more series (raise --limit or aggregate)")
    if not result:
        print("(empty result)")


def cmd_doctor(_: argparse.Namespace) -> None:
    eps = endpoints()
    auth = grafana_auth_header()
    print(f"tailnet: {eps['grafana'].split('grafana.', 1)[1]}")
    print(f"grafana credential: {credential_source()}")
    checks = {
        "prometheus": f"{eps['prometheus']}/-/ready",
        "alertmanager": f"{eps['alertmanager']}/-/ready",
        "pushgateway": f"{eps['pushgateway']}/-/ready",
        "grafana health": f"{eps['grafana']}/api/health",
        "grafana api (auth)": f"{eps['grafana']}/api/search?limit=1",
    }
    for label, url in checks.items():
        print(f"{label}: {status_code(url, auth if 'auth' in label else None)}")
    print(f"otlp http: {status_code(eps['otlp_http'] + '/v1/metrics')} (405 = up, GET not allowed)")


def cmd_prom(args: argparse.Namespace) -> None:
    eps = endpoints()
    if args.history:
        auth = require_grafana()
        base = f"{eps['grafana']}/api/datasources/proxy/uid/prometheus/api/v1"
    else:
        auth, base = None, f"{eps['prometheus']}/api/v1"
    if args.range:
        end = time.time()
        params = {
            "query": args.query,
            "start": end - parse_duration(args.range),
            "end": end,
            "step": args.step,
        }
        payload = get_json(f"{base}/query_range", params, auth)
    else:
        payload = get_json(f"{base}/query", {"query": args.query}, auth)
    emit_vector(payload["data"], args.limit)


def cmd_metrics(args: argparse.Namespace) -> None:
    names = get_json(f"{endpoints()['prometheus']}/api/v1/label/__name__/values")["data"]
    pattern = re.compile(args.regex)
    hits = [name for name in names if pattern.search(name)]
    for name in hits[: args.limit]:
        print(name)
    print(f"({len(hits)} of {len(names)} metric names match)")


def cmd_targets(args: argparse.Namespace) -> None:
    data = get_json(f"{endpoints()['prometheus']}/api/v1/targets", {"state": "active"})["data"]
    rows = [
        t for t in data["activeTargets"] if args.all or t.get("health") != "up"
    ]
    for t in rows:
        labels = t.get("labels", {})
        print(clip(
            f"{t.get('health')}  job={labels.get('job')} instance={labels.get('instance')} "
            f"err={t.get('lastError') or '-'}"
        ))
    total = len(data["activeTargets"])
    print(f"({len(rows)} shown of {total} active targets{'' if args.all else '; down only'})")


def cmd_alerts(args: argparse.Namespace) -> None:
    params = {"active": "true", "silenced": str(args.all).lower(), "inhibited": str(args.all).lower()}
    alerts = get_json(f"{endpoints()['alertmanager']}/api/v2/alerts", params)
    for alert in alerts:
        labels = alert.get("labels", {})
        state = alert.get("status", {}).get("state")
        summary = alert.get("annotations", {}).get("summary", "")
        print(clip(
            f"{state}  {labels.get('severity', '-')}  {labels.get('alertname')}  "
            f"ns={labels.get('namespace', '-')}  since={alert.get('startsAt', '')[:16]}  {summary}"
        ))
    print(f"({len(alerts)} alerts{'' if args.all else '; silenced/inhibited hidden'})")


def cmd_logs(args: argparse.Namespace) -> None:
    auth = require_grafana()
    uid = datasource_uid("loki", auth)
    end = time.time_ns()
    params = {
        "query": args.query,
        "start": end - parse_duration(args.since) * 1_000_000_000,
        "end": end,
        "limit": args.limit,
        "direction": "backward",
    }
    url = f"{endpoints()['grafana']}/api/datasources/proxy/uid/{uid}/loki/api/v1/query_range"
    data = get_json(url, params, auth)["data"]
    if data.get("resultType") != "streams":
        emit_vector(data, args.limit)
        return
    lines = []
    for stream in data["result"]:
        labels = stream.get("stream", {})
        tag = labels.get("service_name") or labels.get("k8s_pod_name") or labels.get("pod") or "-"
        for ts, line in stream.get("values", []):
            lines.append((int(ts), tag, line))
    lines.sort(reverse=True)
    for ts, tag, line in lines[: args.limit]:
        stamp = time.strftime("%m-%d %H:%M:%S", time.gmtime(ts / 1e9))
        print(clip(f"{stamp}Z {tag}  {line.strip()}"))
    print(f"({min(len(lines), args.limit)} lines, newest first, {len(data['result'])} streams)")


def cmd_loglabels(args: argparse.Namespace) -> None:
    auth = require_grafana()
    base = f"{endpoints()['grafana']}/api/datasources/proxy/uid/{datasource_uid('loki', auth)}/loki/api/v1"
    for label in get_json(f"{base}/labels", auth=auth).get("data") or []:
        values = get_json(f"{base}/label/{label}/values", auth=auth).get("data") or []
        shown = ", ".join(values[: args.limit])
        more = f" (+{len(values) - args.limit})" if len(values) > args.limit else ""
        print(clip(f"{label}: {shown}{more}"))


def cmd_dashboards(args: argparse.Namespace) -> None:
    auth = require_grafana()
    grafana = endpoints()["grafana"]
    params = {"type": "dash-db", "query": args.text, "limit": args.limit}
    hits = get_json(f"{grafana}/api/search", params, auth)
    for hit in hits:
        print(clip(f"{hit.get('title')}  {grafana}{hit.get('url')}"))
    print(f"({len(hits)} dashboards)")


def cmd_traces(args: argparse.Namespace) -> None:
    auth = require_grafana()
    base = f"{endpoints()['grafana']}/api/datasources/proxy/uid/{datasource_uid('tempo', auth)}"
    if args.id:
        trace = get_json(f"{base}/api/v2/traces/{args.id}", auth=auth)
        spans = []
        for batch in trace.get("trace", {}).get("resourceSpans", []):
            service = next(
                (a["value"].get("stringValue") for a in batch.get("resource", {}).get("attributes", [])
                 if a.get("key") == "service.name"), "-",
            )
            for scope in batch.get("scopeSpans", []):
                for span in scope.get("spans", []):
                    start, stop = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                    spans.append((start, service, span.get("name"), (stop - start) / 1e6))
        spans.sort()
        for _, service, name, ms in spans[: args.limit]:
            print(clip(f"{ms:9.1f}ms  {service}  {name}"))
        print(f"({min(len(spans), args.limit)} of {len(spans)} spans, by start time)")
        return
    end = int(time.time())
    params = {"limit": args.limit, "start": end - parse_duration(args.since), "end": end}
    if args.traceql:
        params["q"] = args.traceql
    traces = get_json(f"{base}/api/search", params, auth).get("traces", [])
    for t in traces:
        print(clip(
            f"{t.get('traceID')}  {t.get('rootServiceName')}  {t.get('rootTraceName')}  "
            f"{t.get('durationMs', 0)}ms"
        ))
    print(f"({len(traces)} traces)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="resolve endpoints, check reachability and credential")

    prom = sub.add_parser("prom", help="PromQL instant or range query")
    prom.add_argument("query")
    prom.add_argument("--history", action="store_true",
                      help="query Thanos via Grafana (beyond Prometheus' 15d; needs credential)")
    prom.add_argument("--range", help="range window, e.g. 6h; omit for instant query")
    prom.add_argument("--step", default="5m")
    prom.add_argument("--limit", type=int, default=30)

    metrics = sub.add_parser("metrics", help="list metric names matching a regex")
    metrics.add_argument("regex")
    metrics.add_argument("--limit", type=int, default=60)

    targets = sub.add_parser("targets", help="scrape targets (down only by default)")
    targets.add_argument("--all", action="store_true")

    alerts = sub.add_parser("alerts", help="active Alertmanager alerts")
    alerts.add_argument("--all", action="store_true", help="include silenced and inhibited")

    logs = sub.add_parser("logs", help="LogQL query via Grafana (needs credential)")
    logs.add_argument("query")
    logs.add_argument("--since", default="1h")
    logs.add_argument("--limit", type=int, default=50)

    dashboards = sub.add_parser("dashboards", help="search Grafana dashboards by title (needs credential)")
    dashboards.add_argument("text", nargs="?", default="")
    dashboards.add_argument("--limit", type=int, default=30)

    loglabels = sub.add_parser("loglabels", help="Loki label names and sample values (needs credential)")
    loglabels.add_argument("--limit", type=int, default=8)

    traces = sub.add_parser("traces",help="Tempo search or trace lookup via Grafana (needs credential)")
    traces.add_argument("--traceql")
    traces.add_argument("--id")
    traces.add_argument("--since", default="1h")
    traces.add_argument("--limit", type=int, default=20)
    return parser


COMMANDS = {
    "doctor": cmd_doctor, "prom": cmd_prom, "metrics": cmd_metrics, "targets": cmd_targets,
    "alerts": cmd_alerts, "dashboards": cmd_dashboards, "logs": cmd_logs, "loglabels": cmd_loglabels, "traces": cmd_traces,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except LgtmError as err:
        print(f"lgtm: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
