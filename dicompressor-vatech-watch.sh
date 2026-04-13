#!/bin/bash
# Dedicated watch script for the Vatech workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DICOMPRESSOR="$SCRIPT_DIR/dicompressor-vatech.py"
MARKER=".dicompressor_vatech_done"
WATCH_DIR="${1:?Usage: $0 /path/to/patients [interval_seconds] [output_dir]}"
INTERVAL="${2:-300}"
OUTPUT_DIR="${3:-}"

if [ ! -d "$WATCH_DIR" ]; then
    echo "ERROR: Directory not found: $WATCH_DIR"
    exit 1
fi

if [ ! -f "$DICOMPRESSOR" ]; then
    echo "ERROR: dicompressor-vatech.py not found at: $DICOMPRESSOR"
    exit 1
fi

if [ -n "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR"
fi

echo "═══════════════════════════════════════════════════"
echo " DicomPressor Vatech Watch Mode"
echo " Watching:    $WATCH_DIR"
echo " Interval:    ${INTERVAL}s"
echo " Marker:      $MARKER"
if [ -n "$OUTPUT_DIR" ]; then
echo " Output dir:  $OUTPUT_DIR"
fi
echo " Press Ctrl+C to stop"
echo "═══════════════════════════════════════════════════"
echo ""

while true; do
    NEW_COUNT=0
    DONE_COUNT=0
    EMPTY_COUNT=0

    for dir in "$WATCH_DIR"/*/; do
        [ -d "$dir" ] || continue
        FOLDER_NAME=$(basename "$dir")

        if [ -f "$dir/$MARKER" ]; then
            DONE_COUNT=$((DONE_COUNT + 1))
            continue
        fi

        MATCH_COUNT=$(find "$dir" -maxdepth 2 -type f \( -name "*.dcm" -o -name "*.DCM" -o -name "*.ct" -o -name "*.CT" -o -name "*.ct.dcm" -o -name "*.CT.dcm" \) 2>/dev/null | wc -l)
        if [ "$MATCH_COUNT" -eq 0 ]; then
            EMPTY_COUNT=$((EMPTY_COUNT + 1))
            continue
        fi

        NEW_COUNT=$((NEW_COUNT + 1))
        echo ""
        echo "[$(date '+%H:%M:%S')] NEW: $FOLDER_NAME ($MATCH_COUNT matching files)"
        echo "  Processing..."

        CMD=(python3 "$DICOMPRESSOR" -j --skip-if-done)
        if [ -n "$OUTPUT_DIR" ]; then
            CMD+=(--output-dir "$OUTPUT_DIR")
        fi
        CMD+=(-f "$dir")

        if "${CMD[@]}"; then
            echo "  Done!"
        else
            echo "  FAILED (see log above)"
        fi
    done

    TOTAL=$((NEW_COUNT + DONE_COUNT + EMPTY_COUNT))
    if [ "$NEW_COUNT" -eq 0 ]; then
        echo -ne "\r[$(date '+%H:%M:%S')] $TOTAL folders ($DONE_COUNT done, $EMPTY_COUNT empty). Next scan in ${INTERVAL}s...  "
    else
        echo ""
        echo "[$(date '+%H:%M:%S')] Processed $NEW_COUNT new folder(s). Total: $TOTAL ($DONE_COUNT done)"
    fi

    sleep "$INTERVAL"
done
