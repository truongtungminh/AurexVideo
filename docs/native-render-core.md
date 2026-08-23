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

## Tích hợp vào pipeline hiện tại

Native core được triển khai theo mô hình opt-in/fallback. Backend browser hiện vẫn là mặc định cho parity với project cũ. Chỉ chuyển project sang native sau khi project tạo được manifest schema `1` và tất cả layer đều nằm trong capability report. Nếu native không khả dụng hoặc manifest không hợp lệ, caller phải giữ output browser và ghi rõ lý do fallback.

Lộ trình tiếp theo để đưa các project bietchichomet sang native là bổ sung decoder tuần tự cho pose-video, text/karaoke và audio; không nên giả lập các layer đó bằng ảnh tĩnh vì sẽ làm thay đổi nội dung video.
