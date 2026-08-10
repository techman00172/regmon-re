#!/usr/bin/env bash
# Copyright (c) 2026 Terry Porter <regmon@fastmail.com> — MIT license, see COPYING.
# setup.sh — install regmon-re (standalone register monitor + AI)
# Requires: opencode (the AI agent) — https://opencode.ai
#          python3 with tkinter (Arch: tk; Debian: python3-tk)
#          a working SWD debug probe (ST-Link etc.)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
CONSOLE="regmon-console"
SWDD="swdd"

echo "==> regmon-re installer (standalone register monitor + AI)"
echo ""

# --- core requirement: opencode ---
if command -v opencode &>/dev/null; then
    echo "  [   OK    ] opencode found"
else
    echo "  [ MISSING ] opencode — the AI agent."
    echo "             Install from https://opencode.ai then re-run."
    echo "             (Works with a local model OR a cloud AI like DeepSeek.)"
    exit 1
fi

# --- python3 + tkinter ---
if python3 -c "import tkinter" 2>/dev/null; then
    echo "  [   OK    ] python3 + tkinter"
else
    echo "  [ MISSING ] tkinter for python3."
    echo "             Arch:   sudo pacman -S tk"
    echo "             Debian: sudo apt install python3-tk"
    exit 1
fi

# --- SWD probe ---
if lsusb 2>/dev/null | grep -qiE "st-link|stlink"; then
    echo "  [   OK    ] ST-Link debug probe detected"
else
    echo "  [ WARNING ] no ST-Link probe detected on USB."
    echo "             Regmon RE needs an SWD probe to talk to the chip."
    echo "             (You can still launch the console; it will show 'No target'.)"
fi

# --- ensure target dir exists ---
mkdir -p "$BIN_DIR"

# --- link the console + swdd into ~/.local/bin ---
for name in "$CONSOLE" "$SWDD"; do
    src="$SCRIPT_DIR/$name"
    dst="$BIN_DIR/$name"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "  [   OK    ] $name — already linked"
    elif [ -e "$dst" ]; then
        echo "  [ WARNING ] $dst exists.  Remove it manually, then re-run."
        exit 1
    else
        ln -s "$src" "$dst"
        echo "  [   LINK  ] $dst -> $src"
    fi
done

# --- PATH check ---
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo "  [ NOTE ] $BIN_DIR is not in your PATH."
        echo "  Add this to your ~/.bashrc (or equivalent):"
        echo ""
        echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
        ;;
esac

echo ""
echo "==> Done.  To use:"
echo "    1. Plug in the ST-Link + target board."
echo "    2. Start swdd:   swdd &        (serves /tmp/swdd-cmd.sock)"
echo "    3. Launch:       regmon-console"
echo "    The console auto-detects the chip and uses opencode for AI."
