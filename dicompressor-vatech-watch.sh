#!/bin/bash
# Dedicated watch script for the Vatech workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$SCRIPT_DIR/dicompressor-vatech.sh"
WATCH_DIR="${1:?Usage: $0 /path/to/patients [interval_seconds] [output_dir] [log_file]}"
INTERVAL="${2:-300}"
OUTPUT_DIR="${3:-}"
LOG_FILE="${4:-$SCRIPT_DIR/dicompressor-vatech.log}"

if [ ! -d "$WATCH_DIR" ]; then
    echo "ERROR: Directory not found: $WATCH_DIR"
    exit 1
fi

if [ ! -f "$WRAPPER" ]; then
    echo "ERROR: dicompressor-vatech.sh not found at: $WRAPPER"
    exit 1
fi

if [ -n "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR"
fi

mkdir -p "$(dirname "$LOG_FILE")"

echo "═══════════════════════════════════════════════════"
echo " DicomPressor Vatech Watch Mode"
echo " Watching:    $WATCH_DIR"
echo " Interval:    ${INTERVAL}s"
if [ -n "$OUTPUT_DIR" ]; then
echo " Output dir:  $OUTPUT_DIR"
fi
echo " Log file:    $LOG_FILE"
echo " Press Ctrl+C to stop"
echo "═══════════════════════════════════════════════════"
echo ""

CMD=("$WRAPPER" -j --watch "$INTERVAL" --log-file "$LOG_FILE" -f "$WATCH_DIR")
if [ -n "$OUTPUT_DIR" ]; then
    CMD+=(--output-dir "$OUTPUT_DIR")
fi

exec "${CMD[@]}"
