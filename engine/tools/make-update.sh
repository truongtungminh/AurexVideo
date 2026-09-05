#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

usage() {
  cat <<'EOF'
Usage: tools/make-update.sh <base-ref> [output-dir]

<base-ref> can be:
  - A git commit-ish (tag/branch/sha), e.g. v0.2.1
  - The literal word 'latest' (uses most recent tag)

Outputs into output-dir (default: engine/) :
  aurexvideo-delta.json   manifest schema with changed/added/deleted lists
  aurexvideo-delta.tar.gz lightweight tarball (changed/added files; explicit deletions in JSON)
EOF
  exit 1
}
BASE_REF="${1:-}"
OUT_DIR="${2:-engine}"
[ -n "$BASE_REF" ] || usage

if [ "$BASE_REF" = "latest" ]; then
  BASE_REF="$(git describe --tags --abbrev=0 2>/dev/null || echo main)"
fi

CHANGED_LIST="$(mktemp)"
ADDED_LIST="$(mktemp)"
DELETED_LIST="$(mktemp)"
trap 'rm -f "$CHANGED_LIST" "$ADDED_LIST" "$DELETED_LIST"' EXIT

# macOS bash 3.2-compatible: avoid `mapfile` and process substitution
collect() { git diff --name-only --diff-filter="$1" "$BASE_REF"...HEAD 2>/dev/null || true; }
printf '%s\n' "$(collect AMR)" > "$CHANGED_LIST"
printf '%s\n' "$(collect A)"   > "$ADDED_LIST"
printf '%s\n' "$(collect D)"   > "$DELETED_LIST"

# Filter out noise and files we never ship
FILTER_GLOB='\.git/|__pycache__/|\.pyc$|\.DS_Store$|\.hermes/desktop-attachments/'
filter() { grep -Ev "$FILTER_GLOB" 2>/dev/null || true; }
filter < "$CHANGED_LIST" > "${CHANGED_LIST}.out" && mv "${CHANGED_LIST}.out" "$CHANGED_LIST"
filter < "$ADDED_LIST" > "${ADDED_LIST}.out" && mv "${ADDED_LIST}.out" "$ADDED_LIST"
filter < "$DELETED_LIST" > "${DELETED_LIST}.out" && mv "${DELETED_LIST}.out" "$DELETED_LIST"

# JSON helpers
to_json_array() {
  printf '['
  first=1
  while IFS= read -r item; do
    [ -z "$item" ] && continue
    [ "$first" -eq 0 ] && printf ','
    printf '%s' "\"${item//\"/\\\"}\""
    first=0
  done < "$1"
  printf ']'
}

C_JSON="$(to_json_array "$CHANGED_LIST")"
A_JSON="$(to_json_array "$ADDED_LIST")"
D_JSON="$(to_json_array "$DELETED_LIST")"

CUR_VER="$(node -p "require('./engine/VERSION')" 2>/dev/null || cat engine/VERSION 2>/dev/null || echo 0.0.0)"
BASE_VER="$(git rev-list --count "$BASE_REF" 2>/dev/null || echo 0)"

cat > "$OUT_DIR/aurexvideo-delta.json" <<EOF
{
  "version": "${CUR_VER}",
  "deltaVersion": "delta-${BASE_VER}-${CUR_VER}",
  "basedVersion": "${BASE_REF}",
  "releaseNotes": "Delta update from ${BASE_REF} -> ${CUR_VER}",
  "protocol": "aurexvideo-delta-v1",
  "changed": ${C_JSON:-[]},
  "added":   ${A_JSON:-[]},
  "deleted": ${D_JSON:-[]}
}
EOF

mkdir -p "$OUT_DIR/.delta-work"
tar -czf "$OUT_DIR/aurexvideo-delta.tar.gz" -T "$CHANGED_LIST" -T "$ADDED_LIST" 2>/dev/null || true
rm -rf "$OUT_DIR/.delta-work"

echo "delta written: $OUT_DIR/aurexvideo-delta.json"
echo "tar written:   $OUT_DIR/aurexvideo-delta.tar.gz"
echo "changed=$(wc -l < "$CHANGED_LIST" | tr -d ' ') added=$(wc -l < "$ADDED_LIST" | tr -d ' ') deleted=$(wc -l < "$DELETED_LIST" | tr -d ' ')"
