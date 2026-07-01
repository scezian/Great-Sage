#!/usr/bin/env python3
"""
great_sage_core.py — Great Sage Backend
========================================
All non-UI logic: data helpers, module loading, workers, sync, behaviour tracking.
Imported by great_sage_gui.py.
"""

import importlib.util
import json
import os
import re
import subprocess
import signal
import sys
import threading
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Per-path locks — prevents concurrent track_event threads racing on the same .tmp file
_save_locks: dict = {}
_save_locks_lock = threading.Lock()

# ── Session model override ─────────────────────────────────────────────────────
# Set by the UI model switcher chips; overrides the saved settings model for
# the lifetime of this session only. None means "use whatever is in settings".
_session_groq_model: str | None = None

GROQ_MODEL_VERSATILE = "llama-3.3-70b-versatile"
GROQ_MODEL_INSTANT   = "llama-3.1-8b-instant"

def set_session_groq_model(model: str | None):
    """Override the active Groq model for this session only."""
    global _session_groq_model
    _session_groq_model = model

def get_session_groq_model() -> str | None:
    """Return the session model override, or None if not set."""
    return _session_groq_model

_event_executor = None

# ── Logging ────────────────────────────────────────────────────────────────────
try:
    from gs_logger import log
except Exception as _log_err:
    class _NoopLog:
        def __getattr__(self, name): return _NoopLog()
        def __call__(self, *a, **kw): return None
    log = _NoopLog()
    print(f"[great_sage_core] WARNING: gs_logger not available: {_log_err}")

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR       = Path(__file__).parent
LEGION_PROGRESS  = os.path.expanduser("~/.great_sage_legion.json")
MATRIX_PROGRESS  = os.path.expanduser("~/.config/matrix/progress.json")
LEGION_BOOKMARKS = os.path.expanduser("~/.great_sage_bookmarks.json")
SAGE_MEMORY_PATH = os.path.expanduser("~/.great_sage_memory.txt")
BEHAVIOUR_LOG        = os.path.expanduser("~/.great_sage_behaviour.json")
NOTIFICATIONS_PATH   = os.path.expanduser("~/.gs_notifications.json")

# --- Data Versioning ---
MATRIX_DATA_VERSION = 2

def _wl_now() -> str:
    """Return current UTC time as an ISO 8601 string for watchlist updated_at stamps."""
    from datetime import timezone as _tz
    return datetime.now(_tz.utc).isoformat()

# ── Show-name cleaning (canonical) ───────────────────────────────────────────
# This is the SINGLE authoritative implementation of show-title cleaning and
# fuzzy matching. Both matrix.py (Storage class) and sage.py (its own,
# separate get_matrix_data()/add_to_matrix_watchlist_list()) have — or had —
# their own copies of this logic. Multiple independent copies is how the
# Matrix duplicate-entry bug happened: each read/write path cleaned (or
# didn't clean) titles differently, so nothing stayed fixed. Every place
# that reads or writes Matrix watchlist/watching titles should import these
# functions from here rather than reimplementing them.

_FANSUB_TAG_RE = re.compile(
    r'[\[\(](?:SubsPlease|Erai-raws|Anime|HorribleSubs|ASW|Judas|EMBER|'
    r'Ohys-Raws|DB|Coalgirls|FFF|DameDesuYo|Leopard-Raws)[\]\)]',
    re.IGNORECASE,
)
_SEASON_MARKER_INSIDE_BRACKET_RE = re.compile(
    r'^[\[\(]\s*(?:S(?:eason)?\.?\s*\d{1,2}|\d{1,2}(?:st|nd|rd|th)\s*Season'
    r'|Part\s*\d{1,2}|Cour\s*\d{1,2})\s*[\]\)]$',
    re.IGNORECASE,
)
_ANY_BRACKET_RE = re.compile(r'[\[\(][^\[\]\(\)]*[\]\)]')
_QUALITY_UNBRACKETED_RE = re.compile(
    r'[\s_\-–]+(?:\d{3,4}p|HEVC|x26[45]|AVC|AAC|AC3|10[\s-]?bit|'
    r'8[\s-]?bit|WEB[-\s]?DL|WEBRip|BluRay|BD(?:Rip|MV)?|HDTV|DVDRip|'
    r'BRRip|FLAC|DUAL[-\s]?AUDIO|BATCH|Eng[-\s]?Subs?|RAW)\s*$',
    re.IGNORECASE,
)
_SEASON_SUFFIX_RE = re.compile(
    r'[\s_\-–]*\(?\[?(?:'
    r'S(?:eason)?\.?\s*(\d{1,2})'
    r'|(\d{1,2})(?:st|nd|rd|th)\s*Season'
    r'|Part\s*(\d{1,2})'
    r'|Cour\s*(\d{1,2})'
    r')\)?\]?',
    re.IGNORECASE,
)


def _season_suffix_repl(m: "re.Match") -> str:
    num = next(g for g in m.groups() if g)
    return f' S{int(num)}'


def _strip_non_season_brackets(name: str) -> str:
    def _repl(m: "re.Match") -> str:
        return m.group(0) if _SEASON_MARKER_INSIDE_BRACKET_RE.match(m.group(0)) else ''
    prev = None
    while prev != name:
        prev = name
        name = _ANY_BRACKET_RE.sub(_repl, name)
    return name


def clean_show_title(raw: str) -> str:
    """Strip fansub/quality/language tags and normalize season suffixes to
    'S<n>'. Canonical cleaning function — import this instead of writing a
    new one."""
    if not raw:
        return raw
    name = raw
    name = _FANSUB_TAG_RE.sub('', name)
    name = _strip_non_season_brackets(name)
    name = _QUALITY_UNBRACKETED_RE.sub('', name)
    name = _SEASON_SUFFIX_RE.sub(_season_suffix_repl, name)
    name = re.sub(r'\[\s*\]', '', name)
    name = re.sub(r'\(\s*\)', '', name)
    name = re.sub(r'[._]+', ' ', name)
    name = re.sub(r'\s{2,}', ' ', name).strip()
    name = re.sub(r'^[-\u2013\s]+', '', name)
    name = re.sub(r'[\s\-\u2013]+$', '', name)
    return name if name else raw.strip()


def norm_show_title(t: str) -> str:
    """Normalize a title for fuzzy matching — lowercase, collapse all
    non-alphanumerics to single spaces."""
    return re.sub(r'[^a-z0-9]+', ' ', (t or '').lower()).strip()


def fuzzy_title_in(title: str, norm_pool) -> bool:
    """True if `title` (raw) fuzzy-matches any normalized title in norm_pool
    (an iterable/set of already-normalized strings)."""
    nt = norm_show_title(title)
    if not nt:
        return False
    return any(nt == p or nt in p or p in nt for p in norm_pool if p)


