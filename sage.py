#!/usr/bin/env python3
"""
Great Sage — Sage
AI Recommendation Engine powered by Groq Cloud.
Reads from both Legion and Matrix data to build a unified taste profile
and generate cross-media recommendations.

Features:
  - Novel, show, mood, similar, what's next, quick pick recommendations
  - Add any recommendation directly to Matrix watchlist
  - "Explain why I'd like this" — analyse any title against your profile
  - AI chapter summaries — catch up on a novel you've been away from
  - Watchlist prioritiser — rank your unwatched queue by fit
  - Continue-watching awareness — Sage factors in what you're mid-way through
  - Free-form chat with Sage
"""

import json
import os
import re
import sys
import time
import textwrap
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log as _gs_log
    log = _gs_log.sage
except Exception:
    class _NoopLog:
        def __getattr__(self, name): return lambda *a, **kw: None
    log = _NoopLog()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.text import Text
    from rich.table import Table
    from rich.theme import Theme
    from rich.style import Style
    from rich import box as rich_box
    from rich.spinner import Spinner
    from rich.live import Live
    RICH = True
except ImportError:
    RICH = False

try:
    import readchar
    READCHAR = True
except ImportError:
    READCHAR = False

# ── Config ────────────────────────────────────────────────────────────────────

GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
GROQ_BASE        = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"
REQUEST_TIMEOUT  = 120
TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY")
TAVILY_API       = "https://api.tavily.com/search"

# Data file paths (must match legion.py and matrix.py)
# ── Core Imports & Paths ───────────────────────────────────────────────────────
try:
    from great_sage_core import (
        LEGION_PROGRESS, MATRIX_PROGRESS, LEGION_BOOKMARKS,
        load_json, save_json
    )
except ImportError:
    # Fallback paths if core isn't found (for standalone CLI runs)
    LEGION_PROGRESS  = os.path.expanduser("~/.great_sage_legion.json")
    MATRIX_PROGRESS  = os.path.expanduser("~/.config/matrix/progress.json")
    LEGION_BOOKMARKS = os.path.expanduser("~/.great_sage_bookmarks.json")
    def load_json(path, default=None):
        try:
            if os.path.exists(path):
                with open(path, "r") as f: return json.load(f)
        except Exception: pass  # Ignored
        return default or {}
    def save_json(path, data):
        try:
            with open(path, "w") as f: json.dump(data, f, indent=2)
        except Exception: pass  # Ignored

SAGE_CACHE_FILE  = os.path.expanduser("~/.great_sage_sage_cache.json")
SAGE_SEEN_FILE   = os.path.expanduser("~/.great_sage_seen_recs.json")
MATRIX_SYNC_CFG  = os.path.expanduser("~/.config/matrix/sync_config.json")

# Legion saves book .txt files in os.getcwd() — wherever it was launched from.
# We search all likely locations including the script's own directory.
def _build_search_dirs() -> list:
    dirs = [
        os.path.dirname(os.path.abspath(__file__)),   # same folder as sage.py (most likely)
        os.getcwd(),                                   # current working directory
        os.path.expanduser("~/Documents/Great Sage"),
        os.path.expanduser("~/Documents/great_sage"),
        os.path.expanduser("~/Great Sage"),
        os.path.expanduser("~/great_sage"),
        os.path.expanduser("~/Great_Sage"),
        os.path.expanduser("~/greatsage"),
        os.path.expanduser("~"),
    ]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result

LEGION_BOOK_SEARCH_DIRS = _build_search_dirs()

# ── Theme ─────────────────────────────────────────────────────────────────────

SAGE_THEME = Theme({
    "primary":   Style(color="#E8DCC8", bold=True),
    "secondary": Style(color="#A09080"),
    "accent":    Style(color="#C8A96E", bold=True),
    "faint":     Style(color="#5A5048"),
    "title":     Style(color="#F0D090", bold=True),
    "glow":      Style(color="#FFD700", bold=True),
    "neon":      Style(color="#00FFFF", bold=True),
    "stat":      Style(color="#7EC8A0", bold=True, italic=True),
    "warn":      Style(color="#FFB347", bold=True),
    "ok":        Style(color="#7EC8A0", bold=True),
    "err":       Style(color="#FF6B6B", bold=True),
    "panel":     Style(color="#C8A96E"),
    "divider":   Style(color="#6A5A4A"),
    "novel":     Style(color="#A8D8EA", bold=True),
    "media":     Style(color="#FFB6C1", bold=True),
    "number":    Style(color="#C8A96E", bold=True),
})

console = Console(theme=SAGE_THEME) if RICH else None


# ── Data loading ──────────────────────────────────────────────────────────────

def load_seen_recs() -> list:
    return load_json(SAGE_SEEN_FILE, [])

def save_seen_recs(seen: list):
    save_json(SAGE_SEEN_FILE, seen[-100:])

def add_seen_recs(titles: list):
    seen = load_seen_recs()
    seen_lower = [s.lower() for s in seen]
    for t in titles:
        t = t.strip()
        if not t:
            continue
        t_lower = t.lower()
        # Skip if exact match or if this title is a substring of (or contains) an existing entry
        # This catches "Demon Slayer" vs "Demon Slayer: Kimetsu no Yaiba" mismatches
        already = any(
            t_lower == s or t_lower in s or s in t_lower
            for s in seen_lower
        )
        if not already:
            seen.append(t)
            seen_lower.append(t_lower)
    save_seen_recs(seen)

def get_legion_data() -> dict:
    return load_json(LEGION_PROGRESS, {"books": {}})

def get_matrix_data() -> dict:
    return load_json(MATRIX_PROGRESS, {
        "watchlist": {"planning": [], "watching": [], "dropped": [], "completed": []},
        "watching": {}, "completed": {}})

def save_matrix_data(data: dict) -> bool:
    save_json(MATRIX_PROGRESS, data)
    return True

def get_bookmarks_data() -> dict:
    return load_json(LEGION_BOOKMARKS, {"planning": [], "reading": [], "dropped": [], "completed": []})


def save_bookmarks_data(data: dict) -> bool:
    save_json(LEGION_BOOKMARKS, data)
    return True


def _get_matrix_watchlist(data: dict) -> dict:
    """Return the 4-list watchlist dict, handling both old (list) and new (dict) formats."""
    watchlist = data.get("watchlist", {})
    if isinstance(watchlist, list):
        # Old flat list — treat all as planning
        return {"planning": watchlist, "watching": [], "dropped": [], "completed": []}
    for key in ("planning", "watching", "dropped", "completed"):
        watchlist.setdefault(key, [])
    return watchlist


def all_listed_titles() -> set:
    """
    Return lowercase set of every title already in any watchlist, bookmarks,
    or synced Trakt/AniList list — used to filter recommendations.
    """
    titles = set()
    # Matrix watchlist
    matrix_data = get_matrix_data()
    for wl_list in _get_matrix_watchlist(matrix_data).values():
        for entry in wl_list:
            title = entry.get("title", "") if isinstance(entry, dict) else str(entry)
            if title:
                titles.add(title.lower())
    # Also titles in continue-watching
    for title in matrix_data.get("watching", {}).keys():
        titles.add(title.lower())
    # Legion bookmarks
    bookmarks_data = get_bookmarks_data()
    for wl_list in bookmarks_data.values():
        for entry in wl_list:
            title = entry.get("title", "") if isinstance(entry, dict) else str(entry)
            if title:
                titles.add(title.lower())
    return titles


def load_external_profile() -> dict:
    """
    Pull extra taste data from Trakt/AniList sync config so Sage can factor
    in the user's full watch history even before it's been imported to Matrix.
    Returns {trakt_username, anilist_username} — actual fetching is lazy/cached.
    """
    try:
        if os.path.exists(MATRIX_SYNC_CFG):
            with open(MATRIX_SYNC_CFG, "r") as f:
                cfg = json.load(f)
            return {
                "trakt_username":   cfg.get("trakt_username", ""),
                "anilist_username": cfg.get("anilist_username", ""),
                "has_trakt":        bool(cfg.get("trakt_token")),
                "has_anilist":      bool(cfg.get("anilist_username")),
            }
    except Exception as e:
        log.warning("Failed to load external profile config", error=str(e))
    return {}


