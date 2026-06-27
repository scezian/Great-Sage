#!/bin/bash
# Great Sage — Setup Script
# Supports: Arch/EndeavourOS/Manjaro, Ubuntu/Debian/Mint, Fedora/RHEL, openSUSE

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colors & styles ───────────────────────────────────────────────────────────
RESET="\033[0m"
BOLD="\033[1m"
DIM="\033[2m"

RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
CYAN="\033[0;36m"
WHITE="\033[0;37m"
MAGENTA="\033[0;35m"

# ── Helpers ───────────────────────────────────────────────────────────────────

ok()   { echo -e "  ${GREEN}${BOLD}✓${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}${BOLD}⚠${RESET}  $1"; }
err()  { echo -e "  ${RED}${BOLD}✗${RESET}  $1"; }
info() { echo -e "  ${CYAN}${BOLD}→${RESET}  $1"; }

section() {
    echo ""
    echo -e "  ${BOLD}${MAGENTA}$1${RESET}"
    echo -e "  ${DIM}$(printf '─%.0s' {1..40})${RESET}"
}

spinner() {
    local pid=$1
    local msg=$2
    local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${CYAN}${frames[$i]}${RESET}  ${DIM}%s...${RESET}" "$msg"
        i=$(( (i+1) % ${#frames[@]} ))
        sleep 0.08
    done
    printf "\r"
}

# ── Banner ────────────────────────────────────────────────────────────────────

clear
echo ""
echo -e "  ${BOLD}${MAGENTA}╔══════════════════════════════════════════╗${RESET}"
echo -e "  ${BOLD}${MAGENTA}║${RESET}                                          ${BOLD}${MAGENTA}║${RESET}"
echo -e "  ${BOLD}${MAGENTA}║${RESET}   ${BOLD}${WHITE}⚡  G R E A T   S A G E${RESET}               ${BOLD}${MAGENTA}║${RESET}"
echo -e "  ${BOLD}${MAGENTA}║${RESET}   ${DIM}Setup & Installation${RESET}                  ${BOLD}${MAGENTA}║${RESET}"
echo -e "  ${BOLD}${MAGENTA}║${RESET}                                          ${BOLD}${MAGENTA}║${RESET}"
echo -e "  ${BOLD}${MAGENTA}╚══════════════════════════════════════════╝${RESET}"
echo ""
sleep 0.4

# ── 1. Detect package manager ─────────────────────────────────────────────────

section "01  Detecting system"

detect_pkg_manager() {
    if command -v pacman &>/dev/null;  then echo "pacman"
    elif command -v apt &>/dev/null;   then echo "apt"
    elif command -v dnf &>/dev/null;   then echo "dnf"
    elif command -v zypper &>/dev/null; then echo "zypper"
    else echo "unknown"
    fi
}

PKG_MANAGER=$(detect_pkg_manager)

if [ "$PKG_MANAGER" = "unknown" ]; then
    err "Could not detect a supported package manager."
    warn "Please manually install: python3, python3-venv, mpv, git"
    warn "Then re-run this script."
    exit 1
fi

DISTRO_LABEL=""
case "$PKG_MANAGER" in
    pacman) DISTRO_LABEL="Arch / EndeavourOS / Manjaro" ;;
    apt)    DISTRO_LABEL="Ubuntu / Debian / Mint" ;;
    dnf)    DISTRO_LABEL="Fedora / RHEL" ;;
    zypper) DISTRO_LABEL="openSUSE" ;;
esac

ok "Package manager: ${BOLD}$PKG_MANAGER${RESET}  ${DIM}($DISTRO_LABEL)${RESET}"

# ── 2. System dependencies ────────────────────────────────────────────────────

section "02  System dependencies"

# Cache sudo credentials upfront so the password prompt is clean
echo -e "  ${DIM}Sudo access is needed to install system packages.${RESET}"
echo ""
sudo -v
echo ""

case "$PKG_MANAGER" in
    pacman)
        sudo pacman -Sy --needed --noconfirm python python-pip mpv git \
            python-pyqt6 python-pyqt6-webengine &>/dev/null &
        ;;
    apt)
        (sudo apt update -qq && sudo apt install -y python3 python3-pip python3-venv \
            mpv git libxcb-xinerama0 libxcb-cursor0 \
            python3-pyqt6 python3-pyqt6.qtwebengine &>/dev/null) &
        ;;
    dnf)
        sudo dnf install -y python3 python3-pip mpv git \
            python3-pyqt6 python3-pyqt6-webengine &>/dev/null &
        ;;
    zypper)
        sudo zypper install -y python3 python3-pip mpv git \
            python3-qt6 python3-qt6-webengine &>/dev/null &
        ;;
esac

INSTALL_PID=$!
spinner $INSTALL_PID "Installing system packages"
wait $INSTALL_PID
ok "System dependencies installed"

# ── 3. Directories ────────────────────────────────────────────────────────────

section "03  Directories"

mkdir -p ~/.config/mpv/scripts
mkdir -p ~/.config/great_sage
ok "~/.config/great_sage"
ok "~/.config/mpv/scripts"

if [ -f "$HOME/.config/mpv/scripts/next_episode.lua" ]; then
    rm "$HOME/.config/mpv/scripts/next_episode.lua"
    ok "Removed duplicate next_episode.lua"
fi

# ── 4. Virtual environment ────────────────────────────────────────────────────

section "04  Virtual environment"

