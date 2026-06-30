"""
gs_legion_ui.py  —  Legion Discovery Tab
Great Sage  ·  New Legion built from scratch

Layout
------
  Header row  :  LEGION ◈ DISCOVER  |  Search bar  |  Source dropdown
  Grid        :  Unified book covers (7 columns) — trending on load
  Local strip :  Books found in the existing Legion library folder
  Detail panel:  Slides in from the right on card click

Sources
-------
  Royal Road       — scraping (trending + search)
  FreeWebNovel/LR  — plain requests (popular + search + chapters)
                     libread.com now redirects to freewebnovel.com;
                     all fwn requests use a plain requests.Session with a
                     Chrome UA (proven working by diag.py Test 1).
                     cloudscraper is NOT used — fwn blocks its TLS fingerprint.
  Gutenberg        — Gutendex API (most downloaded + search)
  Local            — scans Legion library folder for EPUB / PDF / TXT

All network work runs in QThread workers so the UI never blocks.
"""

from __future__ import annotations

import os
import re
import json
import threading
import urllib.request
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from PyQt6.QtCore import (
    Qt, QThread, QObject, pyqtSignal, QSize, QTimer,
    QPropertyAnimation, QEasingCurve, QRect, QPoint,
    QFileSystemWatcher,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QColor, QBrush,
    QPainterPath, QFont,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QLineEdit,
    QGridLayout, QComboBox, QSizePolicy,
    QStackedWidget, QTextBrowser, QTextEdit, QApplication,
    QDialog, QLayout, QListWidget, QListWidgetItem,
)

from difflib import SequenceMatcher

from gs_theme import *  # noqa: F403
from gs_widgets import _TouchScrollFilter
from great_sage_core import (
    legion_data, get_legion_data, get_bookmarks_data, save_json, load_json_cached,
    LEGION_BOOKMARKS, LEGION_PROGRESS, SCRIPT_DIR,
    sage_mod, _grep_book_for_term, get_matrix_data, get_session_groq_model,
)

# ── Constants ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

COVER_W = 150   # px — all covers forced to this width
COVER_H = 225   # px — all covers forced to this height (2:3 ratio)
GRID_COLS = 7

RR_BASE   = "https://www.royalroad.com"
LR_BASE   = "https://libread.com"
FWN_BASE  = "https://freewebnovel.com"
GX_BASE   = "https://gutendex.com"


def _lr_url_to_fwn(url: str) -> str:
    """
    Convert a libread.com URL to its freewebnovel.com equivalent.

    libread chapter : https://libread.com/libread/{slug}-{id}/chapter-{N}
      → fwn chapter : https://freewebnovel.com/novel/{slug}/chapter-{N}

    libread landing : https://libread.com/libread/{slug}-{id}
      → fwn landing  : https://freewebnovel.com/novel/{slug}

    The trailing numeric ID (e.g. -591) is stripped from the slug because
    freewebnovel does not include it.  Chapter padding is also dropped
    (fwn uses /chapter-1, not /chapter-01).

    If the URL is already a fwn URL it is returned unchanged.
    If it cannot be parsed it is returned unchanged.
    """
    import re as _re
    if "freewebnovel.com" in url:
        return url
    # Match /libread/{slug}/chapter-{N}  or  /libread/{slug}
    m = _re.search(r"/libread/([^/?#]+?)(?:/chapter-(\d+))?$", url)
    if not m:
        return url
    raw_slug   = m.group(1)                          # e.g. the-mighty-dragons-are-dead-591
    chapter_n  = m.group(2)                          # e.g. "01" or "00001" or None
    # Strip trailing numeric ID from slug: -591
    clean_slug = _re.sub(r"-\d+$", "", raw_slug)    # the-mighty-dragons-are-dead
    if chapter_n:
        n = int(chapter_n)                           # remove zero-padding
        return f"{FWN_BASE}/novel/{clean_slug}/chapter-{n}"
    return f"{FWN_BASE}/novel/{clean_slug}"

LOCAL_EXTENSIONS = {".epub", ".pdf", ".txt"}

# Local books folder — lives inside the repo so it's easy to find
LEGION_LIBRARY = str(SCRIPT_DIR / "Local")

# Ensure the folder exists on first run
Path(LEGION_LIBRARY).mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

class BookItem:
    """Normalised book/novel record from any source."""

    __slots__ = (
        "title", "author", "cover_url", "url",
        "source", "synopsis", "local_path", "file_type",
    )

    def __init__(
        self,
        title: str,
        author: str = "",
        cover_url: str = "",
        url: str = "",
        source: str = "",          # "royalroad" | "libread" | "gutenberg" | "local"
        synopsis: str = "",
        local_path: str = "",
        file_type: str = "",       # "EPUB" | "PDF" | "TXT" (local only)
    ):
        self.title      = title
        self.author     = author
        self.cover_url  = cover_url
        self.url        = url
        self.source     = source
        self.synopsis   = synopsis
        self.local_path = local_path
        self.file_type  = file_type


# ══════════════════════════════════════════════════════════════════════════════
# NETWORK WORKERS
# ══════════════════════════════════════════════════════════════════════════════

class FetchWorker(QThread):
    """
    Generic background worker.
    Runs `task()` and emits results(list) for lists, detail(dict) for dicts,
    or error(str) on failure.
    """
    results = pyqtSignal(list)
    detail  = pyqtSignal(dict)
    error   = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task      = task
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            data = self._task()
            if not self._cancelled:
                if isinstance(data, dict):
                    self.detail.emit(data)
                else:
                    self.results.emit(data or [])
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))



# ── Cover disk cache ──────────────────────────────────────────────────────────
_COVER_CACHE_DIR  = Path(os.path.expanduser("~/.cache/great_sage/covers"))
_COVER_CACHE_MAX_BYTES = 50 * 1024 * 1024   # 50 MB

def _cache_key(url: str) -> str:
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()

def _cover_cache_load(url: str) -> QPixmap | None:
    """Return cached QPixmap for url, or None if not cached."""
    path = _COVER_CACHE_DIR / (_cache_key(url) + ".jpg")
    if path.exists():
        px = QPixmap(str(path))
        return px if not px.isNull() else None
    return None

def _cover_cache_save(url: str, data: bytes):
    """Save raw image bytes to disk. Wipes entire cache if over 50MB."""
    try:
        _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Check total cache size — wipe and start fresh if over cap
        total = sum(f.stat().st_size for f in _COVER_CACHE_DIR.glob("*.jpg"))
        if total >= _COVER_CACHE_MAX_BYTES:
            for f in _COVER_CACHE_DIR.glob("*.jpg"):
                try:
                    f.unlink()
                except Exception:
                    pass

        path = _COVER_CACHE_DIR / (_cache_key(url) + ".jpg")
        path.write_bytes(data)
    except Exception:
        pass


class _CoverPool(QObject):
    """
    Singleton thread-pool for cover downloads.
    Max 8 concurrent downloads — no more thread explosion.
    Callbacks are delivered back to the main thread via a Qt signal.
    """
    _delivered = pyqtSignal(object, str, QPixmap)   # (callback_fn, url, pixmap)

    def __init__(self):
        super().__init__()
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cover")
        self._delivered.connect(self._dispatch)

    def request(self, url: str, callback):
        """Queue a cover download; callback(url, QPixmap) fires on the main thread."""
        self._pool.submit(self._fetch, url, callback)

    def _fetch(self, url: str, callback):
        # Cache hit — load from disk, no network
        cached = _cover_cache_load(url)
        if cached is not None:
            self._delivered.emit(callback, url, cached)
            return
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            img  = QImage()
            img.loadFromData(resp.content)
            if not img.isNull():
                _cover_cache_save(url, resp.content)
                px = QPixmap.fromImage(img).scaled(
                    COVER_W, COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._delivered.emit(callback, url, px)
                return
        except Exception:
            pass
        self._delivered.emit(callback, url, QPixmap())

    @staticmethod
    def _dispatch(callback, url: str, px: QPixmap):
        callback(url, px)


# Module-level singleton — shared by every CoverCard
_COVER_POOL = _CoverPool()


class CoverWorker(QThread):
    """
    Legacy shim kept so DetailPanel (which still uses CoverWorker directly) works.
    New card code uses _COVER_POOL instead.
    """
    done = pyqtSignal(str, QPixmap)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        cached = _cover_cache_load(self._url)
        if cached is not None:
            self.done.emit(self._url, cached)
            return
        try:
            resp = requests.get(self._url, headers=HEADERS, timeout=8)
            img  = QImage()
            img.loadFromData(resp.content)
            if not img.isNull():
                _cover_cache_save(self._url, resp.content)
                px = QPixmap.fromImage(img).scaled(
                    COVER_W, COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.done.emit(self._url, px)
                return
        except Exception:
            pass
        self.done.emit(self._url, QPixmap())


# ══════════════════════════════════════════════════════════════════════════════
# SCRAPERS / FETCHERS  (all run inside FetchWorker threads)
# ══════════════════════════════════════════════════════════════════════════════

def _title_score(title: str, query: str) -> float:
    t, q = title.lower().strip(), query.lower().strip()
    if t == q: return 1.0
    if t.startswith(q): return 0.9
    if q in t: return 0.75
    if all(w in t for w in q.split()): return 0.6
    return SequenceMatcher(None, q, t).ratio() * 0.5


# ── Library persistence ────────────────────────────────────────────────────────
# Categories mirror Matrix watchlist: planning / reading / dropped / completed
# Each entry: {"title", "author", "cover_url", "url", "source", "synopsis"}

LIBRARY_CATEGORIES = ("planning", "reading", "dropped", "completed")


def _load_library() -> dict:
    data = get_bookmarks_data()
    for cat in LIBRARY_CATEGORIES:
        data.setdefault(cat, [])
    return data


def _save_library(data: dict) -> bool:
    return save_json(LEGION_BOOKMARKS, data)


def library_add(book: "BookItem", category: str) -> bool:
    """Add book to library under category. Moves it if already in another category."""
    if category not in LIBRARY_CATEGORIES:
        return False
    data  = _load_library()
    entry = {
        "title":     book.title,
        "author":    book.author,
        "cover_url": book.cover_url,
        "url":       book.url,
        "source":    book.source,
        "synopsis":  book.synopsis,
    }
    # Remove from any existing category first
    for cat in LIBRARY_CATEGORIES:
        data[cat] = [e for e in data[cat] if e.get("title") != book.title]
    data[category].append(entry)
    result = _save_library(data)

    # ── Cloud sync ────────────────────────────────────────────────────────
    try:
        from gs_legion_sync import push_book
        push_book(
            title     = book.title,
            category  = category,
            cover_url = book.cover_url or "",
            book_url  = book.url or "",
            source    = book.source or "",
        )
    except Exception:
        pass

    return result


def library_remove(title: str) -> bool:
    """Remove a book from all library categories."""
    data = _load_library()
    changed = False
    for cat in LIBRARY_CATEGORIES:
        before = len(data[cat])
        data[cat] = [e for e in data[cat] if e.get("title") != title]
        if len(data[cat]) != before:
            changed = True
    if changed:
        _save_library(data)

    # ── Cloud sync ────────────────────────────────────────────────────────
    try:
        from gs_legion_sync import delete_book
        delete_book(title)
    except Exception:
        pass

    return changed


def library_get_category(title: str) -> str | None:
    """Return the category a book is in, or None."""
    data = _load_library()
    for cat in LIBRARY_CATEGORIES:
        if any(e.get("title") == title for e in data[cat]):
            return cat
    return None


# ── Chapter download registry ──────────────────────────────────────────────────
# Maps book_title → ChapterDownloadWorker so any caller can stop a worker by title.

class _DownloadRegistry:
    _workers: dict[str, "ChapterDownloadWorker"] = {}

    @classmethod
    def start(cls, book: "BookItem"):
        cls.stop(book.title)   # cancel any existing worker first
        w = ChapterDownloadWorker(book)
        cls._workers[book.title] = w
        w.start()

    @classmethod
    def stop(cls, title: str):
        w = cls._workers.pop(title, None)
        if w is not None:
            w.cancel()          # sets flag; worker exits between chapters

    @classmethod
    def get(cls, title: str) -> "ChapterDownloadWorker | None":
        return cls._workers.get(title)

    @classmethod
    def all_workers(cls) -> list:
        return list(cls._workers.values())

    @classmethod
    def is_running(cls, title: str) -> bool:
        w = cls._workers.get(title)
        return w is not None and w.isRunning()


class ChapterDownloadWorker(QThread):
    """
    Background chapter downloader for a Jump In book.

    Behaviour:
    - Resumes from last_downloaded_url stored in LEGION_PROGRESS (or starts
      from the book's detail-page first chapter if nothing is saved yet).
    - Chains through next_url until it reaches the end of published chapters.
    - When caught up, waits POLL_INTERVAL seconds then re-checks the last
      chapter for a new next_url.
    - Saves last_downloaded_chapter + last_downloaded_url to LEGION_PROGRESS
      after every successful chapter save.
    - Checks _cancelled between every chapter fetch so cancellation is
      near-instantaneous and never blocks the UI.
    """

    POLL_INTERVAL = 120   # 2 minutes

    chapter_downloaded = pyqtSignal(str, int)  # (book_title, new_chapter_count)

    def __init__(self, book: "BookItem", parent=None):
        super().__init__(parent)
        self._book      = book
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_start_url(self) -> str | None:
        """
        Return the URL to resume downloading from, or None if we can't determine one.

        We save the URL of the successfully written chapter (not next_url).
        On resume we must advance one chapter forward — otherwise we re-download
        and duplicate the last written chapter.

        Strategy:
        1. Load saved last_downloaded_url from LEGION_PROGRESS.
        2. If it's a chapter URL (contains /chapter-N), derive the next chapter
           URL arithmetically. For fwn this is trivial: /chapter-{N+1}.
        3. Verify the derived URL exists (HEAD request) before committing to it.
        4. If verification fails or no saved URL, fall back to fetching chapter 1
           from the book's landing page.
        """
        import re as _re
        try:
            data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
            entry = data.get("books", {}).get(self._book.title, {})
            saved = entry.get("last_downloaded_url", "")

            if saved and "/chapter-" in saved:
                # Validate it's a genuine fwn or RR chapter URL, not a landing page
                # that somehow slipped through a previous bug.
                if "freewebnovel.com" in saved or "royalroad.com" in saved:
                    m = _re.search(r"/chapter-(\d+)$", saved)
                    if m:
                        n        = int(m.group(1))
                        base_url = saved[:saved.rfind("/")]
                        candidate = f"{base_url}/chapter-{n + 1}"
                        # Quick HEAD to confirm the chapter exists before we
                        # commit to it. Use the same plain session — no cloudscraper.
                        try:
                            s    = _fwn_session()
                            head = s.head(candidate, timeout=8, allow_redirects=True)
                            if head.status_code < 400:
                                return candidate
                            # 404 → we're already at the end of published chapters.
                            # Return the last saved URL so the main loop can enter
                            # its polling block immediately without re-downloading.
                            return saved
                        except Exception:
                            # Network hiccup — return candidate anyway; the main
                            # loop will 404 and enter polling, which is correct.
                            return candidate
                    # URL has /chapter-N but pattern didn't match (unusual slug).
                    # Fall through to first-chapter resolve.
        except Exception:
            pass

        # No valid saved URL — resolve chapter 1 from the landing page.
        return self._fetch_first_chapter_url()

    def _fetch_first_chapter_url(self) -> str | None:
        """Fetch the fiction/book page and return the first chapter URL."""
        try:
            url = self._book.url
            if not url:
                return None
            if "libread.com" in url or "freewebnovel.com" in url:
                # libread landing pages redirect to freewebnovel — go direct.
                fwn_url = _lr_url_to_fwn(url) if "libread.com" in url else url
                s = _fwn_session()   # plain requests — confirmed working
                r = s.get(fwn_url, timeout=12)
                if r.status_code != 200:
                    return None
                soup = BeautifulSoup(r.text, "html.parser")
                # fwn first chapter link: /novel/{slug}/chapter-1
                # Target the read button or chapter-1 link specifically.
                # Avoid picking up random chapter links from recommended novels.
                a = (
                    soup.select_one("a.btn-read[href*='/chapter-']")
                    or soup.select_one("a[href$='/chapter-1']")
                    or soup.select_one("a[href*='/chapter-']")
                )
                if a:
                    href = a["href"]
                    return (FWN_BASE + href) if href.startswith("/") else href
            else:
                # Royal Road
                s = _session()
                r = s.get(url, timeout=10)
                if r.status_code != 200:
                    return None
                soup = BeautifulSoup(r.text, "html.parser")
                a = soup.select_one("table#chapters a[href*='/chapter/']")
                if a:
                    href = a["href"]
                    return (RR_BASE + href) if href.startswith("/") else href
        except Exception:
            pass
        return None

    # Sentinel returned when a chapter URL definitively does not exist (404 / no content).
    # Callers use this to distinguish end-of-book from transient network failures.
    _NOT_FOUND = "NOT_FOUND"

    def _fetch_chapter(self, url: str):
        """
        Fetch one chapter. Returns (title, paragraphs, next_url, error).
        Uses the same proven logic as ChapterFetchWorker.

        error == ChapterDownloadWorker._NOT_FOUND  →  chapter does not exist (end of book)
        error == <other string>                    →  transient network/parse failure
        error is None                              →  success
        """
        try:
            if "libread.com" in url or "freewebnovel.com" in url:
                # Reuse the proven libread fetcher via a temporary worker instance
                tmp = ChapterFetchWorker(url, self._book.title)
                title, paragraphs, next_url, _prev, error = tmp._fetch_libread(url)
                # Treat HTTP errors (404, 403, etc.) as permanent — chapter doesn't exist
                if error and ("HTTP 4" in error or "HTTP 5" in error):
                    return title, [], None, self._NOT_FOUND
                # "No content found" means the page exists but is empty — LibRead returns
                # 200 on ghost URLs past the real end of the book.  Treat as end-of-book,
                # NOT as a transient failure (which would cause infinite retry loops).
                if error == "No content found":
                    return title, [], None, self._NOT_FOUND
                return title, paragraphs, next_url, error
            else:
                # Royal Road via legion.fetch_chapter
                from great_sage_core import legion_mod
                mod, err = legion_mod()
                if err or not mod:
                    return None, [], None, f"Legion module unavailable: {err}"
                title, paragraphs, next_url, _prev, error, _ = mod.fetch_chapter(
                    url, self._book.title)
                # Treat HTTP 4xx as permanent
                if error and ("HTTP 4" in error or "404" in error):
                    return title, [], None, self._NOT_FOUND
                return title or "Chapter", paragraphs or [], next_url, error
        except Exception as e:
            return None, [], None, str(e)

    def _save_progress(self, chapter_num: int, url: str):
        """Persist last_downloaded_chapter + url to LEGION_PROGRESS."""
        # Never persist a landing-page URL as a resume point — only chapter URLs are valid.
        if "/chapter-" not in url:
            return
        try:
            data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            # Resurrection guard: if the book was removed from LEGION_PROGRESS while
            # this worker was still running (e.g. user hit Remove), do NOT re-create
            # the entry.  setdefault would silently resurrect it — check first.
            if self._book.title not in data.get("books", {}):
                return
            entry = data["books"][self._book.title]
            entry["last_downloaded_chapter"] = chapter_num
            entry["last_downloaded_url"]     = url
            save_json(LEGION_PROGRESS, data)
        except Exception:
            pass

    def _chapter_num_from_progress(self) -> int:
        """Return the last downloaded chapter number (0 if none)."""
        try:
            data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
            entry = data.get("books", {}).get(self._book.title, {})
            return int(entry.get("last_downloaded_chapter", 0))
        except Exception:
            return 0

    # ── Gutenberg helpers ─────────────────────────────────────────────────────

    _CHAPTER_RE = re.compile(
        r"^(CHAPTER|Chapter|PART|Part|BOOK|Book|VOLUME|Volume|SECTION|Section|"
        r"Prologue|PROLOGUE|Epilogue|EPILOGUE)"
        r"[\s\.\-]*(M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})|\d{1,3}|"
        r"ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|"
        r"ELEVEN|TWELVE|THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY)?"
        r"[\s\.\-:]*([A-Z][^a-z]{0,60})?\.?\s*$",
        re.MULTILINE,
    )
    _FILTER_RE = re.compile(r"^\[.*?\]$")

    def _gutenberg_download(self) -> list:
        """One-shot download and chapter split for Gutenberg books."""
        url = self._book.url
        s   = _session()
        try:
            r = s.get(url, timeout=60)
        except Exception:
            return []
        if r.status_code != 200:
            return []
        text = r.text
        start_m = re.search(r"\*{3}\s*START OF.*?\*{3}", text, re.IGNORECASE)
        if start_m:
            text = text[start_m.end():]
        end_m = re.search(r"\*{3}\s*END OF.*?\*{3}", text, re.IGNORECASE)
        if end_m:
            text = text[:end_m.start()]
        text  = text.strip()
        lines = text.splitlines()

        splits = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or len(stripped) > 80:
                continue
            if self._CHAPTER_RE.match(stripped):
                splits.append((i, re.sub(r"[\]\[\.]+$", "", stripped).strip()))

        def to_paragraphs(ls):
            paras, cur = [], []
            for l in ls:
                if l.strip():
                    cur.append(l.strip())
                else:
                    if cur:
                        paras.append(" ".join(cur))
                        cur = []
            if cur:
                paras.append(" ".join(cur))
            return [p for p in paras if len(p) > 10 and not self._FILTER_RE.match(p)]

        if len(splits) >= 2:
            chapters = []
            for idx, (line_idx, heading) in enumerate(splits):
                end   = splits[idx + 1][0] if idx + 1 < len(splits) else len(lines)
                paras = to_paragraphs(lines[line_idx + 1:end])
                if paras:
                    chapters.append((heading, paras))
            return chapters
        else:
            all_paras = to_paragraphs(lines)
            return [
                (f"Part {i // 120 + 1}", all_paras[i:i + 120])
                for i in range(0, len(all_paras), 120)
            ]

    def _run_gutenberg(self):
        """Download and store a complete Gutenberg book. Runs once, no polling."""
        if self._chapter_num_from_progress() > 0:
            return   # already downloaded
        chapters = self._gutenberg_download()
        if not chapters or self._cancelled:
            return
        try:
            from great_sage_core import legion_mod
            mod, err = legion_mod()
            if not mod or err:
                return
        except Exception:
            return
        for chapter_num, (title, paragraphs) in enumerate(chapters, start=1):
            if self._cancelled:
                return
            mod.append_chapter_to_file(self._book.title, chapter_num, title, paragraphs)
            self._save_progress(chapter_num, self._book.url)

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self):
        # Gutenberg — one-shot download, no chaining, no polling
        if self._book.source == "gutenberg":
            self._run_gutenberg()
            return

        url = self._load_start_url()
        if not url or self._cancelled:
            return

        # Bug 2 fix: use actual disk chapter count as the authoritative baseline.
        # The JSON counter drifts over restarts and cannot be trusted.
        try:
            from great_sage_core import legion_mod as _lm2
            _lmod2, _ = _lm2()
            _disk = _lmod2._get_chapter_list_from_file(self._book.title) if _lmod2 else []
            chapter_num = len(_disk) if _disk else self._chapter_num_from_progress()
        except Exception:
            chapter_num = self._chapter_num_from_progress()

        # Safety: derive base_url for arithmetic fallback from the book's canonical
        # landing-page URL stored in LEGION_PROGRESS, not from the current chapter URL.
        # This prevents a drifted/truncated chapter URL from corrupting the book ID in
        # all subsequent arithmetic-generated URLs.
        try:
            _prog = load_json_cached(LEGION_PROGRESS, {"books": {}})
            _landing = _prog.get("books", {}).get(self._book.title, {}).get("url", "")
        except Exception:
            _landing = ""
        self._landing_url = _landing or self._book.url

        _consecutive_empty = 0   # guard against ghost-URL chains (LibRead 200 on dead pages)

        while not self._cancelled:
            title, paragraphs, next_url, error = self._fetch_chapter(url)

            if self._cancelled:
                return

            if error == self._NOT_FOUND:
                # Chapter definitively doesn't exist — reached end of published content.
                # Don't increment chapter_num; fall into the polling loop below.
                next_url          = None
                error             = None
                paragraphs        = []   # ensure write block is skipped
                _consecutive_empty = 0

            if error or not paragraphs:
                if error:
                    # Transient network/parse failure — back off and retry same URL
                    for _ in range(30):   # 30 × 1s = 30s back-off
                        if self._cancelled:
                            return
                        self.msleep(1000)
                    continue
                # Empty paragraphs — fall through; junk filter + has_real_content
                # below handles consecutive empty tracking and caught-up detection.

            # Strip junk paragraphs before content check
            if paragraphs:
                try:
                    from great_sage_core import legion_mod as _lm
                    _lmod, _ = _lm()
                    if _lmod and hasattr(_lmod, "_is_junk_paragraph"):
                        paragraphs = [p for p in paragraphs
                                      if not _lmod._is_junk_paragraph(p)]
                except Exception:
                    pass

            has_real_content = bool(paragraphs) and len("".join(paragraphs)) >= 150

            if not has_real_content:
                _consecutive_empty += 1
                if _consecutive_empty >= 3:
                    next_url           = None
                    _consecutive_empty = 0
            else:
                _consecutive_empty = 0
                chapter_num += 1

                written = False
                try:
                    from great_sage_core import legion_mod
                    mod, err = legion_mod()
                    if mod and not err:
                        mod.append_chapter_to_file(self._book.title, chapter_num, title, paragraphs)
                        written = True
                except Exception:
                    pass

                if written:
                    # Save the URL of the chapter we just successfully wrote, NOT next_url.
                    # Saving next_url caused resume to skip this chapter (treating it as
                    # already done) and re-download the last chapter before end-of-book
                    # (when next_url is None, the fallback `or url` is the same as saving
                    # url, but only after the duplicate was written). Always save `url`.
                    self._save_progress(chapter_num, url)
                    self.chapter_downloaded.emit(self._book.title, chapter_num)
                else:
                    chapter_num -= 1

            if self._cancelled:
                return

            if next_url:
                url = next_url
                self.msleep(800)   # polite delay between chapters
            else:
                # Caught up — poll until a new chapter appears
                last_url = url
                _consecutive_empty = 0
                while not self._cancelled:
                    for _ in range(self.POLL_INTERVAL):
                        if self._cancelled:
                            return
                        self.msleep(1000)

                    # Re-fetch the last chapter to check for a new next_url
                    _, _, new_next, _ = self._fetch_chapter(last_url)
                    if self._cancelled:
                        return
                    if new_next:
                        url = new_next
                        break   # exit poll loop, continue download loop


def jump_in_add(book: "BookItem"):
    """Add book to legion progress (Jump In) without overwriting existing progress."""
    data = load_json_cached(LEGION_PROGRESS, {"books": {}})
    if book.title not in data.get("books", {}):
        data.setdefault("books", {})[book.title] = {
            "title":                   book.title,
            "author":                  book.author,
            "cover_url":               book.cover_url,
            "url":                     book.url,
            "source":                  book.source,
            "synopsis":                book.synopsis,
            "chapters_read":           0,
            "current_chapter":         0,
            "last_read":               0,
            "last_downloaded_chapter": 0,
            "last_downloaded_url":     "",
            "download_state": {
                "status":                      "idle",
                "total_chapters_downloaded":   0,
                "last_downloaded_chapter":     None,
                "last_downloaded_chapter_num": 0,
                "download_path":               None,
                "failed_chapters":             [],
                "timestamp":                   0,
                "pause_requested":             False,
            },
        }
        save_json(LEGION_PROGRESS, data)

    # Always (re)start the download worker — book may already exist in progress
    # but its worker may not be running (e.g. first click after app launch, or
    # after the book was previously added but the worker never started).
    if book.source in ("royalroad", "libread", "gutenberg"):
        if not _DownloadRegistry.is_running(book.title):
            _DownloadRegistry.start(book)


def jump_in_remove(title: str, delete_files: bool = False) -> bool:
    """Remove book from legion progress (Jump In).
    If delete_files=True, also wipe the downloaded chapters from disk.
    Always stops any running download worker immediately.
    """
    # Stop downloader first — non-blocking (sets cancel flag only)
    _DownloadRegistry.stop(title)

    data = load_json_cached(LEGION_PROGRESS, {"books": {}})
    if title in data.get("books", {}):
        del data["books"][title]
        save_json(LEGION_PROGRESS, data)

    if delete_files:
        try:
            import shutil as _shutil
            from great_sage_core import legion_mod
            mod, err = legion_mod()
            if mod and not err:
                import re as _re, os as _os
                safe     = _re.sub(r'[^\w\-_\. ]', '_', title)
                book_dir = _os.path.join(mod.LIBRARY_DIR, safe)
                if _os.path.isdir(book_dir):
                    _shutil.rmtree(book_dir, ignore_errors=True)
        except Exception:
            pass

    return True


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


# diag.py proved that plain requests.Session + Chrome UA = HTTP 200 on fwn.
# cloudscraper (both default and with explicit UA) = HTTP 403. fwn detects
# the cloudscraper TLS fingerprint and blocks it. Do NOT reintroduce cloudscraper
# here under any framing — it will 403. One shared session per call is sufficient;
# fwn does not require cookie persistence across the chapter chain because the
# download worker creates a new session per chapter anyway (acceptable overhead
# vs. the complexity of a shared session that can expire mid-download).
_FWN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language":           "en-US,en;q=0.9",
    # Accept-Encoding intentionally omitted — requests adds it automatically
    # and handles decompression. Setting it manually disables auto-decode,
    # causing BeautifulSoup to receive raw compressed binary garbage.
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
}


