# Báo cáo Day 13 Observability

## 1. Thông tin nhóm

- Tên nhóm: B2.2
- Repository URL: https://github.com/nairyuuu/Day13-K3-Observability
- Commit SHA cuối: xem commit chứa file báo cáo này (issue #11 completion)
- Thành viên và vai trò:
  - Nguyễn Thành Duy — 01599: SRE & Alerts Engineer.
  - Thạch Minh Quân — 01585: Security Engineer.
  - Nguyễn Minh Phúc — 01161: Metrics & Dashboard.
  - Lê Trần Long — 01257: API & Middleware.
  - Phạm Đức Mạnh — 01075: QA & Chief Investigator.

## 2. Kết quả kỹ thuật

- Điểm `validate_logs.py`: **100/100** (53 log records, 26 correlation IDs, 0 field/context thiếu)
- Tổng số traces: **76** trên Langfuse tại thời điểm kiểm tra 2026-08-11
- Số PII leak còn lại: **0**
- Link/đường dẫn dashboard: `python scripts/dashboard_app.py` → `http://127.0.0.1:8501`

## 3. Logging và tracing

- Evidence correlation ID: `submission/evidence/runtime_dashboard.svg` (`req-d8f21dc9`)
- Evidence PII redaction: `submission/evidence/validatelogs.png`
- Evidence trace waterfall: Langfuse trace `4e0b031efe36fe80c3b401c07d9fa664`
- Giải thích một span đáng chú ý: trace trên thuộc session `s09`, latency 2.652 giây; timestamp khớp log `response_sent` của correlation ID `req-d8f21dc9` với `latency_ms=2653`, chứng minh spike do practice incident `rag_slow`.

## 4. Prompt versioning

- Prompt name: `day13-chat`
- Version/label baseline: v1 — `baseline`, `production`
- Version/label candidate: v2 — `candidate`, `latest`
- Trace ID của mỗi version: baseline v1 `78be319e96f604227a88212955b9dfeb`; candidate v2 `5f924504998d67cc10af775a9964a53c`
- Bằng chứng đổi label hoặc rollback: đã chuyển `production` từ v1 sang v2, tạo trace `a8919de0319203a7ad2e11a21978e739`, rồi rollback về v1. Trạng thái cuối đã xác minh: v1=`baseline,production`; v2=`candidate,latest`. Evidence: `submission/evidence/day13-chat.jpg`, `submission/evidence/v1.jpg`, `submission/evidence/trace_versions.svg`, `submission/evidence/prompt_versioning.md`.

## 5. Dashboard, SLO và alerts

- Kết quả `validate_dashboard.py`: **HỢP LỆ: 6/6 panel**
- Evidence dashboard: `submission/evidence/runtime_dashboard.svg`; runtime dashboard trả HTTP 200, render đủ 6 panel và hiển thị slow correlation ID.
- SLO đã chọn và lý do: Latency P95 ≤ 3000 ms, error rate ≤ 2%, quality mean ≥ 0.75 và total cost ≤ 2.5 USD theo `config/dashboard.yaml`/`config/slo.yaml`; các ngưỡng bao phủ latency, reliability, quality và cost.
- Alert rules và runbook: `config/alert_rules.yaml` và `docs/alerts.md`.

## 6. Điều tra challenge

- Challenge ID: `practice-rag_slow` (practice incident, không thay thế official challenge)
- Triệu chứng từ metrics: latency P95 tăng từ **571 ms** lên **2653 ms** (**4.65×**); 10/10 request incident vẫn trả HTTP 200.
- Trace ID liên quan: `4e0b031efe36fe80c3b401c07d9fa664` (session `s09`, latency 2.652 giây)
- Log line/correlation ID liên quan: `req-d8f21dc9`, event `response_sent`, `latency_ms=2653`
- Root cause: practice incident `rag_slow` thêm độ trễ vào bước RAG/agent, làm toàn bộ trace chậm nhưng không tạo HTTP error.
- Fix action: chạy `python scripts/inject_incident.py --scenario rag_slow --disable`; health endpoint xác nhận `rag_slow=false`.
- Preventive measure: alert trên P95, giữ correlation ID xuyên metrics → trace → log, và dùng runbook rollback/disable incident trước khi mở rộng điều tra.

## 7. Đóng góp cá nhân

Với mỗi thành viên, ghi rõ nhiệm vụ và link commit/PR tương ứng.

| Thành viên | Phần việc | Commit/PR | Điều đã học |
|---|---|---|---|
| Nguyễn Thành Duy — 01599 | SRE, SLO, alert rules và runbook | CP2 | Thiết kế ngưỡng cảnh báo và quy trình ứng phó sự cố |
| Thạch Minh Quân — 01585 | PII scrubbing và prompt-version metadata | CP1/CP2 | Bảo vệ dữ liệu nhạy cảm xuyên logs và traces |
| Nguyễn Minh Phúc — 01161 | Metrics và dashboard contract 6 panel | CP2 | Liên kết phép tổng hợp, đơn vị và threshold |
| Lê Trần Long — 01257 | API, middleware và correlation ID | CP1 | Truy vết request xuyên middleware, logs và traces |
| Phạm Đức Mạnh — 01075 | QA, incident simulation và evidence | Issue #11 | Điều tra metrics → trace → correlation ID → log |
