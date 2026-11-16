#!/bin/bash
# Generate no-lyrics music videos (16:9 + 9:16) for every wav in pure-musics/,
# then move the wav and the resulting mp4s into outputs-videos/.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MUSIC_VIDEO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC_DIR="${PURE_MUSICS_DIR:-$ROOT/pure-musics}"
OUT_DIR="${OUTPUT_VIDEOS_DIR:-$ROOT/outputs-videos}"
WORK_ROOT="${PURE_WORK_DIR:-$ROOT/.pure-work}"
LOG_DIR="$ROOT/_logs"

DATE="${DATE:-$(date +%Y-%m-%d)}"
LOG_FILE="$LOG_DIR/pure-videos-$DATE.log"

if [ -z "${PYTHON:-}" ]; then
  if [ -x "$ROOT/.venv/bin/python3" ]; then
    PYTHON="$ROOT/.venv/bin/python3"
  else
    PYTHON=python3
  fi
fi
export PYTHONUNBUFFERED=1

mkdir -p "$OUT_DIR" "$LOG_DIR" "$WORK_ROOT" || exit 1
echo "Log: $LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== PURE VIDEOS START $DATE ==="
echo "root=$ROOT src=$SRC_DIR out=$OUT_DIR"

for COMMAND in "$PYTHON" ffmpeg; do
  if ! command -v "$COMMAND" >/dev/null 2>&1; then
    echo "FATAL: command not found: $COMMAND"
    exit 1
  fi
done
if ! "$PYTHON" -c 'import visgen' >/dev/null 2>&1; then
  echo "FATAL: visgen missing; install requirements.txt for $PYTHON"
  exit 1
fi

shopt -s nullglob
WAVS=("$SRC_DIR"/*.wav)
if [ "${#WAVS[@]}" -eq 0 ]; then
  echo "No wav files in $SRC_DIR; nothing to do."
  exit 0
fi

FAIL=0
for WAV in "${WAVS[@]}"; do
  STEM="$(basename "${WAV%.wav}")"
  WORK="$WORK_ROOT/$STEM"
  VIDEO_16X9="$WORK/${STEM}_16x9.mp4"
  VIDEO_9X16="$WORK/${STEM}_9x16.mp4"
  FINAL_16X9="$OUT_DIR/${STEM}_16x9.mp4"
  FINAL_9X16="$OUT_DIR/${STEM}_9x16.mp4"

  echo "--- [$STEM] start $(date +%T) ---"
  # Fresh work dir: a stale cover.png must never mask this song.
  rm -rf "$WORK"
  mkdir -p "$WORK"

  if "$PYTHON" "$SCRIPT_DIR/render_visualizer.py" \
       --song-wav "$WAV" --no-lyrics \
       --output "$WORK/silent_16x9.mp4" --aspect 16:9 \
  && "$PYTHON" "$SCRIPT_DIR/render_visualizer.py" \
       --song-wav "$WAV" --no-lyrics \
       --output "$WORK/silent_9x16.mp4" --aspect 9:16 \
  && ffmpeg -y -loglevel error \
       -i "$WORK/silent_16x9.mp4" -i "$WAV" \
       -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
       "$VIDEO_16X9" \
  && ffmpeg -y -loglevel error \
       -i "$WORK/silent_9x16.mp4" -i "$WAV" \
       -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
       "$VIDEO_9X16" \
  && [ -s "$VIDEO_16X9" ] && [ -s "$VIDEO_9X16" ]; then
    mv -f "$VIDEO_16X9" "$FINAL_16X9"
    mv -f "$VIDEO_9X16" "$FINAL_9X16"
    mv -f "$WAV" "$OUT_DIR/$STEM.wav"
    rm -rf "$WORK"
    echo "--- [$STEM] OK -> $FINAL_16X9, $FINAL_9X16 (+ wav) ---"
  else
    echo "--- [$STEM] FAILED; wav kept in $SRC_DIR ---"
    FAIL=1
  fi
done

echo "=== PURE VIDEOS DONE $DATE (fail=$FAIL) ==="
exit "$FAIL"
