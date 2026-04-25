#!/usr/bin/env python3
try:
    import cloudscraper
    CLOUDSCRAPER = True
except ImportError:
    CLOUDSCRAPER = False

import base64
import json
import json as _json
import os
import queue
import re
import signal
import shutil
import subprocess
import sys
import textwrap
import time
import threading
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from source_plugin_base import SourcePlugin, ChapterResult

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log as _gs_log
    log = _gs_log.legion
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule
    from rich.table import Table
    from rich.theme import Theme
    from rich.style import Style
    from rich import box as rich_box
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    RICH = True
except ImportError:
    RICH = False

try:
    import readchar
    READCHAR = True
except ImportError:
    READCHAR = False

IS_KITTY = os.environ.get("TERM", "") == "xterm-kitty" or \
           os.environ.get("TERM_PROGRAM", "") == "vscode"

FONT_SIZE_DEFAULT = 84
LINE_SPACING_DEFAULT = 0

THEMES = {
    "dark": Theme({
        "primary":   Style(color="#E8DCC8", bold=True),
        "secondary": Style(color="#A09080"),
        "accent":    Style(color="#C8A96E", bold=True, blink=True),
        "faint":     Style(color="#5A5048"),
        "progress":  Style(color="#C8A96E", bold=True),
        "stat":      Style(color="#7EC8A0", bold=True, italic=True),
        "panel":     Style(color="#C8A96E"),
        "divider":   Style(color="#6A5A4A"),
        "glow":      Style(color="#FFD700", bold=True),
        "neon":      Style(color="#00FFFF", bold=True),
        "title":     Style(color="#F0D090", bold=True),
    }),
    "cyber": Theme({
        "primary":   Style(color="#FF1493", bold=True),
        "secondary": Style(color="#00CED1"),
        "accent":    Style(color="#FF69B4", bold=True, blink=True),
        "faint":     Style(color="#4682B4"),
        "progress":  Style(color="#FF69B4", bold=True),
        "stat":      Style(color="#00FA9A", bold=True, italic=True),
        "panel":     Style(color="#FF69B4"),
        "divider":   Style(color="#8B008B"),
        "glow":      Style(color="#FFD700", bold=True),
        "neon":      Style(color="#00FFFF", bold=True),
        "title":     Style(color="#F0D090", bold=True),
    }),
    "matrix": Theme({
        "primary":   Style(color="#00FF00", bold=True),
        "secondary": Style(color="#008000"),
        "accent":    Style(color="#00FF41", bold=True, blink=True),
        "faint":     Style(color="#003300"),
        "progress":  Style(color="#00FF41", bold=True),
        "stat":      Style(color="#00FF00", bold=True, italic=True),
        "panel":     Style(color="#00FF41"),
        "divider":   Style(color="#006400"),
        "glow":      Style(color="#ADFF2F", bold=True),
        "neon":      Style(color="#00FFFF", bold=True),
        "title":     Style(color="#F0D090", bold=True),
    }),
    "light": Theme({
        "primary":   Style(color="#2C2018"),
        "secondary": Style(color="#6A5A4A"),
        "accent":    Style(color="#8B5E1A", bold=True),
        "faint":     Style(color="#B0A090"),
        "progress":  Style(color="#8B5E1A"),
        "stat":      Style(color="#2A6E4A", bold=True),
        "panel":     Style(color="#8B5E1A"),
        "divider":   Style(color="#C0B0A0"),
        "title":     Style(color="#5A3A0A", bold=True),
    }),
    "neon": Theme({
        "primary":   Style(color="#FF00FF", bold=True, blink=True),
        "secondary": Style(color="#4B0082"),
        "accent":    Style(color="#00FFFF", bold=True, blink=True),
        "faint":     Style(color="#1A0033"),
        "progress":  Style(color="#00FFFF", bold=True),
        "stat":      Style(color="#FF00FF", bold=True, italic=True),
        "panel":     Style(color="#00FFFF"),
        "divider":   Style(color="#8B008B"),
        "glow":      Style(color="#FF1493", bold=True),
        "neon":      Style(color="#00FFFF", bold=True),
        "title":     Style(color="#FF00FF", bold=True, underline=True),
    }),
}

PROGRESS_FILE = os.path.expanduser("~/.great_sage_legion.json")
BACKUP_FILE      = os.path.expanduser("~/.great_sage_legion.backup.json")
BOOKMARKS_FILE   = os.path.expanduser("~/.great_sage_bookmarks.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SCRAPER = cloudscraper.create_scraper() if CLOUDSCRAPER else None

class SourcePluginRegistry:
    def __init__(self):
        self._plugins: list[SourcePlugin] = []

    def register(self, plugin: SourcePlugin):
        self._plugins.append(plugin)

    def for_url(self, url: str) -> SourcePlugin | None:
        for p in self._plugins:
            if p.can_handle(url):
                return p
        return None

    def all_plugins(self) -> list[SourcePlugin]:
        return list(self._plugins)

    def load_user_plugins(self, directory: str):
        """Load .py files from a directory as plugins. Each must define
        a module-level `plugin` variable that is a SourcePlugin instance."""
        import importlib.util, pathlib
        for f in pathlib.Path(directory).glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(f.stem, f)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "plugin") and isinstance(mod.plugin, SourcePlugin):
                    self.register(mod.plugin)
            except Exception as e:
                log.error("Failed to load user plugin", file=str(f), error=str(e))

plugin_registry = SourcePluginRegistry()

# Add project dir to sys.path to ensure sources can be imported
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Built-in plugins
try:
    from sources.novelbin_plugin import plugin as novelbin_plugin
    from sources.royalroad_plugin import plugin as royalroad_plugin
    plugin_registry.register(novelbin_plugin)
    plugin_registry.register(royalroad_plugin)
except ImportError as e:
    log.error("Failed to load built-in plugins", error=str(e))

# User plugins from ~/.config/great-sage/sources/
_user_plugin_dir = os.path.expanduser("~/.config/great-sage/sources")
os.makedirs(_user_plugin_dir, exist_ok=True)
plugin_registry.load_user_plugins(_user_plugin_dir)

DIVIDERS = ["　❦　", "　✦　", "　⁂　", "　❧　", "　◈　", "　✧　", "　⟡　"]

current_theme = "dark"
console = None


def make_console():
    global console
    if RICH:
        console = Console(theme=THEMES[current_theme])

make_console()


def display_kitty_image(img_bytes: bytes, max_cols: int = 18, image_id: int = 1):
    if not IS_KITTY:
        return
    encoded = base64.standard_b64encode(img_bytes).decode()
    chunks = [encoded[i:i+4096] for i in range(0, len(encoded), 4096)]
    for i, chunk in enumerate(chunks):
        m = 0 if (i == len(chunks) - 1) else 1
        ctrl = f"a=T,f=100,i={image_id},c={max_cols},r=8,m={m}" if i == 0 else f"m={m},i={image_id}"
        sys.stdout.write(f"\x1b_G{ctrl};{chunk}\x1b\\")
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()


