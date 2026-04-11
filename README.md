# Great Sage

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/PyQt6-6.0+-green?style=flat-square&logo=qt&logoColor=white" alt="PyQt6">
  <img src="https://img.shields.io/badge/Platform-Linux-lightgrey?style=flat-square&logo=linux&logoColor=white" alt="Linux">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

> **Your personal media companion for Linux.**
> A unified desktop app that tracks your reading, manages your anime/show library, and gives you an AI companion that actually knows your taste — all wrapped in a dark, minimal, distraction-free UI.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
  - [Legion — Novel Reader](#legion--novel-reader)
  - [Matrix — Media Manager](#matrix--media-manager)
  - [Sage — AI Companion](#sage--ai-companion)
  - [Artemis — Rich-Text Editor](#artemis--rich-text-editor)
  - [Catalogue — Chapter Notes](#catalogue--chapter-notes)
  - [Plugin System](#plugin-system)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [File Structure](#file-structure)
- [Plugin Development](#plugin-development)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Great Sage brings together everything you consume — web novels, anime, shows — into one cohesive desktop experience. Instead of juggling browser tabs for reading, separate apps for tracking shows, and external note-taking tools, Great Sage unifies it all with a context-aware AI that can recommend, summarize, and chat about your media.

Built with **PyQt6** for native performance, featuring the **"Ink & Amber"** design system — a dark, warm aesthetic that stays out of your way.

---

## Features

### Legion — Novel Reader

A full-featured web novel reader with automatic chapter fetching.

| Feature | Description |
|---------|-------------|
| **Source Scraping** | Download chapters directly from RoyalRoad, NovelBin, and other sources via extensible plugins |
| **Progress Tracking** | Automatic chapter progress, word count statistics, and reading history |
| **Auto-Sync** | Background worker checks for new chapters and syncs them automatically |
| **Offline Reading** | Download entire novels for offline reading with full-text search |
| **Reading Stats** | Daily streaks, words read, chapters completed — all visualized |
| **Bookmarks** | Save positions and mark favorites |

```
┌─────────────────────────────────────────┐
│  Legion  │  Book Title  │  Chapter 42   │
│─────────────────────────────────────────│
│                                         │
│  The ancient formation hummed with...   │
│                                         │
│  [← Prev]              [Next →]         │
│  Words: 1,247  │  42/156 chapters      │
└─────────────────────────────────────────┘
```

### Matrix — Media Manager

Track and watch your anime and shows with seamless mpv integration.

| Feature | Description |
|---------|-------------|
| **Watchlist Management** | Track shows across Watching, Planning, and Completed states |
| **MPV Integration** | Native playback with position resumption and automatic progress saving |
| **Torrent Support** | Built-in torrent search and download via Transmission integration |
| **Next-Episode Flow** | Smart overlay in mpv asking to continue to next episode |
| **Subtitle Management** | Auto-download and sync subtitles |
| **Progress Sync** | Resume exactly where you left off, every time |

**Matrix mpv overlay:**
```
┌─────────────────────────────────────────┐
│                                         │
│         Continue to Episode 5?          │
│                                         │
│         [Enter/n] Accept    [Esc/x] Skip│
│                                         │
└─────────────────────────────────────────┘
```

### Sage — AI Companion

Powered by **Groq Cloud** — an AI that actually knows what you're consuming.

| Feature | Description |
|---------|-------------|
| **Cross-Media Recommendations** | "Based on the novels you're reading, try these shows" |
| **Chapter Summaries** | Catch up on a novel you've been away from |
| **Watchlist Prioritizer** | Rank your unwatched queue by fit to your taste |
| **Explain Why** | Analyze any title against your profile |
| **Free-Form Chat** | Talk naturally about your media |
| **Context Awareness** | Knows what you're mid-way through |

Sage has access to your full Legion and Matrix data, building a unified taste profile for genuinely useful recommendations.

### Artemis — Rich-Text Editor

A standalone writing environment matching the Ink & Amber design system.

- **Typography-focused**: Palatino Linotype body with JetBrains Mono UI
- **Distraction-free**: Clean interface for focused writing sessions
- **Standalone or Integrated**: Run alone or embed in Great Sage

### Catalogue — Chapter Notes

Chapter-anchored note-taking for deep readers.

- Store notes per book, per chapter
- Tag system: Character, Plot, Power-up, World, Reaction, Quote
- Sidebar panel that mirrors Sage UI patterns
- Persistent storage in `~/Documents/Great Sage/Catalogue/`

### Plugin System

Extensible architecture for custom features.

Drop a `.py` file into `~/Documents/great sage/plugins/` and it automatically loads with access to:

- Your reading data (Legion)
- Your watching data (Matrix)
- Isolated plugin storage
- Sage API for AI features
- UI component library

**Built-in plugins:**
- `ambient.py` — Ambient soundscape generator
- `book_covers.py` — Cover image management
- `clock_widget.py` — Time display widget
- `now_playing.py` — Media status display
- `visualizer.py` — Audio visualizer
- `theme_engine.py` — Theme customization

---

## Installation

### Requirements

- Linux (X11 or Wayland)
- Python 3.10 or higher
- mpv (for video playback)
- Git

### Quick Setup

```bash
# Clone the repository
git clone https://github.com/scezian/Great-Sage.git
cd Great-Sage

# Run the setup script
bash setup.sh

# Reload shell configuration
source ~/.bashrc

# Launch the app
open-great-sage
```

### Manual Setup

```bash
# Install Python dependencies
pip install PyQt6 flask requests beautifulsoup4 rich yt-dlp groq python-dotenv

# Create application directories
mkdir -p ~/Documents/great sage/{plugins,logs,Catalogue,writer}
mkdir -p ~/.config/matrix

# Run the app directly
python3 great_sage_gui.py
```

---

## Configuration

### Groq API Key (Required for Sage)

1. Sign up at [console.groq.com](https://console.groq.com)
2. Generate an API key
3. Paste it in **Settings → Sage → API Key** inside the app

Your key is stored locally with your Matrix data and never leaves your machine.

### Environment Variables

Create a `.env` file in the project root:

```env
# Optional: Pre-configure Groq API key
GROQ_API_KEY=your_key_here

# Optional: Custom data directories
GREAT_SAGE_DATA=/path/to/custom/data
```

### Data Locations

```
~/.great_sage_legion.json          # Novel progress & bookmarks
~/.config/matrix/progress.json     # Show watchlist & progress
~/Documents/great sage/            # Main app folder
├── plugins/                       # Plugin files (.py)
├── Catalogue/                     # Chapter notes
│   └── <Book Title>/
│       └── notes.json
├── logs/                          # Daily log files
│   └── YYYY-MM-DD.log
└── writer/                        # Artemis documents
```

---

## Usage

### Main Navigation

The app uses a **Nav Rail** on the left for primary navigation:

| Icon | Section | Description |
|------|---------|-------------|
| 🏠 | **Dash** | Overview dashboard |
| 📚 | **Legion** | Novel reader |
| 📺 | **Matrix** | Media manager |
| 🤖 | **Sage** | AI companion |
| 🔌 | **Plugins** | Installed plugins |
| ⚙️ | **Settings** | Configuration |

### Reading a Novel (Legion)

1. Navigate to **Legion**
2. Click **"Add Book"** and paste a novel URL (RoyalRoad, NovelBin, etc.)
3. The app scrapes chapter list automatically
4. Click any chapter to start reading
5. Use **Prev/Next** buttons or keyboard shortcuts to navigate

### Watching a Show (Matrix)

1. Navigate to **Matrix**
2. Search for a show or add manually
3. Set status: **Watching**, **Planning**, or **Completed**
4. Click **Play** — opens in mpv with position resumption
5. When episode ends, a prompt asks to continue to next

### Chatting with Sage

1. Navigate to **Sage**
2. Type naturally — no special commands needed
3. Try asking:
   - "What should I watch next?"
   - "Summarize the last 5 chapters of [novel]"
   - "Rank my watchlist by what I'd like most"
   - "Why would I like [specific title]?"

---

## Keyboard Shortcuts

### Global Navigation

| Shortcut | Action |
|----------|--------|
| `Ctrl+1` | Navigate to Dash |
| `Ctrl+2` | Navigate to Legion |
| `Ctrl+3` | Navigate to Matrix |
| `Ctrl+4` | Navigate to Sage |
| `Ctrl+5` | Navigate to Plugins |
| `Ctrl+R` | Refresh current page |
| `Ctrl+W` | Toggle watchface overlay |
| `Ctrl+M` | Open memory palace |
| `Ctrl+Q` | Quit application |

### During Video Playback (mpv)

| Key | Action |
|-----|--------|
| `Enter` or `n` | Accept — play next episode |
| `Esc` or `x` | Dismiss the play-next prompt |

---

## File Structure

```
Great-Sage/
├── great_sage_gui.py       # Main entry point, shell UI
├── great_sage_core.py      # Backend logic, workers, data helpers
│
├── legion.py               # Novel scraping engine
├── gs_legion_ui.py         # Legion UI components
│
├── matrix.py               # Media tracking & mpv integration
├── gs_matrix_ui.py         # Matrix UI components
│
├── sage.py                 # AI recommendation engine
├── gs_sage_ui.py           # Sage chat interface
├── sage_memory_db.py       # Sage conversation persistence
│
├── artemis.py              # Rich-text editor
├── catalogue.py            # Chapter-anchored notes
├── plugin_manager.py       # Plugin system core
│
├── gs_theme.py             # Ink & Amber design tokens
├── gs_widgets.py           # Shared UI components
├── gs_logger.py            # Logging system
│
├── subtitle_manager.py     # Subtitle download/sync
├── source_plugin_base.py   # Base class for source plugins
├── next_episode.lua        # mpv script for play-next overlay
│
├── setup.sh                # Installation script
├── sources/                # Source plugins
│   ├── royalroad_plugin.py
│   └── novelbin_plugin.py
├── plugins/                # User plugins
│   ├── hello_sage.py       # Example plugin
│   ├── ambient.py
│   ├── book_covers.py
│   └── ...
├── Catalogue/              # Book notes storage
├── logs/                   # Daily application logs
└── writer/                 # Artemis documents
```

---

## Plugin Development

### Minimal Plugin Structure

Create a file `~/Documents/great sage/plugins/my_plugin.py`:

```python
"""
my_plugin.py — My Great Sage Plugin
====================================
"""

PLUGIN_NAME        = "My Plugin"
PLUGIN_ICON        = "✨"
PLUGIN_DESCRIPTION = "Does something cool"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Your Name"
PLUGIN_COLOR       = "#4FC4A0"

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel


def build_page(parent, api):
    """Build and return the plugin's page widget."""
    w = QWidget(parent)
    w.setStyleSheet(f"background:{api.colours['BG']};")
    
    layout = QVBoxLayout(w)
    layout.setContentsMargins(40, 32, 40, 32)
    
    # Access Legion data
    legion_data = api.legion_data()
    books = legion_data.get("books", {})
    
    # Access Matrix data
    matrix_data = api.matrix_data()
    watching = matrix_data.get("watching", {})
    
    # Create UI using helper
    title = api.make_label("My Plugin", api.colours["ACCENT"], 18, bold=True)
    layout.addWidget(title)
    
    stats = api.make_label(
        f"📚 {len(books)} books  ·  📺 {len(watching)} shows",
        api.colours["TEXT"], 13
    )
    layout.addWidget(stats)
    
    layout.addStretch()
    return w


def refresh(page):
    """Called when user navigates to this plugin."""
    pass
```

### Plugin API

The `api` object passed to `build_page` provides:

```python
api.legion_data()           # → dict: Full Legion library data
api.matrix_data()           # → dict: Full Matrix watchlist data
api.bookmarks_data()        # → dict: User bookmarks

api.colours                 # → dict: All theme colors
api.make_label(text, color, size, bold=False)  # → QLabel
api.make_button(text, color, size)             # → QPushButton

# Storage (isolated per plugin)
api.store.get(key, default)      # Get value
api.store.set(key, value)        # Set value
api.store.delete(key)            # Delete value
```

### Storage Location

Each plugin gets isolated storage at:
```
~/.config/great_sage/plugins/<plugin_name>.json
```

---

## Architecture

### Design System: Ink & Amber

A dark, warm aesthetic designed for long reading/watching sessions:

| Token | Value | Usage |
|-------|-------|-------|
| `BG` | `#0C0C0E` | Main background |
| `BG2` | `#111116` | Elevated surfaces |
| `BG3` | `#17171D` | Input fields |
| `PANEL` | `#1C1C24` | Cards, panels |
| `BORDER` | `#252530` | Dividers |
| `ACCENT` | `#C9A84C` | Primary gold |
| `ACCENT2` | `#4EC9A4` | Secondary teal |
| `TEXT` | `#E8E4DC` | Primary text |
| `TEXT2` | `#A0A0B4` | Secondary text |
| `MUTED` | `#606070` | Disabled text |

**Typography:**
- **Body**: Palatino Linotype, Palatino, Book Antiqua, Georgia (serif)
- **UI**: JetBrains Mono, Fira Code, Consolas (monospace)
- **Display**: Palatino Linotype family

### Data Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Sources    │────▶│   Legion     │────▶│   Sage AI    │
│  (Plugins)   │     │  (Storage)   │     │  (Groq API)  │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
┌──────────────┐     ┌──────────────┐            │
│   Torrents   │────▶│   Matrix     │────────────┘
│  (Search)    │     │  (Watchlist) │     Taste Profile
└──────────────┘     └──────────────┘
```

### Workers

- **AutoSyncWorker**: Background thread checking for new chapters
- **SageWorker**: AI request handling with rate limiting
- **MobileServer**: Optional Flask server for mobile companion

---

## Contributing

Contributions are welcome! Areas that need help:

- **Source plugins**: Add support for more novel sites
- **Themes**: New color schemes beyond Ink & Amber
- **Plugins**: Creative add-ons for the plugin system
- **Documentation**: Better guides and examples
- **Tests**: Expand test coverage

### Development Setup

```bash
# Fork and clone
git clone https://github.com/yourusername/Great-Sage.git
cd Great-Sage

# Install dev dependencies
pip install -r requirements-dev.txt  # if available

# Run tests
python -m pytest tests/
```

---

## License

MIT License — see LICENSE file for details.

---

## Acknowledgments

- **Groq** for fast AI inference
- **PyQt6** for the native UI framework
- **mpv** for the excellent media player
- **Rich** for beautiful terminal output

---

<p align="center">
  <strong>Built for readers, watchers, and thinkers.</strong><br>
  <em>Great Sage — Your media, unified.</em>
</p>
