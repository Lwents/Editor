# AutoTranslate Studio

Editor OpenCut tích hợp dịch phim, chỉnh từng đoạn phụ đề và làm mờ chữ gốc.

## Cấu trúc

- `fe/`: frontend OpenCut. Giao diện Next.js nằm trong `fe/apps/web`; `fe/rust` và `fe/rust/wasm/pkg` chứa logic dùng chung và renderer WASM. Giữ cấu trúc workspace để các dependency, font, timeline và exporter hoạt động đúng.
- `be/`: backend FastAPI dịch phim, ASR, phụ đề và xử lý media. API là `be/app/main.py`.
- `CHAY_DU_AN.ps1`, `CHAY_DU_AN.bat`: chạy bản FE/BE mới trên Windows.

## Cài trên Windows

Cần Node.js, Bun, Python 3.12 và FFmpeg. 9router là dịch vụ riêng: chạy ở máy bạn hoặc đặt URL dịch vụ trong `be/.env`; repository không chứa tài khoản, token hay dữ liệu 9router.

PowerShell tại thư mục gốc:

```powershell
cd fe
bun install
Copy-Item apps/web/.env.example apps/web/.env.local
cd ../be
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
cd ..
```

Điền API key của bạn vào `be/.env`. Kiểm tra URL/model khớp model được 9router của bạn hỗ trợ. `.env` không đưa lên Git. Encoder mẫu dùng CPU `libx264`, có thể đổi theo GPU máy bạn. Model Whisper được tải về khi dùng lần đầu; dependency, model và 9router cần cài riêng.

Tắt bản FE/BE cũ ở cổng 5175/8100 rồi chạy `CHAY_DU_AN.bat`. FE: http://127.0.0.1:5175/projects. BE: http://127.0.0.1:8100/health.

### Chạy riêng từng phần

BE (PowerShell tại `be`):

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

FE (PowerShell tại `fe`):

```powershell
bun run dev:editor
```

Linux/WSL: tạo `be/.venv` bằng `python3 -m venv .venv`, cài requirements bằng `.venv/bin/python -m pip`, tạo `.env` từ mẫu và chạy `.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100`; FE dùng cùng lệnh Bun ở trên.

Renderer WASM đã có sẵn trong source. Nếu sửa Rust, cài Rust/wasm-pack và chạy `bun run build:wasm` tại `fe`.

## Đưa lên Git mới

Đã khởi tạo Git mới ở thư mục gốc, chưa commit và chưa gắn remote. Tạo repository trống trên GitHub (không tạo sẵn README), rồi chạy tại gốc:

```powershell
git status --short
git add .
git diff --cached --stat
git commit -m "Initial AutoTranslate Studio FE and BE"
git remote add origin https://github.com/TEN_CUA_BAN/TEN_REPO.git
git push -u origin main
```

`.gitignore` loại trừ key/mật khẩu, `.env`, dữ liệu video, database, model, runtime 9router, venv, node_modules, log và bản đóng gói. Chỉ có cấu hình mẫu trống. Không thêm các dữ liệu đó bằng `git add -f`.

## Giấy phép

Frontend giữ giấy phép MIT và thông tin tác giả OpenCut tại `fe/LICENSE`. Các phần upstream giữ thông báo giấy phép tương ứng.
