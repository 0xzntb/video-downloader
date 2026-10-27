#!/bin/sh
# Viddl installer for Linux and macOS.
#
#   ./install.sh
#
# It creates a private virtual environment, installs Viddl into it, puts a
# `viddl` command on your PATH, and registers the viddl:// link handler so the
# browser extension can open the app. Nothing else on the machine is touched,
# and there is nothing to configure afterwards.

set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_DIR="${VIDDL_HOME:-$HOME/.local/share/viddl}"
VENV="$DATA_DIR/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/viddl"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- Python -----------------------------------------------------------------

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PYTHON=$(command -v "$candidate")
            break
        fi
    fi
done
[ -n "$PYTHON" ] || fail "Viddl needs Python 3.9 or newer. Install it, then run this again."

say "Using $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

# --- install ----------------------------------------------------------------

mkdir -p "$DATA_DIR" "$BIN_DIR"

if [ ! -x "$VENV/bin/python" ]; then
    say "Creating a private environment in $VENV"
    "$PYTHON" -m venv "$VENV" || fail "Could not create the environment. On Debian/Ubuntu install python3-venv."
else
    say "Reusing the environment in $VENV"
fi

say "Installing Viddl…"
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
# Install this project, which pulls in Textual. The version policy is the range
# in pyproject.toml, deliberately not the lockfile: `uv.lock` is what the tests
# are run against, and installing it here would pin a user to the versions of
# one Tuesday, with no way to pick up a Textual fix that matters to them.
if ! "$VENV/bin/python" -m pip install --quiet "$SOURCE_DIR"; then
    fail "pip could not install Viddl. Check your internet connection and try again."
fi

ln -sf "$VENV/bin/viddl" "$LAUNCHER"
say "Linked $LAUNCHER"

# --- browser hand-off -------------------------------------------------------

say ""
say "Registering the viddl:// link handler (so the extension can open Viddl)…"
"$VENV/bin/viddl" --register-protocol || {
    say "That step needs to be done by hand — the command is printed above."
}

# --- PATH -------------------------------------------------------------------

case ":${PATH}:" in
    *":$BIN_DIR:"*) ;;
    *)
        say ""
        say "One last thing: $BIN_DIR is not on your PATH yet."
        say "Add this line to your shell profile (~/.bashrc, ~/.zshrc, …) and restart your shell:"
        say ""
        say "    export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac

say ""
say "Viddl is installed."
say "  1. Run:      viddl"
say "  2. Paste any video link and pick a quality."
say "  3. For the browser button:  viddl --install-browser-extension"
say ""
say "The first download fetches yt-dlp into $DATA_DIR/bin (about 3 MB)."
say "ffmpeg is only fetched if you pick MP3 or a quality that needs merging."
say "Check on everything at any time with:  viddl --doctor"