def add_to_matrix_watchlist_list(title: str, list_name: str) -> str:
    """
    Add a title to a specific Matrix watchlist sub-list.
    Returns 'added', 'duplicate', or 'error'.
    """
    matrix_data = get_matrix_data()
    watchlist = _get_matrix_watchlist(matrix_data)

    # Duplicate check across ALL lists
    for wl_list in watchlist.values():
        for entry in wl_list:
            if entry.get("title", "").lower() == title.lower():
                return "duplicate"

    # Remove from other lists if present
    for key in ("planning", "watching", "dropped", "completed"):
        if key == list_name:
            continue
        watchlist[key] = [e for e in watchlist[key] if e.get("title", "").lower() != title.lower()]

    watchlist[list_name].append({
        "title": title, "watched": list_name == "completed",
        "added": time.time(), "is_anime": False,
        "notes": "Added via Sage recommendation",
    })
    matrix_data["watchlist"] = watchlist
    ok = save_matrix_data(matrix_data)
    return "added" if ok else "error"


def add_to_matrix_watchlist(title: str) -> str:
    """Compat shim — adds to Planning."""
    return add_to_matrix_watchlist_list(title, "planning")


def add_to_legion_bookmarks(title: str, list_name: str) -> str:
    """
    Add a title to a Legion bookmarks sub-list (planning/reading/dropped/completed).
    No URL since this comes from Sage — user can add URL later in Legion.
    Returns 'added', 'duplicate', or 'error'.
    """
    bookmarks_data = get_bookmarks_data()

    # Duplicate check across all lists
    for wl_list in bookmarks_data.values():
        for entry in wl_list:
            t = entry.get("title", "") if isinstance(entry, dict) else str(entry)
            if t.lower() == title.lower():
                return "duplicate"

    # Remove from other lists
    for key in ("planning", "reading", "dropped", "completed"):
        if key == list_name:
            continue
        bookmarks_data[key] = [e for e in bookmarks_data.get(key, [])
                     if (e.get("title","") if isinstance(e,dict) else str(e)).lower() != title.lower()]

    bookmarks_data.setdefault(list_name, []).append({
        "title": title, "url": "",
        "metadata": {}, "added": time.time(),
        "notes": "Added via Sage recommendation — add URL in Legion",
    })
    ok = save_bookmarks_data(bookmarks_data)
    return "added" if ok else "error"


# In-memory cache for book file paths — avoids re-scanning large directories
_book_path_cache: dict[str, str] = {}


