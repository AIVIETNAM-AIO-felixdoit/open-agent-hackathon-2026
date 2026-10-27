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

## Trạng thái (cập nhật 28/10 03:10)
Đã xong và đã chạy thật:
- 4 agent + Critic bác bỏ (LLM + luật "mỗi mảng rủi ro phải có bằng chứng được trích dẫn"), tối đa 2 vòng.
- Model: `nvidia/nemotron-3-ultra-550b-a55b:free` qua **OpenRouter** (NVIDIA không gửi OTP về số VN). Free tier chỉ 50 request/ngày, reset 07:00 sáng VN. Mỗi lượt chạy tốn khoảng 4–9 request. Tắt reasoning bằng `extra_body={"reasoning": {"enabled": False}}`.
- **Zetaris** chạy local trong `zetaris-platform/` (gitignored). 3 nguồn REST + USL `lightning.metastore.dd.dd_usl` + DQ rule, tạo bằng `python zetaris_setup.py`. Truy vấn chứng minh: `python zetaris_queries.py` → `zetaris_sql/results.md`. 3 tài liệu ở `docs/zetaris/`.
- Docker: đã tăng WSL lên 9 GB trong `~/.wslconfig` (bản cũ ở `.wslconfig.bak`).
- **Sample mode**: `python run.py --input input_examples/example_1.json --sample`, hoặc checkbox trên UI. Phát lại `samples/*.jsonl`.
- README.md và metadata.json đã viết.

## Cài đặt trên máy mới
```
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # rồi điền key (để trống LLM_API_KEY = sample mode)
.\.venv\Scripts\python -m uvicorn main:app    # mở http://localhost:8000
```

## Việc còn lại
1. Quay video (1–4 phút) theo khung: Problem → Solution → Agents → Live run → Sponsor tech → Output. Tên file `OAH2026-<TeamName>-ExplainWhy-Demo.mp4`. Không để lộ key (`.env`).
2. Đẩy lên GitHub, nộp trên HackOS trước 06:45.
