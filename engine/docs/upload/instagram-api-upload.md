# Đăng Instagram Reels qua Cloudflare R2

Luồng của AurexVideo là:

```text
final_video.mp4 → R2 public HTTPS URL → Instagram media container
→ poll FINISHED → media_publish → lưu media ID/permalink
```

## 1. Điều kiện Meta

- Tài khoản Instagram phải là Professional (Business hoặc Creator).
- Tạo app trên Meta for Developers và cấp quyền content publishing tương ứng với flow đã chọn.
- AurexVideo MVP hỗ trợ nhập thủ công `IG User ID` và access token. Token không được commit vào source code.
- `Instagram Login` dùng `https://graph.instagram.com`; `Facebook Login / Page token` dùng `https://graph.facebook.com`.

## 2. Chuẩn bị R2

1. Tạo bucket R2.
2. Tạo R2 API token có quyền Object Read & Write trên bucket này.
3. Gắn custom domain public cho bucket, ví dụ `https://media.example.com`.
4. Không dùng URL yêu cầu đăng nhập hoặc URL signed quá ngắn; Instagram cần tự GET video từ internet.

Trong Upload Center, nhập:

- Cloudflare Account ID
- Bucket name
- Access Key ID và Secret Access Key
- Public Base URL
- Object prefix, mặc định là `instagram`

R2 public URL là nguồn tạm cho Instagram. Sau khi đăng thành công, AurexVideo mặc định xóa object tạm; bật “Giữ file trên R2” nếu muốn lưu lại.

## 3. Cấu hình AurexVideo

Trong Upload Center → Instagram Reels → Cấu hình Instagram + R2:

- Nhập IG User ID và access token.
- Chọn API login mode.
- Giữ Graph API version theo version đang bật trong Meta app.
- Nhập toàn bộ thông tin R2.

Thông tin được lưu ở `config/social-upload.json` với permission `0600`.

## 4. Ghi chú vận hành

- Video render phải là MP4 có URL public HTTPS. Reels nên dùng H.264/HEVC + AAC, khung dọc 9:16.
- Instagram xử lý container bất đồng bộ; app sẽ poll tới `FINISHED` trước khi gọi `media_publish`.
- Nếu container lỗi hoặc timeout, file R2 được giữ lại để kiểm tra. Có thể xóa thủ công sau khi xác định nguyên nhân.
- Không dùng Playwright/Selenium để đăng Instagram; API chính thức ổn định hơn và tránh CAPTCHA/session.