def find_book_txt(book_name: str) -> str | None:
    """
    Find the Legion .txt file for a book.
    Tries exact sanitised filename first, then fuzzy-matches all .txt files
    in each search directory by comparing normalised titles.
    """
    def sanitise(name: str) -> str:
        return re.sub(r"[^\w\-_\. ]", "_", name) + ".txt"

    def normalise(s: str) -> str:
        """Lowercase, strip punctuation and extra spaces for comparison."""
        s = s.lower()
        s = re.sub(r"[^\w\s]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    # Check cache first — validate the path still exists
    cached = _book_path_cache.get(book_name)
    if cached and os.path.exists(cached):
        return cached

    target_exact    = sanitise(book_name)
    target_norm     = normalise(book_name)
    # Also try without subtitle (everything before first colon/dash)
    short_name      = re.split(r"[:\-\u2013]", book_name)[0].strip()
    short_norm      = normalise(short_name)

    for directory in LEGION_BOOK_SEARCH_DIRS:
        if not os.path.isdir(directory):
            continue

        # 1. Exact sanitised match
        candidate = os.path.join(directory, target_exact)
        if os.path.exists(candidate):
            _book_path_cache[book_name] = candidate
            return candidate

        # 2. Case-insensitive exact match
        try:
            txt_files = [f for f in os.listdir(directory) if f.endswith(".txt")]
        except Exception:
            continue

        for f in txt_files:
            if f.lower() == target_exact.lower():
                result = os.path.join(directory, f)
                _book_path_cache[book_name] = result
                return result

        # 3. Fuzzy: normalise both sides and check if one contains the other
        for f in txt_files:
            f_norm = normalise(os.path.splitext(f)[0])
            # Full title match
            if target_norm in f_norm or f_norm in target_norm:
                result = os.path.join(directory, f)
                _book_path_cache[book_name] = result
                return result
            # Short name match (before colon/dash)
            if short_norm and len(short_norm) > 4:
                if short_norm in f_norm or f_norm.startswith(short_norm):
                    result = os.path.join(directory, f)
                    _book_path_cache[book_name] = result
                    return result

    return None


def parse_chapters(content: str) -> list[str]:
    """
    Heuristically parse a text file into chapters, handling arbitrary formatting.
    Fallback to the old ========= delimiter if heuristics fail.
    """
    chapters = []
    current_chapter = []
    
    for line in content.splitlines():
        stripped = line.strip()
        is_delimiter = bool(re.match(r'^[-=*#_~]{10,}$', stripped))
        is_chapter_title = bool(re.match(r'^(?:chapter|episode|prologue)\s*\d*', stripped, re.IGNORECASE)) and len(stripped) < 100
        
        if is_delimiter or is_chapter_title:
            if current_chapter:
                joined = "\n".join(current_chapter).strip()
                if len(joined) > 50:
                    chapters.append(joined)
                current_chapter = []
            if is_chapter_title:
                current_chapter.append(line)
        else:
            current_chapter.append(line)
            
    if current_chapter:
        joined = "\n".join(current_chapter).strip()
        if len(joined) > 50:
            chapters.append(joined)
            
    if len(chapters) < 2:
        chapters = [c.strip() for c in re.split(r"={50,}", content) if c.strip()]
        
    return chapters

def read_last_n_chapters(book_name: str, n: int = 5) -> str | None:
    """
    Read the last N chapters from a Legion book .txt file.
    Returns the raw text, or None if the file can't be found.
    """
    path = find_book_txt(book_name)
    if not path:
        return None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return None

    chapters = parse_chapters(content)

    if not chapters:
        return content[-8000:]

    last = chapters[-n:] if len(chapters) >= n else chapters
    return "\n\n".join(last)


def read_chapters_around(book_name: str, current_chapter: int, n: int = 5) -> str | None:
    """Read N chapters ending at current_chapter from a Legion book .txt file.
    Uses the reader's actual position, not the end of the file.
    Falls back to read_last_n_chapters if position can't be found."""
    path = find_book_txt(book_name)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return None

    chapters = parse_chapters(content)

    if not chapters:
        return content[-8000:]

    # Try to find the chapter index matching current_chapter number
    target_idx = None
    for i, ch in enumerate(chapters):
        # Look for "Chapter 184" or "Ch. 184" in the first 120 chars
        header = ch[:120]
        m = re.search(r"chapter\s+(\d+)", header, re.IGNORECASE)
        if m and int(m.group(1)) == current_chapter:
            target_idx = i
            break

    if target_idx is None:
        # Fallback: use chapter number as index (0-based)
        target_idx = min(current_chapter - 1, len(chapters) - 1)

    # Grab n chapters ending at target_idx
    start = max(0, target_idx - n + 1)
    selected = chapters[start: target_idx + 1]
    return "\n\n".join(selected)


# ── Profile builder ───────────────────────────────────────────────────────────

def build_profile() -> dict:
    legion_data  = get_legion_data()
    matrix_data  = get_matrix_data()
    profile = {
        "novels":    [],
        "watching":  [],
        "watchlist": [],
        "completed": [],
        "stats":     {},
    }

    # Legion: books
    books          = legion_data.get("books", {})
    total_chapters = 0
    total_minutes  = 0
    for name, book in books.items():
        chapters = book.get("chapters_read", 0)
        minutes  = book.get("minutes_read", 0)
        words    = book.get("words_read", 0)
        total_chapters += chapters
        total_minutes  += minutes
        meta   = book.get("metadata", {})
        genres = meta.get("genres", "")
        author = meta.get("author", "")
        status = meta.get("status", "")
        dl_ch  = book.get("download_state", {}).get("total_chapters_downloaded", 0)
        
        # Calculate velocity-based engagement score (1.0 to 10.0)
        # Baseline comparison: ~15 chapters per hour is very engaged (10/10)
        engagement = 5.0
        if minutes > 0 and chapters > 0:
            ch_per_hour = chapters / (minutes / 60.0)
            engagement = min(10.0, max(1.0, round((ch_per_hour / 15.0) * 10.0, 1)))
            
        profile["novels"].append({
            "title":               name,
            "chapters_read":       chapters,
            "words_read":          words,
            "hours_spent":         round(minutes / 60, 1),
            "engagement":          engagement,
            "genres":              genres,
            "author":              author,
            "status":              status,
            "downloaded_chapters": dl_ch,
        })

    profile["stats"]["total_chapters_read"] = total_chapters
    profile["stats"]["total_reading_hours"]  = round(total_minutes / 60, 1)

    # Matrix: currently watching (continue-watching progress)
    watching = matrix_data.get("watching", {})
    for title, item in watching.items():
        if isinstance(item, dict):
            season      = item.get("current_season",  item.get("season",  0))
            episode     = item.get("current_episode", item.get("episode", 0))
            total_eps   = item.get("total_episodes",  0)
            eps_watched = len(item.get("episodes_watched", []))
            is_anime    = item.get("is_anime", False)

            # Build a human-readable progress string
            parts = []
            if season and season > 0:
                parts.append(f"S{season:02d}")
            if episode and episode > 0:
                parts.append(f"E{episode:02d}")
            if total_eps and total_eps > 0 and eps_watched > 0:
                parts.append(f"({eps_watched}/{total_eps} eps this season)")
            elif total_eps and total_eps > 0:
                parts.append(f"(ep {episode} of {total_eps})")

            progress = " ".join(parts) if parts else "in progress"

            profile["watching"].append({
                "title":            title,
                "progress":         progress,
                "season":           season,
                "episode":          episode,
                "total_episodes":   total_eps,
                "episodes_watched": eps_watched,
                "is_anime":         is_anime,
            })
        else:
            profile["watching"].append({"title": title, "progress": "unknown"})

    # Matrix: watchlist sub-lists
    watchlist = _get_matrix_watchlist(matrix_data)
    for item in watchlist.get("planning", []):
        if isinstance(item, dict) and item.get("title"):
            profile["watchlist"].append(item["title"])
    for item in watchlist.get("watching", []):
        if isinstance(item, dict) and item.get("title"):
            t = item["title"]
            # Avoid duplicating what's already in continue-watching
            if not any(w["title"] == t for w in profile["watching"]):
                profile["watching"].append({"title": t, "progress": "unknown", "is_anime": item.get("is_anime", False)})
    for item in watchlist.get("completed", []):
        if isinstance(item, dict) and item.get("title"):
            profile["completed"].append(item["title"])
    # Legacy completed dict
    for title in matrix_data.get("completed", {}).keys():
        if title not in profile["completed"]:
            profile["completed"].append(title)

    # Legion bookmarks — add to profile so Sage understands reading taste better
    bookmarks_data = get_bookmarks_data()
    profile["bookmarks"] = {
        "planning":  [e.get("title","") for e in bookmarks_data.get("planning", []) if isinstance(e,dict)],
        "reading":   [e.get("title","") for e in bookmarks_data.get("reading", []) if isinstance(e,dict)],
        "completed": [e.get("title","") for e in bookmarks_data.get("completed", []) if isinstance(e,dict)],
    }

    profile["stats"]["shows_watching"]   = len(profile["watching"])
    profile["stats"]["watchlist_items"]  = len(profile["watchlist"])
    profile["stats"]["shows_completed"]  = len(profile["completed"])
    profile["stats"]["books_bookmarked"] = sum(len(v) for v in profile["bookmarks"].values())

    # External services info (for prompt context)
    external_profile = load_external_profile()
    profile["external"] = external_profile

    return profile


def profile_to_text(profile: dict) -> str:
    lines = ["=== My Media Profile ===\n"]

    if profile["novels"]:
        lines.append("NOVELS I'VE BEEN READING:")
        for n in profile["novels"]:
            parts = [f"  - {n['title']}"]
            
            engagement = n.get("engagement", 5.0)
            engagement_text = f" | Engagement: {engagement}/10 "
            if engagement >= 8.0:
                engagement_text += "(High binge velocity)"
            elif engagement <= 3.0:
                engagement_text += "(Slow pacing/low interest)"

            if n["chapters_read"]:
                parts.append(f"({n['chapters_read']} chapters read, {n['hours_spent']}h){engagement_text}")
            if n["genres"]:
                parts.append(f"| Genres: {n['genres']}")
            if n["author"]:
                parts.append(f"| Author: {n['author']}")
            lines.append(" ".join(parts))
        lines.append("")

    if profile["watching"]:
        lines.append("SHOWS/ANIME I'M CURRENTLY WATCHING:")
        for w in profile["watching"]:
            anime_tag = " [anime]" if w.get("is_anime") else ""
            progress  = w.get("progress", "unknown")
            total     = w.get("total_episodes", 0)
            watched   = w.get("episodes_watched", 0)
            # Give Sage a clear summary: title, where they are, how far through the season
            detail = f"{progress}"
            if total and watched:
                pct = round(watched / total * 100)
                detail += f" — {pct}% through this season"
            lines.append(f"  - {w['title']}{anime_tag}: currently at {detail}")
        lines.append("")

    if profile["completed"]:
        lines.append("SHOWS I'VE COMPLETED:")
        for c in profile["completed"]:
            lines.append(f"  - {c}")
        lines.append("")

    if profile["watchlist"]:
        lines.append("ON MY WATCHLIST (haven't started yet):")
        for w in profile["watchlist"]:
            lines.append(f"  - {w}")
        lines.append("")

    bm = profile.get("bookmarks", {})
    if bm.get("reading"):
        lines.append("NOVELS I'M CURRENTLY READING (bookmarks):")
        for t in bm["reading"]:
            lines.append(f"  - {t}")
        lines.append("")
    if bm.get("planning"):
        lines.append("NOVELS ON MY READING LIST (planning):")
        for t in bm["planning"]:
            lines.append(f"  - {t}")
        lines.append("")
    if bm.get("completed"):
        lines.append("NOVELS I'VE COMPLETED:")
        for t in bm["completed"]:
            lines.append(f"  - {t}")
        lines.append("")

    s = profile["stats"]
    lines.append("STATS:")
    lines.append(f"  - {s.get('total_chapters_read', 0)} chapters read total")
    lines.append(f"  - {s.get('total_reading_hours', 0)} hours spent reading")
    lines.append(f"  - {s.get('shows_completed', 0)} shows completed")
    lines.append(f"  - {s.get('books_bookmarked', 0)} books bookmarked")

    ext = profile.get("external", {})
    if ext.get("has_trakt") or ext.get("has_anilist"):
        lines.append("")
        lines.append("CONNECTED SERVICES:")
        if ext.get("has_trakt"):
            lines.append(f"  - Trakt account: {ext['trakt_username']} (full watch history synced)")
        if ext.get("has_anilist"):
            lines.append(f"  - AniList account: {ext['anilist_username']} (anime lists synced)")
        lines.append("  (Use this context to make more personalised recommendations)")

    return "\n".join(lines)


# ── Groq client ───────────────────────────────────────────────────────────────

def check_groq() -> tuple:
    if not GROQ_API_KEY or GROQ_API_KEY == "your-api-key-here":
        return False, [], "GROQ_API_KEY not set. Get a free key from console.groq.com"

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.get(f"{GROQ_BASE}/models", headers=headers, timeout=5)
        if r.status_code == 200:
            models = [m["id"] for m in r.json().get("data", [])]
            return True, models, None
        elif r.status_code == 403:
            return False, [], "Invalid API key."
        else:
            return False, [], f"Groq API error: {r.status_code}"
    except requests.exceptions.ConnectionError:
        return False, [], "Cannot reach Groq API. Check your internet connection."
    except Exception as e:
        return False, [], str(e)


def groq_chat(prompt: str, system: str = None, model: str = None) -> tuple:
    """Returns (response_text, error_string). error_string is None on success."""
    if not GROQ_API_KEY or GROQ_API_KEY == "your-api-key-here":
        log.error("groq_chat called but GROQ_API_KEY not set")
        return None, "GROQ_API_KEY not set."

    model    = model or GROQ_MODEL
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.7,
        "max_tokens":  2048,
        "top_p":       0.9,
    }

    log.debug("groq_chat request", model=model, prompt_len=len(prompt))
    try:
        r = requests.post(f"{GROQ_BASE}/chat/completions",
                          headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            msg = f"Groq API error ({r.status_code})"
            try:
                err = r.json().get("error", {})
                msg += f": {err.get('message', '')}"
            except Exception:
                pass
            log.error("groq_chat API error", status=r.status_code, error=msg)
            return None, msg

        text = r.json()["choices"][0]["message"]["content"].strip()
        log.debug("groq_chat success", response_len=len(text))
        return text, None

    except requests.exceptions.Timeout:
        log.error("groq_chat timed out", timeout=REQUEST_TIMEOUT)
        return None, f"Request timed out ({REQUEST_TIMEOUT}s)."
    except Exception as e:
        log.error("groq_chat exception", error=str(e))
        return None, str(e)


def groq_stream_chat(prompt: str, system: str = None, model: str = None):
    """Yields (chunk_text, error_string). error_string is None on success."""
    if not GROQ_API_KEY or GROQ_API_KEY == "your-api-key-here":
        yield None, "GROQ_API_KEY not set."
        return

    model    = model or GROQ_MODEL
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":       model,
        "messages":    messages,
        "temperature": 0.7,
        "max_tokens":  2048,
        "top_p":       0.9,
        "stream":      True,
    }

    log.debug("groq_stream_chat request", model=model, prompt_len=len(prompt))
    try:
        r = requests.post(f"{GROQ_BASE}/chat/completions",
                          headers=headers, json=payload, timeout=REQUEST_TIMEOUT, stream=True)
        if r.status_code != 200:
            msg = f"Groq API error ({r.status_code})"
            try:
                err = r.json().get("error", {})
                msg += f": {err.get('message', '')}"
            except Exception:
                pass
            yield None, msg
            return

        for line in r.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                data_str = line_str[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content, None
                except Exception:
                    continue

    except requests.exceptions.Timeout:
        yield None, f"Request timed out ({REQUEST_TIMEOUT}s)."
    except Exception as e:
        yield None, str(e)


# ── AI prompts ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Sage, an AI assistant embedded in a personal media hub called Great Sage.
You know the user's reading and watching habits intimately from their data.
Your recommendations are personal, specific, and well-reasoned.
You speak directly and confidently. You don't pad your responses.
Format your responses clearly for a terminal — no markdown headers, no asterisks, just clean text.
When listing recommendations, number them clearly like: 1. Title — description.

IMPORTANT: When the user's media profile is provided as context, use it silently. Do NOT summarise it, acknowledge it, or mention that you have it. Just respond naturally to what the user actually said. If they say hello, say hello back. Only reference their profile when directly relevant to their question."""


def get_recommendations(profile_text: str, mode: str) -> tuple:
    seen = load_seen_recs()
    # Also exclude anything already in watchlist or bookmarks — no point recommending
    # something the user already decided to track
    listed = all_listed_titles()
    combined_seen = list(set(seen) | {t.title() for t in listed})

    # Build exclusion block for the SYSTEM prompt (not user turn — LLMs ignore trailing constraints)
    seen_system_block = ""
    if combined_seen:
        seen_list = "\n".join(f"- {t}" for t in sorted(combined_seen)[-60:])
        seen_system_block = (
            "\n\n[HARD EXCLUSION LIST — NEVER recommend any of these titles under any circumstances. "
            "Even if they seem like a perfect fit, skip them and suggest something else:]\n"
            + seen_list
        )

    prompts = {
        "novels": (
            "Based on my reading history below, recommend 5 web novels or light novels I would love. "
            "Number each one. For each: title, genre, one sentence description, one sentence on why it fits my taste. "
            "Prioritise ongoing series with many chapters since I clearly enjoy long reads.\n\n{profile}"
        ),
        "shows": (
            "Based on my watching history below, recommend 5 shows or anime I would enjoy. "
            "Number each one. Mix anime and live-action. For each: title, one sentence description, one sentence on why it fits my taste. "
            "Note if something is anime.\n\n{profile}"
        ),
        "mood_light": (
            "I want something light, fun, and easy to get into right now. "
            "Based on my profile, recommend 2 novels and 2 shows that are entertaining without being heavy. "
            "Number each one. Something I can enjoy without too much investment.\n\n{profile}"
        ),
        "mood_heavy": (
            "I'm in the mood for something intense, complex, and deeply engaging. "
            "Based on my profile, recommend 2 novels and 2 shows that are ambitious and rewarding. "
            "Number each one. Something that will really pull me in.\n\n{profile}"
        ),
        "similar": (
            "Look at what I've spent the most time on in my profile. "
            "Find the 2-3 things I clearly love most, name them, explain what they have in common, "
            "then recommend 4 things (mix of novels and shows) that are most similar to those. "
            "Number each recommendation and be very specific about why each matches.\n\n{profile}"
        ),
        "whats_next": (
            "Look at what I'm currently watching and reading. "
            "For each thing I'm in the middle of, tell me: should I finish it, and what should I start next when I do? "
            "Number your suggestions. Keep it brief and practical.\n\n{profile}"
        ),
    }
    template = prompts.get(mode, prompts["shows"])
    prompt = template.format(profile=profile_text)
    system = SYSTEM_PROMPT + seen_system_block
    response, error = groq_chat(prompt, system=system)

    # Parse recommended titles from response — stop at " - " or " — " not at ":"
    # so "Title: Subtitle — description" captures the full title
    if response and not error:
        import re as _re
        raw_titles = _re.findall(
            r'^\s*\d+[.)]\s+\*{0,2}([^*\n]{3,80}?)\*{0,2}\s*(?:\s[-\u2014]\s|$)',
            response, _re.MULTILINE
        )
        raw_titles = [t.strip().strip('*').strip() for t in raw_titles if t.strip()]
        if raw_titles:
            add_seen_recs(raw_titles)

    return response, error


def get_quick_pick(profile_text: str) -> tuple:
    seen = load_seen_recs()
    seen_system_block = ""
    if seen:
        seen_list = "\n".join(f"- {t}" for t in seen[-30:])
        seen_system_block = (
            "\n\n[HARD EXCLUSION LIST — do NOT suggest any of these under any circumstances:]\n"
            + seen_list
        )
    prompt = (
        "Based on my profile, give me ONE thing to start right now — either a novel or a show. "
        "The single best fit for my taste. Just the title, what it is in one sentence, "
        "and why it's perfect for me in one sentence. No lists.\n\n" + profile_text
    )
    response, error = groq_chat(prompt, system=SYSTEM_PROMPT + seen_system_block)
    if response and not error:
        # Try to extract the title from first line
        first_line = response.strip().splitlines()[0] if response.strip() else ""
        if first_line and len(first_line) < 80:
            add_seen_recs([first_line.strip('*').strip()])
    return response, error


def get_explain_why(profile_text: str, title: str) -> tuple:
    """
    Given any title the user names, explain whether and why they'd like it
    based on their profile.
    """
    prompt = (
        f"I want to know if I would enjoy: {title}\n\n"
        "Based on my media profile below, give me a direct verdict: yes, probably, maybe, or no. "
        "Then explain specifically why — reference what I already watch and read. "
        "Be honest. If it doesn't fit my taste at all, say so. Keep it under 150 words.\n\n"
        + profile_text
    )
    return groq_chat(prompt, system=SYSTEM_PROMPT)


def get_chapter_summary(book_name: str, chapter_text: str) -> tuple:
    """Ask Sage to summarise the last few chapters of a novel."""
    prompt = (
        f"I've been away from '{book_name}' for a while and need to catch up. "
        "Below are the last few chapters I read. Give me a clear, spoiler-complete summary "
        "so I can jump straight back in. Cover: what happened, where characters are now, "
        "any cliffhangers or unresolved threads. Be thorough but concise — aim for 200-300 words.\n\n"
        f"CHAPTER TEXT:\n{chapter_text[:6000]}"
    )
    return groq_chat(prompt, system=SYSTEM_PROMPT)


def get_watchlist_priority(profile_text: str, watchlist: list) -> tuple:
    """Ask Sage to rank the user's watchlist by how well each item fits their taste."""
    if not watchlist:
        return None, "Watchlist is empty."

    items_text = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(watchlist))
    prompt = (
        "Here is my current watchlist of things I haven't started yet:\n\n"
        f"{items_text}\n\n"
        "Based on my media profile below, rank these from the one I should watch first "
        "to the one I should watch last. For each, give the title and one sentence on why "
        "it's ranked where it is — how well does it match my current taste and what I've been watching?\n\n"
        + profile_text
    )
    return groq_chat(prompt, system=SYSTEM_PROMPT)