def fetch_cover_image(book_url: str):
    try:
        parsed = urlparse(book_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            return None
        index_url = f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:2])}"
        resp = (SCRAPER or SESSION).get(index_url, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        img_tag = None
        for sel in [{"class": "book-cover"}, {"class": "cover"}, {"class": "novel-cover"}]:
            img_tag = soup.find("img", sel)
            if img_tag:
                break
        if not img_tag:
            header = soup.find("div", class_=re.compile(r"header|info|detail", re.I))
            if header:
                img_tag = header.find("img")
        if not img_tag or not img_tag.get("src"):
            return None
        src = img_tag["src"]
        if not src.startswith("http"):
            src = f"{parsed.scheme}://{parsed.netloc}{src}"
        img_resp = (SCRAPER or SESSION).get(src, timeout=10)
        return img_resp.content if img_resp.status_code == 200 else None
    except Exception:
        return None


def _convert_to_png(img_bytes: bytes) -> bytes:
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return img_bytes


def safe_print(message: str, style: str = ""):
    if RICH and console:
        if style:
            clean_style = style.strip('[]')
            console.print(message, style=clean_style)
        else:
            console.print(message)
    else:
        clean = re.sub(r'\[.*?\]', '', message)
        print(clean)


def confirm_action(message: str) -> bool:
    try:
        response = input(f"  {message} (y/n): ").strip().lower()
        return response in ('y', 'yes')
    except KeyboardInterrupt:
        return False


def load_progress() -> dict:
    for path in [PROGRESS_FILE, BACKUP_FILE]:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    progress = json.load(f)
                    if "font_size" not in progress:
                        progress["font_size"] = FONT_SIZE_DEFAULT
                    if "line_spacing" not in progress:
                        progress["line_spacing"] = LINE_SPACING_DEFAULT
                    books = progress.get("books", {})
                    for book in books.values():
                        if "metadata" not in book:
                            book["metadata"] = {}
                        if "total_chapters" not in book:
                            book["total_chapters"] = None
                        if "download_state" not in book:
                            book["download_state"] = {
                                "status": "idle",
                                "last_downloaded_chapter": None,
                                "last_downloaded_chapter_num": 0,
                                "total_chapters_downloaded": 0,
                                "download_path": None,
                                "failed_chapters": [],
                                "timestamp": None,
                                "pause_requested": False
                            }
                        elif book.get("download_state", {}).get("status") == "downloading":
                            # Crash recovery: if download was interrupted (process killed),
                            # mark as paused so user can resume. We know it's a crash if
                            # the last chapter save was more than 60 seconds ago.
                            # If < 60s, the download thread is still alive and saving regularly.
                            ts = book.get("download_state", {}).get("timestamp") or 0
                            if (time.time() - ts) > 60:
                                book["download_state"]["status"] = "paused"
                                book["download_state"]["pause_requested"] = False
                    return progress
            except (json.JSONDecodeError, IOError) as e:
                log.warning("Failed to load progress file", path=path, error=str(e))
                continue
    log.warning("No valid progress file found, returning defaults")
    return {"books": {}, "theme": "dark", "font_size": FONT_SIZE_DEFAULT, "line_spacing": LINE_SPACING_DEFAULT}


def save_progress(data: dict):
    # Merge with what's on disk: only update books we know about,
    # never remove books that were added by the GUI between our reads.
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE) as _f:
                _current = json.load(_f)
            # Start from disk state, then overlay our updates on top
            merged_books = _current.get("books", {}).copy()
            for k, v in data.get("books", {}).items():
                if k in merged_books:
                    # Only update books already on disk (don't add, don't remove)
                    merged_books[k] = v
            data["books"] = merged_books
    except Exception as e:
        log.warning("Failed to merge progress with disk state", error=str(e))
        pass
    tmp = PROGRESS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(PROGRESS_FILE):
            shutil.copy2(PROGRESS_FILE, BACKUP_FILE)
        os.replace(tmp, PROGRESS_FILE)
    except Exception as e:
        log.error("Failed to save progress (atomic write failed), trying direct write", error=str(e))
        try:
            with open(PROGRESS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e2:
            log.error("Failed to save progress (direct write also failed)", error=str(e2))


# ── Bookmarks storage ──────────────────────────────────────────────────────────

def load_bookmarks() -> dict:
    """Load bookmarks from file. Returns dict with planning/reading/dropped/completed lists."""
    try:
        if os.path.exists(BOOKMARKS_FILE):
            with open(BOOKMARKS_FILE, "r") as f:
                data = json.load(f)
                for key in ("planning", "reading", "dropped", "completed"):
                    if key not in data:
                        data[key] = []
                return data
    except Exception as e:
        log.error("Failed to load bookmarks", path=BOOKMARKS_FILE, error=str(e))
    return {"planning": [], "reading": [], "dropped": [], "completed": []}


def save_bookmarks(data: dict):
    """Save bookmarks to file atomically."""
    tmp = BOOKMARKS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, BOOKMARKS_FILE)
    except Exception as e:
        log.error("Failed to save bookmarks (atomic write failed), trying direct write", error=str(e))
        try:
            with open(BOOKMARKS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e2:
            log.error("Failed to save bookmarks (direct write also failed)", error=str(e2))


def all_bookmarked_titles() -> set:
    """Return a set of all titles across all bookmark lists (lowercase, for filtering)."""
    data = load_bookmarks()
    titles = set()
    for lst in data.values():
        for entry in lst:
            t = entry.get("title", "") if isinstance(entry, dict) else str(entry)
            if t:
                titles.add(t.lower())
    return titles


def sync_reading_from_jumpin(progress: dict):
    """
    Keep the Reading list in sync with Jump In books.
    Books in progress["books"] are always in Reading — add any that are missing.
    Does NOT remove books from Reading if they are deleted from Jump In
    (user may want to keep them as a record).
    """
    bm = load_bookmarks()
    reading_urls = {e.get("url", "") for e in bm["reading"]}
    reading_titles = {e.get("title", "").lower() for e in bm["reading"]}

    changed = False
    for name, book in progress.get("books", {}).items():
        url = book.get("current_url", "")
        if name.lower() not in reading_titles and url not in reading_urls:
            bm["reading"].append({
                "title":    name,
                "url":      url,
                "metadata": book.get("metadata", {}),
                "added":    time.time(),
            })
            changed = True

    if changed:
        save_bookmarks(bm)


def add_to_bookmarks(title: str, url: str, list_name: str, metadata: dict = None) -> str:
    """
    Add a book to a bookmark list. Moves it from other lists if found there.
    Returns 'added', 'duplicate', or 'error'.
    list_name: 'planning' | 'reading' | 'dropped' | 'completed'
    """
    bm = load_bookmarks()

    # Check if already in the target list
    for entry in bm.get(list_name, []):
        if entry.get("title", "").lower() == title.lower():
            return "duplicate"

    # Remove from other lists (book moves between lists)
    for lst_key in ("planning", "reading", "dropped", "completed"):
        if lst_key == list_name:
            continue
        bm[lst_key] = [e for e in bm[lst_key]
                       if e.get("title", "").lower() != title.lower()]

    bm[list_name].append({
        "title":    title,
        "url":      url,
        "metadata": metadata or {},
        "added":    time.time(),
    })

    try:
        save_bookmarks(bm)
        return "added"
    except Exception:
        return "error"


def remove_from_bookmarks(title: str) -> bool:
    """Remove a book from all bookmark lists."""
    bm = load_bookmarks()
    changed = False
    for lst_key in ("planning", "reading", "dropped", "completed"):
        before = len(bm[lst_key])
        bm[lst_key] = [e for e in bm[lst_key]
                       if e.get("title", "").lower() != title.lower()]
        if len(bm[lst_key]) < before:
            changed = True
    if changed:
        save_bookmarks(bm)
    return changed


def _show_bookmark_entry(entry: dict, progress: dict, from_list: str):
    """
    Show details for a single bookmarked book and offer actions.
    Returns: 'read' (and adds to Jump In + Reading), 'back', or 'removed'
    """
    title    = entry.get("title", "Unknown")
    url      = entry.get("url", "")
    metadata = entry.get("metadata", {})

    # Try to fetch metadata if we don't have it yet
    if not metadata and url:
        if RICH and console:
            console.print("[secondary]  Fetching metadata...[/secondary]")
        metadata = fetch_book_metadata(url)
        if metadata:
            entry["metadata"] = metadata
            # Save updated metadata back to bookmarks
            bm = load_bookmarks()
            for e in bm.get(from_list, []):
                if e.get("title", "").lower() == title.lower():
                    e["metadata"] = metadata
                    break
            save_bookmarks(bm)

    while True:
        os.system("cls" if os.name == "nt" else "clear")

        if RICH and console:
            title_width = min(get_terminal_size()[0] - 4, 80)
            console.print()
            console.print(f"[title]{' ' * max(0,(title_width-len(title))//2)}{title}[/title]")
            if metadata:
                console.print()
                for label, key in [("Author", "author"), ("Genres", "genres"),
                                   ("Status", "status"), ("Year", "year")]:
                    if metadata.get(key):
                        console.print(f"  [secondary]{label}:[/secondary] {metadata[key]}")
                if metadata.get("synopsis"):
                    syn = metadata["synopsis"]
                    if len(syn) > 800:
                        syn = syn[:800] + "..."
                    console.print(f"\n[secondary]Synopsis:[/secondary]\n  {syn}")
            console.print()
            if from_list != "dropped":
                console.print("[secondary]  [1] Read  ·  [2] Move to list  ·  [3] Remove  ·  [q] Back[/secondary]")
            else:
                console.print("[secondary]  [2] Move to list  ·  [3] Remove  ·  [q] Back[/secondary]")
        else:
            print(f"\n=== {title} ===")
            if metadata:
                for label, key in [("Author", "author"), ("Genres", "genres"),
                                   ("Status", "status"), ("Year", "year")]:
                    if metadata.get(key):
                        print(f"{label}: {metadata[key]}")
                if metadata.get("synopsis"):
                    print(f"\nSynopsis:\n{metadata['synopsis'][:800]}")
            print()
            if from_list != "dropped":
                print("  1. Read  ·  2. Move to list  ·  3. Remove  ·  q. Back")
            else:
                print("  2. Move to list  ·  3. Remove  ·  q. Back")

        if READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  Choice: ").strip().lower()

        if key == "q":
            return "back"

        elif key == "1" and from_list != "dropped":
            # Start reading — add to Jump In if not already there
            if title not in progress.get("books", {}):
                meta = metadata or {}
                entry_data = {
                    "current_url": url, "next_url": None, "last_title": "Not started",
                    "new_chapters_waiting": 0, "chapters_read": 0,
                    "words_read": 0, "minutes_read": 0,
                    "book_title": title, "metadata": meta,
                    "download_state": {
                        "status": "idle", "last_downloaded_chapter": None,
                        "last_downloaded_chapter_num": 0,
                        "total_chapters_downloaded": 0,
                        "download_path": None, "failed_chapters": [],
                        "timestamp": None, "pause_requested": False
                    }
                }
                progress["books"][title] = entry_data
                save_progress(progress)

            # Move to Reading list in bookmarks
            add_to_bookmarks(title, url, "reading", metadata)
            return "read"

        elif key == "2":
            # Move to a different list
            if RICH and console:
                console.print("\n  Move to which list?")
                for i, lst in enumerate(["planning", "reading", "dropped", "completed"], 1):
                    if lst != from_list:
                        console.print(f"  [{i}] {lst.capitalize()}")
                console.print("  [0] Cancel")
            else:
                print("\n  Move to which list?")
                for i, lst in enumerate(["planning", "reading", "dropped", "completed"], 1):
                    if lst != from_list:
                        print(f"  {i}. {lst.capitalize()}")
                print("  0. Cancel")

            lists = ["planning", "reading", "dropped", "completed"]
            try:
                if READCHAR:
                    mv = readchar.readkey()
                    mv = mv.lower() if isinstance(mv, str) else mv
                else:
                    mv = input("  Choice: ").strip()
                mv_idx = int(mv) - 1
                if 0 <= mv_idx < len(lists):
                    target = lists[mv_idx]
                    add_to_bookmarks(title, url, target, metadata)
                    if RICH and console:
                        console.print(f"  [stat]Moved to {target.capitalize()}.[/stat]")
                    else:
                        print(f"  Moved to {target.capitalize()}.")
                    time.sleep(1)
                    return "back"
            except (ValueError, TypeError):
                pass

        elif key == "3":
            remove_from_bookmarks(title)
            if RICH and console:
                console.print(f"  [secondary]Removed '{title}' from bookmarks.[/secondary]")
            else:
                print(f"  Removed '{title}' from bookmarks.")
            time.sleep(1)
            return "removed"


def show_bookmark_list(list_name: str, progress: dict):
    """Show a specific bookmark list (planning/reading/dropped/completed)."""
    while True:
        bm = load_bookmarks()
        entries = bm.get(list_name, [])

        os.system("cls" if os.name == "nt" else "clear")

        label = list_name.capitalize()
        if RICH and console:
            console.print()
            console.print(Panel(f"[title]  Bookmarks — {label}[/title]",
                               border_style="panel",
                               box=rich_box.HEAVY if IS_KITTY else rich_box.ROUNDED,
                               width=60))
            console.print()
            if not entries:
                console.print(f"[secondary]  No books in {label} yet.[/secondary]\n")
            else:
                for i, e in enumerate(entries, 1):
                    console.print(f"  [secondary]{i}[/secondary]  [primary]{e.get('title','?')}[/primary]")
            console.print()
            if list_name != "reading":
                console.print("[secondary]  [#] Open  ·  [a] Add  ·  [q] Back[/secondary]\n")
            else:
                console.print("[secondary]  [#] Open  ·  [q] Back[/secondary]\n")
        else:
            print(f"\n=== Bookmarks — {label} ===\n")
            if not entries:
                print(f"  No books in {label} yet.\n")
            else:
                for i, e in enumerate(entries, 1):
                    print(f"  {i}. {e.get('title','?')}")
            print()
            if list_name != "reading":
                print("  [#] Open  ·  [a] Add  ·  [q] Back\n")
            else:
                print("  [#] Open  ·  [q] Back\n")

        if READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  Choice: ").strip().lower()

        if key == "q":
            return

        elif key == "a" and list_name != "reading":
            url = input("  Chapter URL: ").strip()
            if not url or url.lower() == "q":
                continue
            if RICH and console:
                console.print("[secondary]  Fetching title and metadata...[/secondary]")
            else:
                print("  Fetching title and metadata...")
            name = extract_book_title_from_chapter(url)
            meta = fetch_book_metadata(url)
            # If we got a title from metadata that's better, prefer it
            if not name and meta.get("title"):
                name = meta["title"]
            if name:
                if RICH and console:
                    console.print(f"  Found: [primary]{name}[/primary]")
                else:
                    print(f"  Found: {name}")
                override = input("  Press Enter to accept, or type a different title: ").strip()
                if override:
                    name = override
            else:
                name = input("  Could not detect title. Enter manually: ").strip()
            if not name:
                continue
            result = add_to_bookmarks(name, url, list_name, meta)
            if result == "added":
                safe_print(f"  ✅ Added '{name}' to {label}.", "green")
            elif result == "duplicate":
                safe_print(f"  '{name}' is already in your bookmarks.", "yellow")
            time.sleep(1)

        elif key.isdigit():
            idx = int(key) - 1
            # Handle multi-digit input
            if len(entries) > 9:
                rest = input(f"  (started with {key}): ").strip()
                if rest.isdigit():
                    key = key + rest
                    idx = int(key) - 1
            if 0 <= idx < len(entries):
                result = _show_bookmark_entry(entries[idx], progress, list_name)
                if result == "read":
                    # Jump straight to reading — return the book name and url
                    entry = entries[idx]
                    return ("read", entry.get("title"), entry.get("url"))


def show_bookmarks_menu(progress: dict):
    """Top-level bookmarks menu: Planning / Reading / Dropped / Completed."""
    lists = [
        ("planning",  "Planning"),
        ("reading",   "Reading"),
        ("dropped",   "Dropped"),
        ("completed", "Completed"),
    ]

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        bm = load_bookmarks()

        if RICH and console:
            console.print()
            console.print(Panel("[title]  📚  Bookmarks[/title]",
                               border_style="panel",
                               box=rich_box.HEAVY if IS_KITTY else rich_box.ROUNDED,
                               width=60))
            console.print()
            for i, (key, label) in enumerate(lists, 1):
                count = len(bm.get(key, []))
                console.print(f"  [secondary]{i}[/secondary]  [primary]{label}[/primary]  [faint]({count})[/faint]")
            console.print()
            console.print("[secondary]  [#] Open list  ·  [q] Back[/secondary]\n")
        else:
            print("\n=== Bookmarks ===\n")
            for i, (key, label) in enumerate(lists, 1):
                count = len(bm.get(key, []))
                print(f"  {i}. {label}  ({count})")
            print("\n  [#] Open list  ·  [q] Back\n")

        if READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  Choice: ").strip().lower()

        if key == "q":
            return None, None

        elif key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(lists):
                list_key, list_label = lists[idx]
                result = show_bookmark_list(list_key, progress)
                # If user picked "read" from inside a list, bubble up
                if isinstance(result, tuple) and result[0] == "read":
                    return result[1], result[2]  # title, url


import random
import time
import requests
from urllib.parse import urlparse, urlunparse

# --- User-Agent Rotation ---
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# --- Mirror Rotation ---
# Default mirror domains for generic scraping. These might be dynamically updated later by plugins.
_DEFAULT_MIRROR_DOMAINS = ["novelbin.net", "novelfull.com", "novelhall.com", "boxnovel.com"]
_MIRROR_FAILURES = {} # {domain: last_failed_timestamp} for tracking recently failed mirrors
_MIRROR_FAIL_DURATION = 300 # 5 minutes

def _generic_build_mirror_urls(url: str) -> list[str]:
    """
    Generates a list of potential mirror URLs for the given URL based on known domains.
    Prioritizes the original URL, then tries known mirror domains.
    Filters out recently failed mirrors.
    """
    parsed_url = urlparse(url)
    original_domain = parsed_url.netloc
    
    # Start with the original URL
    mirror_urls = [url]
    
    # Only add mirrors whose domain is related to the original.
    # Matching on a shared keyword (e.g. "novelbin") prevents unrelated sites
    # (novelfull, novelhall) being tried for sources like novelfire or boxnovel.
    def _domains_related(orig, candidate):
        keywords = ["novelbin", "novelfull", "novelhall", "boxnovel", "novelfire",
                    "royalroad", "wuxia", "lightnovel"]
        for kw in keywords:
            if kw in orig and kw in candidate:
                return True
        return False

    for domain in _DEFAULT_MIRROR_DOMAINS:
        if domain != original_domain and _domains_related(original_domain, domain):
            mirror_url = urlunparse(parsed_url._replace(netloc=domain))
            if mirror_url not in mirror_urls:
                mirror_urls.append(mirror_url)
                
    # Filter out recently failed mirrors and prepare for randomized selection
    now = time.time()
    available_mirrors = []
    
    # Add original URL if not marked as failed
    if original_domain not in _MIRROR_FAILURES or (now - _MIRROR_FAILURES[original_domain] > _MIRROR_FAIL_DURATION):
        available_mirrors.append(url)
    else:
        log.debug(f"Skipping original domain {original_domain} due to recent failure.")

    # Add other mirror URLs if not marked as failed
    for m_url in mirror_urls:
        if m_url == url: continue # Already handled original URL
        m_domain = urlparse(m_url).netloc
        if m_domain not in _MIRROR_FAILURES or (now - _MIRROR_FAILURES[m_domain] > _MIRROR_FAIL_DURATION):
            if m_url not in available_mirrors: # Avoid duplicates
                available_mirrors.append(m_url)
        else:
            log.debug(f"Skipping recently failed mirror: {m_domain} for {url}")

    if not available_mirrors: # If all mirrors are down or filtered, try original anyway as a last resort
        log.warning(f"All preferred and mirror URLs for {url} are currently marked as failed or unavailable. Re-attempting original URL.")
        available_mirrors.append(url)
    
    random.shuffle(available_mirrors) # Shuffle to distribute load and vary retry order
    
    return available_mirrors


def _warm_session(base_url: str):
    try:
        (SCRAPER or SESSION).get(base_url, timeout=10)
        time.sleep(0.5)
    except Exception:
        pass


def _generic_get_with_retry(url: str) -> tuple[requests.Response | None, str]:
    """
    Attempts to fetch a URL with retries, exponential backoff, jitter,
    User-Agent rotation, and mirror rotation.
    Returns (response, actual_url) on success, or (None, original_url) on final failure.
    """
    last_error_message = "Unknown error"
    retries = 0
    max_retries = 4 # Total 4 attempts (0, 1, 2, 3)
    original_url = url # Store original URL for final failure return

    while retries < max_retries:
        current_attempt_urls = _generic_build_mirror_urls(original_url)
        current_attempt_url = current_attempt_urls[0] # Take the first available after shuffling and filtering

        # 3. User-Agent Rotation
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": random.choice(["en-US,en;q=0.9", "es-ES,es;q=0.8", "fr-FR,fr;q=0.7", "en;q=0.9"]), # Rotate occasionally
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        }

        log.debug(f"Attempt {retries + 1}/{max_retries} for URL: {current_attempt_url} (original: {original_url})")
        log.debug(f"  Using User-Agent: {headers['User-Agent']}")

        try:
            # Existing _warm_session logic
            parsed = urlparse(current_attempt_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            _warm_session(base)
            time.sleep(0.8) # Initial small delay before request

            resp = (SCRAPER or SESSION).get(current_attempt_url, timeout=15, headers=headers)
            
            # 1. Retry logic - Specific Status codes
            retry_status_codes = [429, 502, 503, 504, 408]
            if resp.status_code in retry_status_codes:
                last_error_message = f"HTTP {resp.status_code} - Server/Rate Limit Error"
                log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
                _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark current mirror as failed
                
                retries += 1
                if retries >= max_retries: break # Exit if max retries reached

                # 2. Exponential backoff with jitter
                base_wait = 2 ** retries
                jitter = random.uniform(0, base_wait * 0.3)
                actual_wait = base_wait + jitter
                log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
                time.sleep(actual_wait)
                continue # Go to next retry attempt
            
            # Keep existing cloudscraper fallback for 403
            if resp.status_code == 403:
                log.warning(f"403 Forbidden for {current_attempt_url}. Attempting Cloudflare bypass.")
                _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as potentially problematic
                cloudscraper_success = False
                if CLOUDSCRAPER and not SCRAPER:
                    try:
                        cs = cloudscraper.create_scraper()
                        resp2 = cs.get(current_attempt_url, timeout=20, headers=headers)
                        if resp2.status_code == 200:
                            log.debug(f"Cloudflare bypass successful for {current_attempt_url}.")
                            return resp2, current_attempt_url
                        last_error_message = f"403 Forbidden - Cloudflare bypass failed (status: {resp2.status_code})"
                    except Exception as cs_e:
                        last_error_message = f"403 Forbidden - Cloudflare bypass failed ({type(cs_e).__name__}: {str(cs_e)})"
                elif SCRAPER:
                    try:
                        time.sleep(2) # Give Cloudscraper time if it's already active
                        resp2 = SCRAPER.get(current_attempt_url, timeout=30, headers=headers)
                        if resp2.status_code == 200:
                            log.debug(f"Cloudscraper re-attempt successful for {current_attempt_url}.")
                            return resp2, current_attempt_url
                        last_error_message = f"403 Forbidden - SCRAPER re-attempt failed (status: {resp2.status_code})"
                    except Exception as scr_e:
                        last_error_message = f"403 Forbidden - SCRAPER re-attempt failed ({type(scr_e).__name__}: {str(scr_e)})"
                
                # If Cloudflare bypass/re-attempt failed, treat as retryable error
                log.warning(f"{last_error_message}. Retrying...")
                
                retries += 1
                if retries >= max_retries: break # Exit if max retries reached

                base_wait = 2 ** retries
                jitter = random.uniform(0, base_wait * 0.3)
                actual_wait = base_wait + jitter
                log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
                time.sleep(actual_wait)
                continue # Go to next retry attempt
            
            resp.raise_for_status() # Raises HTTPError for other bad responses (4xx or 5xx)
            log.info(f"Successfully fetched {current_attempt_url}")
            return resp, current_attempt_url

        # 1. Retry logic - Requests exceptions
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error_message = f"Network error ({type(e).__name__}): {str(e)}"
            log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            # 2. Exponential backoff with jitter
            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
        except requests.exceptions.RequestException as e: # Catch other request-related errors (e.g., HTTPError from raise_for_status for non-retryable codes)
            last_error_message = f"Request failed ({type(e).__name__}): {str(e)}"
            log.warning(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
        except Exception as e: # Catch any other unexpected errors
            last_error_message = f"Unexpected error ({type(e).__name__}): {str(e)}"
            log.error(f"{last_error_message} for {current_attempt_url}. Retrying...")
            _MIRROR_FAILURES[urlparse(current_attempt_url).netloc] = time.time() # Mark domain as failed
            
            retries += 1
            if retries >= max_retries: break # Exit if max retries reached

            base_wait = 2 ** retries
            jitter = random.uniform(0, base_wait * 0.3)
            actual_wait = base_wait + jitter
            log.debug(f"Waiting for {actual_wait:.2f}s before next retry.")
            time.sleep(actual_wait)
            continue # Go to next retry attempt
    
    log.error(f"Final failure for {original_url} after {retries} retries. Last error: {last_error_message}")
    return None, original_url # Return None and original URL on final failure


def _fetch_chapter_generic(url: str):
    # Hardcoded mirrors for the generic fallback (original NovelBin mirrors)
    GENERIC_MIRRORS = ["novelbin.com"]
    GENERIC_WATERMARKS = [
        r"(?i)visit\s+\S+\s+for\s+(more|latest|updates?).*",
        r"(?i)read\s+(at|on)\s+\S+\s+for.*",
        r"(?i)support\s+the\s+(author|translator)\s+at\s+\S+.*",
        r"(?i)this\s+chapter\s+(is|was)\s+(stolen|taken)\s+from.*",
        r"(?i)find\s+(this|more)\s+(story|chapter|content)\s+(at|on)\s+\S+.*",
        r"(?i)novelbin\.(me|com|net)\S*",
        r"(?i)\[.*?novelbin.*?\]",
        r"(?i)^total\s+responses?\s*:\s*\d+$",
        r"(?i)^responses?\s*:\s*\d+$",
        r"(?i)^\d+\s+comments?$",
        r"(?i)^(load|show)\s+more\s+comments?.*",
        r"(?i)^leave\s+a\s+(reply|comment).*",
        r"(?i)^(sponsored|advertisement|advert)\b.*",
        r"(?i)^your\s+email\s+address\s+will\s+not\s+be\s+published.*",
    ]

    def _local_build_mirrors(u):
        parsed = urlparse(u)
        host = parsed.netloc
        urls = [u]
        for m in GENERIC_MIRRORS:
            if m != host:
                urls.append(u.replace(host, m, 1))
        return urls

    def _local_get_with_retry(u):
        urls = _local_build_mirrors(u)
        last_err = "Unknown error"
        for att_url in urls:
            parsed = urlparse(att_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            _warm_session(base)
            time.sleep(0.8)
            try:
                resp = (SCRAPER or SESSION).get(att_url, timeout=15)
                if resp.status_code == 403:
                    if CLOUDSCRAPER and not SCRAPER:
                        try:
                            cs = cloudscraper.create_scraper()
                            resp2 = cs.get(att_url, timeout=20)
                            if resp2.status_code == 200: return resp2, att_url
                        except Exception: pass
                    elif SCRAPER:
                        try:
                            time.sleep(2)
                            resp2 = SCRAPER.get(att_url, timeout=30)
                            if resp2.status_code == 200: return resp2, att_url
                        except Exception: pass
                    last_err = f"403 Forbidden — Cloudflare protected"
                    continue
                resp.raise_for_status()
                return resp, att_url
            except Exception as e:
                last_err = str(e)
                continue
        raise requests.RequestException(f"{last_err} — All mirrors failed.")

    try:
        resp, actual_url = _local_get_with_retry(url)
    except Exception as e:
        log.error("_fetch_chapter_generic failed", url=url, error=str(e))
        return None, [], None, None, str(e), None

    soup = BeautifulSoup(resp.text, "html.parser")
    title = "Chapter"
    for tag_name, attrs in [("span", {"class": "chr-text"}), ("h2", {"class": "chr-title"}), ("h1", {})]:
        tag = soup.find(tag_name, attrs)
        if tag:
            title = tag.get_text(strip=True)
            break

    content_div = None
    for selector in ["chr-content", "chapter-content", "content"]:
        content_div = soup.find("div", id=selector)
        if content_div:
            break
    if not content_div:
        divs = soup.find_all("div")
        content_div = max(divs, key=lambda d: len(d.find_all("p")), default=None)
    
    if not content_div:
        return title, [], None, None, "Could not locate content.", None

    for junk in content_div(["script", "style", "iframe", "ins", "noscript"]):
        junk.decompose()

    raw_paragraphs = [p.get_text(separator=" ").strip() for p in content_div.find_all("p") if p.get_text(strip=True)]
    if not raw_paragraphs:
        raw_text = content_div.get_text(separator="\n").strip()
        raw_paragraphs = [l.strip() for l in raw_text.splitlines() if l.strip()]

    paragraphs = [p for p in raw_paragraphs if not any(re.search(pat, p) for pat in GENERIC_WATERMARKS)]

    parsed = urlparse(actual_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    next_url = _extract_nav(soup, "next_chap", base)
    prev_url = _extract_nav(soup, "prev_chap", base)

    url_ch_num = None
    m_url = re.search(r'/chapter-([0-9]+)', actual_url)
    if m_url:
        url_ch_num = int(m_url.group(1))

    if not prev_url and url_ch_num is not None and url_ch_num > 1:
        ch_prefix = actual_url[:actual_url.index(m_url.group(0))]
        prev_url = f"{ch_prefix}/chapter-{url_ch_num - 1}"

    return title, paragraphs, next_url, prev_url, None, url_ch_num

def fetch_chapter(url: str):
    plugin = plugin_registry.for_url(url)
    if plugin:
        scraper = SCRAPER if plugin.supports_cloudflare else None
        result  = plugin.fetch_chapter(url, SESSION, scraper)
        if result.error:
            return "", [], None, None, result.error, None
        result.paragraphs = plugin.clean_content(result.paragraphs)
        return result.title, result.paragraphs, result.next_url, result.prev_url, None, result.chapter_num
    else:
        return _fetch_chapter_generic(url)


def download_book(book_name: str, start_url: str, incremental: bool = False):
    print(f"\nDownloading '{book_name}'...")
    chapters = []
    url = start_url
    seen = set()
    chapter_num = 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
        disable=not RICH
    ) as progress:
        task = progress.add_task("Downloading chapters...", total=None)

        while url and url not in seen:
            seen.add(url)
            progress.update(task, description=f"Chapter {chapter_num}...")

            title, paragraphs, next_url, prev_url, error, url_ch_num = fetch_chapter(url)
            if error or not paragraphs:
                print(f"\nError at {url}: {error or 'No content'}")
                break

            chapters.append((title, paragraphs))
            url = next_url
            chapter_num += 1
            progress.update(task, advance=1)

    if not chapters:
        print("No chapters downloaded.")
        return

    save_path = get_book_path(book_name)

    if incremental and os.path.exists(save_path):
        existing_chapters = 0
        with open(save_path, 'r', encoding='utf-8') as f:
            content = f.read()
            existing_chapters = content.count('Chapter ')
        with open(save_path, 'a', encoding='utf-8') as f:
            f.write(f"\n\n{'='*60}\n")
            for i, (title, paras) in enumerate(chapters, existing_chapters + 1):
                f.write(f"Chapter {i}: {title}\n")
                f.write(f"{'='*60}\n\n")
                for p in paras:
                    f.write(p + "\n\n")
        print(f"\n✅ Appended {len(chapters)} new chapters to: {save_path}")
    else:
        with open(save_path, 'w', encoding='utf-8') as f:
            for i, (title, paras) in enumerate(chapters, 1):
                f.write(f"\n\n{'='*60}\n")
                f.write(f"Chapter {i}: {title}\n")
                f.write(f"{'='*60}\n\n")
                for p in paras:
                    f.write(p + "\n\n")
        print(f"\n✅ Book saved to: {save_path}")

    return save_path


def truncate_text(text: str, max_length: int = 60) -> str:
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."


# ── Metadata ──────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _is_noise(text: str) -> bool:
    noise = re.compile(
        r'more from|follow|bookmark|add to|all novel|read more|'
        r'latest chapter|chapter list|table of content|^genres?$|^tags?$|'
        r'^author$|^status$|^rating$|^views?$',
        re.I
    )
    return bool(noise.search(text)) or len(text) > 120


def _extract_json_ld(soup) -> dict:
    meta = {}
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            data = _json.loads(tag.string or '')
            if not isinstance(data, dict):
                continue
            if data.get('@type') in ('Book', 'CreativeWork', 'WebPage', 'Article'):
                if data.get('name') and not meta.get('title'):
                    meta['title'] = _clean(data['name'])
                if data.get('author'):
                    author = data['author']
                    if isinstance(author, dict):
                        author = author.get('name', '')
                    elif isinstance(author, list):
                        author = ', '.join(a.get('name', '') for a in author if isinstance(a, dict))
                    if author and not _is_noise(str(author)):
                        meta['author'] = _clean(str(author))
                if data.get('genre') and not meta.get('genres'):
                    g = data['genre']
                    if isinstance(g, list):
                        g = ', '.join(g)
                    meta['genres'] = _clean(str(g))
                if data.get('description') and not meta.get('synopsis'):
                    meta['synopsis'] = _clean(data['description'])
        except Exception:
            pass
    return meta


def _extract_og_meta(soup) -> dict:
    meta = {}
    for tag in soup.find_all('meta'):
        prop = tag.get('property', '') or tag.get('name', '')
        content = _clean(tag.get('content', ''))
        if not content:
            continue
        if prop in ('og:title', 'twitter:title') and not meta.get('title'):
            meta['title'] = content
        if prop in ('og:description', 'description', 'twitter:description') and not meta.get('synopsis'):
            meta['synopsis'] = content
    return meta


LABEL_MAP = {
    'author': 'author', 'writer': 'author',
    'genre': 'genres', 'genres': 'genres',
    'category': 'genres', 'categories': 'genres',
    'status': 'status',
    'alternative': 'alternative_names', 'other name': 'alternative_names',
    'other names': 'alternative_names', 'alt name': 'alternative_names',
    'tag': 'tags', 'tags': 'tags',
    'source': 'source', 'translator': 'translator',
    'year': 'year', 'release': 'year',
    'type': 'novel_type',
}


def _scrape_info_box(soup) -> dict:
    meta = {}
    for li in soup.find_all('li'):
        label_tag = li.find(['label', 'h3', 'h4', 'strong', 'span'], recursive=False)
        if not label_tag:
            continue
        label_text = _clean(label_tag.get_text()).rstrip(':').lower()
        key = LABEL_MAP.get(label_text)
        if not key or meta.get(key):
            continue
        value_parts = []
        for child in li.children:
            if child is label_tag:
                continue
            if hasattr(child, 'get_text'):
                t = _clean(child.get_text(separator=', '))
                if t and not _is_noise(t):
                    value_parts.append(t)
            else:
                t = _clean(str(child))
                if t and not _is_noise(t):
                    value_parts.append(t)
        value = ', '.join(v for v in value_parts if v).strip(', ')
        if value:
            meta[key] = value
    if meta:
        return meta
    for elem in soup.find_all(['div', 'p', 'span', 'td']):
        text = _clean(elem.get_text(separator=': '))
        for label_pat, key in LABEL_MAP.items():
            if meta.get(key):
                continue
            m = re.match(rf'^{re.escape(label_pat)}\s*:\s*(.+)$', text, re.I)
            if m:
                value = _clean(m.group(1))
                if value and not _is_noise(value) and len(value) < 300:
                    meta[key] = value
                    break
    return meta


_SYNOPSIS_SELECTORS = [
    ('div', {'id': 'novel-desc'}),
    ('div', {'id': 'synopsis'}),
    ('div', {'id': 'description'}),
    ('div', {'class': 'desc-text'}),
    ('div', {'class': 'summary__content'}),
    ('div', {'class': 'synopsis'}),
    ('div', {'class': 'description'}),
    ('div', {'class': 'summary'}),
    ('div', {'class': 'novel-desc'}),
    ('div', {'class': 'book-desc'}),
    ('div', {'class': 'detail-desc'}),
    ('div', {'class': 'desc'}),
]


def _extract_synopsis(soup):
    for tag_name, attrs in _SYNOPSIS_SELECTORS:
        tag = soup.find(tag_name, attrs)
        if tag:
            for junk in tag(['script', 'style', 'button', 'iframe', 'ins', 'noscript']):
                junk.decompose()
            for span in tag.find_all(['span', 'a']):
                if re.search(r'read more|show (less|more)|collapse', _clean(span.get_text()), re.I):
                    span.decompose()
            text = _clean(tag.get_text(separator=' '))
            text = re.sub(r'^(synopsis|description|summary|about)\s*[:\-]?\s*', '', text, flags=re.I)
            if len(text) > 60:
                return text[:2000]
    candidates = []
    for div in soup.find_all('div'):
        ps = div.find_all('p', recursive=False)
        if len(ps) < 2:
            continue
        text = _clean(div.get_text(separator=' '))
        if 80 < len(text) < 3000:
            candidates.append(text)
    if candidates:
        candidates.sort(key=len)
        return candidates[0][:2000]
    return None


def fetch_book_metadata(chapter_url: str) -> dict:
    plugin = plugin_registry.for_url(chapter_url)
    if plugin:
        try:
            metadata = plugin.fetch_metadata(chapter_url, SESSION)
            if metadata and metadata.title:
                return {
                    "title": metadata.title,
                    "author": metadata.author,
                    "status": metadata.status,
                    "genres": ", ".join(metadata.genres),
                    "synopsis": metadata.synopsis,
                    "cover_url": metadata.cover_url
                }
        except Exception as e:
            log.error("Plugin fetch_metadata failed", plugin=plugin.id, error=str(e))

    try:
        parsed = urlparse(chapter_url)
        path_parts = parsed.path.strip('/').split('/')
        candidates = []
        if len(path_parts) >= 2:
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/{path_parts[1]}")
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/{path_parts[1]}/")
        if len(path_parts) >= 1:
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}")
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/")
            slug = path_parts[0].rstrip('.html')
            candidates.append(f"{parsed.scheme}://{parsed.netloc}/{slug}.html")
        resp = None
        for url in dict.fromkeys(candidates):
            try:
                r, _ = _generic_get_with_retry(url)
                if r and r.status_code == 200 and len(r.text) > 2000:
                    resp = r
                    break
            except Exception:
                continue
        if not resp:
            return {}
        soup = BeautifulSoup(resp.text, 'html.parser')
        meta = {}
        meta.update(_extract_json_ld(soup))
        for k, v in _extract_og_meta(soup).items():
            if k not in meta:
                meta[k] = v
        for k, v in _scrape_info_box(soup).items():
            if not meta.get(k):
                meta[k] = v
        syn = _extract_synopsis(soup)
        if syn:
            current_synopsis = meta.get('synopsis', '')
            if len(syn) > len(current_synopsis):
                meta['synopsis'] = syn
        for k in list(meta.keys()):
            if isinstance(meta[k], str):
                meta[k] = re.sub(r'&[a-z]+;', ' ', meta[k]).strip()
                if not meta[k]:
                    del meta[k]
        return meta
    except Exception as e:
        log.error("fetch_book_metadata failed", url=chapter_url, error=str(e))
        return {}


def resolve_first_chapter_url(book_page_url: str) -> str | None:
    """
    Given a book landing page URL, resolve the first chapter URL.
    This scrapes the book page and looks for 'Read' or 'Start Reading' links.
    Returns the first chapter URL or None if not found.
    """
    try:
        # Check if plugin can handle this URL
        plugin = plugin_registry.for_url(book_page_url)
        if plugin and hasattr(plugin, 'resolve_first_chapter'):
            try:
                return plugin.resolve_first_chapter(book_page_url, SESSION)
            except Exception as e:
                log.warning("Plugin resolve_first_chapter failed", url=book_page_url, error=str(e))

        # Generic fallback: scrape the book page for first chapter link
        resp, actual_url = _generic_get_with_retry(book_page_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Look for common "Read" or "Start Reading" button patterns
        read_patterns = [
            r"read\s+now",
            r"start\s+reading",
            r"read\s+first\s+chapter",
            r"begin\s+reading",
            r"^read$",
            r"^start$",
        ]

        # 1. Look for buttons/links with read patterns
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            for pat in read_patterns:
                if re.search(pat, text, re.I):
                    href = a["href"]
                    if href:
                        # Make absolute URL
                        if href.startswith("http"):
                            return href
                        elif href.startswith("/"):
                            return base + href
                        else:
                            return base + "/" + href

        # 2. Look for chapter list and get first chapter
        chapter_selectors = [
            ("a", {"class": re.compile(r"chapter|chap", re.I)}),
            ("a", {"href": re.compile(r"/chapter[-/]?", re.I)}),
            ("a", {"href": re.compile(r"chapter-\d+", re.I)}),
        ]

        for tag_name, attrs in chapter_selectors:
            links = soup.find_all(tag_name, attrs)
            if links:
                # Sort by href to get chapter 1 or earliest
                def chapter_sort_key(a):
                    href = a.get("href", "")
                    m = re.search(r"chapter[-/]?(\d+)", href, re.I)
                    if m:
                        return int(m.group(1))
                    return 999999

                sorted_links = sorted(links, key=chapter_sort_key)
                if sorted_links:
                    first_ch = sorted_links[0]["href"]
                    if first_ch.startswith("http"):
                        return first_ch
                    elif first_ch.startswith("/"):
                        return base + first_ch
                    else:
                        return base + "/" + first_ch

        # 3. Look for table of contents links
        toc_selectors = [
            ("a", {"href": re.compile(r"toc|contents|chapters|list", re.I)}),
            ("a", {"class": re.compile(r"toc|contents|chapters|list", re.I)}),
        ]

        for tag_name, attrs in toc_selectors:
            toc_link = soup.find(tag_name, attrs)
            if toc_link:
                href = toc_link["href"]
                if href:
                    toc_url = href if href.startswith("http") else (base + href if href.startswith("/") else base + "/" + href)
                    # Try to get first chapter from TOC page
                    try:
                        toc_resp, _ = _generic_get_with_retry(toc_url)
                        toc_soup = BeautifulSoup(toc_resp.text, "html.parser")
                        for sel in chapter_selectors:
                            links = toc_soup.find_all(*sel)
                            if links:
                                sorted_links = sorted(links, key=lambda a: int(re.search(r"chapter[-/]?(\d+)", a.get("href", ""), re.I).group(1)) if re.search(r"chapter[-/]?(\d+)", a.get("href", ""), re.I) else 999999)
                                if sorted_links:
                                    first_ch = sorted_links[0]["href"]
                                    if first_ch.startswith("http"):
                                        return first_ch
                                    elif first_ch.startswith("/"):
                                        return base + first_ch
                                    else:
                                        return base + "/" + first_ch
                    except Exception:
                        pass

        return None
    except Exception as e:
        log.error("resolve_first_chapter_url failed", url=book_page_url, error=str(e))
        return None


def parse_novelbin(soup): return _scrape_info_box(soup)
def parse_novelfull(soup): return _scrape_info_box(soup)
def parse_wuxiaworld(soup): return _scrape_info_box(soup)
def parse_royalroad(soup): return _scrape_info_box(soup)
def parse_generic(soup): return _scrape_info_box(soup)
def extract_synopsis_fallback(soup): return _extract_synopsis(soup)


def extract_book_title_from_chapter(url: str) -> str:
    try:
        resp, _ = _generic_get_with_retry(url)
        soup = BeautifulSoup(resp.text, "html.parser")
        breadcrumb = soup.find("ol", class_="breadcrumb")
        if breadcrumb:
            for li in breadcrumb.find_all("li"):
                a = li.find("a")
                if a and "novel" in a.get("href", ""):
                    return a.get_text(strip=True)
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if text and len(text) < 100 and "chapter" not in text.lower():
                return text
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            for sep in [" – ", " - ", " | ", " — "]:
                if sep in title:
                    parts = title.split(sep)
                    return parts[-1].strip()
            return title
    except Exception:
        pass
    return None


def _extract_nav(soup, link_id, base):
    def _make_abs(href):
        if not href or href.startswith("javascript") or href in ("#", "null", "undefined"):
            return None
        return href if href.startswith("http") else base + (href if href.startswith("/") else "/" + href)

    is_next = "next" in link_id

    # 1. Exact id match (original behaviour)
    tag = soup.find("a", id=link_id)
    if tag and tag.get("href"):
        return _make_abs(tag["href"])

    # 2. Common id variants
    id_variants = (
        ["next_chap","next-chap","next_chapter","next-chapter","nextchapter",
         "next_btn","btn-next","next-page","nextPage","chapter-next"]
        if is_next else
        ["prev_chap","prev-chap","prev_chapter","prev-chapter","prevchapter",
         "prev_btn","btn-prev","prev-page","prevPage","chapter-prev"]
    )
    for vid in id_variants:
        tag = soup.find("a", id=vid)
        if tag and tag.get("href"):
            return _make_abs(tag["href"])

    # 3. Common class variants
    cls_variants = (
        ["next_chap","next-chap","next_chapter","next-chapter","next-page",
         "nextchapter","btn-next","chapter-next","nav-next","pager-next"]
        if is_next else
        ["prev_chap","prev-chap","prev_chapter","prev-chapter","prev-page",
         "prevchapter","btn-prev","chapter-prev","nav-prev","pager-prev"]
    )
    for cls in cls_variants:
        tag = soup.find("a", class_=re.compile(cls, re.I))
        if tag and tag.get("href"):
            return _make_abs(tag["href"])

    # 4. rel="next" / rel="prev"
    rel = "next" if is_next else "prev"
    tag = soup.find("a", rel=re.compile(rf"\b{rel}\b", re.I))
    if tag and tag.get("href"):
        return _make_abs(tag["href"])

    # 5. Link text matching
    text_patterns = (
        [r"next\s*chapter", r"^next$", r"next\s*>", r">>", r"next\s*ep"]
        if is_next else
        [r"prev\s*chapter", r"previous\s*chapter", r"^prev$", r"^previous$", r"<\s*prev", r"<<"]
    )
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True)
        for pat in text_patterns:
            if re.search(pat, txt, re.I):
                href = _make_abs(a["href"])
                if href: return href

    # 6. aria-label
    label = "next" if is_next else "prev"
    for a in soup.find_all("a", href=True):
        aria = (a.get("aria-label","") or a.get("title","")).lower()
        if label in aria and ("chapter" in aria or "page" in aria or aria == label):
            href = _make_abs(a["href"])
            if href: return href

    return None


def get_download_status_text(book):
    state = book.get('download_state', {})
    status = state.get('status', 'idle')
    if status == 'downloading':
        downloaded = state.get('total_chapters_downloaded', 0)
        return f"[cyan]⏳ Downloading... ({downloaded} chapters)[/cyan]"
    elif status == 'completed':
        downloaded = state.get('total_chapters_downloaded', 0)
        path = state.get('download_path', '')
        filename = os.path.basename(path) if path else get_book_filename(book.get('book_title', 'Unknown'))
        return f"[green]✅ Downloaded ({downloaded} chapters) - {filename}[/green]"
    elif status == 'paused':
        downloaded = state.get('total_chapters_downloaded', 0)
        return f"[yellow]⏸️ Paused ({downloaded} chapters downloaded)[/yellow]"
    elif status == 'queued':
        return "[blue]⏳ Queued for download...[/blue]"
    elif status == 'failed':
        failed = len(state.get('failed_chapters', []))
        return f"[red]❌ Download failed ({failed} chapters failed)[/red]"
    elif status == 'cancelled':
        return "[red]❌ Download cancelled[/red]"
    return None


def get_book_filename(book_name):
    return re.sub(r'[^\w\-_\. ]', '_', book_name) + ".txt"

LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "library")

def get_book_path(book_name):
    """Return the full path to a book's .txt file under library/{name}/{name}.txt"""
    safe = re.sub(r'[^\w\-_\. ]', '_', book_name)
    book_dir = os.path.join(LIBRARY_DIR, safe)
    os.makedirs(book_dir, exist_ok=True)
    return os.path.join(book_dir, safe + ".txt")


def append_chapter_to_file(book_name, chapter_num, title, paragraphs):
    save_path = get_book_path(book_name)
    real_num = chapter_num
    m = re.search(r'chapter[\s\-_]*(\d+)', title, re.IGNORECASE)
    if m:
        real_num = int(m.group(1))
    try:
        with open(save_path, 'a', encoding='utf-8') as f:
            f.write(f"\n\n{'='*60}\n")
            f.write(f"Chapter {real_num}: {title}\n")
            f.write(f"{'='*60}\n\n")
            for p in paragraphs:
                f.write(p + "\n\n")
        log.debug("Chapter appended to file", book=book_name, chapter=real_num)
    except Exception as e:
        log.error("append_chapter_to_file failed", book=book_name, chapter=real_num, error=str(e))


def get_chapter_from_file(book_name: str, chapter_num: int):
    """
    Try to load a chapter from the local .txt file.
    Returns (title, paragraphs) or (None, None) if not found.
    """
    try:
        save_path = get_book_path(book_name)

        if not os.path.exists(save_path):
            return None, None

        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()

        # Split into chapter blocks on the === separator
        blocks = re.split(r'={50,}', raw)
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""

            # Header line: "Chapter 5: Some Title"
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m and int(m.group(1)) == chapter_num:
                title      = m.group(2).strip() or f"Chapter {chapter_num}"
                paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
                if paragraphs:
                    return title, paragraphs

    except Exception as e:
        log.error("get_chapter_from_file failed", book=book_name, chapter=chapter_num, error=str(e))
    return None, None


def read_chapters_around(book_name: str, chapter_num: int, n: int = 5) -> str:
    """
    Read n chapters around chapter_num from the local .txt file.
    Returns concatenated text of those chapters, or empty string if not found.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return ""
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        blocks = re.split(r'={50,}', raw)
        chapters = []
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), body))
        if not chapters:
            return ""
        # Find chapters around chapter_num
        start = max(0, chapter_num - n)
        end   = chapter_num
        result = []
        for ch_num, title, body in chapters:
            if start <= ch_num <= end:
                result.append(f"Chapter {ch_num}: {title}\n\n{body[:3000]}")
        return "\n\n" + ("=" * 40) + "\n\n".join(result) if result else ""
    except Exception as e:
        log.error("read_chapters_around failed", book=book_name, chapter=chapter_num, error=str(e))
        return ""


def read_last_n_chapters(book_name: str, n: int = 5) -> str:
    """
    Read the last n chapters from the local .txt file.
    Returns concatenated text, or empty string if not found.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return ""
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        blocks = re.split(r'={50,}', raw)
        chapters = []
        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
            m = re.match(r'Chapter\s+(\d+)\s*[:\-]?\s*(.*)', header, re.IGNORECASE)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), body))
        if not chapters:
            return ""
        last_n = chapters[-n:]
        result = []
        for ch_num, title, body in last_n:
            result.append(f"Chapter {ch_num}: {title}\n\n{body[:3000]}")
        return "\n\n" + ("=" * 40) + "\n\n".join(result) if result else ""
    except Exception as e:
        log.error("read_last_n_chapters failed", book=book_name, n=n, error=str(e))
        return ""