def _clean_dedupe_matrix_data(data: dict) -> dict:
    """Clean + fuzzy-dedupe titles across the Continue Watching progress
    dict and all four Watchlist sub-lists, and drop stale Planning entries
    that now match something active in Watching. Idempotent — safe to run
    on every load. This is the authoritative self-healing pass; call it
    from every place that loads Matrix data (great_sage_core.get_matrix_data,
    matrix.py's Storage._load, sage.py's get_matrix_data) so no read/write
    path can leave stale duplicate/uncleaned titles behind.
    """
    changed = False
    wl = data.setdefault("watchlist", {})
    for k in ("planning", "watching", "dropped", "completed"):
        wl.setdefault(k, [])

    # --- Continue Watching progress dict ---
    watching_progress = data.get("watching", {})
    if isinstance(watching_progress, dict) and watching_progress:
        new_progress = {}
        for old_key, entry in watching_progress.items():
            if not isinstance(entry, dict):
                new_progress[old_key] = entry
                continue
            stored_title = entry.get("title", old_key)
            cleaned = clean_show_title(stored_title) or clean_show_title(old_key)
            if cleaned != old_key or cleaned != stored_title:
                changed = True
            if cleaned in new_progress:
                cur = new_progress[cleaned]
                newer = entry if entry.get("last_watched", 0) >= cur.get("last_watched", 0) else cur
                merged = dict(newer)
                merged["title"] = cleaned
                merged["current_episode"] = max(entry.get("current_episode", 0), cur.get("current_episode", 0))
                merged["total_episodes"] = max(entry.get("total_episodes", 0), cur.get("total_episodes", 0))
                seen_eps, combined = set(), []
                for ep in (entry.get("episodes_watched", []) + cur.get("episodes_watched", [])):
                    ek = tuple(ep) if isinstance(ep, (list, tuple)) else ep
                    if ek not in seen_eps:
                        seen_eps.add(ek)
                        combined.append(ep)
                merged["episodes_watched"] = combined
                new_progress[cleaned] = merged
                changed = True
            else:
                fixed = dict(entry)
                fixed["title"] = cleaned
                new_progress[cleaned] = fixed
        if changed:
            data["watching"] = new_progress
        watching_progress = new_progress

    # --- Clean + dedupe each Watchlist sub-list independently ---
    for listname in ("planning", "watching", "dropped", "completed"):
        items = wl.get(listname, [])
        if not items:
            continue
        seen_norms, new_items, list_changed = {}, [], False
        for raw_entry in items:
            entry = dict(raw_entry) if isinstance(raw_entry, dict) else {"title": str(raw_entry)}
            title = entry.get("title", "")
            cleaned = clean_show_title(title) if title else title
            if cleaned != title:
                list_changed = True
            entry["title"] = cleaned
            norm = norm_show_title(cleaned)
            if norm and norm in seen_norms:
                idx = seen_norms[norm]
                cur = new_items[idx]
                cur_ts = cur.get("updated_at") or cur.get("added") or 0
                new_ts = entry.get("updated_at") or entry.get("added") or 0
                merged = dict(entry) if (new_ts and new_ts > cur_ts) else dict(cur)
                merged["title"] = cleaned
                new_items[idx] = merged
                list_changed = True
            else:
                if norm:
                    seen_norms[norm] = len(new_items)
                new_items.append(entry)
        if list_changed:
            wl[listname] = new_items
            changed = True

    # --- Ensure every Continue Watching title is represented in Watching ---
    existing_watching_norms = {
        norm_show_title(e.get("title", "")) for e in wl.get("watching", []) if isinstance(e, dict)
    }
    for title in (watching_progress.keys() if isinstance(watching_progress, dict) else []):
        if title and not fuzzy_title_in(title, existing_watching_norms):
            wl.setdefault("watching", []).append({
                "title": title, "is_anime": False,
                "added": time.time(), "watched": False,
                "notes": "Migrated from Continue Watching",
                "updated_at": _wl_now(),
            })
            existing_watching_norms.add(norm_show_title(title))
            changed = True

    # --- Drop stale Planning entries matching something already active ---
    active_norms = set(existing_watching_norms)
    for k in (watching_progress.keys() if isinstance(watching_progress, dict) else []):
        if k:
            active_norms.add(norm_show_title(k))
    planning = wl.get("planning", [])
    if planning and active_norms:
        kept = [
            e for e in planning
            if not fuzzy_title_in(e.get("title", "") if isinstance(e, dict) else str(e), active_norms)
        ]
        if len(kept) != len(planning):
            wl["planning"] = kept
            changed = True

    return data, changed

# ── Module loader ──────────────────────────────────────────────────────────────
_modules: dict = {}

def _load(modname: str, filename: str):
    if modname in _modules:
        return _modules[modname], None
    path = SCRIPT_DIR / filename
    if not path.exists():
        log.error("Module file not found", mod=modname, path=str(path))
        return None, f"{filename} not found in {SCRIPT_DIR}"
    orig = os.getcwd()
    os.chdir(str(SCRIPT_DIR))
    try:
        spec = importlib.util.spec_from_file_location(modname, str(path))
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _modules[modname] = mod
        log.info("Module loaded", mod=modname)
        return mod, None
    except Exception as e:
        log.exc(f"Failed to load module {modname}", e, path=str(path))
        return None, str(e)
    finally:
        os.chdir(orig)

def legion_mod():  return _load("legion",  "legion.py")
def matrix_mod():  return _load("matrix",  "matrix.py")
def sage_mod():    return _load("sage",     "sage.py")

def reload_module(modname: str):
    """Force reload a cached module."""
    _modules.pop(modname, None)
    if modname in sys.modules:
        del sys.modules[modname]

