from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.dashboard_app import (
    aggregate_dashboard,
    compare_threshold,
    detect_latency_anomalies,
    load_contract,
    panel_map,
    parse_timestamp,
    read_jsonl,
    records_in_window,
)


UTC = timezone.utc


def record(ts: datetime, event: str, **values: object) -> dict[str, object]:
    return {"_timestamp": ts, "event": event, **values}


def test_parse_timestamp_normalizes_utc() -> None:
    parsed = parse_timestamp("2026-08-11T10:00:00+07:00")

    assert parsed == datetime(2026, 8, 11, 3, 0, tzinfo=UTC)
    assert parse_timestamp("not-a-timestamp") is None


def test_read_jsonl_skips_malformed_and_missing_timestamps(tmp_path: Path) -> None:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-08-11T03:00:00Z", "event": "request_received"}),
                "{partial",
                json.dumps({"event": "response_sent"}),
            ]
        ),
        encoding="utf-8",
    )

    result = read_jsonl(path)

    assert len(result.records) == 1
    assert result.total_lines == 3
    assert result.invalid_lines == 1
    assert result.invalid_timestamps == 1


def test_window_is_bounded_on_both_sides() -> None:
    anchor = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
    records = [
        record(anchor - timedelta(minutes=61), "request_received"),
        record(anchor - timedelta(minutes=30), "request_received"),
        record(anchor + timedelta(seconds=1), "request_received"),
    ]

    assert records_in_window(records, anchor, 60) == [records[1]]


def test_aggregation_matches_six_panel_contract() -> None:
    anchor = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
    records = [
        record(anchor - timedelta(minutes=2), "request_received", correlation_id="ok"),
        record(anchor - timedelta(minutes=1), "request_received", correlation_id="bad"),
        record(
            anchor - timedelta(minutes=1),
            "request_failed",
            correlation_id="bad",
            error_type="TimeoutError",
        ),
        record(
            anchor - timedelta(minutes=2),
            "response_sent",
            correlation_id="ok",
            latency_ms=100,
            cost_usd=0.2,
            tokens_in=10,
            tokens_out=20,
            quality_score=0.8,
        ),
    ]
    panels = panel_map(load_contract())

    data = aggregate_dashboard(records, anchor, 60, panels)

    assert data["latency"]["p95"] == 100
    assert data["traffic"]["request_count"] == 2
    assert data["traffic"]["rate_per_minute"] == 2 / 60
    assert data["errors"]["rate_pct"] == 50
    assert data["errors"]["breakdown"] == {"TimeoutError": 1}
    assert data["cost"]["total"] == 0.2
    assert data["tokens"]["combined_total"] == 30
    assert data["quality"]["mean"] == 0.8


def test_latency_anomaly_and_threshold_direction() -> None:
    anchor = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
    records = [
        record(
            anchor,
            "response_sent",
            correlation_id=f"req-{index}",
            latency_ms=latency,
        )
        for index, latency in enumerate([100, 100, 101, 102, 900])
    ]

    anomalies, cutoff = detect_latency_anomalies(records, 3000)

    assert cutoff < 900
    assert [item["correlation_id"] for item in anomalies] == ["req-4"]
    assert compare_threshold(2, {"operator": "lte", "value": 2}) is True
    assert compare_threshold(0.7, {"operator": "gte", "value": 0.75}) is False
    assert compare_threshold(None, {"operator": "gte", "value": 0.75}) is None