def find_next_chapter(url: str):
    """Fetch url and extract the next chapter link using all available methods."""
    plugin = plugin_registry.for_url(url)
    if plugin:
        scraper = SCRAPER if plugin.supports_cloudflare else None
        try:
            result = plugin.fetch_chapter(url, SESSION, scraper)
            return result.next_url
        except Exception as e:
            log.warning("Plugin find_next_chapter failed", url=url, plugin=plugin.id, error=str(e))

    try:
        resp, actual_url = _generic_get_with_retry(url)
        if actual_url != url:
            log.warning(
                "find_next_chapter: URL redirected — nav scraping may fail on unfamiliar layout",
                original=url, redirected_to=actual_url,
            )
        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = urlparse(actual_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return _extract_nav(soup, "next_chap", base)
    except Exception as e:
        log.warning("find_next_chapter failed", url=url, error=str(e))
    return None


class DownloadManager:
    def __init__(self):
        self.active_downloads = {}
        self.cancelled_books  = set()   # books removed by user — never download these
        self.download_queue = queue.Queue()
        self._queue_order: list[str] = []   # book names in queue order
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker)
        self.worker_thread.daemon = True
        self.worker_thread.start()
        # 1. Add locks
        self._book_locks = {}
        self._book_locks_lock = threading.Lock()
        self._active_lock = threading.Lock()
        log.debug("DownloadManager: Initialized with locks")

    # 2. Add helper method
    def _get_book_lock(self, book_name):
        log.debug(f"DownloadManager: _get_book_lock called for {book_name}")
        with self._book_locks_lock:
            if book_name not in self._book_locks:
                self._book_locks[book_name] = threading.Lock()
            return self._book_locks[book_name]

    def cancel_book(self, book_name):
        """Permanently cancel all activity for a book. Called when user deletes it."""
        log.debug(f"DownloadManager: Attempting to cancel book {book_name}")
        # 7. Acquire book lock AND active_lock
        book_lock = self._get_book_lock(book_name)
        with book_lock:
            log.debug(f"DownloadManager: Acquired book lock for {book_name} in cancel_book")
            with self._active_lock:
                log.debug(f"DownloadManager: Acquired active_lock for {book_name} in cancel_book")
                self.cancelled_books.add(book_name)
                # It's important to remove from active_downloads ONLY if it was there to begin with
                # to avoid potential issues if it's not present. pop(key, None) handles this.
                self.active_downloads.pop(book_name, None)
                if book_name in self._queue_order:
                    self._queue_order.remove(book_name)
                log.legion.debug("DownloadManager.cancel_book", book=book_name, cancelled_books=list(self.cancelled_books))
                log.debug(f"DownloadManager: Book {book_name} cancelled. Released active_lock and book lock.")

    def get_queue_snapshot(self) -> list[str]:
        """Return current queue order (copy)."""
        with self._active_lock:
            log.debug("DownloadManager: Acquired active_lock in get_queue_snapshot")
            snapshot = list(self._queue_order)
            log.debug("DownloadManager: Released active_lock in get_queue_snapshot")
            return snapshot

    def get_chapter_rate(self, book_name: str) -> float:
        """Return average chapters/minute for active download, or 0."""
        # This method reads from active_downloads, so it should be protected.
        with self._active_lock:
            log.debug(f"DownloadManager: Acquired active_lock for {book_name} in get_chapter_rate")
            book = self.active_downloads.get(book_name)
            log.debug(f"DownloadManager: Released active_lock for {book_name} in get_chapter_rate")
        if not book:
            return 0.0
        # No need to lock book['download_state'] access here as it's a copy
        dl = book.get("download_state", {})
        ts = dl.get("timestamp", 0)
        cnt = dl.get("total_chapters_downloaded", 0)
        if not ts or cnt < 2:
            return 0.0
        elapsed = time.time() - ts
        if elapsed < 10:
            return 0.0
        return cnt / (elapsed / 60)

    def _worker(self):
        while self.running:
            try:
                book_name, book, progress = self.download_queue.get(timeout=1)
                # 5. In queue_download (already handled by queue_download lock)
                # This check happens *before* _download_book, so no need for book lock here for cancelled_books
                # The cancelled_books set itself needs protection, though.
                with self._book_locks_lock: # Protect access to self.cancelled_books
                    log.debug(f"DownloadManager: Acquired _book_locks_lock in _worker (cancelled_books check)")
                    if book_name in self.cancelled_books:
                        log.legion.debug("DownloadManager: skipping cancelled book", book=book_name)
                        log.debug("Skipping cancelled book in queue", book=book_name)
                        log.debug(f"DownloadManager: Released _book_locks_lock in _worker (cancelled_books check)")
                        continue
                    log.debug(f"DownloadManager: Released _book_locks_lock in _worker (cancelled_books check)")

                self._download_book(book_name, book, progress)
            except queue.Empty:
                continue

    def _download_book(self, book_name, book, progress):
        log.debug(f"DownloadManager: _download_book started for {book_name}")
        # 3. Acquire book lock at the START of the method
        book_lock = self._get_book_lock(book_name)
        with book_lock:
            log.debug(f"DownloadManager: Acquired book lock for {book_name} in _download_book")

            # Check cancelled_books inside book_lock for consistency
            with self._book_locks_lock: # Protect self.cancelled_books access
                log.debug(f"DownloadManager: Acquired _book_locks_lock in _download_book (cancelled check)")
                if book_name in self.cancelled_books:
                    log.legion.debug("DownloadManager._download_book: already cancelled, aborting", book=book_name)
                    log.info("Download aborted — book is cancelled", book=book_name)
                    log.debug(f"DownloadManager: Released _book_locks_lock and book lock for {book_name} in _download_book (cancelled check)")
                    return
                log.debug(f"DownloadManager: Released _book_locks_lock in _download_book (cancelled check)")

            with self._active_lock: # Protect _queue_order access
                log.debug(f"DownloadManager: Acquired active_lock for {book_name} in _download_book (_queue_order check)")
                if book_name in self._queue_order:
                    self._queue_order.remove(book_name)
                log.debug(f"DownloadManager: Released active_lock for {book_name} in _download_book (_queue_order check)")

            # Use "with lock:" around ALL code that reads/writes book['download_state']
            # This applies to the entire block as book['download_state'] is accessed/modified extensively.
            book['download_state']['status'] = 'downloading'
            save_progress(progress) # This needs to handle its own thread-safety for saving the whole progress dict

            sync_start = book.get('download_state', {}).get('_sync_start_url')
            if sync_start:
                start_url = sync_start
                book['download_state'].pop('_sync_start_url', None)
            else:
                start_url = book.get('current_url')
                last_downloaded = book.get('download_state', {}).get('last_downloaded_chapter')
                # Guard: reject corrupted /null URLs from JS artifacts
                if last_downloaded and (last_downloaded.endswith('/null') or
                                        last_downloaded.endswith('/undefined') or
                                        '/null' in last_downloaded.split('/')[-1:]):
                    last_downloaded = None
                    book['download_state']['last_downloaded_chapter'] = None
                if last_downloaded:
                    start_url = find_next_chapter(last_downloaded)
                else:
                    # No previous chapter — current_url is the book landing page, not a chapter.
                    # Resolve the first chapter URL before starting the download loop.
                    resolved = resolve_first_chapter_url(start_url)
                    if resolved:
                        log.info("Resolved first chapter URL from landing page", book=book_name, first_chapter=resolved)
                        start_url = resolved
                    else:
                        log.error("Could not resolve first chapter URL from landing page — aborting download", book=book_name, landing=start_url)
                        book['download_state']['status'] = 'idle'
                        book['download_state']['last_error'] = 'Could not find first chapter URL from landing page'
                        save_progress(progress)
                        return

            chapter_num = book.get('download_state', {}).get('total_chapters_downloaded', 0) + 1
            url = start_url
            seen = set()
            while url and url not in seen:
                seen.add(url)
                # Check cancelled_books mid-download
                with self._book_locks_lock: # Protect self.cancelled_books access
                    log.debug(f"DownloadManager: Acquired _book_locks_lock in _download_book (mid-download cancel check)")
                    if book_name in self.cancelled_books:
                        log.legion.debug("DownloadManager: mid-download cancel detected, stopping", book=book_name)
                        log.info("Mid-download cancel detected", book=book_name)
                        log.debug(f"DownloadManager: Released _book_locks_lock and book lock for {book_name} in _download_book (mid-download cancel)")
                        return
                    log.debug(f"DownloadManager: Released _book_locks_lock in _download_book (mid-download cancel)")

                if book['download_state'].get('pause_requested'):
                    book['download_state']['status'] = 'paused'
                    book['download_state']['pause_requested'] = False
                    save_progress(progress)
                    log.debug(f"DownloadManager: Released book lock for {book_name} in _download_book (pause requested)")
                    return
                try:
                    title, paragraphs, next_url, prev_url, error, url_ch_num = fetch_chapter(url)
                    if error or not paragraphs:
                        reason = error or 'No content returned'
                        log.error("Chapter fetch failed during download", book=book_name, url=url, reason=reason)
                        book['download_state']['failed_chapters'].append(url)
                        book['download_state']['last_error'] = reason
                        save_progress(progress)
                        # Stop — chapter missing means we've hit the end of released chapters
                        break
                    else:
                        # Extract real chapter number from title for accurate tracking
                        import re as _re2 # Using alias to avoid conflict if re is imported differently
                        _m = _re2.search(r'chapter[\s\-_]*(\d+)', title, _re2.IGNORECASE)
                        real_ch_num = int(_m.group(1)) if _m else chapter_num
                        append_chapter_to_file(book_name, real_ch_num, title, paragraphs)
                        book['download_state']['last_downloaded_chapter'] = url
                        book['download_state']['last_downloaded_chapter_num'] = real_ch_num
                        book['download_state']['total_chapters_downloaded'] =                         book['download_state'].get('total_chapters_downloaded', 0) + 1
                        book['download_state']['timestamp'] = time.time()
                        chapter_num += 1
                        url = next_url
                        save_progress(progress)  # save every chapter so UI stays current
                except Exception as exc:
                    log.error("Exception during chapter download", book=book_name, url=url, error=str(exc))
                    book['download_state']['failed_chapters'].append(url)
                    book['download_state']['last_error'] = str(exc)
                    save_progress(progress)
                    break
            try:
                dl_path = get_book_path(book_name)
                book['download_state']['download_path'] = dl_path
            except Exception:
                pass
            book['download_state']['status'] = 'completed'
            save_progress(progress)
            with self._active_lock: # Protect active_downloads access
                log.debug(f"DownloadManager: Acquired active_lock for {book_name} in _download_book (pop active_downloads)")
                self.active_downloads.pop(book_name, None)  # no longer active
                log.debug(f"DownloadManager: Released active_lock for {book_name} in _download_book (pop active_downloads)")

        log.debug(f"DownloadManager: Released book lock for {book_name} in _download_book (end)")


    def queue_download(self, book_name, book, progress):
        log.debug(f"DownloadManager: Attempting to queue download for {book_name}")
        # 5. Acquire book lock before checking cancelled_books
        book_lock = self._get_book_lock(book_name)
        with book_lock:
            log.debug(f"DownloadManager: Acquired book lock for {book_name} in queue_download")
            with self._book_locks_lock: # Protect access to self.cancelled_books
                log.debug(f"DownloadManager: Acquired _book_locks_lock in queue_download (cancelled_books check)")
                if book_name in self.cancelled_books:
                    log.legion.debug("DownloadManager.queue_download: refused for cancelled book", book=book_name)
                    log.warning("queue_download refused — book is cancelled", book=book_name)
                    log.debug(f"DownloadManager: Released _book_locks_lock and book lock for {book_name} in queue_download")
                    return
                log.debug(f"DownloadManager: Released _book_locks_lock in queue_download (cancelled_books check)")

            # 5. Acquire active_lock before modifying active_downloads and _queue_order
            with self._active_lock:
                log.debug(f"DownloadManager: Acquired active_lock for {book_name} in queue_download")
                if book_name not in self.active_downloads:
                    self.active_downloads[book_name] = book  # track so pause/UI can find it
                    self._queue_order.append(book_name)
                    self.download_queue.put((book_name, book, progress))
                    log.debug(f"DownloadManager: Book {book_name} queued. Released active_lock.")
                else:
                    log.debug(f"DownloadManager: Book {book_name} already in active_downloads. Released active_lock.")
            log.debug(f"DownloadManager: Released book lock for {book_name} in queue_download")


    def pause_download(self, book_name):
        log.debug(f"DownloadManager: Attempting to pause download for {book_name}")
        # 6. Acquire book lock before checking status and setting pause_requested
        book_lock = self._get_book_lock(book_name)
        with book_lock:
            log.debug(f"DownloadManager: Acquired book lock for {book_name} in pause_download")
            # Need to read active_downloads here, so acquire active_lock too
            with self._active_lock:
                log.debug(f"DownloadManager: Acquired active_lock for {book_name} in pause_download (checking active_downloads)")
                if book_name in self.active_downloads:
                    book = self.active_downloads[book_name]
                    if book.get('download_state', {}).get('status') == 'downloading':
                        book['download_state']['pause_requested'] = True
                        log.debug(f"DownloadManager: Pause requested for {book_name}. Released active_lock and book lock.")
                log.debug(f"DownloadManager: Released active_lock for {book_name} in pause_download")

    def shutdown(self):
        log.debug("DownloadManager: Shutting down worker thread")
        self.running = False
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)
        log.debug("DownloadManager: Worker thread shut down.")


