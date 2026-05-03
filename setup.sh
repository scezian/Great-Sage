#!/bin/bash
# Great Sage — Setup Script

echo "Setting up Great Sage..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create required folders
mkdir -p ~/.config/mpv/scripts
mkdir -p ~/.config/great_sage

# Remove duplicate next_episode.lua if it exists in mpv scripts
if [ -f "$HOME/.config/mpv/scripts/next_episode.lua" ]; then
    rm "$HOME/.config/mpv/scripts/next_episode.lua"
    echo "✓ Removed duplicate next_episode.lua from mpv scripts folder"
fi

# Set up venv if it doesn't exist
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
    echo "✓ venv created"
fi

# Install dependencies into venv
echo "Installing dependencies..."
"$SCRIPT_DIR/venv/bin/pip" install --quiet \
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
    markdown-it-py \
    Pygments

echo "✓ Dependencies installed"

# Create alias pointing to launch script
ALIAS_LINE="alias open-great-sage=\"bash $SCRIPT_DIR/launch-great-sage.sh\""

if ! grep -q "open-great-sage" ~/.bashrc; then
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "✓ Alias added to ~/.bashrc"
else
    echo "✓ Alias already exists in ~/.bashrc"
fi

if [ -f ~/.zshrc ] && ! grep -q "open-great-sage" ~/.zshrc; then
    echo "$ALIAS_LINE" >> ~/.zshrc
    echo "✓ Alias added to ~/.zshrc"
fi

FISH_CONFIG="$HOME/.config/fish/config.fish"
if command -v fish &>/dev/null; then
    mkdir -p "$HOME/.config/fish"
    if ! grep -q "open-great-sage" "$FISH_CONFIG" 2>/dev/null; then
        echo "$ALIAS_LINE" >> "$FISH_CONFIG"
        echo "✓ Alias added to fish config"
    fi
fi

echo ""
echo "✓ Done! Run: open-great-sage"
echo "  (source ~/.bashrc first or open a new terminal)"
