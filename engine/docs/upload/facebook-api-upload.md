# Hướng dẫn chuẩn bị Facebook API để upload Reels/video

FastScene dùng **Facebook Reels Publishing API**. Sau khi lấy Page ID và Page access token, bạn có thể nhập trực tiếp bằng nút **Thêm Page** trong Upload Center.

## 1. Yêu cầu (xem thôi nhảy vào Bước 2)

- Một Meta developer account.
- Một Meta app.
- Một Facebook Page mà bạn có quyền tạo nội dung.
- Page access token có đủ permission để publish video/Reels.
- App Review nếu dùng ngoài tester/admin/developer của app.
- Page ID và Page access token để dán trực tiếp vào Upload Center.

## 2. Tạo Meta app

1. Vào Meta for Developers: https://developers.facebook.com/
2. Vào **My Apps** → **Create App**.
3. Chọn loại app phù hợp với use-case quản lý Page.
![alt text](../assets/upload/image-5.png)
![alt text](../assets/upload/image-6.png)

tiếp tục rồi tạo app

![alt text](../assets/upload/image-10.png)

chọn customize

## 3. Thêm sản phẩm và permission cần thiết

![alt text](../assets/upload/image-22.png)

Bấm add  quyền như hình




## 4. Lấy Page access token

https://developers.facebook.com/tools/explorer/

Chọn hết các quyền như hình

![alt text](../assets/upload/image-23.png)

chọn đúng app và chọn get user access token

![alt text](../assets/upload/image-24.png)

chọn page cần nối

![alt text](../assets/upload/image-20.png)

sau khi xong chọn lại như hình và bấm generate lần nữa
![alt text](../assets/upload/image-25.png)

Hiện đủ quyền là ok

![alt text](../assets/upload/image-26.png)

Sau đó bấm copy token 

rồi vào 

https://developers.facebook.com/tools/debug/accesstoken/

dán token vào rồi debug, kéo xuống dưới chọn extend, rồi copy lại là xong

![alt text](../assets/upload/image-27.png)

báo hết hạn 3 tháng là ok, nào hết hạn thì extend tiếp

5. Lưu hai thông tin quan trọng:
   - `Page ID`
   - `Page access token`

6. Quay lại **Upload Center** → bấm **Thêm Page** → dán Page ID và Page access token → bấm **Lưu Page**.

Token chỉ được lưu trong ứng dụng và không hiển thị lại. Khi token hết hạn, extend lại token trong Token Debugger rồi nhập lại bằng nút **Thêm Page**.

ak trước khi app có thể đăng video trực tiếp cho thì phải public app, xem hướng dẫn https://vt.tiktok.com/ZSxAAfx8D/


xong là ok, thêm page khác, còn lại thì đọc tham khảo thêm

## 5. Chọn trạng thái đăng

`video_state` có thể là:

```text
DRAFT
PUBLISHED
```

Web UI cũng có dropdown **Facebook Reels** để chọn draft hoặc publish ngay. Upload Facebook chỉ đăng Reel/video; link nguồn được tách sang nút **Comment source** riêng để lỗi comment không làm kết quả upload bị cảnh báo.

Nếu sau này làm OAuth callback trong Web UI, redirect URI gợi ý:

```text
http://localhost:8765/api/social/facebook/callback
```

URI này chưa tồn tại trong code hiện tại; chỉ thêm vào Meta app sau khi implement route callback.

## 6. Luồng upload Facebook Reels đang dùng

Theo Reels Publishing API, flow cơ bản:

### Bước 1: Start upload session

```text
POST https://graph.facebook.com/<GRAPH_VERSION>/<PAGE_ID>/video_reels
```

Payload:

```json
{
  "upload_phase": "start",
  "access_token": "PAGE_ACCESS_TOKEN"
}
```

Response trả về `video_id` và `upload_url`.

### Bước 2: Upload file video

Upload binary MP4 lên `rupload.facebook.com` bằng `video_id` đã nhận:

```text
POST https://rupload.facebook.com/video-upload/<GRAPH_VERSION>/<VIDEO_ID>
```

Header chính:

```text
Authorization: OAuth PAGE_ACCESS_TOKEN
offset: 0
file_size: <FILE_SIZE_IN_BYTES>
Content-Type: application/octet-stream
```

Body là binary của:

```text
project/<project>/output/final_video.mp4
```

### Bước 3: Finish/publish

Gọi lại Page reels endpoint với `upload_phase=finish`, `video_id`, caption/description và `video_state`.

`video_state` thường là:

```text
DRAFT
PUBLISHED
SCHEDULED
```

Mặc định repo để `PUBLISHED` để upload xong có thể đăng ngay. Đổi sang `DRAFT` nếu chưa muốn auto publish. Nếu cần thêm nguồn sau khi đã publish, bấm nút **Comment source** riêng. Lưu ý response publish Reel thường chỉ trả `{"success": true}`, nên code dùng `post_id` nếu có, fallback sang `video_id`, rồi chuẩn hóa thành full Page Post ID dạng `<PAGE_ID>_<POST_OR_VIDEO_ID>` trước khi comment nguồn:

```text
POST /<PAGE_ID>_<POST_ID_OR_VIDEO_ID>/comments
```

Nội dung comment:

```text
Nguồn: <source_url>
```

## 7. App Review và môi trường test

- Khi app còn development mode, thường chỉ tester/admin/developer dùng được.
- Muốn user/Page ngoài team dùng được cần App Review cho các permission Page.
- Chuẩn bị video demo cho Meta review, mô tả rõ app upload video do chính user chọn lên Page của họ.
- Nếu upload bị lỗi permission, kiểm tra:
  - token có đúng Page không
  - user có quyền tạo nội dung trên Page không
  - app đã được approve permission chưa
  - Graph API version có thay đổi yêu cầu không

## 8. Mapping với repo

Trong `web_server.py`, Facebook Reels dùng:

- file video từ `final_video_path_for_project(project)`
- caption từ `script.txt`
- hashtag mặc định `#FastScene`
- source URL từ `source/links.txt` hoặc `source/source.md`

Facebook Reels **không gửi title**. Caption cũng **không chứa source link**; source link chỉ được gửi khi bấm nút **Comment source** riêng sau khi upload ở trạng thái `PUBLISHED`.
