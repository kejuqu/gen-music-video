#!/bin/bash
# Generate player-style 9:16 music videos (with karaoke lyrics) for every
# song folder in voice-musics/ (one wav + lyrics.txt per folder), then
# merge audio and archive the mp4 into outputs-videos/. Source folders are
# left in place.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MUSIC_VIDEO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SRC_DIR="${VOICE_MUSICS_DIR:-$ROOT/voice-musics}"
OUT_DIR="${OUTPUT_VIDEOS_DIR:-$ROOT/outputs-videos}"
WORK_ROOT="${VOICE_WORK_DIR:-$ROOT/.voice-work}"
ARTIST="${ARTIST:-一千柯橘}"
LOG_DIR="$ROOT/_logs"

DATE="${DATE:-$(date +%Y-%m-%d)}"
LOG_FILE="$LOG_DIR/voice-videos-$DATE.log"

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
echo "=== VOICE VIDEOS START $DATE ==="
echo "root=$ROOT src=$SRC_DIR out=$OUT_DIR artist=$ARTIST"

for COMMAND in "$PYTHON" ffmpeg; do
  if ! command -v "$COMMAND" >/dev/null 2>&1; then
    echo "FATAL: command not found: $COMMAND"
    exit 1
  fi
done
if ! "$PYTHON" -c 'import visgen, mlx_whisper' >/dev/null 2>&1; then
  echo "FATAL: visgen/mlx_whisper missing; install requirements.txt for $PYTHON"
  exit 1
fi

shopt -s nullglob
SONG_DIRS=("$SRC_DIR"/*/)
if [ "${#SONG_DIRS[@]}" -eq 0 ]; then
  echo "No song folders in $SRC_DIR; nothing to do."
  exit 0
fi

FAIL=0
for DIR in "${SONG_DIRS[@]}"; do
  STEM="$(basename "${DIR%/}")"
  WORK="$WORK_ROOT/$STEM"
  FINAL_9X16="$OUT_DIR/${STEM}_player_9x16.mp4"

  echo "--- [$STEM] start $(date +%T) ---"

  WAVS=("$DIR"*.wav)
  if [ "${#WAVS[@]}" -ne 1 ] || [ ! -f "$DIR/lyrics.txt" ]; then
    echo "--- [$STEM] FAILED: need exactly one wav and a lyrics.txt (found ${#WAVS[@]} wav) ---"
    FAIL=1
    continue
  fi
  WAV="${WAVS[0]}"

  mkdir -p "$WORK"

  if "$PYTHON" "$SCRIPT_DIR/render_player.py" \
       --song-wav "$WAV" \
       --lyrics-txt "$DIR/lyrics.txt" \
       --title "$STEM" \
       --artist "$ARTIST" \
       --timing "$WORK/timing.json" \
       --output "$WORK/silent_9x16.mp4" \
  && ffmpeg -y -loglevel error \
       -i "$WORK/silent_9x16.mp4" -i "$WAV" \
       -c:v copy -c:a aac -b:a 192k -shortest -movflags +faststart \
       "$WORK/${STEM}_player_9x16.mp4" \
  && [ -s "$WORK/${STEM}_player_9x16.mp4" ]; then
    mv -f "$WORK/${STEM}_player_9x16.mp4" "$FINAL_9X16"
    echo "--- [$STEM] OK -> $FINAL_9X16 (source kept in $DIR) ---"
  else
    echo "--- [$STEM] FAILED (work dir $WORK kept for retry) ---"
    FAIL=1
  fi
done

echo "=== VOICE VIDEOS DONE $DATE (fail=$FAIL) ==="
exit "$FAIL"
