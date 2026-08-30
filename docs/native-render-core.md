# Aurex Render Core

Aurex Render Core là pipeline native trên macOS:

`Scene IR v2 → Metal + CoreText + AVAssetReader → IOSurface CVPixelBuffer → VideoToolbox H.264`

## Trạng thái V2

- Swift package `AurexRenderCore` và CLI `aurex-render`.
- Core hỗ trợ layer `solid`, `image`, `video`, `text`.
- Pose MP4 được đọc tuần tự bằng `AVAssetReader`, có `scene`, `timeline`, `freeze`, loop window và timeline theo frame.
- Text/karaoke được rasterize native bằng CoreText, hỗ trợ tiếng Việt, font asset, màu từng span và line-height.
- Python compiler chuyển `topic.rendered.json` thành Scene IR v2; project chuẩn không cần tự viết native manifest.
- Asset được staging vào thư mục job-local với đường dẫn tương đối an toàn; sau render sẽ dọn tự động.
- H.264 dùng VideoToolbox hardware trên máy có encoder phù hợp; audio được mix bởi AurexVideo rồi mux AAC bằng `-c:v copy`.
- Timeline dùng rational frame rate và presentation time theo frame index, tránh drift.

## Build và kiểm thử

```bash
swift build --package-path native/AurexRenderCore
swift test --package-path native/AurexRenderCore
python3 tools/build_native_render_core.py --configuration release
```

Binary release được staging tại `native/bin/aurex-render` và không commit vào Git vì phụ thuộc kiến trúc máy.

Kiểm tra capability:

```bash
native/bin/aurex-render capabilities
```

## Hợp đồng Scene IR v2

Manifest dùng `schemaVersion: 2`. Canvas phải có kích thước chẵn để xuất H.264. Mỗi layer có frame range, z-index, normalized rect và opacity. Các layer v2 chính:

- `solid`: nền hoặc viền card.
- `image`: ảnh tĩnh với `fill`/`fit`, zoom và pan.
- `video`: pose/background video với `videoSyncMode`, loop start/end.
- `text`: text hoặc `spans`, `fontSource`, font size, alignment và màu.

Manifest custom vẫn phải là full-scene contract. Đường dẫn asset trong manifest phải tương đối, không chứa `..` và không dùng đường dẫn tuyệt đối. Schema v1 vẫn được đọc để giữ tương thích với fixture cũ.

## Hybrid routing hiện tại

`Auto` là mặc định. Với scene nằm trong Scene IR contract, bridge compile topic,
đọc capability của binary và render bằng Aurex Render Core (Metal + VideoToolbox).
Scene có CSS riêng theo character hoặc Custom Intro chưa có contract sẽ đi qua
Browser raster compatibility để giữ đúng preview editor; Core vẫn đảm nhiệm bước
encode cuối nếu universal adapter khả dụng. Nếu Core/native runtime lỗi ở scene
chuẩn, `Auto` cũng fallback Browser và ghi rõ lý do trong report.

`Native` là strict mode: thiếu capability, CSS parity hoặc scene không compile
được thì job dừng, không âm thầm chuyển Browser. `Browser` là lựa chọn ép dùng
compatibility path. Profile built-in của `bietchichomet` đã có Native style
contract; chỉ các CSS override/custom scene chưa có contract mới fallback Browser.

Mọi output ghi rõ provenance trong `final_video.render-report.json`:

```json
{
  "backend_requested": "native",
  "backend_used": "aurex-render",
  "core_version": "0.2.0-v2",
  "scene_renderer": "aurex-native-scene",
  "video_encoder": "aurex-render",
  "browser_invocations": 0,
  "fallback_reason": null,
  "native_scene_features": [
    "text", "karaoke", "pose-timeline", "pose-video", "comparison-layout"
  ]
}
```

## Fixture V2 đã kiểm chứng

Fixture `bietchichomet-lo-den-vs-lo-sau-40fc371f` đã chạy strict native ở 1080×1920, 30 fps, 36,8 giây, gồm 5 pose MP4, 9 mốc pose, 2 ảnh so sánh, text và karaoke tiếng Việt. Kết quả:

- `backend_used=aurex-render`.
- `scene_renderer=aurex-native-scene`.
- `browser_invocations=0`.
- H.264 hardware `Apple H.264 (HW)` trên Apple M3 Max, audio AAC.
- Core render khoảng 30 giây cho video 36,8 giây; trước đó Browser path của fixture mất khoảng 208 giây.

Đây là vertical slice đầu tiên của V2. Bước tiếp theo là chuẩn hoá style contract,
rounded clipping/shadow và golden-frame cho các brand có style/layout riêng trước
khi bật Native mặc định cho các project đó.
