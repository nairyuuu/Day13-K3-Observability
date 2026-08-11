"""Checkpoint 2 — trace và generation phải cùng mang đủ 4 field prompt versioning."""
from __future__ import annotations

import pytest

from app import agent as agent_module

PROMPT_FIELDS = ("prompt_name", "prompt_label", "prompt_version", "prompt_source")


class ManagedPrompt:
    version = 4

    def compile(self, **variables: str) -> str:
        return f"Feature={variables['feature']}\nQuestion={variables['message']}"


class RecordingClient:
    def __init__(self, prompt=None, error: Exception | None = None) -> None:
        self.prompt = prompt
        self.error = error
        self.trace_updates: list[dict] = []
        self.generation_updates: list[dict] = []

    def get_prompt(self, name: str, **kwargs):
        if self.error is not None:
            raise self.error
        return self.prompt

    def update_current_trace(self, **kwargs) -> None:
        self.trace_updates.append(kwargs)

    def update_current_generation(self, **kwargs) -> None:
        self.generation_updates.append(kwargs)


def _run(monkeypatch, client, *, name="day13-chat", label="production"):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_PROMPT_NAME", name)
    monkeypatch.setenv("LANGFUSE_PROMPT_LABEL", label)
    monkeypatch.setattr(agent_module, "get_langfuse_client", lambda: client)
    agent_module.LabAgent.run.__wrapped__(
        agent_module.LabAgent(),
        user_id="u01",
        feature="qa",
        session_id="s01",
        message="Explain observability",
    )
    return client


@pytest.mark.parametrize("label", ["baseline", "production", "candidate"])
def test_label_from_env_reaches_trace_and_generation(monkeypatch, label: str) -> None:
    client = _run(monkeypatch, RecordingClient(prompt=ManagedPrompt()), label=label)

    for metadata in (
        client.trace_updates[-1]["metadata"],
        client.generation_updates[-1]["metadata"],
    ):
        assert all(field in metadata for field in PROMPT_FIELDS)
        assert metadata["prompt_name"] == "day13-chat"
        assert metadata["prompt_label"] == label
        assert metadata["prompt_version"] == "4"
        assert metadata["prompt_source"] == "langfuse"


def test_generation_also_carries_fetch_error_and_links_managed_prompt(monkeypatch) -> None:
    prompt = ManagedPrompt()
    client = _run(monkeypatch, RecordingClient(prompt=prompt))
    generation = client.generation_updates[-1]

    assert generation["prompt"] is prompt
    assert generation["metadata"]["prompt_fetch_error"] is None


def test_fallback_is_visible_in_trace_metadata_not_disguised(monkeypatch) -> None:
    # Khi Langfuse offline, trace phải nói thật là local-fallback thay vì giả vờ
    # đã lấy được managed prompt — nếu không, evidence prompt version sẽ sai.
    client = _run(monkeypatch, RecordingClient(error=TimeoutError("langfuse down")))

    trace_metadata = client.trace_updates[-1]["metadata"]
    generation = client.generation_updates[-1]

    assert trace_metadata["prompt_source"] == "local-fallback"
    assert trace_metadata["prompt_version"] == "local-v1"
    assert generation["metadata"]["prompt_fetch_error"] == "TimeoutError"
    assert generation["prompt"] is None
