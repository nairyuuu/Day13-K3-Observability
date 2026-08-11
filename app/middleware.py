from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

REQUEST_ID_RE = re.compile(r"^req-[0-9a-fA-F]{8}$")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        clear_contextvars()

        header_request_id = request.headers.get("x-request-id", "")
        if REQUEST_ID_RE.fullmatch(header_request_id):
            correlation_id = header_request_id.lower()
        else:
            correlation_id = f"req-{uuid.uuid4().hex[:8]}"

        bind_contextvars(correlation_id=correlation_id)
        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        response = await call_next(request)
        response_time_ms = int((time.perf_counter() - start) * 1000)

        response.headers["x-request-id"] = correlation_id
        response.headers["x-response-time-ms"] = str(response_time_ms)
        return response
