// AurexVideo Tauri app — Rust bootstrap + web_server spawner
// Replaces the hand-rolled Swift WKWebView shell.
// Tauri's macOS WebView handles native file dialogs automatically,
// fixing the "Upload PNG button doesn't open picker" bug.

use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{atomic::{AtomicBool, Ordering}, Arc};
use std::time::{Duration, Instant};

use tauri::Manager;

const APP_VERSION: &str = "0.2.4";
const GITHUB_REPO: &str = "truongtungminh/AurexVideo";
const SERVER_PORT: u16 = 4173;
const SERVER_HOST: &str = "127.0.0.1";
const DEV_ENTITLEMENT_UNLOCK_ENV: &str = "AUREXVIDEO_DEV_ENTITLEMENT_UNLOCK";
const DESKTOP_BUILD_PROFILE_ENV: &str = "AUREXVIDEO_DESKTOP_BUILD_PROFILE";

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

fn remove_appledouble_files(root: &Path) {
    let Ok(entries) = fs::read_dir(root) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        if name.to_string_lossy().starts_with("._") {
            if path.is_dir() {
                fs::remove_dir_all(path).ok();
            } else {
                fs::remove_file(path).ok();
            }
            continue;
        }
        if path.is_dir() {
            remove_appledouble_files(&path);
        }
    }
}

fn ensure_python_package(python: &Path, import_name: &str, package: &str) -> Result<(), String> {
    if let Some(python_root) = python.parent().and_then(Path::parent) {
        remove_appledouble_files(python_root);
    }
    let available = Command::new(python)
        .args(["-c", &format!("import {}", import_name)])
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if available {
        return Ok(());
    }

    append_server_log(&format!(
        "[bootstrap] Python module {} missing; installing {}\n",
        import_name, package
    ));
    let ensurepip = Command::new(python)
        .args(["-m", "ensurepip", "--upgrade"])
        .status()
        .map_err(|error| format!("failed to start ensurepip: {}", error))?;
    if !ensurepip.success() {
        return Err(format!("ensurepip failed with {}", ensurepip));
    }

    let install = Command::new(python)
        .args([
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            package,
        ])
        .status()
        .map_err(|error| format!("failed to install {}: {}", package, error))?;
    if !install.success() {
        return Err(format!("pip install {} failed with {}", package, install));
    }
    Ok(())
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

#[cfg(debug_assertions)]
fn expected_backend_profile() -> (&'static str, bool) {
    ("debug", true)
}

#[cfg(not(debug_assertions))]
fn expected_backend_profile() -> (&'static str, bool) {
    ("release", false)
}

fn backend_matches_runtime_profile() -> bool {
    let url = format!("http://{}:{}/api/health", SERVER_HOST, SERVER_PORT);
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
        Ok(client) => client,
        Err(_) => return false,
    };
    let response = match client.get(url).send() {
        Ok(response) if response.status().is_success() => response,
        _ => return false,
    };
    let body = match response.text() {
        Ok(body) => body,
        Err(_) => return false,
    };
    let (profile, dev_unlock) = expected_backend_profile();
    body.contains(&format!("\"desktop_build_profile\": \"{}\"", profile))
        && body.contains(&format!(
            "\"development_entitlement_unlock\": {}",
            dev_unlock
        ))
}

#[cfg(debug_assertions)]
fn configure_development_entitlement_unlock(command: &mut Command) {
    command.env(DESKTOP_BUILD_PROFILE_ENV, "debug");
    command.env(DEV_ENTITLEMENT_UNLOCK_ENV, "1");
}

#[cfg(not(debug_assertions))]
fn configure_development_entitlement_unlock(command: &mut Command) {
    command.env(DESKTOP_BUILD_PROFILE_ENV, "release");
    command.env_remove(DEV_ENTITLEMENT_UNLOCK_ENV);
}

fn spawn_server(python: &Path, engine: &Path) -> Result<Child, String> {
    let _support = support_dir();
    studio_dir().join("project").parent().map(|_| ());
    fs::create_dir_all(studio_dir().join("project")).ok();
    fs::create_dir_all(studio_dir().join("output")).ok();
    fs::create_dir_all(studio_dir().join("config")).ok();
    fs::create_dir_all(studio_dir().join("assets")).ok();

    let server_py = engine.join("web_server.py");
    let mut command = Command::new(python);
    command
        .arg(&server_py)
        .arg("--host").arg(SERVER_HOST)
        .arg("--port").arg(SERVER_PORT.to_string())
        .arg("--source-root").arg(studio_dir().join("project"))
        .current_dir(engine)
        .env("AUREXVIDEO_EMBEDDED_DESKTOP", "1")
        .env("AUREX_DATA_ROOT", studio_dir())
        .env("AUREX_BOOTSTRAP_DATA_ROOT", studio_dir())
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_development_entitlement_unlock(&mut command);
    let child = command
        .spawn()
        .map_err(|e| format!("failed to spawn web_server: {}", e))?;
    Ok(child)
}

