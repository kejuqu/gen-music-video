#!/bin/bash
# Do not enable `set -e`: one language may fail while the other must still run.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PIPELINE_ENV_FILE:-$SCRIPT_DIR/.env}"
if [ -f "$ENV_FILE" ]; then
  # File values provide defaults; explicit environment variables (for example
  # LANGS=zh or DATE=...) must retain priority over scripts/.env.
  SAVED_ENVIRONMENT="$(export -p)"
  set -a
  # shellcheck disable=SC1091
  . "$ENV_FILE"
  set +a
  eval "$SAVED_ENVIRONMENT"
fi

DATE="${DATE:-$(date +%Y-%m-%d)}"
ROOT="${MUSIC_VIDEO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DAY="$ROOT/$DATE"
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$SCRIPT_DIR/../.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/../.venv/bin/python3"
  else
    PYTHON=python3
  fi
fi
export PYTHONUNBUFFERED=1
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
LANGS="${LANGS:-zh en}"
THEME="${THEME:-}"
RETENTION_DAYS="${RETENTION_DAYS:-}"
NOTIFY_FAILURE="${NOTIFY_FAILURE:-0}"
YOVOICE_HOST="${YOVOICE_HOST:-127.0.0.1}"
YOVOICE_PORT="${YOVOICE_PORT:-8080}"
YOVOICE_API_BASE="${YOVOICE_API_BASE:-http://$YOVOICE_HOST:$YOVOICE_PORT}"
YOVOICE_API_BASE="${YOVOICE_API_BASE%/}"
YOVOICE_DATA_DIR="${YOVOICE_DATA_DIR:-$ROOT/.yovoice-data}"
YOVOICE_TOKEN_FILE="${YOVOICE_TOKEN_FILE:-$ROOT/.yovoice-token}"
export MUSIC_VIDEO_ROOT="$ROOT"
export YOVOICE_API_BASE YOVOICE_TOKEN_FILE

