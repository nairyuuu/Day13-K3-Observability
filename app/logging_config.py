from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars

from .pii import scrub_value

LOG_PATH = Path(os.getenv("LOG_PATH", "data/logs.jsonl"))


class JsonlFileProcessor:
    _lock = threading.Lock()

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rendered = structlog.processors.JSONRenderer()(logger, method_name, event_dict)
        with self._lock:
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(rendered + "\n")
        return event_dict


def ensure_required_fields(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("service", os.getenv("APP_NAME", "day13-observability-lab"))
    event_dict.setdefault("correlation_id", "req-00000000")
    return event_dict


def scrub_event(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Che PII trên toàn bộ event trước khi nó được ghi ra file hoặc render.

    Call site đã scrub sẵn qua `summarize_text`, nhưng processor này là lớp phòng thủ
    cuối cùng nên không tin vào một danh sách field cố định: nó duyệt mọi key của
    event_dict (kể cả contextvars đã merge, `payload` lồng nhau và traceback do
    `format_exc_info` sinh ra) thay vì chỉ `event` + `payload`.
    """
    for key, value in list(event_dict.items()):
        event_dict[key] = scrub_value(value)
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")))
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            ensure_required_fields,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            # Đặt sau format_exc_info và ngay trước mọi processor ghi/render:
            # traceback và stack info cũng phải đi qua bộ lọc PII.
            scrub_event,
            JsonlFileProcessor(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger() -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger()
