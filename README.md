# Great Sage

A unified desktop media hub for Linux. Read web novels, track anime and shows, and chat with an AI companion that knows exactly what you're consuming — all in one dark, distraction-free app.

Built with PyQt6 and the **Ink & Amber** design system.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![PyQt6](https://img.shields.io/badge/UI-PyQt6-green) ![License](https://img.shields.io/badge/License-MIT-yellow) ![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey)

---

## Overview

Great Sage replaces the usual pile of browser tabs, tracking spreadsheets, and disconnected note apps with a single native application. It has four core modules that share a unified data layer, so your AI companion can make recommendations that span both what you're reading and what you're watching.

---

## Modules

### Legion — Novel Reader

Full-featured web novel reader with automatic chapter scraping and offline support.

- **Source scraping** via extensible plugins — RoyalRoad, NovelBin, NovelFire, LightNovelPub, and more
- **Jump In** grid — visual bookshelf of your in-progress novels with cover art
- **Bookmarks** — Planning, Reading, Dropped, Completed lists
- **Discover** tab — browse and search live sources, or describe what you want to Groq AI
- **Offline reading** — download entire novels as `.txt` files for local reading
- **Auto-sync** — background worker checks for new chapters and downloads them
- **Progress tracking** — chapter position, words read, reading time, daily streaks
- **Reader settings** — adjustable font size, line height, padding, dark/sepia/white modes
- **Sage companion panel** — AI sidebar that reads along with you
- **Lens panel** — paste a scene description to visualize it with AI-generated images
- **Chapter notes** — per-chapter annotation via the Catalogue module

### Matrix — Media Manager

Track and watch your anime and shows with native mpv integration.

- **Watchlist** — Planning, Watching, Dropped, Completed states
- **Continue Watching** — resume exactly where you left off
- **Browse** tab — filesystem browser for your local video library
- **Stream** tab — watch online via an embedded browser with ad blocking
- **mpv integration** — native playback with automatic position saving
- **Next-episode flow** — Lua overlay in mpv prompting to continue to the next episode
- **Subtitle management** — search and download subtitles via OpenSubtitles
- **Metadata** — TMDB integration for posters, ratings, and episode info
- **Highlights** — track memorable moments across shows
- **Calendar** — weekly airing schedule view

### Sage — AI Companion

Powered by Groq Cloud. Sage has access to your full Legion and Matrix data and builds a unified taste profile.

- **Recommendations** — novels based on your watchlist, shows based on your reading
- **Mood modes** — Light/Fun, Intense/Deep, What's Next
- **Quick Pick** — instant single recommendation
- **Analyse tab** — chapter summaries, title breakdowns, watchlist prioritization
- **Chat** — free-form conversation about your media
- **DuckDuckGo fallback** — web search when Groq is unavailable
- **Template chips** — Who is, What is, Ask shortcuts in the reader companion

### Artemis — Rich-Text Editor

A standalone writing environment that fits the Ink & Amber aesthetic.

- Typography-focused: Palatino Linotype body, JetBrains Mono UI
- Find & Replace, word count, document management
- Distraction-free layout for long writing sessions
- Runs standalone or embedded in Great Sage

### Catalogue — Chapter Notes

Chapter-anchored note-taking for deep readers.

- Notes stored per book, per chapter
- Tags: Character, Plot, Power-up, World, Reaction, Quote
- Sidebar panel in the Legion reader

---

## Plugin System

Drop a `.py` file into `plugins/` and it loads automatically with access to your reading data, watching data, Sage AI, and isolated storage.

**Built-in plugins:**

| Plugin | Description |
|---|---|
| `ambient.py` | Ambient soundscape generator (Aurora, Nebula, Ripples, Orbs modes) |
| `book_covers.py` | Cover image fetching and caching |
| `card_styler.py` | Dashboard card visual customization |
| `clock_widget.py` | Dashboard clock widget |
| `now_playing.py` | MPRIS media status display |
| `now_playing_lyrics.py` | Synced lyrics display for current track |
| `visualizer.py` | Audio visualizer with multiple bar modes and palettes |

---

## Installation

**Requirements:** Linux (X11 or Wayland), Python 3.10+, mpv, Git

```bash
git clone https://github.com/scezian/Great-Sage.git
cd Great-Sage
bash setup.sh
source ~/.bashrc
open-great-sage
```

**Manual:**

```bash
pip install PyQt6 flask requests beautifulsoup4 groq python-dotenv yt-dlp rich
python3 great_sage_gui.py
```

---

## Configuration

**Groq API key** (required for Sage AI): sign up at [console.groq.com](https://console.groq.com), then paste your key in Settings inside the app. Stored locally, never leaves your machine.

**Optional `.env` in project root:**

```env
GROQ_API_KEY=your_key_here
```

**Data locations:**

```
~/.great_sage_legion.json       # Novel progress & bookmarks
~/.config/matrix/progress.json  # Show watchlist & progress
~/Documents/Great-Sage/
├── plugins/                    # Plugin files
├── Catalogue/                  # Chapter notes
├── library/                    # Downloaded novel text files
└── writer/                     # Artemis documents
```

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+1` … `Ctrl+6` | Navigate to Dash / Legion / Matrix / Sage / Editor / Plugins |
| `Ctrl+R` | Refresh current page |
| `Ctrl+W` | Toggle watchface overlay |
| `Ctrl+M` | Open Memory Palace |
| `Ctrl+Q` | Quit |

**In mpv (next-episode overlay):**

| Key | Action |
|---|---|
| `Enter` or `n` | Play next episode |
| `Esc` or `x` | Dismiss |

---

## File Structure

```
Great-Sage/
├── great_sage_gui.py       # Entry point and shell
├── great_sage_core.py      # Backend logic, workers, data helpers
├── gs_theme.py             # Ink & Amber design tokens
├── gs_widgets.py           # Shared UI components
├── gs_logger.py            # Structured logging
│
├── gs_legion_ui.py         # Legion UI
├── legion.py               # Novel scraping engine
│
├── gs_matrix_ui.py         # Matrix UI
├── matrix.py               # Media tracking and mpv integration
│
├── gs_sage_ui.py           # Sage UI
├── sage.py                 # AI recommendation engine
├── sage_memory_db.py       # Conversation persistence
│
├── artemis.py              # Rich-text editor
├── catalogue.py            # Chapter-anchored notes
├── plugin_manager.py       # Plugin system core
├── subtitle_manager.py     # Subtitle download and sync
├── source_plugin_base.py   # Base class for source plugins
├── next_episode.lua        # mpv play-next overlay script
│
├── sources/                # Scraper plugins
├── plugins/                # User and built-in plugins
└── setup.sh                # Installer
```

---

## Plugin Development

```python
# plugins/my_plugin.py
PLUGIN_NAME        = "My Plugin"
PLUGIN_ICON        = "✨"
PLUGIN_DESCRIPTION = "Does something cool"
PLUGIN_VERSION     = "1.0"
PLUGIN_AUTHOR      = "Your Name"
PLUGIN_COLOR       = "#4EC9A4"

from PyQt6.QtWidgets import QWidget, QVBoxLayout

def build_page(parent, api):
    w = QWidget(parent)
    layout = QVBoxLayout(w)

    books   = api.legion_data().get("books", {})
    watches = api.matrix_data().get("watching", {})

    layout.addWidget(api.make_label(f"{len(books)} books  ·  {len(watches)} shows",
                                    api.colours["TEXT"], 13))
    layout.addStretch()
    return w

def refresh(page):
    pass
```

**Plugin API:**

```python
api.legion_data()                             # Full Legion library
api.matrix_data()                             # Full Matrix watchlist
api.colours                                   # Theme colour dict
api.make_label(text, color, size, bold=False) # → QLabel
api.make_button(text, color, size)            # → QPushButton
api.store.get(key, default)                   # Isolated storage
api.store.set(key, value)
```

---

## Design System — Ink & Amber

| Token | Value | Usage |
|---|---|---|
| `BG` | `#0C0C0E` | Main background |
| `BG2` | `#111116` | Elevated surfaces |
| `BG3` | `#17171D` | Input fields |
| `ACCENT` | `#C9A84C` | Primary amber |
| `ACCENT2` | `#4EC9A4` | Secondary teal |
| `TEXT` | `#E8E4DC` | Primary text |
| `TEXT2` | `#A0A0B4` | Secondary text |
| `MUTED` | `#606070` | Labels, hints |

Body: **Palatino Linotype** — UI: **JetBrains Mono**

---

## Contributing

Areas that would benefit most from outside help:

- **Source plugins** — scrapers for additional novel sites
- **Themes** — alternative colour schemes beyond Ink & Amber  
- **Plugins** — creative additions to the plugin system
- **Tests** — expand coverage in `tests/`

```bash
git clone https://github.com/scezian/Great-Sage.git
cd Great-Sage
python -m pytest tests/
```

---

## Acknowledgements

[Groq](https://groq.com) · [PyQt6](https://riverbankcomputing.com/software/pyqt/) · [mpv](https://mpv.io) · [Rich](https://github.com/Textualize/rich)

---

*Great Sage — your media, unified.*
