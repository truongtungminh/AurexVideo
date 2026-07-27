# AurexVideo (development)

Full source để phát triển sau này.

## App chạy đúng: `engine/web_server.py`

Đây là app thật (giao diện FastScene/AurexVideo giống app cài sẵn ở `localhost:8765`).
Nó serve `engine/index.html`, `engine/web/`, `engine/webui/`, `engine/assets/`,
project library, render engine, social upload...

```bash
cd engine
./.venv/bin/python web_server.py --host 127.0.0.1 --port 4173 \
    --source-root /Users/truongminh/.hermes/profiles/aurexvideo/workspace/AurexVideo/decks
# mở http://127.0.0.1:4173/
```

Không cần pip cài thêm (dùng `engine/.venv` có sẵn). Mở browser tự động.

### Đối chiếu với app cài sẵn
- App cài sẵn (Tauri/FastScene) chạy engine tương tự qua `desktop_server.py` ở `localhost:8765`.
- Bản workspace này là copy source của cùng engine đó → giao diện **giống hệt**.
- Đã capture screenshot :4173 vs :8765 → identical dashboard.

## Cấu trúc
- `engine/web_server.py` — app thật (9111 dòng), dashboard + render API
- `engine/index.html`, `engine/web/`, `engine/webui/` — UI bundles
- `engine/tools/render_project.py` — entry render video
- `engine/assets/` — fonts/logo/characters/sfx (engine phục vụ từ đây)
- `engine/template/` — deck templates (dùng bởi generate_deck)
- `core/generate_deck.py` — programmatic deck generator (template/bitcoin)
- `decks/` — generated decks (source-root cho web_server)

## Routes (engine/web_server.py)
- `/` dashboard (project library + render engine)
- `/new-project`, `/upload`, `/project/<slug>/`, `/webui/...`, `/settings`
- `/assets/...` → `engine/assets/`
- render API: `POST /api/<slug>/render` (gọi `tools/render_project.py`)

## Status
- ✅ Source copied from FastScene dump (full engine, không thiếu file)
- ✅ App thật chạy live tại :4173, UI khớp :8765 (verify bằng screenshot)
- ✅ `tools/render_project.py` có sẵn → render hoạt động (cần TTS + deck đầu vào)