download_manager = DownloadManager()


def show_book_details(book_name: str, book: dict, progress: dict):
    if 'metadata' not in book or not book['metadata']:
        metadata = fetch_book_metadata(book.get('current_url'))
        if metadata:
            book['metadata'] = metadata
            save_progress(progress)

    while True:
        # Refresh book dict from progress so download progress is visible in UI
        book = progress["books"].get(book_name, book)
        os.system("cls" if os.name == "nt" else "clear")

        if RICH:
            title_width = min(get_terminal_size()[0]-4, 80)
            console.print()
            console.print(f"[title]{' ' * max(0, (title_width - len(book_name)) // 2)}{book_name}[/title]")

            meta = book.get('metadata', {})
            left_col = []
            right_col = []

            if meta.get('alternative_names'):
                left_col.append(f"[secondary]Alternative names:[/secondary] {meta['alternative_names']}")
            if meta.get('author'):
                left_col.append(f"[secondary]Author:[/secondary] {meta['author']}")
            if meta.get('genres'):
                left_col.append(f"[secondary]Genre:[/secondary] {truncate_text(meta['genres'], 80)}")
            if meta.get('status'):
                right_col.append(f"[secondary]Status:[/secondary] {meta['status']}")
            if meta.get('publishers'):
                right_col.append(f"[secondary]Publishers:[/secondary] {meta['publishers']}")
            if meta.get('tags'):
                right_col.append(f"[secondary]Tag:[/secondary] {truncate_text(meta['tags'], 50)}")
            if meta.get('year'):
                right_col.append(f"[secondary]Year of publishing:[/secondary] {meta['year']}")

            max_rows = max(len(left_col), len(right_col)) if (left_col or right_col) else 0
            for i in range(max_rows):
                left = left_col[i] if i < len(left_col) else ""
                right = right_col[i] if i < len(right_col) else ""
                if left and right:
                    console.print(f"{left:<40} {right}")
                elif left:
                    console.print(left)
                elif right:
                    console.print(f"{' ':<40} {right}")

            if meta.get('synopsis'):
                synopsis = re.sub(r'&[a-z]+;', ' ', meta['synopsis'])
                if len(synopsis) > 2000:
                    synopsis = synopsis[:2000] + "..."
                wrapped = textwrap.fill(synopsis, width=title_width-4)
                console.print(f"\n[secondary]Synopsis:[/secondary]\n{wrapped}")

            console.print()

            progress_table = Table(show_header=False, box=rich_box.SIMPLE, padding=(0, 2))
            progress_table.add_column("Metric", style="secondary")
            progress_table.add_column("Value", style="primary")
            progress_table.add_row("Last read", book.get('last_title', 'Not started'))
            progress_table.add_row("Chapters read", str(book.get('chapters_read', 0)))
            progress_table.add_row("Words read", f"{book.get('words_read', 0):,}")
            mins = book.get('minutes_read', 0)
            progress_table.add_row("Time spent", f"{mins//60}h {mins%60}m" if mins >= 60 else f"{mins}m")

            download_state = book.get('download_state', {})
            dl_status = download_state.get('status', 'idle')
            if dl_status != 'idle':
                dl_icons = {
                    'downloading': f"[cyan]⏳ {download_state.get('total_chapters_downloaded', 0)} ch[/cyan]",
                    'completed':   f"[green]✅ {download_state.get('total_chapters_downloaded', 0)} ch[/green]",
                    'paused':      "[yellow]⏸️ Paused[/yellow]",
                    'failed':      f"[red]❌ Failed ({len(download_state.get('failed_chapters', []))})[/red]",
                    'queued':      "[blue]⏳ Queued[/blue]",
                }
                progress_table.add_row("Download", dl_icons.get(dl_status, dl_status))

            new_ch = book.get('new_chapters_waiting', 0)
            if new_ch > 0:
                progress_table.add_row("New chapters", f"[accent]{new_ch}[/accent]")

            console.print(progress_table)

            dl_text = get_download_status_text(book)
            if dl_text:
                console.print()
                console.print(Panel(dl_text, border_style="accent" if "Downloading" in dl_text else "secondary",
                                    box=rich_box.HEAVY, width=title_width-4))

            total = book.get('total_chapters')
            if total and total > 0:
                pct = int((book.get('chapters_read', 0) / total) * 100)
                bar = render_progress_bar(book.get('chapters_read', 0), total, 40)
                console.print(f"\n[progress]Progress:[/progress] {pct}%")
                console.print(f"[progress]{bar}[/progress]")

            console.print(
                "\n[dim][1] Read  \u2022  [2] Check new  \u2022  [3] Download  \u2022  "
                "[4] Delete  \u2022  [5] Chapter list  \u2022  \\[r] Refresh  \u2022  \\[q] Back[/dim]"
            )

            if dl_status == 'downloading':
                console.print("[dim]  [p] Pause download  \u2022  [c] Cancel download[/dim]")
            elif dl_status == 'paused':
                console.print("[dim]  [v] Resume download  \u2022  [c] Cancel download[/dim]")

        else:
            print(f"\n=== {book_name} ===")
            meta = book.get('metadata', {})
            for label, key in [("Alternative names", "alternative_names"), ("Author", "author"),
                                ("Genres", "genres"), ("Status", "status"),
                                ("Publishers", "publishers"), ("Tags", "tags"), ("Year", "year")]:
                if meta.get(key):
                    print(f"{label}: {meta[key]}")
            if meta.get('synopsis'):
                synopsis = meta['synopsis']
                if len(synopsis) > 2000:
                    synopsis = synopsis[:2000] + "..."
                print(f"\nSynopsis:\n{synopsis}")
            print(f"\nLast read: {book.get('last_title', 'Not started')}")
            print(f"Chapters read: {book.get('chapters_read', 0)}")
            print(f"Words read: {book.get('words_read', 0):,}")
            mins = book.get('minutes_read', 0)
            print(f"Time spent: {mins//60}h {mins%60}m" if mins >= 60 else f"Time spent: {mins}m")
            download_state = book.get('download_state', {})
            dl_status = download_state.get('status', 'idle')
            if dl_status != 'idle':
                print(f"Download: {dl_status} ({download_state.get('total_chapters_downloaded', 0)} ch)")
            new_ch = book.get('new_chapters_waiting', 0)
            if new_ch > 0:
                print(f"New chapters: {new_ch}")
            print("\n1. Read\n2. Check new chapters\n3. Download\n4. Delete\n5. Chapter list\nr. Refresh metadata\nq. Back")
            if dl_status == 'downloading':
                print("p. Pause  •  c. Cancel")
            elif dl_status == 'paused':
                print("v. Resume  •  c. Cancel")

        # Key input — use 3s timeout during active downloads so UI auto-refreshes
        download_state = book.get('download_state', {})
        dl_active = download_state.get('status') in ('downloading', 'queued')
        if READCHAR and dl_active:
            import select, sys as _sys
            import tty, termios
            fd = _sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            key = ''
            try:
                tty.setraw(fd)
                rlist, _, _ = select.select([_sys.stdin], [], [], 3.0)
                if rlist:
                    key = _sys.stdin.read(1).lower()
                # else: timeout, key stays '' -> loop continues, screen refreshes
            except Exception:
                key = ''
            finally:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass
        elif READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  Choice: ").strip().lower()

        # Download control
        download_state = book.get('download_state', {})
        dl_status = download_state.get('status', 'idle')

        if key == "p" and dl_status == 'downloading':
            download_manager.pause_download(book_name)
            book['download_state']['pause_requested'] = True
            save_progress(progress)
            safe_print("⏸️ Download paused", "yellow")
            time.sleep(1)
            continue
        elif key == "v" and dl_status == 'paused':
            book['download_state']['status'] = 'queued'
            save_progress(progress)
            download_manager.queue_download(book_name, book, progress)
            safe_print("▶️ Download resumed", "green")
            time.sleep(1)
            continue
        elif key == "c" and dl_status in ('downloading', 'paused'):
            book['download_state']['pause_requested'] = True
            book['download_state']['status'] = 'cancelled'
            save_progress(progress)
            safe_print("❌ Download cancelled", "red")
            time.sleep(1)
            continue

        if key == "q":
            break
        elif key == "1":
            return "read", book.get("current_url")
        elif key == "2":
            print("\nChecking for new chapters...")
            count = check_for_new_chapters(book)
            book["new_chapters_waiting"] = count
            save_progress(progress)
            msg = f"✅ Found {count} new chapter{'s' if count>1 else ''}." if count > 0 else "No new chapters found."
            print(msg)
            time.sleep(1.5)
            continue
        elif key == "3":
            print(f"\nDownload options for '{book_name}':")
            print("1. Fresh download (overwrite existing file)")
            print("2. Incremental download (append new chapters)")
            print("3. Resume paused download")
            print("4. Cancel")
            choice = input("Choose option [1/2/3/4]: ").strip()
            if choice == "1":
                book['download_state'] = {
                    'status': 'queued', 'last_downloaded_chapter': None,
                    'last_downloaded_chapter_num': 0, 'total_chapters_downloaded': 0,
                    'download_path': None, 'failed_chapters': [],
                    'timestamp': time.time(), 'pause_requested': False
                }
                save_progress(progress)
                download_manager.queue_download(book_name, book, progress)
                safe_print("✅ Fresh download queued in background", "green")
            elif choice == "2":
                if dl_status in ('completed', 'idle'):
                    book['download_state']['status'] = 'queued'
                    save_progress(progress)
                    download_manager.queue_download(book_name, book, progress)
                    safe_print("✅ Incremental download queued", "green")
                else:
                    safe_print("❌ Download already in progress", "yellow")
            elif choice == "3":
                if dl_status == 'paused':
                    book['download_state']['status'] = 'queued'
                    save_progress(progress)
                    download_manager.queue_download(book_name, book, progress)
                    safe_print("✅ Download resumed", "green")
                else:
                    safe_print("❌ No paused download found", "yellow")
            time.sleep(1)
            continue
        elif key == "4":
            if confirm_action(f"Delete '{book_name}'?"):
                del progress["books"][book_name]
                save_progress(progress)
                return "deleted", None
        elif key == "5":
            ch = _show_chapter_list(book_name, progress)
            if ch > 0:
                return "chapter", ch
        elif key == "r":
            metadata = fetch_book_metadata(book.get('current_url'))
            if metadata:
                book['metadata'] = metadata
                save_progress(progress)
                safe_print("✅ Metadata refreshed.", "green")
            else:
                safe_print("❌ Could not refresh metadata.", "red")
            time.sleep(1)
            continue

    return None, None


