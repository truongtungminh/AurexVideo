# AurexVideo

Video automation engine + macOS distribution app.

## App thật chạy ở đâu

Entry thật là `engine/web_server.py`.

```bash
cd engine
./.venv/bin/python web_server.py --host 127.0.0.1 --port 4173 \
    --source-root /Users/truongminh/.hermes/profiles/aurexvideo/workspace/AurexVideo/decks
# mở http://127.0.0.1:4173/
```

## Version

- Code/app: `0.2.3`

## Cách phát hành / cài máy khác

DMG: `~/Desktop/AurexVideo-0.2.3-native.dmg`

Cài máy khác:
1. Mở DMG, kéo `AurexVideo.app` vào `Applications`.
2. Lần đầu mở, chọn ngôn ngữ (EN/VI) → app tự tải runtime 634MB rồi engine 26MB.
3. Lần sau chỉ tải engine 26MB qua OTA, không tải lại runtime.

## Cấu trúc runtime + data (giống FastScene 2-folder)

```
~/Library/Application Support/app.aurexvideo/
├── engine/    ← code, OTA thay thế toàn bộ
├── runtime/   ← Chromium/ffmpeg/python/model, tải 1 lần
├── python_base/ ← python interpreter, tải 1 lần
└── studio/    ← dữ liệu cá nhân, không bao giờ bị OTA xóa
    ├── project/
    ├── output/
    ├── config/
    └── assets/
```

- Xóa app hoặc cài lại app không mất `studio/`.
- Xóa `~/Library/Application Support/app.aurexvideo` thì mất hết.

## Bootstrap / OTA

- Trên GitHub Release `v0.2.3` có:
  - `aurexvideo-runtime-0.2.2.tar.gz` (~634MB), tải 1 lần khi cài mới.
  - `aurexvideo-engine-0.2.3.tar.gz` (~26MB), tải mỗi bản cập nhật.
- App vỏ (Swift + WKWebView) chỉ khoảng vài MB, không nhét engine vào `.app`.

## Tốc độ tải tham chiếu

- Runtime: ~9.3 MB/s → ~70 giây trong điều kiện mạng ổn định.
- Engine: ~26MB → vài giây đến vài chục giây.

## Model

- `whisper-base` hiện dùng từ `runtime/models/faster-whisper-base`.
- Code ưu tiên: `runtime/models` → `engine/models` → `studio/models`.

## Lưu ý dev local

- Không dùng `server.py` gốc nữa; dùng `engine/web_server.py`.
- Đừng đặt `source-root` trùng `studio/`; để trống hoặc trỏ `decks/` là đúng.
- Nếu sửa packaging, rebuild Swift + rebuild DMG để máy khác nhận được.