def groq_chat_with_history(messages: list, system: str = None) -> tuple:
    """
    Send a full conversation history to Groq.
    messages = [{"role": "user"|"assistant", "content": "..."}]
    """
    if not GROQ_API_KEY or GROQ_API_KEY == "your-api-key-here":
        return None, "GROQ_API_KEY not set."

    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model":       GROQ_MODEL,
        "messages":    full_messages,
        "temperature": 0.7,
        "max_tokens":  2048,
        "top_p":       0.9,
    }

    try:
        r = requests.post(f"{GROQ_BASE}/chat/completions",
                          headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            msg = f"Groq API error ({r.status_code})"
            try:
                err = r.json().get("error", {})
                msg += f": {err.get('message', '')}"
            except Exception:
                pass
            return None, msg
        text = r.json()["choices"][0]["message"]["content"].strip()
        return text, None
    except requests.exceptions.Timeout:
        return None, f"Request timed out ({REQUEST_TIMEOUT}s)."
    except Exception as e:
        return None, str(e)


def tavily_search(query: str, max_results: int = 5) -> str:
    """
    Search the web via Tavily and return a formatted string of results
    ready to inject into the Groq prompt.
    Returns empty string on failure.
    """
    if not TAVILY_API_KEY:
        return ""
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "api_key":        TAVILY_API_KEY,
            "query":          query,
            "search_depth":   "basic",
            "max_results":    max_results,
            "include_answer": True,
        }
        r = requests.post(TAVILY_API, headers=headers,
                          json=payload, timeout=15)
        if r.status_code != 200:
            return ""
        data   = r.json()
        chunks = []

        # Tavily's own AI answer if available
        if data.get("answer"):
            chunks.append(f"Summary: {data['answer']}")

        # Individual results
        for res in data.get("results", [])[:max_results]:
            title   = res.get("title", "")
            url     = res.get("url", "")
            snippet = res.get("content", "")[:400]
            chunks.append(f"• {title}\n  {snippet}\n  Source: {url}")

        return "\n\n".join(chunks)
    except Exception:
        return ""