fn append_server_log(message: &str) {
    let log_path = support_dir().join("server.log");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = file.write_all(message.as_bytes());
    }
}

fn drain_server_output<R: Read + Send + 'static>(mut reader: R, stderr: bool) {
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let chunk = String::from_utf8_lossy(&buf[..n]);
                    append_server_log(&chunk);
                    if stderr {
                        eprint!("{}", chunk);
                    } else {
                        print!("{}", chunk);
                    }
                }
            }
        }
    });
}

fn terminate_unhealthy_server_on_port() {
    let port = format!("TCP:{}", SERVER_PORT);
    let output = Command::new("/usr/sbin/lsof")
        .args(["-ti", &port, "-sTCP:LISTEN"])
        .output();
    let Ok(output) = output else { return };
    let pids = String::from_utf8_lossy(&output.stdout);
    for pid in pids.lines().filter(|pid| !pid.trim().is_empty()) {
        append_server_log(&format!(
            "[launcher] terminating unhealthy backend pid={} on port {}\n",
            pid, SERVER_PORT
        ));
        let _ = Command::new("/bin/kill").args(["-TERM", pid]).status();
    }
    if !pids.trim().is_empty() {
        std::thread::sleep(Duration::from_secs(1));
    }
}

fn run_server_watchdog(python: PathBuf, engine: PathBuf, stop_requested: Arc<AtomicBool>) {
    let mut consecutive_failures = 0u32;
    loop {
        if stop_requested.load(Ordering::Acquire) {
            return;
        }
        if wait_for_server(Duration::from_secs(2)) {
            if backend_matches_runtime_profile() {
                consecutive_failures = 0;
                append_server_log("[launcher] using healthy backend with matching build profile on port 4173\n");
                while wait_for_server(Duration::from_secs(2)) {
                    if stop_requested.load(Ordering::Acquire) {
                        return;
                    }
                    std::thread::sleep(Duration::from_secs(2));
                }
                append_server_log("[launcher] existing backend stopped responding; taking ownership\n");
            } else {
                append_server_log("[launcher] existing backend profile mismatch; replacing it\n");
            }
        }

        terminate_unhealthy_server_on_port();
        let started_at = Instant::now();
        append_server_log("\n[launcher] starting backend\n");
        match spawn_server(&python, &engine) {
            Ok(mut server) => {
                if let Some(stdout) = server.stdout.take() {
                    drain_server_output(stdout, false);
                }
                if let Some(stderr) = server.stderr.take() {
                    drain_server_output(stderr, true);
                }
                loop {
                    if stop_requested.load(Ordering::Acquire) {
                        let _ = server.kill();
                        let _ = server.wait();
                        append_server_log("\n[launcher] backend stopped during app exit\n");
                        return;
                    }
                    match server.try_wait() {
                        Ok(Some(status)) => {
                            append_server_log(&format!(
                                "\n[launcher] backend exited status={} uptime={:.1}s\n",
                                status,
                                started_at.elapsed().as_secs_f64()
                            ));
                            break;
                        }
                        Ok(None) => std::thread::sleep(Duration::from_millis(250)),
                        Err(error) => {
                            append_server_log(&format!(
                                "\n[launcher] failed waiting for backend: {}\n",
                                error
                            ));
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                append_server_log(&format!("\n[launcher] backend spawn failed: {}\n", error));
            }
        }

        if started_at.elapsed() >= Duration::from_secs(60) {
            consecutive_failures = 0;
        } else {
            consecutive_failures = consecutive_failures.saturating_add(1);
        }
        let delay = 2u64.saturating_pow(consecutive_failures.min(4));
        append_server_log(&format!(
            "[launcher] restarting backend in {}s (failure {})\n",
            delay, consecutive_failures
        ));
        for _ in 0..(delay * 4) {
            if stop_requested.load(Ordering::Acquire) {
                return;
            }
            std::thread::sleep(Duration::from_millis(250));
        }
    }
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
    if let Err(error) = ensure_python_package(&python, "PIL", "Pillow>=10,<12") {
        eprintln!("[bootstrap] Pillow error: {}", error);
        append_server_log(&format!("[bootstrap] Pillow error: {}\n", error));
    }

    let engine = engine_dir();
    // Keep the backend supervised for the entire app lifetime. If Python exits,
    // preserve its output in server.log and restart it with bounded backoff.
    let stop_requested = Arc::new(AtomicBool::new(false));
    let watchdog_stop_requested = Arc::clone(&stop_requested);
    std::thread::spawn(move || run_server_watchdog(python, engine, watchdog_stop_requested));

    if !wait_for_server(Duration::from_secs(90)) {
        eprintln!("[aurexvideo] WARNING: server did not respond in 90s");
    }

    tauri::Builder::default()
        .setup(|app| {
            // window already configured in tauri.conf.json to load the URL
            let _ = app;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building AurexVideo")
        .run(move |_app_handle, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
                stop_requested.store(true, Ordering::Release);
            }
        });
}