if [[ ! "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "FATAL: DATE must use YYYY-MM-DD" >&2
  exit 1
fi
if [ -n "$RETENTION_DAYS" ] && [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "FATAL: RETENTION_DAYS must be a non-negative integer" >&2
  exit 1
fi

mkdir -p "$DAY" "$ROOT/_logs" || exit 1
LOG_FILE="$ROOT/_logs/$DATE.log"
touch "$LOG_FILE" || exit 1
echo "Log: $LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== START $DATE ==="
echo "root=$ROOT langs=$LANGS"
echo "Checking dependencies and local model services..."

for COMMAND in "$PYTHON" curl ffmpeg ffprobe ollama; do
  if ! command -v "$COMMAND" >/dev/null 2>&1; then
    echo "FATAL: command not found: $COMMAND"
    exit 1
  fi
done
if ! command -v "$BLENDER" >/dev/null 2>&1; then
  echo "FATAL: Blender not found: $BLENDER"
  exit 1
fi
if ! "$PYTHON" -c 'import ollama, requests' >/dev/null 2>&1; then
  echo "FATAL: Python dependencies missing; install requirements.txt for $PYTHON"
  exit 1
fi
if ! ollama list >/dev/null 2>&1; then
  echo "FATAL: Ollama is unavailable; start it with 'ollama serve' and pull both models"
  exit 1
fi
if [ -z "${YOVOICE_API_TOKEN:-}" ]; then
  if [ ! -r "$YOVOICE_TOKEN_FILE" ]; then
    echo "FATAL: YoVoice token not readable: $YOVOICE_TOKEN_FILE"
    exit 1
  fi
  YOVOICE_API_TOKEN="$(< "$YOVOICE_TOKEN_FILE")"
fi
if [ -z "$YOVOICE_API_TOKEN" ]; then
  echo "FATAL: YoVoice token is empty"
  exit 1
fi
# The client and a service launched below must use the same credential.
export YOVOICE_API_TOKEN

read -r -a LANG_LIST <<< "$LANGS"
if [ "${#LANG_LIST[@]}" -eq 0 ]; then
  echo "FATAL: LANGS cannot be empty"
  exit 1
fi
for LANG in "${LANG_LIST[@]}"; do
  case "$LANG" in
    zh|en) ;;
    *) echo "FATAL: unsupported language in LANGS: $LANG (expected zh or en)"; exit 1 ;;
  esac
done

YOVOICE_PID=""
cleanup() {
  if [ -n "$YOVOICE_PID" ] && kill -0 "$YOVOICE_PID" >/dev/null 2>&1; then
    echo "stopping yovoice serve started by this pipeline (pid=$YOVOICE_PID)"
    kill "$YOVOICE_PID" >/dev/null 2>&1 || true
    wait "$YOVOICE_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

yovoice_healthy() {
  curl -sf --max-time 5 -o /dev/null "$YOVOICE_API_BASE/health" || \
    curl -sf --max-time 5 -o /dev/null \
      -H "Authorization: Bearer $YOVOICE_API_TOKEN" \
      "$YOVOICE_API_BASE/v1/status"
}

# Probe/start YoVoice once; both languages share the same service instance.
if ! yovoice_healthy; then
  if ! command -v yovoice >/dev/null 2>&1; then
    echo "FATAL: YoVoice is unhealthy and 'yovoice' CLI is unavailable for auto-start"
    exit 1
  fi
  echo "starting yovoice serve..."
  mkdir -p "$YOVOICE_DATA_DIR"
  yovoice serve --data-dir "$YOVOICE_DATA_DIR" \
    --listen "$YOVOICE_HOST:$YOVOICE_PORT" \
    >> "$ROOT/_logs/yovoice-$DATE.log" 2>&1 &
  YOVOICE_PID=$!
  for ((ATTEMPT = 1; ATTEMPT <= 30; ATTEMPT++)); do
    if yovoice_healthy; then
      break
    fi
    if ! kill -0 "$YOVOICE_PID" >/dev/null 2>&1; then
      echo "FATAL: yovoice serve exited during startup; see _logs/yovoice-$DATE.log"
      exit 1
    fi
    sleep 1
  done
  if ! yovoice_healthy; then
    echo "FATAL: yovoice serve not healthy after 30s"
    exit 1
  fi
fi

FAIL=0
run_stage() {
  local label="$1"
  shift
  echo "[$LANG] $label — started $(date +%T)"
  "$@"
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "[$LANG] $label — FAILED (exit=$status); see $LOG_FILE"
  else
    echo "[$LANG] $label — completed $(date +%T)"
  fi
  return "$status"
}
for LANG in "${LANG_LIST[@]}"; do
  OUT="$DAY/$LANG"
  mkdir -p "$OUT"
  echo "--- [$LANG] start $(date +%T) ---"

  # Remove stale stage outputs so an old file can never mask a failed rerun.
  rm -f "$OUT/lyrics.json" "$OUT/song.wav" "$OUT/analysis.wav" \
    "$OUT/silent_video.mp4"

  if LYRICS_LANG="$LANG" THEME="$THEME" run_stage "1/5 Lyrics (Ollama)" "$PYTHON" "$SCRIPT_DIR/generate_lyrics.py" \
        --output "$OUT/lyrics.json" \
     && FINAL_VIDEO="$OUT/$("$PYTHON" "$SCRIPT_DIR/video_filename.py" \
        --lyrics "$OUT/lyrics.json" --date "$DATE")" \
     && rm -f "$FINAL_VIDEO" \
     && run_stage "2/5 Music (YoVoice, 180 seconds)" "$PYTHON" "$SCRIPT_DIR/generate_music.py" \
        --lyrics "$OUT/lyrics.json" \
        --output "$OUT/song.wav" \
     && run_stage "3/5 Audio analysis preparation" ffmpeg -y -loglevel error -i "$OUT/song.wav" \
        -ac 1 -ar 44100 -sample_fmt s16 "$OUT/analysis.wav" \
     && run_stage "4/5 Video rendering (4320 frames)" "$BLENDER" \
        --background --factory-startup -noaudio \
        --python-exit-code 1 \
        --python "$SCRIPT_DIR/render_scene.py" -- \
        --lyrics-json "$OUT/lyrics.json" \
        --analysis-wav "$OUT/analysis.wav" \
        --output "$OUT/silent_video.mp4" \
     && run_stage "5/5 MP4 assembly" ffmpeg -y -loglevel error \
        -i "$OUT/silent_video.mp4" -i "$OUT/song.wav" \
        -c:v copy -c:a aac -b:a 256k -t 180 -shortest -movflags +faststart \
        "$FINAL_VIDEO" \
     && [ -s "$FINAL_VIDEO" ]; then
    echo "--- [$LANG] OK $(date +%T) -> $FINAL_VIDEO ---"
  else
    echo "--- [$LANG] FAILED $(date +%T) ---"
    FAIL=1
    if [ "$NOTIFY_FAILURE" = "1" ]; then
      osascript -e "display notification \"$DATE $LANG 生成失败\" with title \"music-video\"" || true
    fi
  fi
done

if [ -n "$RETENTION_DAYS" ]; then
  find "$ROOT" -type f -name analysis.wav -mtime "+$RETENTION_DAYS" -delete
  find "$ROOT" -type f -name silent_video.mp4 -mtime "+$RETENTION_DAYS" -delete
fi

echo "=== DONE $DATE (fail=$FAIL) ==="
exit "$FAIL"
