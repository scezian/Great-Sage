# Great Sage

A personal media companion desktop app for Linux. Tracks your reading, manages your anime/show library, and gives you an AI assistant that knows your taste — all in one dark, minimal UI.

---

## What it does

**Legion — Novel Reader**
Read and download web novels directly from source URLs. Tracks your chapter progress, word count, and reading history across your whole library. Auto-syncs new chapters in the background when they're released.

**Matrix — Media Manager**
Tracks anime and shows you're watching, planning to watch, or have completed. Plays local video files via mpv with position resumption, seamless next-episode playback, and automatic progress saving.

**Sage — AI Companion**
Chat with an AI that has full context of what you're reading and watching. Ask for recommendations, get novel chapter summaries, rank your watchlist by fit, or just talk. Powered by Groq.

**Plugins**
Drop a `.py` file into the plugins folder to add your own features. Plugins get access to your reading/watching data, isolated storage, and a Sage API.

---

## Requirements

- Linux
- Python 3.10+
- mpv (for video playback)

---

## Setup

```bash
bash setup.sh
source ~/.bashrc
open-great-sage
```

The setup script installs Python dependencies, creates the app folders, and adds a shell alias.

**Python dependencies installed:** `PyQt6`, `flask`, `requests`, `beautifulsoup4`, `rich`, `yt-dlp`, `groq`

---

## File layout

```
great_sage_gui.py       — main app, run this
great_sage_core.py      — backend logic (workers, sync, data helpers)
legion.py               — novel scraping and download engine
matrix.py               — media tracking and mpv integration
sage.py                 — AI chat (Groq)
catalogue.py            — catalogue/browse panel
plugin_manager.py       — plugin system
subtitle_manager.py     — subtitle handling
gs_logger.py            — logging system
next_episode.lua        — mpv script for play-next overlay
setup.sh                — setup script
```

Data is stored in:
```
~/.great_sage_legion.json       — novel progress
~/.config/matrix/progress.json  — show/watchlist progress
~/Documents/great sage/plugins/ — plugin files
~/Documents/great sage/logs/    — daily log files
```

---

## Groq API key

Sage requires a [Groq](https://console.groq.com) API key. Set it in **Settings** inside the app — it's stored with your Matrix data and never leaves your machine.

---

## Plugins

Drop any `.py` file into `~/Documents/great sage/plugins/`. A plugin needs:

```python
PLUGIN_NAME        = "My Plugin"
PLUGIN_ICON        = "◉"
PLUGIN_DESCRIPTION = "Does something cool"

def build_page(parent, api):
    # return a QWidget
    ...

def refresh(page):
    # called on navigation
    ...
```

An example plugin (`hello_sage.py`) is written to the folder on first run.

---

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+1–5` | Navigate to Dash / Legion / Matrix / Sage / Plugins |
| `Ctrl+R` | Refresh current page |
| `Ctrl+W` | Toggle watchface overlay |
| `Ctrl+M` | Open memory palace |

**During video playback (mpv):**

| Key | Action |
|---|---|
| `Enter` or `n` | Accept — play next episode |
| `Esc` or `x` | Dismiss the play-next prompt |
