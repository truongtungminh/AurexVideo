import SwiftUI
import WebKit

// MARK: - Brand logo (from AurexVideoLogo.png in bundle, fallback to system icon)
func aurexBrandLogo() -> Image {
    if let url = Bundle.main.url(forResource: "AurexVideoLogo", withExtension: "png"),
       let img = NSImage(contentsOf: url) {
        return Image(nsImage: img).resizable()
    }
    return Image(systemName: "play.rectangle.fill").resizable()
}

// MARK: - Shared state
final class AppState: ObservableObject {
    @Published var stage: Stage = .language
    @Published var progress: Double = 0
    @Published var statusText: String = ""
    @Published var speedText: String = ""
    @Published var etaText: String = ""
    @Published var errorText: String = ""

    enum Stage { case language, downloading, ready, failed }

    let engineBase = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Library/Application Support/app.aurexvideo")
    let engineURL = URL(string: "https://github.com/truongtungminh/AurexVideo/releases/download/v0.2.3/aurexvideo-engine-0.2.3.tar.gz")!
    // Python giữ từ GitHub (nhẹ ~80MB) — các component nặng tải từ chính chủ
    let pythonURL = URL(string: "https://github.com/truongtungminh/AurexVideo/releases/download/v0.2.3/aurexvideo-python-0.2.3.tar.gz")!
    // whisper-base từ HuggingFace chính chủ (Systran)
    let whisperFiles = [
        "config.json", "model.bin", "tokenizer.json", "vocabulary.txt",
        "preprocessor_config.json", "tokenizer_config.json"
    ]
    let whisperBase = "https://huggingface.co/Systran/faster-whisper-base/resolve/main/"
    // ffmpeg từ evermeet.cx (mac ffmpeg chính chủ)
    let ffmpegURL = URL(string: "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip")!

    var lang = "en"

