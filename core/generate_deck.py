from pathlib import Path
import json, re, shutil

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_ROOT = REPO_ROOT / "engine"
TEMPLATE = ENGINE_ROOT / "template" / "bitcoin"
DECK_ROOT = REPO_ROOT / "decks"

def slugify(name: str) -> str:
    text = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\-]+", "-", text).strip("-").lower()
    return text or "project"

def _replace_js_array(path: Path, key: str, value) -> None:
    txt = path.read_text(encoding="utf-8")
    start = txt.find(f'"{key}": [')
    if start < 0:
        raise RuntimeError(f"Missing {key} array in app.js")
    depth = 0
    i = start + len(f'"{key}": [')
    start_arr = i
    while i < len(txt):
        c = txt[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                break
        i += 1
    end = i + 1
    replacement = json.dumps(value, ensure_ascii=False, indent=2)
    new = txt[:start_arr] + replacement + txt[end:]
    path.write_text(new, encoding="utf-8")

def generate_compare_deck(spec: dict) -> dict:
    name = str(spec.get("project") or slugify(spec.get("brand") or "aurexvideo")).strip()
    out = DECK_ROOT / name
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(TEMPLATE, out)
    # write marker
    (out / "aurexvideo.json").write_text(json.dumps({"app": "aurexvideo", "generated": True, "title": spec.get("brand", name)}, ensure_ascii=False), encoding="utf-8")
    script = "\n".join(str(x) for x in (spec.get("slides") or []) if str(x).strip()) or "So sánh A và B."
    (out / "scripts" / "script-90s.txt").write_text(script + "\n", encoding="utf-8")
    return {"ok": True, "project": name}

