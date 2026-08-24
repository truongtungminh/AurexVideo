# Aurex Render Core

Aurex Render Core là backend native đầu tiên của Aurex Video trên macOS. MVP hiện chạy theo chuỗi:

`Metal compositor → IOSurface-backed CVPixelBuffer → AVAssetWriter/VideoToolbox → H.264 MP4`

## Trạng thái hiện tại

- Swift Package `AurexRenderCore` và CLI `aurex-render`.
- Timeline dùng rational frame rate và presentation time theo frame index, tránh drift do số thực.
- Compositor Metal hỗ trợ layer `solid` và `image`, có keyframe nội suy rect/opacity.
- H.264 encoder discovery qua VideoToolbox, hỗ trợ `automatic`, `prefer`, `require`, `software` và fallback khi encoder không nhận cấu hình.
- Output có report JSON: hash manifest, timeline, codec, màu, thiết bị Metal, encoder và thời gian render.
- Audio, text/karaoke, pose-video và subtitle chưa nằm trong native MVP.

## Build và kiểm thử

```bash
swift build --package-path native/AurexRenderCore
swift test --package-path native/AurexRenderCore
```

Để staging binary release vào engine package trước khi đóng gói app:

```bash
python3 tools/build_native_render_core.py --configuration release
```

Binary sẽ được đặt tại `native/bin/aurex-render`; thư mục này được giữ ngoài Git vì binary phụ thuộc kiến trúc macOS. Gói phân phối cần chạy bước staging này; nếu không, `Auto` sẽ fallback Browser và `Native` sẽ báo thiếu core.

Kiểm tra khả năng máy:

```bash
swift run --package-path native/AurexRenderCore aurex-render capabilities
```

Render smoke test:

```bash
swift run --package-path native/AurexRenderCore aurex-render render \
  --manifest native/AurexRenderCore/Examples/basic-manifest.json \
  --output /tmp/aurex-render-core-smoke.mp4 \
  --overwrite
```

## Hợp đồng manifest

Manifest schema version `1` là hợp đồng ổn định giữa pipeline chuẩn bị scene và native core. Đường dẫn image phải là đường dẫn tương đối an toàn tính từ thư mục manifest; không được dùng `..` hoặc đường dẫn tuyệt đối. Canvas H.264 phải có kích thước chẵn.

CLI chỉ ghi output sau khi toàn bộ frame hoàn tất: encoder ghi vào file tạm rồi mới atomically move/replace output. Vì vậy render lỗi không để lại MP4 dở dang tại đường dẫn đích.

## Tích hợp Core-first

UI, API và CLI mặc định dùng `Auto`. Mỗi job preflight theo thứ tự: đọc scene contract, kiểm tra layer `solid/image`, đọc `aurex-render capabilities`, validate manifest bằng chính Core, rồi mới render. Kết quả có ba chế độ:

- `Auto`: chạy `aurex-render` khi cả scene và máy đủ capability; nếu không thì fallback Browser.
- `Aurex Render Core`: strict mode, thiếu capability sẽ dừng job thay vì fallback.
- `Browser`: compatibility mode do người dùng chọn rõ ràng.

Mỗi `final_video.render-report.json` luôn ghi hợp đồng quyết định:

```json
{
  "backend_requested": "auto",
  "backend_used": "browser",
  "fallback_reason": "unsupported_scene_features:text,karaoke,pose-video",
  "fallback_detail": "Core MVP chỉ hỗ trợ solid/image; scene hiện tại cần text, karaoke, pose-video.",
  "capability_report": {
    "core_mvp_layer_types": ["solid", "image"],
    "decision": "fallback"
  }
}
```

Fallback không bao giờ được gắn nhãn native. Job API cũng trả `backend_requested`, `backend_used`, `fallback_reason` sau khi hoàn tất.

### Scene contract native

Project có thể dùng manifest file đầy đủ:

```json
{
  "nativeRenderManifest": "native-render.json"
}
```

Hoặc dùng adapter inline; bridge sẽ sinh manifest tạm và tự điền canvas/FPS/frame count:

```json
{
  "nativeRenderScene": {
    "backgroundColor": "#101820",
    "layers": [
      {
        "id": "card",
        "type": "image",
        "source": "assets/card.png",
        "rect": {"x": 0.1, "y": 0.15, "width": 0.8, "height": 0.7},
        "contentMode": "fit"
      }
    ]
  }
}
```

`nativeRenderScene` và native manifest là full-scene contract, không phải lớp phủ một phần. Image source phải nằm trong cùng thư mục project/manifest, dùng đường dẫn tương đối không có `..`. Manifest tạm được validate bằng CLI rồi xóa sau job. Core xuất H.264; AurexVideo mux audio đã mix bằng `-c:v copy`, còn branding/outro đi qua finalizer hiện hữu.

### Inventory workspace ngày 2026-08-24

Workspace hiện có 228 topic thuộc 7 brand. Không topic nào khai báo full-scene native contract, và mọi topic đều có text/label/karaoke cùng pose timeline; vì vậy chúng chạy `Auto → Browser` đúng chủ đích. Phân loại chính:

| Brand | Project | Pose video | Pose image | Native hiện tại |
|---|---:|---:|---:|---|
| Aurex | 99 | 58 | 41 | fallback |
| anhtinhbiettuot | 50 | 0 | 50 | fallback |
| bietchichomet | 39 | 39 | 0 | fallback |
| engzy | 10 | 10 | 0 | fallback |
| july | 1 | 1 | 0 | fallback |
| knowzy | 7 | 0 | 7 | fallback |
| popsy | 22 | 0 | 22 | fallback |

Native thật được kiểm chứng bằng fixture `solid/image` và example manifest của Core. Các topic hiện hữu không bị sửa để đạt con số native giả.

Lộ trình tiếp theo để đưa các project bietchichomet sang native là bổ sung decoder tuần tự cho pose-video, text/karaoke và audio; không nên giả lập các layer đó bằng ảnh tĩnh vì sẽ làm thay đổi nội dung video.
