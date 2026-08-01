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

- Code/app: `0.2.4`
- Render pipeline accepts `--engine maziao` end to end. Direct Maziao voice IDs from the favourites dropdown are preserved exactly, and Maziao now standardizes on a single modelId: `vieten_speech`, so rendering no longer drifts between `vietten_speech` / `vieten_speech` variants.
- The standalone voiceover worker bootstraps the engine root before importing `tts.maziao`, preventing `No module named 'tts'` in the packaged runtime.
- Render audio UI: Maziao là tab mặc định; danh sách TTS Voice tải động từ `/api/voices/favourites`, với dropdown 56px và nút play/pause tròn rõ nét ngay bên phải phát trực tiếp `previewUrl`. OncoinX và Mạnh Dũng có link preview fallback khi API không trả link. Checkbox tạo lại cache được đặt sát hàng chọn voice. Không còn hàng preview riêng, text mẫu, custom Voice ID hay ElevenLabs trong render page.
- Crop dialog của editor giờ theo kiểu zoom/pan: khung 1:1 cố định, kéo ảnh để đổi vị trí, không còn tay nắm crop cứng; cache-bust editor JS đã được đẩy lên `20260731-crop-paste-modes-v4`.
- Editor/preview đã có thêm font chooser cho nhãn A/B (`labelFontFamily`), áp vào live preview và render output, để đổi font nhãn mà không đụng phần auto-fit kích thước.
- Khi dán ảnh clipboard vào editor, hệ thống có 2 mode: `square` (tự fit vào khung 1:1) và `original` (giữ ảnh gốc, chỉ set frame zoom/pan), đúng kiểu FastScene.
- Presenter mặc định là `<img>`; chỉ `character-bietchichomet` mới upgrade sang `<video>` khi pose source là video, các character khác luôn hiển thị bằng ảnh.
- Editor/preview giờ có cả `＋ Thêm so sánh` và `＋ Thêm ảnh đơn`; scene single dùng `layout: "single"` để ẩn cột phải và render một ảnh duy nhất.

### Bản cập nhật tính năng (parity FastScene 0.1.44)

**Font & Nhãn**
- Bundle webfont chuẩn tiếng Việt (subset `vietnamese`) trong `engine/assets/fonts/` + `catalog.css`, nạp qua `style.css` và `webui/styles.css`. Không còn phụ thuộc Arial/Georgia hệ thống → hết lỗi vỡ dấu trên Windows.
- Catalog font mới: Inter, Be Vietnam Pro, Manrope, Lexend, Nunito, Quicksand, Saira, Roboto, Literata, Playfair Display. Nguồn duy nhất: `m3_backend.LABEL_FONT_CATALOG`, expose qua `GET /api/label-fonts`.
- Arial/Georgia/Times cũ được map tự động sang Inter/Literata khi mở project cũ.
- Font nhãn được **ghi nhớ theo từng nhân vật** (`labelFontByCharacter` trong project-defaults, giống nền và màu sub). Tạo project mới hoặc đổi nhân vật trong editor sẽ tự áp đúng font đó.

**Pose & Nhân vật**
- Pose có trường `focusSide` (`left` / `right` / `center`) rõ ràng, không còn đoán theo tên/số pose. Manifest cũ được suy luận tự động khi đọc.
- Dialog **Sửa pose** trên dashboard: đổi tên pose, chọn hướng, và **xoá pose** (xoá cả file ảnh trong `studio/assets/characters/<id>/`).
- **Chặn chỉnh sửa nhân vật đang được dùng trong project** (cùng cơ chế an toàn với xoá). `/api/characters` trả thêm `usedBy`, nút Sửa pose bị disable kèm nhãn trạng thái.

**Karaoke & Timing**
- Thêm edge epsilon (`WORD_EDGE_EPSILON = 0.035s`) trong `app.js::activeWordAt` để hấp thụ lệch giữa mốc frame và mốc từ, giảm lệch highlight từ.

**Social & Render**
- Gỡ tài khoản đã kết nối: `POST /api/social/youtube/disconnect`, `POST /api/social/facebook/disconnect`, kèm nút `×` trong dropdown chọn account.
- Tuỳ chọn render (engine, speed, volume, size, branding) được tự lưu sau mỗi lần start render và khôi phục ở lần mở sau (`GET /api/render-preferences`).

**Script & Preview**
- `normalize_display_text()` chuẩn hoá mọi text khởi tạo/nhãn: NFC (chống dấu tổ hợp NFD từ Windows), bỏ zero-width, gộp khoảng trắng.
- Preview giữ nguyên vị trí câu/thời điểm khi iframe phải reload sau upload voiceover (`reloadPreviewKeepingState`).

