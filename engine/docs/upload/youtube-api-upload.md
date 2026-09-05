# Hướng dẫn kết nối YouTube API để upload video

Sau khi tạo OAuth Client trên Google Cloud, bạn có thể nhập Client ID và Client Secret trực tiếp trong Upload Center. Không cần mở hay sửa file cấu hình.

## 1. Cần chuẩn bị

- Một Google account có quyền upload lên kênh YouTube cần dùng.
- Một Google Cloud project.
- YouTube Data API v3 đã được bật.
- OAuth Client ID dạng Web application.
- `Client ID` và `Client Secret` để dán trực tiếp vào Upload Center.

## 2. Tạo Google Cloud project

1. Vào Google Cloud Console: https://console.cloud.google.com/
2. Tạo project mới, hoặc chọn project đang dùng.
![alt text](../assets/upload/image.png)
3. Vào **APIs & Services** → **Library**.
4. Tìm và enable **YouTube Data API v3**.
![alt text](../assets/upload/image-1.png)
![alt text](../assets/upload/image-3.png)

Tài liệu chính thức:
- Upload video: https://developers.google.com/youtube/v3/guides/uploading_a_video
- `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- OAuth YouTube Data API: https://developers.google.com/youtube/v3/guides/authentication

## 3. Cấu hình OAuth consent screen

1. Vào **APIs & Services** → **OAuth consent screen** → **Get Started**.

![alt text](../assets/upload/image-4.png)

2. Điền app name và support email → ở Audience chọn **External** → điền contact email → Continue → Finish → Create.
3. Khi mới thử, vào lại **Audience** → **Test users** → thêm email Google của bạn.
4. Khi đã chạy ổn, chuyển **Publishing status** sang **In production** (Public). Với app External còn ở trạng thái Testing, refresh token thường chỉ sống khoảng 7 ngày; chuyển sang Production giúp token không bị giới hạn bởi thời hạn thử nghiệm này.

Lưu ý: Production không có nghĩa token sống vĩnh viễn. Google vẫn có thể thu hồi token khi người dùng gỡ quyền, thay đổi bảo mật, token không được dùng lâu ngày hoặc ứng dụng vi phạm chính sách.

## 4. Tạo OAuth Client ID

1. Vào **Clients**.
2. Chọn **Create Client**.
3. Application type: **Web application**.
4. Ở phần **Authorized redirect URIs**, thêm chính xác:

```text
http://localhost:8765/api/social/youtube/callback
```

Bản AurexVideo desktop luôn chạy OAuth ở port `8765`, nên URI trên chỉ cần khai báo một lần. Ô Redirect URI trong app cũng tự kiểm tra theo đúng địa chỉ và port mà app đang dùng.

Nếu đổi port chạy AurexVideo thì Authorized redirect URI trong Google Cloud cũng phải đổi thành:

```text
http://localhost:<PORT>/api/social/youtube/callback
```

5. Lưu lại hai thông tin:
   - `Client ID`
   - `Client Secret`

## 5. Nhập OAuth key và kết nối trong Upload Center

1. Mở AurexVideo và vào **Upload Center**.
2. Chọn project đã render xong và có `final_video.mp4`.
3. Bấm **Thêm channel**. Nếu chưa có OAuth key, AurexVideo sẽ mở form nhập trực tiếp.
4. Dán `Client ID`, `Client Secret`, kiểm tra Redirect URI rồi bấm **Lưu và kết nối**.
5. AurexVideo mở Google OAuth trong trình duyệt mặc định. Đăng nhập đúng Google account có kênh YouTube cần upload và cho phép quyền upload.
6. Quay lại AurexVideo, chọn:
   - title
   - description
   - privacy: `private`, `unlisted`, hoặc `public`
7. Bấm **Upload YouTube**.

Bạn có thể bấm **OAuth key** để thay Client ID hoặc Client Secret đã lưu. Các khóa và token không được hiển thị lại trên giao diện.

Mặc định metadata trong AurexVideo:
- title lấy từ dòng đầu của `script.txt`
- description gồm dòng đầu, nguồn nếu có trong `source/links.txt` hoặc `source/source.md`, và hashtag
- category YouTube: `22`
- `selfDeclaredMadeForKids`: `false`

## 6. Lỗi thường gặp

### Redirect URI mismatch

Kiểm tra URI trong Google Cloud và ô Redirect URI trong AurexVideo phải giống tuyệt đối:

```text
http://localhost:8765/api/social/youtube/callback
```

### Google không trả `refresh_token`

Thường xảy ra khi account đã từng cấp quyền cho app. Cách xử lý:

1. Vào Google Account → Security → Third-party access.
2. Gỡ quyền app OAuth đó.
3. Bấm **Thêm channel** lại.

AurexVideo dùng `access_type=offline` và `prompt=consent` để xin refresh token.

### Token hết hạn hoặc bị thu hồi

Nếu upload báo `invalid_grant`, token hết hạn hoặc kênh mất kết nối, bấm **Thêm channel** và đăng nhập/cấp quyền lại. Kênh sẽ được cập nhật bằng token mới.

Nếu app vẫn ở trạng thái **Testing**, hãy chuyển OAuth consent screen sang **In production** (Public) để tránh giới hạn refresh token khoảng 7 ngày của app thử nghiệm. Sau đó kết nối lại channel một lần để nhận token mới.

### App đang Testing nên account khác không đăng nhập được

Thêm email đó vào **Test users**, hoặc chuyển app sang **In production**. Google có thể yêu cầu verification tùy scope và cách bạn phân phối ứng dụng.

### Upload xong nhưng chưa public

Upload Center mặc định chọn `public` để đăng ngay. Nếu đổi sang `private` hoặc `unlisted`, vào YouTube Studio để review/publish. AurexVideo trả link video và link Studio sau khi upload thành công.
