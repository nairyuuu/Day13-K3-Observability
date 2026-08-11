"""Checkpoint 2 — bảo đảm không có PII nào rời khỏi app qua đường trace Langfuse."""
from __future__ import annotations

import hashlib
import json
import re

from app import agent as agent_module
from app.pii import hash_user_id

# Cùng bộ detector với scripts/validate_logs.py: trace phải sạch theo đúng
# tiêu chuẩn mà log đang bị chấm.
PII_DETECTORS = {
    "email": re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    "phone_vn": re.compile(r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)"),
    "cccd": re.compile(r"\b\d{12}\b"),
    "credit_card": re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
}

DIRTY_MESSAGE = (
    "Mail toi la student@vinuni.edu.vn, so dien thoai 0987654321, "
    "CCCD 079201001234 va the 4111 1111 1111 1111"
)
RAW_USER_ID = "u01"


class ManagedPrompt:
    version = 2

    def compile(self, **variables: str) -> str:
        return f"Feature={variables['feature']}\nQuestion={variables['message']}"


class RecordingLangfuseClient:
    def __init__(self) -> None:
        self.prompt = ManagedPrompt()
        self.trace_updates: list[dict] = []
        self.generation_updates: list[dict] = []

    def get_prompt(self, name: str, **kwargs):
        return self.prompt

    def update_current_trace(self, **kwargs) -> None:
        self.trace_updates.append(kwargs)

    def update_current_generation(self, **kwargs) -> None:
        self.generation_updates.append(kwargs)


def _run_agent(monkeypatch, **overrides):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-66666666-7777-8888-9999-000000000000")
    client = RecordingLangfuseClient()
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)

    payload = {
        "user_id": RAW_USER_ID,
        "feature": "qa",
        "session_id": "s01",
        "message": DIRTY_MESSAGE,
    }
    payload.update(overrides)
    agent = agent_module.LabAgent()
    # __wrapped__ để bỏ qua decorator @observe (không cần Langfuse thật khi test).
    agent_module.LabAgent.run.__wrapped__(agent, **payload)
    return client


def _sent_payload(client: RecordingLangfuseClient) -> str:
    return json.dumps(
        {"trace": client.trace_updates, "generation": client.generation_updates},
        ensure_ascii=False,
        default=repr,
    )


def test_no_pii_reaches_langfuse_trace_or_generation(monkeypatch) -> None:
    client = _run_agent(monkeypatch)
    sent = _sent_payload(client)

    leaked = sorted(name for name, det in PII_DETECTORS.items() if det.search(sent))
    assert leaked == [], f"PII bi gui len Langfuse: {leaked}\n{sent}"
    for fragment in ("student@vinuni", "0987654321", "079201001234", "4111"):
        assert fragment not in sent


def test_trace_user_id_is_hashed_not_raw(monkeypatch) -> None:
    client = _run_agent(monkeypatch)
    trace = client.trace_updates[-1]

    expected = hashlib.sha256(RAW_USER_ID.encode("utf-8")).hexdigest()[:12]
    assert trace["user_id"] == expected
    assert len(trace["user_id"]) == 12
    assert trace["user_id"] != RAW_USER_ID


def test_observe_decorator_does_not_capture_input_or_output() -> None:
    # @observe giữ kwargs trên __closure__ không truy cập được, nên kiểm tra ở mức
    # source: decorator phải khai báo tắt cả hai chiều capture.
    import inspect

    source = inspect.getsource(agent_module.LabAgent)
    assert "capture_input=False" in source
    assert "capture_output=False" in source


def test_pii_in_client_controlled_tags_and_session_id_is_scrubbed(monkeypatch) -> None:
    # feature trở thành tag được index trên Langfuse; session_id do client gửi lên.
    client = _run_agent(
        monkeypatch,
        feature="lien he 0987654321",
        session_id="phien-cua-student@vinuni.edu.vn",
        message="cau hoi binh thuong",
    )
    trace = client.trace_updates[-1]

    assert "0987654321" not in json.dumps(trace["tags"], ensure_ascii=False)
    assert "REDACTED_PHONE_VN" in json.dumps(trace["tags"], ensure_ascii=False)
    assert "student@vinuni.edu.vn" not in trace["session_id"]


def test_langfuse_api_keys_are_redacted_if_they_ever_reach_a_payload() -> None:
    from app.pii import scrub_text

    out = scrub_text(
        "auth failed for pk-lf-98f62af0-c1a6-425f-8925-141ea0c36ec1 / "
        "sk-lf-52968a0c-738b-491f-8bbf-55750b543322"
    )
    assert "pk-lf-98f62af0" not in out
    assert "sk-lf-52968a0c" not in out
    assert out.count("[REDACTED_API_KEY]") == 2