def _fwn_session() -> requests.Session:
    """Plain requests.Session that works on freewebnovel.com (HTTP 200, confirmed)."""
    s = requests.Session()
    s.headers.update(_FWN_HEADERS)
    return s


# libread.com redirects to freewebnovel.com. We go direct to fwn so we never
# follow the redirect chain. Same session factory applies — no cloudscraper.
_libread_scraper = _fwn_session   # alias; callers updated below


def fetch_royalroad_trending(page: int = 1) -> list[BookItem]:
    s    = _session()
    r    = s.get(f"{RR_BASE}/fictions/trending", params={"page": page}, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for card in soup.select("div.fiction-list-item"):
        title_el = card.select_one("h2.fiction-title")
        cover_el = card.select_one("img")
        link_el  = card.select_one("a[href*='/fiction/']")
        if not title_el or not link_el:
            continue
        synopsis_el = card.select_one("div.fiction-description")
        author_el   = card.select_one("span.author a, h4.font-white a, .author a")
        items.append(BookItem(
            title     = title_el.text.strip(),
            author    = author_el.text.strip() if author_el else "",
            cover_url = cover_el["src"] if cover_el else "",
            url       = RR_BASE + link_el["href"],
            source    = "royalroad",
            synopsis  = synopsis_el.get_text(" ", strip=True)[:400] if synopsis_el else "",
        ))
    return items



def fetch_royalroad_best_rated(page: int = 1) -> list[BookItem]:
    s    = _session()
    r    = s.get(f"{RR_BASE}/fictions/best-rated", params={"page": page}, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for card in soup.select("div.fiction-list-item"):
        title_el = card.select_one("h2.fiction-title")
        cover_el = card.select_one("img")
        link_el  = card.select_one("a[href*='/fiction/']")
        if not title_el or not link_el:
            continue
        synopsis_el = card.select_one("div.fiction-description")
        author_el   = card.select_one("span.author a, h4.font-white a, .author a")
        items.append(BookItem(
            title     = title_el.text.strip(),
            author    = author_el.text.strip() if author_el else "",
            cover_url = cover_el["src"] if cover_el else "",
            url       = RR_BASE + link_el["href"],
            source    = "royalroad",
            synopsis  = synopsis_el.get_text(" ", strip=True)[:400] if synopsis_el else "",
        ))
    return items

def fetch_royalroad_detail(url: str) -> dict:
    """Fetch synopsis and chapter list for a Royal Road fiction page."""
    s = _session()
    r = s.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Synopsis — RR wraps it in div.description > div (inner content)
    synopsis = ""
    syn_el = soup.select_one("div.description")
    if syn_el:
        # Remove any nested script/style tags
        for tag in syn_el.find_all(["script", "style"]):
            tag.decompose()
        synopsis = syn_el.get_text("\n", strip=True)

    # Author — RR detail page has it in h4.font-white > a or span[property="name"]
    author = ""
    author_el = soup.select_one("h4.font-white a, span[property='name'], .author a")
    if author_el:
        author = author_el.get_text(strip=True)

    # Chapters — listed in a table with class fiction-list-chapter or in tbody tr
    chapters = []
    for row in soup.select("table#chapters tbody tr, table.table tbody tr"):
        a = row.select_one("td a[href*='/chapter/']")
        if a:
            chapters.append({
                "title": a.text.strip(),
                "url":   RR_BASE + a["href"] if a["href"].startswith("/") else a["href"],
            })

    return {"synopsis": synopsis, "author": author, "chapters": chapters, "total_pages": 1}


def fetch_libread_popular(page: int = 1) -> list[BookItem]:
    """Fetch popular books from FreeWebNovel (the live backend for LibRead content)."""
    # libread.com/most-popular redirects to freewebnovel.com — go direct.
    # fwn uses the same li-row / h3.tit structure as old libread.
    s   = _fwn_session()
    url = f"{FWN_BASE}/most-popular" if page == 1 else f"{FWN_BASE}/most-popular?page={page}"
    try:
        r = s.get(url, timeout=12)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    soup  = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select("div.ul-list1 div.li-row, div.list div.li-row"):
        title_el = row.select_one("h3.tit a, h3 a")
        if not title_el:
            continue
        href      = title_el.get("href", "")
        cover_el  = row.select_one("img")
        cover_url = cover_el.get("src", "") if cover_el else ""
        # fwn serves cover paths as absolute URLs already; normalise just in case
        if cover_url and cover_url.startswith("/"):
            cover_url = FWN_BASE + cover_url
        author_el   = row.select_one("p.author a, span.author")
        synopsis_el = row.select_one("p.intro")
        # Build the canonical fwn URL — href is like /novel/slug
        book_url = (FWN_BASE + href) if href.startswith("/") else href
        items.append(BookItem(
            title     = title_el.text.strip(),
            author    = author_el.text.strip() if author_el else "",
            cover_url = cover_url,
            url       = book_url,
            source    = "libread",   # keep source label so Jump In worker picks it up
            synopsis  = synopsis_el.get_text(" ", strip=True)[:400] if synopsis_el else "",
        ))
    return items


def _parse_gutendex_results(results: list) -> list[BookItem]:
    """Convert a list of Gutendex book dicts into BookItems."""
    items = []
    for book in results:
        title     = book.get("title", "").strip()
        authors   = ", ".join(a["name"] for a in book.get("authors", []))
        cover_url = book.get("formats", {}).get("image/jpeg", "")
        txt_url   = (
            book.get("formats", {}).get("text/plain; charset=utf-8")
            or book.get("formats", {}).get("text/html; charset=utf-8")
            or book.get("formats", {}).get("text/plain")
            or ""
        )
        if not title or not txt_url:
            continue
        # Build a synopsis from subjects — Gutendex has no description field
        subjects  = book.get("subjects", [])
        synopsis  = "; ".join(subjects[:6]) if subjects else ""
        # Store the Gutenberg book ID in url so detail can look up synopsis later
        book_id   = book.get("id", "")
        items.append(BookItem(
            title     = title,
            author    = authors,
            cover_url = cover_url,
            url       = txt_url,
            source    = "gutenberg",
            synopsis  = synopsis,
        ))
    return items


def fetch_gutenberg_popular(page: int = 1) -> list[BookItem]:
    """Fetch a page of popular Gutenberg books."""
    s = _session()
    r = s.get(f"{GX_BASE}/books/", params={"sort": "popular", "languages": "en", "page": page}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return _parse_gutendex_results(data.get("results", []))


def search_gutenberg(query: str, page: int = 1) -> list[BookItem]:
    """Search Project Gutenberg via the Gutendex API."""
    s = _session()
    r = s.get(f"{GX_BASE}/books/", params={"search": query, "languages": "en", "page": page}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return _parse_gutendex_results(data.get("results", []))


def fetch_gutenberg_detail(url: str) -> dict:
    """
    Fetch synopsis/metadata for a Gutenberg book.
    url is the Gutendex API endpoint e.g. https://gutendex.com/books/1342/
    """
    try:
        s = _session()
        r = s.get(url, timeout=10)
        r.raise_for_status()
        data     = r.json()
        subjects = data.get("subjects", [])
        synopsis = (
            "Subjects: " + ", ".join(subjects[:6])
            if subjects else "No synopsis available for this Gutenberg title."
        )
        return {"synopsis": synopsis, "chapters": [], "total_pages": 0}
    except Exception:
        return {"synopsis": "", "chapters": [], "total_pages": 0}


def search_royalroad(query: str, page: int = 1) -> list[BookItem]:
    s    = _session()
    r    = s.get(f"{RR_BASE}/fictions/search", params={"title": query, "page": page}, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for card in soup.select("div.fiction-list-item"):
        title_el = card.select_one("h2.fiction-title")
        cover_el = card.select_one("img")
        link_el  = card.select_one("a[href*='/fiction/']")
        if not title_el or not link_el:
            continue
        author_el   = card.select_one("span.author a, h4.font-white a, .author a")
        synopsis_el = card.select_one("div.fiction-description")
        items.append(BookItem(
            title     = title_el.text.strip(),
            author    = author_el.text.strip() if author_el else "",
            cover_url = cover_el["src"] if cover_el else "",
            url       = RR_BASE + link_el["href"],
            source    = "royalroad",
            synopsis  = synopsis_el.get_text(" ", strip=True)[:400] if synopsis_el else "",
        ))
    return items


def search_libread(query: str, page: int = 1) -> list[BookItem]:
    """Search FreeWebNovel (the live backend for LibRead content)."""
    # libread.com/search is a dead redirect. fwn /search?searchkey= works
    # and was confirmed returning 50 results in test_scraper.py.
    s = _fwn_session()
    params = {"searchkey": query}
    if page > 1:
        params["page"] = page
    try:
        r = s.get(f"{FWN_BASE}/search", params=params, timeout=12)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    soup  = BeautifulSoup(r.text, "html.parser")
    items = []
    # fwn search results use the same li-row structure as the popular page
    for row in soup.select("div.ul-list1 div.li-row, div.list div.li-row"):
        title_el = row.select_one("h3.tit a, h3 a")
        if not title_el:
            continue
        href      = title_el.get("href", "")
        cover_el  = row.select_one("img")
        cover_url = cover_el.get("src", "") if cover_el else ""
        if cover_url and cover_url.startswith("/"):
            cover_url = FWN_BASE + cover_url
        author_el   = row.select_one("p.author a, span.author")
        synopsis_el = row.select_one("p.intro")
        book_url = (FWN_BASE + href) if href.startswith("/") else href
        items.append(BookItem(
            title     = title_el.text.strip(),
            author    = author_el.text.strip() if author_el else "",
            cover_url = cover_url,
            url       = book_url,
            source    = "libread",
            synopsis  = synopsis_el.get_text(" ", strip=True)[:400] if synopsis_el else "",
        ))
    return items

def fetch_libread_detail(url: str) -> dict:
    """Fetch synopsis, author and chapter list for a LibRead book.

    libread.com landing pages redirect to freewebnovel.com, so we convert
    the URL up-front and scrape fwn directly using its confirmed selectors.
    """
    import re as _re

    fwn_url = _lr_url_to_fwn(url) if "libread.com" in url else url

    s = _fwn_session()  # _fwn_scraper() was removed; use plain session
    r = s.get(fwn_url, timeout=12)
    if r.status_code != 200:
        return {"synopsis": "", "author": "", "chapters": [], "total_pages": 0}

    soup = BeautifulSoup(r.text, "html.parser")

    # Synopsis — fwn stores it in div.m-desc (same class as old libread, confirmed by test)
    synopsis = ""
    desc_div = soup.find("div", class_="m-desc")
    if desc_div:
        paras = [
            p.get_text(strip=True) for p in desc_div.find_all("p")
            if p.get_text(strip=True)
            and "vote" not in p.get("class", [])
            and not any(x in p.get_text().lower() for x in ["facebook", "twitter", "whatsapp", "pinterest"])
        ]
        synopsis = "\n\n".join(paras)

    # Author — fwn uses same selector pattern (span[title="Author"] ~ div.right a.a1)
    author = ""
    author_el = soup.select_one('span[title="Author"] ~ div.right a.a1')
    if not author_el:
        # Fallback: look for any author-labelled element
        author_el = soup.select_one(".author a, .writer a")
    if author_el:
        author = author_el.get_text(strip=True)

    # Chapter count — fwn lists chapters as <a href="/novel/{slug}/chapter-N"> links
    # Extract the highest chapter number from all chapter links on the page
    chapter_links = soup.select("a[href*='/chapter-']")
    total = 0
    for a in chapter_links:
        href = a.get("href", "")
        cm = _re.search(r"/chapter-(\d+)$", href)
        if cm:
            total = max(total, int(cm.group(1)))

    # Fallback: try numeric count from page text
    if not total:
        count_el = soup.select_one(".chapter-count, span.s1")
        if count_el:
            cm = _re.search(r"(\d+)", count_el.get_text())
            if cm:
                total = int(cm.group(1))

    if not total:
        total = 1

    # Build chapter list using fwn URL format (no padding)
    # Derive slug from the fwn_url: https://freewebnovel.com/novel/{slug}
    slug_m = _re.search(r"/novel/([^/?#]+)$", fwn_url)
    chapters = []
    if slug_m:
        slug = slug_m.group(1)
        for i in range(1, min(total + 1, 201)):   # cap at 200
            chapters.append({
                "title": f"Chapter {i}",
                "url":   f"{FWN_BASE}/novel/{slug}/chapter-{i}",
            })

    return {"synopsis": synopsis, "author": author, "chapters": chapters, "total_pages": 1}


def scan_local_library() -> list[BookItem]:
    """
    Scan LEGION_LIBRARY for EPUB / PDF / TXT files and return them as BookItems.
    Returns an empty list (never raises) if the folder doesn't exist or is unreadable.
    """
    items = []
    library = Path(LEGION_LIBRARY)
    if not library.is_dir():
        return items
    try:
        for path in sorted(library.rglob("*")):
            if path.suffix.lower() not in LOCAL_EXTENSIONS:
                continue
            title     = path.stem.replace("_", " ").replace("-", " ").strip()
            file_type = path.suffix.lstrip(".").upper()
            items.append(BookItem(
                title      = title,
                source     = "local",
                local_path = str(path),
                file_type  = file_type,
            ))
    except Exception:
        pass
    return items


# ══════════════════════════════════════════════════════════════════════════════
# COVER CARD WIDGET
# ══════════════════════════════════════════════════════════════════════════════

class CoverCard(QWidget):
    """
    Single book card: fixed-size cover image + title label below.
    Emits clicked(BookItem) on tap, delete_requested(BookItem) when ✕ is pressed
    in edit mode.
    """
    clicked          = pyqtSignal(object)  # BookItem
    delete_requested = pyqtSignal(object)  # BookItem

    def __init__(self, book: BookItem, parent=None):
        super().__init__(parent)
        self.book       = book
        self._pixmap    = None
        self._worker    = None
        self._drag_start = None   # tracks press position for touch-scroll vs tap
        self.setFixedWidth(COVER_W)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build()
        if book.cover_url:
            self._load_cover()
        # No cover_url → show placeholder icon, card stays visible

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        # Cover frame
        self._cover = QLabel()
        self._cover.setFixedSize(COVER_W, COVER_H)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover.setStyleSheet(
            f"background:{BG2}; border:1px solid {BORDER}; border-radius:5px;")
        self._set_placeholder()
        lay.addWidget(self._cover)

        # Title
        self._title_lbl = QLabel(self.book.title)
        self._title_lbl.setFixedWidth(COVER_W)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._title_lbl.setStyleSheet(
            f"color:{TEXT2}; font-size:14px; background:transparent; border:none;")
        self._title_lbl.setMaximumHeight(80)
        lay.addWidget(self._title_lbl)

        # File type badge for local files
        if self.book.source == "local" and self.book.file_type:
            badge = QLabel(self.book.file_type)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"background:{BG3}; color:{ACCENT}; font-size:8px; "
                f"letter-spacing:1px; border:1px solid {BORDER2}; border-radius:3px; "
                f"padding:1px 4px;")
            lay.addWidget(badge)

        # Delete overlay (shown in edit mode)
        self._del_btn = QPushButton("✕", self._cover)
        self._del_btn.setFixedSize(22, 22)
        self._del_btn.move(COVER_W - 26, 4)
        self._del_btn.setStyleSheet(
            "QPushButton{background:#c0392b; color:white; border:none; "
            "border-radius:11px; font-size:11px; font-weight:bold;}"
            "QPushButton:hover{background:#e74c3c;}")
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.clicked.connect(lambda: self.delete_requested.emit(self.book))
        self._del_btn.hide()

    def _set_placeholder(self):
        icon = {"local": "📄", "gutenberg": "📖"}.get(self.book.source, "📚")
        self._cover.setText(icon)
        self._cover.setStyleSheet(
            f"background:{BG2}; border:1px solid {BORDER}; border-radius:5px; "
            f"font-size:28px; color:{BORDER2};")

    def _load_cover(self):
        _COVER_POOL.request(self.book.cover_url, self._on_cover)

    def _on_cover(self, url: str, px: QPixmap):
        try:
            _ = self._cover.objectName()  # raises RuntimeError if C++ obj deleted
        except RuntimeError:
            return
        if px.isNull():
            self._set_placeholder()
            return
        # Crop to exact COVER_W × COVER_H
        scaled = px.scaled(
            COVER_W, COVER_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Rounded corners via painter
        result = QPixmap(COVER_W, COVER_H)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, COVER_W, COVER_H, 5, 5)
        p.setClipPath(path)
        # Centre-crop
        sx = (scaled.width()  - COVER_W) // 2
        sy = (scaled.height() - COVER_H) // 2
        p.drawPixmap(0, 0, scaled, sx, sy, COVER_W, COVER_H)
        p.end()
        self._pixmap = result
        try:
            self._cover.setPixmap(result)
            self._cover.setStyleSheet(
                "background:transparent; border:none; border-radius:5px;")
        except RuntimeError:
            pass

    def enterEvent(self, e):
        self._cover.setStyleSheet(
            f"background:{BG2}; border:1px solid {ACCENT}; border-radius:5px;"
            + ("" if self._pixmap else f" font-size:28px; color:{BORDER2};"))
        self._title_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:10px; background:transparent; border:none;")
        if self._pixmap:
            self._cover.setPixmap(self._pixmap)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._cover.setStyleSheet(
            f"background:{BG2}; border:1px solid {BORDER}; border-radius:5px;"
            + ("" if self._pixmap else f" font-size:28px; color:{BORDER2};"))
        self._title_lbl.setStyleSheet(
            f"color:{TEXT2}; font-size:10px; background:transparent; border:none;")
        if self._pixmap:
            self._cover.setPixmap(self._pixmap)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            delta = (e.position().toPoint() - self._drag_start).manhattanLength()
            if delta < 8:   # genuine tap, not a scroll gesture
                self.clicked.emit(self.book)
            self._drag_start = None
        super().mouseReleaseEvent(e)

    def set_delete_mode(self, enabled: bool):
        """Show/hide the red ✕ delete button on this card."""
        self._del_btn.setVisible(enabled)


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL PANEL
# ══════════════════════════════════════════════════════════════════════════════

class FlowLayout(QLayout):
    """Wrapping flow layout for genre chips."""
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items   = []
        self._spacing = spacing

    def setSpacing(self, s):
        self._spacing = s

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size + QSize(0, 0)

    def _do_layout(self, rect, test):
        x, y, row_h = rect.x(), rect.y(), 0
        for item in self._items:
            w = item.widget()
            if not w:
                continue
            iw = item.sizeHint().width()
            ih = item.sizeHint().height()
            if x + iw > rect.right() and x > rect.x():
                x  = rect.x()
                y += row_h + self._spacing
                row_h = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x     += iw + self._spacing
            row_h  = max(row_h, ih)
        return y + row_h - rect.y()


class DetailPanel(QWidget):
    """
    Slides in from the right over the discovery grid.
    Shows cover, title, author, source, synopsis, and chapter list
    (or a Read button for Gutenberg / local files).
    """
    closed      = pyqtSignal()
    book_action = pyqtSignal(str, object)  # (action, BookItem) — "read" | "library_add" | "library_remove"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG2}; border-left:1px solid {BORDER};")
        self._detail_worker       = None
        self._detail_cover_worker = None
        self._first_chapter_url: str | None = None
        self._preview_worker: "PreviewWorker | None" = None
        self._original_synopsis: str = ""
        self._build()
        self.hide()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────────────────
        topbar = QWidget()
        topbar.setStyleSheet(
            f"background:{BG3}; border-bottom:1px solid {BORDER};")
        topbar.setFixedHeight(44)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 0, 14, 0)

        back_btn = QPushButton("← Back")
        back_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; color:{MUTED}; "
            f"font-size:11px; letter-spacing:0.5px;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self.close_panel)
        tb.addWidget(back_btn)
        tb.addStretch()
        root.addWidget(topbar)

        # ── Scrollable content ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            "QScrollBar:vertical{width:4px; background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{BORDER2}; border-radius:2px;}}")

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(24, 24, 24, 24)
        iv.setSpacing(16)

        # Cover + meta side-by-side
        meta_row = QHBoxLayout()
        meta_row.setSpacing(20)
        meta_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._detail_cover = QLabel()
        self._detail_cover.setFixedSize(180, 270)
        self._detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_cover.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:8px; "
            f"font-size:48px;")
        self._detail_cover.setText("📚")
        meta_row.addWidget(self._detail_cover)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(8)
        meta_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._detail_source = QLabel()
        self._detail_source.setStyleSheet(
            f"color:{ACCENT}; font-size:9px; letter-spacing:3px; "
            f"font-weight:bold; background:transparent;")
        meta_col.addWidget(self._detail_source)

        self._detail_title = QLabel()
        self._detail_title.setWordWrap(True)
        self._detail_title.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._detail_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._detail_title.setStyleSheet(
            f"color:{TEXT}; font-size:20px; font-weight:bold; "
            f"line-height:1.3; background:transparent;")
        meta_col.addWidget(self._detail_title)

        self._detail_author = QLabel()
        self._detail_author.setStyleSheet(
            f"color:{TEXT2}; font-size:13px; background:transparent;")
        meta_col.addWidget(self._detail_author)

        meta_col.addSpacing(10)

        # ── Stat cards row ────────────────────────────────────────────────────
        self._stat_row = QHBoxLayout()
        self._stat_row.setSpacing(8)
        self._stat_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._stat_status = self._make_stat_card("Status", "—")
        self._stat_last   = self._make_stat_card("Last read", "—")
        self._stat_dl     = self._make_stat_card("Downloaded", "—")

        for card in (self._stat_status, self._stat_last, self._stat_dl):
            self._stat_row.addWidget(card)
        self._stat_row.addStretch()
        meta_col.addLayout(self._stat_row)

        meta_col.addSpacing(10)

        # ── Genre chips row ───────────────────────────────────────────────────
        self._genre_wrap = QWidget()
        self._genre_wrap.setStyleSheet("background:transparent;")
        self._genre_layout = FlowLayout(self._genre_wrap)
        self._genre_layout.setSpacing(6)
        self._genre_wrap.hide()
        meta_col.addWidget(self._genre_wrap)

        meta_col.addSpacing(8)

        # ── Action buttons row ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._read_btn = QPushButton("▶  Read Now")
        self._read_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._read_btn.setStyleSheet(
            f"QPushButton{{background:{ACCENT}; border:none; color:{BG}; "
            f"font-size:10px; font-weight:700; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 16px;}}"
            f"QPushButton:hover{{background:#D4B460;}}")
        btn_row.addWidget(self._read_btn)

        # Category picker — shown as a compact combo next to the add button
        self._cat_combo = QComboBox()
        self._cat_combo.addItems(["Planning", "Reading", "Dropped", "Completed"])
        self._cat_combo.setFixedWidth(110)
        self._cat_combo.setStyleSheet(
            f"QComboBox{{background:{BG3}; border:1px solid {BORDER2}; "
            f"border-radius:4px; color:{TEXT2}; font-size:10px; padding:4px 8px;}}"
            f"QComboBox:hover{{border-color:{ACCENT};}}"
            f"QComboBox::drop-down{{border:none; width:16px;}}"
            f"QComboBox QAbstractItemView{{background:{BG2}; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; selection-background-color:{ACCENT}; selection-color:{BG};}}")
        btn_row.addWidget(self._cat_combo)

        self._add_lib_btn = QPushButton("+ Library")
        self._add_lib_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_lib_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; font-size:10px; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 12px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")
        btn_row.addWidget(self._add_lib_btn)

        # Remove button — same size as Library button, sits next to it
        self._remove_btn = QPushButton("✕ Remove")
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:1px solid #3D1A20; "
            f"color:{RED}; font-size:10px; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 12px;}}"
            f"QPushButton:hover{{background:#2A0E14; border-color:{RED};}}")
        self._remove_btn.hide()
        btn_row.addWidget(self._remove_btn)

        self._clean_btn = QPushButton("🧹 Clean")
        self._clean_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clean_btn.setToolTip("Strip junk lines from downloaded chapters")
        self._clean_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:1px solid #2A2A3A; "
            f"color:{MUTED}; font-size:10px; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 12px;}}"
            f"QPushButton:hover{{background:#1E1E2E; border-color:{ACCENT}; color:{ACCENT};}}")
        self._clean_btn.hide()
        btn_row.addWidget(self._clean_btn)

        self._preview_btn = QPushButton("✦ Preview")
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.setToolTip("AI analysis of the first chapters")
        self._preview_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:1px solid #2A2A3A; "
            f"color:{MUTED}; font-size:10px; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 12px;}}"
            f"QPushButton:hover{{background:#1E1E2E; border-color:{ACCENT}; color:{ACCENT};}}")
        self._preview_btn.hide()
        btn_row.addWidget(self._preview_btn)

        btn_row.addStretch()
        meta_col.addLayout(btn_row)

        # In-library badge
        self._lib_badge = QLabel()
        self._lib_badge.setStyleSheet(
            f"color:{ACCENT}; font-size:9px; letter-spacing:1px; background:transparent;")
        self._lib_badge.hide()
        meta_col.addWidget(self._lib_badge)

        meta_row.addLayout(meta_col, 1)
        iv.addLayout(meta_row)

        # Synopsis
        syn_label = QLabel("SYNOPSIS")
        syn_label.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        iv.addWidget(syn_label)

        self._synopsis = QTextBrowser()
        self._synopsis.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:6px; "
            f"color:{TEXT}; font-size:15px; padding:14px; line-height:1.6;")
        self._synopsis.setMinimumHeight(400)
        self._synopsis.setMaximumHeight(800)
        self._synopsis.setOpenExternalLinks(False)
        iv.addWidget(self._synopsis)

        # Chapters section
        self._ch_label = QLabel("CHAPTERS")
        self._ch_label.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        iv.addWidget(self._ch_label)

        self._ch_loading = QLabel("Loading chapters…")
        self._ch_loading.setStyleSheet(
            f"color:{MUTED}; font-size:11px; background:transparent;")
        iv.addWidget(self._ch_loading)

        self._ch_scroll = QScrollArea()
        self._ch_scroll.setWidgetResizable(True)
        self._ch_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._ch_scroll.setFixedHeight(260)
        self._ch_scroll.setStyleSheet(
            f"QScrollArea{{background:{BG3}; border:1px solid {BORDER}; "
            f"border-radius:6px;}}"
            "QScrollBar:vertical{width:4px; background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{BORDER2}; border-radius:2px;}}")

        self._ch_inner  = QWidget()
        self._ch_inner.setStyleSheet("background:transparent;")
        self._ch_layout = QVBoxLayout(self._ch_inner)
        self._ch_layout.setContentsMargins(10, 8, 10, 8)
        self._ch_layout.setSpacing(2)
        self._ch_scroll.setWidget(self._ch_inner)
        self._ch_scroll.hide()
        iv.addWidget(self._ch_scroll)

        iv.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self._inner_layout = iv
        self._current_book: Optional[BookItem] = None

    def _make_stat_card(self, label: str, value: str) -> QWidget:
        card = QWidget()
        card.setFixedWidth(100)
        card.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:6px;")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:2px; background:transparent; border:none;")
        val = QLabel(value)
        val.setStyleSheet(
            f"color:{TEXT}; font-size:13px; font-weight:bold; background:transparent; border:none;")
        val.setObjectName("stat_val")
        lay.addWidget(lbl)
        lay.addWidget(val)
        return card

    def _set_stat(self, card: QWidget, value: str):
        val = card.findChild(QLabel, "stat_val")
        if val:
            val.setText(value)

    def _update_stats(self, book: BookItem):
        status_map = {
            "reading":   ("● Ongoing",   f"color:{GREEN}; font-size:13px; font-weight:bold; background:transparent; border:none;"),
            "planning":  ("◉ Planning",  f"color:{MUTED}; font-size:13px; font-weight:bold; background:transparent; border:none;"),
            "dropped":   ("✕ Dropped",   f"color:{RED}; font-size:13px; font-weight:bold; background:transparent; border:none;"),
            "completed": ("✓ Completed", f"color:{ACCENT}; font-size:13px; font-weight:bold; background:transparent; border:none;"),
        }
        cat = library_get_category(book.title)
        label_text, label_style = status_map.get(cat or "", ("—", f"color:{MUTED}; font-size:13px; font-weight:bold; background:transparent; border:none;"))
        val = self._stat_status.findChild(QLabel, "stat_val")
        if val:
            val.setText(label_text)
            val.setStyleSheet(label_style)
        try:
            import re as _re2
            data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
            entry = data.get("books", {}).get(book.title, {})
            # current_chapter is never written by the reader — derive the number
            # from reader_url (most accurate) or reader_chapter (title string).
            ch = 0
            reader_url = entry.get("reader_url", "")
            if reader_url:
                m = _re2.search(r"/chapter-(\d+)", reader_url)
                if not m:
                    m = _re2.search(r"local-disk://chapter/(\d+)/", reader_url)
                if m:
                    ch = int(m.group(1))
            if not ch:
                ch_title = entry.get("reader_chapter", "")
                if ch_title:
                    m = _re2.search(r"(\d+)", ch_title)
                    if m:
                        ch = int(m.group(1))
        except Exception:
            ch = 0
        # Count chapters directly from disk — always accurate, never drifts.
        try:
            from great_sage_core import legion_mod as _dlm
            _dm, _ = _dlm()
            dl = len(_dm._get_chapter_list_from_file(book.title)) if _dm else 0
        except Exception:
            dl = 0
        self._set_stat(self._stat_last, f"Ch. {ch}" if ch else "—")
        self._set_stat(self._stat_dl,   f"{dl} ch"  if dl else "—")
        self._connect_download_signal(book)

    def _connect_download_signal(self, book: BookItem):
        """Disconnect ALL workers from this panel, then connect only the
        worker for the current book (if one is running)."""
        for w in _DownloadRegistry.all_workers():
            try:
                w.chapter_downloaded.disconnect()
            except TypeError:
                pass
        w = _DownloadRegistry.get(book.title)
        if w is None:
            return
        def _on_chapter(title, count):
            if title != book.title:
                return
            val = self._stat_dl.findChild(QLabel, "stat_val")
            try:
                current = int(val.text().replace(" ch", "")) if val else 0
            except (ValueError, AttributeError):
                current = 0
            if count > current:
                self._set_stat(self._stat_dl, f"{count} ch")
        w.chapter_downloaded.connect(_on_chapter)

    def _update_genres(self, book: BookItem):
        import re as _re
        while self._genre_layout.count():
            item = self._genre_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        genres = []
        if book.synopsis:
            m = _re.search(r"(?i)genre[s]?\s*[:\-]\s*(.+)", book.synopsis)
            if m:
                genres = [g.strip() for g in _re.split(r"[,;/]", m.group(1)) if g.strip()]
        if not genres:
            self._genre_wrap.hide()
            return
        for genre in genres[:8]:
            chip = QLabel(genre)
            chip.setStyleSheet(
                f"color:{ACCENT}; background:transparent; "
                f"border:1px solid {BORDER2}; border-radius:10px; "
                f"font-size:11px; padding:2px 10px;")
            self._genre_layout.addWidget(chip)
        self._genre_wrap.show()
        self._genre_wrap.updateGeometry()

    def show_book(self, book: BookItem):
        self._current_book = book
        self._first_chapter_url = None

        # Cover
        self._detail_cover.setText("📚")
        self._detail_cover.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:6px; "
            f"font-size:36px;")
        if book.cover_url:
            w = CoverWorker(book.cover_url)
            w.done.connect(self._on_detail_cover)
            w.start()
            self._detail_cover_worker = w

        # Meta
        self._detail_title.setText(book.title)
        self._detail_author.setText(book.author or "Unknown author")
        source_labels = {
            "royalroad": "ROYAL ROAD",
            "libread":    "LIBREAD",
            "gutenberg":  "PROJECT GUTENBERG",
            "local":      "LOCAL LIBRARY",
        }
        self._detail_source.setText(source_labels.get(book.source, book.source.upper()))

        # ── Wire buttons (disconnect first to avoid stacking) ─────────────────
        for btn in (self._read_btn, self._add_lib_btn, self._remove_btn, self._clean_btn, self._preview_btn):
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass

        self._read_btn.clicked.connect(lambda: self._on_read(book))
        self._add_lib_btn.clicked.connect(lambda: self._on_add_to_library(book))
        self._remove_btn.clicked.connect(lambda: self._on_remove_from_library(book))
        self._clean_btn.clicked.connect(lambda: self._on_clean_chapters(book))
        self._preview_btn.clicked.connect(lambda: self._on_preview(book))

        # Show Clean button only for books that have a downloaded file
        try:
            from great_sage_core import legion_mod as _clm
            _cm, _ = _clm()
            from pathlib import Path as _Path
            _has_file = _cm and _Path(_cm.get_book_path(book.title)).exists()
        except Exception:
            _has_file = False
        self._clean_btn.setVisible(bool(_has_file))
        self._preview_btn.setVisible(bool(_has_file))

        # ── Library status ────────────────────────────────────────────────────
        self._refresh_lib_status(book)
        self._update_stats(book)
        self._update_genres(book)

        # ── Chapters ──────────────────────────────────────────────────────────
        if book.source == "local":
            self._ch_label.hide()
            self._ch_loading.hide()
            self._ch_scroll.hide()
        elif book.source == "libread":
            # Chapter list not shown for libread — URL pattern handles navigation
            self._ch_label.hide()
            self._ch_loading.hide()
            self._ch_scroll.hide()
            self._load_chapters(book)  # still needed to fetch synopsis from detail page
        else:
            self._ch_label.show()
            self._ch_loading.setText("Loading chapters…")
            self._ch_loading.show()
            self._ch_scroll.hide()
            self._load_chapters(book)

        # Reset any active preview state
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.terminate()
            self._preview_worker = None
        self._original_synopsis = ""

        # Synopsis — show cached value immediately; detail fetch overwrites
        if book.synopsis:
            self._synopsis.setPlainText(book.synopsis)
        elif book.source in ("royalroad", "libread"):
            self._synopsis.setPlainText("Loading synopsis…")
        else:
            self._synopsis.setPlainText("No synopsis available.")

        self.show()

    def _refresh_lib_status(self, book: BookItem):
        """Update badge, remove button, and category combo to reflect current library state."""
        cat = library_get_category(book.title)
        if cat:
            self._lib_badge.setText(f"✓ In library — {cat.capitalize()}")
            self._lib_badge.show()
            self._remove_btn.show()
            # Set combo to current category
            cat_map = {"planning": 0, "reading": 1, "dropped": 2, "completed": 3}
            self._cat_combo.setCurrentIndex(cat_map.get(cat, 0))
        else:
            self._lib_badge.hide()
            # Even if not in the library, show the remove button if the book is an
            # orphaned Jump In entry — so the user always has an escape hatch.
            try:
                _ji_data = load_json_cached(LEGION_PROGRESS, {"books": {}})
                _in_ji   = book.title in _ji_data.get("books", {})
            except Exception:
                _in_ji = False
            if _in_ji:
                self._lib_badge.setText("⚠ In Jump In (not in library)")
                self._lib_badge.show()
                self._remove_btn.show()
            else:
                self._remove_btn.hide()

    def _on_read(self, book: BookItem):
        """Read Now: add to library as Reading + add to Jump In, then signal to open reader."""
        library_add(book, "reading")
        jump_in_add(book)
        self._refresh_lib_status(book)
        self.book_action.emit("read", book)

    def _on_add_to_library(self, book: BookItem):
        """Add/move to selected category."""
        cat_names = ("planning", "reading", "dropped", "completed")
        cat = cat_names[self._cat_combo.currentIndex()]
        library_add(book, cat)
        if cat == "reading":
            jump_in_add(book)
        else:
            jump_in_remove(book.title, delete_files=False)
        self._refresh_lib_status(book)
        self.book_action.emit("library_add", book)

    def _on_remove_from_library(self, book: BookItem):
        """Remove absolutely — from library, Jump In, and downloaded chapters."""
        library_remove(book.title)
        jump_in_remove(book.title, delete_files=True)
        self._refresh_lib_status(book)
        self.book_action.emit("library_remove", book)

    def _on_clean_chapters(self, book: BookItem):
        """Run junk chapter cleanup on the book's downloaded .txt file."""
        try:
            from great_sage_core import legion_mod as _clm
            mod, err = _clm()
            if not mod or err:
                self._lib_badge.setText(f"Clean failed: {err}")
                self._lib_badge.show()
                return
        except Exception as e:
            self._lib_badge.setText(f"Clean error: {e}")
            self._lib_badge.show()
            return

        self._lib_badge.setText("🧹 Cleaning…")
        self._lib_badge.show()
        QApplication.processEvents()

        result = mod.clean_junk_chapters(book.title)
        if result.get("error"):
            self._lib_badge.setText(f"Clean error: {result['error'][:60]}")
            return

        removed       = result["removed"]
        paras_stripped = result.get("paras_stripped", 0)
        kept          = result["kept"]
        new_last      = result["new_last"]

        duplicates    = result.get("duplicates", 0)
        total_changed = removed + paras_stripped + duplicates
        if total_changed == 0:
            self._lib_badge.setText(f"✓ No junk found — {kept} chapters clean")
        else:
            parts = []
            if duplicates:     parts.append(f"{duplicates} dupes")
            if removed:        parts.append(f"{removed} empty")
            if paras_stripped: parts.append(f"{paras_stripped} junk lines")
            self._lib_badge.setText(f"✓ Removed {', '.join(parts)} — {kept} kept")

        if total_changed > 0:
            try:
                data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
                entry = data.get("books", {}).get(book.title)
                if entry:
                    entry["last_downloaded_chapter"] = new_last
                    save_json(LEGION_PROGRESS, data)
            except Exception:
                pass
            # Update the stat card directly from clean result — don't re-read
            # JSON which may still lag behind the live download counter.
            self._set_stat(self._stat_dl, f"{new_last} ch")

    # ── AI Preview ────────────────────────────────────────────────────────────

    def _on_preview(self, book: BookItem):
        """Kick off the PreviewWorker and show a spinner in the synopsis area."""
        # Stop any running preview first
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.terminate()
            self._preview_worker = None

        # Stash original synopsis so we can show it again if needed
        self._original_synopsis = self._synopsis.toPlainText()
        self._synopsis.setPlainText("✦  Analysing chapters…")
        self._preview_btn.setEnabled(False)

        w = PreviewWorker(book.title)
        w.done.connect(lambda text: self._on_preview_done(text))
        w.error.connect(lambda msg: self._on_preview_error(msg))
        w.start()
        self._preview_worker = w

    def _on_preview_done(self, text: str):
        self._synopsis.setPlainText(text)
        self._preview_btn.setEnabled(True)

    def _on_preview_error(self, msg: str):
        self._synopsis.setPlainText(
            self._original_synopsis or "Preview failed — could not reach Groq."
        )
        self._lib_badge.setText(f"Preview error: {msg[:80]}")
        self._lib_badge.show()
        self._preview_btn.setEnabled(True)

    def _on_detail_cover(self, url: str, px: QPixmap):
        if px.isNull():
            return
        scaled = px.scaled(
            180, 270,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(180, 270)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 180, 270, 8, 8)
        p.setClipPath(path)
        sx = (scaled.width()  - 180) // 2
        sy = (scaled.height() - 270) // 2
        p.drawPixmap(0, 0, scaled, sx, sy, 180, 270)
        p.end()
        self._detail_cover.setPixmap(result)
        self._detail_cover.setStyleSheet("background:transparent; border:none;")

    def _load_chapters(self, book: BookItem):
        # Clear old chapters
        while self._ch_layout.count():
            item = self._ch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        def _task():
            if book.source == "royalroad":
                return fetch_royalroad_detail(book.url)
            elif book.source == "libread":
                return fetch_libread_detail(book.url)
            elif book.source == "gutenberg":
                return fetch_gutenberg_detail(book.url)
            return {}

        self._detail_worker = FetchWorker(_task)
        self._detail_worker.detail.connect(
            lambda data: self._on_chapters(data, book))
        self._detail_worker.error.connect(
            lambda e: self._on_detail_error(e, book))
        self._detail_worker.start()

    def _on_detail_error(self, error: str, book: BookItem):
        self._ch_loading.hide()
        # Show whatever synopsis we already have from the grid scrape
        if book.synopsis:
            self._synopsis.setPlainText(book.synopsis)
        else:
            self._synopsis.setPlainText("Synopsis unavailable.")
        if book.source not in ("gutenberg", "local"):
            self._ch_loading.setText("Could not load chapters.")
            self._ch_loading.show()

    def _on_chapters(self, data: dict, book: BookItem):
        chapters = data.get("chapters", [])
        synopsis = data.get("synopsis", "")
        author   = data.get("author", "")

        if synopsis:
            book.synopsis = synopsis
            self._synopsis.setPlainText(synopsis)
        elif not book.synopsis:
            self._synopsis.setPlainText("No synopsis available.")

        if author and not book.author:
            book.author = author
            self._detail_author.setText(author)

        self._ch_loading.hide()

        if book.source in ("gutenberg", "local", "libread"):
            self._ch_label.hide()
            self._ch_scroll.hide()
            # For libread, still cache the first chapter URL so _open_reader
            # can use it immediately (Priority 2) without a second network fetch.
            if book.source == "libread" and chapters:
                self._first_chapter_url = chapters[0].get("url", "")
            return

        if not chapters:
            self._ch_label.hide()
            return

        self._ch_label.show()
        self._first_chapter_url = chapters[0].get("url", "") if chapters else ""
        count_lbl = QLabel(f"{len(chapters)} chapters")
        count_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:1px; background:transparent;")
        self._ch_layout.addWidget(count_lbl)

        for ch in chapters:
            btn = QPushButton(ch["title"])
            btn.setStyleSheet(
                f"QPushButton{{background:transparent; border:none; "
                f"color:{TEXT2}; font-size:11px; text-align:left; padding:5px 6px;}}"
                f"QPushButton:hover{{color:{ACCENT}; background:{BG2}; border-radius:3px;}}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            ch_url = ch["url"]
            btn.clicked.connect(lambda _, u=ch_url: self._open_url(u))
            self._ch_layout.addWidget(btn)

        self._ch_layout.addStretch()
        self._ch_scroll.show()

    def _open_url(self, url: str):
        import subprocess
        subprocess.Popen(["xdg-open", url])

    def close_panel(self):
        self.hide()
        self.closed.emit()


# ══════════════════════════════════════════════════════════════════════════════
# BOOKS GRID WIDGET
# ══════════════════════════════════════════════════════════════════════════════

class BooksGrid(QWidget):
    """Scrollable grid of CoverCard widgets — columns fill available width."""
    book_clicked     = pyqtSignal(object)  # BookItem
    delete_requested = pyqtSignal(object)  # BookItem

    _CARD_W   = COVER_W + 16   # card width + per-slot spacing budget
    _MARGIN   = 28              # horizontal margin each side
    _MIN_COLS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: list[CoverCard] = []
        self._pending_books: list[BookItem] = []
        self._cols = GRID_COLS      # updated dynamically on resize
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            "QScrollBar:vertical{width:5px; background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{BORDER2}; border-radius:2px;}}")

        self._container = QWidget()
        self._container.setStyleSheet("background:transparent;")
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(self._MARGIN, 14, self._MARGIN, 28)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(24)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll)

        # Empty state label — shown via set_books([], empty_message=...)
        self._empty_label = QLabel()
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            f"color:{MUTED}; font-size:13px; background:transparent; padding:40px;")
        self._empty_label.hide()
        lay.addWidget(self._empty_label, 1)

    # ── Dynamic column count ──────────────────────────────────────────────────

    def _cols_for_width(self, w: int) -> int:
        usable = w - self._MARGIN * 2
        return max(self._MIN_COLS, usable // self._CARD_W)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        new_cols = self._cols_for_width(self._scroll.viewport().width())
        if new_cols != self._cols:
            self._cols = new_cols
            self._reflow()

    def _reflow(self):
        """Re-place all existing cards using the updated column count."""
        while self._grid.count():
            self._grid.takeAt(0)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // self._cols, i % self._cols)

    # ── Public API ────────────────────────────────────────────────────────────

    _BATCH = 12   # cards per event-loop tick — small enough to stay responsive

    def set_books(self, books: list[BookItem], empty_message: str = ""):
        """Clear existing cards and start populating asynchronously in batches."""
        # Cancel any pending batch job first
        self._pending_books: list[BookItem] = []

        # Detach from layout (fast), hide + schedule destruction
        while self._grid.count():
            self._grid.takeAt(0)
        for card in self._cards:
            card.hide()
            card.deleteLater()
        self._cards.clear()

        if not books and empty_message:
            self._empty_label.setText(empty_message)
            self._empty_label.show()
            self._scroll.hide()
            return

        self._empty_label.hide()
        self._scroll.show()
        self._pending_books = list(books)
        self._flush_batch()

    def _flush_batch(self):
        """Add one batch of cards then yield back to the event loop."""
        if not self._pending_books:
            return
        batch, self._pending_books = (
            self._pending_books[:self._BATCH],
            self._pending_books[self._BATCH:],
        )
        self.add_books(batch)
        if self._pending_books:
            # 30ms gap: long enough for the event loop to process cover signals
            # but short enough that the full grid appears quickly
            QTimer.singleShot(30, self._flush_batch)

    def add_books(self, books: list[BookItem]):
        """Append books to the existing grid, skipping duplicates already shown."""
        existing_titles = {c.book.title.strip().lower() for c in self._cards}
        existing_urls   = {c.book.url.strip().rstrip("/").lower() for c in self._cards}
        start = len(self._cards)
        offset = 0
        for book in books:
            t = book.title.strip().lower()
            u = book.url.strip().rstrip("/").lower()
            # Local books have no URL — skip URL dedup to avoid all of them
            # collapsing into a single "" match after the first is added.
            url_is_dupe = bool(u) and u in existing_urls
            if t in existing_titles or url_is_dupe:
                continue
            existing_titles.add(t)
            if u:
                existing_urls.add(u)
            card = CoverCard(book)
            card.clicked.connect(self.book_clicked)
            card.delete_requested.connect(self.delete_requested)
            idx = start + offset
            self._grid.addWidget(card, idx // self._cols, idx % self._cols)
            self._cards.append(card)
            offset += 1

    def clear(self):
        self.set_books([])



# ══════════════════════════════════════════════════════════════════════════════
# READER PANEL
# ══════════════════════════════════════════════════════════════════════════════

READER_SETTINGS_PATH = os.path.expanduser("~/.config/great_sage/reader_settings.json")
READER_FONT_DEFAULT  = 17
READER_FONT_MIN      = 11
READER_FONT_MAX      = 32


def _load_reader_settings() -> dict:
    try:
        if os.path.exists(READER_SETTINGS_PATH):
            with open(READER_SETTINGS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {"font_size": READER_FONT_DEFAULT}


def _save_reader_settings(data: dict):
    try:
        os.makedirs(os.path.dirname(READER_SETTINGS_PATH), exist_ok=True)
        with open(READER_SETTINGS_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL FILE PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _parse_epub(path: str) -> tuple:
    """Parse an EPUB file. Returns (paragraphs, cover_bytes). cover_bytes is None if no cover found."""
    import zipfile
    import xml.etree.ElementTree as ET

    paragraphs: list = []
    cover_bytes = None

    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()

            # Find container.xml -> rootfile path
            container_xml = z.read("META-INF/container.xml")
            container = ET.fromstring(container_xml)
            rootfile_el = container.find(".//{urn:oasis:names:tc:opf:2.0:container}rootfile")
            if rootfile_el is None:
                return paragraphs, cover_bytes
            opf_path = rootfile_el.attrib.get("full-path", "")
            if not opf_path:
                return paragraphs, cover_bytes

            opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
            opf_xml = z.read(opf_path)
            opf = ET.fromstring(opf_xml)

            # Try to find cover image — method 1: meta name="cover"
            cover_id = None
            for meta in opf.findall(".//{http://www.idpf.org/2007/opf}meta"):
                if meta.attrib.get("name") == "cover":
                    cover_id = meta.attrib.get("content")
                    break
            if cover_id:
                for item in opf.findall(".//{http://www.idpf.org/2007/opf}item"):
                    if item.attrib.get("id") == cover_id:
                        href = item.attrib.get("href", "")
                        cover_path = opf_dir + href
                        if cover_path in names:
                            cover_bytes = z.read(cover_path)
                        break

            # Method 2: properties="cover-image"
            if not cover_bytes:
                for item in opf.findall(".//{http://www.idpf.org/2007/opf}item"):
                    if "cover-image" in item.attrib.get("properties", ""):
                        href = item.attrib.get("href", "")
                        cover_path = opf_dir + href
                        if cover_path in names:
                            cover_bytes = z.read(cover_path)
                        break

            # Method 3: filename contains "cover"
            if not cover_bytes:
                for name in names:
                    low = name.lower()
                    if "cover" in low and any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                        cover_bytes = z.read(name)
                        break

            # Build spine order
            spine_ids = [
                itemref.attrib.get("idref", "")
                for itemref in opf.findall(".//{http://www.idpf.org/2007/opf}itemref")
            ]
            id_to_href: dict = {}
            for item in opf.findall(".//{http://www.idpf.org/2007/opf}item"):
                item_id = item.attrib.get("id", "")
                href    = item.attrib.get("href", "")
                media   = item.attrib.get("media-type", "")
                if "html" in media or "xhtml" in media or href.endswith((".html", ".xhtml", ".htm")):
                    id_to_href[item_id] = opf_dir + href

            ordered = [id_to_href[sid] for sid in spine_ids if sid in id_to_href]

            for item_path in ordered:
                if item_path not in names:
                    continue
                html_bytes = z.read(item_path)
                html_text = html_bytes.decode("utf-8", errors="replace")
                soup = BeautifulSoup(html_text, "html.parser")
                for tag in soup.find_all(["p", "div"]):
                    if tag.find(["p", "div"]):
                        continue
                    text = tag.get_text(" ", strip=True)
                    if text and len(text) > 8:
                        paragraphs.append(text)

    except Exception:
        pass

    return paragraphs, cover_bytes


def _parse_pdf(path: str) -> list:
    """Parse a PDF and return list of paragraph strings."""
    paragraphs: list = []
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        for page in reader.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if line and len(line) > 8:
                    paragraphs.append(line)
    except Exception as e:
        paragraphs.append(f"Could not read PDF: {e}")
    return paragraphs


def _parse_txt(path: str) -> list:
    """Parse a plain text file and return list of paragraph strings."""
    paragraphs: list = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        for para in content.split("\n\n"):
            para = para.strip()
            if para and len(para) > 4:
                paragraphs.append(para)
        if not paragraphs:
            for line in content.split("\n"):
                line = line.strip()
                if line:
                    paragraphs.append(line)
    except Exception as e:
        paragraphs.append(f"Could not read file: {e}")
    return paragraphs


class LocalFileWorker(QThread):
    """Parses a local EPUB/PDF/TXT file in a background thread."""
    done = pyqtSignal(list, object)  # (paragraphs, cover_bytes_or_None)

    def __init__(self, book, parent=None):
        super().__init__(parent)
        self._book      = book
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            ft = self._book.file_type.upper()
            if ft == "EPUB":
                paragraphs, cover_bytes = _parse_epub(self._book.local_path)
            elif ft == "PDF":
                paragraphs = _parse_pdf(self._book.local_path)
                cover_bytes = None
            else:
                paragraphs = _parse_txt(self._book.local_path)
                cover_bytes = None

            if not self._cancelled:
                self.done.emit(paragraphs, cover_bytes)
        except Exception as e:
            if not self._cancelled:
                self.done.emit([f"Error reading file: {e}"], None)



class ChapterFetchWorker(QThread):
    """Fetches a chapter in a background thread."""
    done = pyqtSignal(str, list, object, object, object)  # title, paragraphs, next_url, prev_url, error

    def __init__(self, url: str, book_name: str = "", parent=None):
        super().__init__(parent)
        self._url       = url
        self._book_name = book_name
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            title, paragraphs, next_url, prev_url, error = self._fetch()
            if not self._cancelled:
                self.done.emit(title, paragraphs or [], next_url, prev_url, error)
        except Exception as e:
            if not self._cancelled:
                self.done.emit("Error", [], None, None, str(e))

    def _fetch(self):
        url = self._url

        # ── Local disk sentinel ───────────────────────────────────────────────
        # URL format: local-disk://chapter/{num}/{book_title}
        # Serves directly from the downloaded .txt file — zero network.
        if url.startswith("local-disk://chapter/"):
            return self._fetch_from_disk(url)

        # ── Local cache check ────────────────────────────────────────────────
        # If this specific chapter is already downloaded, serve it from disk.
        # Extract the chapter number from the URL being fetched — NOT from
        # current_chapter in LEGION_PROGRESS (that's the reader's reading
        # position, not the chapter we're being asked to load right now).
        if self._book_name and (
            "freewebnovel.com" in url or "libread.com" in url
        ):
            try:
                import re as _re2
                from great_sage_core import legion_mod
                mod, err = legion_mod()
                if mod and not err:
                    m = _re2.search(r"/chapter-(\d+)$", url)
                    # Also handle libread padded format: /chapter-01
                    if not m:
                        m = _re2.search(r"/chapter-0*(\d+)$", url)
                    if m:
                        ch_num = int(m.group(1))
                        local_title, local_paragraphs = mod.get_chapter_from_file(
                            self._book_name, ch_num)
                        if local_title and local_paragraphs:
                            # Chapter is on disk. Derive prev/next as local-disk
                            # sentinels so the reader never needs the network for nav.
                            # This requires knowing total chapters — scan once.
                            try:
                                ch_list = mod._get_chapter_list_from_file(self._book_name)
                                total   = ch_list[-1][0] if ch_list else ch_num
                            except Exception:
                                total = ch_num
                            def _sentinel(n):
                                # URL-encode the book name so titles with /
                                # don't break the local-disk:// URL pattern.
                                from urllib.parse import quote as _q
                                return f"local-disk://chapter/{n}/{_q(self._book_name, safe='')}"
                            prev_s = _sentinel(ch_num - 1) if ch_num > 1 else None
                            next_s = _sentinel(ch_num + 1) if ch_num < total else None
                            return local_title, local_paragraphs, next_s, prev_s, None
            except Exception:
                pass  # disk miss — fall through to network

        # ── Network fetch ────────────────────────────────────────────────────
        # LibRead / FreeWebNovel: use proven selectors directly
        if "libread.com" in url or "freewebnovel.com" in url:
            return self._fetch_libread(url)
        # All other sources: delegate to legion.fetch_chapter
        from great_sage_core import legion_mod
        mod, err = legion_mod()
        if err or not mod:
            return "Error", [], None, None, f"Legion module unavailable: {err}"
        title, paragraphs, next_url, prev_url, error, _ = mod.fetch_chapter(
            url, self._book_name)
        return title or "Chapter", paragraphs or [], next_url, prev_url, error

    def _fetch_libread(self, url: str):
        """
        Fetch a chapter from freewebnovel.com.

        Accepts both libread.com and freewebnovel.com chapter URLs — libread
        URLs are converted to fwn equivalents before the request so we never
        follow the 302 → Cloudflare-challenge redirect that caused HTTP 403.

        Returns: (title, paragraphs, next_url, prev_url, error)
          error is None on success, "No content found" when the page exists but
          is empty (ghost URL past end-of-book), or "HTTP NNN" on HTTP error.
          Callers treat "HTTP 4xx" as permanent (end-of-book), other errors as
          transient (retry).
        """
        import re as _re

        # Convert libread URL to fwn equivalent before any network touch.
        # This prevents following the libread→fwn redirect which triggers CF.
        fwn_url = _lr_url_to_fwn(url) if "libread.com" in url else url

        # Plain requests.Session — proven to return HTTP 200 on fwn (diag.py Test 1).
        # Do NOT use cloudscraper here; fwn blocks it (diag.py Tests 2 & 3 both 403).
        s = _fwn_session()
        try:
            r = s.get(fwn_url, timeout=12)
        except requests.exceptions.Timeout:
            return "Error", [], None, None, "Timeout fetching chapter"
        except requests.exceptions.ConnectionError as e:
            return "Error", [], None, None, f"Connection error: {e}"
        except Exception as e:
            return "Error", [], None, None, str(e)

        if r.status_code != 200:
            return "Error", [], None, None, f"HTTP {r.status_code}"

        # Guard against JS challenge pages served with HTTP 200 (CF anti-bot).
        # A challenge page has < 2000 chars and contains CF-specific markers.
        # Writing challenge HTML as chapter content would corrupt the book file.
        if len(r.text) < 2000 and any(
            marker in r.text for marker in (
                "Just a moment", "cf-browser-verification",
                "_cf_chl", "Checking your browser", "DDoS protection",
            )
        ):
            return "Error", [], None, None, "JS challenge page — cannot scrape"

        soup = BeautifulSoup(r.text, "html.parser")

        # Chapter title — fwn primary: <span class="chapter">, fallback: <h1>
        title_el = soup.find("span", class_="chapter") or soup.find("h1")
        title    = title_el.get_text(strip=True) if title_el else "Chapter"

        # Content — fwn primary selector is div.txt; chain for safety.
        # These are ordered by specificity: most specific first.
        container = (
            soup.find("div", class_="txt")
            or soup.find("div", id="chapter-content")
            or soup.find("div", class_="chapter-content")
            or soup.find("div", class_="content")
        )

        paragraphs = []
        if container:
            # Navigation noise fwn injects as <p> tags inside the content div.
            # Filter by exact text rather than class because fwn uses no classes
            # on these paragraphs — only content matters.
            _NAV_NOISE = {"Prev Chapter", "Next Chapter", "Font size", "Report"}
            for p in container.find_all("p"):
                text = p.get_text(strip=True)
                if text and text not in _NAV_NOISE:
                    paragraphs.append(text)

        # Nav links — fwn nav buttons live in a dedicated .m-con-btn container
        # (div.m-con-btn > a). Targeting this avoids the false-positive match
        # from sidebar recommended-novel links which also contain /chapter- hrefs.
        # Fallback to rel="prev"/rel="next" attributes as a secondary signal.
        next_url = prev_url = None

        nav_container = soup.find("div", class_="m-con-btn")
        if nav_container:
            for a in nav_container.find_all("a", href=True):
                href = a["href"]
                if "/chapter-" not in href:
                    continue
                full = (FWN_BASE + href) if href.startswith("/") else href
                txt  = a.get_text(strip=True).lower()
                if "next" in txt and next_url is None:
                    next_url = full
                elif "prev" in txt and prev_url is None:
                    prev_url = full

        # rel="next" / rel="prev" as fallback (fwn sets these in <link> tags)
        if next_url is None:
            tag = soup.find("link", rel="next") or soup.find("a", rel="next")
            if tag and tag.get("href"):
                href = tag["href"]
                next_url = (FWN_BASE + href) if href.startswith("/") else href
        if prev_url is None:
            tag = soup.find("link", rel="prev") or soup.find("a", rel="prev")
            if tag and tag.get("href"):
                href = tag["href"]
                prev_url = (FWN_BASE + href) if href.startswith("/") else href

        # Arithmetic fallback — fwn uses clean /chapter-N slugs with no padding.
        # We verify the candidate with a HEAD request using the same plain session
        # (not cloudscraper) so we don't 403 the check.
        m = _re.search(r"/chapter-(\d+)$", fwn_url)
        if m:
            n        = int(m.group(1))
            base_url = fwn_url[:fwn_url.rfind("/")]   # strips /chapter-N

            if next_url is None:
                candidate = f"{base_url}/chapter-{n + 1}"
                try:
                    # HEAD with same session — no cloudscraper, no 403.
                    # Accept 200–399: fwn may 301 to canonical URL which is fine.
                    # Reject >= 400: chapter genuinely doesn't exist.
                    head = s.head(candidate, timeout=6, allow_redirects=True)
                    if head.status_code < 400:
                        next_url = candidate
                    # 404 → next_url stays None → downloader enters polling loop
                except Exception:
                    # Network hiccup on HEAD — leave next_url as None.
                    # The downloader will re-check via arithmetic on next poll.
                    pass

            if prev_url is None and n > 1:
                prev_url = f"{base_url}/chapter-{n - 1}"

        error = None if paragraphs else "No content found"
        return title, paragraphs, next_url, prev_url, error

    def _fetch_from_disk(self, url: str):
        """
        Serve a chapter from the locally downloaded .txt file.
        URL format: local-disk://chapter/{num}/{url_encoded_book_title}

        Book title is URL-encoded in the sentinel to handle titles containing /
        or other characters that would break a naive regex capture.
        Prev/next URLs are also local-disk sentinels so navigation never hits
        the network once chapters are on disk.
        """
        import re as _re, os
        from urllib.parse import unquote as _unquote, quote as _q

        # Strip the scheme and split on the first two path components.
        # Format: local-disk://chapter/{num}/{encoded_title}
        # We can't use a greedy regex on the title because titles may contain
        # characters that look like URL separators.
        path = url[len("local-disk://"):]   # "chapter/{num}/{encoded_title}"
        parts = path.split("/", 2)           # ["chapter", "N", "encoded_title"]
        if len(parts) != 3 or parts[0] != "chapter" or not parts[1].isdigit():
            return "Error", [], None, None, f"Bad local-disk URL: {url}"

        ch_num    = int(parts[1])
        book_name = _unquote(parts[2])   # decode %2F etc. back to original title

        # Locate the file using the same sanitization as get_book_path.
        from great_sage_core import SCRIPT_DIR
        safe     = _re.sub(r"[^\w\-_\. ]", "_", book_name)
        txt_path = str(SCRIPT_DIR / "library" / safe / f"{safe}.txt")
        if not os.path.exists(txt_path):
            return "Error", [], None, None, f"Book file not found: {txt_path}"

        try:
            with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except Exception as e:
            return "Error", [], None, None, str(e)

        # Parse chapter blocks
        blocks         = _re.split(r"={50,}", raw)
        total_chapters = 0
        title          = f"Chapter {ch_num}"
        paragraphs     = []

        for i in range(len(blocks) - 1):
            header = blocks[i].strip()
            body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
            hm = _re.match(r"Chapter\s+(\d+)\s*[:\-]?\s*(.*)", header, _re.IGNORECASE)
            if hm:
                n = int(hm.group(1))
                total_chapters = max(total_chapters, n)
                if n == ch_num:
                    title      = hm.group(2).strip() or f"Chapter {ch_num}"
                    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

        if not paragraphs:
            return "Error", [], None, None, f"Chapter {ch_num} not found in downloaded file."

        # Build prev/next as local-disk sentinels — URL-encode the title.
        def _sentinel(n):
            return f"local-disk://chapter/{n}/{_q(book_name, safe='')}"

        prev_url = _sentinel(ch_num - 1) if ch_num > 1             else None
        next_url = _sentinel(ch_num + 1) if ch_num < total_chapters else None

        return title, paragraphs, next_url, prev_url, None

class LocalDetailPanel(QWidget):
    """
    Detail panel for local files (EPUB/PDF/TXT).
    Shows cover (extracted if EPUB), title, file type, file size, and Read button.
    Emits read_requested(BookItem) when the user clicks Read Now.
    """
    closed        = pyqtSignal()
    read_requested = pyqtSignal(object)  # BookItem

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG2}; border-left:1px solid {BORDER};")
        self._book: "BookItem | None" = None
        self._build()
        self.hide()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        topbar = QWidget()
        topbar.setStyleSheet(f"background:{BG3}; border-bottom:1px solid {BORDER};")
        topbar.setFixedHeight(44)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 0, 14, 0)
        back_btn = QPushButton("← Back")
        back_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; color:{MUTED}; "
            f"font-size:11px; letter-spacing:0.5px;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._on_close)
        tb.addWidget(back_btn)
        tb.addStretch()
        root.addWidget(topbar)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            "QScrollBar:vertical{width:4px; background:transparent;}"
            f"QScrollBar::handle:vertical{{background:{BORDER2}; border-radius:2px;}}")

        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(24, 24, 24, 24)
        iv.setSpacing(20)

        # Cover + meta
        meta_row = QHBoxLayout()
        meta_row.setSpacing(20)
        meta_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(180, 270)
        self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_lbl.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:8px; font-size:48px;")
        self._cover_lbl.setText("📄")
        meta_row.addWidget(self._cover_lbl)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(10)
        meta_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Source badge
        src_lbl = QLabel("LOCAL LIBRARY")
        src_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:9px; letter-spacing:3px; font-weight:bold; background:transparent;")
        meta_col.addWidget(src_lbl)

        # Title
        self._title_lbl = QLabel()
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:20px; font-weight:bold; line-height:1.3; background:transparent;")
        meta_col.addWidget(self._title_lbl)

        # File type badge + size on same row
        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        self._type_badge = QLabel()
        self._type_badge.setStyleSheet(
            f"background:{BG3}; color:{ACCENT}; border:1px solid {ACCENT}44; "
            f"border-radius:3px; font-size:9px; letter-spacing:2px; padding:2px 8px;")
        info_row.addWidget(self._type_badge)
        self._size_lbl = QLabel()
        self._size_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; background:transparent;")
        info_row.addWidget(self._size_lbl)
        info_row.addStretch()
        meta_col.addLayout(info_row)

        meta_col.addSpacing(8)

        # Read Now button
        self._read_btn = QPushButton("▶  Read Now")
        self._read_btn.setFixedWidth(160)
        self._read_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._read_btn.setStyleSheet(
            f"QPushButton{{background:{ACCENT}; border:none; color:{BG}; "
            f"font-size:10px; font-weight:700; letter-spacing:1px; "
            f"border-radius:4px; padding:8px 16px;}}"
            f"QPushButton:hover{{background:#D4B460;}}")
        self._read_btn.clicked.connect(self._on_read)
        meta_col.addWidget(self._read_btn)

        meta_row.addLayout(meta_col, 1)
        iv.addLayout(meta_row)

        # Path info
        path_hdr = QLabel("FILE PATH")
        path_hdr.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        iv.addWidget(path_hdr)

        self._path_lbl = QLabel()
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:6px; "
            f"color:{TEXT2}; font-size:11px; padding:10px 14px; font-family:monospace;")
        iv.addWidget(self._path_lbl)

        iv.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def show_book(self, book: "BookItem"):
        self._book = book
        self._title_lbl.setText(book.title)
        self._type_badge.setText(book.file_type)
        self._path_lbl.setText(book.local_path)

        # File size
        try:
            size = Path(book.local_path).stat().st_size
            if size >= 1_048_576:
                size_str = f"{size / 1_048_576:.1f} MB"
            else:
                size_str = f"{size / 1024:.0f} KB"
        except Exception:
            size_str = ""
        self._size_lbl.setText(size_str)

        # Cover placeholder — EPUB cover extracted on read, show emoji for now
        icon = {"EPUB": "📖", "PDF": "📄", "TXT": "📝"}.get(book.file_type, "📄")
        self._cover_lbl.setText(icon)
        self._cover_lbl.setStyleSheet(
            f"background:{BG3}; border:1px solid {BORDER}; border-radius:8px; font-size:48px;")
        self._cover_lbl.setPixmap(QPixmap())  # clear any previous pixmap

        self.show()

    def set_cover(self, pixmap: QPixmap):
        """Called after EPUB cover is extracted to update the cover image."""
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            180, 270,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(180, 270)
        result.fill(Qt.GlobalColor.transparent)
        p = QPainter(result)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, 180, 270, 8, 8)
        p.setClipPath(path)
        sx = (scaled.width()  - 180) // 2
        sy = (scaled.height() - 270) // 2
        p.drawPixmap(0, 0, scaled, sx, sy, 180, 270)
        p.end()
        self._cover_lbl.setText("")
        self._cover_lbl.setStyleSheet("background:transparent; border:none;")
        self._cover_lbl.setPixmap(result)

    def _on_read(self):
        if self._book:
            self.read_requested.emit(self._book)

    def _on_close(self):
        self.hide()
        self.closed.emit()

    def close_panel(self):
        self._on_close()


