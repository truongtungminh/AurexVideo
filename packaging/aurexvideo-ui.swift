import SwiftUI
import WebKit

// MARK: - Shared state
final class AppState: ObservableObject {
    @Published var stage: Stage = .language
    @Published var progress: Double = 0
    @Published var statusText: String = ""
    @Published var errorText: String = ""

    enum Stage { case language, downloading, ready, failed }

    let engineBase = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("Library/Application Support/app.aurexvideo")
    let downloadURL = URL(string: "https://github.com/truongtungminh/AurexVideo/releases/download/v0.2.1/aurexvideo-engine.tar.gz")!

    var lang = "en"

    func proceed(_ lang: String) {
        self.lang = lang
        stage = .downloading
        // offload from main thread
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootstrap()
        }
    }

    func bootstrap() {
        let fm = FileManager.default
        let engineDir = engineBase.appendingPathComponent("engine")
        let marker = engineBase.appendingPathComponent(".engine_ready")

        if fm.fileExists(atPath: marker.path), fm.fileExists(atPath: engineDir.path) {
            startServerAndShow()
            return
        }
        DispatchQueue.main.async { self.statusText = "Đang tải engine video về máy…"; self.progress = 0 }
        downloadEngine()
    }

    func downloadEngine() {
        let task = URLSession.shared.downloadTask(with: downloadURL) { [weak self] tmpURL, _, err in
            guard let self else { return }
            let logURL = self.engineBase.appendingPathComponent("app.log")
            if let err {
                try? ("[download] error: \(err.localizedDescription)".write(to: logURL, atomically: true, encoding: .utf8))
                DispatchQueue.main.async { self.errorText = "Lỗi tải: \(err.localizedDescription)"; self.stage = .failed }
                return
            }
            guard let tmpURL else {
                try? ("[download] no tmpURL".write(to: logURL, atomically: true, encoding: .utf8))
                DispatchQueue.main.async { self.errorText = "Không tải được file engine."; self.stage = .failed }
                return
            }
            let dest = self.engineBase.appendingPathComponent("aurexvideo-engine.tar.gz")
            let fm = FileManager.default
            try? fm.createDirectory(at: self.engineBase, withIntermediateDirectories: true)
            try? fm.removeItem(at: dest)
            do { try fm.moveItem(at: tmpURL, to: dest) }
            catch {
                DispatchQueue.main.async { self.errorText = "Lỗi lưu file: \(error.localizedDescription)"; self.stage = .failed }
                return
            }
            self.unpack(dest)
        }
        task.resume()
        Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { t in
            DispatchQueue.main.async { let p = task.progress.fractionCompleted; if p > 0 { self.progress = p } }
            if task.state == .completed || task.state == .canceling { t.invalidate() }
        }
    }

    func unpack(_ archive: URL) {
        let logURL = engineBase.appendingPathComponent("app.log")
        try? ("[unpack] start, archive: \(archive.path)".write(to: logURL, atomically: true, encoding: .utf8))
        DispatchQueue.main.async { self.statusText = "Đang giải nén…"; self.progress = 1 }
        let fm = FileManager.default
        try? fm.createDirectory(at: engineBase, withIntermediateDirectories: true)
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        proc.arguments = ["xzf", archive.path, "-C", engineBase.path]
        proc.terminationHandler = { [weak self] p in
            guard let self else { return }
            try? ("[unpack] tar exit: \(p.terminationStatus)".write(to: logURL, atomically: true, encoding: .utf8))
            if p.terminationStatus != 0 {
                DispatchQueue.main.async { self.errorText = "Giải nén thất bại (mã \(p.terminationStatus))."; self.stage = .failed }
                return
            }
            fm.createFile(atPath: self.engineBase.appendingPathComponent(".engine_ready").path, contents: Data())
            try? fm.createDirectory(at: self.engineBase.appendingPathComponent("engine/decks"), withIntermediateDirectories: true)
            self.startServerAndShow()
        }
        do { try proc.run() } catch {
            try? ("[unpack] tar run failed: \(error.localizedDescription)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async { self.errorText = "Không chạy được tar: \(error.localizedDescription)"; self.stage = .failed }
        }
    }

    func startServerAndShow() {
        let fm = FileManager.default
        let engineDir = engineBase.appendingPathComponent("engine")
        let venvPy = engineDir.appendingPathComponent(".venv/bin/python3")
        let server = engineDir.appendingPathComponent("web_server.py")
        let decks = engineDir.appendingPathComponent("decks")
        let logURL = engineBase.appendingPathComponent("app.log")
        try? ("[startServer] called, venvPy exists: \(fm.fileExists(atPath: venvPy.path)), server exists: \(fm.fileExists(atPath: server.path))".write(to: logURL, atomically: true, encoding: .utf8))

        guard fm.fileExists(atPath: venvPy.path), fm.fileExists(atPath: server.path) else {
            let m = "Thiếu python hoặc web_server.py trong engine."
            try? ("[startServer] \(m)".write(to: logURL, atomically: true, encoding: .utf8))
            DispatchQueue.main.async { self.errorText = m; self.stage = .failed }
            return
        }
        if let s = try? String(contentsOf: URL(string: "http://localhost:4173/")!), s.contains("<") {
            DispatchQueue.main.async { self.stage = .ready }
            return
        }
        let proc = Process()
        proc.executableURL = venvPy
        proc.arguments = [server.path, "--host", "127.0.0.1", "--port", "4173", "--source-root", decks.path]
        proc.currentDirectoryURL = engineDir
        var env = ProcessInfo.processInfo.environment
        env["AUREXVIDEO_UI_LANGUAGE"] = lang
        env["PYTHONHOME"] = engineDir.appendingPathComponent("python_base").path
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
        // Give the server a moment, then show the web UI (WebView will keep loading)
        var tries = 0
        while tries < 80 {
            usleep(500_000)
            if let url = URL(string: "http://localhost:4173/"),
               let code = try? String(contentsOf: url),
               code.contains("<") {
                try? ("[startServer] server up, switching to ready".write(to: logURL, atomically: true, encoding: .utf8))
                DispatchQueue.main.async { self.stage = .ready }
                return
            }
            tries += 1
        }
        // Fallback: still show UI even if poll missed
        try? ("[startServer] poll timeout, forcing ready".write(to: logURL, atomically: true, encoding: .utf8))
        DispatchQueue.main.async { self.stage = .ready }
    }

    func retry() {
        stage = .downloading
        progress = 0
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in self?.bootstrap() }
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
            Image(systemName: "play.rectangle.fill").resizable().frame(width: 56, height: 56).foregroundColor(.orange)
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
            Image(systemName: "play.rectangle.fill").resizable().frame(width: 52, height: 52).foregroundColor(.orange)
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
                case .ready: WebContentView(url: URL(string: "http://localhost:4173/")!)
                    .frame(minWidth: 1100, minHeight: 720)
                }
            }
        }
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentSize)
    }
}
