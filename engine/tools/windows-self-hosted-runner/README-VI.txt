AUREX WINDOWS SELF-HOSTED RUNNER
===================================

Gói này giúp máy Windows tự build AurexVideo.exe chuẩn mà không dùng phút
GitHub-hosted Actions.

CÁCH CHẠY
1. Giải nén ZIP vào Desktop.
2. Nhấp đúp START-HERE.cmd.
3. Chấp nhận quyền Administrator.
4. Script sẽ tự cài Git và Visual Studio Build Tools nếu máy còn thiếu.
5. Khi script hỏi token:
   - Mở https://github.com/tinbeta/escbase_m3/settings/actions/runners/new
   - Chọn Windows, kiến trúc x64.
   - Trong lệnh config.cmd, copy phần ký tự sau --token.
   - Dán token đó vào cửa sổ PowerShell rồi Enter.
6. Chờ đến khi cửa sổ hiện: Listening for Jobs.
7. Giữ cửa sổ PowerShell mở và nhắn Codex: runner online.

KẾT QUẢ BUILD
Sau khi workflow hoàn tất, file Windows nằm tại:
  C:\AurexVideoBuilds\0.1.8

Gồm AurexVideo.exe, chữ ký updater, engine Windows và manifest checksum.

LƯU Ý BẢO MẬT
- Token đăng ký chỉ dùng một lần và không được lưu trong ZIP.
- Không gửi file .runner hoặc .credentials cho người khác.
- Khi không build, có thể đóng cửa sổ runner.
- Muốn gỡ runner, mở PowerShell tại C:\AurexVideoRunner và chạy:
    .\config.cmd remove
