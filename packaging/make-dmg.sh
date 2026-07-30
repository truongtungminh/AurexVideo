#!/usr/bin/env bash
#
# make-dmg.sh — Build the AurexVideo .app and package a distributable DMG
# with a drag-to-Applications layout (standard macOS installer window).
#
# Usage:
#   ./make-dmg.sh [version] [output.dmg]
#   version   : app version (default: read from engine/VERSION, fallback 0.2.4)
#   output    : output DMG path (default: ~/Desktop/AurexVideo-<version>-native.dmg)
#
# What it does:
#   1. Compile packaging/aurexvideo-ui.swift -> AurexVideo.app/Contents/MacOS/aurexvideo-ui
#   2. Copy Info.plist + AurexVideo.icns (app icon) + AurexVideoLogo.png (in-window logo)
#   3. Ad-hoc codesign (deep)
#   4. Stage app + /Applications symlink in a temp folder
#   5. Set a clean 2-column window layout via AppleScript (icon size, spacing, background)
#   6. Create a compressed (UDZO) DMG
#
# Requirements: swiftc, iconutil, hdiutil, codesign, osascript (macOS only).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

# ---- resolve version ----
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  if [[ -f engine/VERSION ]]; then
    VERSION="$(tr -d '[:space:]' < engine/VERSION)"
  else
    VERSION="0.2.4"
  fi
fi

OUT_DMG="${2:-$HOME/Desktop/AurexVideo-${VERSION}-native.dmg}"

APP_NAME="AurexVideo.app"
BUILD_DIR="/tmp/aurex-build"
APP="$BUILD_DIR/$APP_NAME"
STAGE="/tmp/aurex-dmg-stage"
MOUNT="/tmp/aurex-dmg-mount"

echo "==> Building AurexVideo $VERSION"
echo "    source : $ROOT"
echo "    output : $OUT_DMG"

# ---- 1. compile ----
echo "==> Compiling Swift app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -parse-as-library -o "$APP/Contents/MacOS/aurexvideo-ui" packaging/aurexvideo-ui.swift

# ---- 2. resources ----
echo "==> Copying resources"
# Info.plist (substitute version)
sed "s/__VERSION__/$VERSION/g" packaging/Info.plist > "$APP/Contents/Info.plist"
# app icon: prefer prebuilt AurexVideo.icns, else generate from aurexvideo-logo.png
if [[ -f packaging/AurexVideo.icns ]]; then
  cp packaging/AurexVideo.icns "$APP/Contents/Resources/AurexVideo.icns"
elif [[ -f assets/aurexvideo-logo.png ]]; then
  ICONSET="$(mktemp -d)/iconset"
  mkdir -p "$ICONSET"
  SRC=assets/aurexvideo-logo.png
  for sz in 16 32 64 128 256 512 1024; do
    sips -s format png --resampleWidth "$sz" "$SRC" --out "$ICONSET/icon_${sz}.png" >/dev/null 2>&1
  done
  cp "$ICONSET/icon_32.png"  "$ICONSET/icon_16x16@2x.png"
  cp "$ICONSET/icon_64.png"  "$ICONSET/icon_32x32@2x.png"
  cp "$ICONSET/icon_256.png" "$ICONSET/icon_128x128@2x.png"
  cp "$ICONSET/icon_512.png" "$ICONSET/icon_256x256@2x.png"
  cp "$ICONSET/icon_1024.png" "$ICONSET/icon_512x512@2x.png"
  iconutil --convert icns "$ICONSET" --output "$APP/Contents/Resources/AurexVideo.icns"
fi
# in-window logo
[[ -f assets/aurexvideo-logo.png ]] && cp assets/aurexvideo-logo.png "$APP/Contents/Resources/AurexVideoLogo.png"

# ---- 3. codesign ----
echo "==> Codesigning (ad-hoc)"
codesign --force --deep --sign - "$APP"

# ---- 4. stage ----
echo "==> Staging DMG layout"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/$APP_NAME"
ln -s /Applications "$STAGE/Applications"

# ---- 5. create RW DMG (uncompressed, so layout persists) ----
echo "==> Creating RW DMG (layout stage)"
RW_DMG="${OUT_DMG%.dmg}-rw.dmg"
rm -f "$RW_DMG"
hdiutil create -volname "AurexVideo" -srcfolder "$STAGE" -ov -format UDRW "$RW_DMG"

# ---- 6. apply window layout (mount, AppleScript, unmount) ----
echo "==> Applying window layout"
rm -rf "$MOUNT"; mkdir -p "$MOUNT"
# attach RW (Finder may auto-open; suppress with -nobrowse + manual open)
hdiutil attach "$RW_DMG" -nobrowse -mountpoint "$MOUNT" >/dev/null
# give Finder a moment to register the disk
sleep 2
osascript <<EOF 2>/dev/null || true
tell application "Finder"
  tell disk "AurexVideo"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {400, 200, 920, 540}
    set theViewOptions to the icon view options of container window
    set icon size of theViewOptions to 128
    set arrangement of theViewOptions to not arranged
    set position of item "$APP_NAME" of container window to {170, 200}
    set position of item "Applications" of container window to {420, 200}
    update without registering applications
    close
    delay 1
  end tell
end tell
EOF
hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
sleep 1

# ---- 7. convert to compressed UDZO (final distributable) ----
echo "==> Compressing to UDZO"
rm -f "$OUT_DMG"
hdiutil convert "$RW_DMG" -format UDZO -o "$OUT_DMG" | tail -1
rm -f "$RW_DMG"

# ---- 7. final codesign verification ----
echo "==> Verifying"
codesign -v --verbose=2 "$APP" 2>&1 | tail -1 || true
ls -la "$OUT_DMG"
echo "==> Done: $OUT_DMG"