def needs_web_search(message: str) -> bool:
    """
    Decide if a user message needs a live web search.
    Looks for signals: current events, recent news, prices, live scores, etc.
    """
    msg = message.lower()

    # Strong current-events signals
    current_signals = [
        "right now", "currently", "latest", "recent", "today", "this week",
        "this month", "this year", "2024", "2025", "2026",
        "what is happening", "what's happening", "whats happening",
        "what happened", "news", "update", "status", "live",
        "score", "war", "election", "announce", "release date",
        "when does", "when is", "has it been", "is it out",
        "new season", "trailer", "dropped", "just released",
        "price", "stock", "weather",
    ]
    for signal in current_signals:
        if signal in msg:
            return True

    # Questions about specific named current events / people in the news
    news_patterns = [
        r"what.*(happening|going on).*(in|with|at)",
        r"(who|what) is .*(president|prime minister|ceo|leader)",
        r"did .* (win|lose|die|resign|announce)",
    ]
    for pattern in news_patterns:
        if re.search(pattern, msg):
            return True

    return False


def chat_with_sage(profile_text: str, user_message: str, history: list) -> tuple:
    """
    Chat with Sage with full conversation memory and optional web search.
    history = list of (role, content) tuples from this session.
    """
    system = SYSTEM_PROMPT + (
        "\n\nYou have access to real-time web search results when the user asks about "
        "current events, news, or anything time-sensitive. When search results are "
        "provided, use them to give accurate, up-to-date answers and cite sources naturally."
    )

    # Build message list for Groq
    messages = []

    # Inject profile as a system-level context on first turn,
    # then carry full history on subsequent turns
    if not history:
        messages.append({
            "role":    "user",
            "content": f"[My media profile — use this as background context for our whole conversation]\n\n{profile_text}\n\n---\n\n{user_message}",
        })
        return groq_chat_with_history(messages, system=system)
    else:
        # Rebuild history from tuples
        for role, msg_content in history:
            messages.append({"role": role, "content": msg_content})

    # Check if this query needs live web search
    search_context = ""
    if needs_web_search(user_message):
        search_context = tavily_search(user_message)

    # Build the final user message — inject search results if we have them
    if search_context:
        final_message = (
            f"[Web search results for your query]\n\n"
            f"{search_context}\n\n"
            f"---\n\n"
            f"My question: {user_message}"
        )
    else:
        final_message = user_message

    messages.append({"role": "user", "content": final_message})

    return groq_chat_with_history(messages, system=system)


# ── Parse titles from AI response ─────────────────────────────────────────────

def parse_titles_from_response(text: str) -> list[str]:
    """
    Extract numbered titles from an AI recommendation response.
    Handles formats like:
      1. Title — description
      1. Title: description
      1) Title
    Returns a list of title strings.
    """
    titles = []
    for line in text.split("\n"):
        line = line.strip()
        # Match lines starting with a number
        m = re.match(r"^\d+[\.\)]\s+(.+)", line)
        if m:
            raw = m.group(1).strip()
            # Strip everything after the first — or :
            for sep in [" — ", " - ", ": ", " ("]:
                if sep in raw:
                    raw = raw[:raw.index(sep)].strip()
            if raw:
                titles.append(raw)
    return titles


# ── Terminal UI ───────────────────────────────────────────────────────────────

def get_terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def print_wrapped(text: str, width: int = None, indent: int = 2):
    width  = width or min(get_terminal_width() - 4, 88)
    prefix = " " * indent
    for para in text.split("\n"):
        if para.strip() == "":
            print()
        else:
            wrapped = textwrap.fill(para, width=width,
                                    initial_indent=prefix,
                                    subsequent_indent=prefix)
            if RICH and console:
                console.print(wrapped, style="primary")
            else:
                print(wrapped)


def print_thinking(msg: str = "Sage is thinking"):
    if RICH and console:
        console.print(f"\n  [faint]⟡ {msg}...[/faint]", end="")
    else:
        print(f"\n  {msg}...", end="", flush=True)


def clear_thinking():
    if not RICH:
        print("\r" + " " * 40 + "\r", end="", flush=True)
    else:
        print()  # newline after the faint line


def splash():
    os.system("cls" if os.name == "nt" else "clear")
    if RICH and console:
        console.print()
        console.print("[glow]╭──────────────────────────────────────────────────────────────╮[/glow]")
        console.print("[glow]│[/glow]  [neon]⟡  SAGE  ⟡[/neon]  [secondary]AI Recommendation Engine[/secondary]                    [glow]│[/glow]")
        console.print("[glow]│[/glow]  [faint]Powered by Groq Cloud · Recommendations in seconds[/faint]   [glow]│[/glow]")
        console.print("[glow]╰──────────────────────────────────────────────────────────────╯[/glow]")
        console.print()
    else:
        print("\n=== SAGE — AI Recommendation Engine ===")
        print("Powered by Groq Cloud\n")


def show_profile_summary(profile: dict):
    if RICH and console:
        console.print()
        console.print(Rule(style="divider"))
        console.print("[secondary]  What Sage knows about you:[/secondary]\n")

        novels = profile.get("novels", [])
        if novels:
            console.print("[novel]  Novels:[/novel]")
            for n in novels:
                ch_str = f"  {n['chapters_read']} ch" if n["chapters_read"] else ""
                g_str  = f"  [{n['genres']}]" if n["genres"] else ""
                console.print(f"    [primary]{n['title']}[/primary][faint]{ch_str}{g_str}[/faint]")

        watching = profile.get("watching", [])
        if watching:
            console.print("\n[media]  Currently watching:[/media]")
            for w in watching:
                anime = " [anime]" if w.get("is_anime") else ""
                console.print(f"    [primary]{w['title']}[/primary][faint]{anime} · {w['progress']}[/faint]")

        completed = profile.get("completed", [])
        if completed:
            console.print(f"\n[faint]  Completed: {', '.join(completed[:5])}{'...' if len(completed) > 5 else ''}[/faint]")

        watchlist = profile.get("watchlist", [])
        if watchlist:
            console.print(f"[faint]  Watchlist: {', '.join(watchlist[:4])}{'...' if len(watchlist) > 4 else ''}[/faint]")

        s = profile.get("stats", {})
        console.print(
            f"\n[stat]  {s.get('total_chapters_read', 0)} chapters read[/stat]  ·  "
            f"[stat]{s.get('total_reading_hours', 0)}h reading[/stat]  ·  "
            f"[stat]{s.get('shows_completed', 0)} shows completed[/stat]"
        )
        console.print(Rule(style="divider"))
        console.print()
    else:
        print("\n--- Your Profile ---")
        for n in profile.get("novels", []):
            print(f"  Novel: {n['title']} ({n['chapters_read']} ch)")
        for w in profile.get("watching", []):
            print(f"  Watching: {w['title']}")
        print()