def notify_new_chapters(book_name, count):
    try:
        if sys.platform == "linux":
            subprocess.run(["notify-send", "📚 Legion: New Chapters",
                            f"{book_name} has {count} new chapter{'s' if count>1 else ''}.",
                            "--icon=accessories-text-editor"], timeout=2)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{book_name} has {count} new chapters." with title "Legion"'],
                           timeout=2)
    except Exception:
        pass


background_checking = False
checking_status = {"running": False, "message": "", "progress": 0}
background_thread = None
SHARED_STATUS_FILE = os.path.expanduser('~/.great_sage_background_status.json')


def background_check_chapters(books: dict):
    global background_checking, checking_status, background_thread
    if background_checking:
        return
    background_checking = True
    checking_status = {"running": True, "message": "Starting chapter check...", "progress": 0}

    def check_worker():
        global checking_status, background_checking
        total_books = len(books)
        completed = 0
        for name, book in books.items():
            checking_status["message"] = f"Checking {name}..."
            checking_status["progress"] = int((completed / total_books) * 100) if total_books > 0 else 0
            count = check_for_new_chapters(book)
            books[name]["new_chapters_waiting"] = count
            if count > 0:
                notify_new_chapters(name, count)
            completed += 1
            try:
                with open(SHARED_STATUS_FILE, 'w') as f:
                    json.dump(checking_status, f)
            except Exception:
                pass
        checking_status["message"] = "Chapter check complete!"
        checking_status["progress"] = 100
        total_new = sum(book.get("new_chapters_waiting", 0) for book in books.values())
        if total_new > 0:
            notify_new_chapters("Library", total_new)
        time.sleep(1)
        checking_status["running"] = False
        background_checking = False
        try:
            with open(SHARED_STATUS_FILE, 'w') as f:
                json.dump(checking_status, f)
        except Exception:
            pass

    background_thread = threading.Thread(target=check_worker, daemon=True)
    background_thread.start()