    func proceed(_ lang: String) {
        self.lang = lang
        stage = .downloading
        let marker = NSHomeDirectory() + "/.aurexvideo-autolang"
        try? lang.write(toFile: marker, atomically: true, encoding: .utf8)
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootstrap()
        }
    }

    func retry() {
        stage = .downloading
        progress = 0
        speedText = ""
        etaText = ""
        errorText = ""
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootstrap()
        }
    }

    // MARK: - Multi-source bootstrap
    func bootstrap() {
        let fm = FileManager.default
        let engineDir = engineBase.appendingPathComponent("engine")
        let runtimeDir = engineBase.appendingPathComponent("runtime")
        let pythonBase = engineBase.appendingPathComponent("python_base")
        let engineMarker = engineBase.appendingPathComponent(".engine_ready")
        let runtimeMarker = engineBase.appendingPathComponent(".runtime_ready")

        // B1: Nếu chưa có runtime (python + chromium + ffmpeg + whisper) -> tải từ chính chủ
        let needRuntime = !fm.fileExists(atPath: runtimeMarker.path)
            || !fm.fileExists(atPath: pythonBase.appendingPathComponent("bin/python3.11").path)
            || !fm.fileExists(atPath: runtimeDir.appendingPathComponent("ms-playwright/chromium_headless_shell-1228").path)
            || !fm.fileExists(atPath: runtimeDir.appendingPathComponent("models/faster-whisper-base/model.bin").path)

        if needRuntime {
            downloadPythonThenComponents(runtimeMarker: runtimeMarker, engineMarker: engineMarker, engineDir: engineDir)
            return
        }

        fetchEngine(engineMarker: engineMarker, engineDir: engineDir)
    }

    // Tải Python (GitHub, nhẹ) trước, sau đó dùng Python đó chạy playwright install + tải HF/ffmpeg
    func downloadPythonThenComponents(runtimeMarker: URL, engineMarker: URL, engineDir: URL) {
        DispatchQueue.main.async {
            self.statusText = "Đang tải Python (~80MB)…"
            self.progress = 0
            self.speedText = ""
            self.etaText = ""
        }
        downloadArchive(from: pythonURL, to: "aurexvideo-python.tar.gz", marker: ".python_ready", label: "python") { [weak self] ok in
            guard ok, let self else { return }
            self.unpackPython { ok2 in
                guard ok2 else { return }
                self.downloadComponents(runtimeMarker: runtimeMarker, engineMarker: engineMarker, engineDir: engineDir)
            }
        }
    }

    // Tải song song: whisper (HF) + ffmpeg (evermeet) + chromium (playwright install)
    func downloadComponents(runtimeMarker: URL, engineMarker: URL, engineDir: URL) {
        let group = DispatchGroup()
        var whisperOK = true, ffmpegOK = true, chromiumOK = true

        // (1) whisper-base từ HuggingFace
        group.enter()
        DispatchQueue.main.async { self.statusText = "Đang tải whisper model (HuggingFace)…" }
        downloadWhisper { ok in whisperOK = ok; group.leave() }

        // (2) ffmpeg từ evermeet.cx
        group.enter()
        DispatchQueue.main.async { self.statusText = "Đang tải ffmpeg (evermeet)…" }
        downloadFFmpeg { ok in ffmpegOK = ok; group.leave() }

        // (3) chromium qua playwright install (tự tải CDN chính chủ)
        group.enter()
        DispatchQueue.main.async { self.statusText = "Đang tải Chromium (Playwright CDN)…" }
        installChromium { ok in chromiumOK = ok; group.leave() }

        group.notify(queue: .main) { [weak self] in
            guard let self else { return }
            guard whisperOK, ffmpegOK, chromiumOK else {
                self.errorText = "Tải component thất bại (whisper=\(whisperOK) ffmpeg=\(ffmpegOK) chromium=\(chromiumOK))"
                self.stage = .failed
                return
            }
            try? FileManager.default.createFile(atPath: runtimeMarker.path, contents: Data())
            self.fetchEngine(engineMarker: engineMarker, engineDir: engineDir)
        }
    }

    func downloadWhisper(completion: @escaping (Bool) -> Void) {
        let destDir = engineBase.appendingPathComponent("runtime/models/faster-whisper-base")
        let fm = FileManager.default
        try? fm.createDirectory(at: destDir, withIntermediateDirectories: true)
        let group = DispatchGroup()
        var allOK = true
        for f in whisperFiles {
            group.enter()
            let url = URL(string: whisperBase + f)!
            let dest = destDir.appendingPathComponent(f)
            downloadFile(from: url, to: dest) { ok in
                if !ok { allOK = false }
                group.leave()
            }
        }
        group.notify(queue: .main) { completion(allOK) }
    }

    func downloadFFmpeg(completion: @escaping (Bool) -> Void) {
        let zipDest = engineBase.appendingPathComponent("ffmpeg.zip")
        downloadFile(from: ffmpegURL, to: zipDest) { ok in
            guard ok else { completion(false); return }
            let binDir = self.engineBase.appendingPathComponent("runtime/bin")
            try? FileManager.default.createDirectory(at: binDir, withIntermediateDirectories: true)
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
            proc.arguments = ["-o", zipDest.path, "-d", binDir.path]
            proc.terminationHandler = { p in
                let ff = binDir.appendingPathComponent("ffmpeg")
                try? FileManager.default.removeItem(at: zipDest)
                completion(p.terminationStatus == 0 && FileManager.default.fileExists(atPath: ff.path))
            }
            try? proc.run()
        }
    }

    func installChromium(completion: @escaping (Bool) -> Void) {
        let py = engineBase.appendingPathComponent("python_base/bin/python3.11")
        guard FileManager.default.fileExists(atPath: py.path) else { completion(false); return }
        let proc = Process()
        proc.executableURL = py
        proc.arguments = ["-m", "playwright", "install", "chromium-headless-shell"]
        proc.currentDirectoryURL = engineBase
        var env = ProcessInfo.processInfo.environment
        // KHÔNG set PYTHONHOME — gây lỗi import module. Chỉ cần PATH trỏ python_base/bin.
        env["PATH"] = engineBase.appendingPathComponent("python_base/bin").path + ":" + (env["PATH"] ?? "")
        env["PLAYWRIGHT_BROWSERS_PATH"] = engineBase.appendingPathComponent("runtime/ms-playwright").path
        proc.environment = env
        proc.terminationHandler = { p in
            let ok = p.terminationStatus == 0
                && FileManager.default.fileExists(atPath: self.engineBase.appendingPathComponent("runtime/ms-playwright/chromium_headless_shell-1228").path)
            completion(ok)
        }
        try? proc.run()
    }

    // Generic file download (URLSession background download)
    func downloadFile(from url: URL, to dest: URL, completion: @escaping (Bool) -> Void) {
        let session = URLSession.shared
        let task = session.downloadTask(with: url) { [weak self] tmp, _, err in
            guard let self, let tmp, err == nil else {
                try? ("[download] error \(url.lastPathComponent): \(err?.localizedDescription ?? "")".write(to: self?.engineBase.appendingPathComponent("app.log") ?? URL(fileURLWithPath: "/tmp/aurex.log"), atomically: true, encoding: .utf8))
                completion(false); return
            }
            let fm = FileManager.default
            try? fm.removeItem(at: dest)
            do { try fm.moveItem(at: tmp, to: dest); completion(true) }
            catch { completion(false) }
        }
        task.resume()
    }

    func unpackPython(completion: @escaping (Bool) -> Void) {
        let archive = engineBase.appendingPathComponent("aurexvideo-python.tar.gz")
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        proc.arguments = ["xzf", archive.path, "-C", engineBase.path]
        proc.terminationHandler = { p in
            try? FileManager.default.removeItem(at: archive)
            completion(p.terminationStatus == 0)
        }
        try? proc.run()
    }

    func fetchEngine(engineMarker: URL, engineDir: URL) {
        let fm = FileManager.default
        let server = engineDir.appendingPathComponent("web_server.py")
        let engineReady = fm.fileExists(atPath: engineMarker.path)
            && fm.fileExists(atPath: engineDir.path)
            && fm.fileExists(atPath: server.path)
        if engineReady {
            startServerAndShow()
            return
        }
        DispatchQueue.main.async {
            self.statusText = "Đang tải engine video (~26MB)…"
            self.progress = 0
            self.speedText = ""
            self.etaText = ""
        }
        downloadArchive(from: engineURL, to: "aurexvideo-engine.tar.gz", marker: ".engine_ready", label: "engine") { [weak self] ok in
            guard ok else { return }
            self?.startServerAndShow()
        }
    }

    // MARK: - Download delegate
    func downloadArchive(from url: URL, to saveAs: String, marker: String, label: String, completion: @escaping (Bool) -> Void) {
        let session = URLSession(configuration: .default,
                                delegate: DownloadDelegate(parent: self, saveAs: saveAs, marker: marker, label: label, completion: completion),
                                delegateQueue: .main)
        var req = URLRequest(url: url)
        req.timeoutInterval = 180
        session.downloadTask(with: req).resume()
    }

    final class DownloadDelegate: NSObject, URLSessionDownloadDelegate {
        weak var parent: AppState?
        let saveAs: String
        let marker: String
        let label: String
        let completion: (Bool) -> Void
        var lastBytes: Int64 = 0
        var lastTime = Date()
        var retries = 0

        init(parent: AppState, saveAs: String, marker: String, label: String, completion: @escaping (Bool) -> Void) {
            self.parent = parent
            self.saveAs = saveAs
            self.marker = marker
            self.label = label
            self.completion = completion
        }

        private func fail(parent: AppState, error: String) {
            let logURL = parent.engineBase.appendingPathComponent("app.log")
            try? ("[download] error (\(saveAs)): \(error)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async {
                parent.errorText = "Lỗi tải \(self.label): \(error)"
                parent.stage = .failed
            }
            completion(false)
        }

        func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                        didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
            guard let parent else { return }
            let frac = totalBytesExpectedToWrite > 0 ? Double(totalBytesWritten) / Double(totalBytesExpectedToWrite) : 0
            DispatchQueue.main.async {
                parent.progress = frac
                let now = Date()
                let dt = now.timeIntervalSince(self.lastTime)
                if dt >= 0.5 {
                    let dps = Double(totalBytesWritten - self.lastBytes) / dt
                    self.lastBytes = totalBytesWritten
                    self.lastTime = now
                    let mb = dps / 1024 / 1024
                    parent.speedText = String(format: "%.1f MB/s", mb)
                    if totalBytesExpectedToWrite > 0 && dps > 0 {
                        let remain = Double(totalBytesExpectedToWrite - totalBytesWritten) / dps
                        parent.etaText = String(format: "~%d giây", Int(remain))
                    }
                }
                parent.statusText = "Đang tải \(self.label)… \(Int(frac * 100))%"
            }
        }

        func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
            guard let parent else { return }
            let dest = parent.engineBase.appendingPathComponent(saveAs)
            let fm = FileManager.default
            try? fm.createDirectory(at: parent.engineBase, withIntermediateDirectories: true)
            try? fm.removeItem(at: dest)
            do {
                try fm.moveItem(at: location, to: dest)
                parent.unpack(dest, marker: marker, completion: completion)
            } catch {
                fail(parent: parent, error: error.localizedDescription)
            }
        }

        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            guard let parent else { return }
            if let error {
                if retries < 2 {
                    retries += 1
                    DispatchQueue.main.async { parent.statusText = "Lỗi mạng, thử lại (\(self.retries)/2)…" }
                    var req = URLRequest(url: task.originalRequest?.url ?? task.currentRequest?.url ?? URL(string: "https://github.com")!)
                    req.timeoutInterval = 180
                    session.downloadTask(with: req).resume()
                } else {
                    fail(parent: parent, error: error.localizedDescription)
                }
            }
        }
    }

    func unpack(_ archive: URL, marker: String, completion: @escaping (Bool) -> Void) {
        let logURL = engineBase.appendingPathComponent("app.log")
        try? ("[unpack] start, archive: \(archive.path)".write(to: logURL, atomically: true, encoding: .utf8))
        DispatchQueue.main.async {
            self.statusText = "Đang giải nén… (bước này mất khoảng 1-2 phút)"
            self.progress = 1
            self.speedText = ""
            self.etaText = ""
        }
        let fm = FileManager.default
        try? fm.createDirectory(at: engineBase, withIntermediateDirectories: true)
        let engineDir = engineBase.appendingPathComponent("engine")
        try? fm.createDirectory(at: engineDir, withIntermediateDirectories: true)
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        proc.arguments = ["xzf", archive.path, "-C", engineDir.path]
        proc.terminationHandler = { [weak self] p in
            guard let self else { return }
            try? ("[unpack] tar exit: \(p.terminationStatus)".write(to: logURL, atomically: true, encoding: .utf8))
            if p.terminationStatus != 0 {
                DispatchQueue.main.async { self.errorText = "Giải nén thất bại (mã \(p.terminationStatus))."; self.stage = .failed }
                completion(false); return
            }
            fm.createFile(atPath: self.engineBase.appendingPathComponent(marker).path, contents: Data())
            completion(true)
        }
        do { try proc.run() } catch {
            try? ("[unpack] tar run failed: \(error.localizedDescription)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async { self.errorText = "Không chạy được tar: \(error.localizedDescription)"; self.stage = .failed }
            completion(false)
        }
    }

    func startServerAndShow() {
        let fm = FileManager.default
        let engineDir = engineBase.appendingPathComponent("engine")
        let studioDir = engineBase.appendingPathComponent("studio")
        let pyBase = engineBase.appendingPathComponent("python_base/bin/python3.11")
        let venvPy = engineBase.appendingPathComponent(".venv/bin/python3")
        let server = engineDir.appendingPathComponent("web_server.py")
        let projectDir = studioDir.appendingPathComponent("project")
        let logURL = engineBase.appendingPathComponent("app.log")
        try? ("[startServer] called, pyBase exists: \(fm.fileExists(atPath: pyBase.path)), venvPy exists: \(fm.fileExists(atPath: venvPy.path)), server exists: \(fm.fileExists(atPath: server.path))".write(to: logURL, atomically: true, encoding: .utf8))

        // Ensure personal data folder exists (separate from engine, survives OTA)
        for sub in ["project", "output", "config", "assets"] {
            try? fm.createDirectory(at: studioDir.appendingPathComponent(sub), withIntermediateDirectories: true)
        }

        let pythonExec: URL = fm.fileExists(atPath: pyBase.path) ? pyBase : venvPy
        guard fm.fileExists(atPath: pythonExec.path), fm.fileExists(atPath: server.path) else {
            let m = "Thiếu python hoặc web_server.py trong engine."
            try? ("[startServer] \(m)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async { self.errorText = m; self.stage = .failed }
            return
        }
        if let s = try? String(contentsOf: URL(string: "http://127.0.0.1:4173/")!), s.contains("<") {
            DispatchQueue.main.async { self.stage = .ready }
            return
        }
        let proc = Process()
        proc.executableURL = pythonExec
        proc.arguments = [server.path, "--host", "0.0.0.0", "--port", "4173", "--source-root", engineDir.path]
        proc.currentDirectoryURL = engineDir
        var env = ProcessInfo.processInfo.environment
        env["AUREXVIDEO_UI_LANGUAGE"] = lang
        env["AUREXVIDEO_DESKTOP"] = "1"
        env["AUREX_DATA_ROOT"] = studioDir.path
        env["AUREX_BOOTSTRAP_DATA_ROOT"] = studioDir.path
        // KHÔNG set PYTHONHOME — gây lỗi import engine modules. Chỉ PATH + AUREX_FFMPEG.
        env["PATH"] = engineBase.appendingPathComponent("runtime/bin").path + ":" + (env["PATH"] ?? "")
        env["AUREX_FFMPEG"] = engineBase.appendingPathComponent("runtime/bin/ffmpeg").path
        proc.environment = env
        let errPipe = Pipe()
        proc.standardError = errPipe
        proc.standardOutput = errPipe
        do {
            try proc.run()
        } catch {
            try? ("[startServer] run failed: \(error.localizedDescription)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async { self.errorText = "Không khởi được server: \(error.localizedDescription)"; self.stage = .failed }
            return
        }
        var tries = 0
        while tries < 80 {
            usleep(500_000)
            if let url = URL(string: "http://127.0.0.1:4173/"),
               let code = try? String(contentsOf: url),
               code.contains("<") {
                try? ("[startServer] server up, switching to ready".write(to: logURL, atomically: true, encoding: .utf8))
                DispatchQueue.main.async { self.stage = .ready }
                return
            }
            tries += 1
        }
        try? ("[startServer] poll timeout, forcing ready".write(to: logURL, atomically: true, encoding: .utf8))
        DispatchQueue.main.async { self.stage = .ready }
    }
}

// MARK: - Language view
struct LangRow: View {
    let code: String; let title: String; let sub: String; let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Text(code).font(.system(size: 13, weight: .bold))
                    .frame(width: 38, height: 38).background(Color.orange).foregroundColor(.white).cornerRadius(9)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).foregroundColor(.white).font(.system(size: 15, weight: .semibold))
                    Text(sub).foregroundColor(.gray).font(.system(size: 12))
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundColor(.orange)
            }
            .padding(14)
            .background(Color(NSColor(red:0.12, green:0.12, blue:0.14, alpha:1)))
            .cornerRadius(12)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.gray.opacity(0.25)))
        }
        .buttonStyle(PlainButtonStyle())
    }
}

