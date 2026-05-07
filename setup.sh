#!/bin/bash
# Great Sage — Setup Script
# Supports: Arch/EndeavourOS/Manjaro, Ubuntu/Debian/Mint, Fedora/RHEL, openSUSE

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "╔══════════════════════════════════╗"
echo "║       Great Sage — Setup         ║"
echo "╚══════════════════════════════════╝"
echo ""

# ── 1. Detect package manager ────────────────────────────────────────────────

detect_pkg_manager() {
    if command -v pacman &>/dev/null; then
        echo "pacman"
    elif command -v apt &>/dev/null; then
        echo "apt"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v zypper &>/dev/null; then
        echo "zypper"
    else
        echo "unknown"
    fi
}

PKG_MANAGER=$(detect_pkg_manager)

if [ "$PKG_MANAGER" = "unknown" ]; then
    echo "⚠  Could not detect a supported package manager."
    echo "   Please manually install: python3, python3-venv, mpv, git"
    echo "   Then re-run this script."
    exit 1
fi

echo "✓ Detected package manager: $PKG_MANAGER"

# ── 2. Install system dependencies ───────────────────────────────────────────

echo "Installing system dependencies (may prompt for sudo)..."

case "$PKG_MANAGER" in
    pacman)
        sudo pacman -Sy --needed --noconfirm python python-pip mpv git
        ;;
    apt)
        sudo apt update -qq
        sudo apt install -y python3 python3-pip python3-venv mpv git \
            libxcb-xinerama0 libxcb-cursor0
        # libxcb packages needed for PyQt6 on some Ubuntu setups
        ;;
    dnf)
        sudo dnf install -y python3 python3-pip mpv git
        ;;
    zypper)
        sudo zypper install -y python3 python3-pip mpv git
        ;;
esac

echo "✓ System dependencies installed"

# ── 3. Create config and mpv dirs ────────────────────────────────────────────

mkdir -p ~/.config/mpv/scripts
mkdir -p ~/.config/great_sage
echo "✓ Config directories ready"

# ── 4. Remove duplicate next_episode.lua if present ──────────────────────────

if [ -f "$HOME/.config/mpv/scripts/next_episode.lua" ]; then
    rm "$HOME/.config/mpv/scripts/next_episode.lua"
    echo "✓ Removed duplicate next_episode.lua from mpv scripts"
fi

# ── 5. Set up virtualenv ──────────────────────────────────────────────────────

if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
    echo "✓ venv created"
else
    echo "✓ venv already exists"
fi

# ── 6. Install Python dependencies ───────────────────────────────────────────

echo "Installing Python dependencies..."
"$SCRIPT_DIR/venv/bin/pip" install --quiet --upgrade pip
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

echo "✓ Python dependencies installed"

# ── 7. API key setup ──────────────────────────────────────────────────────────

ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "Great Sage needs a Groq API key to use the AI features."
    echo "Get one free at: https://console.groq.com"
    echo ""
    read -rp "Enter your Groq API key (or press Enter to skip): " GROQ_KEY

    if [ -n "$GROQ_KEY" ]; then
        echo "GROQ_API_KEY=$GROQ_KEY" > "$ENV_FILE"
        echo "✓ API key saved to .env"
    else
        echo "⚠  Skipped. Add GROQ_API_KEY=your_key to $ENV_FILE before launching."
        echo "GROQ_API_KEY=" > "$ENV_FILE"
    fi
else
    echo "✓ .env already exists, skipping API key setup"
fi

# ── 8. Shell alias setup ──────────────────────────────────────────────────────

BASH_ALIAS="alias open-great-sage=\"bash $SCRIPT_DIR/launch-great-sage.sh\""
FISH_ALIAS="alias open-great-sage 'bash $SCRIPT_DIR/launch-great-sage.sh'"

# bash
if [ -f ~/.bashrc ] && ! grep -q "open-great-sage" ~/.bashrc; then
    echo "$BASH_ALIAS" >> ~/.bashrc
    echo "✓ Alias added to ~/.bashrc"
fi

# zsh
if [ -f ~/.zshrc ] && ! grep -q "open-great-sage" ~/.zshrc; then
    echo "$BASH_ALIAS" >> ~/.zshrc
    echo "✓ Alias added to ~/.zshrc"
fi

# fish (uses different alias syntax)
if command -v fish &>/dev/null; then
    mkdir -p "$HOME/.config/fish"
    FISH_CONFIG="$HOME/.config/fish/config.fish"
    if ! grep -q "open-great-sage" "$FISH_CONFIG" 2>/dev/null; then
        echo "$FISH_ALIAS" >> "$FISH_CONFIG"
        echo "✓ Alias added to fish config"
    fi
fi

# ── 9. Done ───────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════"
echo "  ✓ Great Sage is ready!"
echo ""
echo "  Run:  open-great-sage"
echo "  (open a new terminal or run: source ~/.bashrc)"
echo "══════════════════════════════════════"
echo ""
