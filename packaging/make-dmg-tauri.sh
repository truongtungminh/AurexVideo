#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAURI_DIR="$SCRIPT_DIR/../tauri-src"
APP_NAME="AurexVideo"
VERSION="0.2.3"
STAGE="/tmp/aurex-tauri-stage"
DMG_DEST="$HOME/Desktop/AurexVideo-$VERSION-tauri.dmg"

rm -rf "$STAGE"
mkdir -p "$STAGE/$APP_NAME.app/Contents/MacOS"
mkdir -p "$STAGE/$APP_NAME.app/Contents/Resources"

# Binary
cp "$TAURI_DIR/target/release/aurexvideo" "$STAGE/$APP_NAME.app/Contents/MacOS/$APP_NAME"

# Icon
cp "$TAURI_DIR/icons/icon.png" "$STAGE/$APP_NAME.app/Contents/Resources/AurexVideo.png"
# Also embed icns for Finder
cp "$SCRIPT_DIR/packaging/AurexVideo.icns" "$STAGE/$APP_NAME.app/Contents/Resources/AurexVideo.icns" 2>/dev/null || true

# Info.plist
cat > "$STAGE/$APP_NAME.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>app.aurexvideo</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleExecutable</key><string>$APP_NAME</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>10.15</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>CFBundleIconFile</key><string>AurexVideo.icns</string>
</dict>
</plist>
PLIST

# Code sign (ad-hoc)
echo "== codesign =="
codesign --force --deep --sign - "$STAGE/$APP_NAME.app"

# Verify
codesign -v "$STAGE/$APP_NAME.app" && echo "codesign OK"

# DMG with drag-to-Applications
rm -rf /tmp/aurex-tauri-rw.dmg
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE/$APP_NAME.app" -ov -format UDRW /tmp/aurex-tauri-rw.dmg
# mount, add Applications symlink
DEV=$(hdiutil attach /tmp/aurex-tauri-rw.dmg -nobrowse | grep Volumes | awk -F'/Volumes/' '{print $2}')
ln -s /Applications "/Volumes/$DEV/Applications"
# set layout via AppleScript
osascript <<SCPT 2>/dev/null || true
tell application "Finder"
    tell disk "$DEV"
        open
        tell container window
            set current view to icon view
            set bounds to {100, 100, 620, 460}
        end tell
        tell icon file "$APP_NAME.app"
            set position to {150, 120}
        end tell
        tell icon file "Applications"
            set position to {430, 120}
        end tell
        close
    end tell
end tell
SCPT
sync
hdiutil detach "/Volumes/$DEV" || hdiutil detach "/Volumes/$DEV" -force
rm -f "$DMG_DEST"
hdiutil convert /tmp/aurex-tauri-rw.dmg -format UDZO -o "$DMG_DEST"
rm -f /tmp/aurex-tauri-rw.dmg
echo "== DMG at $DMG_DEST =="
ls -la "$DMG_DEST"
