from __future__ import annotations

import html
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONFIG_PATH = REPO_ROOT / "config" / "dashboard.yaml"
UTC = timezone.utc


@dataclass(frozen=True)
class LogReadResult:
    records: tuple[dict[str, Any], ...]
    total_lines: int
    invalid_lines: int
    invalid_timestamps: int
    modified_at: datetime | None


def load_contract(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the same contract used by the checkpoint validator."""
    from scripts.validate_dashboard import load_dashboard_config

    return load_dashboard_config(path)["dashboard"]


def resolve_source(source: str) -> Path:
    path = Path(source)
    return path if path.is_absolute() else REPO_ROOT / path


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def read_jsonl(path: Path) -> LogReadResult:
    """Read an append-only JSONL file without failing on a partial final line."""
    records: list[dict[str, Any]] = []
    total_lines = 0
    invalid_lines = 0
    invalid_timestamps = 0

    if not path.exists():
        return LogReadResult((), 0, 0, 0, None)

    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            if not raw_line.strip():
                continue
            total_lines += 1
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if not isinstance(record, dict):
                invalid_lines += 1
                continue

            timestamp = parse_timestamp(record.get("ts"))
            if timestamp is None:
                invalid_timestamps += 1
                continue
            normalized = dict(record)
            normalized["_timestamp"] = timestamp
            records.append(normalized)

    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return LogReadResult(
        tuple(records), total_lines, invalid_lines, invalid_timestamps, modified_at
    )


def records_in_window(
    records: Iterable[dict[str, Any]], anchor: datetime, minutes: int
) -> list[dict[str, Any]]:
    anchor = anchor.astimezone(UTC)
    start = anchor - timedelta(minutes=minutes)
    return [
        record
        for record in records
        if isinstance(record.get("_timestamp"), datetime)
        and start <= record["_timestamp"] <= anchor
    ]


def minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def minute_axis(anchor: datetime, minutes: int) -> list[datetime]:
    start = minute_floor(anchor - timedelta(minutes=minutes))
    end = minute_floor(anchor)
    count = int((end - start).total_seconds() // 60)
    return [start + timedelta(minutes=index) for index in range(count + 1)]


def numeric(record: dict[str, Any], field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def percentile(values: Iterable[float], percent: int) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def group_by_minute(
    records: Iterable[dict[str, Any]], event: str
) -> dict[datetime, list[dict[str, Any]]]:
    grouped: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("event") == event:
            grouped[minute_floor(record["_timestamp"])].append(record)
    return grouped


def compare_threshold(value: float | None, threshold: dict[str, Any]) -> bool | None:
    if value is None:
        return None
    target = float(threshold["value"])
    if threshold["operator"] == "lte":
        return value <= target
    return value >= target


def detect_latency_anomalies(
    records: Iterable[dict[str, Any]],
    slo_ms: float,
    event: str = "response_sent",
    field: str = "latency_ms",
) -> tuple[list[dict[str, Any]], float]:
    latency_records = [
        record
        for record in records
        if record.get("event") == event and numeric(record, field) is not None
    ]
    values = [numeric(record, field) for record in latency_records]
    clean_values = [value for value in values if value is not None]
    dynamic_cutoff = slo_ms

    if len(clean_values) >= 5:
        center = median(clean_values)
        mad = median(abs(value - center) for value in clean_values)
        robust_cutoff = center + (4.5 * 1.4826 * mad if mad else max(center, 250.0))
        dynamic_cutoff = min(slo_ms, robust_cutoff)

    anomalies = [
        record
        for record in latency_records
        if (numeric(record, field) or 0) > dynamic_cutoff
    ]
    anomalies.sort(key=lambda item: numeric(item, field) or 0, reverse=True)
    return anomalies, dynamic_cutoff


def aggregate_dashboard(
    records: list[dict[str, Any]], anchor: datetime, minutes: int, panels: dict[str, Any]
) -> dict[str, Any]:
    axis = minute_axis(anchor, minutes)
    latency_event = panels["latency"]["events"][0]
    latency_field = panels["latency"]["fields"][0]
    traffic_event = panels["traffic"]["events"][0]
    error_base_event, error_event = panels["errors"]["events"][:2]
    error_field = panels["errors"]["fields"][0]
    cost_event = panels["cost"]["events"][0]
    cost_field = panels["cost"]["fields"][0]
    token_event = panels["tokens"]["events"][0]
    token_input_field, token_output_field = panels["tokens"]["fields"][:2]
    quality_event = panels["quality"]["events"][0]
    quality_field = panels["quality"]["fields"][0]

    latency_groups = group_by_minute(records, latency_event)
    traffic_groups = group_by_minute(records, traffic_event)
    cost_groups = group_by_minute(records, cost_event)
    token_groups = group_by_minute(records, token_event)
    quality_groups = group_by_minute(records, quality_event)

    latency_records = [r for r in records if r.get("event") == latency_event]
    traffic_records = [r for r in records if r.get("event") == traffic_event]
    error_base_records = [r for r in records if r.get("event") == error_base_event]
    failed_records = [r for r in records if r.get("event") == error_event]
    cost_records = [r for r in records if r.get("event") == cost_event]
    token_records = [r for r in records if r.get("event") == token_event]
    quality_records = [r for r in records if r.get("event") == quality_event]

    latency_values = [
        value
        for record in latency_records
        if (value := numeric(record, latency_field)) is not None
    ]
    latency_series = {label: [] for label in ("p50", "p95", "p99")}
    for bucket in axis:
        values = [
            value
            for record in latency_groups.get(bucket, [])
            if (value := numeric(record, latency_field)) is not None
        ]
        for percent in (50, 95, 99):
            latency_series[f"p{percent}"].append(percentile(values, percent))

    traffic_counts = [len(traffic_groups.get(bucket, [])) for bucket in axis]
    error_breakdown = Counter(
        str(record.get(error_field) or "unknown") for record in failed_records
    )

    minute_cost: list[float] = []
    cumulative_cost: list[float] = []
    running_cost = 0.0
    token_input: list[float] = []
    token_output: list[float] = []
    quality_by_minute: list[float | None] = []

    for bucket in axis:
        cost_bucket = cost_groups.get(bucket, [])
        token_bucket = token_groups.get(bucket, [])
        quality_bucket = quality_groups.get(bucket, [])
        cost = sum(numeric(record, cost_field) or 0 for record in cost_bucket)
        running_cost += cost
        minute_cost.append(cost)
        cumulative_cost.append(running_cost)
        token_input.append(
            sum(numeric(record, token_input_field) or 0 for record in token_bucket)
        )
        token_output.append(
            sum(numeric(record, token_output_field) or 0 for record in token_bucket)
        )
        scores = [
            score
            for record in quality_bucket
            if (score := numeric(record, quality_field)) is not None
        ]
        quality_by_minute.append(sum(scores) / len(scores) if scores else None)

    quality_values = [
        score
        for record in quality_records
        if (score := numeric(record, quality_field)) is not None
    ]
    input_total = sum(
        numeric(record, token_input_field) or 0 for record in token_records
    )
    output_total = sum(
        numeric(record, token_output_field) or 0 for record in token_records
    )
    total_cost = sum(numeric(record, cost_field) or 0 for record in cost_records)
    latency_anomalies, dynamic_cutoff = detect_latency_anomalies(
        records,
        float(panels["latency"]["threshold"]["value"]),
        latency_event,
        latency_field,
    )

    return {
        "axis": axis,
        "latency": {
            **{f"p{p}": percentile(latency_values, p) for p in (50, 95, 99)},
            "series": latency_series,
            "anomalies": latency_anomalies,
            "anomaly_cutoff": dynamic_cutoff,
        },
        "traffic": {
            "request_count": len(traffic_records),
            "rate_per_minute": len(traffic_records) / minutes,
            "series": traffic_counts,
        },
        "errors": {
            "count": len(failed_records),
            "rate_pct": (
                len(failed_records) / len(error_base_records) * 100
                if error_base_records
                else None
            ),
            "breakdown": dict(error_breakdown),
            "records": sorted(
                failed_records, key=lambda item: item["_timestamp"], reverse=True
            ),
        },
        "cost": {
            "total": total_cost,
            "count": sum(numeric(record, cost_field) is not None for record in cost_records),
            "minute": minute_cost,
            "cumulative": cumulative_cost,
        },
        "tokens": {
            "input_total": input_total,
            "output_total": output_total,
            "combined_total": input_total + output_total,
            "count": sum(
                numeric(record, token_input_field) is not None
                or numeric(record, token_output_field) is not None
                for record in token_records
            ),
            "input_series": token_input,
            "output_series": token_output,
        },
        "quality": {
            "mean": sum(quality_values) / len(quality_values) if quality_values else None,
            "series": quality_by_minute,
            "count": len(quality_values),
        },
    }


def panel_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {panel["id"]: panel for panel in contract["panels"]}


def format_number(value: float | None, decimals: int = 1) -> str:
    return "—" if value is None else f"{value:,.{decimals}f}"


def status_label(status: bool | None) -> tuple[str, str]:
    if status is True:
        return "Within target", "healthy"
    if status is False:
        return "Threshold breached", "critical"
    return "No data", "neutral"


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "No events"
    return value.astimezone().strftime("%d %b %Y · %H:%M:%S %Z")


def inject_styles(st: Any) -> None:
    st.markdown(
        """
        <style>
        :root {
            --surface: rgba(15, 23, 42, .72);
            --border: rgba(148, 163, 184, .16);
            --muted: #94a3b8;
            --cyan: #22d3ee;
            --violet: #a78bfa;
        }
        .stApp {
            background:
              radial-gradient(circle at 12% 8%, rgba(34,211,238,.11), transparent 25rem),
              radial-gradient(circle at 88% 4%, rgba(139,92,246,.14), transparent 28rem),
              linear-gradient(145deg, #050816 0%, #090d1c 46%, #07111d 100%);
            color: #e2e8f0;
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] {
            background: rgba(5, 8, 22, .88);
            border-right: 1px solid var(--border);
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: linear-gradient(145deg, rgba(15,23,42,.76), rgba(15,23,42,.48));
            border: 1px solid var(--border);
            border-radius: 20px;
            box-shadow: 0 18px 45px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.035);
            backdrop-filter: blur(18px);
        }
        .block-container { max-width: 1600px; padding-top: 2rem; padding-bottom: 3rem; }
        .hero-kicker { color: var(--cyan); font-size: .76rem; font-weight: 800; letter-spacing: .18em; text-transform: uppercase; }
        .hero-title { font-size: clamp(2rem, 4vw, 3.4rem); line-height: 1.05; font-weight: 820; letter-spacing: -.045em; margin: .35rem 0 .7rem; }
        .hero-title span { background: linear-gradient(90deg, #67e8f9, #c4b5fd); -webkit-background-clip: text; color: transparent; }
        .hero-subtitle { color: var(--muted); max-width: 760px; font-size: .98rem; }
        .live-pill { display: inline-flex; align-items: center; gap: .45rem; padding: .35rem .7rem; border: 1px solid rgba(52,211,153,.28); border-radius: 999px; color: #6ee7b7; background: rgba(16,185,129,.09); font-size: .76rem; font-weight: 750; }
        .live-dot { width: .48rem; height: .48rem; border-radius: 50%; background: #34d399; box-shadow: 0 0 0 .25rem rgba(52,211,153,.10); }
        .panel-eyebrow { color: var(--muted); font-size: .70rem; letter-spacing: .13em; text-transform: uppercase; font-weight: 750; }
        .panel-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:1rem; margin-bottom:.1rem; }
        .panel-title { font-size: 1.07rem; font-weight: 750; margin-top: .2rem; }
        .status { white-space:nowrap; padding:.26rem .55rem; border-radius:999px; font-size:.68rem; font-weight:780; border:1px solid; }
        .status.healthy { color:#6ee7b7; border-color:rgba(52,211,153,.30); background:rgba(16,185,129,.09); }
        .status.critical { color:#fda4af; border-color:rgba(251,113,133,.32); background:rgba(244,63,94,.10); }
        .status.neutral { color:#cbd5e1; border-color:rgba(148,163,184,.25); background:rgba(148,163,184,.08); }
        .metric-value { font-size:1.85rem; line-height:1.2; font-weight:800; letter-spacing:-.04em; color:#f8fafc; }
        .metric-caption { color:var(--muted); font-size:.74rem; }
        .threshold-note { color:#94a3b8; font-size:.73rem; margin-top:.15rem; }
        .correlation-label { color:#fbbf24; font-size:.75rem; font-weight:760; letter-spacing:.06em; text-transform:uppercase; margin-top:.4rem; }
        div[data-testid="stCode"] { border: 1px solid rgba(245,158,11,.20); }
        div[data-testid="stPlotlyChart"] { border-radius: 14px; overflow: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def chart_layout(fig: Any, height: int = 315) -> Any:
    fig.update_layout(
        height=height,
        margin=dict(l=12, r=12, t=22, b=12),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1", family="Inter, ui-sans-serif, system-ui"),
        hoverlabel=dict(bgcolor="#111827", bordercolor="#334155", font_color="#f8fafc"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(gridcolor="rgba(148,163,184,.08)", showline=False),
        yaxis=dict(gridcolor="rgba(148,163,184,.10)", zeroline=False),
        hovermode="x unified",
    )
    return fig


def add_threshold(fig: Any, value: float, label: str) -> None:
    fig.add_hline(
        y=value,
        line_color="#fb7185",
        line_dash="dash",
        line_width=1.6,
        annotation_text=label,
        annotation_position="top right",
        annotation_font_color="#fda4af",
    )


def render_panel_header(
    st: Any,
    panel: dict[str, Any],
    status: bool | None,
    eyebrow: str,
) -> None:
    label, css_class = status_label(status)
    st.markdown(
        f"""
        <div class="panel-heading">
          <div><div class="panel-eyebrow">{html.escape(eyebrow)}</div>
          <div class="panel-title">{html.escape(panel['title'])}</div></div>
          <span class="status {css_class}">{label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metric(st: Any, value: str, caption: str) -> None:
    st.markdown(
        f'<div class="metric-value">{html.escape(value)}</div>'
        f'<div class="metric-caption">{html.escape(caption)}</div>',
        unsafe_allow_html=True,
    )


def render_correlation_ids(st: Any, records: list[dict[str, Any]], kind: str) -> None:
    ids = list(
        dict.fromkeys(
            str(record.get("correlation_id"))
            for record in records
            if record.get("correlation_id")
        )
    )
    if not ids:
        return
    visible = ids[:20]
    st.markdown(
        f'<div class="correlation-label">Investigation · {len(ids)} {html.escape(kind)}</div>',
        unsafe_allow_html=True,
    )
    st.caption("Copy a correlation ID to find the matching log and trace.")
    st.code("\n".join(visible), language=None)
    if len(ids) > len(visible):
        st.caption(f"Showing 20 of {len(ids)} correlation IDs.")


def render_latency(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = compare_threshold(data["p95"], threshold)
    with st.container(border=True):
        render_panel_header(st, panel, status, "Performance · P50 / P95 / P99")
        columns = st.columns(3)
        for column, key in zip(columns, ("p50", "p95", "p99")):
            with column:
                render_metric(st, f"{format_number(data[key], 0)} ms", key.upper())

        colors = {"p50": "#22d3ee", "p95": "#a78bfa", "p99": "#fbbf24"}
        figure = go.Figure()
        for key in ("p50", "p95", "p99"):
            figure.add_trace(
                go.Scatter(
                    x=data["axis"], y=data["series"][key], name=key.upper(),
                    mode="lines+markers", line=dict(color=colors[key], width=2.4),
                    marker=dict(size=4), connectgaps=False,
                    hovertemplate=f"{key.upper()}: %{{y:,.0f}} ms<extra></extra>",
                )
            )
        add_threshold(figure, float(threshold["value"]), f"P95 SLO · {threshold['value']:,.0f} ms")
        figure.update_yaxes(title_text="Latency (ms)", rangemode="tozero")
        st.plotly_chart(chart_layout(figure), use_container_width=True, config=PLOT_CONFIG, key="latency_chart")
        cutoff = data["anomaly_cutoff"]
        st.markdown(
            f'<div class="threshold-note">SLO P95 ≤ {threshold["value"]:,.0f} ms · anomaly cutoff {cutoff:,.0f} ms</div>',
            unsafe_allow_html=True,
        )
        render_correlation_ids(st, data["anomalies"], "latency spikes")


def render_traffic(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = compare_threshold(data["rate_per_minute"], threshold)
    with st.container(border=True):
        render_panel_header(st, panel, status, "Throughput · Requests / minute")
        left, right = st.columns(2)
        with left:
            render_metric(st, format_number(data["rate_per_minute"], 2), "Average requests / minute")
        with right:
            render_metric(st, f"{data['request_count']:,}", "Requests in window")
        figure = go.Figure(
            go.Scatter(
                x=data["axis"], y=data["series"], name="Requests/min",
                mode="lines", line=dict(color="#22d3ee", width=2.6),
                fill="tozeroy", fillcolor="rgba(34,211,238,.12)",
                hovertemplate="%{y:,.0f} requests<extra></extra>",
            )
        )
        add_threshold(figure, float(threshold["value"]), f"Target ≥ {threshold['value']:g} rpm")
        figure.update_yaxes(title_text="requests_per_minute", rangemode="tozero")
        st.plotly_chart(chart_layout(figure), use_container_width=True, config=PLOT_CONFIG, key="traffic_chart")
        st.markdown(
            f'<div class="threshold-note">Window-average target ≥ {threshold["value"]:g} request/minute</div>',
            unsafe_allow_html=True,
        )


def render_errors(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = compare_threshold(data["rate_pct"], threshold)
    with st.container(border=True):
        render_panel_header(st, panel, status, "Reliability · Rate & error type")
        left, right = st.columns(2)
        with left:
            render_metric(st, f"{format_number(data['rate_pct'], 2)}%", "Error rate")
        with right:
            render_metric(st, f"{data['count']:,}", "Failed requests")
        breakdown = data["breakdown"] or {"No errors": 0}
        colors = ["#fb7185", "#f59e0b", "#a78bfa", "#38bdf8", "#64748b"]
        figure = go.Figure(
            go.Bar(
                x=list(breakdown.keys()), y=list(breakdown.values()), name="Errors",
                marker=dict(color=colors[: len(breakdown)], line=dict(width=0)),
                hovertemplate="%{x}: %{y:,.0f}<extra></extra>",
            )
        )
        figure.update_yaxes(title_text="Error count", rangemode="tozero", dtick=1)
        st.plotly_chart(chart_layout(figure), use_container_width=True, config=PLOT_CONFIG, key="errors_chart")
        st.markdown(
            f'<div class="threshold-note">Error rate = failed / received × 100 · target ≤ {threshold["value"]:g}%</div>',
            unsafe_allow_html=True,
        )
        render_correlation_ids(st, data["records"], "request errors")


def render_cost(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = compare_threshold(data["total"], threshold) if data["count"] else None
    with st.container(border=True):
        render_panel_header(st, panel, status, "Spend · Per minute & cumulative")
        render_metric(st, f"${format_number(data['total'], 4)}", "Total cost in window (USD)")
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=data["axis"], y=data["minute"], name="Per minute",
                mode="lines", line=dict(color="#22d3ee", width=2.2),
                fill="tozeroy", fillcolor="rgba(34,211,238,.08)",
                hovertemplate="$%{y:,.5f}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=data["axis"], y=data["cumulative"], name="Cumulative",
                mode="lines", line=dict(color="#a78bfa", width=2.7),
                hovertemplate="$%{y:,.5f}<extra></extra>",
            )
        )
        add_threshold(figure, float(threshold["value"]), f"Budget · ${threshold['value']:g}")
        figure.update_yaxes(title_text="Cost (USD)", tickprefix="$", rangemode="tozero")
        st.plotly_chart(chart_layout(figure), use_container_width=True, config=PLOT_CONFIG, key="cost_chart")
        st.markdown(
            f'<div class="threshold-note">Cumulative budget ≤ ${threshold["value"]:g} per {data["window_minutes"]}-minute window</div>',
            unsafe_allow_html=True,
        )


def render_tokens(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = (
        compare_threshold(data["combined_total"], threshold) if data["count"] else None
    )
    with st.container(border=True):
        render_panel_header(st, panel, status, "Usage · Input vs output")
        columns = st.columns(3)
        values = (
            (data["input_total"], "Input tokens"),
            (data["output_total"], "Output tokens"),
            (data["combined_total"], "Combined"),
        )
        for column, (value, caption) in zip(columns, values):
            with column:
                render_metric(st, f"{value:,.0f}", caption)
        figure = go.Figure()
        figure.add_trace(
            go.Bar(
                x=data["axis"], y=data["input_series"], name="Input",
                marker_color="#22d3ee", hovertemplate="Input: %{y:,.0f}<extra></extra>",
            )
        )
        figure.add_trace(
            go.Bar(
                x=data["axis"], y=data["output_series"], name="Output",
                marker_color="#a78bfa", hovertemplate="Output: %{y:,.0f}<extra></extra>",
            )
        )
        figure.update_layout(barmode="stack")
        figure.update_yaxes(title_text="Tokens", rangemode="tozero")
        st.plotly_chart(chart_layout(figure), use_container_width=True, config=PLOT_CONFIG, key="tokens_chart")
        st.markdown(
            f'<div class="threshold-note">Combined input + output target ≤ {threshold["value"]:,.0f} tokens</div>',
            unsafe_allow_html=True,
        )


def render_quality(st: Any, go: Any, panel: dict[str, Any], data: dict[str, Any]) -> None:
    threshold = panel["threshold"]
    status = compare_threshold(data["mean"], threshold)
    with st.container(border=True):
        render_panel_header(st, panel, status, "Experience · Score 0 to 1")
        value = data["mean"] or 0
        gauge_color = "#34d399" if status else "#fb7185" if status is False else "#64748b"
        figure = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=value,
                number=dict(valueformat=".3f", font=dict(size=46, color="#f8fafc")),
                title=dict(text=f"Mean quality · {data['count']:,} responses", font=dict(size=14, color="#94a3b8")),
                gauge=dict(
                    axis=dict(range=[0, 1], tickwidth=1, tickcolor="#64748b"),
                    bar=dict(color=gauge_color, thickness=.32),
                    bgcolor="rgba(15,23,42,.45)", borderwidth=0,
                    steps=[
                        dict(range=[0, threshold["value"]], color="rgba(244,63,94,.10)"),
                        dict(range=[threshold["value"], 1], color="rgba(16,185,129,.09)"),
                    ],
                    threshold=dict(
                        line=dict(color="#fbbf24", width=4), thickness=.78,
                        value=threshold["value"],
                    ),
                ),
            )
        )
        figure.update_layout(
            height=315, margin=dict(l=35, r=35, t=55, b=18),
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1", family="Inter, ui-sans-serif, system-ui"),
        )
        st.plotly_chart(figure, use_container_width=True, config=PLOT_CONFIG, key="quality_chart")
        st.markdown(
            f'<div class="threshold-note">Mean quality target ≥ {threshold["value"]:.2f} score_0_to_1</div>',
            unsafe_allow_html=True,
        )


PLOT_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "scrollZoom": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}


def render_dashboard_body(
    st: Any,
    go: Any,
    contract: dict[str, Any],
    source_path: Path,
    anchor_mode: str,
) -> None:
    result = read_jsonl(source_path)
    window_minutes = int(contract["time_range_minutes"])
    dashboard_events = {
        event for panel in contract["panels"] for event in panel["events"]
    }
    relevant_records = tuple(
        record for record in result.records if record.get("event") in dashboard_events
    )
    latest = max(
        (record["_timestamp"] for record in relevant_records), default=None
    )
    anchor = (
        latest
        if anchor_mode == "Latest log event" and latest is not None
        else datetime.now(tz=UTC)
    )
    window_records = records_in_window(relevant_records, anchor, window_minutes)
    panels = panel_map(contract)
    data = aggregate_dashboard(window_records, anchor, window_minutes, panels)

    for key in ("latency", "traffic", "errors", "cost", "tokens", "quality"):
        data[key]["axis"] = data["axis"]
        data[key]["window_minutes"] = window_minutes

    header_left, header_right = st.columns([4, 1])
    with header_left:
        st.markdown(
            f"""
            <div class="hero-kicker">Checkpoint 2 · Metrics, traces & dashboard</div>
            <div class="hero-title">{html.escape(contract['title'])} <span>Control Room</span></div>
            <div class="hero-subtitle">A live view of performance, reliability, spend and response quality from the canonical JSONL event stream.</div>
            """,
            unsafe_allow_html=True,
        )
    with header_right:
        st.markdown(
            '<div class="live-pill"><span class="live-dot"></span>AUTO REFRESH</div>',
            unsafe_allow_html=True,
        )
        st.caption(f"Every {contract['refresh_seconds']} seconds")

    st.caption(
        f"Last {window_minutes} minutes · anchor {format_timestamp(anchor)} · "
        f"latest event {format_timestamp(latest)}"
    )

    if not source_path.exists():
        st.error(f"Log source not found: {source_path}")
    elif not window_records:
        st.warning(
            "No valid events were found in the selected 60-minute window. "
            "Run the load test, or use ‘Latest log event’ in the sidebar for historical evidence."
        )
    elif result.invalid_lines or result.invalid_timestamps:
        st.warning(
            f"Skipped {result.invalid_lines} malformed JSON line(s) and "
            f"{result.invalid_timestamps} record(s) with invalid timestamps."
        )

    row_one = st.columns(2, gap="large")
    with row_one[0]:
        render_latency(st, go, panels["latency"], data["latency"])
    with row_one[1]:
        render_traffic(st, go, panels["traffic"], data["traffic"])

    row_two = st.columns(2, gap="large")
    with row_two[0]:
        render_errors(st, go, panels["errors"], data["errors"])
    with row_two[1]:
        render_cost(st, go, panels["cost"], data["cost"])

    row_three = st.columns(2, gap="large")
    with row_three[0]:
        render_tokens(st, go, panels["tokens"], data["tokens"])
    with row_three[1]:
        render_quality(st, go, panels["quality"], data["quality"])

    st.caption(
        f"Source: {source_path.relative_to(REPO_ROOT)} · {len(window_records):,} events in window · "
        f"{result.total_lines:,} lines scanned · updated {format_timestamp(result.modified_at)}"
    )


def main() -> None:
    try:
        import plotly.graph_objects as go
        import streamlit as st
    except ImportError as exc:
        missing = exc.name or "dashboard dependency"
        raise SystemExit(
            f"Missing {missing}. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    st.set_page_config(
        page_title="AI Observability Control Room",
        page_icon="📡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles(st)

    try:
        contract = load_contract()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        st.error(f"Dashboard contract is invalid: {exc}")
        st.stop()

    panels = panel_map(contract)
    source_path = resolve_source(panels["latency"]["source"])

    with st.sidebar:
        st.markdown("### Control room")
        st.caption("The six panels are driven by config/dashboard.yaml.")
        anchor_mode = st.radio(
            "Time anchor",
            ("Live — current time", "Latest log event"),
            help="Live mode is the runtime view. Latest event is useful for reviewing older evidence.",
        )
        st.markdown(f"**Window**  \nLast {contract['time_range_minutes']} minutes")
        st.markdown(f"**Source**  \n`{panels['latency']['source']}`")
        st.markdown(f"**Refresh**  \n{contract['refresh_seconds']} seconds")
        if st.button("↻ Refresh now", use_container_width=True, type="primary"):
            st.rerun()
        st.divider()
        st.caption("Drag to zoom charts. Double-click to reset. Hover for exact values.")

    @st.fragment(run_every=timedelta(seconds=int(contract["refresh_seconds"])))
    def live_fragment() -> None:
        render_dashboard_body(st, go, contract, source_path, anchor_mode)

    live_fragment()


if __name__ == "__main__":
    main()
