#!/usr/bin/env bash
#
# Build the standalone Swift shell as an arm64 AurexVideo.app and a compressed
# drag-to-Applications DMG. The package deliberately has a small allow-list:
# the Swift shell and the two branding assets below. It never copies engine
# contents, studio data, user configuration, logs, backups, or secrets.

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SWIFT_SOURCE="$SCRIPT_DIR/aurexvideo-ui.swift"
readonly LOGO_SOURCE="$REPO_ROOT/assets/aurexvideo-logo.png"
readonly BACKGROUND_SOURCE="$REPO_ROOT/assets/dmg-background.png"
readonly APP_NAME="AurexVideo"
readonly BUNDLE_ID="app.aurexvideo"
readonly MINIMUM_MACOS="11.0"

VERSION=""
OUTPUT_DMG=""
APP_OUTPUT=""
FORCE=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: build-dmg.sh [options]

Build the arm64 AurexVideo Swift shell and package it in a DMG.

Options:
  --version VERSION     Bundle version (default: contents of ../VERSION)
  --output PATH         DMG destination (default: ~/Desktop/AurexVideo-<version>-arm64.dmg)
  --app-output PATH     Also copy the signed clean .app bundle to PATH
  --force               Replace an existing requested output file/directory
  --dry-run             Validate inputs and print the build plan without writing files
  -h, --help            Show this help

Only these repository files are packaged: packaging/aurexvideo-ui.swift,
assets/aurexvideo-logo.png, and (for the Finder DMG background)
assets/dmg-background.png. No engine or studio directory is copied.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