def get_shared_background_status():
    try:
        if os.path.exists(SHARED_STATUS_FILE):
            with open(SHARED_STATUS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {"running": False, "message": "", "progress": 0}


def stop_background_checking():
    global background_checking, checking_status, background_thread
    if background_checking and background_thread:
        background_checking = False
        checking_status["running"] = False
        checking_status["message"] = "Background check stopped"
        checking_status["progress"] = 0


def get_checking_status():
    return checking_status.copy()


def check_for_new_chapters(book: dict) -> int:
    next_url = book.get("next_url")
    if not next_url:
        return 0
    count = 0
    url = next_url
    seen = set()
    while url and url not in seen:
        seen.add(url)
        plugin = plugin_registry.for_url(url)
        if plugin:
            scraper = SCRAPER if plugin.supports_cloudflare else None
            try:
                result = plugin.fetch_chapter(url, SESSION, scraper)
                if result.error or not result.next_url:
                    break
                count += 1
                url = result.next_url
                continue
            except Exception:
                break

        # Fallback
        try:
            resp, actual_url = _generic_get_with_retry(url)
            soup = BeautifulSoup(resp.text, "html.parser")
            content_div = None
            for selector in ["chr-content", "chapter-content", "content"]:
                content_div = soup.find("div", id=selector)
                if content_div:
                    break
            if not content_div:
                break
            count += 1
            parsed = urlparse(actual_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            url = _extract_nav(soup, "next_chap", base)
        except Exception:
            break
    return count


def word_count(paragraphs):
    return sum(len(p.split()) for p in paragraphs)


def reading_time_str(words):
    minutes = max(1, round(words / 238))
    return f"{minutes} min read" if minutes < 60 else f"{minutes // 60}h {minutes % 60}m read"


def get_terminal_size():
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return 80, 24


def get_wrap_width():
    cols, _ = get_terminal_size()
    progress = load_progress()
    max_width = progress.get("font_size", FONT_SIZE_DEFAULT)
    return min(cols - 8, max_width)


def get_page_height():
    _, lines = get_terminal_size()
    return lines - 8


def paginate_rich(paragraphs, wrap_width):
    indent = "    "
    page_height = get_page_height()
    progress = load_progress()
    line_spacing = progress.get("line_spacing", 0)
    pages, current_page, current_height = [], [], 0
    for i, para in enumerate(paragraphs):
        wrapped_lines = textwrap.fill(para, width=wrap_width,
                                      initial_indent=indent, subsequent_indent="").splitlines()
        block = []
        if i > 0:
            block.append(("divider", DIVIDERS[i % len(DIVIDERS)], wrap_width))
        block.append(("text", "\n".join(wrapped_lines)))
        if line_spacing > 0:
            block.append(("text", "\n" * line_spacing))
        block_height = sum(len(wrapped_lines) + 1 if item[0] == "text" else 3 for item in block) + line_spacing
        if current_height + block_height > page_height and current_page:
            pages.append(current_page)
            current_page = block[:]
            current_height = block_height
        else:
            current_page.extend(block)
            current_height += block_height
    if current_page:
        pages.append(current_page)
    return pages


def paginate_plain(paragraphs, wrap_width):
    indent = "    "
    page_height = get_page_height()
    progress = load_progress()
    line_spacing = progress.get("line_spacing", 0)
    pages, current_page, current_height = [], [], 0
    for i, para in enumerate(paragraphs):
        wrapped = textwrap.fill(para, width=wrap_width, initial_indent=indent, subsequent_indent="")
        lines = wrapped.splitlines()
        div_lines = ["", f"{DIVIDERS[i % len(DIVIDERS)]:^{wrap_width}}", ""] if i > 0 else []
        block = div_lines + lines
        if line_spacing > 0:
            block.extend([""] * line_spacing)
        if current_height + len(block) > page_height and current_page:
            pages.append(current_page)
            current_page = block[:]
            current_height = len(block)
        else:
            current_page.extend(block)
            current_height += len(block)
    if current_page:
        pages.append(current_page)
    return pages


def render_progress_bar(page, total, width):
    filled = int((page / total) * width) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)


def render_header_rich(title, page, total_pages, words, read_time):
    width = get_wrap_width() + 8
    bar = render_progress_bar(page, total_pages, width - 4)
    console.print()
    console.print(Panel(
        f"[title]{title}[/title]\n"
        f"[secondary]{words:,} words  ·  {read_time}  ·  Page {page}/{total_pages}[/secondary]",
        border_style="panel",
        box=rich_box.HEAVY if IS_KITTY else rich_box.ROUNDED,
        width=width,
    ))
    console.print(f"[progress]{bar}[/progress]")
    console.print()


def render_page_rich(lines):
    for item in lines:
        if item[0] == "divider":
            _, divider, wrap_width = item
            console.print(f"\n[divider]{' ' * (wrap_width // 2)}{divider}[/divider]\n")
        else:
            console.print(Text(item[1], style="primary"), soft_wrap=False)


def display_chapter_paged(title, paragraphs, chapter_num=1) -> str:
    global current_theme
    wrap_width = get_wrap_width()
    words = word_count(paragraphs)
    read_time = reading_time_str(words)
    pages = paginate_rich(paragraphs, wrap_width) if RICH else paginate_plain(paragraphs, wrap_width)
    total_pages = len(pages)
    page_idx = 0

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        if RICH:
            render_header_rich(title, page_idx + 1, total_pages, words, read_time)
            render_page_rich(pages[page_idx])
            console.print()
            cols, _ = get_terminal_size()
            console.print(f"[faint]{'─' * (cols - 2)}[/faint]")
            hints = []
            if page_idx < total_pages - 1:
                hints.append("[accent]SPACE[/accent] next page")
            if page_idx > 0:
                hints.append("[accent]←[/accent] prev page")
            if page_idx == total_pages - 1:
                hints.append("[accent]n[/accent] next ch")
            hints += ["[accent]p[/accent] prev ch", "[accent]b[/accent] menu",
                      "[accent]t[/accent] theme", "[accent]+[/accent] bigger", "[accent]-[/accent] smaller",
                      "[accent][[/accent] less space", "[accent]][/accent] more space", "[accent]q[/accent] quit"]
            console.print(f"\n  [secondary]{' · '.join(hints)}[/secondary]\n")
        else:
            cols, _ = get_terminal_size()
            width = min(cols, 92)
            print(f"\n  {title}")
            print(f"  {words:,} words · {read_time} · Page {page_idx+1}/{total_pages}")
            print(f"  [{render_progress_bar(page_idx + 1, total_pages, width - 4)}]\n")
            for line in pages[page_idx]:
                print(line)
            print("\n" + "─" * width)
            print("\n  SPACE next · ← prev · n next ch · p prev ch · b menu · t theme · + bigger · - smaller · [ less space · ] more space · q quit\n")

        if READCHAR:
            key = readchar.readkey()
            key_lower = key.lower() if isinstance(key, str) else key
        else:
            key = input("  > ").strip().lower()
            key_lower = key
            if key_lower == "oblivion":
                return "q"

        if READCHAR and key == readchar.key.UP:
            if page_idx > 0:
                page_idx -= 1
        elif READCHAR and key == readchar.key.DOWN:
            if page_idx < total_pages - 1:
                page_idx += 1
            else:
                return "n"
        elif key_lower in (" ", "\r", "\n") or (READCHAR and key == readchar.key.RIGHT):
            if page_idx < total_pages - 1:
                page_idx += 1
            else:
                return "n"
        elif READCHAR and key == readchar.key.LEFT:
            if page_idx > 0:
                page_idx -= 1
        elif key_lower == "n":
            return "n"
        elif key_lower == "p":
            return "p"
        elif key_lower == "b":
            return "b"
        elif key_lower == "t":
            current_theme = "light" if current_theme == "dark" else "dark"
            make_console()
        elif key_lower in ("+", "="):
            progress = load_progress()
            current_font = progress.get("font_size", FONT_SIZE_DEFAULT)
            new_font = min(get_terminal_size()[0] - 8, current_font + 10)
            progress["font_size"] = new_font
            save_progress(progress)
            wrap_width = get_wrap_width()
            pages = paginate_rich(paragraphs, wrap_width) if RICH else paginate_plain(paragraphs, wrap_width)
            total_pages = len(pages)
            page_idx = min(page_idx, total_pages - 1)
        elif key_lower == "-":
            progress = load_progress()
            current_font = progress.get("font_size", FONT_SIZE_DEFAULT)
            new_font = max(40, current_font - 10)
            progress["font_size"] = new_font
            save_progress(progress)
            wrap_width = get_wrap_width()
            pages = paginate_rich(paragraphs, wrap_width) if RICH else paginate_plain(paragraphs, wrap_width)
            total_pages = len(pages)
            page_idx = min(page_idx, total_pages - 1)
        elif key_lower == "[":
            progress = load_progress()
            current_spacing = progress.get("line_spacing", 0)
            progress["line_spacing"] = max(0, current_spacing - 1)
            save_progress(progress)
            wrap_width = get_wrap_width()
            pages = paginate_rich(paragraphs, wrap_width) if RICH else paginate_plain(paragraphs, wrap_width)
            total_pages = len(pages)
            page_idx = min(page_idx, total_pages - 1)
        elif key_lower == "]":
            progress = load_progress()
            current_spacing = progress.get("line_spacing", 0)
            progress["line_spacing"] = current_spacing + 1
            save_progress(progress)
            wrap_width = get_wrap_width()
            pages = paginate_rich(paragraphs, wrap_width) if RICH else paginate_plain(paragraphs, wrap_width)
            total_pages = len(pages)
            page_idx = min(page_idx, total_pages - 1)
        elif key_lower == "q" or (READCHAR and key == readchar.key.CTRL_C):
            return "q"


def show_stats(books):
    os.system("cls" if os.name == "nt" else "clear")
    if RICH:
        console.print()
        console.print(Panel("[title]  Reading Stats[/title]", border_style="panel",
                            box=rich_box.HEAVY if IS_KITTY else rich_box.ROUNDED, width=60))
        console.print()
        table = Table(show_header=True, header_style="accent",
                      box=rich_box.SIMPLE_HEAVY if IS_KITTY else rich_box.SIMPLE, padding=(0, 2))
        table.add_column("Book")
        table.add_column("Chapters", justify="right")
        table.add_column("Words Read", justify="right")
        table.add_column("Time Spent", justify="right")
        total_ch = total_words = total_mins = 0
        for name, book in books.items():
            ch = book.get("chapters_read", 0)
            w  = book.get("words_read", 0)
            m  = book.get("minutes_read", 0)
            total_ch += ch; total_words += w; total_mins += m
            m_str = f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"
            table.add_row(name, str(ch), f"{w:,}", m_str)
        console.print(table)
        console.print(Rule(style="faint"))
        total_m_str = f"{total_mins // 60}h {total_mins % 60}m" if total_mins >= 60 else f"{total_mins}m"
        console.print(f"  [secondary]Total:[/secondary]  [stat]{total_ch} chapters[/stat]  ·  "
                      f"[stat]{total_words:,} words[/stat]  ·  [stat]{total_m_str}[/stat]\n")
    else:
        print("\n=== Reading Stats ===\n")
        for name, book in books.items():
            ch = book.get("chapters_read", 0)
            w  = book.get("words_read", 0)
            m  = book.get("minutes_read", 0)
            print(f"  {name}: {ch} chapters · {w:,} words · {m // 60}h {m % 60}m")
        print()
    input("  Press Enter to go back...")


def splash():
    os.system("cls" if os.name == "nt" else "clear")
    if RICH:
        cols, _ = get_terminal_size()
        w = min(cols - 4, 70)
        console.print()
        console.print("[glow]╭────────────────────────────────────────────────────────────╮[/glow]")
        console.print("[glow]│[/glow][neon]  ⟡  LEGION  ⟡  [/neon][glow]│[/glow]")
        console.print("[glow]│[/glow][title]  Novel Reader  [/title][glow]│[/glow]")
        console.print("[glow]│[/glow][secondary]  ✦ Enhanced Visual Experience ✦  [/secondary][glow]│[/glow]")
        console.print("[glow]╰────────────────────────────────────────────────────────────╯[/glow]")
        console.print()
        console.print(Panel(
            f"[neon]⚡ Theme: {current_theme.upper()} ⚡[/neon]\n[secondary]Press [t] to cycle themes[/secondary]",
            border_style="accent",
            box=rich_box.HEAVY if IS_KITTY else rich_box.ROUNDED,
            width=w,
        ))
        console.print()
    else:
        print("\n=== LEGION — Novel Reader (Enhanced) ===\n")
        print(f"Theme: {current_theme.upper()} | Press [t] to cycle")


def book_menu(progress):
    global current_theme
    books = progress.get("books", {})

    while True:
        splash()

        if not books:
            if RICH:
                console.print("[secondary]No books saved yet.[/secondary]\n")
            else:
                print("No books saved yet.\n")
        else:
            if RICH:
                table = Table(show_header=True, header_style="accent",
                              box=rich_box.SIMPLE_HEAVY if IS_KITTY else rich_box.SIMPLE, padding=(0, 2))
                table.add_column("#", style="secondary", width=4)
                table.add_column("Title", style="primary")
                table.add_column("Last Read", style="secondary")
                table.add_column("Read", style="secondary", justify="right")
                table.add_column("DL", style="accent", justify="center", width=8)
                table.add_column("New", style="stat", justify="right")
                for i, (name, book) in enumerate(books.items(), 1):
                    new_ch = book.get("new_chapters_waiting", 0)
                    dl_status = book.get('download_state', {}).get('status', 'idle')
                    dl_icon = {'downloading': '⏳', 'completed': '✅', 'paused': '⏸️',
                               'queued': '⌛', 'failed': '❌', 'cancelled': '✖️'}.get(dl_status, '')
                    table.add_row(str(i), name, book.get('last_title', 'Not started'),
                                  str(book.get('chapters_read', 0)), dl_icon,
                                  f"+{new_ch}" if new_ch > 0 else "—")
                console.print(table)
                console.print()
                status = get_checking_status()
                if status["running"]:
                    console.print(f"[cyan]🔄 {status['message']} ({status['progress']}%)[/cyan]")
                else:
                    console.print("[secondary]  [#] Open  •  [c] Check new  •  [a] Add  •  [d] Delete  •  [x] Download  •  [s] Stats  •  [t] Theme  •  [q] Quit[/secondary]\n")
            else:
                for i, (name, book) in enumerate(books.items(), 1):
                    new_ch = book.get("new_chapters_waiting", 0)
                    tag = f"  (+{new_ch} new!)" if new_ch > 0 else ""
                    print(f"  {i}. {name} — {book.get('last_title', 'Not started')}{tag}")
                print("\n  [#] Open · [c] Check new · [a] Add · [d] Delete · [x] Download · [s] Stats · [t] Theme · [q] Quit\n")

        if READCHAR:
            key = readchar.readkey().lower()
        else:
            key = input("  Choice: ").strip().lower()

        if key.isdigit():
            idx = int(key) - 1
            names = list(books.keys())
            if 0 <= idx < len(names):
                name = names[idx]
                action, val = show_book_details(name, books[name], progress)
                if action == "read":
                    return name, books[name]["current_url"]
                elif action == "chapter":
                    # Jump directly to a specific chapter from chapter list
                    _read_loop(name, books[name].get("current_url", ""), progress, start_chapter=val)
                    continue
                elif action == "deleted":
                    continue
        elif key == "a":
            url = input("  Chapter URL: ").strip()
            if not url or url.lower() == 'q':
                continue
            print("  Fetching book title...")
            suggested = extract_book_title_from_chapter(url)
            if suggested:
                print(f"  Suggested title: {suggested}")
                name = input("  Use this title? (Enter to accept, or type a new one, or 'q' to cancel): ").strip()
                if name.lower() == 'q':
                    continue
                if not name:
                    name = suggested
            else:
                name = input("  Book title (or 'q' to cancel): ").strip()
                if name.lower() == 'q' or not name:
                    continue
            if name and url:
                print("  Fetching book metadata...")
                metadata = fetch_book_metadata(url)
                entry = {
                    "current_url": url, "next_url": None, "last_title": "Not started",
                    "new_chapters_waiting": 0, "chapters_read": 0, "words_read": 0, "minutes_read": 0,
                    "book_title": name, "metadata": metadata or {},
                    "download_state": {
                        "status": "idle", "last_downloaded_chapter": None,
                        "last_downloaded_chapter_num": 0, "total_chapters_downloaded": 0,
                        "download_path": None, "failed_chapters": [],
                        "timestamp": None, "pause_requested": False
                    }
                }
                books[name] = entry
                progress["books"] = books
                save_progress(progress)
                if RICH:
                    console.print(f"[green]✅ Added '{name}'[/green]")
                else:
                    print(f"✅ Added '{name}'")
                time.sleep(1)
            continue
        elif key == "d":
            if not books:
                continue
            if RICH:
                console.print("\n[secondary]Current books:[/secondary]")
                for i, name in enumerate(books.keys(), 1):
                    console.print(f"  {i}. {name}")
            else:
                print("\nCurrent books:")
                for i, name in enumerate(books.keys(), 1):
                    print(f"  {i}. {name}")
            num_str = input("  Enter number to delete (0 to cancel): ").strip()
            if not num_str:
                continue
            try:
                num = int(num_str)
            except ValueError:
                safe_print("Invalid number.", "red")
                time.sleep(1)
                continue
            if num == 0:
                continue
            if 1 <= num <= len(books):
                name = list(books.keys())[num-1]
                if confirm_action(f"Delete '{name}'?"):
                    del books[name]
                    progress["books"] = books
                    save_progress(progress)
                    safe_print(f"✅ Deleted '{name}'.", "green")
                else:
                    safe_print("Cancelled.", "dim")
            else:
                safe_print("Invalid number.", "red")
            time.sleep(1)
            continue
        elif key == "c":
            if not books:
                continue
            background_check_chapters(books)
            if RICH:
                console.print("\n[cyan]🔍 Background chapter check started![/cyan]")
            else:
                print("\n🔍 Background chapter check started!")
            input("\n  Press Enter to continue...")
        elif key == "x":
            name = input("  Book title to download: ").strip()
            if name in books:
                start_url = books[name].get("current_url")
                if start_url:
                    download_book(name, start_url)
                else:
                    print("No URL available for this book.")
            else:
                print(f"Book '{name}' not found.")
        elif key == "s":
            show_stats(books)
        elif key == "t":
            current_theme = "light" if current_theme == "dark" else "dark"
            progress["theme"] = current_theme
            save_progress(progress)
            make_console()
        elif key in ("q", "oblivion"):
            return None, None


def _get_chapter_list_from_file(book_name: str) -> list:
    """
    Return a list of (chapter_num, title) tuples from the local .txt file.
    Used for the chapter list picker.
    """
    try:
        save_path  = get_book_path(book_name)
        if not os.path.exists(save_path):
            return []
        with open(save_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        # File format: ====header====  body  ====header====  body ...
        # After splitting on ===, blocks alternate: [junk, header, body, header, body, ...]
        # Only odd-indexed blocks are chapter headers — skip even (body) blocks
        # to avoid matching chapter references inside story text.
        blocks = re.split(r"={50,}", raw)
        seen   = {}
        for i, block in enumerate(blocks):
            if i % 2 == 0:
                continue   # even = body content, skip
            m = re.match(r"\s*Chapter\s+(\d+)\s*[:\-]?\s*(.*)", block.strip(), re.IGNORECASE)
            if m:
                num = int(m.group(1))
                if num not in seen:
                    seen[num] = m.group(2).strip() or f"Chapter {num}"
        return sorted(seen.items(), key=lambda x: x[0])
    except Exception:
        return []


def _show_chapter_list(book_name: str, progress: dict) -> int:
    """
    Show a paginated list of locally cached chapters.
    Returns the chapter number the user picks, or 0 to cancel.

    Controls:
      [n]/[p]  — page through the list
      [/]      — search: type a chapter number to jump directly (e.g. /200)
      [q]      — cancel
    """
    chapters = _get_chapter_list_from_file(book_name)
    if not chapters:
        if RICH:
            console.print("\n[secondary]No chapters cached locally yet.[/secondary]")
        else:
            print("\nNo chapters cached locally yet.")
        input("  Press Enter to continue...")
        return 0

    chapter_nums = {num for num, _ in chapters}  # fast lookup set
    PAGE = 20
    page = 0
    total_pages = max(1, (len(chapters) + PAGE - 1) // PAGE)
    status_msg  = ""  # feedback line shown below nav

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        start   = page * PAGE
        end     = min(start + PAGE, len(chapters))
        pg_info = f"  ({page+1}/{total_pages})" if total_pages > 1 else ""

        if RICH:
            console.print(f"\n[title]  Chapter List — {book_name}[/title][faint]{pg_info}[/faint]\n")
            for num, title in chapters[start:end]:
                console.print(f"  [secondary]Ch.{num:<6}[/secondary] {title[:60]}")
            console.print()
            nav = []
            if page > 0:               nav.append(r"\[p] prev")
            if page < total_pages - 1: nav.append(r"\[n] next")
            nav += [r"\[/] go to chapter", r"\[q] cancel"]
            console.print(f"  [faint]{'  ·  '.join(nav)}[/faint]")
            if status_msg:
                console.print(f"  [err]{status_msg}[/err]")
            console.print()
        else:
            print(f"\n  Chapter List — {book_name}{pg_info}\n")
            for num, title in chapters[start:end]:
                print(f"  Ch.{num:<6} {title[:60]}")
            nav = []
            if page > 0:               nav.append("p=prev")
            if page < total_pages - 1: nav.append("n=next")
            nav += ["/=go to ch", "q=cancel"]
            print("\n  " + "  ·  ".join(nav))
            if status_msg:
                print(f"  {status_msg}")

        status_msg = ""  # clear after display

        if READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  > ").strip().lower()

        if key == "q":
            return 0

        elif key == "n" and page < total_pages - 1:
            page += 1

        elif key == "p" and page > 0:
            page -= 1

        elif key == "/":
            # Direct chapter jump — prompt for number
            raw = input("  Go to chapter: ").strip()
            if raw.isdigit():
                target = int(raw)
                if target in chapter_nums:
                    return target
                else:
                    status_msg = f"Chapter {target} not cached locally."
            else:
                status_msg = "Enter a number."


def _read_loop(book_name: str, current_url: str, progress: dict, start_chapter: int = 0):
    """Inner reading loop — shared between Jump In and Bookmarks 'Read' action.
    start_chapter: if >0, jump directly to that chapter number.
    """
    # If jumping to a specific chapter, try loading it from file directly
    if start_chapter > 0:
        local_title, local_paragraphs = get_chapter_from_file(book_name, start_chapter)
        if local_title and local_paragraphs:
            book = progress["books"].get(book_name, {})
            chapter_start = time.time()
            words = word_count(local_paragraphs)
            action = display_chapter_paged(local_title, local_paragraphs, start_chapter)
            minutes_spent = max(1, round((time.time() - chapter_start) / 60))
            progress["books"][book_name]["chapters_read"] = start_chapter
            progress["books"][book_name]["words_read"]    = book.get("words_read", 0) + words
            progress["books"][book_name]["minutes_read"]  = book.get("minutes_read", 0) + minutes_spent
            save_progress(progress)
            if action == "n":
                # Continue with next chapter via URL
                pass
            elif action == "p":
                # Go back one chapter from file
                if start_chapter > 1:
                    _read_loop(book_name, current_url, progress, start_chapter - 1)
                return
            else:
                return

    while current_url:
        book        = progress["books"].get(book_name, {})
        # chapters_read stores the last chapter number that was read.
        # When opening a book, current chapter = chapters_read (resume it).
        # After reading, we increment to chapters_read + 1 for next chapter.
        # On very first open (chapters_read == 0), chapter_num starts at 1.
        chapter_num = max(1, book.get("chapters_read", 0))

        # ── Try local file first (offline-capable) ───────────────────────
        local_title, local_paragraphs = get_chapter_from_file(book_name, chapter_num)

        if local_title and local_paragraphs:
            title      = local_title
            paragraphs = local_paragraphs
            next_url   = book.get("next_url", current_url)
            prev_url   = book.get("prev_url", "")
            if RICH:
                console.print("\n[secondary]Loading chapter (offline)...[/secondary]")
            else:
                print("\nLoading chapter (offline)...")
        else:
            if RICH:
                console.print("\n[secondary]Loading chapter...[/secondary]")
            else:
                print("\nLoading chapter...")

            title, paragraphs, next_url, prev_url, error, _url_ch_num = fetch_chapter(current_url)

            if error:
                print(f"\nError: {error}")
                input("  Press Enter to go back...")
                break
            if not paragraphs:
                print(f"\nNo text found at {current_url}.")
                input("  Press Enter to go back...")
                break

        chapter_start = time.time()
        words = word_count(paragraphs)

        progress["books"][book_name].update({
            "current_url": current_url, "next_url": next_url,
            "prev_url":    prev_url or "",
            "last_title":  title, "new_chapters_waiting": 0,
        })
        save_progress(progress)

        action = display_chapter_paged(title, paragraphs, chapter_num)

        minutes_spent = max(1, round((time.time() - chapter_start) / 60))
        # Save current chapter number as chapters_read
        progress["books"][book_name]["chapters_read"] = chapter_num
        progress["books"][book_name]["words_read"]    = book.get("words_read", 0) + words
        progress["books"][book_name]["minutes_read"]  = book.get("minutes_read", 0) + minutes_spent
        save_progress(progress)

        last_saved = progress["books"][book_name].get("last_saved_chapter_num", 0)
        if chapter_num > last_saved:
            try:
                append_chapter_to_file(book_name, chapter_num, title, paragraphs)
                progress["books"][book_name]["last_saved_chapter_num"] = chapter_num
                save_progress(progress)
            except Exception:
                pass

        if action == "n" and next_url:
            # Advance chapter counter for next chapter
            progress["books"][book_name]["chapters_read"] = chapter_num + 1
            save_progress(progress)
            current_url = next_url
        elif action == "p":
            if prev_url:
                # Step back chapter counter
                progress["books"][book_name]["chapters_read"] = max(1, chapter_num - 1)
                save_progress(progress)
                current_url = prev_url
            else:
                # No prev URL — go back to book details
                break
        elif action == "b":
            # [b] returns to book details
            break
        else:
            if RICH:
                console.print("\n[secondary]Progress saved.[/secondary]\n")
            else:
                print("\nProgress saved.")
            break


def show_main_menu(progress: dict):
    """Top-level Legion menu: Jump In or Bookmarks."""
    while True:
        # Sync Jump In books into Reading list automatically
        sync_reading_from_jumpin(progress)

        os.system("cls" if os.name == "nt" else "clear")
        if RICH and console:
            cols, _ = get_terminal_size()
            w = min(cols - 4, 70)
            console.print()
            console.print("[glow]╭────────────────────────────────────────────────────────────╮[/glow]")
            console.print("[glow]│[/glow][neon]  ⟡  LEGION  ⟡  [/neon][glow]│[/glow]")
            console.print("[glow]│[/glow][title]  Novel Reader  [/title][glow]│[/glow]")
            console.print("[glow]╰────────────────────────────────────────────────────────────╯[/glow]")
            console.print()
            bm = load_bookmarks()
            total_bm = sum(len(bm.get(k, [])) for k in ("planning","reading","dropped","completed"))
            jmp_count = len(progress.get("books", {}))
            console.print(f"  [secondary]1[/secondary]  [primary]Jump In[/primary]  [faint]({jmp_count} books)[/faint]")
            console.print(f"  [secondary]2[/secondary]  [primary]Bookmarks[/primary]  [faint]({total_bm} entries)[/faint]")
            console.print()
            console.print("[secondary]  [1] Jump In  ·  [2] Bookmarks  ·  [q] Quit[/secondary]\n")
        else:
            print("\n=== LEGION — Novel Reader ===\n")
            jmp_count = len(progress.get("books", {}))
            print(f"  1. Jump In  ({jmp_count} books)")
            print(f"  2. Bookmarks")
            print("\n  [1/2/q]: ", end="")

        if READCHAR:
            key = readchar.readkey()
            key = key.lower() if isinstance(key, str) else key
        else:
            key = input("  Choice: ").strip().lower()

        if key == "q":
            return

        elif key == "1":
            # Jump In — existing book_menu flow
            book_name, current_url = book_menu(progress)
            if book_name and current_url:
                _read_loop(book_name, current_url, progress)

        elif key == "2":
            # Bookmarks
            result_title, result_url = show_bookmarks_menu(progress)
            if result_title and result_url:
                # User picked "Read" from inside bookmarks
                # Ensure the book is in Jump In
                if result_title not in progress.get("books", {}):
                    progress["books"][result_title] = {
                        "current_url": result_url, "next_url": None,
                        "last_title": "Not started",
                        "new_chapters_waiting": 0, "chapters_read": 0,
                        "words_read": 0, "minutes_read": 0,
                        "book_title": result_title, "metadata": {},
                        "download_state": {
                            "status": "idle", "last_downloaded_chapter": None,
                            "last_downloaded_chapter_num": 0,
                            "total_chapters_downloaded": 0,
                            "download_path": None, "failed_chapters": [],
                            "timestamp": None, "pause_requested": False
                        }
                    }
                    save_progress(progress)
                _read_loop(result_title, result_url, progress)


def main():
    global current_theme
    progress = load_progress()
    if "books" not in progress:
        progress["books"] = {}
    current_theme = progress.get("theme", "dark")
    make_console()
    show_main_menu(progress)


if __name__ == "__main__":
    def _handle_signal(sig, frame):
        print("\n\nShutdown signal received. Progress was already saved.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        signal.signal(signal.SIGHUP, _handle_signal)
    except AttributeError:
        pass

    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted. Progress was saved.")
        sys.exit(0)
