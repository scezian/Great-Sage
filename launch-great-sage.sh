#!/bin/bash

# Great Sage Launcher Script
# This script activates the virtual environment and launches Great Sage

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Change to the Great Sage directory
cd "$SCRIPT_DIR"

# Launch Great Sage GUI using venv python explicitly
echo "Launching Great Sage..."
nohup "$SCRIPT_DIR/venv/bin/python" great_sage_gui.py > /dev/null 2>&1 &
echo "Great Sage launched. You can close this terminal."