# ── Catalogue loader ───────────────────────────────────────────────────────────
def _catalogue_panel_class():
    try:
        spec = importlib.util.spec_from_file_location(
            "catalogue", str(SCRIPT_DIR / "catalogue.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.CataloguePanel
    except Exception as e:
        log.warning("catalogue.py load error", error=str(e))
        return None

# ── JSON helpers ───────────────────────────────────────────────────────────────
def load_json(path: str, default=None) -> dict:
    """Load JSON from path. On JSONDecodeError tries the .bak file before giving up."""
    def _try_load(p):
        with open(p) as f:
            return json.load(f)
    try:
        if os.path.exists(path):
            return _try_load(path)
    except json.JSONDecodeError as e:
        log.exc("Failed to load JSON (corrupt — trying backup)", e, path=path)
        bak = path + ".bak"
        try:
            if os.path.exists(bak):
                data = _try_load(bak)
                log.warning("Recovered from backup JSON", path=path, bak=bak)
                try:
                    os.replace(bak, path)
                except OSError:
                    pass
                return data
        except Exception as e2:
            log.exc("Backup JSON also failed", e2, path=bak)
    except Exception as e:
        log.exc("Failed to load JSON", e, path=path)
    return default if default is not None else {}

_json_cache: dict[str, tuple[float, dict]] = {}

def load_json_cached(path: str, default=None) -> dict:
    """Load JSON only if the file has changed since last read."""
    p_str = os.path.realpath(os.path.expanduser(str(path)))
    try:
        mtime = os.path.getmtime(p_str)
        if p_str in _json_cache and _json_cache[p_str][0] == mtime:
            return _json_cache[p_str][1]
        data = load_json(p_str, default if default is not None else {})
        _json_cache[p_str] = (mtime, data)
        return data
    except FileNotFoundError:
        return default if default is not None else {}
    except Exception:
        return load_json(p_str, default if default is not None else {})

def save_json(path: str, data: dict) -> bool:
    """Atomically write data to path as JSON. Thread-safe per path. Invalidates cache."""
    # Normalise fully (expand ~ AND resolve to real path) so all callers share one lock
    # regardless of whether they pass "~/.great_sage_legion.json" or the expanded form.
    abs_path = os.path.realpath(os.path.expanduser(path))
    with _save_locks_lock:
        lock = _save_locks.setdefault(abs_path, threading.Lock())
    with lock:
        tmp = abs_path + ".tmp"
        bak = abs_path + ".bak"
        original_file_exists_at_start = os.path.exists(abs_path)
        backup_created = False

        # Safety guard: refuse to overwrite a non-empty file with a near-empty payload.
        # This catches race conditions where a thread reads the default {} during an
        # atomic rename and then saves it back, wiping real data.
        if original_file_exists_at_start:
            try:
                existing_size = os.path.getsize(abs_path)
                new_size = len(json.dumps(data).encode())
                if existing_size > 500 and new_size < existing_size * 0.10:
                    log.error(
                        "save_json REFUSED: would overwrite non-empty file with near-empty data",
                        path=abs_path, existing_bytes=existing_size, new_bytes=new_size
                    )
                    return False
            except Exception:
                pass

        try:
            log.info(f"Attempting to save JSON to {abs_path}")

            dir_ = os.path.dirname(abs_path)
            os.makedirs(dir_, exist_ok=True)

            # Write to .tmp file
            log.debug(f"Writing data to temporary file: {tmp}")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
                # 3. Use fsync
                f.flush()
                os.fsync(f.fileno())
            log.debug(f"Finished writing to temporary file: {tmp}")

            # 1. Validate JSON before replace
            log.debug(f"Validating JSON in temporary file: {tmp}")
            with open(tmp, "r") as f:
                json.load(f)  # This will raise JSONDecodeError if invalid
            log.info(f"JSON in {tmp} is valid.")

            # 2. Create backup
            if original_file_exists_at_start:
                try:
                    # Check if the existing file is valid JSON before backing up
                    with open(path, "r") as f_orig:
                        json.load(f_orig)  # Check if original is valid JSON
                    log.debug(f"Creating backup of {path} to {bak}")
                    os.replace(path, bak)  # Atomically replace original with backup
                    backup_created = True
                    log.info(f"Backup created: {bak}")
                except json.JSONDecodeError:
                    log.warning(f"Original file {path} is corrupt or not valid JSON; not creating backup.")
                except Exception as e:
                    log.exc(f"Failed to create backup for {path}", e, path=path, bak=bak)
            else:
                log.debug(f"No existing file at {path} to backup.")

            # Replace original with new temp file
            log.info(f"Replacing {path} with content from {tmp}")
            os.replace(tmp, path)
            log.info(f"Replace successful for {path}")

            _json_cache.pop(str(path), None)
            return True  # 6. Return boolean

        except json.JSONDecodeError as e:
            log.exc(f"Failed to save JSON: Invalid JSON detected during validation or original file was corrupt", e, path=path, tmp=tmp)
            # 4. Recovery from backup (only if backup was successfully created)
            if original_file_exists_at_start and backup_created:
                try:
                    log.warning(f"Attempting to restore {path} from backup {bak}")
                    os.replace(bak, path)  # Restore from backup
                    log.info(f"Successfully restored {path} from backup {bak}")
                    _json_cache.pop(str(path), None)  # Invalidate cache for restored file
                except Exception as e_restore:
                    log.exc(f"Failed to restore {path} from backup {bak}", e_restore, path=path, bak=bak)
            return False  # 6. Return boolean
        except Exception as e:
            log.exc(f"Failed to save JSON to {path} due to an unexpected error", e, path=path, tmp=tmp)
            # 4. Recovery from backup (only if backup was successfully created)
            if original_file_exists_at_start and backup_created:
                try:
                    log.warning(f"Attempting to restore {path} from backup {bak}")
                    os.replace(bak, path)  # Restore from backup
                    log.info(f"Successfully restored {path} from backup {bak}")
                    _json_cache.pop(str(path), None)  # Invalidate cache for restored file
                except Exception as e_restore:
                    log.exc(f"Failed to restore {path} from backup {bak}", e_restore, path=path, bak=bak)
            return False  # 6. Return boolean

# ── Data accessors ─────────────────────────────────────────────────────────────
def get_legion_data() -> dict:
    return load_json_cached(LEGION_PROGRESS, {"books": {}})

def _migrate_matrix_data(data: dict, from_version: int) -> dict:
    """Migrates matrix data from an older version to the current version."""
    if from_version < 2:
        log.info("Migrating matrix data", from_version=from_version, to_version=MATRIX_DATA_VERSION)
        # Migration from list to dict for watchlist
        watchlist = data.get("watchlist", [])
        if isinstance(watchlist, list):
            data["watchlist"] = {
                "planning": watchlist,
                "watching": [],
                "dropped": [],
                "completed": []
            }
        # Ensure all sub-lists exist for new data or after migration
        for k in ("planning", "watching", "dropped", "completed"):
            data["watchlist"].setdefault(k, [])
        log.info("Migration complete", fields_added=["watchlist.planning", "_version"])
    return data


def get_matrix_data() -> dict:
    data = load_json_cached(MATRIX_PROGRESS, {
        "watchlist": {"planning": [], "watching": [], "dropped": [], "completed": []},
        "watching": {}, "completed": {}}) # Initial default for new files

    current_version = data.get("_version", 1) # Default to 1 for data without version field

    if current_version < MATRIX_DATA_VERSION:
        log.info("Migrating matrix data", from_version=current_version, to_version=MATRIX_DATA_VERSION)
        data = _migrate_matrix_data(data, current_version)
        data["_version"] = MATRIX_DATA_VERSION
        # Save immediately to disk so migration persists
        save_json(MATRIX_PROGRESS, data)
        # Also update the cache — guard against missing file after save
        try:
            _json_cache[str(MATRIX_PROGRESS)] = (os.path.getmtime(MATRIX_PROGRESS), data)
        except OSError:
            pass
        log.info("Matrix data migration complete", new_version=MATRIX_DATA_VERSION)

    # Ensure watchlist is always a dict, never None or a list
    if not isinstance(data.get("watchlist"), dict):
        data["watchlist"] = {"planning": [], "watching": [], "dropped": [], "completed": []}

    # Ensure all sub-lists exist
    for k in ("planning", "watching", "dropped", "completed"):
        data["watchlist"].setdefault(k, [])

    # Clean + fuzzy-dedupe show titles (fansub/quality tags, stale
    # cross-list duplicates). Self-healing on every load — see
    # _clean_dedupe_matrix_data's docstring for why this matters.
    data, dedupe_changed = _clean_dedupe_matrix_data(data)
    if dedupe_changed:
        save_json(MATRIX_PROGRESS, data)
        try:
            _json_cache[str(MATRIX_PROGRESS)] = (os.path.getmtime(MATRIX_PROGRESS), data)
        except OSError:
            pass

    return data

def get_bookmarks_data() -> dict:
    return load_json_cached(LEGION_BOOKMARKS,
        {"planning": [], "reading": [], "dropped": [], "completed": []})

def legion_data() -> dict:
    return get_legion_data()

def matrix_data() -> dict:
    return get_matrix_data()

def bookmarks_data() -> dict:
    return get_bookmarks_data()

def behaviour_data() -> dict:
    return get_behaviour_data()

# ── Sage persistent memory ─────────────────────────────────────────────────────
_sage_memory_db_instance = None

def _get_memory_db():
    global _sage_memory_db_instance
    if _sage_memory_db_instance is None:
        from sage_memory_db import SageMemoryDB
        _sage_memory_db_instance = SageMemoryDB()
    return _sage_memory_db_instance


def sage_memory_load() -> str:
    try:
        texts = _get_memory_db().dump_all()
        return "\n".join(texts[-20:])
    except Exception as e:
        log.warning("sage_memory_load failed", error=str(e))
    return ""

def sage_memory_append(fact: str):
    try:
        fact = fact.strip()
        if not fact:
            return
        import datetime as _dt
        stamp = _dt.datetime.now().strftime("%Y-%m-%d")
        _get_memory_db().add_memory(f"[{stamp}] {fact}")
    except Exception as e:
        log.warning("sage_memory_append failed", error=str(e))

def sage_memory_extract(response: str, user_msg: str) -> list:
    combined = (user_msg + " " + response).lower()
    facts = []
    for pat in [r"(?:don'?t|hate|dislike|not a fan of|can'?t stand)\s+(.{4,45})(?=[.,!\n]|$)"]:
        for m in re.finditer(pat, combined):
            facts.append("User dislikes: " + m.group(1).strip())
    for pat in [r"(?:love|really like|prefer|enjoy|favourite|favorite)\s+(.{4,45})(?=[.,!\n]|$)"]:
        for m in re.finditer(pat, combined):
            facts.append("User likes: " + m.group(1).strip())
    for pat in [r"i'?m (?:currently|always|usually|really)\s+(.{4,45})(?=[.,!\n]|$)"]:
        for m in re.finditer(pat, combined):
            facts.append("User is " + m.group(1).strip())
    return facts[:3]

# ── Behaviour tracking ─────────────────────────────────────────────────────────
def get_behaviour_data() -> dict:
    return load_json(BEHAVIOUR_LOG, {"sessions": [], "signals": {}})

def track_event(event_type: str, data=None):
    def _write():
        try:
            behaviour_data = get_behaviour_data()
            # Consolidate watch_time events to prevent log flooding
            if event_type == "watch_time" and behaviour_data["sessions"]:
                last = behaviour_data["sessions"][-1]
                # If last event was watch_time and within the same hour, merge it
                if last.get("type") == "watch_time" and (time.time() - last.get("timestamp", 0)) < 300:
                    last["data"]["minutes"] = last["data"].get("minutes", 0) + (data or {}).get("minutes", 0)
                    last["timestamp"] = time.time()
                else:
                    behaviour_data["sessions"].append({
                        "type":      event_type,
                        "data":      data or {},
                        "timestamp": time.time(),
                        "hour":      datetime.now().hour,
                        "weekday":   datetime.now().weekday(),
                    })
            else:
                behaviour_data["sessions"].append({
                    "type":      event_type,
                    "data":      data or {},
                    "timestamp": time.time(),
                    "hour":      datetime.now().hour,
                    "weekday":   datetime.now().weekday(),
                })

            behaviour_data["sessions"] = behaviour_data["sessions"][-10000:]
            signals = behaviour_data.setdefault("signals", {})
            if event_type == "chapter_finished":
                signals["chapters_finished"] = signals.get("chapters_finished", 0) + 1
                genre = (data or {}).get("genre", "")
                if genre:
                    genre_counts = signals.setdefault("genre_counts", {})
                    genre_counts[genre] = genre_counts.get(genre, 0) + 1
            elif event_type == "chapter_abandoned":
                signals["chapters_abandoned"] = signals.get("chapters_abandoned", 0) + 1
            elif event_type == "episode_finished":
                signals["episodes_finished"] = signals.get("episodes_finished", 0) + 1
                genre = (data or {}).get("genre", "")
                if genre:
                    genre_counts = signals.setdefault("genre_counts", {})
                    genre_counts[genre] = genre_counts.get(genre, 0) + 1
            elif event_type == "words_read":
                signals["total_words"] = signals.get("total_words", 0) + (data or {}).get("words", 0)
            elif event_type == "watch_time":
                signals["total_watch_minutes"] = signals.get("total_watch_minutes", 0) + (data or {}).get("minutes", 0)
            save_json(BEHAVIOUR_LOG, behaviour_data)
        except Exception as e:
            log.warning("track_event write failed", event=event_type, error=str(e))
    
    global _event_executor
    if _event_executor is None:
        _event_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="track_event")
    _event_executor.submit(_write)

def behaviour_summary() -> str:
    behaviour_data = get_behaviour_data()
    signals        = behaviour_data.get("signals", {})
    sessions       = behaviour_data.get("sessions", [])
    if not sessions:
        return ""
    parts = []
    fin = signals.get("chapters_finished", 0)
    abd = signals.get("chapters_abandoned", 0)
    if fin + abd > 0:
        parts.append(f"Finishes {int(fin/(fin+abd)*100)}% of chapters started")
    words = signals.get("total_words", 0)
    if words: parts.append(f"Read {words:,} words total")
    mins = signals.get("total_watch_minutes", 0)
    if mins: parts.append(f"Watched {mins//60}h total")
    genre_counts = signals.get("genre_counts", {})
    if genre_counts:
        top = sorted(genre_counts.items(), key=lambda x: -x[1])[:3]
        parts.append("Favourite genres: " + ", ".join(g for g, _ in top))
    hours = [s.get("hour") for s in sessions if s.get("hour") is not None]
    if hours:
        from collections import Counter
        peak   = Counter(hours).most_common(1)[0][0]
        period = "morning" if peak < 12 else "afternoon" if peak < 17 else "evening" if peak < 21 else "night"
        parts.append(f"Usually reads/watches in the {period}")
    return ". ".join(parts) + "." if parts else ""

# ── Stream watch context ───────────────────────────────────────────────────────
def stream_watch_context() -> str:
    matrix_data     = get_matrix_data()
    watching        = matrix_data.get("watching", {})
    watchlist       = matrix_data.get("watchlist", {})
    parts           = []

    stream_entries, regular_entries = [], []
    for k, v in watching.items():
        if not isinstance(v, dict):
            regular_entries.append(str(k)); continue
        title  = v.get("title", k)
        ep     = v.get("current_episode", 0)
        source = v.get("source", "")
        eps_w  = v.get("episodes_watched", [])
        ep_str = f"ep {ep}" if ep else ""
        if eps_w and len(eps_w) > 1:
            ep_str += f" ({len(eps_w)} episodes watched)"
        entry = title + (f" [{ep_str}]" if ep_str else "")
        if source in ("animekai", "animekai_sync"):
            stream_entries.append(entry)
        else:
            regular_entries.append(entry)

    if stream_entries:
        parts.append("Currently watching on AnimeKai: " + "; ".join(stream_entries))
    if regular_entries:
        parts.append("Also watching (local files): " + "; ".join(regular_entries))

    def _titles(lst, limit=40):
        return [e.get("title", "") if isinstance(e, dict) else str(e)
                for e in lst[:limit]
                if (e.get("title", "") if isinstance(e, dict) else str(e))]

    for label, key in [
        ("In watchlist (watching): ", "watching"),
        ("Planning to watch: ",       "planning"),
        ("Completed: ",               "completed"),
        ("Dropped (did not like): ",  "dropped"),
    ]:
        titles = _titles(watchlist.get(key, []))
        if titles:
            parts.append(label + ", ".join(titles))

    return "\n".join(parts) if parts else ""

# ── Media title helpers ────────────────────────────────────────────────────────
def _clean_media_title(raw: str) -> str:
    s    = raw
    junk = re.compile(
        r'[\[\(]?'
        r'(VOSTFR|VOSTA|SUBFRENCH|FRENCH|ENGLISH|MULTI|'
        r'1080p?|720p?|480p?|2160p?|4K|UHD|HDR|SDR|'
        r'BluRay|BDRip|BRRip|WEB(?:-?DL)?|WEBRip|HDTV|DVDRip|'
        r'x264|x265|HEVC|AVC|AVI|MKV|MP4|'
        r'AAC|AC3|DTS|DD5?\.?1|FLAC|TrueHD|'
        r'H\.?264|H\.?265|10bit|8bit|S\d{2}E\d{2}|E\d{2,3})'
        r'[\]\)]?.*$',
        re.IGNORECASE
    )
    s = junk.sub("", s).strip(" .-_")
    s = re.sub(r"\s*-\s*\w+Raws?\s*$", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\*{1,3}", "", s).strip()

    # Strip standalone season/part/cour suffixes, roman-numeral season
    # markers, and trailing release years — these are kept in the
    # canonical display title (e.g. "Karakuri Circus S1") for
    # disambiguation, but must NOT be sent to AniList/TMDB, which index
    # shows under their base title without a season suffix. Applied
    # repeatedly to handle stacked cases (e.g. "Show S2 (2024)" -> "Show").
    _season_tail_re = re.compile(
        r'[\s_\-–]*\(?\[?(?:'
        r'S(?:eason)?\.?\s*\d{1,2}'
        r'|\d{1,2}(?:st|nd|rd|th)\s*Season'
        r'|Part\s*\d{1,2}'
        r'|Cour\s*\d{1,2}'
        r'|\b(?:I{1,3}|IV|V|VI{0,3}|IX)\b'
        r'|\(?(?:19|20)\d{2}\)?'
        r')\)?\]?\s*$',
        re.IGNORECASE,
    )
    prev = None
    while prev != s:
        prev = s
        s = _season_tail_re.sub('', s).strip()

    return s or raw

def _strip_markdown(text: str) -> str:
    text = re.sub(r"[*]{1,3}", "", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"`+", "", text)
    return text.strip()

# ── Genre detection ────────────────────────────────────────────────────────────
def _detect_genre(book_name: str, book_data: dict) -> str:
    tags      = []
    meta      = book_data.get("metadata", {})
    if isinstance(meta, dict):
        tags = [t.lower() for t in meta.get("tags", [])]
    name_lower = book_name.lower()
    tag_str    = " ".join(tags)

    if any(k in name_lower or k in tag_str for k in
           ["cultivat", "xianxia", "wuxia", "immortal", "dao", "qi ", "sect",
            "martial", "sovereign", "emperor", "heaven", "sage", "pill",
            "reincarn", "transmigr"]):
        return "cultivation"
    if any(k in name_lower or k in tag_str for k in
           ["sci-fi", "scifi", "space", "galaxy", "mech", "cyber", "android",
            "robot", "future", "starship", "alien"]):
        return "sci-fi"
    if any(k in name_lower or k in tag_str for k in
           ["romance", "love", "heart", "kiss", "wedding", "bride", "husband",
            "wife", "ceo", "billionaire", "contract marriage"]):
        return "romance"
    if any(k in name_lower or k in tag_str for k in
           ["thriller", "mystery", "detective", "murder", "crime", "killer",
            "assassin", "secret", "conspiracy"]):
        return "thriller"
    if any(k in name_lower or k in tag_str for k in
           ["fantasy", "magic", "dragon", "elf", "dwarf", "wizard", "witch",
            "dungeon", "kingdom", "quest", "sword", "hero"]):
        return "fantasy"
    return "default"

def _detect_show_genre(title: str, is_anime: bool = False) -> str:
    t = title.lower()
    if is_anime:
        if any(k in t for k in ["shonen", "shounen", "battle", "fight", "action"]): return "shonen"
        if any(k in t for k in ["isekai", "reborn", "another world"]): return "isekai"
        if any(k in t for k in ["slice", "life", "school", "comedy"]): return "slice-of-life"
        return "anime"
    
    if any(k in t for k in ["thriller", "mystery", "crime", "detective"]): return "thriller"
    if any(k in t for k in ["horror", "scary", "ghost", "dark"]): return "horror"
    if any(k in t for k in ["comedy", "funny", "sitcom"]): return "comedy"
    if any(k in t for k in ["sci-fi", "science", "space", "future"]): return "sci-fi"
    if any(k in t for k in ["fantasy", "magic", "dragon"]): return "fantasy"
    if any(k in t for k in ["documentary", "history", "real"]): return "documentary"
    return "live-action"

# ── Book file search ───────────────────────────────────────────────────────────
def _grep_book_for_term(book_name: str, term: str, up_to_chapter: int,
                         max_excerpts: int = 60) -> str:
    # Match get_book_path() in legion.py: library/{safe_name}/{safe_name}.txt
    safe = re.sub(r'[^\w\-_\. ]', '_', book_name)
    library_path = str(SCRIPT_DIR / "library" / safe / f"{safe}.txt")
    # Legacy fallback candidates for any files stored under old layout
    candidates = [
        library_path,
        str(SCRIPT_DIR / f"{book_name}.txt"),
        str(SCRIPT_DIR / f"{safe}.txt"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
        log.warning("_grep_book_for_term: no book file found", book=book_name, tried=candidates)
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        log.warning("_grep_book_for_term file read failed", path=path, error=str(e))
        return ""

    blocks     = re.split(r"={50,}", content)
    blocks     = [b.strip() for b in blocks if b.strip()]
    excerpts   = []
    total_chars = 0
    term_lower  = term.lower()

    for block in blocks:
        hm = re.search(r"chapter\s+(\d+)", block[:150], re.IGNORECASE)
        if hm and int(hm.group(1)) > up_to_chapter:
            break
        for para in [p.strip() for p in block.split("\n\n") if p.strip()]:
            if term_lower in para.lower() and len(para) > 40:
                excerpts.append(para)
                total_chars += len(para)
                if len(excerpts) >= max_excerpts or total_chars >= 12000:
                    break
        if len(excerpts) >= max_excerpts or total_chars >= 12000:
            break

    return "\n\n---\n\n".join(excerpts)

# ── Workers ────────────────────────────────────────────────────────────────────

class FetchChapterWorker(QThread):
    done   = pyqtSignal(str, list, str, str, int)
    error  = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, url: str, book_name: str = ""):
        super().__init__()
        self.url = url
        self.book_name = book_name

    def run(self):
        self.status.emit(f"Loading {self.url[:70]}...")
        log.legion.debug("Fetching chapter", url=self.url)
        mod, err = legion_mod()
        if err:
            log.legion.error("legion.py unavailable for fetch", error=err)
            self.error.emit(f"Cannot load legion.py: {err}")
            return
        try:
            # Note: SIGALRM cannot be used from worker threads (Python 3.12+).
            # requests already enforces its own timeout per call, so no wrapper needed.
            title, paragraphs, next_url, prev_url, error, url_ch_num = mod.fetch_chapter(self.url, self.book_name)
            if error:
                log.legion.warning("Chapter fetch returned error", url=self.url, error=error)
                self.error.emit(error)
                return
            if not paragraphs:
                log.legion.warning("Chapter fetch returned no content", url=self.url)
                self.error.emit("No content found at this URL.")
                return
            log.legion.info("Chapter fetched", url=self.url, title=title, paragraphs=len(paragraphs))
            self.done.emit(title or "Chapter", paragraphs, next_url or "", prev_url or "", url_ch_num or 0)
        except Exception as e:
            log.legion.exc("Chapter fetch exception", e, url=self.url)
            self.error.emit(str(e))


class SageWorker(QThread):
    chunk_ready = pyqtSignal(str)
    finished    = pyqtSignal()
    done        = pyqtSignal(str)
    error       = pyqtSignal(str)

    def __init__(self, mode: str, user_msg: str = "", extra: str = "", history=None):
        super().__init__()
        self.mode     = mode
        self.user_msg = user_msg
        self.extra    = extra
        self.history  = history or []
        self._stop    = False

    def run(self):
        mod, err = sage_mod()
        if err:
            log.sage.error("sage.py unavailable", error=err)
            self.error.emit(f"Cannot load sage.py: {err}")
            return
        
        # Use get_matrix_data directly to avoid shadowing issues with local matrix_data variable
        settings = get_matrix_data().get("settings", {})
        if settings.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
            mod.GROQ_API_KEY = settings["groq_api_key"]
        if settings.get("groq_model") and hasattr(mod, "GROQ_MODEL"):
            mod.GROQ_MODEL = settings["groq_model"]

        log.sage.info("Sage request started", mode=self.mode)
        try:
            profile      = mod.build_profile()
            profile_text = mod.profile_to_text(profile)
            memory       = mod.load_memory() if hasattr(mod, "load_memory") else {}
            mem_ctx      = mod.memory_to_context(memory) if hasattr(mod, "memory_to_context") else ""
            
            relevant_memories = []
            try:
                if self.user_msg:
                    relevant_memories = _get_memory_db().search(self.user_msg, k=5)
            except Exception as e:
                log.sage.warning("Memory search failed", error=str(e))

            if relevant_memories:
                pers_mem = "\n".join(relevant_memories)
                mem_ctx = (mem_ctx + "\n\n[Relevant Past Memories]\n" + pers_mem).strip()
            else:
                try:
                    pers_mem = sage_memory_load()
                    if pers_mem:
                        mem_ctx = (mem_ctx + "\n\n[Persistent user memory]\n" + pers_mem).strip()
                except Exception:
                    pass
            bsummary = behaviour_summary()
            if bsummary:
                mem_ctx = (mem_ctx + "\n\nLearned behaviour signals: " + bsummary).strip()
            seen   = mod.load_seen_recs() if hasattr(mod, "load_seen_recs") else []
            # Also exclude titles already in Legion (reading) and Matrix watchlist/bookmarks
            # so Sage never recommends something the user is actively reading or tracking.
            try:
                from sage import all_listed_titles
                listed_titles = {t.title() for t in all_listed_titles()}
            except Exception:
                listed_titles = set()
            # Add currently-reading Legion books directly from profile
            for n in profile.get("novels", []):
                listed_titles.add(n["title"].title())
            combined_seen = list(dict.fromkeys(seen + list(listed_titles)))
            # Build a hard exclusion block — goes into the system prompt, not user turn
            seen_block = ""
            if combined_seen:
                seen_list = "\n".join(f"- {t}" for t in combined_seen[-80:])
                seen_block = (
                    "\n\n[HARD EXCLUSION LIST — NEVER recommend any of these titles under any circumstances. "
                    "Even if they seem like a perfect fit, skip them entirely and suggest something else:]\n"
                    + seen_list
                )

            prompts = {
                "novels":     "Recommend 6 web novels or light novels I haven't read yet.",
                "shows":      "Recommend 6 TV shows or anime I haven't watched.",
                "similar":    "Find the title I'm most invested in and suggest 5 very similar ones.",
                "mood_light": "Suggest 5 light, fun, easy-going picks (any medium).",
                "mood_heavy": "Suggest 5 intense, gripping, deep picks (any medium).",
                "whats_next": "What single thing should I watch or read right now? Short reason.",
                "quick":      "Give me exactly ONE recommendation with a two-sentence pitch.",
                "explain":    f"Would I enjoy '{self.extra}'? Be honest and specific. Under 250 words.",
                "priority":   "__PRIORITY__",
                "profile":    "Summarise my media taste profile in 3-4 paragraphs.",
                "chapter_summary": "__CHAPTER_SUMMARY__",
                "chat":       self.user_msg,
            }
            user_msg = prompts.get(self.mode, self.user_msg)

            if self.mode == "priority":
                matrix_data = get_matrix_data()
                watchlist = matrix_data.get("watchlist", {})
                if isinstance(watchlist, list):
                    watchlist = {"planning": watchlist, "watching": [], "dropped": [], "completed": []}
                all_unwatched = []
                for sub in ("planning", "watching"):
                    for e in watchlist.get(sub, []):
                        t = e.get("title", "") if isinstance(e, dict) else str(e)
                        if t and t not in all_unwatched:
                            all_unwatched.append(t)
                if all_unwatched:
                    items_text = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(all_unwatched))
                    user_msg = (
                        f"Here is my watchlist of things I haven't finished yet:\n\n{items_text}\n\n"
                        "Rank these from the one I should watch first to the one I should watch last. "
                        "For each, give the title and one sentence on why it's ranked where it is."
                    )
                else:
                    user_msg = "My watchlist appears to be empty. Suggest what I should add based on my profile."

            if self.mode == "chapter_summary":
                book_name    = self.extra
                legion_data  = get_legion_data()
                book_entry   = legion_data.get("books", {}).get(book_name, {})
                # Chapter progress is stored as reader_url (e.g. "local-disk://chapter/2087/..."
                # or a live web URL like ".../chapter-2087"), not a "current_chapter" field.
                cur_ch = 0
                reader_url = book_entry.get("reader_url", "")
                if reader_url:
                    m = re.match(r"local-disk://chapter/(\d+)/", reader_url)
                    if not m:
                        m = re.search(r"chapter[-/](\d+)", reader_url, re.IGNORECASE)
                    if m:
                        cur_ch = int(m.group(1))
                chapter_text = None
                try:
                    from sage import read_chapters_around, read_last_n_chapters
                    if cur_ch:
                        chapter_text = read_chapters_around(book_name, cur_ch, n=5)
                    if not chapter_text:
                        chapter_text = read_last_n_chapters(book_name, n=5)
                except Exception:
                    pass
                if chapter_text:
                    user_msg = (
                        f"The user is reading '{book_name}' and is currently on chapter {cur_ch}. "
                        f"Summarise the following chapters to catch them up on the story so far. "
                        f"Cover key events, character developments, and important revelations. "
                        f"Be specific — mention character names, locations, and plot points.\n\n"
                        f"CHAPTERS:\n{chapter_text[:24000]}"
                    )
                else:
                    user_msg = (
                        f"The user is reading '{book_name}' (currently on chapter {cur_ch}). "
                        f"Summarise the recent story developments to catch them up. "
                        f"Note: local chapter files were not found, so use general knowledge of this novel."
                    )

            stream_ctx  = stream_watch_context()
            enriched    = profile_text
            if stream_ctx:
                enriched = f"{profile_text}\n\n[Live streaming history]\n{stream_ctx}"
            full_prompt = f"{enriched}\n\nUser request: {user_msg}"

            # Append the hard exclusion list to the system prompt (mem_ctx), not the user turn
            system_msg = mem_ctx if mem_ctx else ""
            if seen_block:
                system_msg = (system_msg + seen_block).strip()
            
            # Streaming implementation
            full_resp = ""
            if self.mode == "chat" and hasattr(mod, "chat_with_sage"):
                # Multi-turn chat — use history-aware path
                full_resp, error = mod.chat_with_sage(profile_text, self.user_msg, self.history)
                if error:
                    self.error.emit(error)
                    return
                self.chunk_ready.emit(full_resp)
            elif hasattr(mod, "groq_stream_chat"):
                for chunk, error in mod.groq_stream_chat(full_prompt, system=system_msg if system_msg else None):
                    if self._stop:
                        return
                    if error:
                        self.error.emit(error)
                        return
                    if chunk:
                        full_resp += chunk
                        self.chunk_ready.emit(chunk)
            else:
                # Fallback to non-streaming if mod doesn't have groq_stream_chat
                full_resp, error = mod.groq_chat(full_prompt, system=system_msg if system_msg else None)
                if error:
                    self.error.emit(error)
                    return
                self.chunk_ready.emit(full_resp)

            if self.mode in ("novels", "shows", "similar", "mood_light", "mood_heavy", "quick", "whats_next"):
                try:
                    titles = []
                    # Pattern 1: numbered list with dash/em-dash/colon separator
                    titles += re.findall(
                        r'^\s*\d+[.)]\s+\*{0,2}([^*\n]{3,80}?)\*{0,2}\s*(?:\s[-\u2014:]\s|$)',
                        full_resp, re.MULTILINE
                    )
                    # Pattern 2: numbered list, title alone on its own line
                    titles += re.findall(
                        r'^\s*\d+[.)]\s+\*{0,2}([^*\n]{3,80}?)\*{0,2}\s*$',
                        full_resp, re.MULTILINE
                    )
                    # Pattern 3: bold title on a numbered line
                    titles += re.findall(
                        r'^\s*\d+[.)]\s+\*\*([^*\n]{3,80}?)\*\*',
                        full_resp, re.MULTILINE
                    )
                    # Deduplicate preserving order, strip markdown artifacts
                    seen_set = set()
                    clean_titles = []
                    for t in titles:
                        t = t.strip().strip('*').strip(' :')
                        if t and t.lower() not in seen_set:
                            seen_set.add(t.lower())
                            clean_titles.append(t)
                    if clean_titles and hasattr(mod, "add_seen_recs"):
                        mod.add_seen_recs(clean_titles[:8])
                except Exception as e:
                    log.sage.warning("Failed to update seen recs", error=str(e))

            if hasattr(mod, "trigger_memory_update"):
                mod.trigger_memory_update(self.mode, user_msg[:500], (full_resp or "")[:800])

            if self.mode == "chat":
                for fact in sage_memory_extract(full_resp or "", self.user_msg):
                    sage_memory_append(fact)

            log.sage.info("Sage request complete", mode=self.mode, response_len=len(full_resp or ""))
            self.done.emit(full_resp or "")
            self.finished.emit()

        except Exception as e:
            log.sage.exc("SageWorker unhandled exception", e, mode=self.mode)
            self.error.emit(str(e))



class MetadataWorker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, title: str, is_anime: bool = False):
        super().__init__()
        self.title    = title
        self.clean    = _clean_media_title(title)
        self.is_anime = is_anime

    def run(self):
        import requests as _req
        log.matrix.debug("Fetching metadata", title=self.clean, is_anime=self.is_anime)
        mod, err = matrix_mod()
        if not err:
            try:
                info = mod.MetadataFetcher.fetch_movie_info(self.clean, self.is_anime)
                if info:
                    if info.get("synopsis"):
                        info["synopsis"] = _strip_markdown(info["synopsis"])
                    log.matrix.info("Metadata fetched", title=self.clean, source=info.get("source", "?"))
                    self.done.emit(info)
                    return
            except Exception as e:
                log.matrix.exc("MetadataFetcher failed, falling back to TMDB", e, title=self.clean)
        try:
            r   = _req.get("https://api.themoviedb.org/3/search/multi",
                params={"api_key": "a58e553cfec69c54b7fd360041870216", "query": self.clean}, timeout=60)
            res = r.json().get("results", [])
            if res:
                x  = res[0]
                ov = _strip_markdown(x.get("overview", ""))
                poster_path = x.get("poster_path", "")
                img_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ""
                log.matrix.info("Metadata fetched via TMDB fallback", title=self.clean)
                self.done.emit({
                    "title":   x.get("title") or x.get("name", self.clean),
                    "year":    (x.get("release_date") or x.get("first_air_date", ""))[:4],
                    "synopsis": ov,
                    "score":   round(x.get("vote_average", 0), 1),
                    "source":  "TMDB",
                    "image_url": img_url,
                })
            else:
                log.matrix.warning("No metadata found", title=self.clean)
                self.done.emit({})
        except Exception as e:
            log.matrix.exc("TMDB fallback failed", e, title=self.clean)
            self.error.emit(str(e))


class _SageCompanionWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, question: str, book: str,
                 current_chapter: int = 0, web_search: bool = False):
        super().__init__()
        self.question        = question
        self.book            = book
        self.current_chapter = current_chapter
        self.web_search      = web_search

    def run(self):
        mod, err = sage_mod()
        if not mod or not hasattr(mod, "groq_chat"):
            self.done.emit(f"Sage unavailable: {err or 'sage.py not loaded'}")
            return

        # Apply API key / model overrides from settings
        _s = matrix_data().get("settings", {})
        if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
            mod.GROQ_API_KEY = _s["groq_api_key"]
        active_model = get_session_groq_model() or _s.get("groq_model")
        if active_model and hasattr(mod, "GROQ_MODEL"):
            mod.GROQ_MODEL = active_model

        q = self.question.strip()

        # ── WEB SEARCH MODE ──────────────────────────────────────────────────
        # When the user toggled the "Ask" (web) template in the reader sidebar,
        # we do a Tavily search and inject the results into the Groq prompt.
        # This path does NOT look at local chapter excerpts.
        if self.web_search:
            search_context = ""
            if hasattr(mod, "tavily_search"):
                try:
                    search_context = mod.tavily_search(q)
                except Exception:
                    pass

            if search_context:
                prompt = (
                    f"You are a knowledgeable assistant helping a reader.\n"
                    f"The reader is currently reading \'{self.book}\' "
                    f"(chapter {self.current_chapter}).\n\n"
                    f"[Live web search results for: \"{q}\"]\n"
                    f"{search_context}\n\n"
                    f"---\n"
                    f"Using the search results above, answer the reader's question "
                    f"clearly and concisely. Cite sources where relevant.\n\n"
                    f"Question: {q}"
                )
            else:
                # No Tavily key or search failed — fall back to Groq knowledge
                prompt = (
                    f"You are a knowledgeable assistant. "
                    f"Answer the following question as accurately as possible. "
                    f"If the answer requires very recent information you may not have, "
                    f"say so clearly.\n\nQuestion: {q}"
                )
            try:
                resp, error = mod.groq_chat(prompt)
                self.done.emit(resp if not error else f"Error: {error}")
            except Exception as e:
                self.done.emit(f"Error: {e}")
            return

        # ── BOOK COMPANION MODE ───────────────────────────────────────────────
        # Normal path: look up terms in local chapter excerpts first,
        # then fall back to Groq general knowledge.
        lookup_match = re.match(
            r"^(?:who\s+is|what\s+is|tell\s+me\s+about|describe|explain)\s+(.+)$",
            q, re.IGNORECASE)

        if lookup_match and self.current_chapter > 0:
            term     = lookup_match.group(1).strip().rstrip("?.")
            excerpts = _grep_book_for_term(self.book, term, self.current_chapter)
            if excerpts:
                is_who = re.match(r"who\s+is", q, re.IGNORECASE)
                if is_who:
                    prompt = (
                        f"You are a reading companion for \'{self.book}\'.\n"
                        f"The reader is on chapter {self.current_chapter} and wants to know about \'{term}\'.\n"
                        f"Using ONLY the excerpts below (no outside knowledge), write a detailed character dossier.\n\n"
                        f"EXCERPTS:\n{excerpts}"
                    )
                else:
                    prompt = (
                        f"You are a reading companion for \'{self.book}\'.\n"
                        f"The reader is on chapter {self.current_chapter} and wants to know about \'{term}\'.\n"
                        f"Using ONLY the excerpts below, write a detailed entry.\n\n"
                        f"EXCERPTS:\n{excerpts}"
                    )
            else:
                prompt = (
                    f"You are a reading companion for \'{self.book}\' (chapter {self.current_chapter}).\n"
                    f"The reader asks: \'{q}\'\n"
                    f"No local chapter data was found. Answer based on general knowledge if available."
                )
        else:
            prompt = (
                f"The user is reading \'{self.book}\' (chapter {self.current_chapter}). "
                f"Quick reading companion question: \'{q}\'\n\n"
                f"Answer clearly and concisely — under 100 words."
            )

        try:
            resp, error = mod.groq_chat(prompt)
            self.done.emit(resp if not error else f"Error: {error}")
        except Exception as e:
            self.done.emit(f"Error: {e}")


