#!/bin/bash
# Repair 2026-11-13 outputs (user approved overwriting these MP4s):
# re-render silent videos with the fixed lyric layout, then mux the existing
# song.wav into the final MP4s.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PY="$REPO_ROOT/.venv/bin/python"

repair_lang() {
  local lang="$1" name16="$2" name9="$3"
  local out="2026-11-13/$lang"
  echo "=== [$lang] rendering silent 16:9 ==="
  "$PY" scripts/render_visualizer.py --lyrics-json "$out/lyrics.json" \
    --song-wav "$out/song.wav" --output "$out/silent_16x9.mp4" --aspect 16:9 \
    || return 1
  echo "=== [$lang] rendering silent 9:16 ==="
  "$PY" scripts/render_visualizer.py --lyrics-json "$out/lyrics.json" \
    --song-wav "$out/song.wav" --output "$out/silent_9x16.mp4" --aspect 9:16 \
    || return 1
  echo "=== [$lang] muxing ==="
  ffmpeg -y -loglevel error -i "$out/silent_16x9.mp4" -i "$out/song.wav" \
    -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart "$out/$name16" \
    && ffmpeg -y -loglevel error -i "$out/silent_9x16.mp4" -i "$out/song.wav" \
    -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart "$out/$name9" \
    || return 1
  echo "=== [$lang] done ==="
}

FAIL=0
repair_lang en "Shining_Sun_2026-11-13.mp4" "Shining_Sun_2026-11-13_9x16.mp4" || FAIL=1
repair_lang zh "阳光的旅程_2026-11-13.mp4" "阳光的旅程_2026-11-13_9x16.mp4" || FAIL=1
exit "$FAIL"
