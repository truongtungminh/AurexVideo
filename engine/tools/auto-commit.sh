#!/bin/bash
# auto-commit.sh — Tự động commit mọi thay đổi trong repo engine của AurexVideo.
# Được gọi định kỳ bởi LaunchAgent com.aurexvideo.autocommit (mỗi 2 phút).
# - Không có gì thay đổi  -> thoát ngay.
# - File đang được ghi     -> chờ lần chạy sau (debounce 60s, tránh commit nửa chừng).
# - Có thay đổi đã ổn định -> git add -A && commit.

REPO="/Users/truongminh/Library/Application Support/app.aurexvideo/engine"
LOG="$HOME/Library/Logs/aurexvideo-auto-commit.log"
PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
GIT="/usr/bin/git"
mkdir -p "$(dirname "$LOG")"

cd "$REPO" || { echo "$(date '+%F %T') ERROR: khong vao duoc $REPO" >> "$LOG"; exit 1; }

# 1) Không có thay đổi nào -> xong
if ! "$GIT" status --porcelain | grep -q .; then
  exit 0
fi

# 2) Debounce: nếu có file nào thay đổi trong 60 giây qua -> chờ lần chạy sau
if find "$REPO" -type f -mmin -1 -not -path "$REPO/.git/*" 2>/dev/null | grep -q .; then
  exit 0
fi

# 3) Commit tất cả
"$GIT" add -A
N=$("$GIT" diff --cached --name-only | wc -l | tr -d ' ')
if [ "$N" -eq 0 ]; then
  exit 0
fi
MSG="auto: update $(date '+%Y-%m-%d %H:%M') - $N file(s) thay doi"

if "$GIT" commit -m "$MSG" >> "$LOG" 2>&1; then
  echo "$(date '+%F %T') OK: $MSG" >> "$LOG"
else
  echo "$(date '+%F %T') ERROR: commit that bai (co the do khoa git, se thu lai lan sau)" >> "$LOG"
  exit 1
fi
exit 0
