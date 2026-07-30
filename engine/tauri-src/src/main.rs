// AurexVideo Tauri app — Rust bootstrap + web_server spawner
// Replaces the hand-rolled Swift WKWebView shell.
// Tauri's macOS WebView handles native file dialogs automatically,
// fixing the "Upload PNG button doesn't open picker" bug.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use tauri::Manager;

const APP_VERSION: &str = "0.2.4";
const GITHUB_REPO: &str = "truongtungminh/AurexVideo";
const SERVER_PORT: u16 = 4173;
const SERVER_HOST: &str = "127.0.0.1";

// Where the app stores its data (outside the .app bundle so OTA doesn't lose user data)
fn support_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/".to_string());
    PathBuf::from(home).join("Library/Application Support/app.aurexvideo")
}

fn runtime_dir() -> PathBuf { support_dir().join("runtime") }
fn engine_dir() -> PathBuf { support_dir().join("engine") }
fn studio_dir() -> PathBuf { support_dir().join("studio") }

fn download_file(url: &str, dest: &Path) -> Result<(), String> {
    println!("[bootstrap] downloading {}", url);
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client.get(url).send().map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {} for {}", resp.status(), url));
    }
    let bytes = resp.bytes().map_err(|e| e.to_string())?;
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent).ok();
    }
    fs::write(dest, &bytes).map_err(|e| e.to_string())?;
    println!("[bootstrap]   -> {} bytes", bytes.len());
    Ok(())
}

fn extract_tar_gz(archive: &Path, dest: &Path) -> Result<(), String> {
    println!("[bootstrap] extracting {:?} -> {:?}", archive, dest);
    fs::create_dir_all(dest).ok();
    let file = fs::File::open(archive).map_err(|e| e.to_string())?;
    let dec = flate2::read::GzDecoder::new(file);
    let mut ar = tar::Archive::new(dec);
    ar.unpack(dest).map_err(|e| e.to_string())?;
    Ok(())
}

fn release_asset_url(name: &str) -> String {
    format!(
        "https://github.com/{}/releases/download/v{}/{}",
        GITHUB_REPO, APP_VERSION, name
    )
}

// Download python_base (with playwright) + engine code from GitHub releases.
// Chromium/ffmpeg/whisper are fetched from their upstream hosts at first launch
// by a small bootstrap script OR here. For simplicity we fetch the full engine
// tarball which already includes the runtime fetch logic invoked by web_server.
fn ensure_engine() -> Result<(), String> {
    let eng = engine_dir();
    let marker = eng.join(".engine_ready");
    let server_py = eng.join("web_server.py");
    if marker.exists() && server_py.exists() {
        println!("[bootstrap] engine already present");
        return Ok(());
    }
    fs::create_dir_all(&eng).ok();
    let tmp = support_dir().join("engine-download.tar.gz");
    download_file(&release_asset_url("aurexvideo-engine-0.2.4.tar.gz"), &tmp)?;
    // Engine tarball is git-archive of `engine/` -> unpack directly into engine_dir
    extract_tar_gz(&tmp, &eng)?;
    fs::remove_file(&tmp).ok();
    fs::write(&marker, "1").ok();
    Ok(())
}

fn ensure_python() -> Result<PathBuf, String> {
    let py = runtime_dir().join("python_base/bin/python3.11");
    if py.exists() {
        return Ok(py);
    }
    fs::create_dir_all(&runtime_dir()).ok();
    let tmp = support_dir().join("python-download.tar.gz");
    download_file(&release_asset_url("aurexvideo-python-0.2.4.tar.gz"), &tmp)?;
    extract_tar_gz(&tmp, &runtime_dir())?;
    fs::remove_file(&tmp).ok();
    Ok(py)
}

fn wait_for_server(timeout: Duration) -> bool {
    let url = format!("http://{}:{}/", SERVER_HOST, SERVER_PORT);
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(r) = client.get(&url).send() {
            if r.status().is_success() {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn spawn_server(python: &Path, engine: &Path) -> Result<Child, String> {
    let _support = support_dir();
    studio_dir().join("project").parent().map(|_| ());
    fs::create_dir_all(studio_dir().join("project")).ok();
    fs::create_dir_all(studio_dir().join("output")).ok();
    fs::create_dir_all(studio_dir().join("config")).ok();
    fs::create_dir_all(studio_dir().join("assets")).ok();

    let server_py = engine.join("web_server.py");
    let child = Command::new(python)
        .arg(&server_py)
        .arg("--host").arg(SERVER_HOST)
        .arg("--port").arg(SERVER_PORT.to_string())
        .arg("--source-root").arg(studio_dir().join("project"))
        .current_dir(engine)
        .env("AUREXVIDEO_DESKTOP", "1")
        .env("AUREX_DATA_ROOT", studio_dir())
        .env("AUREX_BOOTSTRAP_DATA_ROOT", studio_dir())
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to spawn web_server: {}", e))?;
    Ok(child)
}

fn main() {
    println!("[aurexvideo] starting bootstrap");
    if let Err(e) = ensure_engine() {
        eprintln!("[bootstrap] engine error: {}", e);
    }
    let python = match ensure_python() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("[bootstrap] python error: {}", e);
            std::process::exit(1);
        }
    };

    let engine = engine_dir();
    let server = match spawn_server(&python, &engine) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[aurexvideo] {}", e);
            std::process::exit(1);
        }
    };

    // Give the server a moment, then let Tauri open the window which polls the URL.
    std::thread::spawn(move || {
        let mut srv = server;
        // keep stderr/stdout flowing to parent for debugging
        let mut out = srv.stdout.take();
        let mut err = srv.stderr.take();
        std::thread::spawn(move || {
            if let Some(mut o) = out {
                let mut buf = [0u8; 1024];
                while let Ok(n) = o.read(&mut buf) {
                    if n == 0 { break; }
                    print!("{}", String::from_utf8_lossy(&buf[..n]));
                }
            }
        });
        std::thread::spawn(move || {
            if let Some(mut e) = err {
                let mut buf = [0u8; 1024];
                while let Ok(n) = e.read(&mut buf) {
                    if n == 0 { break; }
                    eprint!("{}", String::from_utf8_lossy(&buf[..n]));
                }
            }
        });
        let _ = srv.wait();
    });

    if !wait_for_server(Duration::from_secs(90)) {
        eprintln!("[aurexvideo] WARNING: server did not respond in 90s");
    }

    tauri::Builder::default()
        .setup(|app| {
            // window already configured in tauri.conf.json to load the URL
            let _ = app;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running AurexVideo");
}
