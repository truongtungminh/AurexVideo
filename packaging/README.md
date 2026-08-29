# AurexVideo macOS packaging

`build-dmg.sh` rebuilds the legacy Swift/WKWebView shell in `aurexvideo-ui.swift`
as an arm64 `AurexVideo.app` and a compressed, drag-to-Applications DMG.

```bash
cd engine
bash packaging/build-dmg.sh --dry-run
bash packaging/build-dmg.sh
```

The default artifact is `~/Desktop/AurexVideo-<VERSION>-arm64.dmg`. Supply an
explicit path for CI or release builds:

```bash
bash packaging/build-dmg.sh \
  --output "$PWD/dist/AurexVideo-0.2.4-arm64.dmg" \
  --app-output "$PWD/dist/AurexVideo.app"
```

Existing outputs are protected; use `--force` only when intentionally replacing
the requested output. The script requires macOS on arm64 plus the Xcode command
line tools (`swiftc`, `lipo`, `sips`, `iconutil`, `plutil`, `codesign`, and
`hdiutil`). It ad-hoc signs the generated bundle, so it is not notarized.

The payload is strictly allow-listed: the Swift source, the app logo, and the
DMG background. It does not copy `studio/`, engine/runtime files, user config,
logs, `.playwright-cli`, backups, secrets, or source `.DS_Store` files. Finder
may generate a volume-local `.DS_Store` solely to retain the DMG's icon layout
and background; that metadata is created during packaging, never copied from
the workspace.
