# HANDOFF: Open Agent Hackathon 2026

> Đọc file này đầu tiên khi mở dự án trên máy mới (dán vào Claude Code: "đọc HANDOFF.md rồi làm tiếp").

## Link quan trọng
| Gì | Link |
|---|---|
| Trang hackathon (Resources, nộp bài trên HackOS) | https://hackathon.genai.works/event/open-agent-hackathon-2026 |
| Discord (thông báo, kênh #help desk) | https://discord.gg/tfwSeqVWmR |
| Repo dự án | https://github.com/AIVIETNAM-AIO-felixdoit/open-agent-hackathon-2026 |
| NVIDIA model Nemotron + Get API Key | https://build.nvidia.com/nvidia/llama-3_3-nemotron-super-49b-v1_5 |
| Tavily API key (tìm kiếm web) | https://app.tavily.com |
| Zetaris: đăng ký | https://cloud.enterprise.zetaris.com/register |
| Zetaris: tải bản chạy local + lệnh docker login | https://cloud.enterprise.zetaris.com/dashboard/download |
| Zetaris: hướng dẫn và recipe | https://github.com/zetaris/getting-started |
| Zetaris: danh sách nguồn REST (SEC EDGAR...) | https://github.com/zetaris/getting-started/blob/main/open_data/rest_apis/rest-api-sources.md |
| Docker Desktop | https://docs.docker.com/desktop/ |
| Meterless (engines, prompt mẫu) | https://meterless.ai/engines.html |
| Meterless GitHub | https://github.com/Meterless/Meterless |

## Hạn chót
**27/10/2026 23:45 UTC = 06:45 sáng 28/10 giờ VN.** Đăng ký và nộp bài đóng cùng giờ này. Team đã tạo xong.

## Đề bài và cách chấm
- Track đã chọn: **Track 02, The Agent That Can Explain Why**. Use case: thẩm định công ty (due diligence / M&A).
- Điểm (100): Impact 30, Technical 20, Innovation 15, Demo 15, Product & UX 10, **Sponsor tech 10**.
- Bài nộp gồm: **video 1–4 phút** (MP4, tên file `OAH2026-<TeamName>-ExplainWhy-Demo.mp4`), repo GitHub có README, `input_examples/`, lệnh chạy (`run.py` / `POST /run`), và `metadata.json` ghi engine Meterless đã dùng.
- Video BẮT BUỘC cho thấy: track, vấn đề, người dùng; ≥ 2 agent có vai trò rõ và **phối hợp thật** (có vòng phản hồi, agent này bác bỏ việc của agent kia); một lần chạy thật; **Zetaris và Meterless thực sự chạy** (thấy trong trace); kết quả cuối cùng và vì sao nó hữu ích.
- Tuyệt đối không để lộ API key trong video.

## Đã làm xong
| File | Nội dung |
|---|---|
| `agent.py` | Hệ 4 agent: **Planner → Retriever → Analyst → Critic**. Critic bác bản nháp khi nhận định không có bằng chứng hoặc thiếu mảng nào, rồi gửi ngược về Planner (tối đa 2 vòng). Mỗi lượt trao đổi ghi vào `logs/trace_<run_id>.jsonl`. Mọi lời gọi model đi qua một hàm duy nhất `ask_json` (đây là "injected generator" theo chuẩn Meterless). |
| `hmem.py` | **Meterless H-MEM** (MemoryStore + Retrieval + TrustLedger): bộ nhớ chia 3 tầng (ngắn hạn / làm việc / dài hạn), lưu nguồn gốc và tên agent ghi. Bộ nhớ dài hạn lưu ở `memory/long_term.jsonl` để lần chạy sau vẫn dùng được. Ledger và retrieval trace ghi vào `logs/`. Đã test: lần chạy 2 lấy lại được memory của lần 1. |
| `main.py` | FastAPI: `GET /` (giao diện), `POST /run` (trả JSON), `POST /api/investigate` (streaming cho giao diện). |
| `run.py` | CLI: `python run.py --input input_examples/example_1.json` |
| `static/index.html` | Giao diện: trace các agent theo thời gian thực, kết luận, độ tin cậy, lý do, rủi ro, mâu thuẫn giữa các nguồn, chuỗi bằng chứng có link nguồn. |
| `input_examples/example_1.json` | Ví dụ: có nên ký hợp đồng cung cấp 3 năm với VinFast. **Nên đổi sang công ty Mỹ (vd Rivian) để khớp với dữ liệu SEC EDGAR trên Zetaris.** |

**Lưu ý:** agent CHƯA chạy trọn một lần nào vì chưa có key. Chạy thử là việc đầu tiên.

## Cài đặt trên máy mới
```
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # rồi điền key
.\.venv\Scripts\python -m uvicorn main:app --reload    # mở http://localhost:8000
```
Key cần có trong `.env`:
- `LLM_API_KEY`: NVIDIA, lấy ở build.nvidia.com, model `nvidia/llama-3.3-nemotron-super-49b-v1.5`, nút Get API Key. Tài khoản NVIDIA đã tạo.
- `TAVILY_API_KEY`: app.tavily.com, miễn phí. (Thư viện `ddgs` không kết nối được trên máy cũ nên chuyển sang Tavily.)

## Việc còn lại (theo thứ tự)
1. **Điền key, chạy thử, sửa lỗi** (JSON từ Nemotron, timeout...).
2. **Zetaris** (Docker trên máy này):
   - Đăng ký cloud.enterprise.zetaris.com/register (gói Hobby), vào /dashboard/download để tải `zetaris-platform.zip` và lấy lệnh `docker login zetarishackathon.azurecr.io`.
   - Docker Desktop: Memory 8 GB, CPU 4. Trong file `.env` của bundle đặt: ADMIN_EMAIL, ADMIN_PASSWORD (có chữ hoa, chữ thường, số và một ký tự `$@!%*?&`), ADMIN_ORG, `ZSPARK_DRIVER_MEM=2g`, `ZSPARK_DRIVER_CORES=2`, `ZSPARK_MASTER_URL=local[2]`, `ZETARIS_API_MAX_HEAP=1g`.
   - Chạy `docker compose pull`, `docker compose up -d`, rồi `./verify.sh`. Mở http://localhost:3000, chạy `SELECT 1`.
   - Lấy API key (Management → API Tokens) và Org ID (Management → Organisation). REST base: `http://localhost:8888/api/v1.0`.
   - Hướng dẫn và recipe: repo github.com/zetaris/getting-started (đọc `AGENTS.md`, `docs/connections/client-setup.md`, `docs/guides/create-edgar-pudl-noaa-usl.md`, `open_data/rest_apis/`).
   - **Yêu cầu chấm của Zetaris:** ≥ 3 nguồn dữ liệu, 2–3 truy vấn mỗi nguồn; 1 USL kèm DDL, 2–3 truy vấn chứng minh USL chạy, 1 luật kiểm tra chất lượng dữ liệu (DQ rule); và 3 tài liệu: (a) USL tiết kiệm thời gian ra sao so với tự code tay, (b) USL giúp gì trong công việc, (c) đề xuất cải tiến viết như một PM.
   - Nguồn dữ liệu đề xuất (REST, không cần key, chỉ cần User-Agent): **SEC EDGAR Company Facts**, **EDGAR company profiles (SIC)**, **company_dns US SIC**. Đúng 3 nguồn, khớp mảng thẩm định công ty.
   - Code: thêm tool `zetaris_query(sql)` cho Retriever. Agent lấy số liệu tài chính từ USL qua Zetaris, rồi kết hợp với tìm kiếm web. Ghi `trace.log("Retriever", "tool_call", tool="zetaris", sql=...)` để video thấy Zetaris thực sự chạy.
3. **Sample mode**: chạy được khi không có key, phát lại một lần chạy đã ghi. Meterless checklist yêu cầu mục này.
4. **README.md + metadata.json**: ghi rõ đã dùng engine Meterless nào và ở đâu (H-MEM trong `hmem.py`), Zetaris ở đâu, NVIDIA Nemotron. Thêm sơ đồ kiến trúc.
5. **Demo**: chạy 2 lần cùng một công ty để thấy H-MEM nhớ qua các lần chạy; quay video theo khung: Problem → Solution → Agents → Live run → Sponsor tech → Output.
6. Đẩy lên GitHub, nộp trên HackOS trước 06:45.
