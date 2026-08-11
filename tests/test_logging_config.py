from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import structlog

from app import logging_config


def test_jsonl_file_processor_creates_parent_and_appends_utf8(
    monkeypatch, tmp_path
) -> None:
    log_path = tmp_path / "nested" / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    processor = logging_config.JsonlFileProcessor()
    message = "Xin chao, \u0110\u00e0 N\u1eb5ng"
    answer = "Ho\u00e0n t\u1ea5t"

    processor(
        None,
        "info",
        {
            "event": "request_received",
            "service": "api",
            "correlation_id": "req-12345678",
            "payload": {"message": message},
        },
    )
    processor(
        None,
        "info",
        {
            "event": "response_sent",
            "service": "api",
            "correlation_id": "req-12345678",
            "payload": {"answer": answer},
        },
    )

    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert log_path.parent.exists()
    assert [record["event"] for record in records] == [
        "request_received",
        "response_sent",
    ]
    assert records[0]["payload"]["message"] == message
    assert records[1]["payload"]["answer"] == answer


def test_jsonl_file_processor_keeps_concurrent_lines_valid(
    monkeypatch, tmp_path
) -> None:
    log_path = tmp_path / "logs" / "concurrent.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    processor = logging_config.JsonlFileProcessor()

    def write_record(index: int) -> None:
        processor(
            None,
            "info",
            {
                "event": "concurrent_write",
                "service": "api",
                "correlation_id": f"req-{index:08x}"[-12:],
                "payload": {"index": index},
            },
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_record, range(50)))

    lines = log_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 50
    assert sorted(record["payload"]["index"] for record in records) == list(range(50))


def test_configured_timestamp_is_iso_utc() -> None:
    timestamper = structlog.processors.TimeStamper(
        fmt="iso",
        utc=True,
        key="ts",
    )

    event = timestamper(None, "info", {})
    parsed = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
