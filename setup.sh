#!/bin/bash
# Great Sage — Setup Script

echo "Setting up Great Sage..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create folders
mkdir -p ~/Documents/great\ sage
mkdir -p ~/Documents/great\ sage/plugins
mkdir -p ~/.config/mpv/scripts

# next_episode.lua must only live in the app folder, NOT in ~/.config/mpv/scripts/
if [ -f "$HOME/.config/mpv/scripts/next_episode.lua" ]; then
    rm "$HOME/.config/mpv/scripts/next_episode.lua"
    echo "✓ Removed duplicate next_episode.lua from mpv scripts folder"
fi
echo "✓ next_episode.lua stays in the app folder (great_sage_gui.py loads it directly)"

# Create virtual environment if it doesn't exist
VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "✓ Virtual environment created at $VENV_DIR"
else
    echo "✓ Virtual environment already exists"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

# Install Python dependencies
echo "Installing dependencies..."
pip install --quiet \
    PyQt6 \
    PyQt6-WebEngine \
    flask \
    requests \
    beautifulsoup4 \
    rich \
    yt-dlp \
    groq \
    cloudscraper \
    python-dotenv \
    sounddevice \
    numpy \
    lxml
echo "✓ Dependencies installed"

# Create the alias (points to venv python)
ALIAS_LINE="alias open-great-sage=\"$VENV_DIR/bin/python $SCRIPT_DIR/great_sage_gui.py\""

# Add to bashrc if not already there
if ! grep -q "open-great-sage" ~/.bashrc; then
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "✓ Alias added to ~/.bashrc"
else
    # Update existing alias
    sed -i '/open-great-sage/d' ~/.bashrc
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "✓ Alias updated in ~/.bashrc"
fi

# Also add to zshrc if it exists
if [ -f ~/.zshrc ]; then
    if grep -q "open-great-sage" ~/.zshrc; then
        sed -i '/open-great-sage/d' ~/.zshrc
    fi
    echo "$ALIAS_LINE" >> ~/.zshrc
    echo "✓ Alias updated in ~/.zshrc"
fi

echo ""
echo "Done!"
echo "  bash : source ~/.bashrc"
echo "  zsh  : source ~/.zshrc"
echo "Then type: open-great-sage"