class _NewChaptersWorker(QThread):
    done = pyqtSignal(int, str)

    def __init__(self, book: dict):
        super().__init__()
        self.book = book

    def run(self):
        mod, err = legion_mod()
        if not mod:
            self.done.emit(0, err or "legion.py not loaded")
            return
        if not hasattr(mod, "check_for_new_chapters"):
            self.done.emit(0, "check_for_new_chapters not found")
            return
        try:
            count = mod.check_for_new_chapters(self.book) or 0
            self.done.emit(count, "")
        except Exception as e:
            self.done.emit(0, str(e)[:100])


class _MetaRefreshWorker(QThread):
    done = pyqtSignal(dict, str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        mod, err = legion_mod()
        if not mod:
            self.done.emit({}, f"legion.py error: {err}")
            return
        if not hasattr(mod, "fetch_book_metadata"):
            self.done.emit({}, "fetch_book_metadata not found")
            return
        try:
            meta = mod.fetch_book_metadata(self.url) or {}
            if not meta:
                self.done.emit({}, "No metadata found for this URL")
                return
            self.done.emit(meta, "")
        except Exception as e:
            self.done.emit({}, str(e)[:120])


# ── Notification Store ────────────────────────────────────────────────────────
# Moved to gs_notifications.py — re-exported here so all existing callers
# (gs_widgets, great_sage_gui, gs_settings_ui) continue to work unchanged.
from gs_notifications import NotificationStore, get_notification_store  # noqa: F401


class AutoSyncWorker(QThread):
    status_update = pyqtSignal(str)
    sync_done     = pyqtSignal(str)
    sync_clear    = pyqtSignal()

    def run(self):
        mod, err = legion_mod()
        if not mod:
            log.sync.warning("Auto-sync skipped — legion.py unavailable", error=err)
            return

        legion_data = get_legion_data()
        books = legion_data.get("books", {})
        log.sync.info("Auto-sync started", total_books=len(books))

        # ── Fix 1: Disk reconciliation ────────────────────────────────────────
        # Ground truth is the filesystem. Correct total_chapters_downloaded in
        # the JSON before any sync decisions are made so stale/corrupt metadata
        # can't cause books to be skipped or double-downloaded.
        reconciled = False
        if hasattr(mod, "_get_chapter_list_from_file"):
            for name, book in legion_data.get("books", {}).items():
                dl_state = book.get("download_state", {})
                json_count = dl_state.get("total_chapters_downloaded", 0)
                try:
                    disk_chapters = mod._get_chapter_list_from_file(name)
                    disk_count = len(disk_chapters)
                except Exception:
                    disk_count = json_count  # can't read disk — leave as-is
                if disk_count != json_count:
                    log.sync.warning(
                        "Reconciling chapter count — JSON/disk mismatch",
                        book=name, json_count=json_count, disk_count=disk_count,
                    )
                    legion_data["books"][name].setdefault("download_state", {})
                    legion_data["books"][name]["download_state"]["total_chapters_downloaded"] = disk_count
                    # If disk has chapters but status is idle/completed with 0 recorded,
                    # correct the status so the book re-enters the active sync path.
                    if disk_count > 0 and dl_state.get("status") in ("idle", "completed") and json_count == 0:
                        legion_data["books"][name]["download_state"]["status"] = "completed"
                    reconciled = True
            if reconciled:
                save_json(LEGION_PROGRESS, legion_data)
                legion_data = get_legion_data()
                books = legion_data.get("books", {})
        # ─────────────────────────────────────────────────────────────────────

        # ── Fix 1b: Library validation ───────────────────────────────────────
        # Scan all book files for corrupt content (nav-menu garbage, bot-challenge
        # pages, near-empty files). Deletes corrupt files and resets download_state
        # so the fresh-filter below re-queues a clean download automatically.
        try:
            import importlib.util as _ilu, os as _os
            _val_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "library_validator.py")
            _spec = _ilu.spec_from_file_location("library_validator", _val_path)
            _val_mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_val_mod)
            _lib_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "library")
            legion_data, _cleaned = _val_mod.validate_library(legion_data, _lib_dir, log=log.sync)
            if _cleaned:
                log.sync.warning("Library validator cleaned corrupt books", books=_cleaned)
                save_json(LEGION_PROGRESS, legion_data)
                legion_data = get_legion_data()
                books = legion_data.get("books", {})
        except Exception as _ve:
            log.sync.error("Library validator failed", error=str(_ve))
        # ─────────────────────────────────────────────────────────────────────

        # ── Note: download queueing removed ──────────────────────────────────
        # Downloads for Jump In books are now handled exclusively by
        # ChapterDownloadWorker / _DownloadRegistry in gs_legion_ui.py.
        # AutoSyncWorker's role is reconciliation + validation only.
        self.sync_clear.emit()