while (($#)); do
  case "$1" in
    --version)
      (($# >= 2)) || die "--version requires a value"
      VERSION="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die "--output requires a path"
      OUTPUT_DMG="$2"
      shift 2
      ;;
    --app-output)
      (($# >= 2)) || die "--app-output requires a path"
      APP_OUTPUT="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1 (run with --help)"
      ;;
  esac
done

[[ "$(uname -s)" == "Darwin" ]] || die "this script must run on macOS"
[[ "$(uname -m)" == "arm64" ]] || die "this script only builds the macOS arm64 distribution"

if [[ -z "$VERSION" ]]; then
  [[ -f "$REPO_ROOT/VERSION" ]] || die "missing version file: $REPO_ROOT/VERSION"
  VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/VERSION")"
fi
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){0,2}([+-][A-Za-z0-9._-]+)?$ ]] || die "invalid version: $VERSION"

if [[ -z "$OUTPUT_DMG" ]]; then
  OUTPUT_DMG="$HOME/Desktop/AurexVideo-${VERSION}-arm64.dmg"
fi
[[ "$OUTPUT_DMG" == *.dmg ]] || die "--output must end in .dmg"

for command_name in swiftc lipo sips iconutil plutil codesign hdiutil ditto osascript SetFile; do
  require_command "$command_name"
done
[[ -f "$SWIFT_SOURCE" ]] || die "missing Swift source: $SWIFT_SOURCE"
[[ -f "$LOGO_SOURCE" ]] || die "missing icon/logo asset: $LOGO_SOURCE"
[[ -f "$BACKGROUND_SOURCE" ]] || die "missing DMG background asset: $BACKGROUND_SOURCE"

if ((DRY_RUN)); then
  cat <<PLAN
Dry run: no files will be written.
  source:     $SWIFT_SOURCE
  icon/logo:  $LOGO_SOURCE
  background: $BACKGROUND_SOURCE
  version:    $VERSION
  app:        clean temporary ${APP_NAME}.app (arm64, macOS $MINIMUM_MACOS+)
  DMG:        $OUTPUT_DMG
  app export: ${APP_OUTPUT:-not requested}
  contents:   AurexVideo.app, Applications symlink, generated Finder background metadata
PLAN
  exit 0
fi

if [[ -e "$OUTPUT_DMG" && $FORCE -ne 1 ]]; then
  die "output already exists: $OUTPUT_DMG (pass --force to replace it)"
fi
if [[ -n "$APP_OUTPUT" && -e "$APP_OUTPUT" && $FORCE -ne 1 ]]; then
  die "app output already exists: $APP_OUTPUT (pass --force to replace it)"
fi

mkdir -p "$(dirname "$OUTPUT_DMG")"
if [[ -n "$APP_OUTPUT" ]]; then
  mkdir -p "$(dirname "$APP_OUTPUT")"
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aurexvideo-dmg.XXXXXX")"
APP_BUNDLE="$WORK_DIR/${APP_NAME}.app"
STAGE_DIR="$WORK_DIR/stage"
RW_DMG="$WORK_DIR/${APP_NAME}-rw.dmg"
FINAL_DMG="$WORK_DIR/${APP_NAME}-${VERSION}-arm64.dmg"
MOUNT_DIR="$WORK_DIR/mount"
MOUNTED=0

cleanup() {
  if ((MOUNTED)); then
    hdiutil detach "$MOUNT_DIR" -quiet >/dev/null 2>&1 || hdiutil detach "$MOUNT_DIR" -force -quiet >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

write_info_plist() {
  cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleVersion</key><string>${VERSION}</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleExecutable</key><string>aurexvideo-ui</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AurexVideo</string>
  <key>LSMinimumSystemVersion</key><string>${MINIMUM_MACOS}</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST
  plutil -lint "$APP_BUNDLE/Contents/Info.plist" >/dev/null
}

make_icon() {
  local iconset="$WORK_DIR/AurexVideo.iconset"
  mkdir -p "$iconset"
  sips -z 16 16 "$LOGO_SOURCE" --out "$iconset/icon_16x16.png" >/dev/null
  sips -z 32 32 "$LOGO_SOURCE" --out "$iconset/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$LOGO_SOURCE" --out "$iconset/icon_32x32.png" >/dev/null
  sips -z 64 64 "$LOGO_SOURCE" --out "$iconset/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$LOGO_SOURCE" --out "$iconset/icon_128x128.png" >/dev/null
  sips -z 256 256 "$LOGO_SOURCE" --out "$iconset/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$LOGO_SOURCE" --out "$iconset/icon_256x256.png" >/dev/null
  sips -z 512 512 "$LOGO_SOURCE" --out "$iconset/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$LOGO_SOURCE" --out "$iconset/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$LOGO_SOURCE" --out "$iconset/icon_512x512@2x.png" >/dev/null
  iconutil --convert icns "$iconset" --output "$APP_BUNDLE/Contents/Resources/AurexVideo.icns"
}

assert_clean_bundle() {
  local forbidden
  for forbidden in studio .DS_Store .playwright-cli backups backup logs secrets; do
    if find "$APP_BUNDLE" -name "$forbidden" -print -quit | grep -q .; then
      die "refusing to package forbidden path: $forbidden"
    fi
  done
}

apply_finder_layout() {
  # Finder writes a volume-local .DS_Store to remember icon positions/background.
  # It is generated here; no .DS_Store is copied from the repository or user data.
  mkdir -p "$MOUNT_DIR"
  hdiutil attach "$RW_DMG" -nobrowse -mountpoint "$MOUNT_DIR" >/dev/null
  MOUNTED=1
  if ! osascript <<'APPLESCRIPT'
tell application "Finder"
  tell disk "AurexVideo"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {400, 200, 1060, 600}
    set viewOptions to the icon view options of container window
    set icon size of viewOptions to 112
    set arrangement of viewOptions to not arranged
    set background picture of viewOptions to file ".background:background.png"
    set position of item "AurexVideo.app" of container window to {185, 205}
    set position of item "Applications" of container window to {475, 205}
    update without registering applications
    close
  end tell
end tell
APPLESCRIPT
  then
    printf 'warning: Finder layout could not be saved; DMG remains installable.\n' >&2
  fi
  hdiutil detach "$MOUNT_DIR" -quiet >/dev/null
  MOUNTED=0
}

printf '==> Building %s %s (arm64)\n' "$APP_NAME" "$VERSION"
mkdir -p "$APP_BUNDLE/Contents/MacOS" "$APP_BUNDLE/Contents/Resources"
swiftc -O -parse-as-library -target "arm64-apple-macosx${MINIMUM_MACOS}" \
  -o "$APP_BUNDLE/Contents/MacOS/aurexvideo-ui" "$SWIFT_SOURCE"
[[ "$(lipo -archs "$APP_BUNDLE/Contents/MacOS/aurexvideo-ui")" == *arm64* ]] || die "compiled executable is not arm64"
write_info_plist
make_icon
cp "$LOGO_SOURCE" "$APP_BUNDLE/Contents/Resources/AurexVideoLogo.png"
assert_clean_bundle

printf '==> Ad-hoc signing clean app bundle\n'
codesign --force --deep --sign - --timestamp=none "$APP_BUNDLE"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

printf '==> Staging allow-listed DMG contents\n'
mkdir -p "$STAGE_DIR/.background"
ditto "$APP_BUNDLE" "$STAGE_DIR/${APP_NAME}.app"
ln -s /Applications "$STAGE_DIR/Applications"
cp "$BACKGROUND_SOURCE" "$STAGE_DIR/.background/background.png"
SetFile -a V "$STAGE_DIR/.background"
assert_clean_bundle

printf '==> Creating compressed DMG\n'
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE_DIR" -ov -format UDRW "$RW_DMG" >/dev/null
apply_finder_layout
hdiutil convert "$RW_DMG" -format UDZO -o "$FINAL_DMG" >/dev/null
hdiutil verify "$FINAL_DMG" >/dev/null

if [[ -n "$APP_OUTPUT" ]]; then
  if [[ -e "$APP_OUTPUT" ]]; then
    rm -rf "$APP_OUTPUT"
  fi
  ditto "$APP_BUNDLE" "$APP_OUTPUT"
fi
if [[ -e "$OUTPUT_DMG" ]]; then
  rm -f "$OUTPUT_DMG"
fi
mv "$FINAL_DMG" "$OUTPUT_DMG"

printf '==> Done\n  DMG: %s\n' "$OUTPUT_DMG"
if [[ -n "$APP_OUTPUT" ]]; then
  printf '  App: %s\n' "$APP_OUTPUT"
fi