struct LanguageView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: 22) {
            Spacer().frame(height: 36)
            aurexBrandLogo().frame(width: 56, height: 56)
            Text("AurexVideo").font(.system(size: 30, weight: .bold)).foregroundColor(.white)
            Text("Turn scripts into videos, fast.").foregroundColor(.gray).font(.system(size: 13))
            Spacer().frame(height: 18)
            VStack(alignment: .leading, spacing: 8) {
                Text("Choose your language").font(.system(size: 20, weight: .bold)).foregroundColor(.white)
                Text("Chọn ngôn ngữ giao diện").foregroundColor(.gray).font(.system(size: 13))
            }
            VStack(spacing: 12) {
                LangRow(code: "EN", title: "English", sub: "Continue in English") { state.proceed("en") }
                LangRow(code: "VI", title: "Tiếng Việt", sub: "Tiếp tục bằng Tiếng Việt") { state.proceed("vi") }
            }
            Spacer()
        }
        .padding(32).frame(width: 480, height: 560)
        .background(Color(NSColor(red:0.07, green:0.07, blue:0.08, alpha:1)))
        .onAppear {
            let marker = NSHomeDirectory() + "/.aurexvideo-autolang"
            if let l = try? String(contentsOfFile: marker).trimmingCharacters(in: .whitespacesAndNewlines), !l.isEmpty {
                state.proceed(l)
            }
        }
    }
}

