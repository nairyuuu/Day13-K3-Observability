from __future__ import annotations

import hashlib
import re
from typing import Any

# Thứ tự quan trọng: pattern nào chạy trước sẽ "ăn" chuỗi trước.
# Xếp từ định danh dài/cụ thể nhất xuống ngắn nhất để thẻ tín dụng (13-16 chữ số)
# và CCCD (12 chữ số) không bị pattern số điện thoại cắt mất một phần.
PII_PATTERNS: dict[str, str] = {
    # Local part cho phép dấu "+" (user+tag@example.com).
    "email": r"[\w.+-]+@[\w.-]+\.\w+",
    # Thẻ 13-16 chữ số dạng 4-4-4-N, cộng thêm layout Amex 4-6-5.
    "credit_card": (
        r"(?<!\d)(?:"
        r"\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}"
        r"|\d{4}[ -]?\d{6}[ -]?\d{5}"
        r")(?!\d)"
    ),
    # CCCD: 12 chữ số liền hoặc nhóm 3-3-3-3.
    "cccd": r"(?<!\d)(?:\d{12}|\d{3}[ .-]\d{3}[ .-]\d{3}[ .-]\d{3})(?!\d)",
    # SĐT VN: tiền tố 0 / 84 / +84 / (+84), theo sau 9-11 chữ số, separator tùy chọn.
    "phone_vn": r"(?<!\d)(?:\(\+?84\)|\+?84|0)[ .-]?(?:\d[ .-]?){8,10}\d(?!\d)",
    # Hộ chiếu VN: 1 chữ cái in hoa + 7 chữ số (B1234567).
    "passport": r"\b[A-Z]\d{7}\b",
    # CMND đời cũ: 9 chữ số.
    "cmnd": r"(?<!\d)\d{9}(?!\d)",
    # Cố ý không match từ khóa địa chỉ (đường/phường/quận): tỉ lệ false positive
    # quá cao, sẽ che nhầm nội dung nghiệp vụ trong log.
}

# Compile sẵn vì scrub chạy trên mọi log record.
_COMPILED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern)) for name, pattern in PII_PATTERNS.items()
)


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in _COMPILED_PATTERNS:
        safe = pattern.sub(f"[REDACTED_{name.upper()}]", safe)
    return safe


def scrub_value(value: Any) -> Any:
    """Scrub đệ quy mọi chuỗi bên trong dict/list lồng nhau.

    Giá trị không phải chuỗi được giữ nguyên để không phá kiểu dữ liệu mà
    `config/logging_schema.json` yêu cầu (latency_ms là integer, cost_usd là number).
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    return value


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
