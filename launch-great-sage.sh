#!/bin/bash

# Great Sage Launcher Script
# This script activates the virtual environment and launches Great Sage

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Auto-update ────────────────────────────────────────────────────────────
# Pull latest changes quietly. If anything new came in, re-run setup so the
# venv/system deps stay in sync — users never have to think about this.
if command -v git &>/dev/null && [ -d "$SCRIPT_DIR/.git" ]; then
    BEFORE=$(git rev-parse HEAD 2>/dev/null)
    git pull --quiet --ff-only >/dev/null 2>&1
    AFTER=$(git rev-parse HEAD 2>/dev/null)

    if [ "$BEFORE" != "$AFTER" ] || [ ! -d "$SCRIPT_DIR/venv" ]; then
        echo "Updating Great Sage, this may take a moment..."
        chmod +x "$SCRIPT_DIR/setup.sh"
        "$SCRIPT_DIR/setup.sh"
    fi
fi

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Launch Great Sage GUI using venv python explicitly
echo "Launching Great Sage..."
nohup "$SCRIPT_DIR/venv/bin/python" great_sage_gui.py > /dev/null 2>&1 &
echo "Great Sage launched. You can close this terminal."