## Cách phát hành / cài máy khác

DMG: `~/Desktop/AurexVideo-0.2.4-tauri.dmg`

Cài máy khác:
1. Mở DMG, kéo `AurexVideo.app` vào `Applications`.
2. Lần đầu mở, app tự tải python_base (~42MB) + engine (~26MB) từ GitHub releases.
3. Lần sau chỉ tải engine qua OTA, không tải lại runtime.
4. OTA engine 0.2.4 đã được publish lên GitHub Release `v0.2.4` và manifest update trỏ trực tiếp tới asset `aurexvideo-engine-0.2.4.tar.gz`.

## Cấu trúc runtime + data (giống FastScene 2-folder)

```
~/Library/Application Support/app.aurexvideo/
├── engine/    ← code, OTA thay thế toàn bộ
├── runtime/   ← Chromium/ffmpeg/python/model, tải 1 lần
├── python_base/ ← python interpreter, tải 1 lần
└── studio/    ← dữ liệu cá nhân, không bao giờ bị OTA xóa
    ├── project/  ← project cá nhân theo slug
    ├── output/
    ├── config/
    └── assets/

- Xóa app hoặc cài lại app không mất `studio/`.
- Xóa `~/Library/Application Support/app.aurexvideo` thì mất hết.

## Kiến trúc app vỏ

App vỏ là **Tauri v2** (Rust + system WebView), không nhét engine vào `.app`.
Khi cài mới, Rust bootstrap tự tải các thành phần runtime **từ chính chủ** (không qua một file tarball lớn):

- **Python** (~42MB) ← GitHub Release `aurexvideo-python-0.2.4.tar.gz`
- **faster-whisper-base** (145MB) ← HuggingFace `Systran/faster-whisper-base`
- **ffmpeg** (80MB) ← evermeet.cx (mac build)
- **Chromium** (headless shell) ← Playwright CDN qua `python -m playwright install`
- **Engine** (~26MB) ← GitHub Release `aurexvideo-engine-0.2.4.tar.gz`, tải lại mỗi OTA

Các component nặng tải song song, mỗi lần cài chỉ 1 lần (marker `.runtime_ready`).
Chỉ engine được thay thế khi có bản cập nhật OTA (marker `.engine_ready`).

**Lợi ích Tauri so với Swift WKWebView thủ công:** Tauri dùng system WebView (WKWebView macOS),
tự xử lý native file dialog khi web gọi `<input type=file>` → **không cần viết UIDelegate**,
fix triệt để lỗi "Upload PNG button không mở được picker" ở bản Swift cũ.

## Tốc độ tải tham chiếu

- Tổng runtime ~270MB tải song song từ nhiều host: thường < 60 giây mạng ổn định.
- Engine: ~26MB → vài giây đến vài chục giây.

## Model

- `whisper-base` hiện dùng từ `runtime/models/faster-whisper-base`.
- Code ưu tiên: `runtime/models` → `engine/models` → `studio/models`.

## Lưu ý dev local

- Không dùng `server.py` gốc nữa; dùng `engine/web_server.py`.
- Đừng đặt `source-root` trùng `studio/`; để trống hoặc trỏ `decks/` là đúng.
- Build app: `cd tauri-src && cargo build --release`, đóng gói bằng `bash packaging/make-dmg-tauri.sh`.
- Swift app vỏ cũ (packaging/aurexvideo-ui.swift) đã bỏ, thay bằng Tauri (tauri-src/).

## Icon app (macOS)

- Icon app `.icns` sinh từ `assets/aurexvideo-logo.png` (emblem xanh cyan + nút play + film strip, 1024x1024).
- Sinh bằng: resize PNG sang iconset (16→1024, đủ @2x) → `iconutil --convert icns`.
- Không dùng `~/Desktop/Aurex.png` cũ; file `aurexvideo-logo.png` là chuẩn app icon từ bản 0.2.4.

## Trạng thái build (2026-07-28)

- ✅ `AurexVideo-0.2.4-tauri.dmg` (3.2MB) tại `~/Desktop/`: app vỏ **Tauri v2** + ICNS + symlink `/Applications`.
- ✅ `codesign --verify` valid (ad-hoc signing, arm64).
- ✅ Launch test thực tế: app mở, server up HTTP 200 (không trắng), UI dashboard đầy đủ (verify bằng screenshot).
- ✅ Tauri WebView tự xử lý native file dialog → fix lỗi "Upload PNG không bấm được" của bản Swift cũ.
- ✅ Version đồng bộ: `web_server.py APP_VERSION`, `engine/VERSION`, `update-manifest.json` đều `0.2.4`.

- ✅ Browser render flow đang dùng `/api/render` + `/api/jobs/<id>`; đã vá `syncEdgeVoiceCustomField` bị thiếu, đồng bộ tab/pane Edge TTS (`edgetts`), thêm `/api/jobs` để dashboard theo dõi nhiều job render song song thay vì chỉ 1 job live, và chuẩn hoá root project về `studio/project/` để dashboard không còn tự rơi sang `projects/`.
- ✅ Render demo đã hỗ trợ pose asset dạng `.mp4` của custom character bằng cách cache frame poster đầu tiên trước khi capture, nên project như `pnj-va-sjc` render được lại bình thường.
- ✅ Editor/live preview của `#teacher` giờ dùng `<video>` cho pose `.mp4`, nên custom character có pose video chạy trực tiếp trên `/project/<slug>/` thay vì chỉ đứng poster.
- ✅ Cột **Status** trong **Your projects** giờ phản ánh trạng thái render thực tế: `Rendering` khi job đang chạy, `Rendered` khi có `final_video.mp4`, còn lại `Ready`/`Thiếu script`/`Render lỗi` tùy trạng thái.
- ✅ Benchmark thực tế `testspeed` trên 4173: render full `Edge TTS` với defaults mới (**Audio Speed = 1.0**, **Logo + brand off**, FPS mặc định 30) mất **107.3s**.
- ✅ Render default FPS đã quay về **30** để giảm rườm rà UI; `AUREXVIDEO_RENDER_FPS` vẫn có thể override khi benchmark thủ công.
- ✅ Tên project khi tạo/đổi tên sẽ tự động chuyển sang slug không dấu, ví dụ `gửi tiết kiệm` → `gui-tiet-kiem`.
- ✅ Dashboard gọn hơn: bỏ khối **Đang render / Job live / Lịch sử phiên / Tác vụ gần đây** khỏi panel chính.
- ✅ Khi đổi character, pose mặc định giờ quay vòng theo số pose thực có của character mới, không còn chốt dồn vào pose cuối.
- ✅ Âm lượng hiệu ứng pose mặc định ở editor/topic mới chuyển về **50%**.
- ✅ Topic cũ sẽ auto-sync `poseAssets`/`poseLabels` theo manifest hiện tại của character khi load, nên thêm pose mới không còn bị kẹt trong snapshot cũ.
- ✅ Riêng `bietchichomet` dùng chuỗi pose mặc định `1 2 3 1 2 4 1 2 5 1 2` cho các segment auto-select / project mới.
- ✅ Thư viện dự án ở home page có thêm cột **Đăng social**, lấy từ `upload-metadata.json` và được cập nhật sau khi upload YouTube/Facebook thành công.
- ✅ **Auto-comment nguồn Facebook**: sau khi bấm **Upload Facebook Reels** (trạng thái `Publish now`) thành công, nếu ô **Comment nguồn** có nội dung, app tự động chờ **30s** để Facebook tạo object rồi tự comment nguồn (retry sau 45s nếu lỗi). Trước đây auto-comment chỉ chạy ở nút gộp **Upload Facebook + YouTube + comment nguồn**; nay áp dụng cả cho nút upload Facebook đơn lẻ. Logic dùng `commentFacebookSourceWithDelay()` trong `engine/web/render_page.js`.
- ⚠️ TTS vẫn là stage phụ thuộc bên thứ 3; tối ưu chính tập trung vào render video/frame export, không chạm vào chất lượng TTS.

## Custom character CSS (per-character override)

Mỗi `topic.characterId` được `engine/app.js` tự động gắn thêm class `character-<id>` lên `#teacherWrap`
(kèm class `custom-character` chung). Nhờ đó có thể viết CSS riêng cho từng nhân vật mà không ảnh hưởng nhân vật khác.

- Quy tắc chung: `.teacher-wrap.custom-character` (engine/style.css).
- Quy tắc riêng ví dụ: `.teacher-wrap.character-bietchichomet` (đã copy sẵn ở cuối engine/style.css, bạn sửa thoải mái).
- Đổi nhân vật → class `character-*` cũ tự gỡ, class mới tự gắn (app.js dọn sạch prefix `character-` trước khi add).
- Style block nằm CUỐI engine/style.css nên ghi đè `.teacher-wrap.custom-character`/`.teacher`.
- Khi sửa style.css nhớ tăng query version trong engine/index.html (`style.css?v=...`) để trình duyệt reload.
