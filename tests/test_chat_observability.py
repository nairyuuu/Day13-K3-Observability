from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import logging_config
from app.main import app


REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}$")


def test_chat_response_log_exposes_quality_for_dashboard(
    monkeypatch, tmp_path: Path
) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)

    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={
                "user_id": "student-01",
                "session_id": "session-01",
                "feature": "qa",
                "message": "Explain observability",
            },
        )

    assert response.status_code == 200
    assert REQUEST_ID_RE.fullmatch(response.headers["x-request-id"])
    assert response.json()["correlation_id"] == response.headers["x-request-id"]

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    response_event = next(event for event in events if event["event"] == "response_sent")
    assert response_event["quality_score"] == response.json()["quality_score"]
    assert response_event["correlation_id"] == response.json()["correlation_id"]
    assert response_event["user_id_hash"]
    assert response_event["session_id"] == "session-01"
    assert response_event["feature"] == "qa"
    assert response_event["model"]
    assert response_event["env"] == "dev"
