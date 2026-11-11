#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.local.musicvideo.daily"
TEMPLATE="$PROJECT_ROOT/launchd/$LABEL.plist.template"
DESTINATION="${LAUNCHD_PLIST:-$HOME/Library/LaunchAgents/$LABEL.plist}"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "launchd installation is only supported on macOS" >&2
  exit 1
fi
if [ ! -f "$TEMPLATE" ]; then
  echo "missing template: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$(dirname "$DESTINATION")" "$PROJECT_ROOT/_logs"
python3 - "$TEMPLATE" "$DESTINATION" "$PROJECT_ROOT" <<'PY'
from pathlib import Path
import sys

template, destination, root = map(Path, sys.argv[1:])
content = template.read_text(encoding="utf-8").replace("@PROJECT_ROOT@", str(root))
destination.write_text(content, encoding="utf-8")
PY

chmod 644 "$DESTINATION"
plutil -lint "$DESTINATION"
launchctl unload "$DESTINATION" >/dev/null 2>&1 || true
launchctl load "$DESTINATION"

echo "Installed and loaded: $DESTINATION"
echo "Trigger now: launchctl start $LABEL"
echo "Inspect:     launchctl list | grep musicvideo"
