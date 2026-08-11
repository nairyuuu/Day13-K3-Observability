# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm: B22
- Repository URL: https://github.com/nairyuuu/Day13-K3-Observability
- Commit SHA cuối: Chưa điền
- Thành viên và vai trò: Chưa điền

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`: Estimated Score 100/100
- Tổng số traces: Chưa kiểm tra trong checkpoint này
- Số PII leak còn lại: 0
- Link/đường dẫn dashboard: Chưa hoàn thiện ở checkpoint này

### Kết quả kiểm thử logging

- Server API khởi động thành công và `scripts/load_test.py` chạy hết 10 request không lỗi.
- `python scripts/validate_logs.py` trả về:
  - `Records with missing required fields: 0`
  - `Records with missing enrichment (context): 0`
  - `Potential PII leaks detected: 0`
  - `Estimated Score: 100/100`

### Evidence mẫu

- Correlation ID và enrichment có mặt trong log API:

```json
{"service": "api", "payload": {"message_preview": "What is your refund policy? My email is [REDACTED_EMAIL]"}, "event": "request_received", "user_id_hash": "2055254ee30a", "model": "claude-sonnet-4-5", "env": "dev", "correlation_id": "req-4f3bb336", "feature": "qa", "session_id": "s01", "level": "info", "ts": "2026-08-11T04:07:07.923914Z"}
```

- PII đã được che trước khi ghi log:

```json
{"service": "api", "payload": {"message_preview": "Here is my phone [REDACTED_PHONE_VN], what should be logged?"}, "event": "request_received", "user_id_hash": "64f6ec689229", "model": "claude-sonnet-4-5", "env": "dev", "correlation_id": "req-edd9b862", "feature": "qa", "session_id": "s05", "level": "info", "ts": "2026-08-11T04:07:11.582137Z"}
```

## 3. Logging và tracing

- Evidence correlation ID: `req-4f3bb336`, `req-edd9b862`, `req-1e87cd7e`
- Evidence PII redaction: `[REDACTED_EMAIL]`, `[REDACTED_PHONE_VN]`, `[REDACTED_CREDIT_CARD]`
- Evidence trace waterfall: Chưa thu thập ở checkpoint 1
- Giải thích một span đáng chú ý: Chưa thu thập ở checkpoint 1

## 4. Prompt versioning

- Prompt name: Chưa thực hiện
- Version/label baseline: Chưa thực hiện
- Version/label candidate: Chưa thực hiện
- Trace ID của mỗi version: Chưa thực hiện
- Bằng chứng đổi label hoặc rollback: Chưa thực hiện

## 5. Dashboard, SLO và alerts

- Kết quả `validate_dashboard.py`: Chưa thực hiện
- Evidence dashboard: Chưa thực hiện
- SLO đã chọn và lý do: Chưa thực hiện
- Alert rules và runbook: Chưa thực hiện

## 6. Điều tra challenge

- Challenge ID: Chưa được release
- Triệu chứng từ metrics: Chưa thực hiện
- Trace ID liên quan: Chưa thực hiện
- Log line/correlation ID liên quan: Chưa thực hiện
- Root cause: Chưa thực hiện
- Fix action: Chưa thực hiện
- Preventive measure: Chưa thực hiện

## 7. Đóng góp cá nhân

Với mỗi thành viên, ghi rõ nhiệm vụ và link commit/PR tương ứng.

| Thành viên | Phần việc | Commit/PR | Điều đã học |
|---|---|---|---|
| | | | |
