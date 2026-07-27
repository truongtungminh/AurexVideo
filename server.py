import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

REPO_ROOT = Path(__file__).resolve().parent
ENGINE_DIR = REPO_ROOT / "engine"
SLIDE_ROOT = REPO_ROOT / "decks"
VENV_PY = ENGINE_DIR / ".venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
DEFAULT_PORT = 8899


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def _send(self, status, content_type, body):
        payload = body if isinstance(body, bytes) else str(body or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status, data):
        self._send(status, "application/json", json.dumps(data, ensure_ascii=False, indent=2))

    def _serve_file(self, rel):
        rel = rel.lstrip("/")
        # security: only under web/
        p = (REPO_ROOT / "web" / Path(rel)).resolve()
        if not str(p).startswith(str((REPO_ROOT / "web").resolve())):
            return self._send(404, "text/plain", "Not found")
        if not p.is_file():
            return self._send(404, "text/plain", "Not found")
        self.send_response(200)
        suffix = p.suffix.lower()
        ctype = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".json": "application/json",
            ".ico": "image/x-icon",
            ".mp4": "video/mp4",
        }.get(suffix, "application/octet-stream")
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(p.stat().st_size))
        self.end_headers()
        self.wfile.write(p.read_bytes())

    def _abs_slide(self, name):
        return (SLIDE_ROOT / str(name).strip()).resolve()

    def do_GET(self):
        path = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        if path in ("/", "/index.html", "/home.html"):
            return self._serve_file("home.html")
        if path == "/app" or path.startswith("/app/"):
            rel = path[len("/app"):] or "/index.html"
            return self._serve_file(rel if rel != "/" else "index.html")
        if path == "/watch.html":
            return self._serve_file("watch.html")
        if path.startswith("/watch/"):
            name = path[len("/watch/"):].strip("/")
            return self._serve_file(f"watch.html?project={name}")
        if path == "/settings":
            return self._serve_file("settings.html")
        if path in ("/assets", "/assets/"):
            return self._send(200, "text/plain", "AurexVideo assets root")
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            p = (REPO_ROOT / "assets" / Path(rel)).resolve()
            allowed = (REPO_ROOT / "assets").resolve()
            if not str(p).startswith(str(allowed)) or not p.is_file():
                return self._send(404, "text/plain", "Not found")
            self.send_response(200)
            suffix = p.suffix.lower()
            ctype = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".svg": "image/svg+xml",
                ".ico": "image/x-icon",
                ".wav": "audio/wav",
                ".mp3": "audio/mpeg",
                ".json": "application/json",
            }.get(suffix, "application/octet-stream")
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(p.stat().st_size))
            self.end_headers()
            self.wfile.write(p.read_bytes())
        if path == "/api/projects":
            rows = []
            for d in sorted(SLIDE_ROOT.iterdir()):
                if not d.is_dir():
                    continue
                marker = d / "aurexvideo.json"
                if not marker.is_file():
                    continue
                try:
                    data = json.loads(marker.read_text(encoding="utf-8"))
                except Exception:
                    continue
                rows.append({
                    "name": d.name,
                    "title": data.get("title", d.name),
                    "slides": len(list((d / "scripts").glob("script-90s.txt")) > 0 and open(d / "scripts" / "script-90s.txt", encoding="utf-8").read().splitlines() or []),
                })
            return self._json(200, {"projects": rows})
        if path == "/api/health":
            return self._json(200, {"status": "ok", "engine": str(ENGINE_DIR), "port": DEFAULT_PORT})
        if path.startswith("/api/project/") and path.count("/") == 3:
            name = path.split("/")[-1]
            return self._json(200, {"name": name, "exists": self._abs_slide(name).is_dir()})
        if path.startswith("/video"):
            name = parse_qs(urlparse(self.path).query).get("project", [""])[0]
            p = self._abs_slide(name) / "output" / "final_video.mp4"
            if not p.is_file():
                return self._send(404, "text/plain", "No video")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(p.stat().st_size))
            self.end_headers()
            self.wfile.write(p.read_bytes())
        if path.startswith("/api/") and path.endswith("/render"):
            name = path.split("/")[-2]
            body = b""
            if "Content-Length" in self.headers:
                try:
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                except Exception:
                    pass
            payload = json.loads(body.decode("utf-8") or "{}") if body else {}
            cwd = str(ENGINE_DIR)
            cmd = [str(VENV_PY), "auto_render.py", "--out", str(self._abs_slide(name)), "--voiceover", str(self._abs_slide(name) / "output" / "voiceover_concat.mp3")]
            if payload.get("engine") == "edge":
                cmd += ["--engine", "edge"]
            else:
                cmd += ["--engine", "maziao", "--maziao-mode", "full", "--voice", "clone_8ci7vkGMoJLyKe9IJ7MfV"]
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self._json(200, {"ok": True, "pid": proc.pid, "job": name})
            return
        return self._send(404, "text/plain", "Not found")

    def do_POST(self):
        return self.do_GET()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    (REPO_ROOT / "decks").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "preview").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"status":"ready","host":args.host,"port":args.port,"engine":str(ENGINE_DIR)}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

if __name__ == "__main__":
    main()


