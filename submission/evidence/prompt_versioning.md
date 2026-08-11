# Evidence — Prompt Versioning (Checkpoint 2)

Project Langfuse: `My Project` (`cmso2kfsm0055ad0f016gy5nb`), host `https://jp.cloud.langfuse.com`.

- Traces: <https://jp.cloud.langfuse.com/project/cmso2kfsm0055ad0f016gy5nb/traces>
- Prompts: <https://jp.cloud.langfuse.com/project/cmso2kfsm0055ad0f016gy5nb/prompts>

## Prompt contract

`day13-chat` giữ đủ 3 biến bắt buộc theo `docs/PROMPT_VERSIONING.md`:

| Version | Nội dung | Label |
|---|---|---|
| v1 | `Feature={{feature}}` / `Docs={{docs}}` / `Question={{message}}` | `baseline`, `production` |
| v2 | như v1, thêm dòng `Answer in at most 3 concise sentences.` | `candidate` |

## Ma trận label → trace (chạy thật, không mô phỏng)

Mỗi bước chạy trong một tiến trình mới để prompt cache (`cache_ttl_seconds=60`) không dính kết quả bước trước.

| Bước | `LANGFUSE_PROMPT_LABEL` | Trace ID | `prompt_version` | `prompt_source` |
|---|---|---|---|---|
| Lấy v1 qua label baseline | `baseline` | `84e2de57d98ce5f62c16ba83cc21b70e` | 1 | `langfuse` |
| Lấy v2 qua label candidate | `candidate` | `2844536306a543a2444115a4b487859c` | 2 | `langfuse` |
| production trước khi đổi | `production` | `1a557f7d0a443a951cb94adee57a31dd` | 1 | `langfuse` |
| **Đổi label** production → v2 | `production` | `f4534191df0ed054fc2660cb5386112b` | **2** | `langfuse` |
| **Rollback** production → v1 | `production` | `8530ec2c7f0981d5f79f4fd1f7dc76ad` | **1** | `langfuse` |
| Fallback: prompt không tồn tại | `production` | `5656fbc3a03a1abd53815e41ae2720b6` | `local-v1` | `local-fallback` |

Hai trace chứng minh hai version khác nhau với cùng một input: `84e2de57…` (v1) và `2844536306…` (v2).

Cặp trace chứng minh rollback: `f4534191…` (production = v2) → `8530ec2c…` (production = v1).

## Fallback

Cả 4 nhánh của `resolve_prompt()` đều có test trong `tests/test_prompt_management.py`:

| Tình huống | `prompt_source` | `prompt_version` | `prompt_fetch_error` |
|---|---|---|---|
| Không có key (`enabled=False`) | `local` | `local-v1` | — |
| Langfuse timeout / lỗi mạng | `local-fallback` | `local-v1` | `TimeoutError` |
| SDK trả prompt fallback | `local-fallback` | `local-v1` | `LangfuseFallback` |
| Lấy được managed prompt | `langfuse` | version thật | `None` |

Nhánh `local-fallback` còn được chứng minh trên trace thật: `5656fbc3a03a1abd53815e41ae2720b6`.

## Số lượng trace

43 trace trên Langfuse, trong đó 33 trace có đủ metadata prompt versioning (10 trace dùng managed prompt v1/v2, 23 trace `local-fallback`).

10 trace còn lại (timestamp `03:20`) là rác từ lần chạy trước khi sửa lỗi SDK `langfuse 4.14.3` — lúc đó `update_current_trace()` ném `AttributeError` nên trace không kịp gắn metadata. Lọc theo thời gian sau `03:30` để bỏ nhóm này.

## PII

Quét toàn bộ 43 trace bằng đúng 4 detector của `scripts/validate_logs.py`: **0 rò rỉ**.

Một chuỗi khớp `phone_vn` duy nhất là **false positive** — nằm trong span ID hex do Langfuse tự sinh (`07c12a0121771044` chứa 10 chữ số liên tiếp bắt đầu bằng `0`), không phải dữ liệu app gửi lên.
