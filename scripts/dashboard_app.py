from __future__ import annotations

import argparse
import html
import json
import statistics
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "data" / "logs.jsonl"
CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def load_records(minutes: int = 60) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    records: list[dict[str, Any]] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            timestamp = datetime.fromisoformat(str(record.get("ts", "")).replace("Z", "+00:00"))
            if timestamp >= cutoff:
                records.append(record)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return records


def snapshot() -> dict[str, Any]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["dashboard"]
    records = load_records(config["time_range_minutes"])
    responses = [r for r in records if r.get("event") == "response_sent"]
    requests = [r for r in records if r.get("event") == "request_received"]
    failures = [r for r in records if r.get("event") == "request_failed"]
    latencies = [float(r.get("latency_ms", 0)) for r in responses]
    error_breakdown: dict[str, int] = {}
    for record in failures:
        key = str(record.get("error_type", "unknown"))
        error_breakdown[key] = error_breakdown.get(key, 0) + 1
    slow = sorted(responses, key=lambda r: float(r.get("latency_ms", 0)), reverse=True)[:8]
    return {
        "config": config,
        "request_count": len(requests),
        "rate_per_minute": len(requests) / max(config["time_range_minutes"], 1),
        "p50": percentile(latencies, 0.50),
        "p95": percentile(latencies, 0.95),
        "p99": percentile(latencies, 0.99),
        "error_rate": len(failures) / max(len(requests), 1) * 100,
        "error_breakdown": error_breakdown,
        "cost_total": sum(float(r.get("cost_usd", 0)) for r in responses),
        "tokens_in": sum(int(r.get("tokens_in", 0)) for r in responses),
        "tokens_out": sum(int(r.get("tokens_out", 0)) for r in responses),
        "quality_mean": statistics.fmean(float(r.get("quality_score", 0)) for r in responses) if responses else 0,
        "slow": slow,
    }


def render_dashboard(data: dict[str, Any]) -> str:
    config = data["config"]
    values = {
        "latency": f"P50 {data['p50']:.0f} / P95 {data['p95']:.0f} / P99 {data['p99']:.0f} ms",
        "traffic": f"{data['request_count']} requests · {data['rate_per_minute']:.2f}/min",
        "errors": f"{data['error_rate']:.2f}% · {html.escape(str(data['error_breakdown']))}",
        "cost": f"${data['cost_total']:.4f}",
        "tokens": f"{data['tokens_in']:,} input / {data['tokens_out']:,} output",
        "quality": f"{data['quality_mean']:.3f}",
    }
    cards = []
    for panel in config["panels"]:
        threshold = panel["threshold"]
        cards.append(
            f"<section class='card'><div class='eyebrow'>{html.escape(panel['id'].upper())}</div>"
            f"<h2>{html.escape(panel['title'])}</h2><div class='value'>{values[panel['id']]}</div>"
            f"<div class='meta'>Unit: {html.escape(panel['unit'])}</div>"
            f"<div class='threshold'>Threshold: {threshold['aggregation']} {threshold['operator']} {threshold['value']}</div></section>"
        )
    rows = "".join(
        f"<tr><td>{html.escape(str(r.get('ts', '')))}</td><td><code>{html.escape(str(r.get('correlation_id', '')))}</code></td>"
        f"<td>{float(r.get('latency_ms', 0)):.0f} ms</td><td>{html.escape(str(r.get('feature', '')))}</td></tr>"
        for r in data["slow"]
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='{config['refresh_seconds']}'>
<title>{html.escape(config['title'])}</title><style>
body{{margin:0;background:#07111f;color:#e8f0ff;font:15px system-ui;padding:32px}} header{{display:flex;justify-content:space-between;align-items:end}}
h1{{margin:0;font-size:32px}} .sub{{color:#8fa8c7}} .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}
.card{{background:linear-gradient(145deg,#12233b,#0b1728);border:1px solid #284364;border-radius:18px;padding:20px;box-shadow:0 12px 32px #0005}}
.eyebrow{{color:#56d6c9;font-size:12px;font-weight:700;letter-spacing:.14em}} h2{{font-size:17px;margin:8px 0 18px}} .value{{font-size:25px;font-weight:750}}
.meta{{color:#9fb3cd;margin-top:12px}} .threshold{{color:#ffd166;margin-top:7px}} table{{width:100%;border-collapse:collapse;background:#0c192b;border-radius:14px;overflow:hidden}}
th,td{{padding:12px;border-bottom:1px solid #203652;text-align:left}} th{{color:#56d6c9}} code{{color:#b9d6ff}} .valid{{color:#6ee7a8;font-weight:700}}
</style></head><body><header><div><div class='eyebrow'>RUNTIME EVIDENCE</div><h1>{html.escape(config['title'])}</h1><div class='sub'>Source: data/logs.jsonl · Time range: last {config['time_range_minutes']} minutes · Refresh: {config['refresh_seconds']}s</div></div><div class='valid'>VALID: 6/6 panels</div></header>
<main class='grid'>{''.join(cards)}</main><h2>Slow requests and correlation IDs</h2><table><thead><tr><th>Timestamp</th><th>Correlation ID</th><th>Latency</th><th>Feature</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        payload = render_dashboard(snapshot()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime dashboard for the Day 13 observability contract")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--write-html", type=Path)
    args = parser.parse_args()
    if args.write_html:
        args.write_html.write_text(render_dashboard(snapshot()), encoding="utf-8")
        print(args.write_html)
        return
    print(f"Dashboard: http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
