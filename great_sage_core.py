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
import sys
import threading
import time
from pathlib import Path

# Per-path locks — prevents concurrent track_event threads racing on the same .tmp file
_save_locks: dict = {}
_save_locks_lock = threading.Lock()

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
BEHAVIOUR_LOG    = os.path.expanduser("~/.great_sage_behaviour.json")

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
    return default or {}

def save_json(path: str, data: dict):
    """Atomically write data to path as JSON. Thread-safe per path."""
    with _save_locks_lock:
        lock = _save_locks.setdefault(path, threading.Lock())
    with lock:
        try:
            dir_ = os.path.dirname(path) or "."
            os.makedirs(dir_, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            log.exc("Failed to save JSON", e, path=path)

# ── Data accessors ─────────────────────────────────────────────────────────────
def legion_data() -> dict:
    return load_json(LEGION_PROGRESS, {"books": {}})

def matrix_data() -> dict:
    d = load_json(MATRIX_PROGRESS, {
        "watchlist": {"planning": [], "watching": [], "dropped": [], "completed": []},
        "watching": {}, "completed": {}})
    wl = d.get("watchlist", {})
    if isinstance(wl, list):
        d["watchlist"] = {"planning": wl, "watching": [], "dropped": [], "completed": []}
    for k in ("planning", "watching", "dropped", "completed"):
        d["watchlist"].setdefault(k, [])
    return d

def bookmarks_data() -> dict:
    return load_json(LEGION_BOOKMARKS,
        {"planning": [], "reading": [], "dropped": [], "completed": []})

# ── Sage persistent memory ─────────────────────────────────────────────────────
from sage_memory_db import SageMemoryDB
_sage_memory_db = SageMemoryDB()

def sage_memory_load() -> str:
    try:
        texts = _sage_memory_db.dump_all()
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
        _sage_memory_db.add_memory(f"[{stamp}] {fact}")
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
def behaviour_data() -> dict:
    return load_json(BEHAVIOUR_LOG, {"sessions": [], "signals": {}})

def track_event(event_type: str, data=None):
    def _write():
        try:
            bd = behaviour_data()
            bd["sessions"].append({
                "type":      event_type,
                "data":      data or {},
                "timestamp": time.time(),
                "hour":      __import__("datetime").datetime.now().hour,
                "weekday":   __import__("datetime").datetime.now().weekday(),
            })
            bd["sessions"] = bd["sessions"][-500:]
            sigs = bd.setdefault("signals", {})
            if event_type == "chapter_finished":
                sigs["chapters_finished"] = sigs.get("chapters_finished", 0) + 1
            elif event_type == "chapter_abandoned":
                sigs["chapters_abandoned"] = sigs.get("chapters_abandoned", 0) + 1
            elif event_type == "episode_finished":
                sigs["episodes_finished"] = sigs.get("episodes_finished", 0) + 1
                genre = (data or {}).get("genre", "")
                if genre:
                    gc = sigs.setdefault("genre_counts", {})
                    gc[genre] = gc.get(genre, 0) + 1
            elif event_type == "words_read":
                sigs["total_words"] = sigs.get("total_words", 0) + (data or {}).get("words", 0)
            elif event_type == "watch_time":
                sigs["total_watch_minutes"] = sigs.get("total_watch_minutes", 0) + (data or {}).get("minutes", 0)
            save_json(BEHAVIOUR_LOG, bd)
        except Exception as e:
            log.warning("track_event write failed", event=event_type, error=str(e))
    threading.Thread(target=_write, daemon=True).start()

def behaviour_summary() -> str:
    bd   = behaviour_data()
    sigs = bd.get("signals", {})
    sessions = bd.get("sessions", [])
    if not sessions:
        return ""
    parts = []
    fin = sigs.get("chapters_finished", 0)
    abd = sigs.get("chapters_abandoned", 0)
    if fin + abd > 0:
        parts.append(f"Finishes {int(fin/(fin+abd)*100)}% of chapters started")
    words = sigs.get("total_words", 0)
    if words: parts.append(f"Read {words:,} words total")
    mins = sigs.get("total_watch_minutes", 0)
    if mins: parts.append(f"Watched {mins//60}h total")
    gc = sigs.get("genre_counts", {})
    if gc:
        top = sorted(gc.items(), key=lambda x: -x[1])[:3]
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
    md        = matrix_data()
    watching  = md.get("watching", {})
    watchlist = md.get("watchlist", {})
    parts     = []

    stream_entries, regular_entries = [], []
    for k, v in watching.items():
        if not isinstance(v, dict):
            regular_entries.append(str(k)); continue
        title  = v.get("title", k)
        ep     = v.get("current_episode", 0)
        src_   = v.get("source", "")
        eps_w  = v.get("episodes_watched", [])
        ep_str = f"ep {ep}" if ep else ""
        if eps_w and len(eps_w) > 1:
            ep_str += f" ({len(eps_w)} episodes watched)"
        entry = title + (f" [{ep_str}]" if ep_str else "")
        if src_ in ("animekai", "animekai_sync"):
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

# ── Book file search ───────────────────────────────────────────────────────────
def _grep_book_for_term(book_name: str, term: str, up_to_chapter: int,
                         max_excerpts: int = 60) -> str:
    candidates = [
        str(SCRIPT_DIR / f"{book_name}.txt"),
        str(SCRIPT_DIR / f"{book_name.replace(' ', '_')}.txt"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    if not path:
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

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        self.status.emit(f"Loading {self.url[:70]}...")
        log.legion.debug("Fetching chapter", url=self.url)
        mod, err = legion_mod()
        if err:
            log.legion.error("legion.py unavailable for fetch", error=err)
            self.error.emit(f"Cannot load legion.py: {err}")
            return
        try:
            title, paragraphs, next_url, prev_url, error, url_ch_num = mod.fetch_chapter(self.url)
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
    done  = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, mode: str, user_msg: str = "", extra: str = "", history=None):
        super().__init__()
        self.mode     = mode
        self.user_msg = user_msg
        self.extra    = extra
        self.history  = history or []

    def run(self):
        mod, err = sage_mod()
        if err:
            log.sage.error("sage.py unavailable", error=err)
            self.error.emit(f"Cannot load sage.py: {err}")
            return
        _s = matrix_data().get("settings", {})
        if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
            mod.GROQ_API_KEY = _s["groq_api_key"]
        if _s.get("groq_model") and hasattr(mod, "GROQ_MODEL"):
            mod.GROQ_MODEL = _s["groq_model"]

        log.sage.info("Sage request started", mode=self.mode)
        try:
            profile      = mod.build_profile()
            profile_text = mod.profile_to_text(profile)
            memory       = mod.load_memory() if hasattr(mod, "load_memory") else {}
            mem_ctx      = mod.memory_to_context(memory) if hasattr(mod, "memory_to_context") else ""
            
            # Use semantic vector search for precision
            relevant_memories = _sage_memory_db.search(self.user_msg, k=5)
            if relevant_memories:
                pers_mem = "\n".join(relevant_memories)
                mem_ctx = (mem_ctx + "\n\n[Relevant Past Memories]\n" + pers_mem).strip()
            else:
                pers_mem = sage_memory_load()
                if pers_mem:
                    mem_ctx = (mem_ctx + "\n\n[Persistent user memory]\n" + pers_mem).strip()
            bsummary = behaviour_summary()
            if bsummary:
                mem_ctx = (mem_ctx + "\n\nLearned behaviour signals: " + bsummary).strip()
            seen   = mod.load_seen_recs() if hasattr(mod, "load_seen_recs") else []
            seen_s = ("Avoid recommending these (already seen): " +
                      ", ".join(seen[-30:])) if seen else ""

            if self.mode == "chat":
                if hasattr(mod, "chat_with_sage"):
                    response, error = mod.chat_with_sage(profile_text, self.user_msg, self.history)
                else:
                    response, error = mod.groq_chat(self.user_msg)
                if error:
                    self.error.emit(error)
                    return
                if hasattr(mod, "trigger_memory_update"):
                    mod.trigger_memory_update("chat", self.user_msg[:500], (response or "")[:800])
                for fact in sage_memory_extract(response or "", self.user_msg):
                    sage_memory_append(fact)
                self.done.emit(response or "")
                return

            prompts = {
                "novels":     f"Recommend 6 web novels or light novels I haven't read yet. {seen_s}",
                "shows":      f"Recommend 6 TV shows or anime I haven't watched. {seen_s}",
                "similar":    f"Find the title I'm most invested in and suggest 5 very similar ones. {seen_s}",
                "mood_light": f"Suggest 5 light, fun, easy-going picks (any medium). {seen_s}",
                "mood_heavy": f"Suggest 5 intense, gripping, deep picks (any medium). {seen_s}",
                "whats_next": "What single thing should I watch or read right now? Short reason.",
                "quick":      "Give me exactly ONE recommendation with a two-sentence pitch.",
                "explain":    f"Would I enjoy '{self.extra}'? Be honest and specific. Under 250 words.",
                "priority":   "__PRIORITY__",
                "profile":    "Summarise my media taste profile in 3-4 paragraphs.",
                "chapter_summary": "__CHAPTER_SUMMARY__",
            }
            user_msg = prompts.get(self.mode, self.user_msg)

            if self.mode == "priority":
                md = matrix_data()
                wl = md.get("watchlist", {})
                if isinstance(wl, list):
                    wl = {"planning": wl, "watching": [], "dropped": [], "completed": []}
                all_unwatched = []
                for sub in ("planning", "watching"):
                    for e in wl.get(sub, []):
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
                book_name  = self.extra
                ld         = legion_data()
                cur_ch     = ld.get("books", {}).get(book_name, {}).get("current_chapter", 0)
                chapter_text = None
                if hasattr(mod, "read_chapters_around") and cur_ch:
                    try:
                        chapter_text = mod.read_chapters_around(book_name, cur_ch, n=5)
                    except Exception:
                        pass
                if not chapter_text and hasattr(mod, "read_last_n_chapters"):
                    try:
                        chapter_text = mod.read_last_n_chapters(book_name, n=5)
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
            resp_max    = 4096 if self.mode == "chapter_summary" else 2048

            if self.mode in ("novels", "shows", "similar"):
                # Multi-Agent Chain-of-Thought Pipeline
                
                # Phase 1: Analyst
                analyst_prompt = f"{enriched}\n\nAnalyze the user's profile and output exactly 3 core recurring narrative tropes or themes they are heavily engaged with right now."
                tropes, error = mod.groq_chat(analyst_prompt, system=mem_ctx if mem_ctx else None)
                if error:
                    self.error.emit(error)
                    return
                
                # Phase 2: Generator
                gen_prompt = f"Based on the following tropes the user loves:\n{tropes}\n\nGenerate exactly 12 bold, specific candidate titles that strongly match these tropes. Give a one-line pitch for each. {seen_s}"
                candidates, error = mod.groq_chat(gen_prompt, system=mem_ctx if mem_ctx else None)
                if error:
                    self.error.emit(error)
                    return
                    
                # Phase 3: Critic
                critic_prompt = f"Here are 12 candidate titles:\n{candidates}\n\nEnsure none of these match the user's completed or dropped lists from their profile. Filter out any that don't fit well. Then format the absolute top 6 remaining titles perfectly as numbered items (e.g. 1. Title - Pitch).\n\nUser Profile as reference:\n{profile_text}"
                response, error = mod.groq_chat(critic_prompt, system=mem_ctx if mem_ctx else None)
            else:
                response, error = mod.groq_chat(
                    full_prompt, system=mem_ctx if mem_ctx else None)
                    
            if error:
                log.sage.error("Sage API error", mode=self.mode, error=error)
                self.error.emit(error)
                return

            if self.mode in ("novels", "shows", "similar", "mood_light", "mood_heavy", "quick", "whats_next"):
                try:
                    titles = re.findall(r'\d+\.\s+(.+?)(?:\s+[-\u2014]|\n|$)', response)
                    if titles and hasattr(mod, "add_seen_recs"):
                        mod.add_seen_recs(titles[:8])
                except Exception as e:
                    log.sage.warning("Failed to update seen recs", error=str(e))

            if hasattr(mod, "trigger_memory_update"):
                mod.trigger_memory_update(self.mode, user_msg[:500], (response or "")[:800])

            log.sage.info("Sage request complete", mode=self.mode, response_len=len(response or ""))
            self.done.emit(response or "")
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
                params={"api_key": "a58e553cfec69c54b7fd360041870216", "query": self.clean}, timeout=10)
            res = r.json().get("results", [])
            if res:
                x  = res[0]
                ov = _strip_markdown(x.get("overview", ""))
                log.matrix.info("Metadata fetched via TMDB fallback", title=self.clean)
                self.done.emit({
                    "title":   x.get("title") or x.get("name", self.clean),
                    "year":    (x.get("release_date") or x.get("first_air_date", ""))[:4],
                    "synopsis": ov,
                    "score":   round(x.get("vote_average", 0), 1),
                    "source":  "TMDB",
                })
            else:
                log.matrix.warning("No metadata found", title=self.clean)
                self.done.emit({})
        except Exception as e:
            log.matrix.exc("TMDB fallback failed", e, title=self.clean)
            self.error.emit(str(e))


class _SageCompanionWorker(QThread):
    done = pyqtSignal(str)

    def __init__(self, question: str, book: str, current_chapter: int = 0):
        super().__init__()
        self.question        = question
        self.book            = book
        self.current_chapter = current_chapter

    def run(self):
        mod, err = sage_mod()
        _s = matrix_data().get("settings", {})
        if _s.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
            mod.GROQ_API_KEY = _s["groq_api_key"]
        if _s.get("groq_model") and hasattr(mod, "GROQ_MODEL"):
            mod.GROQ_MODEL = _s["groq_model"]
        if not mod or not hasattr(mod, "groq_chat"):
            self.done.emit(f"Sage unavailable: {err or 'sage.py not loaded'}")
            return

        q            = self.question.strip()
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
                        f"You are a reading companion for '{self.book}'.\n"
                        f"The reader is on chapter {self.current_chapter} and wants to know about '{term}'.\n"
                        f"Using ONLY the excerpts below (no outside knowledge), write a detailed character dossier.\n\n"
                        f"EXCERPTS:\n{excerpts}"
                    )
                else:
                    prompt = (
                        f"You are a reading companion for '{self.book}'.\n"
                        f"The reader is on chapter {self.current_chapter} and wants to know about '{term}'.\n"
                        f"Using ONLY the excerpts below, write a detailed entry.\n\n"
                        f"EXCERPTS:\n{excerpts}"
                    )
            else:
                prompt = (
                    f"You are a reading companion for '{self.book}' (chapter {self.current_chapter}).\n"
                    f"The reader asks: '{q}'\n"
                    f"No local chapter data was found. Answer based on general knowledge if available."
                )
        else:
            prompt = (
                f"The user is reading '{self.book}' (chapter {self.current_chapter}). "
                f"Quick reading companion question: '{q}'\n\n"
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


class _DiscoveryWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            import urllib.parse
            mod, err = sage_mod()
            if not mod:
                self.error.emit(err or "sage.py not loaded")
                return

            # Apply settings-override API key (same pattern as SageWorker)
            settings = load_json(MATRIX_PROGRESS, {}).get("settings", {})
            if settings.get("groq_api_key") and hasattr(mod, "GROQ_API_KEY"):
                mod.GROQ_API_KEY = settings["groq_api_key"]

            profile_text = mod.profile_to_text(mod.build_profile())
            prompt = (
                f"You are a web novel expert. A user wants novel recommendations.\n\n"
                f"User's request: \"{self.query}\"\n\n"
                f"Rules:\n"
                f"- Treat every requirement as a HARD filter\n"
                f"- Only recommend novels that exist on novelbin.com or webnovel.com\n"
                f"- Return EXACTLY 6 titles, one per line\n"
                f"- Format: Title | one sentence description\n"
                f"- No numbering, no bullet points\n\n"
                f"Return 6 novels matching: \"{self.query}\""
            )
            response, error = mod.groq_chat(prompt)
            if error:
                self.error.emit(error)
                return

            results = []
            for line in (response or "").splitlines():
                line = line.strip().strip("*-•0123456789. ")
                if not line or len(line) < 3:
                    continue
                if "|" in line:
                    parts = line.split("|", 1)
                    title = parts[0].strip()
                    desc  = parts[1].strip()
                else:
                    title = line
                    desc  = ""
                if not title:
                    continue
                slug = "".join(c for c in title.lower().replace(" ", "-") if c.isalnum() or c == "-")
                results.append({"title": title, "url": f"https://novelbin.com/b/{slug}", "desc": desc})
                if len(results) >= 6:
                    break
            self.done.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class AutoSyncWorker(QThread):
    status_update = pyqtSignal(str)
    sync_done     = pyqtSignal(str)
    sync_clear    = pyqtSignal()

    def run(self):
        mod, err = legion_mod()
        if not mod:
            log.sync.warning("Auto-sync skipped — legion.py unavailable", error=err)
            return

        ld    = legion_data()
        books = ld.get("books", {})
        log.sync.info("Auto-sync started", total_books=len(books))

        fresh = [
            (name, book) for name, book in books.items()
            if book.get("current_url") and
               book.get("download_state", {}).get("status") == "idle" and
               book.get("download_state", {}).get("total_chapters_downloaded", 0) == 0
        ]
        for name, book in fresh:
            ld = legion_data()
            if name not in ld.get("books", {}):
                continue
            ld["books"][name]["download_state"]["status"] = "queued"
            save_json(LEGION_PROGRESS, ld)
            try:
                mod.download_manager.queue_download(name, ld["books"][name], ld)
                log.sync.info("Fresh book queued for download", book=name)
            except Exception as e:
                log.sync.exc("Failed to queue fresh book download", e, book=name)

        active = [
            (name, book) for name, book in books.items()
            if book.get("current_url") and
               book.get("download_state", {}).get("status") not in ("downloading", "queued") and
               book.get("download_state", {}).get("total_chapters_downloaded", 0) > 0
        ]
        if not active:
            self.sync_clear.emit()
            return

        any_new      = False
        synced_names = []
        for name, book in active:
            dl_state    = book.get("download_state", {})
            last_dl_ch  = dl_state.get("last_downloaded_chapter_num", 0)
            last_dl_url = dl_state.get("last_downloaded_chapter")
            if last_dl_url and (last_dl_url.endswith("/null") or last_dl_url.endswith("/undefined")):
                log.sync.warning("Corrupt last_downloaded_chapter URL discarded", book=name, url=last_dl_url)
                last_dl_url = None
            probe_url = last_dl_url or book.get("current_url", "")
            if not probe_url:
                continue

            self.status_update.emit(f"Checking {name}...")
            log.sync.debug("Probing for new chapters", book=name, probe_url=probe_url)
            try:
                next_url = mod.find_next_chapter(probe_url) if hasattr(mod, "find_next_chapter") else None
            except Exception as e:
                log.sync.exc("find_next_chapter failed", e, book=name, probe_url=probe_url)
                next_url = None

            if not next_url:
                log.sync.debug("No new chapters found", book=name)
                continue

            log.sync.info("New chapters found — queuing sync", book=name, next_url=next_url)
            any_new = True
            synced_names.append(name)
            self.status_update.emit(f"Syncing {name}...")

            existing_state = book.get("download_state", {})
            ld = legion_data()
            if name not in ld.get("books", {}):
                continue
            ld["books"][name]["download_state"] = {
                "status":                      "queued",
                "last_downloaded_chapter":     last_dl_url,
                "last_downloaded_chapter_num": last_dl_ch,
                "total_chapters_downloaded":   existing_state.get("total_chapters_downloaded", 0),
                "download_path":               existing_state.get("download_path"),
                "failed_chapters":             [],
                "timestamp":                   time.time(),
                "pause_requested":             False,
                "_sync_start_url":             next_url,
            }
            save_json(LEGION_PROGRESS, ld)
            try:
                mod.download_manager.queue_download(name, ld["books"][name], ld)
            except Exception as e:
                log.sync.exc("Failed to queue sync download", e, book=name)

        if any_new:
            log.sync.info("Auto-sync complete — new chapters found", books=synced_names)
            self.sync_done.emit(f"📥 Syncing new chapters — {', '.join(synced_names)}")
        else:
            log.sync.info("Auto-sync complete — library up to date")
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
            md    = matrix_data()
            items = []
            for key, info in md.get("watching", {}).items():
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
            ld    = legion_data()
            items = []
            for name, b in ld.get("books", {}).items():
                if b.get("chapters_read", 0) == 0:
                    continue
                items.append({
                    "title":      name,
                    "ch_read":    b.get("chapters_read", 0),
                    "current_ch": b.get("current_chapter", 0),
                    "words":      b.get("words_read", 0),
                })
            return jsonify({"items": items})

        @app.route("/api/watchlist")
        def api_watchlist():
            md     = matrix_data()
            wl     = md.get("watchlist", {})
            result = {}
            for lst, entries in wl.items():
                result[lst] = [
                    {"title": e.get("title", "?") if isinstance(e, dict) else str(e)}
                    for e in entries
                ]
            return jsonify(result)

        @app.route("/api/remove_watching", methods=["POST"])
        def api_remove_watching():
            data  = request.json or {}
            title = data.get("title", "")
            md    = matrix_data()
            if title in md.get("watching", {}):
                del md["watching"][title]
                save_json(MATRIX_PROGRESS, md)
            return jsonify({"ok": True})

        @app.route("/api/move_watchlist", methods=["POST"])
        def api_move_watchlist():
            data      = request.json or {}
            title     = data.get("title", "")
            from_list = data.get("from_list", "")
            to_list   = data.get("to_list", "")
            md        = matrix_data()
            wl        = md.setdefault("watchlist", {})
            entry     = None
            for e in wl.get(from_list, []):
                t = e.get("title", "") if isinstance(e, dict) else str(e)
                if t == title:
                    entry = e
                    break
            if entry:
                wl[from_list] = [e for e in wl[from_list]
                    if (e.get("title", "") if isinstance(e, dict) else str(e)) != title]
                wl.setdefault(to_list, []).append(entry)
                save_json(MATRIX_PROGRESS, md)
            return jsonify({"ok": True})

        @app.route("/api/update_reading", methods=["POST"])
        def api_update_reading():
            data  = request.json or {}
            title = data.get("title", "")
            ch    = data.get("current_chapter", 0)
            ld    = legion_data()
            b     = ld.get("books", {}).get(title)
            if b and ch > 0:
                b["current_chapter"] = ch
                save_json(LEGION_PROGRESS, ld)
            return jsonify({"ok": True})

        def _run():
            app.run(host="0.0.0.0", port=_mobile_server_port, debug=False, use_reloader=False)

        _mobile_server_thread = threading.Thread(target=_run, daemon=True)
        _mobile_server_thread.start()
        return _mobile_server_port
    except Exception as e:
        log.warning("Mobile server failed to start", error=str(e))
        return None
