#!/bin/sh
# Remove Viddl.
#
#   ./uninstall.sh           remove the app, keep the yt-dlp/ffmpeg helpers
#   ./uninstall.sh --purge   remove everything, including the helpers and logs

set -eu

DATA_DIR="${VIDDL_HOME:-$HOME/.local/share/viddl}"
VENV="$DATA_DIR/venv"
LAUNCHER="$HOME/.local/bin/viddl"
PURGE=0

[ "${1:-}" = "--purge" ] && PURGE=1

say() { printf '%s\n' "$*"; }

if [ -x "$VENV/bin/viddl" ]; then
    "$VENV/bin/viddl" --unregister-protocol >/dev/null 2>&1 || true
fi

rm -f "$LAUNCHER"
say "Removed $LAUNCHER"

rm -rf "$VENV"
say "Removed $VENV"

# Linux desktop entries, in case they are somewhere unexpected.
for entry in \
    "$HOME/.local/share/applications/viddl-handler.desktop" \
    "$DATA_DIR/viddl-handler.desktop"
do
    [ -e "$entry" ] && rm -f "$entry" && say "Removed $entry"
done

if [ "$PURGE" -eq 1 ]; then
    rm -rf "$DATA_DIR"
    say "Removed $DATA_DIR (yt-dlp, ffmpeg and logs)"
else
    say ""
    say "Kept $DATA_DIR (yt-dlp, ffmpeg and logs)."
    say "Remove that folder too with:  ./uninstall.sh --purge"
fi

say ""
say "The browser extension is managed by the browser: remove it from"
say "chrome://extensions if you loaded it."
