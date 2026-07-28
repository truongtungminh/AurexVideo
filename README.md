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

App vỏ (Swift + WKWebView) chỉ khoảng vài MB, không nhét engine vào `.app`.
Khi cài mới, app tự tải các thành phần runtime **từ chính chủ** (không qua một file tarball lớn):

- **Python** (~42MB) ← GitHub Release `aurexvideo-python-0.2.3.tar.gz`
- **faster-whisper-base** (145MB) ← HuggingFace `Systran/faster-whisper-base`
- **ffmpeg** (80MB) ← evermeet.cx (mac build)
- **Chromium** (headless shell) ← Playwright CDN qua `python -m playwright install`
- **Engine** (~26MB) ← GitHub Release `aurexvideo-engine-0.2.3.tar.gz`, tải lại mỗi OTA

Các component nặng tải song song, mỗi lần cài chỉ 1 lần (marker `.runtime_ready`).
Chỉ engine được thay thế khi có bản cập nhật OTA (marker `.engine_ready`).

## Tốc độ tải tham chiếu

- Tổng runtime ~270MB tải song song từ nhiều host: thường < 60 giây mạng ổn định.
- Engine: ~26MB → vài giây đến vài chục giây.

## Model

- `whisper-base` hiện dùng từ `runtime/models/faster-whisper-base`.
- Code ưu tiên: `runtime/models` → `engine/models` → `studio/models`.

## Lưu ý dev local

- Không dùng `server.py` gốc nữa; dùng `engine/web_server.py`.
- Đừng đặt `source-root` trùng `studio/`; để trống hoặc trỏ `decks/` là đúng.
- Nếu sửa packaging, rebuild Swift + rebuild DMG để máy khác nhận được.

## Icon app (macOS)

- Icon app `.icns` sinh từ `assets/aurexvideo-logo.png` (emblem xanh cyan + nút play + film strip, 1024x1024).
- Sinh bằng: resize PNG sang iconset (16→1024, đủ @2x) → `iconutil --convert icns`.
- Không dùng `~/Desktop/Aurex.png` cũ; file `aurexvideo-logo.png` là chuẩn app icon từ bản 0.2.3.

## Trạng thái build (2026-07-28)

- ✅ `AurexVideo-0.2.3-native.dmg` (2.6MB) tại `~/Desktop/`: app vỏ Swift + ICNS + symlink `/Applications`.
- ✅ `codesign --verify` valid (ad-hoc signing, arm64).
- ✅ Launch test thực tế: app mở, downloader UX hiển thị % / tốc độ MB/s / ETA (đã verify bằng screenshot).
- ✅ Version đồng bộ: `web_server.py APP_VERSION`, `engine/VERSION`, `update-manifest.json` đều `0.2.3`.