if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv --system-site-packages "$SCRIPT_DIR/venv" &>/dev/null &
    VENV_PID=$!
    spinner $VENV_PID "Creating venv"
    wait $VENV_PID
    ok "venv created at ${DIM}$SCRIPT_DIR/venv${RESET} ${DIM}(with system site-packages)${RESET}"
else
    ok "venv already exists"
    # Ensure existing venvs also get access to system PyQt6-WebEngine
    if ! grep -q "include-system-site-packages = true" "$SCRIPT_DIR/venv/pyvenv.cfg" 2>/dev/null; then
        sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' \
            "$SCRIPT_DIR/venv/pyvenv.cfg" 2>/dev/null || true
        ok "Enabled system site-packages for existing venv"
    fi
fi

# ── 5. Python dependencies ────────────────────────────────────────────────────

section "05  Python packages"

PACKAGES=(
    "flask"
    "requests"
    "beautifulsoup4"
    "rich"
    "yt-dlp"
    "groq"
    "cloudscraper"
    "python-dotenv"
    "sounddevice"
    "numpy"
    "markdown-it-py"
    "Pygments"
    "pypdf"
)

"$SCRIPT_DIR/venv/bin/pip" install --quiet --upgrade pip &>/dev/null

# Remove any pip-installed PyQt6/PyQt6-WebEngine from a previous setup — these
# bundle their own ffmpeg/OpenSSL and conflict with/shadow the system Qt
# packages installed in step 02, which are what actually have working
# codecs and DRM support for the embedded browser.
"$SCRIPT_DIR/venv/bin/pip" uninstall --quiet -y PyQt6-WebEngine PyQt6-WebEngine-Qt6 \
    PyQt6 PyQt6-Qt6 PyQt6-sip &>/dev/null || true

TOTAL=${#PACKAGES[@]}
for i in "${!PACKAGES[@]}"; do
    PKG="${PACKAGES[$i]}"
    NUM=$((i + 1))
    BAR_FILLED=$(( NUM * 20 / TOTAL ))
    BAR_EMPTY=$(( 20 - BAR_FILLED ))
    BAR_STR=""
    for ((b=0; b<BAR_FILLED; b++)); do BAR_STR+="█"; done
    EMPTY_STR=""
    for ((b=0; b<BAR_EMPTY; b++)); do EMPTY_STR+="░"; done

    printf "\r  ${GREEN}%s${RESET}${DIM}%s${RESET}  ${DIM}%-22s${RESET}  ${CYAN}%2d/%d${RESET}" \
        "$BAR_STR" "$EMPTY_STR" "$PKG" "$NUM" "$TOTAL"

    "$SCRIPT_DIR/venv/bin/pip" install --quiet "$PKG" &>/dev/null
done

FULL_BAR=""
for ((b=0; b<20; b++)); do FULL_BAR+="█"; done
printf "\r  ${GREEN}%s${RESET}  %-22s  ${GREEN}${BOLD}Done!${RESET}              \n" "$FULL_BAR" ""
ok "All Python packages installed"

# ── 6. API key ────────────────────────────────────────────────────────────────

section "06  Groq API key"

ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo -e "  ${DIM}Great Sage uses Groq for AI features.${RESET}"
    echo -e "  ${DIM}Get a free key at: ${CYAN}https://console.groq.com${RESET}"
    echo ""
    read -rp "$(echo -e "  ${BOLD}${CYAN}Groq API key${RESET} ${DIM}(Enter to skip)${RESET}: ")" GROQ_KEY
    echo ""

    if [ -n "$GROQ_KEY" ]; then
        echo "GROQ_API_KEY=$GROQ_KEY" > "$ENV_FILE"
        ok "API key saved to .env"
    else
        warn "Skipped — add GROQ_API_KEY=your_key to .env before launching"
        echo "GROQ_API_KEY=" > "$ENV_FILE"
    fi
else
    ok ".env already exists, skipping"
fi

# ── 7. Shell aliases ──────────────────────────────────────────────────────────

section "07  Shell aliases"

BASH_ALIAS="alias open-great-sage=\"bash $SCRIPT_DIR/launch-great-sage.sh\""
FISH_ALIAS="alias open-great-sage 'bash $SCRIPT_DIR/launch-great-sage.sh'"

if [ -f ~/.bashrc ] && ! grep -q "open-great-sage" ~/.bashrc; then
    echo "$BASH_ALIAS" >> ~/.bashrc
    ok "Added to ~/.bashrc"
fi

if [ -f ~/.zshrc ] && ! grep -q "open-great-sage" ~/.zshrc; then
    echo "$BASH_ALIAS" >> ~/.zshrc
    ok "Added to ~/.zshrc"
fi

if command -v fish &>/dev/null; then
    mkdir -p "$HOME/.config/fish"
    FISH_CONFIG="$HOME/.config/fish/config.fish"
    if ! grep -q "open-great-sage" "$FISH_CONFIG" 2>/dev/null; then
        echo "$FISH_ALIAS" >> "$FISH_CONFIG"
        ok "Added to fish config"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
sleep 0.3
echo -e "  ${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}                                          ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}   ${BOLD}${WHITE}✓  Setup complete!${RESET}                    ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}                                          ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}   ${DIM}Open a new terminal, then run:${RESET}        ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}   ${BOLD}${CYAN}open-great-sage${RESET}                       ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}║${RESET}                                          ${BOLD}${GREEN}║${RESET}"
echo -e "  ${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
