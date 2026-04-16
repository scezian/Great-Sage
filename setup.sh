#!/bin/bash
# Great Sage — Setup Script

echo "Setting up Great Sage..."

# Create folders
mkdir -p ~/Documents/great\ sage
mkdir -p ~/Documents/great\ sage/plugins
mkdir -p ~/.config/mpv/scripts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# next_episode.lua must only live in the app folder, NOT in ~/.config/mpv/scripts/
# (having it in both causes double-loading which breaks the play-next feature)
if [ -f "$HOME/.config/mpv/scripts/next_episode.lua" ]; then
    rm "$HOME/.config/mpv/scripts/next_episode.lua"
    echo "✓ Removed duplicate next_episode.lua from mpv scripts folder"
fi
echo "✓ next_episode.lua stays in the app folder (great_sage_gui.py loads it directly)"

# Install Python dependencies
echo "Installing dependencies..."
pip install PyQt6 PyQt6-WebEngine flask requests beautifulsoup4 rich yt-dlp groq cloudscraper python-dotenv sounddevice numpy --quiet
echo "✓ Dependencies installed"

# Create the alias
ALIAS_LINE='alias open-great-sage="python3 ~/Documents/great\ sage/great_sage_gui.py"'

# Add to bashrc if not already there
if ! grep -q "open-great-sage" ~/.bashrc; then
    echo "$ALIAS_LINE" >> ~/.bashrc
    echo "✓ Alias added to ~/.bashrc"
else
    echo "✓ Alias already exists in ~/.bashrc"
fi

# Also add to zshrc if it exists
if [ -f ~/.zshrc ] && ! grep -q "open-great-sage" ~/.zshrc; then
    echo "$ALIAS_LINE" >> ~/.zshrc
    echo "✓ Alias added to ~/.zshrc"
fi

# Also add to fish config if fish is installed
FISH_CONFIG="$HOME/.config/fish/config.fish"
FISH_ALIAS='alias open-great-sage="python3 ~/Documents/great\ sage/great_sage_gui.py"'
if command -v fish &>/dev/null; then
    mkdir -p "$HOME/.config/fish"
    if [ -f "$FISH_CONFIG" ] && grep -q "open-great-sage" "$FISH_CONFIG"; then
        echo "✓ Alias already exists in $FISH_CONFIG"
    else
        echo "$FISH_ALIAS" >> "$FISH_CONFIG"
        echo "✓ Alias added to $FISH_CONFIG"
    fi
fi

echo ""
echo "Done!"
echo "  bash/zsh : source ~/.bashrc  (or open a new terminal)"
echo "  fish     : source ~/.config/fish/config.fish  (or open a new terminal)"
echo "Then type: open-great-sage"