// MARK: - Download / progress view
struct DownloadView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: 22) {
            Spacer().frame(height: 40)
            aurexBrandLogo().frame(width: 52, height: 52)
            Text("AurexVideo").font(.system(size: 26, weight: .bold)).foregroundColor(.white)
            Text("Turn scripts into videos, fast.").foregroundColor(.gray).font(.system(size: 12))
            Spacer().frame(height: 18)
            Text("Đang chuẩn bị AurexVideo").font(.system(size: 20, weight: .bold)).foregroundColor(.white)
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("Đang tải").foregroundColor(.orange).font(.system(size: 14, weight: .bold))
                    Spacer()
                    Text("\(Int(state.progress * 100))%").foregroundColor(.gray).font(.system(size: 13))
                }
                ProgressView(value: state.progress).progressViewStyle(LinearProgressViewStyle(tint: .orange)).frame(height: 6)
                Text(state.statusText.isEmpty ? "Đang tải engine video về máy…" : state.statusText)
                    .foregroundColor(.gray).font(.system(size: 12))
                HStack(spacing: 12) {
                    if !state.speedText.isEmpty {
                        Text(state.speedText).foregroundColor(.green).font(.system(size: 12, weight: .medium))
                    }
                    if !state.etaText.isEmpty {
                        Text(state.etaText).foregroundColor(.orange).font(.system(size: 12))
                    }
                    Spacer()
                }
                Text("Giữ AurexVideo mở trong lúc tải.").foregroundColor(.orange.opacity(0.85)).font(.system(size: 12))
            }
            .padding(18)
            .background(Color(NSColor(red:0.12, green:0.12, blue:0.14, alpha:1)))
            .cornerRadius(14)
            Spacer()
        }
        .padding(32).frame(width: 480, height: 560)
        .background(Color(NSColor(red:0.07, green:0.07, blue:0.08, alpha:1)))
    }
}

