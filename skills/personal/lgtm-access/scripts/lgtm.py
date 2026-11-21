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

Inject them with bws, passing query text through an env var so the shell
that `bws run` spawns never parses it (see the lgtm-credentials subskill):

  Q='{service_name="x"}' bws run -- 'uv run lgtm.py logs "$Q"'

The tailnet suffix comes from LGTM_TAILNET, else `tailscale status --json`,
else the `search` line of /etc/resolv.conf.

Usage:
  lgtm.py doctor
  lgtm.py prom 'sum by (namespace) (kube_pod_info)' [--range 6h --step 5m]
  lgtm.py prom 'count(up)' --history --ago 40d
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
TRACE_ID = re.compile(r"[0-9a-fA-F]{1,32}")


class LgtmError(Exception):
    """Expected failure with a message fit to show the agent."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so an Authorization header never follows one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LgtmError(f"refusing redirect ({code}) to {newurl.split('?')[0]}")


OPENER = urllib.request.build_opener(_NoRedirect)


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
    base = url
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if auth:
        request.add_header("Authorization", auth)
    try:
        with OPENER.open(request, timeout=TIMEOUT_S) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")[:300].replace("\n", " ")
        raise LgtmError(f"HTTP {err.code} from {base}: {body}") from None
    except urllib.error.URLError as err:
        raise LgtmError(f"cannot reach {base}: {err.reason}") from None
    except ValueError as err:  # includes JSONDecodeError
        raise LgtmError(f"non-JSON response from {base}: {err}") from None
    except OSError as err:  # read timeouts, resets
        raise LgtmError(f"network error from {base}: {err}") from None


def status_code(url: str, auth: str | None = None) -> str:
    request = urllib.request.Request(url)
    if auth:
        request.add_header("Authorization", auth)
    try:
        with OPENER.open(request, timeout=8) as response:
            return str(response.status)
    except urllib.error.HTTPError as err:
        return str(err.code)
    except (urllib.error.URLError, OSError, LgtmError) as err:
        return f"unreachable ({getattr(err, 'reason', err)})"


def require_grafana() -> str:
    auth = grafana_auth_header()
    if not auth:
        raise LgtmError(
            "no Grafana credential in env; Loki, Tempo, dashboards and --history "
            "need one. Run under `bws run` (see the lgtm-credentials subskill)"
        )
    return auth


def datasource_uid(kind: str, auth: str) -> str:
    """Find a datasource UID by type via frontend settings (readable by Viewer)."""
    settings = get_json(f"{endpoints()['grafana']}/api/frontend/settings", auth=auth)
    for source in (settings.get("datasources") or {}).values():
        if source.get("type") == kind and source.get("uid"):
            return source["uid"]
    raise LgtmError(f"no {kind} datasource visible to this Grafana credential")


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
    text = text.replace("\r", "").replace("\n", "⏎")
    return text if len(text) <= LINE_WIDTH else text[: LINE_WIDTH - 1] + "…"


def emit_vector(data: dict, limit: int) -> None:
    kind = data.get("resultType")
    result = data.get("result") or []
    if kind in ("scalar", "string"):
        print(result[1] if len(result) > 1 else "(empty result)")
        return
    for series in result[:limit]:
        labels = fmt_labels(series.get("metric", {}))
        if kind == "matrix":
            values = series.get("values") or []
            if values:
                print(clip(f"{labels}  points={len(values)} first={values[0][1]} last={values[-1][1]}"))
            else:
                print(clip(f"{labels}  points=0"))
        else:
            print(clip(f"{labels}  {series.get('value', [None, '?'])[1]}"))
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
    at = time.time() - (parse_duration(args.ago) if args.ago else 0)
    if args.range:
        params = {
            "query": args.query,
            "start": at - parse_duration(args.range),
            "end": at,
            "step": args.step,
        }
        payload = get_json(f"{base}/query_range", params, auth)
    else:
        payload = get_json(f"{base}/query", {"query": args.query, "time": at}, auth)
    emit_vector(payload["data"], args.limit)


def cmd_metrics(args: argparse.Namespace) -> None:
    try:
        pattern = re.compile(args.regex)
    except re.error as err:
        raise LgtmError(f"bad regex {args.regex!r}: {err}") from None
    names = get_json(f"{endpoints()['prometheus']}/api/v1/label/__name__/values")["data"]
    hits = [name for name in names if pattern.search(name)]
    for name in hits[: args.limit]:
        print(name)
    print(f"({len(hits)} of {len(names)} metric names match)")


def cmd_targets(args: argparse.Namespace) -> None:
    data = get_json(f"{endpoints()['prometheus']}/api/v1/targets", {"state": "active"})["data"]
    active = data.get("activeTargets") or []
    rows = [t for t in active if args.all or t.get("health") != "up"]
    for t in rows:
        labels = t.get("labels", {})
        print(clip(
            f"{t.get('health')}  job={labels.get('job')} instance={labels.get('instance')} "
            f"err={t.get('lastError') or '-'}"
        ))
    print(f"({len(rows)} shown of {len(active)} active targets{'' if args.all else '; down only'})")


def cmd_alerts(args: argparse.Namespace) -> None:
    flag = str(args.all).lower()
    params = {"active": "true", "silenced": flag, "inhibited": flag}
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


def cmd_dashboards(args: argparse.Namespace) -> None:
    auth = require_grafana()
    grafana = endpoints()["grafana"]
    params = {"type": "dash-db", "query": args.text, "limit": args.limit}
    hits = get_json(f"{grafana}/api/search", params, auth)
    for hit in hits:
        print(clip(f"{hit.get('title')}  {grafana}{hit.get('url')}"))
    print(f"({len(hits)} dashboards)")


def loki_base(auth: str) -> str:
    uid = datasource_uid("loki", auth)
    return f"{endpoints()['grafana']}/api/datasources/proxy/uid/{uid}/loki/api/v1"


def cmd_loglabels(args: argparse.Namespace) -> None:
    auth = require_grafana()
    base = loki_base(auth)
    for label in get_json(f"{base}/labels", auth=auth).get("data") or []:
        values = get_json(f"{base}/label/{urllib.parse.quote(label)}/values", auth=auth).get("data") or []
        shown = ", ".join(values[: args.limit])
        more = f" (+{len(values) - args.limit})" if len(values) > args.limit else ""
        print(clip(f"{label}: {shown}{more}"))


def cmd_logs(args: argparse.Namespace) -> None:
    auth = require_grafana()
    end = time.time_ns()
    params = {
        "query": args.query,
        "start": end - parse_duration(args.since) * 1_000_000_000,
        "end": end,
        "limit": args.limit,
        "direction": "backward",
    }
    data = get_json(f"{loki_base(auth)}/query_range", params, auth)["data"]
    if data.get("resultType") != "streams":
        emit_vector(data, args.limit)
        return
    lines = []
    for stream in data.get("result") or []:
        labels = stream.get("stream", {})
        tag = labels.get("service_name") or labels.get("k8s_pod_name") or labels.get("pod") or "-"
        for ts, line in stream.get("values", []):
            lines.append((int(ts), tag, line))
    lines.sort(reverse=True)
    for ts, tag, line in lines[: args.limit]:
        stamp = time.strftime("%m-%d %H:%M:%S", time.gmtime(ts / 1e9))
        print(clip(f"{stamp}Z {tag}  {line.strip()}"))
    print(f"({min(len(lines), args.limit)} lines, newest first, {len(data.get('result') or [])} streams)")


def emit_trace(trace: dict, limit: int) -> None:
    spans = []
    for batch in trace.get("trace", trace).get("resourceSpans", []) or []:
        attrs = batch.get("resource", {}).get("attributes", [])
        service = next(
            (a["value"].get("stringValue") for a in attrs if a.get("key") == "service.name"), "-",
        )
        for scope in batch.get("scopeSpans", []) or []:
            for span in scope.get("spans", []) or []:
                start, stop = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                spans.append((start, service, span.get("name"), (stop - start) / 1e6))
    if not spans:
        print("(trace not found or has no spans)")
        return
    spans.sort()
    for _, service, name, ms in spans[:limit]:
        print(clip(f"{ms:9.1f}ms  {service}  {name}"))
    print(f"({min(len(spans), limit)} of {len(spans)} spans, by start time)")


def cmd_traces(args: argparse.Namespace) -> None:
    auth = require_grafana()
    base = f"{endpoints()['grafana']}/api/datasources/proxy/uid/{datasource_uid('tempo', auth)}"
    if args.id:
        if not TRACE_ID.fullmatch(args.id):
            raise LgtmError(f"bad trace id {args.id!r}: expected 1-32 hex characters")
        try:
            trace = get_json(f"{base}/api/v2/traces/{args.id}", auth=auth)
        except LgtmError as err:
            if "HTTP 404" in str(err):
                print("(trace not found)")
                return
            raise
        emit_trace(trace, args.limit)
        return
    end = int(time.time())
    params = {"limit": args.limit, "start": end - parse_duration(args.since), "end": end}
    if args.traceql:
        params["q"] = args.traceql
    traces = get_json(f"{base}/api/search", params, auth).get("traces") or []
    for t in traces:
        print(clip(
            f"{t.get('traceID')}  {t.get('rootServiceName')}  {t.get('rootTraceName')}  "
            f"{t.get('durationMs', 0)}ms"
        ))
    print(f"({len(traces)} traces)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="resolve endpoints, check reachability and credential")

    prom = sub.add_parser("prom", help="PromQL instant or range query")
    prom.add_argument("query")
    prom.add_argument("--history", action="store_true",
                      help="query Thanos via Grafana (beyond Prometheus' 15d; needs credential)")
    prom.add_argument("--range", help="range window, e.g. 6h; omit for instant query")
    prom.add_argument("--step", default="5m")
    prom.add_argument("--ago", help="evaluate (or end the range) this long ago, e.g. 40d")
    prom.add_argument("--limit", type=int, default=30)

    metrics = sub.add_parser("metrics", help="list metric names matching a regex")
    metrics.add_argument("regex")
    metrics.add_argument("--limit", type=int, default=60)

    targets = sub.add_parser("targets", help="scrape targets (down only by default)")
    targets.add_argument("--all", action="store_true")

    alerts = sub.add_parser("alerts", help="active Alertmanager alerts")
    alerts.add_argument("--all", action="store_true", help="include silenced and inhibited")

    dashboards = sub.add_parser("dashboards", help="search Grafana dashboards by title (needs credential)")
    dashboards.add_argument("text", nargs="?", default="")
    dashboards.add_argument("--limit", type=int, default=30)

    loglabels = sub.add_parser("loglabels", help="Loki label names and sample values (needs credential)")
    loglabels.add_argument("--limit", type=int, default=8)

    logs = sub.add_parser("logs", help="LogQL query via Grafana (needs credential)")
    logs.add_argument("query")
    logs.add_argument("--since", default="1h")
    logs.add_argument("--limit", type=int, default=50)

    traces = sub.add_parser("traces", help="Tempo search or trace lookup (needs credential)")
    traces.add_argument("--traceql")
    traces.add_argument("--id")
    traces.add_argument("--since", default="1h")
    traces.add_argument("--limit", type=int, default=20)
    return parser


COMMANDS = {
    "doctor": cmd_doctor,
    "prom": cmd_prom,
    "metrics": cmd_metrics,
    "targets": cmd_targets,
    "alerts": cmd_alerts,
    "dashboards": cmd_dashboards,
    "loglabels": cmd_loglabels,
    "logs": cmd_logs,
    "traces": cmd_traces,
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