def show_response(title: str, text: str):
    """
    Show AI response with accurate terminal-line paging.
    Wraps each paragraph to the terminal width first, then paginates
    by actual rendered lines — so Rich reflow can't break the page count.
    """
    import shutil, textwrap
    term_size   = shutil.get_terminal_size()
    term_width  = term_size.columns
    term_height = term_size.lines
    PAGE_LINES  = max(8, term_height - 5)   # lines per page, reserve space for header+nav
    wrap_width  = min(term_width - 4, 88)   # indent of 2 each side

    # Pre-wrap every paragraph into terminal-width lines
    wrapped_lines = []
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw:
            wrapped_lines.append("")
        else:
            wrapped_lines.extend(textwrap.wrap(raw, width=wrap_width) or [""])

    total       = len(wrapped_lines)
    total_pages = max(1, (total + PAGE_LINES - 1) // PAGE_LINES)
    page        = 0

    while True:
        start      = page * PAGE_LINES
        end        = min(start + PAGE_LINES, total)
        page_lines = wrapped_lines[start:end]

        # Clear and print header
        print()
        pg_info = f" ({page+1}/{total_pages})" if total_pages > 1 else ""
        header  = f"  ── {title}{pg_info} "
        print(header + "─" * max(0, wrap_width + 4 - len(header)))

        # Print content lines
        for line in page_lines:
            print(f"  {line}")

        # Nav prompt
        if total_pages == 1:
            print()
            break

        nav = []
        if page > 0:               nav.append("[p] prev")
        if page < total_pages - 1: nav.append("[n] next")
        nav.append("[any key] done")
        print()
        print(f"  {'  ·  '.join(nav)}  ", end="", flush=True)

        try:
            key = input("").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if key == 'n' and page < total_pages - 1:
            page += 1
        elif key == 'p' and page > 0:
            page -= 1
        else:
            print()
            break


def prompt_add_to_list(response_text: str):
    """
    After showing recommendations, let the user pick a title to save.
    Asks: Watchlist or Bookmarks? Then which sub-list?
    Adding to any list removes it from future recommendations.
    """
    titles = parse_titles_from_response(response_text)
    if not titles:
        return

    if RICH and console:
        console.print("[secondary]  Save a recommendation? Enter number or Enter to skip:[/secondary] ", end="")
    else:
        print("  Save a recommendation? Enter number or Enter to skip: ", end="")

    try:
        raw = input("").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not raw:
        return

    try:
        idx = int(raw) - 1
    except ValueError:
        return

    if idx < 0 or idx >= len(titles):
        if RICH and console:
            console.print("  [warn]Invalid number.[/warn]")
        else:
            print("  Invalid number.")
        return

    chosen = titles[idx]

    # Ask destination: Watchlist or Bookmarks
    if RICH and console:
        console.print(f"\n  Save [bold]{chosen}[/bold] to:")
        console.print("  [dim]1[/dim] Watchlist  (Matrix — shows/anime)")
        console.print("  [dim]2[/dim] Bookmarks  (Legion — novels)")
        console.print("  [dim]0[/dim] Cancel")
    else:
        print(f"\n  Save '{chosen}' to:")
        print("  1. Watchlist  (Matrix — shows/anime)")
        print("  2. Bookmarks  (Legion — novels)")
        print("  0. Cancel")

    try:
        dest = input("  Choice: ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if dest == "0" or not dest:
        return

    if dest == "1":
        # Watchlist sub-list
        WLISTS = ["planning", "watching", "dropped", "completed"]
        if RICH and console:
            console.print("  Which list?")
            for i, lst in enumerate(WLISTS, 1):
                console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
        else:
            print("  Which list?")
            for i, lst in enumerate(WLISTS, 1):
                print(f"  {i}. {lst.capitalize()}")
        try:
            sub = int(input("  Choice: ").strip()) - 1
            list_name = WLISTS[sub] if 0 <= sub < len(WLISTS) else "planning"
        except (ValueError, IndexError):
            list_name = "planning"

        result = add_to_matrix_watchlist_list(chosen, list_name)
        if RICH and console:
            if result == "added":
                console.print(f"  [ok]✓ Added '{chosen}' to Watchlist → {list_name.capitalize()}[/ok]")
            elif result == "duplicate":
                console.print(f"  [warn]'{chosen}' is already in your watchlist.[/warn]")
            else:
                console.print(f"  [err]Could not save — check Matrix data file.[/err]")
        else:
            msgs = {"added": f"  Added '{chosen}' → {list_name.capitalize()}",
                    "duplicate": f"  '{chosen}' already in watchlist.",
                    "error": "  Error saving."}
            print(msgs.get(result, ""))

    elif dest == "2":
        # Bookmarks sub-list
        BLISTS = ["planning", "reading", "dropped", "completed"]
        if RICH and console:
            console.print("  Which list?")
            for i, lst in enumerate(BLISTS, 1):
                console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
        else:
            print("  Which list?")
            for i, lst in enumerate(BLISTS, 1):
                print(f"  {i}. {lst.capitalize()}")
        try:
            sub = int(input("  Choice: ").strip()) - 1
            list_name = BLISTS[sub] if 0 <= sub < len(BLISTS) else "planning"
        except (ValueError, IndexError):
            list_name = "planning"

        result = add_to_legion_bookmarks(chosen, list_name)
        if RICH and console:
            if result == "added":
                console.print(f"  [ok]✓ Added '{chosen}' to Bookmarks → {list_name.capitalize()}[/ok]")
                console.print(f"  [dim](Add the URL later in Legion)[/dim]")
            elif result == "duplicate":
                console.print(f"  [warn]'{chosen}' is already in your bookmarks.[/warn]")
            else:
                console.print(f"  [err]Could not save — check bookmarks file.[/err]")
        else:
            msgs = {"added": f"  Added '{chosen}' → Bookmarks/{list_name.capitalize()} (add URL in Legion)",
                    "duplicate": f"  '{chosen}' already in bookmarks.",
                    "error": "  Error saving."}
            print(msgs.get(result, ""))


def prompt_add_to_watchlist(response_text: str):
    """Compat alias."""
    prompt_add_to_list(response_text)


def get_key() -> str:
    if READCHAR:
        k = readchar.readkey()
        return k.lower() if isinstance(k, str) else k
    return input("  > ").strip().lower()


# ── Feature runners ───────────────────────────────────────────────────────────

def run_recommendation(profile_text: str, mode: str, title: str):
    """Run a recommendation, show it, then offer watchlist add."""
    print_thinking()
    t0 = time.time()
    response, error = get_recommendations(profile_text, mode)
    elapsed = time.time() - t0
    clear_thinking()

    if error:
        if RICH and console:
            console.print(f"\n  [err]Error: {error}[/err]")
        else:
            print(f"\n  Error: {error}")
        input("\n  Press Enter to continue...")
        return

    show_response(title, response)

    if RICH and console:
        console.print(f"  [faint]Generated in {elapsed:.1f}s[/faint]\n")
    else:
        print(f"  ({elapsed:.1f}s)")

    # Offer to add to watchlist
    prompt_add_to_watchlist(response)

    input("\n  Press Enter to continue...")


def run_quick_pick(profile_text: str):
    print_thinking()
    t0 = time.time()
    response, error = get_quick_pick(profile_text)
    elapsed = time.time() - t0
    clear_thinking()

    if error:
        if RICH and console:
            console.print(f"\n  [err]Error: {error}[/err]")
        else:
            print(f"\n  Error: {error}")
        input("\n  Press Enter to continue...")
        return

    show_response("Your Quick Pick", response)

    if RICH and console:
        console.print(f"  [faint]{elapsed:.1f}s[/faint]\n")

    # Quick pick is one title — offer to add it
    # Try to extract any title from the response
    if RICH and console:
        console.print("[secondary]  Add this to your watchlist? (y/n):[/secondary] ", end="")
    else:
        print("  Add to watchlist? (y/n): ", end="")

    try:
        ans = input("").strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""

    if ans == "y":
        # Extract first line that looks like a title
        first_line = response.split("\n")[0].strip()
        # Strip common prefixes
        first_line = re.sub(r"^(I recommend|Start with|Watch|Read)\s+", "", first_line, flags=re.IGNORECASE)
        # Ask which list
        WLISTS = ["planning", "watching", "dropped", "completed"]
        if RICH and console:
            console.print("  Add to Watchlist or Bookmarks?  [dim]1[/dim] Watchlist  [dim]2[/dim] Bookmarks  [dim]0[/dim] Cancel")
        else:
            print("  1. Watchlist  2. Bookmarks  0. Cancel")
        try:
            dest_choice = input("  Choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            dest_choice = "0"
        if dest_choice == "1":
            if RICH and console:
                for i, lst in enumerate(WLISTS, 1):
                    console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
            else:
                for i, lst in enumerate(WLISTS, 1):
                    print(f"  {i}. {lst.capitalize()}")
            try:
                sub = int(input("  List: ").strip()) - 1
                list_name = WLISTS[sub] if 0 <= sub < len(WLISTS) else "planning"
            except (ValueError, IndexError):
                list_name = "planning"
            result = add_to_matrix_watchlist_list(first_line, list_name)
            tag = f"Watchlist/{list_name.capitalize()}"
        elif dest_choice == "2":
            BLISTS = ["planning", "reading", "dropped", "completed"]
            if RICH and console:
                for i, lst in enumerate(BLISTS, 1):
                    console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
            else:
                for i, lst in enumerate(BLISTS, 1):
                    print(f"  {i}. {lst.capitalize()}")
            try:
                sub = int(input("  List: ").strip()) - 1
                list_name = BLISTS[sub] if 0 <= sub < len(BLISTS) else "planning"
            except (ValueError, IndexError):
                list_name = "planning"
            result = add_to_legion_bookmarks(first_line, list_name)
            tag = f"Bookmarks/{list_name.capitalize()}"
        else:
            result = None
            tag = ""
        if result == "added":
            if RICH and console:
                console.print(f"  [ok]✓ Added to {tag}.[/ok]")
            else:
                print(f"  Added to {tag}.")
        elif result == "duplicate":
            if RICH and console:
                console.print("  [warn]Already in your lists.[/warn]")
            else:
                print("  Already in your lists.")
        elif result is not None:
            if RICH and console:
                console.print("  [err]Error saving.[/err]")
            else:
                print("  Error saving.")

    input("\n  Press Enter to continue...")


def run_explain_why(profile_text: str):
    """Prompt user for a title, then explain whether they'd like it."""
    if RICH and console:
        console.print()
        console.print("[secondary]  Enter a title to analyse (novel, show, anime, film):[/secondary]")
        console.print("[accent]  > [/accent]", end="")
    else:
        print("\n  Enter a title to analyse: ", end="")

    try:
        title = input("").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not title:
        return

    print_thinking(f"Analysing '{title}'")
    t0 = time.time()
    response, error = get_explain_why(profile_text, title)
    elapsed = time.time() - t0
    clear_thinking()

    if error:
        if RICH and console:
            console.print(f"\n  [err]Error: {error}[/err]")
        else:
            print(f"\n  Error: {error}")
        input("\n  Press Enter to continue...")
        return

    show_response(f"Would you like: {title}?", response)

    if RICH and console:
        console.print(f"  [faint]{elapsed:.1f}s[/faint]\n")
        console.print("[secondary]  Add to watchlist anyway? (y/n):[/secondary] ", end="")
    else:
        print("  Add to watchlist? (y/n): ", end="")

    try:
        ans = input("").strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""

    if ans == "y":
        # Ask which list
        WLISTS = ["planning", "watching", "dropped", "completed"]
        if RICH and console:
            console.print("  Add to Watchlist or Bookmarks?  [dim]1[/dim] Watchlist  [dim]2[/dim] Bookmarks  [dim]0[/dim] Cancel")
        else:
            print("  1. Watchlist  2. Bookmarks  0. Cancel")
        try:
            dest_choice = input("  Choice: ").strip()
        except (KeyboardInterrupt, EOFError):
            dest_choice = "0"
        if dest_choice == "1":
            if RICH and console:
                for i, lst in enumerate(WLISTS, 1):
                    console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
            else:
                for i, lst in enumerate(WLISTS, 1):
                    print(f"  {i}. {lst.capitalize()}")
            try:
                sub = int(input("  List: ").strip()) - 1
                list_name = WLISTS[sub] if 0 <= sub < len(WLISTS) else "planning"
            except (ValueError, IndexError):
                list_name = "planning"
            result = add_to_matrix_watchlist_list(title, list_name)
            tag = f"Watchlist/{list_name.capitalize()}"
        elif dest_choice == "2":
            BLISTS = ["planning", "reading", "dropped", "completed"]
            if RICH and console:
                for i, lst in enumerate(BLISTS, 1):
                    console.print(f"  [dim]{i}[/dim] {lst.capitalize()}")
            else:
                for i, lst in enumerate(BLISTS, 1):
                    print(f"  {i}. {lst.capitalize()}")
            try:
                sub = int(input("  List: ").strip()) - 1
                list_name = BLISTS[sub] if 0 <= sub < len(BLISTS) else "planning"
            except (ValueError, IndexError):
                list_name = "planning"
            result = add_to_legion_bookmarks(title, list_name)
            tag = f"Bookmarks/{list_name.capitalize()}"
        else:
            result = None
            tag = ""
        if result == "added":
            if RICH and console:
                console.print(f"  [ok]✓ Added to {tag}.[/ok]")
            else:
                print(f"  Added to {tag}.")
        elif result == "duplicate":
            if RICH and console:
                console.print("  [warn]Already in your lists.[/warn]")
            else:
                print("  Already in your lists.")
        elif result is not None:
            if RICH and console:
                console.print("  [err]Error saving.[/err]")
            else:
                print("  Error saving.")

    input("\n  Press Enter to continue...")


def run_chapter_summary(profile: dict):
    """Let user pick a novel from their list, then summarise the last chapters."""
    novels = profile.get("novels", [])
    if not novels:
        if RICH and console:
            console.print("\n  [warn]No novels found in your Legion library.[/warn]")
        else:
            print("\n  No novels found.")
        input("\n  Press Enter to continue...")
        return

    # Show numbered list
    if RICH and console:
        console.print()
        console.print("[secondary]  Choose a novel to summarise:[/secondary]\n")
        for i, n in enumerate(novels):
            ch_str = f"  [faint]({n['chapters_read']} ch read)[/faint]" if n["chapters_read"] else ""
            console.print(f"  [number][{i+1}][/number] [primary]{n['title']}[/primary]{ch_str}")
        console.print("\n  [faint]Enter number, or press Enter to cancel:[/faint] ", end="")
    else:
        print("\n  Choose a novel:")
        for i, n in enumerate(novels):
            print(f"  [{i+1}] {n['title']} ({n['chapters_read']} ch)")
        print("\n  Enter number or Enter to cancel: ", end="")

    try:
        raw = input("").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not raw:
        return

    try:
        idx = int(raw) - 1
    except ValueError:
        return

    if idx < 0 or idx >= len(novels):
        if RICH and console:
            console.print("  [warn]Invalid selection.[/warn]")
        else:
            print("  Invalid selection.")
        input("\n  Press Enter to continue...")
        return

    book_name = novels[idx]["title"]

    print_thinking(f"Loading chapters from '{book_name}'")
    chapter_text = read_last_n_chapters(book_name, n=5)
    clear_thinking()

    if not chapter_text:
        searched = ", ".join(
            d for d in LEGION_BOOK_SEARCH_DIRS if os.path.isdir(d)
        ) or "no valid directories found"
        expected_filename = re.sub(r"[^\w\-_\. ]", "_", book_name) + ".txt"
        if RICH and console:
            console.print(
                f"\n  [warn]Could not find the book file for '{book_name}'.[/warn]\n"
                f"  [faint]Searched in:\n"
                f"  {searched}\n\n"
                f"  Legion saves .txt files wherever it was run from.\n"
                f"  The expected filename is: [accent]{expected_filename}[/accent][/faint]"
            )
        else:
            print(f"\n  Could not find book file for '{book_name}'.")
            print(f"  Expected filename: {expected_filename}")
            print(f"  Searched: {searched}")
        input("\n  Press Enter to continue...")
        return

    print_thinking("Sage is reading and summarising")
    t0 = time.time()
    response, error = get_chapter_summary(book_name, chapter_text)
    elapsed = time.time() - t0
    clear_thinking()

    if error:
        if RICH and console:
            console.print(f"\n  [err]Error: {error}[/err]")
        else:
            print(f"\n  Error: {error}")
    else:
        show_response(f"Chapter Summary — {book_name}", response)
        if RICH and console:
            console.print(f"  [faint]{elapsed:.1f}s[/faint]")

    input("\n  Press Enter to continue...")


def run_watchlist_priority(profile_text: str, profile: dict):
    """Ask Sage to rank the user's watchlist."""
    watchlist = profile.get("watchlist", [])

    if not watchlist:
        if RICH and console:
            console.print("\n  [warn]Your watchlist is empty. Add shows in Matrix first.[/warn]")
        else:
            print("\n  Watchlist is empty.")
        input("\n  Press Enter to continue...")
        return

    print_thinking("Sage is ranking your watchlist")
    t0 = time.time()
    response, error = get_watchlist_priority(profile_text, watchlist)
    elapsed = time.time() - t0
    clear_thinking()

    if error:
        if RICH and console:
            console.print(f"\n  [err]Error: {error}[/err]")
        else:
            print(f"\n  Error: {error}")
        input("\n  Press Enter to continue...")
        return

    show_response("Watchlist — Ranked by Fit", response)

    if RICH and console:
        console.print(f"  [faint]{elapsed:.1f}s[/faint]\n")
    else:
        print(f"  ({elapsed:.1f}s)")

    input("\n  Press Enter to continue...")


def run_chat_mode(profile_text: str):
    history = []
    os.system("cls" if os.name == "nt" else "clear")
    if RICH and console:
        console.print()
        console.print("  [title]Chat with Sage[/title]  [faint]· type 'q' to go back[/faint]")
        console.print()
    else:
        print("\n  Chat with Sage  (q to go back)\n")

    while True:
        try:
            if RICH and console:
                console.print("[accent]  You:[/accent] ", end="")
            else:
                print("  You: ", end="")
            user_input = input("").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input or user_input.lower() in ("q", "quit", "back"):
            break

        # Show search indicator if needed
        if needs_web_search(user_input):
            if RICH and console:
                console.print("\n  [faint]⟡ Searching the web...[/faint]", end="")
            else:
                print("\n  Searching the web...", end="", flush=True)
        else:
            print_thinking()

        t0 = time.time()
        response, error = chat_with_sage(profile_text, user_input, history)
        elapsed = time.time() - t0
        clear_thinking()

        if error:
            if RICH and console:
                console.print(f"\n  [err]Sage: Error — {error}[/err]\n")
            else:
                print(f"\n  Sage: Error — {error}\n")
        else:
            if RICH and console:
                console.print(f"\n  [neon]Sage[/neon] [faint]({elapsed:.1f}s):[/faint]")
                print_wrapped(response)
                console.print()
            else:
                print(f"\n  Sage ({elapsed:.1f}s):")
                print_wrapped(response)
                print()

            history.append(("user", user_input))
            history.append(("assistant", response))
            if len(history) > 12:
                history = history[-12:]


# ── Main menu ─────────────────────────────────────────────────────────────────

def show_main_menu(profile: dict, groq_ok: bool) -> str:
    if RICH and console:
        console.print("[secondary]  What would you like?[/secondary]\n")
        console.print("  [accent] 1[/accent]  [primary]Novel recommendations[/primary]    [faint]— web novels & light novels[/faint]")
        console.print("  [accent] 2[/accent]  [primary]Show recommendations[/primary]     [faint]— series & anime[/faint]")
        console.print("  [accent] 3[/accent]  [primary]Something similar[/primary]        [faint]— more of what you love most[/faint]")
        console.print("  [accent] 4[/accent]  [primary]Mood: light & fun[/primary]        [faint]— easy entertainment[/faint]")
        console.print("  [accent] 5[/accent]  [primary]Mood: intense & deep[/primary]     [faint]— something gripping[/faint]")
        console.print("  [accent] 6[/accent]  [primary]What\'s next?[/primary]             [faint]— finish/start something now[/faint]")
        console.print("  [accent] 7[/accent]  [primary]Quick pick[/primary]               [faint]— just tell me one thing[/faint]")
        console.print("  [accent] 8[/accent]  [primary]Would I like this?[/primary]       [faint]— analyse any title[/faint]")
        console.print("  [accent] 9[/accent]  [primary]Chapter summary[/primary]          [faint]— catch up on a novel[/faint]")
        console.print("  [accent]10[/accent]  [primary]Rank my watchlist[/primary]        [faint]— what to watch first[/faint]")
        console.print("  [accent]11[/accent]  [primary]Chat with Sage[/primary]           [faint]— ask anything[/faint]")
        console.print("  [accent]12[/accent]  [primary]View your profile[/primary]")
        console.print("  [accent]13[/accent]  [primary]Back[/primary]")
        if not groq_ok:
            console.print("\n  [warn]⚠  Cannot reach Groq — check your API key and internet connection[/warn]")
        console.print()
        console.print("  [faint]Choice (____) :[/faint] ", end="")
    else:
        print("\n   1  Novel recommendations    — web novels & light novels")
        print("   2  Show recommendations     — series & anime")
        print("   3  Something similar        — more of what you love most")
        print("   4  Mood: light & fun        — easy entertainment")
        print("   5  Mood: intense & deep     — something gripping")
        print("   6  What\'s next?             — finish/start something now")
        print("   7  Quick pick               — just tell me one thing")
        print("   8  Would I like this?       — analyse any title")
        print("   9  Chapter summary          — catch up on a novel")
        print("  10  Rank my watchlist        — what to watch first")
        print("  11  Chat with Sage           — ask anything")
        print("  12  View your profile")
        print("  13  Back")
        if not groq_ok:
            print("\n  WARNING: Cannot reach Groq")
        print()
        print("  Choice (____) : ", end="")

    try:
        choice = input("").strip()
    except (KeyboardInterrupt, EOFError):
        choice = "13"
    return choice

# ── Entry point ───────────────────────────────────────────────────────────────

def _print_status(groq_ok, active_model, err, profile, has_data):
    if RICH and console:
        if groq_ok:
            console.print(f"  [ok]✓ Groq connected[/ok]  [faint]·  model: {active_model}[/faint]")
        else:
            console.print(f"  [warn]⚠ Groq offline[/warn]  [faint]·  {err}[/faint]")

        if has_data:
            n_novels   = len(profile["novels"])
            n_watching = len(profile["watching"])
            n_done     = len(profile["completed"])
            n_wl       = len(profile["watchlist"])
            console.print(
                f"  [faint]Profile: {n_novels} novel{'s' if n_novels != 1 else ''}  ·  "
                f"{n_watching} watching  ·  {n_done} completed  ·  {n_wl} on watchlist[/faint]"
            )
        else:
            console.print("  [warn]No data yet — add books in Legion and shows in Matrix first.[/warn]")
        console.print()
    else:
        print(f"  {'Groq: OK' if groq_ok else 'Groq: OFFLINE'}")

def main():
    global GROQ_MODEL

    groq_ok, models, err = check_groq()

    # Fall back to first available model if preferred isn't listed
    model_ok = groq_ok and any(GROQ_MODEL in m for m in models)
    active_model = GROQ_MODEL
    if groq_ok and not model_ok and models:
        chat_models = [m for m in models if not any(
            x in m.lower() for x in ["whisper", "tts", "vision", "arabic", "orpheus"]
        )]
        if chat_models:
            active_model = chat_models[0]
            GROQ_MODEL = active_model

    profile = build_profile()
    profile_text = profile_to_text(profile)
    has_data = bool(profile["novels"] or profile["watching"] or
                    profile["completed"] or profile["watchlist"])

    actions = {
        "1": lambda p, t: run_recommendation(t, "novels", "Novel Recommendations"),
        "2": lambda p, t: run_recommendation(t, "shows", "Show & Anime Recommendations"),
        "3": lambda p, t: run_recommendation(t, "similar", "More of What You Love"),
        "4": lambda p, t: run_recommendation(t, "mood_light", "Light & Fun Picks"),
        "5": lambda p, t: run_recommendation(t, "mood_heavy", "Intense & Deep Picks"),
        "6": lambda p, t: run_recommendation(t, "whats_next", "What's Next For You"),
        "7": lambda p, t: run_quick_pick(t),
        "8": lambda p, t: run_explain_why(t),
        "9": lambda p, t: run_chapter_summary(p),
        "10": lambda p, t: run_watchlist_priority(t, p),
        "11": lambda p, t: run_chat_mode(t),
    }

    while True:
        splash()
        _print_status(groq_ok, active_model, err, profile, has_data)
        choice = show_main_menu(profile, groq_ok)

        if choice == "13":
            break
        
        if not groq_ok and choice not in ("12", "13"):
            msg = "\n  [err]Cannot reach Groq. Check your API key and internet connection.[/err]" if RICH and console else "\n  Cannot reach Groq."
            if RICH and console: console.print(msg)
            else: print(msg)
            input("\n  Press Enter to continue...")
            groq_ok, models, err = check_groq()
            continue

        if choice == "12":
            splash()
            show_profile_summary(profile)
            input("  Press Enter to continue...")
        elif choice in actions:
            actions[choice](profile, profile_text)
        else:
            continue

        # Reload data after each action
        profile = build_profile()
        profile_text = profile_to_text(profile)
        has_data = bool(profile["novels"] or profile["watching"] or
                        profile["completed"] or profile["watchlist"])



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
        sys.exit(0)