# Hướng dẫn chuẩn bị TikTok API để upload video

TikTok upload hiện đi qua **Zernio**. AurexVideo thử direct post trước; nếu Zernio trả lỗi TikTok direct posting đang **at capacity**, app chỉ retry một lần bằng `tiktokSettings.draft: true` để đưa video vào Creator Inbox/Draft. Người dùng cần mở TikTok và hoàn tất đăng bản nháp.

Lịch TikTok được giữ trong local scheduler của AurexVideo và worker gọi Zernio đúng giờ, vì vậy fallback capacity cũng được xử lý ở thời điểm đăng. Khi dùng hẹn giờ, cần giữ AurexVideo đang chạy; queue được lưu bền vững và sẽ chạy bù khi app mở lại.

## 1. Cần chuẩn bị

- Một TikTok for Developers account.
- Một TikTok app đã thêm Content Posting API.
- App được approve scope upload/publish.
- OAuth redirect URI.
- User TikTok cấp quyền cho app.
- File cấu hình local: `config/social-upload.json`.

Không commit `config/social-upload.json` vì file này sẽ chứa `client_secret`, access token và refresh token.

## 2. Tạo TikTok developer app

1. Vào TikTok for Developers: https://developers.tiktok.com/
2. Tạo app mới.
3. Vào app dashboard và ghi lại:
   - Client key
   - Client secret
4. Thêm product **Content Posting API**.
5. Cấu hình platform web/desktop phù hợp và redirect URI.

Tài liệu chính thức:
- Get started Content Posting API: https://developers.tiktok.com/doc/content-posting-api-get-started
- TikTok scopes: https://developers.tiktok.com/doc/tiktok-api-scopes
- Upload video API: https://developers.tiktok.com/doc/content-posting-api-reference-upload-video
- Direct post API: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- OAuth token: https://developers.tiktok.com/doc/oauth-user-access-token-management

## 3. Chọn scope đúng nhu cầu

TikTok có 2 hướng chính:

### Upload vào inbox/draft để user tự đăng

Scope:

```text
video.upload
```

Endpoint init thường dùng:

```text
POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/
```

Luồng này phù hợp nếu muốn an toàn: app upload video vào TikTok inbox/draft, user tự review rồi post.

### Direct post thẳng lên profile

Scope:

```text
video.publish
```

Endpoint init thường dùng:

```text
POST https://open.tiktokapis.com/v2/post/publish/video/init/
```

Luồng này cần approval/audit kỹ hơn. Theo tài liệu TikTok, client chưa audit có thể bị giới hạn visibility/private.

## 4. Cấu hình redirect URI

Thêm redirect URI này vào TikTok app:

```text
http://localhost:8765/api/social/tiktok/callback
```

URI này là gợi ý nếu sau này thêm lại nút **Connect TikTok** trong Web UI.

TikTok yêu cầu `redirect_uri` khi đổi authorization code lấy token phải giống đúng URI đã dùng lúc xin code.

## 5. Verify URL/domain nếu dùng PULL_FROM_URL

TikTok Content Posting API hỗ trợ 2 kiểu đưa video:

### FILE_UPLOAD

Backend init upload, lấy `upload_url`, rồi PUT binary video lên URL đó.

Ưu điểm:
- Hợp với repo hiện tại vì file nằm local ở `project/<project>/output/final_video.mp4`.
- Không cần public URL cho video.

### PULL_FROM_URL

TikTok tự kéo video từ URL public.

Yêu cầu:
- URL/domain hoặc URL prefix phải được verify trong TikTok developer dashboard.
- Video phải public-accessible để TikTok server tải được.

Với repo local, nên ưu tiên `FILE_UPLOAD`.

## 6. Cấu trúc config

Nếu triển khai lại, có thể dùng các field này trong `config/social-upload.json`:

```json
{
  "tiktok": {
    "client_key": "YOUR_TIKTOK_CLIENT_KEY",
    "client_secret": "YOUR_TIKTOK_CLIENT_SECRET",
    "redirect_uri": "http://localhost:8765/api/social/tiktok/callback",
    "scopes": ["video.upload"],
    "tokens": {
      "access_token": "USER_ACCESS_TOKEN",
      "refresh_token": "USER_REFRESH_TOKEN",
      "open_id": "TIKTOK_OPEN_ID",
      "expires_at": 0
    }
  }
}
```

Nếu muốn direct post, đổi scope thành:

```json
["video.publish"]
```

Có thể giữ cả hai scope nếu app đã được approve và user đã authorize, nhưng nên bắt đầu bằng `video.upload` vào Inbox/Draft:

```json
["video.upload", "video.publish"]
```

## 7. Luồng OAuth đề xuất

1. Tạo URL authorize đến TikTok với:
   - `client_key`
   - `scope`
   - `response_type=code`
   - `redirect_uri`
   - `state`
2. User đăng nhập TikTok và approve.
3. Callback nhận `code` và `state`.
4. Backend đổi code lấy token:

```text
POST https://open.tiktokapis.com/v2/oauth/token/
```

Body dạng form:

```text
client_key=...
client_secret=...
code=...
grant_type=authorization_code
redirect_uri=...
```

5. Lưu `access_token`, `refresh_token`, `open_id`, `expires_at` vào `config/social-upload.json`.
6. Khi token gần hết hạn, refresh bằng `refresh_token`.

## 8. Luồng upload FILE_UPLOAD đề xuất

### Bước 1: Init upload

Với upload draft/inbox:

```text
POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/
Authorization: Bearer USER_ACCESS_TOKEN
Content-Type: application/json; charset=UTF-8
```

Payload mẫu:

```json
{
  "source_info": {
    "source": "FILE_UPLOAD",
    "video_size": 12345678,
    "chunk_size": 12345678,
    "total_chunk_count": 1
  }
}
```

Với direct post, dùng endpoint `/v2/post/publish/video/init/` và thêm `post_info` như title, privacy, comment/duet/stitch setting theo schema TikTok.

### Bước 2: Upload binary

Lấy `upload_url` từ response init và upload file:

```text
PUT <upload_url>
Content-Type: video/mp4
Content-Range: bytes 0-<LAST_BYTE>/<FILE_SIZE>
```

Body là binary của:

```text
project/<project>/output/final_video.mp4
```

Với file lớn, làm chunk upload theo hướng dẫn TikTok.

### Bước 3: Check status

Lưu `publish_id` từ response init và gọi API get status để biết TikTok đã xử lý xong chưa.

## 9. App Review và lỗi thường gặp

- `scope_not_authorized`: app chưa được approve scope, hoặc user chưa authorize scope đó.
- `access_token_invalid`: token hết hạn/sai user; refresh token hoặc connect lại.
- Direct post bị giới hạn private: app chưa audit/approve đầy đủ.
- PULL_FROM_URL lỗi: domain/URL prefix chưa verify hoặc video URL không public.
- Rate limit: TikTok giới hạn số request theo user/token; cần retry có kiểm soát.

## 10. Gợi ý mapping với repo

Khi implement trong `web_server.py`, nên dùng:

- `final_video_path_for_project(project)` để lấy MP4 local.
- Với fallback Creator Inbox, caption/title có thể cần kiểm tra và hoàn thiện trong app TikTok sau khi nhận draft.
- `write_social_config(config)` để lưu token với chmod `600`.

Nếu triển khai lại TikTok, nên bắt đầu bằng `video.upload` vào draft/inbox trước, vì ít rủi ro hơn direct publish.