class PreviewWorker(QThread):
    """
    Reads the first 20–30 chapters of a downloaded book, truncates intelligently,
    then calls Groq to produce a structured mini-report about the book.

    Signals
    -------
    done  — emitted with the formatted report string on success
    error — emitted with an error message string on failure
    """
    done  = pyqtSignal(str)
    error = pyqtSignal(str)

    MAX_CHAPTERS   = 25
    MAX_CH_CHARS   = 1500   # cap per chapter
    MAX_TOTAL_CHARS = 40_000  # hard total cap (well under Groq context limit)

    SYSTEM_PROMPT = (
        "You are a book analyst. Given the first chapters of a web novel, "
        "return a structured preview report with exactly these labelled sections:\n\n"
        "MC Name — the protagonist's name (or 'Unknown' if unclear from these chapters)\n"
        "Power System — how powers/abilities work in this world; write 'None' if it's "
        "slice-of-life or there is no power system evident yet\n"
        "Notable Characters — 3–5 names with a one-line role each, as a short bulleted list\n"
        "Pacing — one of: Slow / Moderate / Fast, followed by one sentence of justification\n"
        "Verdict — a full paragraph giving an honest take on the writing quality, "
        "whether it hooks the reader, and who it is likely to appeal to\n\n"
        "Use the exact section headers above. Do not add extra sections."
    )

    def __init__(self, book_title: str, parent=None):
        super().__init__(parent)
        self._title = book_title

    def _apply_settings(self, mod):
        try:
            settings = get_matrix_data().get("settings", {})
            if settings.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
                mod.GROQ_API_KEY = settings["groq_api_key"]
            active = get_session_groq_model() or settings.get("groq_model")
            if active and hasattr(mod, "GROQ_MODEL"):
                mod.GROQ_MODEL = active
        except Exception:
            pass

    def _read_first_chapters(self, mod) -> str:
        """Return the first MAX_CHAPTERS chapters, each capped at MAX_CH_CHARS."""
        try:
            import re as _re
            save_path = mod.get_book_path(self._title)
            if not save_path or not Path(save_path).exists():
                return ""
            with open(save_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
            blocks = _re.split(r"={50,}", raw)
            chapters = []
            i = 1
            while i < len(blocks) - 1:
                header = blocks[i].strip()
                body   = blocks[i + 1].strip() if i + 1 < len(blocks) else ""
                i += 2
                m = _re.match(r"Chapter\s+(\d+)\s*[:\-]?\s*(.*)", header, _re.IGNORECASE)
                if m:
                    chapters.append((int(m.group(1)), m.group(2).strip(), body))
            if not chapters:
                return ""
            chapters.sort(key=lambda x: x[0])
            first = chapters[: self.MAX_CHAPTERS]
            parts = []
            total = 0
            for ch_num, title, body in first:
                snippet = body[: self.MAX_CH_CHARS]
                entry   = f"Chapter {ch_num}: {title}\n\n{snippet}"
                if total + len(entry) > self.MAX_TOTAL_CHARS:
                    break
                parts.append(entry)
                total += len(entry)
            return ("\n\n" + "=" * 40 + "\n\n").join(parts)
        except Exception as e:
            return ""

    def run(self):
        try:
            from great_sage_core import legion_mod as _clm
            mod, err = _clm()
            if err or not mod:
                self.error.emit(f"Legion unavailable: {err or 'not loaded'}")
                return
        except Exception as e:
            self.error.emit(f"Import error: {e}")
            return

        self._apply_settings(mod)

        chapter_text = self._read_first_chapters(mod)
        if not chapter_text.strip():
            self.error.emit("No chapter text found for this book.")
            return

        try:
            from great_sage_core import sage_mod as _sm
            smod, serr = _sm()
            if serr or not smod:
                self.error.emit(f"Sage unavailable: {serr or 'not loaded'}")
                return
        except Exception as e:
            self.error.emit(f"Sage import error: {e}")
            return

        self._apply_settings(smod)

        prompt = (
            f"Book title: {self._title}\n\n"
            f"--- CHAPTER TEXT START ---\n{chapter_text}\n--- CHAPTER TEXT END ---\n\n"
            f"Write the structured preview report now."
        )

        try:
            resp, err = smod.groq_chat(prompt, system=self.SYSTEM_PROMPT)
            if err:
                self.error.emit(err)
            else:
                self.done.emit(resp.strip())
        except Exception as e:
            self.error.emit(str(e))


class ReaderSageWorker(QThread):
    """
    Background worker for the reader Sage sidebar.

    Two modes
    ---------
    web_search=False  (default)
        Scans all downloaded chapter text up to `current_chapter` for
        mentions of `term`, builds a prompt from the excerpts, and
        streams the Groq answer back chunk by chunk.

    web_search=True
        Runs a Tavily search for `term` and streams the answer.
        Used by the Browse button.

    Signals
    -------
    chunk   — emitted for every streamed text fragment
    done    — emitted with the full assembled response when complete
    error   — emitted with an error string on failure
    """
    chunk  = pyqtSignal(str)
    done   = pyqtSignal(str)
    error  = pyqtSignal(str)

    def __init__(self, term: str, book: str,
                 current_chapter: int = 0,
                 web_search: bool = False,
                 local_paragraphs: list | None = None,
                 parent=None):
        super().__init__(parent)
        self.term             = term.strip()
        self.book             = book
        self.current_chapter  = current_chapter
        self.web_search       = web_search
        self.local_paragraphs = local_paragraphs or []
        self._stop            = False

    def stop(self):
        self._stop = True

    # ── helpers ───────────────────────────────────────────────────────────────

    def _apply_settings(self, mod):
        """Push API key / model overrides from settings into the sage module."""
        try:
            settings = get_matrix_data().get("settings", {})
            if settings.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
                mod.GROQ_API_KEY = settings["groq_api_key"]
            active = get_session_groq_model() or settings.get("groq_model")
            if active and hasattr(mod, "GROQ_MODEL"):
                mod.GROQ_MODEL = active
        except Exception:
            pass

    def _stream(self, mod, prompt: str, system: str = None):
        """Stream groq_stream_chat chunks through the chunk signal."""
        full = []
        if not hasattr(mod, "groq_stream_chat"):
            # Fallback: blocking call
            resp, err = mod.groq_chat(prompt, system=system)
            if err:
                self.error.emit(err)
            else:
                self.chunk.emit(resp)
                self.done.emit(resp)
            return

        for text, err in mod.groq_stream_chat(prompt, system=system):
            if self._stop:
                break
            if err:
                self.error.emit(err)
                return
            if text:
                full.append(text)
                self.chunk.emit(text)

        self.done.emit("".join(full))

    # ── run ───────────────────────────────────────────────────────────────────

    def run(self):
        mod, err = sage_mod()
        if err or not mod:
            self.error.emit(f"Sage unavailable: {err or 'sage.py not loaded'}")
            return

        self._apply_settings(mod)
        q = self.term

        # ── WEB BROWSE MODE ───────────────────────────────────────────────────
        if self.web_search:
            search_ctx = ""
            if hasattr(mod, "tavily_search"):
                try:
                    search_ctx = mod.tavily_search(q)
                except Exception:
                    pass

            if search_ctx:
                prompt = (
                    f"You are a knowledgeable assistant helping a reader.\n"
                    f"The reader is currently reading '{self.book}' "
                    f"(chapter {self.current_chapter}).\n\n"
                    f"[Live web search results for: \"{q}\"]\n"
                    f"{search_ctx}\n\n---\n"
                    f"Using the search results above, answer the reader's question "
                    f"clearly and concisely. Cite sources where relevant.\n\n"
                    f"Question: {q}"
                )
            else:
                prompt = (
                    f"You are a knowledgeable assistant. "
                    f"Answer the following question as accurately as possible. "
                    f"If the answer requires very recent information you may not have, "
                    f"say so clearly.\n\nQuestion: {q}"
                )
            self._stream(mod, prompt)
            return

        # ── BOOK COMPANION MODE ───────────────────────────────────────────────
        import re as _re
        lookup = _re.match(
            r"^(?:who\s+is|what\s+is|tell\s+me\s+about|describe|explain)\s+(.+)$",
            q, _re.IGNORECASE,
        )
        term_to_grep = lookup.group(1).strip().rstrip("?.") if lookup else q

        excerpts = ""
        if self.local_paragraphs:
            # Local book — grep the in-memory paragraphs directly
            needle = term_to_grep.lower()
            hits = [p for p in self.local_paragraphs if needle in p.lower()]
            if hits:
                excerpts = "\n\n".join(hits[:40])
        elif self.current_chapter > 0:
            try:
                excerpts = _grep_book_for_term(
                    self.book, term_to_grep, self.current_chapter)
            except Exception:
                pass

        if self._stop:
            return

        is_local = bool(self.local_paragraphs)
        position = f"page {self.current_chapter}" if is_local else f"chapter {self.current_chapter}"

        if excerpts:
            is_who = bool(_re.match(r"who\s+is", q, _re.IGNORECASE))
            if is_who:
                prompt = (
                    f"You are a reading companion for '{self.book}'.\n"
                    f"The reader is on {position} "
                    f"and wants to know about '{term_to_grep}'.\n"
                    f"Using ONLY the excerpts below (no outside knowledge), "
                    f"write a detailed character dossier: "
                    f"appearance, personality, role, relationships, notable moments.\n\n"
                    f"EXCERPTS:\n{excerpts}"
                )
            else:
                prompt = (
                    f"You are a reading companion for '{self.book}'.\n"
                    f"The reader is on {position} "
                    f"and wants to know about '{term_to_grep}'.\n"
                    f"Using ONLY the excerpts below (no outside knowledge), "
                    f"write a clear, detailed entry: what it is, why it matters, "
                    f"how it fits into the story so far.\n\n"
                    f"EXCERPTS:\n{excerpts}"
                )
        else:
            no_text_msg = (
                "No matching text was found in this book for that term."
                if is_local else
                "No local chapter text was found for this term."
            )
            prompt = (
                f"You are a reading companion for '{self.book}' "
                f"({position}).\n"
                f"The reader asks: '{q}'\n\n"
                f"{no_text_msg} "
                f"Answer based on general knowledge if the title is well-known, "
                f"otherwise say the term hasn't appeared yet in the text read."
            )

        self._stream(mod, prompt)


# ── Chapter List Drawer ────────────────────────────────────────────────────────

class ChapterListDrawer(QFrame):
    """
    Slide-up drawer that shows all downloaded chapters for the current book.

    - Parses the book's .txt file for Chapter headers (=====\\nChapter N: Title)
    - Auto-scrolls to the current chapter when opened
    - Chapter number input at the top for quick jumps
    - QFileSystemWatcher keeps the list live as chapters download
    - Clicking a chapter emits chapter_selected(local_disk_url)
    """

    chapter_selected = pyqtSignal(str)   # emits local-disk://chapter/{num}/{title}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book_title: str = ""
        self._txt_path:   str = ""
        self._current_num: int = 0
        self._chapters:   list[tuple[int, str]] = []   # (num, title)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        self.setObjectName("ChapterDrawer")
        self.setStyleSheet(
            f"QFrame#ChapterDrawer{{"
            f"  background:{BG2}; border-top:1px solid {BORDER2};"
            f"}}"
        )
        self._build()
        self.hide()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        # ── Header row ────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        title_lbl = QLabel("CHAPTERS")
        title_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:2px; background:transparent;")
        hdr.addWidget(title_lbl)

        hdr.addStretch()

        # Chapter jump input
        self._jump_input = QLineEdit()
        self._jump_input.setPlaceholderText("Go to chapter…")
        self._jump_input.setFixedWidth(140)
        self._jump_input.setFixedHeight(26)
        self._jump_input.setStyleSheet(
            f"QLineEdit{{background:{BG3}; border:1px solid {BORDER2}; "
            f"border-radius:3px; color:{TEXT}; font-size:11px; padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT};}}")
        self._jump_input.returnPressed.connect(self._on_jump)
        hdr.addWidget(self._jump_input)

        jump_btn = QPushButton("Jump")
        jump_btn.setFixedSize(50, 26)
        jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        jump_btn.setStyleSheet(
            f"QPushButton{{background:{BG3}; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; border-radius:3px; font-size:10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")
        jump_btn.clicked.connect(self._on_jump)
        hdr.addWidget(jump_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; "
            f"color:{MUTED}; font-size:12px;}}"
            f"QPushButton:hover{{color:{TEXT};}}")
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)

        lay.addLayout(hdr)

        # ── Chapter list ──────────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{"
            f"  background:{BG3}; border:1px solid {BORDER}; border-radius:4px;"
            f"  color:{TEXT2}; font-size:11px; outline:none;"
            f"}}"
            f"QListWidget::item{{"
            f"  padding:6px 10px; border-bottom:1px solid {BORDER};"
            f"}}"
            f"QListWidget::item:hover{{"
            f"  background:#1A1628; color:{TEXT};"
            f"}}"
            f"QListWidget::item:selected{{"
            f"  background:#1E1040; color:{ACCENT}; border-left:2px solid {ACCENT};"
            f"}}"
            f"QScrollBar:vertical{{background:{BG2}; width:6px; border:none; margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{BORDER2}; border-radius:3px; min-height:30px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{ACCENT};}}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{{height:0;}}"
        )
        self._list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self._list)

        # ── Chapter count label ───────────────────────────────────────────────
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:1px; background:transparent;")
        lay.addWidget(self._count_lbl)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_book(self, book_title: str, txt_path: str, current_chapter: int = 0):
        """Set the book and rebuild the chapter list."""
        # Unwatch old file
        if self._txt_path and self._txt_path in self._watcher.files():
            self._watcher.removePath(self._txt_path)

        self._book_title  = book_title
        self._txt_path    = txt_path
        self._current_num = current_chapter

        if txt_path and os.path.exists(txt_path):
            self._watcher.addPath(txt_path)

        self._rebuild()

    def set_current_chapter(self, num: int):
        """Update the highlighted current chapter without rebuilding the list."""
        self._current_num = num
        self._highlight_current()

    def open(self):
        """Show the drawer and scroll to the current chapter."""
        self.show()
        self._highlight_current()
        self._jump_input.clear()
        self._jump_input.setFocus()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _on_file_changed(self, _path: str):
        """Called by QFileSystemWatcher when the .txt file grows."""
        # Re-add the path — some editors replace the file on save
        if self._txt_path and not self._txt_path in self._watcher.files():
            self._watcher.addPath(self._txt_path)
        QTimer.singleShot(300, self._rebuild)   # slight delay for write to flush

    def _rebuild(self):
        """Parse the .txt file and repopulate the list widget."""
        self._chapters = self._parse_chapters()
        self._list.clear()

        for num, title in self._chapters:
            item = QListWidgetItem(f"Chapter {num}  —  {title}" if title else f"Chapter {num}")
            item.setData(Qt.ItemDataRole.UserRole, num)
            self._list.addItem(item)

        self._count_lbl.setText(
            f"{len(self._chapters)} chapters downloaded" if self._chapters else "No chapters downloaded yet")
        self._highlight_current()

    def _parse_chapters(self) -> list[tuple[int, str]]:
        """Return list of (chapter_num, title) from the book's .txt file."""
        if not self._txt_path or not os.path.exists(self._txt_path):
            return []
        try:
            with open(self._txt_path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            blocks   = re.split(r"={50,}", raw)
            chapters = []
            seen     = set()
            for block in blocks:
                m = re.match(r"\s*Chapter\s+(\d+)\s*[:\-]?\s*(.*)", block.strip(), re.IGNORECASE)
                if m:
                    num   = int(m.group(1))
                    title = m.group(2).strip()
                    if num not in seen:
                        seen.add(num)
                        chapters.append((num, title))
            chapters.sort(key=lambda x: x[0])
            return chapters
        except Exception:
            return []

    def _highlight_current(self):
        """Select and scroll to the current chapter in the list."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == self._current_num:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(
                    item, QListWidget.ScrollHint.PositionAtCenter)
                return

    def _on_item_clicked(self, item: QListWidgetItem):
        num = item.data(Qt.ItemDataRole.UserRole)
        if num is not None and self._book_title:
            from urllib.parse import quote as _q
            url = f"local-disk://chapter/{num}/{_q(self._book_title)}"
            self.chapter_selected.emit(url)
            self.hide()

    def _on_jump(self):
        text = self._jump_input.text().strip()
        if not text.isdigit():
            return
        num = int(text)
        if self._book_title:
            from urllib.parse import quote as _q
            url = f"local-disk://chapter/{num}/{_q(self._book_title)}"
            self.chapter_selected.emit(url)
            self.hide()


class ReaderPanel(QWidget):
    """
    Full-screen reading panel.
    Opens a chapter URL, renders paragraphs in a scrollable QTextEdit,
    and provides prev/next navigation + persistent font-size control.
    Right side: collapsible Sage sidebar (320 px fixed).
    """
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG};")
        self._settings        = _load_reader_settings()
        self._font_size       = self._settings.get("font_size", READER_FONT_DEFAULT)
        self._book: BookItem | None  = None
        self._current_url: str | None = None
        self._next_url:    str | None = None
        self._prev_url:    str | None = None
        self._paragraphs:  list       = []
        self._current_chapter_num: int = 0
        self._worker: ChapterFetchWorker | None = None
        self._sage_worker: ReaderSageWorker | None = None
        self._sidebar_visible: bool = True
        self._sage_full_text:  str  = ""
        self._scroll_save_timer = QTimer(self)
        self._scroll_save_timer.setSingleShot(True)
        self._scroll_save_timer.setInterval(800)  # debounce: save 800ms after scrolling stops
        self._scroll_save_timer.timeout.connect(self._save_scroll_position)
        self._restoring_scroll: bool = False
        self._build()
        self._chapter_drawer = ChapterListDrawer(self)
        self._chapter_drawer.chapter_selected.connect(self._load_chapter)
        self.hide()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ──────────────────────────────────────────────────────────
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"background:{BG2}; border-bottom:1px solid {BORDER};")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 0, 16, 0)
        bar_lay.setSpacing(12)

        back_btn = QPushButton("← Back")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; color:{TEXT2}; "
            f"font-size:11px; letter-spacing:1px; padding:4px 10px;}}"
            f"QPushButton:hover{{color:{ACCENT};}}")
        back_btn.clicked.connect(self._on_close)
        bar_lay.addWidget(back_btn)

        self._chapter_title = QLabel()
        self._chapter_title.setStyleSheet(
            f"color:{TEXT}; font-size:12px; letter-spacing:1px; background:transparent;")
        self._chapter_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar_lay.addWidget(self._chapter_title, 1)

        # Font size controls
        font_lbl = QLabel("A")
        font_lbl.setStyleSheet(f"color:{MUTED}; font-size:10px; background:transparent;")
        bar_lay.addWidget(font_lbl)

        self._font_dec = QPushButton("−")
        self._font_dec.setFixedSize(28, 28)
        self._font_dec.setCursor(Qt.CursorShape.PointingHandCursor)
        _font_btn_ss = (
            f"QPushButton{{background:{BG3}; border:1px solid {BORDER}; "
            f"color:{TEXT2}; border-radius:4px; font-size:14px; padding:0;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")
        self._font_dec.setStyleSheet(_font_btn_ss)
        self._font_dec.clicked.connect(self._decrease_font)
        bar_lay.addWidget(self._font_dec)

        self._font_lbl = QLabel(str(self._font_size))
        self._font_lbl.setFixedWidth(28)
        self._font_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._font_lbl.setStyleSheet(
            f"color:{TEXT2}; font-size:11px; background:transparent;")
        bar_lay.addWidget(self._font_lbl)

        self._font_inc = QPushButton("+")
        self._font_inc.setFixedSize(28, 28)
        self._font_inc.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font_inc.setStyleSheet(_font_btn_ss)
        self._font_inc.clicked.connect(self._increase_font)
        bar_lay.addWidget(self._font_inc)

        # Sage toggle button
        self._sage_toggle_btn = QPushButton("✦ Sage")
        self._sage_toggle_btn.setFixedHeight(28)
        self._sage_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sage_toggle_btn.setCheckable(True)
        self._sage_toggle_btn.setChecked(True)
        self._sage_toggle_btn.setStyleSheet(
            f"QPushButton{{background:{BG3}; border:1px solid {BORDER}; "
            f"color:{MUTED}; border-radius:4px; font-size:10px; "
            f"letter-spacing:1px; padding:0 10px;}}"
            f"QPushButton:checked{{background:#0D0A18; border-color:{ACCENT}; color:{ACCENT};}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")
        self._sage_toggle_btn.clicked.connect(self._toggle_sidebar)
        bar_lay.addWidget(self._sage_toggle_btn)

        root.addWidget(bar)

        # ── Horizontal body (reading area + sidebar) ──────────────────────────
        body = QWidget()
        body.setStyleSheet(f"background:{BG};")
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        # Reading area
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFrameShape(QFrame.Shape.NoFrame)
        self._text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setStyleSheet(
            f"QTextEdit{{"
            f"  background:{BG}; color:{TEXT}; border:none; padding:0;"
            f"}}"
            f"QScrollBar:vertical{{"
            f"  background:{BG2}; width:8px; border:none; margin:0;"
            f"}}"
            f"QScrollBar::handle:vertical{{"
            f"  background:{BORDER2}; border-radius:4px; min-height:40px;"
            f"}}"
            f"QScrollBar::handle:vertical:hover{{background:{ACCENT};}}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{{height:0;}}")
        self._text.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._apply_font()
        body_lay.addWidget(self._text, 1)

        # ── Sage sidebar ──────────────────────────────────────────────────────
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(400)
        self._sidebar.setStyleSheet(
            f"QFrame{{background:#09080F; border-left:1px solid {BORDER};}}")
        sb = QVBoxLayout(self._sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(0)


        # Template buttons row
        tpl_row = QWidget()
        tpl_row.setStyleSheet("background:transparent;")
        tpl_lay = QHBoxLayout(tpl_row)
        tpl_lay.setContentsMargins(12, 10, 12, 0)
        tpl_lay.setSpacing(8)

        _tpl_ss = (
            f"QPushButton{{background:#0F0D1A; border:1px solid #2A2040; "
            f"color:#7060A0; border-radius:3px; font-size:9px; "
            f"letter-spacing:1px; padding:5px 8px; font-family:{FONT_UI};}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")

        self._tpl_who_btn = QPushButton("Who is…")
        self._tpl_who_btn.setStyleSheet(_tpl_ss)
        self._tpl_who_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tpl_who_btn.clicked.connect(lambda: self._apply_template("Who is "))
        tpl_lay.addWidget(self._tpl_who_btn)

        self._tpl_what_btn = QPushButton("What is…")
        self._tpl_what_btn.setStyleSheet(_tpl_ss)
        self._tpl_what_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tpl_what_btn.clicked.connect(lambda: self._apply_template("What is "))
        tpl_lay.addWidget(self._tpl_what_btn)

        tpl_lay.addStretch()
        sb.addWidget(tpl_row)

        # Term input row
        input_row = QWidget()
        input_row.setStyleSheet("background:transparent;")
        input_lay = QHBoxLayout(input_row)
        input_lay.setContentsMargins(12, 8, 12, 0)
        input_lay.setSpacing(6)

        self._sage_input = QLineEdit()
        self._sage_input.setPlaceholderText("Character, place, power…")
        self._sage_input.setStyleSheet(
            f"QLineEdit{{background:#0F0D1A; border:1px solid #2A2040; "
            f"border-radius:3px; color:{TEXT}; font-size:12px; "
            f"padding:6px 10px; font-family:{FONT_UI};}}"
            f"QLineEdit:focus{{border-color:{ACCENT};}}")
        self._sage_input.returnPressed.connect(self._ask_sage)
        input_lay.addWidget(self._sage_input, 1)
        sb.addWidget(input_row)

        # Action buttons row
        action_row = QWidget()
        action_row.setStyleSheet("background:transparent;")
        action_lay = QHBoxLayout(action_row)
        action_lay.setContentsMargins(12, 8, 12, 10)
        action_lay.setSpacing(8)

        self._browse_btn = QPushButton("⌕  Browse")
        self._browse_btn.setFixedHeight(30)
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.setStyleSheet(
            f"QPushButton{{background:#0F0D1A; border:1px solid #2A2040; "
            f"color:#7060A0; border-radius:3px; font-size:10px; "
            f"letter-spacing:0.5px; padding:0 12px; font-family:{FONT_UI};}}"
            f"QPushButton:hover{{border-color:#5040A0; color:#A090E0;}}"
            f"QPushButton:disabled{{color:#302840; border-color:#1A1628;}}")
        self._browse_btn.clicked.connect(self._browse_term)
        action_lay.addWidget(self._browse_btn)

        self._ask_btn = QPushButton("✦  Ask Sage")
        self._ask_btn.setFixedHeight(30)
        self._ask_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ask_btn.setStyleSheet(
            f"QPushButton{{background:#1A0F2E; border:1px solid {ACCENT}; "
            f"color:{ACCENT}; border-radius:3px; font-size:10px; "
            f"letter-spacing:0.5px; padding:0 12px; font-family:{FONT_UI};}}"
            f"QPushButton:hover{{background:#220F3E; border-color:#D4B870;}}"
            f"QPushButton:disabled{{background:#0D0A18; border-color:#2A2040; "
            f"color:#3A3050;}}")
        self._ask_btn.clicked.connect(self._ask_sage)
        action_lay.addWidget(self._ask_btn, 1)
        sb.addWidget(action_row)

        # Divider
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet(f"border:none; border-top:1px solid {BORDER}; margin:0;")
        div.setFixedHeight(1)
        sb.addWidget(div)

        # Output area
        self._sage_output = QTextEdit()
        self._sage_output.setReadOnly(True)
        self._sage_output.setFrameShape(QFrame.Shape.NoFrame)
        self._sage_output.setStyleSheet(
            f"QTextEdit{{background:transparent; color:{TEXT}; border:none; "
            f"font-size:12px; font-family:{FONT_UI}; padding:14px 14px;}}"
            f"QScrollBar:vertical{{background:#0D0B18; width:6px; border:none; margin:0;}}"
            f"QScrollBar::handle:vertical{{background:#2A2040; border-radius:3px; min-height:30px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{ACCENT};}}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{{height:0;}}")
        self._sage_output.setPlaceholderText(
            "Ask Sage anything about this book.\n\n"
            "Use the templates above or type freely.\n\n"
            "Sage reads only chapters you've already reached.")
        sb.addWidget(self._sage_output, 1)

        # Sage status bar (spinner / word count)
        self._sage_status = QLabel()
        self._sage_status.setFixedHeight(24)
        self._sage_status.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:1px; "
            f"background:#0D0B18; border-top:1px solid {BORDER}; "
            f"padding:0 14px; font-family:{FONT_UI};")
        sb.addWidget(self._sage_status)

        body_lay.addWidget(self._sidebar)
        root.addWidget(body, 1)

        # ── Loading overlay ────────────────────────────────────────────────────
        self._loading_lbl = QLabel("Loading chapter…")
        self._loading_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_lbl.setStyleSheet(
            f"color:{MUTED}; font-size:13px; background:{BG};")
        self._loading_lbl.hide()

        # ── Bottom nav bar ─────────────────────────────────────────────────────
        nav = QWidget()
        nav.setFixedHeight(52)
        nav.setStyleSheet(
            f"background:{BG2}; border-top:1px solid {BORDER};")
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(24, 0, 24, 0)
        nav_lay.setSpacing(12)

        self._prev_btn = QPushButton("← Previous")
        self._prev_btn.setFixedHeight(34)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _nav_btn_ss = (
            f"QPushButton{{background:{BG3}; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; border-radius:4px; padding:0 20px; font-size:11px; letter-spacing:1px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}"
            f"QPushButton:disabled{{color:{MUTED}; border-color:{BORDER};}}")
        self._prev_btn.setStyleSheet(_nav_btn_ss)
        self._prev_btn.clicked.connect(self._on_prev)
        nav_lay.addWidget(self._prev_btn)

        nav_lay.addStretch()

        self._nav_label = QLabel()
        self._nav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_label.setStyleSheet(
            f"color:{MUTED}; font-size:10px; letter-spacing:1px; background:transparent;")
        nav_lay.addWidget(self._nav_label)

        self._chapters_btn = QPushButton("≡  Chapters")
        self._chapters_btn.setFixedHeight(34)
        self._chapters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chapters_btn.setStyleSheet(
            f"QPushButton{{background:{BG3}; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; border-radius:4px; padding:0 16px; font-size:11px; letter-spacing:1px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}"
            f"QPushButton:checked{{background:#1E1040; border-color:{ACCENT}; color:{ACCENT};}}")
        self._chapters_btn.setCheckable(True)
        self._chapters_btn.clicked.connect(self._toggle_chapter_drawer)
        nav_lay.addWidget(self._chapters_btn)

        nav_lay.addStretch()

        self._next_btn = QPushButton("Next →")
        self._next_btn.setFixedHeight(34)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(_nav_btn_ss)
        self._next_btn.clicked.connect(self._on_next)
        nav_lay.addWidget(self._next_btn)

        root.addWidget(nav)

    # ── Font ──────────────────────────────────────────────────────────────────

    def _apply_font(self):
        self._text.setStyleSheet(
            f"QTextEdit{{"
            f"  background:{BG}; color:{TEXT}; border:none;"
            f"  font-family:{FONT_BODY}; font-size:{self._font_size}px; padding:0;"
            f"}}"
            f"QScrollBar:vertical{{"
            f"  background:{BG2}; width:8px; border:none; margin:0;"
            f"}}"
            f"QScrollBar::handle:vertical{{"
            f"  background:{BORDER2}; border-radius:4px; min-height:40px;"
            f"}}"
            f"QScrollBar::handle:vertical:hover{{background:{ACCENT};}}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{{height:0;}}")

    def _increase_font(self):
        if self._font_size < READER_FONT_MAX:
            self._font_size += 1
            self._font_lbl.setText(str(self._font_size))
            self._rerender()
            self._persist_font()

    def _decrease_font(self):
        if self._font_size > READER_FONT_MIN:
            self._font_size -= 1
            self._font_lbl.setText(str(self._font_size))
            self._rerender()
            self._persist_font()

    def _rerender(self):
        """Re-render current paragraphs with updated font size, preserving scroll position."""
        if not self._paragraphs:
            return
        pos = self._text.verticalScrollBar().value()
        self._text.setHtml(self._build_html(self._paragraphs))
        self._text.verticalScrollBar().setValue(pos)

    def _persist_font(self):
        self._settings["font_size"] = self._font_size
        _save_reader_settings(self._settings)

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def _toggle_sidebar(self):
        """Show/hide the 320-px Sage sidebar."""
        self._sidebar_visible = self._sage_toggle_btn.isChecked()
        self._sidebar.setVisible(self._sidebar_visible)

    def _toggle_chapter_drawer(self):
        """Show/hide the chapter list drawer."""
        if self._chapter_drawer.isVisible():
            self._chapter_drawer.hide()
            self._chapters_btn.setChecked(False)
        else:
            self._position_drawer()
            self._chapter_drawer.open()
            self._chapters_btn.setChecked(True)

    def _position_drawer(self):
        """Size and position the drawer above the bottom nav bar."""
        drawer_h = min(320, self.height() - 100)
        drawer_w = self.width()
        self._chapter_drawer.setGeometry(
            0, self.height() - 52 - drawer_h, drawer_w, drawer_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._chapter_drawer.isVisible():
            self._position_drawer()

    def _apply_template(self, prefix: str):
        """Pre-fill the input with a template prefix and focus it."""
        current = self._sage_input.text().strip()
        # If input already starts with a template prefix, replace it
        import re as _re
        cleaned = _re.sub(r"^(Who is |What is )", "", current, flags=_re.IGNORECASE).strip()
        self._sage_input.setText(prefix + cleaned)
        self._sage_input.setFocus()
        self._sage_input.setCursorPosition(len(self._sage_input.text()))

    def _sage_set_busy(self, busy: bool):
        self._ask_btn.setEnabled(not busy)
        self._browse_btn.setEnabled(not busy)
        self._sage_input.setEnabled(not busy)
        self._tpl_who_btn.setEnabled(not busy)
        self._tpl_what_btn.setEnabled(not busy)
        if busy:
            self._sage_status.setText("  ◌  Asking Sage…")
        else:
            words = len(self._sage_full_text.split()) if self._sage_full_text else 0
            self._sage_status.setText(f"  {words} words" if words else "")

    def _stop_sage_worker(self):
        if self._sage_worker and self._sage_worker.isRunning():
            self._sage_worker.stop()
            self._sage_worker.blockSignals(True)
            self._sage_worker.wait(800)

    def _ask_sage(self):
        term = self._sage_input.text().strip()
        if not term:
            return
        if not self._book:
            self._sage_output.setPlainText("No book open.")
            return

        self._stop_sage_worker()
        self._sage_full_text = ""
        self._sage_output.clear()
        self._sage_set_busy(True)

        self._sage_worker = ReaderSageWorker(
            term=term,
            book=self._book.title,
            current_chapter=self._current_chapter_num,
            web_search=False,
            local_paragraphs=self._paragraphs if self._book.source == "local" else None,
        )
        self._sage_worker.chunk.connect(self._on_sage_chunk)
        self._sage_worker.done.connect(self._on_sage_done)
        self._sage_worker.error.connect(self._on_sage_error)
        self._sage_worker.start()

    def _browse_term(self):
        term = self._sage_input.text().strip()
        if not term:
            return
        if not self._book:
            self._sage_output.setPlainText("No book open.")
            return

        self._stop_sage_worker()
        self._sage_full_text = ""
        self._sage_output.clear()
        self._sage_set_busy(True)
        self._sage_status.setText("  ◌  Browsing the web…")

        self._sage_worker = ReaderSageWorker(
            term=term,
            book=self._book.title,
            current_chapter=self._current_chapter_num,
            web_search=True,
        )
        self._sage_worker.chunk.connect(self._on_sage_chunk)
        self._sage_worker.done.connect(self._on_sage_done)
        self._sage_worker.error.connect(self._on_sage_error)
        self._sage_worker.start()

    def _on_sage_chunk(self, text: str):
        """Append a streamed chunk to the output area."""
        self._sage_full_text += text
        cursor = self._sage_output.textCursor()
        from PyQt6.QtGui import QTextCursor
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._sage_output.setTextCursor(cursor)
        self._sage_output.ensureCursorVisible()

    def _on_sage_done(self, _full_text: str):
        self._sage_set_busy(False)

    def _on_sage_error(self, msg: str):
        self._sage_output.setPlainText(f"Error: {msg}")
        self._sage_set_busy(False)

    def _build_html(self, paragraphs: list) -> str:
        body = "".join(
            f"<p>{p.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')}</p>"
            for p in paragraphs
        )
        return (
            f"<style>"
            f"body{{font-family:{FONT_BODY}; font-size:{self._font_size}px; "
            f"color:{TEXT}; line-height:1.8; margin:40px 60px;}}"
            f"p{{margin:0 0 1.1em 0;}}"
            f"</style><body>{body}</body>"
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def open_book(self, book: BookItem, start_url: str):
        """Open the reader, resuming from saved progress if available."""
        self._book = book
        self._prev_url = None

        # Wire chapter drawer to this book's .txt file
        import re as _re
        from great_sage_core import SCRIPT_DIR
        _safe    = _re.sub(r"[^\w\-_\. ]", "_", book.title)
        _txt     = str(SCRIPT_DIR / "library" / _safe / f"{_safe}.txt")
        self._chapter_drawer.set_book(book.title, _txt, self._current_chapter_num)
        self._chapter_drawer.hide()
        self._chapters_btn.setChecked(False)

        # Check for saved reading progress
        resume_url = self._load_progress(book.title)
        self._load_chapter(resume_url if resume_url else start_url)
        self.show()

    def open_local_book(self, book: BookItem):
        """Open a local EPUB/PDF/TXT file directly in the reader."""
        self._book     = book
        self._prev_url = None
        self._next_url = None

        self._paragraphs = []
        self._text.clear()
        self._chapter_title.setText("Loading…")
        self._nav_label.setText(book.title[:48] + "…" if len(book.title) > 48 else book.title)
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)

        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.blockSignals(True)

        self._worker = LocalFileWorker(book)
        self._worker.done.connect(self._on_local_file_loaded)
        self._worker.start()
        self.show()

    def _on_local_file_loaded(self, paragraphs: list, cover_bytes):
        """Called when LocalFileWorker finishes parsing the file."""
        if not paragraphs:
            self._chapter_title.setText("Empty file")
            self._text.setPlainText("No readable content found in this file.")
            return

        self._paragraphs = paragraphs
        self._chapter_title.setText(self._book.title if self._book else "")
        self._text.setHtml(self._build_html(paragraphs))
        self._text.verticalScrollBar().setValue(0)
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)

    # ── Progress persistence ──────────────────────────────────────────────────

    def _load_progress(self, title: str) -> str | None:
        """Return saved chapter URL for this book, or None."""
        try:
            data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            book = data.get("books", {}).get(title, {})
            return book.get("reader_url") or None
        except Exception:
            return None

    def _on_scroll(self, _value: int):
        """Restart the debounce timer whenever the user scrolls."""
        if self._restoring_scroll:
            return  # don't treat programmatic restore as a user scroll
        self._scroll_save_timer.start()

    def _save_scroll_position(self):
        """Persist current scroll position as a fraction of total scroll range."""
        if not self._book:
            return
        bar = self._text.verticalScrollBar()
        max_val = bar.maximum()
        fraction = (bar.value() / max_val) if max_val > 0 else 0.0
        try:
            data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            data.setdefault("books", {}).setdefault(self._book.title, {})
            data["books"][self._book.title]["scroll_fraction"] = fraction
            save_json(LEGION_PROGRESS, data)
        except Exception:
            pass

    def _restore_scroll_position(self, fraction: float):
        """Apply a saved scroll fraction once the chapter content has rendered."""
        if fraction <= 0:
            return
        bar = self._text.verticalScrollBar()
        self._restoring_scroll = True
        target = int(bar.maximum() * fraction)
        bar.setValue(target)
        self._restoring_scroll = False

    def _save_progress(self, title: str, url: str, chapter_title: str):
        """Persist current chapter URL into LEGION_PROGRESS."""
        data = {}
        try:
            import time as _time
            data = load_json_cached(LEGION_PROGRESS, {"books": {}})
            book_entry = data.setdefault("books", {}).setdefault(title, {})
            is_new_chapter = book_entry.get("reader_url") != url
            book_entry["reader_url"]     = url
            book_entry["reader_chapter"]  = chapter_title
            book_entry["last_read"]       = _time.time()
            if is_new_chapter:
                # Fresh chapter — start at the top, not wherever the last
                # chapter's scroll fraction happened to be.
                book_entry["scroll_fraction"] = 0.0
            save_json(LEGION_PROGRESS, data)
        except Exception:
            pass

        # ── Cloud sync ────────────────────────────────────────────────────
        try:
            import re as _re
            book_entry = data.get("books", {}).get(title, {})
            ch_num   = 0
            sync_url = ""

            if url and url.startswith("local-disk://chapter/"):
                # Reading from downloaded chapters on disk.
                # The sentinel URL carries the chapter number but is not a real
                # web URL — extract the number and use the saved fwn URL instead
                # so Supabase / TrackFlix always sees a meaningful reader_url.
                m = _re.match(r"local-disk://chapter/(\d+)/", url)
                if m:
                    ch_num = int(m.group(1))
                # Prefer last_downloaded_url (most specific chapter URL we have),
                # fall back to the book landing page URL.
                sync_url = (
                    book_entry.get("last_downloaded_url", "")
                    or book_entry.get("url", "")
                )
            elif url and not url.startswith("local-disk://"):
                # Live web URL (Royal Road, fwn direct) — extract chapter from URL.
                m = _re.search(r"/chapter-(\d+)", url)
                ch_num   = int(m.group(1)) if m else 0
                sync_url = url

            # Only push if we have a real URL and a non-zero chapter number.
            # This prevents accidentally resetting progress to 0 in Supabase.
            if sync_url and ch_num > 0:
                from gs_legion_sync import push_reader_progress
                push_reader_progress(
                    title       = title,
                    reader_url  = sync_url,
                    book_url    = book_entry.get("url", ""),
                    source      = book_entry.get("source", ""),
                    cover_url   = book_entry.get("cover_url", ""),
                    chapter_num = ch_num,
                )
        except Exception:
            pass

    # ── Chapter loading ───────────────────────────────────────────────────────

    def _load_chapter(self, url: str):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.blockSignals(True)

        self._current_url = url
        self._paragraphs  = []
        self._text.clear()
        self._chapter_title.setText("Loading…")
        self._nav_label.setText("")
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)

        book_name = self._book.title if self._book else ""
        self._worker = ChapterFetchWorker(url, book_name)
        self._worker.done.connect(self._on_chapter_loaded)
        self._worker.start()

    def _on_chapter_loaded(self, title: str, paragraphs: list,
                           next_url, prev_url, error):
        self._next_url = next_url
        self._prev_url = prev_url

        if error or not paragraphs:
            self._chapter_title.setText("Failed to load")
            self._text.setPlainText(
                f"Could not load chapter.\n\n{error or 'No content returned.'}\n\n"
                f"URL: {self._current_url}")
            return

        self._chapter_title.setText(title)
        self._paragraphs = paragraphs

        # Extract chapter number from URL sentinel or title — used by Sage
        import re as _re
        if self._current_url and self._current_url.startswith("local-disk://chapter/"):
            m = _re.match(r"local-disk://chapter/(\d+)/", self._current_url)
            if m:
                self._current_chapter_num = int(m.group(1))
        else:
            m = _re.search(r"chapter\s+(\d+)", title, _re.IGNORECASE)
            if not m and self._current_url:
                m = _re.search(r"[/-](\d+)(?:[/-]|$)", self._current_url)
            if m:
                self._current_chapter_num = int(m.group(1))

        # Determine if this is a resume of the exact chapter we left off on
        # (so we restore scroll position) vs navigating to a new chapter
        # (so we start at the top), BEFORE _save_progress overwrites the saved url.
        resume_fraction = 0.0
        if self._book:
            try:
                prog_data = load_json_cached(LEGION_PROGRESS, {"books": {}})
                saved_entry = prog_data.get("books", {}).get(self._book.title, {})
                if saved_entry.get("reader_url") == self._current_url:
                    resume_fraction = float(saved_entry.get("scroll_fraction", 0.0))
            except Exception:
                pass

        self._text.setHtml(self._build_html(paragraphs))
        self._text.verticalScrollBar().setValue(0)
        if resume_fraction > 0:
            # Defer restore until after the QTextEdit has laid out content
            # and computed a real scrollbar maximum.
            QTimer.singleShot(50, lambda f=resume_fraction: self._restore_scroll_position(f))

        # Persist reading position
        if self._book:
            self._save_progress(self._book.title, self._current_url, title)

        # Update chapter drawer highlight
        self._chapter_drawer.set_current_chapter(self._current_chapter_num)

        # Nav state
        self._prev_btn.setEnabled(bool(prev_url))
        self._next_btn.setEnabled(bool(next_url))
        book_title = self._book.title if self._book else ""
        self._nav_label.setText(book_title[:48] + "…" if len(book_title) > 48 else book_title)

    def _on_prev(self):
        if self._prev_url:
            self._load_chapter(self._prev_url)

    def _on_next(self):
        if self._next_url:
            self._load_chapter(self._next_url)

    def _on_close(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.blockSignals(True)
        self._stop_sage_worker()
        self._scroll_save_timer.stop()
        self._save_scroll_position()  # flush immediately, don't wait for debounce
        self.hide()
        self.closed.emit()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LEGION PAGE
# ══════════════════════════════════════════════════════════════════════════════

class LegionPage(QWidget):
    """
    Top-level Legion Discovery tab widget.
    Drop this into the Great Sage nav stack as the Legion page.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG};")
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._workers: list[QThread] = []
        self._active_tab = 0   # 0=discover, 1=jump_in, 2=library, 3=local
        self._current_page = 1  # pagination state for discover/search
        self._build()
        QTimer.singleShot(100, self._load_trending)
        QTimer.singleShot(500, self._resume_downloads)
        QTimer.singleShot(2000, self._startup_clean_junk)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # Outer: sidebar + main area side by side
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = QFrame()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(
            f"QFrame{{background:{BG2}; border-right:1px solid {BORDER};}}")
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0, 0, 0, 12)
        sv.setSpacing(0)

        # Sidebar header
        sb_hdr = QWidget()
        sb_hdr.setFixedHeight(52)
        sb_hdr.setStyleSheet(
            f"background:{BG2}; border-bottom:1px solid {BORDER};")
        sh = QHBoxLayout(sb_hdr)
        sh.setContentsMargins(16, 0, 16, 0)
        module_lbl = QLabel("LEGION")
        module_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:10px; letter-spacing:3px; "
            f"background:transparent;")
        sh.addWidget(module_lbl)
        sh.addStretch()
        sv.addWidget(sb_hdr)

        # Tab buttons
        _active_style = (
            f"background:{BG3}; color:{ACCENT}; border:none; "
            f"border-left:2px solid {ACCENT}; font-size:12px; "
            f"padding:12px 16px; text-align:left; border-radius:0;")
        _idle_style = (
            f"background:transparent; color:{MUTED}; border:none; "
            f"border-left:2px solid transparent; font-size:12px; "
            f"padding:12px 16px; text-align:left; border-radius:0;")
        self._tab_style_active = _active_style
        self._tab_style_idle   = _idle_style

        self._tab_btns = []
        for label, idx in [("Discover", 0), ("Jump In", 1), ("Library", 2), ("Local", 3)]:
            b = QPushButton(label)
            b.setStyleSheet(_active_style if idx == 0 else _idle_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            sv.addWidget(b)
            self._tab_btns.append(b)

        sv.addStretch(1)

        # Refresh button at bottom of sidebar
        ref_b = QPushButton("↻  Refresh")
        ref_b.setStyleSheet(
            f"background:transparent; border:none; color:{MUTED}; "
            f"font-size:11px; padding:10px 16px; text-align:left;")
        ref_b.setCursor(Qt.CursorShape.PointingHandCursor)
        ref_b.clicked.connect(self.refresh)
        sv.addWidget(ref_b)

        outer.addWidget(sidebar)

        # ── Main area ─────────────────────────────────────────────────────────
        main_w = QWidget()
        main_w.setStyleSheet("background:transparent;")
        main_lay = QVBoxLayout(main_w)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # Header bar (search + source dropdown)
        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(
            f"background:{BG2}; border-bottom:1px solid {BORDER};")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(20, 0, 20, 0)
        hh.setSpacing(12)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search novels, books, authors…")
        self._search_box.setStyleSheet(
            f"QLineEdit{{background:{BG3}; border:1px solid {BORDER2}; "
            f"border-radius:5px; color:{TEXT}; font-size:12px; padding:6px 12px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT};}}")
        self._search_box.textChanged.connect(self._on_search_changed)
        self._search_box.returnPressed.connect(self._on_search_commit)
        hh.addWidget(self._search_box, 1)

        self._search_btn = QPushButton("Search")
        self._search_btn.setFixedHeight(34)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setStyleSheet(
            f"QPushButton{{background:{ACCENT}; color:{BG}; border:none; "
            f"border-radius:5px; font-size:12px; font-weight:bold; padding:0 18px;}}"
            f"QPushButton:hover{{background:{ACCENT}dd;}}"
            f"QPushButton:pressed{{background:{ACCENT}aa;}}")
        self._search_btn.clicked.connect(self._on_search_commit)
        hh.addWidget(self._search_btn)

        self._source_combo = QComboBox()
        self._source_combo.addItems(
            ["All Sources", "Royal Road", "LibRead", "Gutenberg"])
        self._source_combo.setFixedWidth(140)
        self._source_combo.setStyleSheet(
            f"QComboBox{{background:{BG3}; border:1px solid {BORDER2}; "
            f"border-radius:5px; color:{TEXT2}; font-size:11px; padding:5px 10px;}}"
            f"QComboBox:hover{{border-color:{ACCENT};}}"
            f"QComboBox::drop-down{{border:none; width:20px;}}"
            f"QComboBox QAbstractItemView{{background:{BG2}; border:1px solid {BORDER2}; "
            f"color:{TEXT2}; selection-background-color:{ACCENT}; "
            f"selection-color:{BG};}}")
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        hh.addWidget(self._source_combo)
        main_lay.addWidget(header)

        # Status bar
        self._status_bar = QLabel("Loading…")
        self._status_bar.setFixedHeight(24)
        self._status_bar.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:1px; "
            f"padding:0 20px; background:{BG2}; border-bottom:1px solid {BORDER};")
        main_lay.addWidget(self._status_bar)

        # Content stack: 0=discover grid, 1=jump in, 2=bookmarks, 3=detail
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet("background:transparent;")

        # ── Page 0: Discover grid ─────────────────────────────────────────────
        disc_page = QWidget()
        disc_page.setStyleSheet("background:transparent;")
        dp_lay = QVBoxLayout(disc_page)
        dp_lay.setContentsMargins(0, 0, 0, 0)
        dp_lay.setSpacing(0)

        self._section_label = QLabel("TRENDING")
        self._section_label.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; "
            f"padding:12px 20px 4px; background:transparent;")
        dp_lay.addWidget(self._section_label)

        self._grid = BooksGrid()
        self._grid.book_clicked.connect(self._on_book_clicked)
        # Make scrollbar visible
        self._grid._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._grid._scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            "QScrollBar:vertical{"
            f"  background:{BG2}; width:8px; border-radius:4px; margin:0;}}"
            "QScrollBar::handle:vertical{"
            f"  background:{BORDER2}; border-radius:4px; min-height:30px;}}"
            "QScrollBar::handle:vertical:hover{"
            f"  background:{ACCENT};}}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{height:0;}")
        self._touch_filter = _TouchScrollFilter(
            self._grid._scroll.verticalScrollBar(), self._grid._scroll)
        self._grid._scroll.installEventFilter(self._touch_filter)
        dp_lay.addWidget(self._grid, 1)

        # ── Pagination bar ────────────────────────────────────────────────────
        pag_bar = QWidget()
        pag_bar.setFixedHeight(48)
        pag_bar.setStyleSheet(f"background:{BG2}; border-top:1px solid {BORDER};")
        pag_lay = QHBoxLayout(pag_bar)
        pag_lay.setContentsMargins(20, 0, 20, 0)
        pag_lay.setSpacing(12)

        btn_style = (
            f"QPushButton{{background:{BG3}; color:{TEXT2}; border:1px solid {BORDER2}; "
            f"border-radius:5px; font-size:12px; padding:4px 18px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}"
            f"QPushButton:disabled{{color:{MUTED}; border-color:{BORDER};}}")

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setFixedHeight(32)
        self._prev_btn.setStyleSheet(btn_style)
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(self._on_prev_page)
        self._prev_btn.setEnabled(False)
        pag_lay.addWidget(self._prev_btn)

        self._page_label = QLabel("Page 1")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet(
            f"color:{TEXT2}; font-size:12px; background:transparent;")
        pag_lay.addWidget(self._page_label, 1)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setFixedHeight(32)
        self._next_btn.setStyleSheet(btn_style)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._on_next_page)
        pag_lay.addWidget(self._next_btn)

        dp_lay.addWidget(pag_bar)
        self._content_stack.addWidget(disc_page)       # index 0

        # ── Page 1: Jump In ───────────────────────────────────────────────────
        ji_page = QWidget()
        ji_page.setStyleSheet("background:transparent;")
        ji_lay = QVBoxLayout(ji_page)
        ji_lay.setContentsMargins(20, 16, 20, 16)
        ji_lay.setSpacing(10)

        ji_top = QHBoxLayout()
        ji_top.setContentsMargins(0, 0, 0, 0)
        ji_hdr = QLabel("CONTINUE READING")
        ji_hdr.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        ji_top.addWidget(ji_hdr)
        ji_top.addStretch()
        self._ji_edit_btn = QPushButton("Edit")
        self._ji_edit_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:1px solid {BORDER2}; "
            f"color:{MUTED}; font-size:9px; letter-spacing:1px; padding:3px 10px; "
            f"border-radius:3px;}}"
            f"QPushButton:hover{{border-color:{ACCENT}; color:{ACCENT};}}")
        self._ji_edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ji_edit_btn.setCheckable(True)
        self._ji_edit_btn.toggled.connect(self._toggle_ji_edit_mode)
        ji_top.addWidget(self._ji_edit_btn)
        ji_lay.addLayout(ji_top)

        self._ji_grid = BooksGrid()
        self._ji_grid.book_clicked.connect(self._on_book_clicked)
        self._ji_grid.delete_requested.connect(self._delete_ji_book)
        # Install touch scroll filter on the ji grid too
        self._ji_touch_filter = _TouchScrollFilter(
            self._ji_grid._scroll.verticalScrollBar(), self._ji_grid._scroll)
        self._ji_grid._scroll.installEventFilter(self._ji_touch_filter)
        ji_lay.addWidget(self._ji_grid, 1)

        self._ji_empty = QLabel("No books in progress yet.\nDiscover a book and start reading!")
        self._ji_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ji_empty.setStyleSheet(
            f"color:{MUTED}; font-size:12px; background:transparent;")
        self._ji_empty.hide()
        ji_lay.addWidget(self._ji_empty)
        self._content_stack.addWidget(ji_page)         # index 1

        # ── Page 2: Library (categorised) ────────────────────────────────────
        lib_page = QWidget()
        lib_page.setStyleSheet("background:transparent;")
        lib_lay = QVBoxLayout(lib_page)
        lib_lay.setContentsMargins(0, 0, 0, 0)
        lib_lay.setSpacing(0)

        # Category tab bar
        lib_tab_bar = QWidget()
        lib_tab_bar.setFixedHeight(44)
        lib_tab_bar.setStyleSheet(
            f"background:{BG2}; border-bottom:1px solid {BORDER};")
        ltb = QHBoxLayout(lib_tab_bar)
        ltb.setContentsMargins(16, 0, 16, 0)
        ltb.setSpacing(0)

        self._lib_cat_btns = []
        self._lib_active_cat = 0
        _cat_active = (
            f"QPushButton{{background:transparent; color:{ACCENT}; border:none; "
            f"border-bottom:2px solid {ACCENT}; font-size:11px; "
            f"letter-spacing:1px; padding:0 16px;}}")
        _cat_idle = (
            f"QPushButton{{background:transparent; color:{MUTED}; border:none; "
            f"border-bottom:2px solid transparent; font-size:11px; "
            f"letter-spacing:1px; padding:0 16px;}}"
            f"QPushButton:hover{{color:{TEXT2};}}")
        self._lib_cat_active_style = _cat_active
        self._lib_cat_idle_style   = _cat_idle

        for i, label in enumerate(["Planning", "Reading", "Dropped", "Completed"]):
            b = QPushButton(label)
            b.setStyleSheet(_cat_active if i == 0 else _cat_idle)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, idx=i: self._switch_lib_cat(idx))
            ltb.addWidget(b)
            self._lib_cat_btns.append(b)
        ltb.addStretch()
        lib_lay.addWidget(lib_tab_bar)

        # Grid area
        lib_content = QWidget()
        lib_content.setStyleSheet("background:transparent;")
        lc_lay = QVBoxLayout(lib_content)
        lc_lay.setContentsMargins(20, 16, 20, 16)
        lc_lay.setSpacing(10)

        self._lib_grid = BooksGrid()
        self._lib_grid.book_clicked.connect(self._on_book_clicked)
        self._lib_grid.delete_requested.connect(self._on_lib_remove)
        lc_lay.addWidget(self._lib_grid, 1)

        self._lib_empty = QLabel("Nothing here yet.")
        self._lib_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lib_empty.setStyleSheet(
            f"color:{MUTED}; font-size:12px; background:transparent;")
        self._lib_empty.hide()
        lc_lay.addWidget(self._lib_empty)

        lib_lay.addWidget(lib_content, 1)
        self._content_stack.addWidget(lib_page)        # index 2

        # ── Page 3: Local books ───────────────────────────────────────────────
        local_page = QWidget()
        local_page.setStyleSheet("background:transparent;")
        loc_lay = QVBoxLayout(local_page)
        loc_lay.setContentsMargins(20, 16, 20, 16)
        loc_lay.setSpacing(10)

        loc_hdr = QLabel("LOCAL BOOKS")
        loc_hdr.setStyleSheet(
            f"color:{MUTED}; font-size:9px; letter-spacing:3px; background:transparent;")
        loc_lay.addWidget(loc_hdr)

        self._local_grid = BooksGrid()
        self._local_grid.book_clicked.connect(self._on_local_book_clicked)
        self._local_touch_filter = _TouchScrollFilter(
            self._local_grid._scroll.verticalScrollBar(), self._local_grid._scroll)
        self._local_grid._scroll.installEventFilter(self._local_touch_filter)
        loc_lay.addWidget(self._local_grid, 1)

        self._content_stack.addWidget(local_page)     # index 3

        # ── Page 4: Detail panel ──────────────────────────────────────────────
        self._detail = DetailPanel()
        self._detail.closed.connect(self._on_detail_closed)
        self._detail.book_action.connect(self._on_detail_book_action)
        self._content_stack.addWidget(self._detail)    # index 4

        # ── Page 5: Local detail panel ────────────────────────────────────────
        self._local_detail = LocalDetailPanel()
        self._local_detail.closed.connect(lambda: (
            self._status_bar.show(),
            self._content_stack.setCurrentIndex(3),
        ))
        self._local_detail.read_requested.connect(self._on_local_read)
        self._content_stack.addWidget(self._local_detail)  # index 5

        # ── Page 6: Reader ────────────────────────────────────────────────────
        self._reader = ReaderPanel()
        self._reader.closed.connect(self._on_reader_closed)
        self._content_stack.addWidget(self._reader)    # index 6

        main_lay.addWidget(self._content_stack, 1)
        outer.addWidget(main_w, 1)

    # ── Loading ───────────────────────────────────────────────────────────────

    def _cancel_workers(self):
        """Mark all in-flight workers as cancelled and release them — never blocks the UI."""
        for w in self._workers:
            w.cancel()          # tells worker to drop its result when done
            w.blockSignals(True)  # belt-and-suspenders: no signals reach the UI
        self._workers.clear()

    def _load_trending(self):
        self._cancel_workers()

        self._status_bar.setText("Loading trending…")
        self._grid.clear()
        self._update_page_controls()

        p = self._current_page
        source_filter = self._source_combo.currentIndex()  # 0 = all
        source_map    = {1: "royalroad", 2: "libread", 3: "gutenberg"}

        if p == 1:
            # Page 1: all sources merged
            tasks = [
                ("royalroad", lambda p=p: fetch_royalroad_trending(p)),
                ("libread",   lambda p=p: fetch_libread_popular(p)),
                ("gutenberg", lambda p=p: fetch_gutenberg_popular(p)),
            ]
            if source_filter > 0:
                key = source_map[source_filter]
                tasks = [(k, v) for k, v in tasks if k == key]
        else:
            # Page 2+: RoyalRoad best-rated (proper pagination, 20/page)
            tasks = [("royalroad", lambda p=p: fetch_royalroad_best_rated(p))]

        self._pending  = len(tasks)
        self._all_books: list[BookItem] = []

        for key, task in tasks:
            w = FetchWorker(task)
            w.results.connect(lambda items, k=key: self._on_trending_batch(items))
            w.error.connect(lambda e, k=key: self._on_fetch_error(k, e))
            w.start()
            self._workers.append(w)

    def _update_page_controls(self):
        self._page_label.setText(f"Page {self._current_page}")
        self._prev_btn.setEnabled(self._current_page > 1)

    def _on_prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._grid._scroll.verticalScrollBar().setValue(0)
            self._reload_current()

    def _on_next_page(self):
        self._current_page += 1
        self._grid._scroll.verticalScrollBar().setValue(0)
        self._reload_current()

    def _reload_current(self):
        """Re-run trending or search for the current page number."""
        if self._search_box.text().strip():
            self._do_search()
        else:
            self._load_trending()

    def _on_trending_batch(self, items: list[BookItem]):
        self._all_books.extend(items)
        self._pending -= 1
        query = self._search_box.text().strip()
        if self._pending <= 0:
            # Deduplicate by URL, then by normalised title as fallback
            seen_urls   = set()
            seen_titles = set()
            deduped     = []
            for b in self._all_books:
                url_key   = b.url.strip().rstrip("/").lower()
                title_key = b.title.strip().lower()
                if url_key in seen_urls or title_key in seen_titles:
                    continue
                seen_urls.add(url_key)
                seen_titles.add(title_key)
                deduped.append(b)

            if query:
                q_words = query.lower().split()
                ranked  = [b for b in deduped
                           if all(w in b.title.lower() for w in q_words)]
                ranked.sort(key=lambda b: _title_score(b.title, query), reverse=True)
                self._grid.set_books(ranked)
                self._status_bar.setText(f"{len(ranked)} results")
            else:
                self._grid.set_books(deduped)
                self._status_bar.setText(f"{len(deduped)} titles loaded")
            self._section_label.setText(
                "TRENDING" if not query else "RESULTS FOR  " + query.upper())
        else:
            if not query:
                self._grid.add_books(items)
            self._status_bar.setText(f"Loading… ({len(self._all_books)} so far)")

    def _on_fetch_error(self, source: str, err: str):
        self._pending -= 1
        self._status_bar.setText(f"Error loading {source}: {err[:60]}")

    # ── Search ────────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        if not text.strip():
            self._search_timer.stop()
            self._current_page = 1
            self._load_trending()

    def _on_search_commit(self):
        self._search_timer.stop()
        self._current_page = 1
        self._do_search()

    def _do_search(self):
        query = self._search_box.text().strip()
        if not query:
            return
        self._cancel_workers()
        self._status_bar.setText(f"Searching '{query}'…")
        self._grid.clear()
        self._section_label.setText("RESULTS FOR  " + query.upper())
        self._update_page_controls()

        source_filter = self._source_combo.currentIndex()
        p = self._current_page
        tasks = []
        if source_filter in (0, 1):
            tasks.append(("royalroad", lambda q=query, p=p: search_royalroad(q, p)))
        if source_filter in (0, 2):
            tasks.append(("libread",   lambda q=query, p=p: search_libread(q, p)))
        if source_filter in (0, 3):
            tasks.append(("gutenberg", lambda q=query, p=p: search_gutenberg(q, p)))

        self._pending = len(tasks)
        self._all_books = []
        for key, task in tasks:
            w = FetchWorker(task)
            w.results.connect(lambda items, k=key: self._on_trending_batch(items))
            w.error.connect(lambda e, k=key: self._on_fetch_error(k, e))
            w.start()
            self._workers.append(w)

    def _on_source_changed(self, _):
        if self._search_box.text().strip():
            self._do_search()
        else:
            self._load_trending()

    # ── Tab switching ─────────────────────────────────────────────────────────

    def _switch_tab(self, idx: int):
        self._active_tab = idx
        for i, b in enumerate(self._tab_btns):
            b.setStyleSheet(
                self._tab_style_active if i == idx else self._tab_style_idle)
        self._content_stack.setCurrentIndex(idx)
        show_search = (idx == 0)
        self._search_box.setVisible(show_search)
        self._search_btn.setVisible(show_search)
        self._source_combo.setVisible(show_search)
        if idx == 1:
            self._load_jump_in()
        elif idx == 2:
            self._load_library()
        elif idx == 3:
            self._load_local()

    # ── Jump In ───────────────────────────────────────────────────────────────

    def _load_jump_in(self):
        try:
            ld = get_legion_data()
            books = ld.get("books", {})
            sorted_books = sorted(
                books.items(),
                key=lambda x: x[1].get("last_read", x[1].get("chapters_read", 0)),
                reverse=True,
            )
            items = [
                BookItem(
                    title     = name,
                    cover_url = book.get("cover_url", ""),
                    url       = book.get("current_url", book.get("url", "")),
                    source    = book.get("source", "local"),
                    synopsis  = book.get("synopsis", ""),
                    author    = book.get("author", ""),
                )
                for name, book in sorted_books
            ]
            self._ji_grid.set_books(items)
            self._ji_empty.setVisible(len(items) == 0)
            self._ji_grid.setVisible(len(items) > 0)
        except Exception as e:
            self._status_bar.setText(f"Jump In load error: {e}")

    def _toggle_ji_edit_mode(self, enabled: bool):
        self._ji_edit_btn.setText("Done" if enabled else "Edit")
        for card in self._ji_grid._cards:
            card.set_delete_mode(enabled)

    def _delete_ji_book(self, book: BookItem):
        jump_in_remove(book.title, delete_files=False)
        self._status_bar.setText(f'Removed "{book.title}" from Jump In')
        self._ji_edit_btn.setChecked(False)
        self._load_jump_in()

    # ── Library ───────────────────────────────────────────────────────────────

    def _switch_lib_cat(self, idx: int):
        self._lib_active_cat = idx
        for i, b in enumerate(self._lib_cat_btns):
            b.setStyleSheet(
                self._lib_cat_active_style if i == idx
                else self._lib_cat_idle_style)
        self._load_library()

    def _load_library(self):
        try:
            cat_names = ("planning", "reading", "dropped", "completed")
            cat = cat_names[self._lib_active_cat]
            data = _load_library()
            entries = data.get(cat, [])
            items = [
                BookItem(
                    title     = e.get("title", "?"),
                    author    = e.get("author", ""),
                    cover_url = e.get("cover_url", ""),
                    url       = e.get("url", ""),
                    source    = e.get("source", "local"),
                    synopsis  = e.get("synopsis", ""),
                )
                for e in entries if isinstance(e, dict)
            ]
            self._lib_grid.set_books(items)
            # Always show remove button on library cards — no edit mode needed
            QTimer.singleShot(50, self._show_lib_remove_btns)
            self._lib_empty.setVisible(len(items) == 0)
            self._lib_grid.setVisible(len(items) > 0)
            empty_labels = {
                "planning":  "Nothing planned yet.\nAdd books from Discover.",
                "reading":   "Not reading anything.\nHit Read Now on a book to start.",
                "dropped":   "No dropped books.",
                "completed": "No completed books yet.",
            }
            self._lib_empty.setText(empty_labels[cat])
        except Exception as e:
            self._status_bar.setText(f"Library load error: {e}")

    def _show_lib_remove_btns(self):
        for card in self._lib_grid._cards:
            card.set_delete_mode(True)

    def _on_lib_remove(self, book: BookItem):
        """Remove button pressed on a library card — absolute removal."""
        library_remove(book.title)
        jump_in_remove(book.title, delete_files=True)
        self._status_bar.setText(f'Removed "{book.title}" from library')
        self._load_library()

    # ── Detail actions ────────────────────────────────────────────────────────

    def _on_book_clicked(self, book: BookItem):
        self._detail.show_book(book)
        self._status_bar.hide()
        # Hide search row when detail panel slides in — only belongs on Discover grid
        self._search_box.setVisible(False)
        self._search_btn.setVisible(False)
        self._source_combo.setVisible(False)
        self._content_stack.setCurrentIndex(4)

    def _on_detail_closed(self):
        """Return to grid and restore search bar if we came from Discover."""
        self._status_bar.show()
        self._content_stack.setCurrentIndex(self._active_tab)
        # Restore search UI only when returning to Discover tab (index 0)
        show_search = (self._active_tab == 0)
        self._search_box.setVisible(show_search)
        self._search_btn.setVisible(show_search)
        self._source_combo.setVisible(show_search)

    def _on_detail_book_action(self, action: str, book: BookItem):
        """Triggered by DetailPanel after read/add/remove — refresh affected tabs."""
        if action == "read":
            # Need the first chapter URL — get it from the detail worker's last result
            # or fall back to fetching chapters list on the fly
            self._open_reader(book)
            return

        if action in ("library_add", "library_remove"):
            self._load_jump_in()
            self._load_library()
            msgs = {
                "library_add":    f'"{book.title}" saved to library',
                "library_remove": f'"{book.title}" removed from library',
            }
            self._status_bar.setText(msgs.get(action, ""))

    def _enter_reader(self):
        """Switch to the reader panel and hide the status bar."""
        self._status_bar.hide()
        self._content_stack.setCurrentIndex(6)

    def _open_reader(self, book: BookItem):
        """Open ReaderPanel — use saved reading position or chapter 1 from disk if available."""
        # ── Priority 1: resume from saved reader URL
        try:
            from great_sage_core import load_json_cached
            data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
            entry = data.get("books", {}).get(book.title, {})
            saved_url = entry.get("reader_url", "")
            if saved_url:
                self._reader.open_book(book, saved_url)
                self._enter_reader()
                return
        except Exception:
            pass

        # ── Priority 2: _first_chapter_url already fetched by DetailPanel worker
        first_url = getattr(self._detail, "_first_chapter_url", None)
        if first_url:
            self._reader.open_book(book, first_url)
            self._enter_reader()
            return

        # ── Priority 3: book has downloaded chapters — open ch 1 from disk via sentinel
        try:
            import os, re as _re
            from urllib.parse import quote as _q
            from great_sage_core import SCRIPT_DIR
            safe      = _re.sub(r"[^\w\-_\. ]", "_", book.title)
            txt_path  = str(SCRIPT_DIR / "library" / safe / f"{safe}.txt")
            if os.path.exists(txt_path):
                # URL-encode the title so sentinel survives titles containing /
                encoded = _q(book.title, safe="")
                self._reader.open_book(book, f"local-disk://chapter/1/{encoded}")
                self._enter_reader()
                return
        except Exception:
            pass

        # ── Priority 4: fetch detail page in background to get chapter 1 URL
        self._status_bar.setText("Fetching first chapter…")
        def _task():
            if book.source == "royalroad":
                return fetch_royalroad_detail(book.url)
            elif book.source == "libread":
                return fetch_libread_detail(book.url)
            return {}

        w = FetchWorker(_task)
        w.detail.connect(lambda data: self._on_first_chapter_fetched(data, book))
        w.error.connect(lambda e: self._status_bar.setText(f"Could not open reader: {e}"))
        w.start()
        self._workers.append(w)

    def _on_first_chapter_fetched(self, data: dict, book: BookItem):
        chapters = data.get("chapters", [])
        if not chapters:
            self._status_bar.setText("No chapters found — cannot open reader.")
            return
        first_url = chapters[0].get("url", "")
        if not first_url:
            self._status_bar.setText("First chapter URL missing.")
            return
        self._reader.open_book(book, first_url)
        self._enter_reader()

    def _on_reader_closed(self):
        """Return to whichever panel launched the reader."""
        self._status_bar.show()
        if self._active_tab == 3:
            self._content_stack.setCurrentIndex(5)
        else:
            self._content_stack.setCurrentIndex(4)
        # Refresh Last Read stat now that the reader has saved progress.
        # The detail panel was opened before reading started so its stats
        # are stale — re-run _update_stats with the book that was just read.
        try:
            book = getattr(self._detail, "_current_book", None)
            if book:
                self._detail._update_stats(book)
        except Exception:
            pass

    # ── Local tab ─────────────────────────────────────────────────────────────

    def _load_local(self):
        """Scan LEGION_LIBRARY and populate the local grid."""
        books = scan_local_library()
        empty_msg = (
            "No books found.\n\n"
            f"Add EPUB, PDF, or TXT files to:\n\n  {LEGION_LIBRARY}\n\nthen hit  ↻ Refresh."
        ) if not books else ""
        self._local_grid.set_books(books, empty_message=empty_msg)
        self._status_bar.setText(f"{len(books)} local file{'s' if len(books) != 1 else ''}")

    def _on_local_book_clicked(self, book: BookItem):
        self._local_detail.show_book(book)
        self._content_stack.setCurrentIndex(5)  # local detail
        self._status_bar.hide()

    def _on_local_read(self, book: BookItem):
        """Read Now pressed on a local book — open in reader directly."""
        self._reader.open_local_book(book)
        self._enter_reader()

    # ── Required by MainWindow ─────────────────────────────────────────────────

    def _startup_clean_junk(self):
        """Silently clean junk paragraphs from all Jump In books on startup."""
        class _CleanWorker(QThread):
            done = pyqtSignal(str, int, int)  # title, paras_stripped, new_last

            def __init__(self, books, parent=None):
                super().__init__(parent)
                self._books = books

            def run(self):
                try:
                    from great_sage_core import legion_mod as _lm
                    mod, err = _lm()
                    if not mod or err:
                        return
                    for title in self._books:
                        import os as _os
                        if not _os.path.exists(mod.get_book_path(title)):
                            continue
                        result = mod.clean_junk_chapters(title)
                        stripped = result.get("paras_stripped", 0)
                        removed  = result.get("removed", 0)
                        if stripped > 0 or removed > 0:
                            self.done.emit(title, stripped, result["new_last"])
                except Exception:
                    pass

        def _on_done(title, stripped, new_last):
            try:
                data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
                entry = data.get("books", {}).get(title)
                if entry:
                    entry["last_downloaded_chapter"] = new_last
                    save_json(LEGION_PROGRESS, data)
                log.info("Startup junk cleanup", book=title,
                         paras_stripped=stripped, new_last=new_last)
            except Exception:
                pass

        try:
            data  = load_json_cached(LEGION_PROGRESS, {"books": {}})
            books = list(data.get("books", {}).keys())
        except Exception:
            return
        if not books:
            return
        self._startup_clean_worker = _CleanWorker(books)
        self._startup_clean_worker.done.connect(_on_done)
        self._startup_clean_worker.start()

    def _resume_downloads(self):
        """On startup, restart download workers for all existing Jump In books."""
        try:
            # ── Orphan cleanup ────────────────────────────────────────────────
            # Remove any LEGION_PROGRESS entries that have no matching library entry
            # in LEGION_BOOKMARKS.  These are books that were removed from Jump In
            # while a download worker was still running — the worker resurrected the
            # entry via _save_progress before the fix, leaving ghost entries that
            # reappear in Jump In on every launch with no remove button.
            try:
                ld       = get_legion_data()
                lib_data = get_bookmarks_data()
                lib_titles: set = set()
                for cat in ("planning", "reading", "dropped", "completed"):
                    for e in lib_data.get(cat, []):
                        if isinstance(e, dict) and e.get("title"):
                            lib_titles.add(e["title"])
                orphans = [t for t in ld.get("books", {}) if t not in lib_titles]
                if orphans:
                    for t in orphans:
                        _DownloadRegistry.stop(t)
                        del ld["books"][t]
                    save_json(LEGION_PROGRESS, ld)
            except Exception:
                pass
            # ─────────────────────────────────────────────────────────────────

            ld = get_legion_data()
            for title, entry in ld.get("books", {}).items():
                source = entry.get("source", "")
                if source not in ("royalroad", "libread", "gutenberg"):
                    continue
                if _DownloadRegistry.is_running(title):
                    continue
                book = BookItem(
                    title     = title,
                    author    = entry.get("author", ""),
                    cover_url = entry.get("cover_url", ""),
                    url       = entry.get("url", ""),
                    source    = source,
                    synopsis  = entry.get("synopsis", ""),
                )
                _DownloadRegistry.start(book)
        except Exception:
            pass

    def refresh(self):
        """Called by MainWindow._navigate() every time Legion is opened."""
        if self._active_tab == 1:
            self._load_jump_in()
        elif self._active_tab == 2:
            self._load_library()
        elif self._active_tab == 3:
            self._load_local()
        else:
            if not self._grid._cards:
                self._load_trending()

    def _show_toast(self, message: str):
        self._status_bar.setText(message)
        QTimer.singleShot(4000, lambda: self._status_bar.setText(
            f"{len(self._grid._cards)} titles loaded"
            if self._grid._cards else ""))