// MARK: - Failed view
struct FailedView: View {
    @ObservedObject var state: AppState
    var body: some View {
        VStack(spacing: 20) {
            Spacer().frame(height: 60)
            Image(systemName: "exclamationmark.triangle.fill").resizable().frame(width: 54, height: 54).foregroundColor(.red)
            Text("Có lỗi xảy ra").font(.system(size: 20, weight: .bold)).foregroundColor(.white)
            Text(state.errorText).foregroundColor(.gray).font(.system(size: 13))
                .multilineTextAlignment(.center).padding(.horizontal, 24)
            Button("Thử lại") { state.retry() }
                .padding(.horizontal, 24).padding(.vertical, 10)
                .background(Color.orange).foregroundColor(.white).cornerRadius(10)
            Spacer()
        }
        .padding(32).frame(width: 480, height: 560)
        .background(Color(NSColor(red:0.07, green:0.07, blue:0.08, alpha:1)))
    }
}

// MARK: - WebView
struct WebContentView: NSViewRepresentable {
    let url: URL
    func makeNSView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.preferences.javaScriptCanOpenWindowsAutomatically = true
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.load(URLRequest(url: url))
        return wv
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

@main
struct AurexVideoApp: App {
    @StateObject private var state = AppState()
    var body: some Scene {
        WindowGroup {
            Group {
                switch state.stage {
                case .language: LanguageView(state: state)
                case .downloading: DownloadView(state: state)
                case .failed: FailedView(state: state)
                case .ready: WebContentView(url: URL(string: "http://127.0.0.1:4173/")!)
                    .frame(minWidth: 1100, minHeight: 720)
                }
            }
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
    }
}