# ── Mobile server ──────────────────────────────────────────────────────────────
_mobile_server_thread = None
_mobile_server_port   = 7331

def start_mobile_server():
    global _mobile_server_thread
    if _mobile_server_thread and _mobile_server_thread.is_alive():
        return _mobile_server_port
    try:
        from flask import Flask, jsonify, request, render_template_string
        import logging as _logging
        app = Flask("great_sage_mobile")
        _logging.getLogger("werkzeug").setLevel(_logging.ERROR)

        @app.route("/")
        def index():
            return "<h1>Great Sage Mobile</h1><p>API running.</p>"

        @app.route("/api/watching")
        def api_watching():
            matrix_data = get_matrix_data()
            items = []
            for key, info in matrix_data.get("watching", {}).items():
                if not isinstance(info, dict):
                    continue
                items.append({
                    "title":  info.get("title", key),
                    "season": info.get("current_season", 1),
                    "ep":     info.get("current_episode", 0),
                    "pos":    info.get("position", 0),
                    "dur":    info.get("duration", 0),
                    "fidx":   info.get("file_index", 0),
                    "tot":    info.get("total_episodes", 0),
                })
            return jsonify({"items": items})

        @app.route("/api/reading")
        def api_reading():
            legion_data = get_legion_data()
            items = []
            for name, book in legion_data.get("books", {}).items():
                if book.get("chapters_read", 0) == 0:
                    continue
                items.append({
                    "title":      name,
                    "ch_read":    book.get("chapters_read", 0),
                    "current_ch": book.get("current_chapter", 0),
                    "words":      book.get("words_read", 0),
                })
            return jsonify({"items": items})

        @app.route("/api/watchlist")
        def api_watchlist():
            matrix_data = get_matrix_data()
            watchlist = matrix_data.get("watchlist", {})
            result = {}
            for lst, entries in watchlist.items():
                result[lst] = [
                    {"title": e.get("title", "?") if isinstance(e, dict) else str(e)}
                    for e in entries
                ]
            return jsonify(result)

        @app.route("/api/remove_watching", methods=["POST"])
        def api_remove_watching():
            data  = request.json or {}
            title = data.get("title", "")
            matrix_data = get_matrix_data()
            if title in matrix_data.get("watching", {}):
                del matrix_data["watching"][title]
                save_json(MATRIX_PROGRESS, matrix_data)
            return jsonify({"ok": True})

        @app.route("/api/move_watchlist", methods=["POST"])
        def api_move_watchlist():
            data      = request.json or {}
            title     = data.get("title", "")
            from_list = data.get("from_list", "")
            to_list   = data.get("to_list", "")
            matrix_data = get_matrix_data()
            watchlist = matrix_data.setdefault("watchlist", {})
            entry     = None
            for e in watchlist.get(from_list, []):
                t = e.get("title", "") if isinstance(e, dict) else str(e)
                if t == title:
                    entry = e
                    break
            if entry:
                watchlist[from_list] = [e for e in watchlist[from_list]
                    if (e.get("title", "") if isinstance(e, dict) else str(e)) != title]
                watchlist.setdefault(to_list, []).append(entry)
                save_json(MATRIX_PROGRESS, matrix_data)
            return jsonify({"ok": True})

        @app.route("/api/update_reading", methods=["POST"])
        def api_update_reading():
            data  = request.json or {}
            title = data.get("title", "")
            ch    = data.get("current_chapter", 0)
            legion_data = get_legion_data()
            book  = legion_data.get("books", {}).get(title)
            if book and ch > 0:
                book["current_chapter"] = ch
                save_json(LEGION_PROGRESS, legion_data)
            return jsonify({"ok": True})

        def _run():
            app.run(host="127.0.0.1", port=_mobile_server_port, debug=False, use_reloader=False)

        _mobile_server_thread = threading.Thread(target=_run, daemon=True)
        _mobile_server_thread.start()
        return _mobile_server_port
    except Exception as e:
        log.warning("Mobile server failed to start", error=str(e))
        return None
